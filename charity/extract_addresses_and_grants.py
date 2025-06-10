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
    etree.XPath(".//irs:Filer/irs:USAddress/irs:AddressLine1Txt", namespaces=NAMESPACES),
    etree.XPath(".//irs:Filer/irs:USAddress/irs:AddressLine2Txt", namespaces=NAMESPACES),
    etree.XPath(".//irs:Filer/irs:USAddress/irs:CityNm", namespaces=NAMESPACES),
    etree.XPath(".//irs:Filer/irs:USAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES),
    etree.XPath(".//irs:Filer/irs:USAddress/irs:ZIPCd", namespaces=NAMESPACES),
    etree.XPath(".//Filer/USAddress/AddressLine1Txt", namespaces=NAMESPACES),
    etree.XPath(".//Filer/USAddress/AddressLine2Txt", namespaces=NAMESPACES),
    etree.XPath(".//Filer/USAddress/CityNm", namespaces=NAMESPACES),
    etree.XPath(".//Filer/USAddress/StateAbbreviationCd", namespaces=NAMESPACES),
    etree.XPath(".//Filer/USAddress/ZIPCd", namespaces=NAMESPACES),
]
GRANT_XPATHS_990PF = [
    etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:GrantOrContributionPdDurYrGrp", namespaces=NAMESPACES),
    etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp", namespaces=NAMESPACES),
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
]
ADDRESS_COLUMNS = ["filer_ein", "filer_name", "canonical_address"]
GRANT_COLUMNS = ["filer_ein", "filer_name", "grant_ein", "grant_amt", "tax_year"]
DEBUG_ADDRESS_COLUMNS = ["filer_ein", "filer_name", "xml_filename", "raw_components", "canonical_address", "status", "reason"]
DEBUG_GRANT_COLUMNS = ["filer_ein", "filer_name", "xml_filename", "grantee_name", "grant_ein", "grant_address", "grant_amt", "tax_year", "status", "reason"]
INVALID_EIN_COLUMNS = ["tsv_ein", "xml_ein", "filer_name", "xml_filename", "reason"]
CSV_QUOTE_FIELDS = {
    'addresses': ['filer_name', 'canonical_address'],
    'grants': ['filer_name', 'grant_ein'],
    'debug_address': ['filer_name', 'xml_filename', 'raw_components', 'canonical_address', 'reason'],
    'debug_grant': ['filer_name', 'xml_filename', 'grantee_name', 'grant_ein', 'grant_address', 'reason'],
    'invalid_eins': ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename', 'reason']
}
EIN_REGEX = re.compile(r'^\d{9}$')

# Global variables
logger = None
verbose = False
quiet = False
total_addresses = 0
total_grants = 0
total_queue_puts = 0
total_tasks_done = 0
tsv_write_queue = queue.Queue(maxsize=20000)
done_queuing = False
queue_lock = threading.Lock()
address_entries = []
grant_entries = []
debug_address_entries = []
debug_grant_entries = []
invalid_ein_entries = []
write_lock = threading.Lock()

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
    if args:
        logger.info(msg_format.format(*args), extra={'ein': ein} if ein else None, exc_info=exc_info)
    else:
        logger.info(msg_format, extra={'ein': ein} if ein else None, exc_info=exc_info)

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
                        if filename in index:
                            log_error(
                                "Duplicate XML filename {} found in ZIP {}, overwriting with {}",
                                filename, index[filename][0], zip_path
                            )
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
        log_error("No TSV files found in {}. Expected 'charity_latest.tsv' or 'charities_<org_type>_<year>.tsv'. Found: {}", 
                  source_dir, os.listdir(source_dir))
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
    return rows

def canonicalize_address(address_components):
    if not address_components:
        return ""
    address_str = " ".join(comp for comp in address_components if comp)
    try:
        parsed = parse_address(address_str)
        normalized = expand_address(address_str)
        canonical = normalized[0] if normalized else address_str
        zip_match = re.search(r'\b(\d{5})\d{4}\b', canonical)
        if zip_match:
            canonical = canonical.replace(zip_match.group(0), zip_match.group(1))
        return canonical
    except Exception as e:
        log_error("Error canonicalizing address '{}': {}", address_str, str(e), exc_info=True)
        return address_str

