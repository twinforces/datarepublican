import os
import glob
import argparse
import logging
import zipfile
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import re
import queue
from logging.handlers import QueueHandler, QueueListener
from lxml import etree
from io import BytesIO
from collections import defaultdict
try:
    from postal.parser import parse_address
    from postal.expand import expand_address
except ImportError:
    print("Error: 'pypostal' not installed. Install with 'pip install pypostal' and ensure libpostal is set up.")
    exit(1)
import signal
import sys

# Constants
NAMESPACES = {'irs': 'http://www.irs.gov/efile'}
ADDRESS_XPATHS = [
    etree.XPath(".//irs:Filer/irs:USAddress/* | .//Filer/USAddress/*", namespaces=NAMESPACES),
    etree.XPath(".//USAddress/*", namespaces=NAMESPACES),
    etree.XPath(".//irs:Filer/irs:USAddress/irs:ZIPCd | .//Filer/USAddress/ZIPCd | .//ZIPCd", namespaces=NAMESPACES),
]
GRANT_XPATHS_990PF = [
    etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:GrantOrContributionPdDurYrGrp", namespaces=NAMESPACES),
    etree.XPath(".//irs:IRS990PF//irs:GrantOrContributionPdDurYrGrp", namespaces=NAMESPACES),
]
GRANTEE_NAME_XPATHS = [
    etree.XPath(".//irs:RecipientBusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
    etree.XPath(".//irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
]
FILER_EIN_XPATHS = [
    etree.XPath(".//irs:Filer/irs:EIN", namespaces=NAMESPACES),
    etree.XPath(".//Filer/EIN", namespaces=NAMESPACES),
]
AMOUNT_XPATHS = [
    etree.XPath(".//irs:Amt", namespaces=NAMESPACES),
    etree.XPath(".//irs:GrantOrContributionAmt", namespaces=NAMESPACES),
    etree.XPath(".//irs:TotalGrantOrContriPdDurYrAmt", namespaces=NAMESPACES),
    etree.XPath(".//Amt", namespaces={}),
]
GRANTEE_ADDRESS_XPATHS = [
    etree.XPath(".//irs:RecipientUSAddress/* | .//irs:RecipientForeignAddress/*", namespaces=NAMESPACES),
    etree.XPath(".//RecipientUSAddress/* | .//RecipientForeignAddress/*", namespaces={}),
]
ZIP_REGEX = re.compile(r'^\d{5}$')
PO_BOX_NUMBER_REGEX = re.compile(r'\b\d+\b')  # Regex to extract PO Box number
STOP_WORDS = {'and', 'the', 'of', 'for', 'in', 'to', 'a', 'an'}
ADDRESS_COLUMNS = ["filer_ein", "filer_name", "canonical_address"]
GRANT_COLUMNS = ["filer_ein", "filer_name", "grant_ein", "grant_amt", "tax_year"]
DEBUG_ADDRESS_COLUMNS = ["filer_ein", "filer_name", "xml_filename", "raw_components", "canonical_address", "raw_zip", "zip_code", "status", "reason"]
DEBUG_GRANT_COLUMNS = ["filer_ein", "filer_name", "xml_filename", "grantee_name", "grant_ein", "grant_address", "grant_amt", "tax_year", "status", "heuristic_score", "reason"]
INVALID_EIN_COLUMNS = ["tsv_ein", "xml_ein", "filer_name", "xml_filename", "reason"]
PO_BOX_COLUMNS = ["po_box", "zip_code", "org_name", "ein", "type", "xml_filename"]
ZIP_ERROR_COLUMNS = ["xml_filename", "filer_ein", "zip_code", "raw_zip", "address"]
CSV_QUOTE_FIELDS = {
    'addresses': ['filer_name', 'canonical_address'],
    'grants': ['filer_name', 'grant_ein'],
    'debug_address': ['filer_name', 'xml_filename', 'raw_components', 'canonical_address', 'reason'],
    'debug_grant': ['filer_name', 'xml_filename', 'grantee_name', 'grant_ein', 'grant_address', 'reason'],
    'invalid_eins': ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename', 'reason'],
    'po_box_matches': ['org_name', 'xml_filename'],
    'zip_errors': ['xml_filename', 'filer_ein', 'zip_code', 'raw_zip', 'address']
}
EIN_REGEX = re.compile(r'^\d{9}$')

# Global variables
logger = None
verbose = False
quiet = False
fast_interrupt = False
debug_limit = None
skip_address_errors = True
sample_xml = None
log_zip_errors = False
total_addresses = 0
total_grants = 0
total_990pf_rows = 0
total_address_errors = 0
total_queue_puts = 0
total_tasks_done = 0
tsv_write_queue = queue.Queue(maxsize=20000)
done_queuing = False
queue_lock = threading.Lock()
zip_index_lock = threading.Lock()
address_entries = []
grant_entries = []
debug_address_entries = []
debug_grant_entries = []
invalid_ein_entries = []
debug_success_grants = False
po_box_entries = []
write_lock = threading.Lock()
zip_index = {}  # filename -> (zip_path, internal_path)
zip_code_index = {}  # zip_code -> set((filer_ein, filer_name))
po_box_zip_index = {}  # (po_box, zip_code) -> set((filer_ein, filer_name))
ein_mismatch_set = set()

# Thread-local storage for indices
thread_local = threading.local()

# Logging setup
def setup_logging(output_dir):
    global logger
    log_queue = queue.Queue(-1)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(os.path.join(output_dir, 'extract_addresses_grants_log.txt'))
    file_handler.setFormatter(formatter)
    listener = QueueListener(log_queue, file_handler)
    listener.start()
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR if not verbose else logging.INFO)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers = [queue_handler, console_handler] if not quiet else []
    return listener

def log_error(msg_format, *args, ein=None, exc_info=False):
    if not quiet and logger:
        try:
            logger.info(msg_format.format(*args), extra={'ein': ein} if ein else None, exc_info=exc_info)
        except Exception as e:
            logger.info("Log formatting error: {}; args: {}", str(e), args)

# Thread-local counters
file_counter_local = threading.local()

def initialize_thread_local_counters():
    if not hasattr(file_counter_local, 'value'):
        file_counter_local.value = 0
    if not hasattr(file_counter_local, 'skipped'):
        file_counter_local.skipped = 0

def build_zip_index(zip_dir, start_year, end_year):
    """Build an index of XML filenames to ZIP paths with a progress bar."""
    index = {}
    zip_files = []
    zip_names = set()
    for year in range(start_year, end_year + 2):
        zip_files.extend(glob.glob(os.path.join(zip_dir, f"{year}*.zip")))
    
    for zip_path in tqdm(zip_files, desc="Indexing ZIP files"):
        zip_names.add(os.path.basename(zip_path))
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for internal_path in zf.namelist():
                    if internal_path.endswith('.xml'):
                        filename = os.path.basename(internal_path)
                        log_error("Indexing XML: {}", filename)
                        if filename in index:
                            log_error("Duplicate XML filename {} found in ZIP {}, overwriting with {}", filename, index[filename][0], zip_path)
                        index[filename] = (zip_path, internal_path)
        except Exception as e:
            log_error("Error indexing ZIP {}: {}", zip_path, str(e), exc_info=True)
    
    log_error("Built ZIP index with {} XML files from {} ZIPs: {}", len(index), len(zip_files), sorted(zip_names))
    return index

def read_tsv_files(source_dir, start_year, end_year):
    rows = []
    tsv_patterns = [
        os.path.join(source_dir, "charity_latest.tsv"),
        os.path.join(source_dir, "charities_*.tsv")
    ]
    tsv_files = []
    for pattern in tsv_patterns:
        tsv_files.extend(glob.glob(pattern))
    
    if not tsv_files:
        log_error("No TSV files found in {}. Expected 'charity_latest.tsv' or 'charities_<org_type>_<year>.tsv'. Found: {}", source_dir, os.listdir(source_dir))
        return []

    for tsv_file in tsv_files:
        match = re.match(r'charities_(.+)_(\d{4})\.tsv', os.path.basename(tsv_file))
        year = None
        if match:
            year = int(match.group(2))
            if not (start_year <= year <= end_year):
                continue
        try:
            with open(tsv_file, 'r', encoding='utf-8') as f:
                header = f.readline().strip().split('\t')
                header_map = {col: idx for idx, col in enumerate(header)}
                required_cols = ['filer_ein', 'filer_name', 'tax_year', 'form_type', 'xml_name']
                if not all(col in header_map for col in required_cols):
                    log_error("Missing required columns in TSV {}: {}", tsv_file, required_cols)
                    continue
                for line in f:
                    fields = line.strip().split('\t')
                    if len(fields) < len(header_map):
                        continue
                    row = {col: fields[idx] for col, idx in header_map.items()}
                    rows.append(row)
        except Exception as e:
            log_error("Error reading TSV {}: {}", tsv_file, str(e), exc_info=True)
    
    log_error("Read {} rows from {} TSV files: {}", len(rows), len(tsv_files), tsv_files)
    log_error("Sample xml_name: {}", rows[0]['xml_name'] if rows else "None")
    return rows

def compute_name_heuristic(grantee_name, filer_name):
    """Compute heuristic score: 1 point per matching word (case-insensitive, excluding stop words)."""
    if not grantee_name or not filer_name:
        return 0
    words1 = {w.lower() for w in grantee_name.split() if w.lower() not in STOP_WORDS}
    words2 = {w.lower() for w in filer_name.split() if w.lower() not in STOP_WORDS}
    return len(words1 & words2)

def canonicalize_address(address_components, output_dir):
    if not address_components:
        return "", None, None, ""
    address_str = " ".join(comp for comp in address_components if comp)
    try:
        # Use pypostal to parse the address into components
        parsed = parse_address(address_str)
        # Extract PO Box and ZIP code from parsed components
        po_box = None
        zip_code = None
        for component, label in parsed:
            if label == 'po_box':
                # Extract the numeric part of the PO Box
                match = PO_BOX_NUMBER_REGEX.search(component)
                if match:
                    po_box = match.group(0)
                    log_error("Extracted PO Box number: {} from full PO Box: {} in address: {}", po_box, component, address_str)
                else:
                    log_error("Failed to extract PO Box number from: {} in address: {}", component, address_str)
            elif label == 'postcode':
                zip_code = component
                log_error("Extracted ZIP code: {} from address: {}", zip_code, address_str)
        
        # Process ZIP code to get the first 5 digits
        if zip_code:
            zip_code_digits = re.sub(r'\D', '', zip_code)
            if len(zip_code_digits) >= 5:
                zip_code = zip_code_digits[:5]
                log_error("Extracted 5-digit ZIP {} from original ZIP {} in address: {}", zip_code, zip_code, address_str)
            else:
                log_error("Invalid zip_code: {} in address: {}", zip_code, address_str)
                zip_code = None
        
        # Use pypostal to expand and normalize the address
        normalized = expand_address(address_str)
        canonical = normalized[0] if normalized else address_str
        
        # If PO Box was found, ensure it's included in the canonical address
        if po_box and 'po box' not in canonical.lower():
            canonical = f"PO Box {po_box} {canonical}"
        
        return canonical, po_box, zip_code, ""
    except Exception as e:
        log_error("Error canonicalizing address '{}': {}", address_str, str(e), exc_info=True)
        return address_str, None, None, ""

def parse_addresses(xml_content, xml_filename, row, zip_index, output_dir):
    global total_addresses, total_address_errors, total_queue_puts
    try:
        # Initialize thread-local storage
        if not hasattr(thread_local, 'address_entries'):
            thread_local.address_entries = []
            thread_local.debug_address_entries = []
            thread_local.po_box_entries = []
            thread_local.invalid_ein_entries = []
            thread_local.total_addresses = 0
            thread_local.total_queue_puts = 0
            thread_local.total_address_errors = 0
            thread_local.zip_code_index = {}
            thread_local.po_box_zip_index = {}
            thread_local.ein_mismatch_set = set()

        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        
        tsv_ein = row['filer_ein'].strip()
        filer_name = row['filer_name'].strip()
        
        # Re-extract EIN from XML
        xml_ein = None
        for xpath in FILER_EIN_XPATHS:
            elem = xpath(root)
            if elem and elem[0].text:
                xml_ein = elem[0].text.strip()
                break
        if not xml_ein:
            thread_local.invalid_ein_entries.append({
                'tsv_ein': tsv_ein,
                'xml_ein': '',
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'reason': 'No EIN found in XML'
                 })
            if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                thread_local.debug_address_entries.append({
                        'filer_ein': tsv_ein,
                        'filer_name': filer_name,
                        'xml_filename': xml_filename,
                        'raw_components': '',
                        'canonical_address': '',
                        'raw_zip': '',
                        'zip_code': '',
                        'status': 'skipped',
                        'reason': "No EIN found in XML"
                    })
            return False, None
        # Validate XML EIN
        if not EIN_REGEX.match(xml_ein):
            thread_local.invalid_ein_entries.append({
                'tsv_ein': tsv_ein,
                'xml_ein': xml_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'reason': f"Invalid XML EIN format: {xml_ein or 'None'}"
                })
            if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                thread_local.debug_address_entries.append({
                    'filer_ein': tsv_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'raw_components': '',
                    'canonical_address': '',
                    'raw_zip': '',
                    'zip_code': '',
                    'status': 'skipped',
                    'reason': f"Invalid XML EIN: {xml_ein}"
                })
            return False, None
        
        # Compare TSV and XML EINs
        if tsv_ein != xml_ein and tsv_ein not in ein_mismatch_set:
            thread_local.invalid_ein_entries.append({
                'tsv_ein': tsv_ein,
                'xml_ein': xml_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'reason': 'TSV EIN differs from XML EIN'
                })
            thread_local.ein_mismatch_set.add(tsv_ein)
        

        
        filer_ein = xml_ein
        
        # Parse address
        address_components = []
        for xpath in ADDRESS_XPATHS:
            elements = xpath(root)
            for elem in elements:
                if elem.text:
                    text = elem.text.strip()
                    log_error("Extracted address component: {} from {}", text, xml_filename)
                    address_components.append(text)
        canonical_address, po_box, zip_code, _ = canonicalize_address(address_components, output_dir)
        raw_components_str = ";".join(address_components)
        us_address = root.find(".//irs:Filer/irs:USAddress", namespaces=NAMESPACES)
        address_snippet = etree.tostring(us_address if us_address is not None else root, encoding='unicode', method='xml', pretty_print=True)[:500]
        
        if canonical_address:
            thread_local.address_entries.append({
                'filer_ein': filer_ein,
                'filer_name': filer_name,
                'canonical_address': canonical_address,
                'po_box': po_box,
                'zip_code': zip_code
            })
            if debug_success_grants and (not debug_limit or len(thread_local.debug_address_entries) < debug_limit):
                thread_local.debug_address_entries.append({
                    'filer_ein': filer_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'raw_components': raw_components_str,
                    'canonical_address': canonical_address,
                    'raw_zip': '',
                    'zip_code': zip_code or '',
                    'status': 'success',
                    'reason': f"snippet: {address_snippet}"
                })
            if po_box and zip_code:
                log_error("Adding filer PO Box: po_box={} zip_code={} ein={} name={}", po_box, zip_code, filer_ein, filer_name)
                thread_local.po_box_entries.append({
                    'po_box': po_box,
                    'zip_code': zip_code,
                    'org_name': filer_name,
                    'ein': filer_ein,
                    'type': 'filer',
                    'xml_filename': xml_filename
                })
            thread_local.total_addresses += 1
            thread_local.total_queue_puts += 1

            
            # Update thread-local indices without locks
            if zip_code and isinstance(zip_code, str) and ZIP_REGEX.match(zip_code) and zip_code.isdigit() and len(zip_code) == 5:
                log_error("Attempting to index zip_code={} (type={}) for EIN={}", zip_code, type(zip_code), filer_ein)
                if zip_code not in thread_local.zip_code_index:
                    thread_local.zip_code_index[zip_code] = set()
                thread_local.zip_code_index[zip_code].add((filer_ein, filer_name))
                log_error("Indexed zip_code={} (type={}) for EIN={}", zip_code, type(zip_code), filer_ein)
            else:
                log_error("Skipping invalid zip_code={} (type={}) for EIN={}", zip_code, type(zip_code), filer_ein)
            
            if po_box and zip_code and isinstance(zip_code, str) and ZIP_REGEX.match(zip_code) and zip_code.isdigit() and len(zip_code) == 5:
                po_box_key = (po_box, zip_code)
                if po_box_key not in thread_local.po_box_zip_index:
                    thread_local.po_box_zip_index[po_box_key] = set()
                thread_local.po_box_zip_index[po_box_key].add((filer_ein, filer_name))
        else:
            log_error("Possible foreign address for EIN={} in XML {}: no USAddress found", filer_ein, xml_filename)
            thread_local.total_address_errors += 1
            if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                thread_local.debug_address_entries.append({
                    'filer_ein': filer_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'raw_components': raw_components_str,
                    'canonical_address': '',
                    'raw_zip': '',
                    'zip_code': '',
                    'status': 'error',
                    'reason': f"Invalid address; zip_code=None; components={address_components}; snippet: {address_snippet}"
                })
            if sample_xml:
                os.makedirs(sample_xml, exist_ok=True)
                with open(os.path.join(sample_xml, xml_filename), 'wb') as f:
                    f.write(xml_content)
            if not skip_address_errors:
                return False, None
        
        log_error("Active threads in parse_addresses: {}", threading.active_count())
        log_error("Thread-local zip_code_index size: {}", len(thread_local.zip_code_index))
        log_error("Thread-local po_box_zip_index size: {}", len(thread_local.po_box_zip_index))
        for k in thread_local.zip_code_index:
            if not (k and k.isdigit() and len(k) == 5):
                log_error("Invalid thread-local zip_code_index key: {}", k)
        return True, filer_ein
    except Exception as e:
        log_error("Error parsing XML {} for EIN={}: {}", xml_filename, row['filer_ein'], str(e), exc_info=True)
        thread_local.total_address_errors += 1
        if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
            thread_local.debug_address_entries.append({
                'filer_ein': row['filer_ein'],
                'filer_name': row['filer_name'],
                'xml_filename': xml_filename,
                'raw_components': '',
                'canonical_address': '',
                'raw_zip': '',
                'zip_code': '',
                'status': 'error',
                'reason': str(e)
            })
        return False, None
        
def parse_grants(xml_content, xml_filename, row, filer_ein, output_dir):
    global total_grants, total_990pf_rows, total_queue_puts
    try:
        # Initialize thread-local storage for grants
        if not hasattr(thread_local, 'grant_entries'):
            thread_local.grant_entries = []
            thread_local.debug_grant_entries = []
            thread_local.po_box_entries_grants = []
            thread_local.total_grants = 0
            thread_local.total_queue_puts_grants = 0
            thread_local.total_990pf_rows = 0



        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        
        filer_name = row['filer_name'].strip()
        status_counts = defaultdict(int)  # Track grant statuses
        
        # Parse grants for 990PF
        if row['form_type'] == '990PF':
            thread_local.total_990pf_rows += 1
            grant_count = 0
            xpath_matches = defaultdict(int)
            for xpath_grant in GRANT_XPATHS_990PF:
                grant_elements = xpath_grant(root)
                xpath_matches[str(xpath_grant)] += len(grant_elements)
                grant_count += len(grant_elements)
                for element in grant_elements:
                    ein_elem = element.xpath(".//irs:EIN | .//irs:RecipientEIN", namespaces=NAMESPACES)
                    name_elem = None
                    for xpath_name in GRANTEE_NAME_XPATHS:
                        names = xpath_name(element)
                        xpath_matches[str(xpath_name)] += len(names)
                        if names and names[0].text:
                            name_elem = names[0]
                            break
                    amount_elem = None
                    for xpath_amount in AMOUNT_XPATHS:
                        amounts = xpath_amount(element)
                        xpath_matches[str(xpath_amount)] += len(amounts)
                        if amounts and amounts[0].text is not None:
                            amount_elem = amounts[0]
                            break
                    address_elems = None
                    for xpath_address in GRANTEE_ADDRESS_XPATHS:
                        addrs = xpath_address(element)
                        xpath_matches[str(xpath_address)] += len(addrs)
                        if addrs:
                            address_elems = addrs
                            break
                    grant_ein = ein_elem[0].text.strip() if ein_elem and ein_elem[0].text else "Unknown"
                    grantee_name = name_elem.text.strip() if name_elem is not None and name_elem.text is not None else "Unknown"
                    grant_address_components = [elem.text.strip() for elem in address_elems if elem.text is not None] if address_elems else []
                    grant_address, grant_po_box, grant_zip_code, _ = canonicalize_address(grant_address_components, output_dir)
                    
                    if amount_elem is not None and amount_elem.text is not None:
                        try:
                            grant_amt = int(float(amount_elem.text.strip()))
                            if grant_amt > 0:
                                # Compute name heuristic using indices
                                best_score = 0
                                best_filer = None
                                best_heuristic = None
                                candidates = set()
                                if grant_po_box and grant_zip_code and ZIP_REGEX.match(grant_zip_code):
                                    candidates = po_box_zip_index.get((grant_po_box, grant_zip_code), set())
                                    heuristic_type = 'po_box_name'
                                elif grant_zip_code and ZIP_REGEX.match(grant_zip_code):
                                    candidates = zip_code_index.get(grant_zip_code, set())
                                    heuristic_type = 'zip_name'
                                
                                for filer_ein, filer_name in candidates:
                                    score = compute_name_heuristic(grantee_name, filer_name)
                                    if score > best_score:
                                        best_score = score
                                        best_filer = (filer_ein, filer_name)
                                        best_heuristic = heuristic_type
                                
                                if best_filer and best_score >= 1:
                                    grant_ein = best_filer[0]
                                    status = f"success_{best_heuristic}_score_{best_score}"
                                elif grant_ein == "Unknown" and grant_address:
                                    grant_ein = f"Address:{grant_address}"
                                    status = 'success_address'
                                elif grantee_name != "Unknown":
                                    grant_ein = f"Name:{grantee_name}"
                                    status = 'success_name'
                                else:
                                    grant_ein = "Unknown"
                                    status = 'skipped'
                                    log_error("Skipped grant for grantee {}: {}", grantee_name, "No valid EIN, address, or name")
                                
                                status_counts[status] += 1
                                log_error("Grant status: {} (count={}) for grantee {} in XML {}", status, status_counts[status], grantee_name, xml_filename)
                                snippet = etree.tostring(element, encoding='unicode', method='xml', pretty_print=True)[:500]
                                
                                if grant_ein != "Unknown":
                                    thread_local.grant_entries.append({
                                        'filer_ein': filer_ein,
                                        'filer_name': filer_name,
                                        'grant_ein': grant_ein,
                                        'grant_amt': grant_amt,
                                        'tax_year': row['tax_year']
                                    })
                                    if debug_success_grants and (not debug_limit or len(thread_local.debug_grant_entries) < debug_limit):
                                        thread_local.debug_grant_entries.append({
                                            'filer_ein': filer_ein,
                                            'filer_name': filer_name,
                                            'xml_filename': xml_filename,
                                            'grantee_name': grantee_name,
                                            'grant_ein': grant_ein,
                                            'grant_address': grant_address,
                                            'grant_amt': grant_amt,
                                            'tax_year': row['tax_year'],
                                            'status': status,
                                            'heuristic_score': best_score,
                                            'reason': "success"
                                        })
                                    if grant_po_box and grant_zip_code and ZIP_REGEX.match(grant_zip_code):
                                        thread_local.po_box_entries_grants.append({
                                            'po_box': grant_po_box,
                                            'zip_code': grant_zip_code,
                                            'org_name': grantee_name,
                                            'ein': grant_ein,
                                            'type': 'grantee',
                                            'xml_filename': xml_filename
                                        })
                                    thread_local.total_queue_puts_grants += 1
                                    thread_local.total_grants += 1
                                else:
                                    # Log XML snippet for debug
                                    if not debug_limit or len(thread_local.debug_grant_entries) < debug_limit:
                                        thread_local.debug_grant_entries.append({
                                                'filer_ein': filer_ein,
                                                'filer_name': filer_name,
                                                'xml_filename': xml_filename,
                                                'grantee_name': grantee_name,
                                                'grant_ein': grant_ein,
                                                'grant_address': grant_address,
                                                'grant_amt': grant_amt,
                                                'tax_year': row['tax_year'],
                                                'status': status,
                                                'heuristic_score': best_score,
                                                'reason': "No valid EIN, address, or name"
                                        })
                        except (ValueError, TypeError) as e:
                            # Log XML snippet for debug
                            snippet = etree.tostring(element, encoding='unicode', method='xml', pretty_print=True)[:500]
                            if not debug_limit or len(thread_local.debug_grant_entries) < debug_limit:
                                thread_local.debug_grant_entries.append({
                                    'filer_ein': filer_ein,
                                    'filer_name': filer_name,
                                    'xml_filename': xml_filename,
                                    'grantee_name': grantee_name,
                                    'grant_ein': grant_ein,
                                    'grant_address': grant_address,
                                    'grant_amt': '',
                                    'tax_year': row['tax_year'],
                                    'status': 'error',
                                    'heuristic_score': 0,
                                    'reason': f"Invalid grant amount: {str(e)}; XML snippet: {snippet}"
                                })
                    else:
                        # Log XML snippet for debug
                        snippet = etree.tostring(element, encoding='unicode', method='xml', pretty_print=True)[:500]
                        if not debug_limit or len(thread_local.debug_grant_entries) < debug_limit:
                            thread_local.debug_grant_entries.append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'xml_filename': xml_filename,
                                'grantee_name': grantee_name,
                                'grant_ein': grant_ein,
                                'grant_address': grant_address,
                                'grant_amt': '',
                                'tax_year': row['tax_year'],
                                'status': 'skipped',
                                'heuristic_score': 0,
                                'reason': f"No grant amount found for grantee {grantee_name}; XML snippet: {snippet}"
                            })
        
        log_error("Final grant status counts for XML {}: {}", xml_filename, dict(status_counts))
        log_error("Active threads in parse_grants: {}", threading.active_count())
        return True
    except Exception as e:
        log_error("Error parsing grants in XML {} for EIN={}: {}", xml_filename, filer_ein, str(e), exc_info=True)
        return False