def parse_address_and_grants(xml_content, xml_filename, row, zip_index):
    global total_addresses, total_grants, total_queue_puts
    try:
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
            with write_lock:
                invalid_ein_entries.append({
                    'tsv_ein': tsv_ein,
                    'xml_ein': '',
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'reason': 'No EIN found in XML'
                })
                debug_address_entries.append({
                    'filer_ein': tsv_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'raw_components': '',
                    'canonical_address': '',
                    'status': 'skipped',
                    'reason': 'No EIN found in XML'
                })
            log_error("No EIN found in XML {} for TSV EIN={}, skipping", xml_filename, tsv_ein, ein=tsv_ein)
            return False
        
        # Validate XML EIN
        if not EIN_REGEX.match(xml_ein):
            with write_lock:
                invalid_ein_entries.append({
                    'tsv_ein': tsv_ein,
                    'xml_ein': xml_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'reason': f"Invalid XML EIN format: {xml_ein}"
                })
                debug_address_entries.append({
                    'filer_ein': tsv_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'raw_components': '',
                    'canonical_address': '',
                    'status': 'skipped',
                    'reason': f"Invalid XML EIN: {xml_ein}"
                })
            log_error("Invalid XML EIN {} for TSV EIN={} in {}, skipping", xml_ein, tsv_ein, xml_filename, ein=tsv_ein)
            return False
        
        # Compare TSV and XML EINs
        if tsv_ein != xml_ein:
            with write_lock:
                invalid_ein_entries.append({
                    'tsv_ein': tsv_ein,
                    'xml_ein': xml_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'reason': 'TSV EIN differs from XML EIN'
                })
            log_error("TSV EIN {} differs from XML EIN {} in {}, using XML EIN", tsv_ein, xml_ein, xml_filename, ein=tsv_ein)
        
        filer_ein = xml_ein
        
        # Parse address
        address_components = []
        for xpath in ADDRESS_XPATHS:
            elements = xpath(root)
            if elements and elements[0].text:
                address_components.append(elements[0].text.strip())
        canonical_address = canonicalize_address(address_components)
        raw_components_str = ";".join(address_components)
        if canonical_address:
            with write_lock:
                address_entries.append({
                    'filer_ein': filer_ein,
                    'filer_name': filer_name,
                    'canonical_address': canonical_address
                })
                debug_address_entries.append({
                    'filer_ein': filer_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'raw_components': raw_components_str,
                    'canonical_address': canonical_address,
                    'status': 'success',
                    'reason': ''
                })
            with queue_lock:
                total_addresses += 1
                total_queue_puts += 1
            if verbose:
                log_error(
                    "Parsed address '{}' for EIN={} in {}",
                    canonical_address, filer_ein, xml_filename, ein=filer_ein
                )
        else:
            with write_lock:
                debug_address_entries.append({
                    'filer_ein': filer_ein,
                    'filer_name': filer_name,
                    'xml_filename': xml_filename,
                    'raw_components': raw_components_str,
                    'canonical_address': '',
                    'status': 'skipped',
                    'reason': 'No valid address found'
                })
            log_error("No valid address found for EIN={} in {}", filer_ein, xml_filename, ein=filer_ein)
        
        # Parse grants for 990PF
        if row['form_type'] == '990PF':
            grant_count = 0
            for xpath_grant in GRANT_XPATHS_990PF:
                grant_elements = xpath_grant(root)
                grant_count += len(grant_elements)
                for element in grant_elements:
                    ein_elem = element.xpath(".//irs:EIN | .//irs:RecipientEIN", namespaces=NAMESPACES)
                    name_elem = None
                    for xpath_name in GRANTEE_NAME_XPATHS:
                        names = xpath_name(element)
                        if names and names[0].text:
                            name_elem = names[0]
                            break
                    amount_elem = None
                    for xpath_amount in AMOUNT_XPATHS:
                        amounts = xpath_amount(element)
                        if amounts and amounts[0].text:
                            amount_elem = amounts[0]
                            break
                    address_elems = element.xpath(
                        ".//irs:USAddress/* | .//irs:ForeignAddress/*",
                        namespaces=NAMESPACES
                    )
                    grant_ein = ein_elem[0].text.strip() if ein_elem and ein_elem[0].text else "Unknown"
                    grantee_name = name_elem.text.strip() if name_elem is not None and name_elem.text is not None else "Unknown"
                    grant_address_components = [elem.text.strip() for elem in address_elems if elem.text]
                    grant_address = canonicalize_address(grant_address_components) if grant_address_components else ""
                    
                    if amount_elem and amount_elem.text:
                        try:
                            grant_amt = int(float(amount_elem.text.strip()))
                            if grant_amt > 0:
                                if grant_ein == "Unknown" and grant_address:
                                    grant_ein = f"Address:{grant_address}"
                                if grant_ein != "Unknown":
                                    with write_lock:
                                        grant_entries.append({
                                            'filer_ein': filer_ein,
                                            'filer_name': filer_name,
                                            'grant_ein': grant_ein,
                                            'grant_amt': grant_amt,
                                            'tax_year': row['tax_year']
                                        })
                                        debug_grant_entries.append({
                                            'filer_ein': filer_ein,
                                            'filer_name': filer_name,
                                            'xml_filename': xml_filename,
                                            'grantee_name': grantee_name,
                                            'grant_ein': grant_ein,
                                            'grant_address': grant_address,
                                            'grant_amt': grant_amt,
                                            'tax_year': row['tax_year'],
                                            'status': 'success',
                                            'reason': ''
                                        })
                                    with queue_lock:
                                        total_queue_puts += 1
                                    total_grants += 1
                                    if verbose:
                                        log_error(
                                            "Found grant: EIN/Address={}, Name={}, Amount={} for EIN={} in {}",
                                            grant_ein, grantee_name, grant_amt, filer_ein, xml_filename,
                                            ein=filer_ein
                                        )
                                else:
                                    with write_lock:
                                        debug_grant_entries.append({
                                            'filer_ein': filer_ein,
                                            'filer_name': filer_name,
                                            'xml_filename': xml_filename,
                                            'grantee_name': grantee_name,
                                            'grant_ein': 'Unknown',
                                            'grant_address': grant_address,
                                            'grant_amt': grant_amt,
                                            'tax_year': row['tax_year'],
                                            'status': 'skipped',
                                            'reason': 'No valid EIN or address'
                                        })
                                    log_error(
                                        "Skipped grant with no EIN or address for EIN={} in {}",
                                        filer_ein, xml_filename, ein=filer_ein
                                    )
                        except (ValueError, TypeError) as e:
                            with write_lock:
                                debug_grant_entries.append({
                                    'filer_ein': filer_ein,
                                    'filer_name': filer_name,
                                    'xml_filename': xml_filename,
                                    'grantee_name': grantee_name,
                                    'grant_ein': grant_ein,
                                    'grant_address': grant_address,
                                    'grant_amt': '',
                                    'tax_year': row['tax_year'],
                                    'status': 'error',
                                    'reason': f"Invalid grant amount: {str(e)}"
                                })
                            log_error(
                                "Invalid grant amount '{}' in {} for EIN={}: {}",
                                amount_elem.text, xml_filename, filer_ein, str(e),
                                ein=filer_ein
                            )
                    else:
                        with write_lock:
                            debug_grant_entries.append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'xml_filename': xml_filename,
                                'grantee_name': grantee_name,
                                'grant_ein': grant_ein,
                                'grant_address': grant_address,
                                'grant_amt': '',
                                'tax_year': row['tax_year'],
                                'status': 'skipped',
                                'reason': 'No grant amount found'
                            })
                        log_error(
                            "No grant amount found for grant in {} for EIN={}", xml_filename, filer_ein, ein=filer_ein
                        )
            if verbose:
                log_error("Found {} grant elements in {} for EIN={}", grant_count, xml_filename, filer_ein, ein=filer_ein)
        
        return True
    except Exception as e:
        log_error(
            "Error parsing XML {} for EIN={}: {}", xml_filename, row['filer_ein'], str(e),
            exc_info=True, ein=row['filer_ein']
        )
        with write_lock:
            debug_address_entries.append({
                'filer_ein': row['filer_ein'],
                'filer_name': row['filer_name'],
                'xml_filename': xml_filename,
                'raw_components': '',
                'canonical_address': '',
                'status': 'error',
                'reason': str(e)
            })
        return False