def process_rows(rows, worker_threads, zip_index, start_year, end_year, output_dir):
    global total_990pf_rows
    zip_cache = {}
    initialize_thread_local_counters()
    total_skipped = 0
    filer_eins = {}

    # Initialize zip_errors.tsv with headers
    if log_zip_errors:
        with open(os.path.join(output_dir, 'zip_errors.tsv'), 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerow(ZIP_ERROR_COLUMNS)
            
    # First pass: Process addresses and build indices
    def process_address_row(row):
        nonlocal total_skipped
        xml_path = row.get('xml_name', '')
        if not xml_path:
            log_error("No xml_name for EIN={}, skipping", row['filer_ein'], ein=row['filer_ein'])
            if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                thread_local.debug_address_entries.append({
                        'filer_ein': row['filer_ein'],
                        'filer_name': row['filer_name'],
                        'xml_filename': '',
                        'raw_components': '',
                        'canonical_address': '',
                        'raw_zip': '',
                        'zip_code': '',
                        'status': 'skipped',
                        'reason': 'Missing xml_name'
                    })
            file_counter_local.skipped += 1
            total_skipped += 1
            return
        try:
            parts = xml_path.split('/')
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                log_error("No file named {} found in ZIP index for EIN={}, skipping", xml_filename, row['filer_ein'], ein=row['filer_ein'])
                if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                    thread_local.debug_address_entries.append({
                            'filer_ein': row['filer_ein'],
                            'filer_name': row['filer_name'],
                            'xml_filename': xml_filename,
                            'raw_components': '',
                            'canonical_address': '',
                            'raw_zip': '',
                            'zip_code': '',
                            'status': 'skipped',
                            'reason': 'XML not found in ZIP index'
                        })
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            zip_path, internal_path = zip_index[xml_filename]
            zip_year_match = re.match(r'.*(\d{4})', os.path.basename(zip_path))
            if not zip_year_match:
                log_error("Invalid ZIP path format {} for EIN={}, skipping", zip_path, row['filer_ein'], ein=row['filer_ein'])
                if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                    thread_local.debug_address_entries.append({
                            'filer_ein': row['filer_ein'],
                            'filer_name': row['filer_name'],
                            'xml_filename': xml_filename,
                            'raw_components': '',
                            'canonical_address': '',
                            'raw_zip': '',
                            'zip_code': '',
                            'status': 'skipped',
                            'reason': 'Invalid ZIP path format'
                        })
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            zip_year = int(zip_year_match.group(1))
            if zip_year < start_year or zip_year > end_year + 1:
                log_error("ZIP year {} outside range {}-{} for xml_path {} and EIN={}, skipping", zip_year, start_year, end_year + 1, xml_path, row['filer_ein'], ein=row['filer_ein'])
                if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                    thread_local.debug_address_entries.append({
                            'filer_ein': row['filer_ein'],
                            'filer_name': row['filer_name'],
                            'xml_filename': xml_filename,
                            'raw_components': '',
                            'canonical_address': '',
                            'raw_zip': '',
                            'zip_code': '',
                            'status': 'skipped',
                            'reason': f"ZIP year {zip_year} outside range"
                        })
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            if zip_path not in zip_cache:
                zip_cache[zip_path] = zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                success, filer_ein = parse_addresses(xml_content, xml_filename, row, zip_index, output_dir)
                if success and filer_ein:
                    thread_local.filer_eins = thread_local.__dict__.setdefault('filer_eins', {})
                    thread_local.filer_eins[xml_filename] = (filer_ein, row)
        except Exception as e:
            log_error("Error processing row for XML {} and EIN={}: {}", xml_path, row['filer_ein'], str(e), exc_info=True)
            if not debug_limit or len(thread_local.debug_address_entries) < debug_limit:
                thread_local.debug_address_entries.append({
                        'filer_ein': row['filer_ein'],
                        'filer_name': row['filer_name'],
                        'xml_filename': xml_path,
                        'raw_components': '',
                        'canonical_address': '',
                        'raw_zip': '',
                        'zip_code': '',
                        'status': 'error',
                        'reason': str(e)
                    })
            file_counter_local.skipped += 1
            total_skipped += 1

# Address pass
    log_error("Found {} 990PF rows in {} total rows", sum(1 for row in rows if row['form_type'] == '990PF'), len(rows))
    thread_local_data = []
    if args.no_threads:
        for row in tqdm(rows, desc="Processing addresses"):
            process_address_row(row)
            thread_local_data.append(thread_local.__dict__.copy())
    else:
        with ThreadPoolExecutor(max_workers=worker_threads) as executor:
            futures = [executor.submit(process_address_row, row) for row in rows]
            with tqdm(total=len(rows), desc="Processing addresses") as pbar:
                for future in as_completed(futures):
                    try:
                        future.result()
                        # Collect thread-local data after each future completes
                        thread_local_data.append(thread_local.__dict__.copy())
                    except Exception as e:
                        log_error("Error in address future: {}", str(e), exc_info=True)
                    pbar.update(1)
    
    # Merge thread-local indices into global indices
    with zip_index_lock:
        for data in thread_local_data:
            if 'address_entries' in data:
                address_entries.extend(data['address_entries'])
            if 'debug_address_entries' in data:
                debug_address_entries.extend(data['debug_address_entries'])
            if 'po_box_entries' in data:
                po_box_entries.extend(data['po_box_entries'])
            if 'invalid_ein_entries' in data:
                invalid_ein_entries.extend(data['invalid_ein_entries'])
            if 'ein_mismatch_set' in data:
                ein_mismatch_set.update(data['ein_mismatch_set'])
            if 'filer_eins' in data:
                filer_eins.update(data['filer_eins'])
            if 'total_addresses' in data:
                total_addresses += data['total_addresses']
            if 'total_queue_puts' in data:
                total_queue_puts += data['total_queue_puts']
            if 'total_address_errors' in data:
                total_address_errors += data['total_address_errors']
            if 'zip_code_index' in data:
                for zip_code, entries in data['zip_code_index'].items():
                    if zip_code not in zip_code_index:
                        zip_code_index[zip_code] = set()
                    zip_code_index[zip_code].update(entries)
            if 'po_box_zip_index' in data:
                for po_box_zip, entries in data['po_box_zip_index'].items():
                    if po_box_zip not in po_box_zip_index:
                        po_box_zip_index[po_box_zip] = set()
                    po_box_zip_index[po_box_zip].update(entries)

    log_error("Address pass complete: {} addresses, {} errors, zip_code_index size={}, active threads={}", total_addresses, total_address_errors, len(zip_code_index), threading.active_count())
        
    # Second pass: Process grants
    thread_local_data = []  # Reset for grant pass
    def process_grant_row(xml_filename, filer_info):
        if not filer_eins:
            log_error("No filer_eins available for grant processing, skipping grant pass")
            return
        nonlocal total_skipped
        initialize_thread_local_counters()
        log_error("Processing grant in thread: {}", threading.get_ident())
        filer_ein, row = filer_info
        xml_path = row.get('xml_name', '')
        try:
            parts = xml_path.split('/')
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                log_error("XML {} not in zip_index for EIN={}, skipping", xml_filename, filer_ein)
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            zip_path, internal_path = zip_index[xml_filename]
            if zip_path not in zip_cache:
                zip_cache[zip_path] = zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                parse_grants(xml_content, xml_filename, row, filer_ein, output_dir)
                thread_local_data.append(thread_local.__dict__.copy())
        except Exception as e:
            log_error("Error processing grant row for XML {} and EIN={}: {}", xml_path, filer_ein, str(e), exc_info=True)
            file_counter_local.skipped += 1
            total_skipped += 1

    if args.no_threads:
        for xml_filename, filer_info in tqdm(filer_eins.items(), desc="Processing grants"):
            process_grant_row(xml_filename, filer_info)
            thread_local_data.append(thread_local.__dict__.copy())
    else:
        with ThreadPoolExecutor(max_workers=worker_threads) as executor:
            futures = [executor.submit(process_grant_row, xml_filename, filer_info) for xml_filename, filer_info in filer_eins.items()]
            with tqdm(total=len(filer_eins), desc="Processing grants") as pbar:
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        log_error("Error in grant future: {}", str(e), exc_info=True)
                    pbar.update(1)
    
    # Merge thread-local data from grant pass
    with zip_index_lock:
        for data in thread_local_data:
            if 'grant_entries' in data:
                grant_entries.extend(data['grant_entries'])
            if 'debug_grant_entries' in data:
                debug_grant_entries.extend(data['debug_grant_entries'])
            if 'po_box_entries_grants' in data:
                po_box_entries.extend(data['po_box_entries_grants'])
            if 'total_grants' in data:
                total_grants += data['total_grants']
            if 'total_queue_puts_grants' in data:
                total_queue_puts += data['total_queue_puts_grants']
            if 'total_990pf_rows' in data:
                total_990pf_rows += data['total_990pf_rows']
    for zip_file in zip_cache.values():
        zip_file.close()
    
    log_error("Grant pass complete: {} grants, {} 990PF rows, total skipped: {}, active threads={}", total_grants, total_990pf_rows, total_skipped, threading.active_count())

def write_outputs(output_dir):
    global address_entries, grant_entries, debug_address_entries, debug_grant_entries, invalid_ein_entries, po_box_entries
    address_file = os.path.join(output_dir, "charity_addresses.tsv")
    grant_file = os.path.join(output_dir, "inferred_grants.tsv")
    debug_address_file = os.path.join(output_dir, "address_debug.tsv")
    debug_grant_file = os.path.join(output_dir, "grant_debug.tsv")
    invalid_ein_file = os.path.join(output_dir, "invalid_eins.tsv")
    po_box_file = os.path.join(output_dir, "po_box_matches.tsv")

    # Summarize grant status
    status_counts = defaultdict(int)
    for entry in debug_grant_entries:
        status_counts[entry['status']] += 1
    log_error("Grant status summary: {}", dict(status_counts))

    # Summarize address error reasons
    reason_counts = defaultdict(int)
    for entry in debug_address_entries:
        if entry['status'] == 'error':
            reason = entry['reason'].split(';')[0]  # First part of reason
            reason_counts[reason] += 1
    log_error("Address error summary: {}", dict(reason_counts))

    log_error("Writing outputs: {} addresses, {} grants, {} debug addresses, {} debug grants, {} invalid EINs, {} PO Boxes, {} 990PF rows, {} address errors",
              len(address_entries), len(grant_entries), len(debug_address_entries), len(debug_grant_entries),
              len(invalid_ein_entries), len(po_box_entries), total_990pf_rows, total_address_errors)

    # Write addresses without de-duplication
    log_error("Opening TSV file: {}", address_file)
    with open(address_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(ADDRESS_COLUMNS)
        for entry in address_entries:
            writer.writerow([entry[col] for col in ADDRESS_COLUMNS])
        f.flush()
    log_error("Wrote {} address rows to {}", len(address_entries), address_file)

    # Write grants without de-duplication
    log_error("Opening TSV file: {}", grant_file)
    with open(grant_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(GRANT_COLUMNS)
        for entry in grant_entries:
            writer.writerow([entry[col] for col in GRANT_COLUMNS])
        f.flush()
    log_error("Wrote {} grant rows to {}", len(grant_entries), grant_file)

    # Write address debug
    log_error("Opening TSV file: {}", debug_address_file)
    with open(debug_address_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(DEBUG_ADDRESS_COLUMNS)
        for entry in debug_address_entries:
            writer.writerow([entry[col] for col in DEBUG_ADDRESS_COLUMNS])
        f.flush()
    log_error("Wrote {} address debug rows to {}", len(debug_address_entries), debug_address_file)

    # Write grant debug
    log_error("Opening TSV file: {}", debug_grant_file)
    with open(debug_grant_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(DEBUG_GRANT_COLUMNS)
        for entry in debug_grant_entries:
            writer.writerow([entry[col] for col in DEBUG_GRANT_COLUMNS])
        f.flush()
    log_error("Wrote {} grant debug rows to {}", len(debug_grant_entries), debug_grant_file)

    # Sort and write invalid EINs
    sorted_invalid_eins = sorted(invalid_ein_entries, key=lambda x: (x['xml_ein'], x['tsv_ein'], x['filer_name'].lower()))
    log_error("Opening TSV file: {}", invalid_ein_file)
    with open(invalid_ein_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(INVALID_EIN_COLUMNS)
        for entry in sorted_invalid_eins:
            writer.writerow([entry[col] for col in INVALID_EIN_COLUMNS])
        f.flush()
    log_error("Wrote {} invalid EIN rows to {}", len(sorted_invalid_eins), invalid_ein_file)

    # Filter and sort PO Box matches for filers only
    filer_po_boxes = [entry for entry in po_box_entries if entry['type'] == 'filer']
    sorted_po_boxes = sorted(filer_po_boxes, key=lambda x: (x['zip_code'], x['po_box'], x['org_name'].lower()))
    log_error("Filtered {} PO Box entries to {} filer entries", len(po_box_entries), len(filer_po_boxes))
    log_error("Opening TSV file: {}", po_box_file)
    with open(po_box_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(PO_BOX_COLUMNS)
        for entry in sorted_po_boxes:
            writer.writerow([entry[col] for col in PO_BOX_COLUMNS])
        f.flush()
    
    log_error("Wrote {} filer PO Box rows to {}", len(sorted_po_boxes), po_box_file)
    log_error("Total unique EIN mismatches: {}", len(ein_mismatch_set))

def signal_handler(sig, frame):
    if fast_interrupt:
        os._exit(0)
    log_error("Received interrupt signal, writing partial outputs")
    write_outputs(args.output_dir)
    sys.exit(0)

def main():
    global verbose, quiet, fast_interrupt, debug_limit, skip_address_errors, sample_xml, log_zip_errors, done_queuing, total_addresses, total_grants, total_990pf_rows, total_address_errors, total_queue_puts, total_tasks_done, args
    parser = argparse.ArgumentParser(description="Extract addresses and grants from IRS 990 XML files.")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--source-dir", type=str, default=".", help="Directory containing TSV files")
    parser.add_argument("--zip-dir", type=str, default="..", help="Directory containing ZIP files")
    parser.add_argument("--output-dir", type=str, default=".", help="Directory for output TSV files")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument("--fast-interrupt", action="store_true", help="Exit immediately on interrupt without writing outputs")
    parser.add_argument("--debug-limit", type=int, default=100000, help="Limit debug entries for testing (e.g., 100000)")
    parser.add_argument("--skip-address-errors", action="store_true", help="Continue processing grants despite address errors")
    parser.add_argument("--debug-success-grants", action="store_true", help="Include successful grants in debug output")
    parser.add_argument("--sample-xml", type=str, default=None, help="Directory to save failing XMLs for debugging")
    parser.add_argument("--log-zip-errors", action="store_true", help="Log invalid ZIP codes to zip_errors.tsv")
    parser.add_argument("--no-threads", action="store_true", help="Run single-threaded for debugging")
    parser.add_argument("--worker-threads", type=int, default=2, help="Number of worker threads for processing")
    parser.add_argument("--writer-threads", type=int, default=1, help="Number of TSV writer threads")
    args = parser.parse_args()

    verbose = args.verbose
    quiet = args.quiet
    fast_interrupt = args.fast_interrupt
    debug_limit = args.debug_limit
    skip_address_errors = args.skip_address_errors or True
    debug_success_grants = args.debug_success_grants
    sample_xml = args.sample_xml
    log_zip_errors = args.log_zip_errors
    start_year = args.start_year
    end_year = args.end_year
    worker_threads = args.worker_threads
    writer_threads = args.writer_threads
    source_dir = args.source_dir
    zip_dir = args.zip_dir
    output_dir = args.output_dir

    if args.no_threads:
        worker_threads = 1

    listener = setup_logging(output_dir)
    log_error("Logging initialized successfully")
    log_error("Starting with fast_interrupt={}, debug_limit={}, skip_address_errors={}, sample_xml={}, log_zip_errors={}, no_threads={}, worker_threads={}", 
              fast_interrupt, debug_limit, skip_address_errors, sample_xml, log_zip_errors, args.no_threads, worker_threads)

    if worker_threads < 1 or writer_threads < 1:
        raise ValueError("Number of threads must be at least 1")
    if not os.path.isdir(source_dir):
        raise ValueError(f"Source directory {source_dir} does not exist")
    if not os.path.isdir(zip_dir):
        raise ValueValueError(f"ZIP directory {zip_dir} does not exist")
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    writer_threads_list = []

    signal.signal(signal.SIGINT, signal_handler)

    try:
        zip_index = build_zip_index(zip_dir, start_year, end_year)
        if not zip_index:
            print("No ZIP files found in {} for years {}-{}", zip_dir, start_year, end_year + 1)
            return

        rows = read_tsv_files(source_dir, start_year, end_year)
        if not rows:
            print("No valid TSV files found in {}. Ensure 'charity_latest.tsv' or 'charities_<org_type>_<year>.tsv' exist.", source_dir)
            return

        log_error("Processing {} total rows", len(rows))
        try:
            process_rows(rows, worker_threads, zip_index, start_year, end_year, output_dir)
            write_outputs(output_dir)
        except Exception as e:
            log_error("Error during processing: {}", str(e), exc_info=True)
            write_outputs(output_dir)

    finally:
        done_queuing = True
        for _ in range(writer_threads):
            tsv_write_queue.put(None)
        for thread in writer_threads_list:
            thread.join()
        listener.stop()
        log_error("Queue summary: {} items put, {} tasks done", total_queue_puts, total_tasks_done)

    print(f"Total addresses extracted: {len(address_entries)}")
    print(f"Total grants extracted: {total_grants}")
    print(f"Total 990PF rows processed: {total_990pf_rows}")
    print(f"Total address errors: {total_address_errors}")
    print(f"Queue summary: {total_queue_puts} items put, {total_tasks_done} tasks done")
    print(f"Invalid EINs found: {len(invalid_ein_entries)}")
    print(f"PO Boxes found: {len(po_box_entries)}")

if __name__ == "__main__":
    main()