def process_rows(rows, worker_threads, zip_index, start_year, end_year):
    zip_cache = {}
    initialize_thread_local_counters()
    total_skipped = 0

    def process_row(row):
        nonlocal total_skipped
        xml_path = row.get('xml_name', '')
        if not xml_path:
            log_error("No xml_name for EIN={}, skipping", row['filer_ein'], ein=row['filer_ein'])
            with write_lock:
                debug_address_entries.append({
                    'filer_ein': row['filer_ein'],
                    'filer_name': row['filer_name'],
                    'xml_filename': '',
                    'raw_components': '',
                    'canonical_address': '',
                    'status': 'skipped',
                    'reason': 'Missing xml_name'
                })
            file_counter_local.skipped += 1
            total_skipped += 1
            return
        try:
            parts = xml_path.split('/')
            if len(parts) < 2:
                log_error(
                    "Invalid xml_path format {} for EIN={}, skipping",
                    xml_path, row['filer_ein'], ein=row['filer_ein']
                )
                with write_lock:
                    debug_address_entries.append({
                        'filer_ein': row['filer_ein'],
                        'filer_name': row['filer_name'],
                        'xml_filename': xml_path,
                        'raw_components': '',
                        'canonical_address': '',
                        'status': 'skipped',
                        'reason': 'Invalid xml_path format'
                    })
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                log_error(
                    "No file named {} found in ZIP index for EIN={}, skipping",
                    xml_filename, row['filer_ein'], ein=row['filer_ein']
                )
                with write_lock:
                    debug_address_entries.append({
                        'filer_ein': row['filer_ein'],
                        'filer_name': row['filer_name'],
                        'xml_filename': xml_filename,
                        'raw_components': '',
                        'canonical_address': '',
                        'status': 'skipped',
                        'reason': 'XML not found in ZIP index'
                    })
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            zip_path, internal_path = zip_index[xml_filename]
            zip_year_match = re.match(r'.*(\d{4})', os.path.basename(zip_path))
            if not zip_year_match:
                log_error(
                    "Invalid ZIP path format {} for EIN={}, skipping",
                    zip_path, row['filer_ein'], ein=row['filer_ein']
                )
                with write_lock:
                    debug_address_entries.append({
                        'filer_ein': row['filer_ein'],
                        'filer_name': row['filer_name'],
                        'xml_filename': xml_filename,
                        'raw_components': '',
                        'canonical_address': '',
                        'status': 'skipped',
                        'reason': 'Invalid ZIP path format'
                    })
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            zip_year = int(zip_year_match.group(1))
            if zip_year < start_year or zip_year > end_year + 1:
                log_error(
                    "ZIP year {} outside range {}-{} for xml_path {} and EIN={}, skipping",
                    zip_year, start_year, end_year + 1, xml_path, row['filer_ein'],
                    ein=row['filer_ein']
                )
                with write_lock:
                    debug_address_entries.append({
                        'filer_ein': row['filer_ein'],
                        'filer_name': row['filer_name'],
                        'xml_filename': xml_filename,
                        'raw_components': '',
                        'canonical_address': '',
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
                parse_address_and_grants(xml_content, xml_filename, row, zip_index)
        except Exception as e:
            log_error(
                "Error processing row for XML {} and EIN={}: {}",
                xml_path, row['filer_ein'], str(e), exc_info=True, ein=row['filer_ein']
            )
            with write_lock:
                debug_address_entries.append({
                    'filer_ein': row['filer_ein'],
                    'filer_name': row['filer_name'],
                    'xml_filename': xml_path,
                    'raw_components': '',
                    'canonical_address': '',
                    'status': 'error',
                    'reason': str(e)
                })
            file_counter_local.skipped += 1
            total_skipped += 1

    with ThreadPoolExecutor(max_workers=worker_threads) as executor:
        futures = [executor.submit(process_row, row) for row in rows]
        with tqdm(total=len(rows), desc="Processing rows") as pbar:
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    log_error("Error in future: {}", str(e), exc_info=True)
                pbar.update(1)
    
    for zip_file in zip_cache.values():
        zip_file.close()
    
    log_error("Total rows skipped: {}", total_skipped)

def write_outputs(output_dir):
    global address_entries, grant_entries, debug_address_entries, debug_grant_entries, invalid_ein_entries
    address_file = os.path.join(output_dir, "charity_addresses.tsv")
    grant_file = os.path.join(output_dir, "inferred_grants.tsv")
    debug_address_file = os.path.join(output_dir, "address_debug.tsv")
    debug_grant_file = os.path.join(output_dir, "grant_debug.tsv")
    invalid_ein_file = os.path.join(output_dir, "invalid_eins.tsv")

    # Deduplicate and sort addresses
    unique_addresses = {
        (entry['filer_ein'], entry['filer_name'], entry['canonical_address']): entry
        for entry in address_entries
    }
    sorted_addresses = sorted(
        unique_addresses.values(),
        key=lambda x: (x['filer_name'].lower(), x['canonical_address'].lower(), x['filer_ein'])
    )
    with open(address_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(ADDRESS_COLUMNS)
        for entry in sorted_addresses:
            writer.writerow([entry[col] for col in ADDRESS_COLUMNS])
    log_error("Wrote {} unique address rows to {}", len(sorted_addresses), address_file)

    # Write grants
    with open(grant_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(GRANT_COLUMNS)
        for entry in grant_entries:
            writer.writerow([entry[col] for col in GRANT_COLUMNS])
    log_error("Wrote {} grant rows to {}", len(grant_entries), grant_file)

    # Write address debug
    with open(debug_address_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(DEBUG_ADDRESS_COLUMNS)
        for entry in debug_address_entries:
            writer.writerow([entry[col] for col in DEBUG_ADDRESS_COLUMNS])
    log_error("Wrote {} address debug rows to {}", len(debug_address_entries), debug_address_file)

    # Write grant debug
    with open(debug_grant_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(DEBUG_GRANT_COLUMNS)
        for entry in debug_grant_entries:
            writer.writerow([entry[col] for col in DEBUG_GRANT_COLUMNS])
    log_error("Wrote {} grant debug rows to {}", len(debug_grant_entries), debug_grant_file)

    # Write invalid EINs
    with open(invalid_ein_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(INVALID_EIN_COLUMNS)
        for entry in invalid_ein_entries:
            writer.writerow([entry[col] for col in INVALID_EIN_COLUMNS])
    log_error("Wrote {} invalid EIN rows to {}", len(invalid_ein_entries), invalid_ein_file)

def signal_handler(sig, frame):
    log_error("Received interrupt signal, writing partial outputs")
    write_outputs(args.output_dir)
    sys.exit(0)

def main():
    global verbose, quiet, done_queuing, total_addresses, total_grants, total_queue_puts, total_tasks_done, args
    parser = argparse.ArgumentParser(
        description="Extract addresses and grants from IRS 990 XML files."
    )
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument(
        "--source-dir",
        type=str,
        default=".",
        help="Directory containing TSV files"
    )
    parser.add_argument(
        "--zip-dir",
        type=str,
        default="..",
        help="Directory containing ZIP files"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Directory for output TSV files"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument(
        "--worker-threads",
        type=int,
        default=16,
        help="Number of worker threads for processing"
    )
    parser.add_argument(
        "--writer-threads",
        type=int,
        default=1,
        help="Number of TSV writer threads"
    )
    args = parser.parse_args()

    verbose = args.verbose
    quiet = args.quiet
    start_year = args.start_year
    end_year = args.end_year
    worker_threads = args.worker_threads
    writer_threads = args.writer_threads
    source_dir = args.source_dir
    zip_dir = args.zip_dir
    output_dir = args.output_dir

    if worker_threads < 1 or writer_threads < 1:
        raise ValueError("Number of threads must be at least 1")
    if not os.path.isdir(source_dir):
        raise ValueError(f"Source directory {source_dir} does not exist")
    if not os.path.isdir(zip_dir):
        raise ValueError(f"ZIP directory {zip_dir} does not exist")
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    listener = setup_logging(output_dir)
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

        process_rows(rows, worker_threads, zip_index, start_year, end_year)
        write_outputs(output_dir)

    finally:
        done_queuing = True
        for _ in range(writer_threads):
            tsv_write_queue.put(None)
        for thread in writer_threads_list:
            thread.join()
        listener.stop()
        log_error("Queue summary: {} items put, {} tasks done", total_queue_puts, total_tasks_done)

    print(f"Total addresses extracted: {len(address_entries)} (unique: {len(set((e['filer_ein'], e['filer_name'], e['canonical_address']) for e in address_entries))})")
    print(f"Total grants extracted: {total_grants}")
    print(f"Queue summary: {total_queue_puts} items put, {total_tasks_done} tasks done")
    print(f"Invalid EINs found: {len(invalid_ein_entries)}")

if __name__ == "__main__":
    main()