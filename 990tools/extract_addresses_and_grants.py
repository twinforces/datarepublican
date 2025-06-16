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
import subprocess
import signal
import sys
import pickle
import json
import psutil
import xml.etree.ElementTree as ET
import traceback

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
    etree.XPath("./irs:RecipientBusinessName/irs:BusinessNameLine1Txt | .//*[local-name()='BusinessNameLine1Txt']", namespaces=NAMESPACES),
    etree.XPath("./irs:RecipientName", namespaces=NAMESPACES),
]
FILER_EIN_XPATHS = [
    etree.XPath(".//irs:Filer/irs:EIN", namespaces=NAMESPACES),
    etree.XPath(".//Filer/EIN", namespaces=NAMESPACES),
]
AMOUNT_XPATHS = [
    etree.XPath("./irs:Amt | .//*[local-name()='Amt']", namespaces=NAMESPACES),
    etree.XPath("./irs:GrantOrContributionAmt | ./irs:ContributionAmt", namespaces=NAMESPACES),
    etree.XPath("./irs:TotalGrantOrContriPdDurYrAmt", namespaces=NAMESPACES),
    etree.XPath(".//Amt", namespaces={}),
]
GRANTEE_ADDRESS_XPATHS = [
    etree.XPath(".//irs:RecipientUSAddress/* | .//irs:RecipientForeignAddress/*", namespaces=NAMESPACES),
    etree.XPath(".//RecipientUSAddress/* | .//RecipientForeignAddress/*", namespaces={}),
]
ZIP_REGEX = re.compile(r'^\d{5}$')
PO_BOX_REGEX = re.compile(r'P.*BOX\s+\w+', re.IGNORECASE)
PO_BOX_NUMBER_REGEX = re.compile(r'\b\d+\b')
STOP_WORDS = {'and', 'the', 'of', 'for', 'in', 'to', 'a', 'an'}
ADDRESS_COLUMNS = ['filer_ein', 'filer_name', 'canonical_address', 'tax_year', 'zip_code', 'po_box']
GRANT_COLUMNS = ['filer_ein', 'filer_name', 'grant_ein', 'grant_amt', 'tax_year', 'filer_canonical_address', 'grantee_canonical_address']
DEBUG_ADDRESS_COLUMNS = ['filer_ein', 'filer_name', 'xml_filename', 'raw_components', 'canonical_address', 'raw_zip', 'zip_code', 'status', 'reason']
DEBUG_GRANT_COLUMNS = ['filer_ein', 'filer_name', 'xml_filename', 'grantee_name', 'grant_ein', 'grant_address', 'grant_amt', 'tax_year', 'status', 'heuristic_score', 'reason']
INVALID_EIN_COLUMNS = ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename', 'reason']
PO_BOX_COLUMNS = ['po_box', 'zip_code', 'ein', 'org_name']
ZIP_ERROR_COLUMNS = ['xml_filename', 'filer_ein', 'zip_code', 'raw_zip', 'address']
CSV_QUOTE_FIELDS = {
    'addresses': ['filer_name', 'canonical_address'],
    'grants': ['filer_name', 'grant_ein'],
    'debug_address': ['filer_name', 'xml_filename', 'raw_components', 'canonical_address', 'reason'],
    'debug_grant': ['filer_name', 'xml_filename', 'grantee_name', 'grant_ein', 'grant_address', 'reason'],
    'invalid_eins': ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename', 'reason'],
    'po_box_matches': ['org_name'],
    'zip_errors': ['xml_filename', 'filer_ein', 'raw_zip', 'address']
}
EIN_REGEX = re.compile(r'^\d{9}$')

USPS_FIXES = {
            'Saint': 'Street', 'St': 'Street', 'Ave': 'Avenue', 'Av': 'Avenue',
            'Blvd': 'Boulevard', 'Dr': 'Drive', 'Ln': 'Lane', 'Rd': 'Road',
            'Cir': 'Circle', 'Ct': 'Court', 'Pl': 'Place', 'Ter': 'Terrace',
            'Pkwy': 'Parkway', 'Hwy': 'Highway', 'Sq': 'Square'
}

logger = None
verbose = False
quiet = False
debug_grant_entries = []
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
duplicate_grant_count = 0
unique_grant_eins = set()
grant_ein_counts = defaultdict(int)
results_queue = None
grants_queue = None
zip_index_lock = threading.Lock()
address_entries = []
debug_address_entries = []
invalid_ein_entries = []
debug_success_grants = False
po_box_entries = []
zip_index = {}
zip_code_index = {}
po_box_zip_index = {}
ein_mismatch_set = set()
thread_local = threading.local()
file_counter_local = threading.local()
filer_eins = {}
status_counts = {}

def log_error(msg_format, *args, ein=None, exc_info=False):
    if not quiet and logger:
        try:
            logger.info(msg_format.format(*args), extra={'ein': ein} if ein else None, exc_info=exc_info)
        except Exception as e:
            logger.info("Log formatting error: {}; args: {}", str(e), args)

def main():
    global verbose, quiet, debug_limit, skip_address_errors, sample_xml, log_zip_errors, status_counts
    global total_addresses, total_grants, total_990pf_rows, total_address_errors, total_queue_puts, total_tasks_done
    global args, zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index
    
    parser = argparse.ArgumentParser(description="Extract addresses and grants from IRS 990 XML files.")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--source-dir", type=str, default=".", help="Directory containing charity_latest.tsv")
    parser.add_argument("--zip-dir", type=str, default="..", help="Directory containing ZIP files")
    parser.add_argument("--cache-dir", type=str, default="./_cache", help="Directory for cache files")
    parser.add_argument("--output-dir", type=str, default="./_output", help="Directory for output TSV files")
    parser.add_argument("--charity-source", type=str, help="Path to charity_latest.tsv", default=None)
    parser.add_argument("--force-reprocess", action="store_true", help="Force reprocessing despite cache")
    parser.add_argument("--merge-batch-size", type=int, default=1000, help="Batch size for queuing results")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument("--no-threads", action="store_true", help="Run single-threaded")
    parser.add_argument("--worker-threads", type=int, default=16, help="Number of worker threads")
    parser.add_argument("--debug-limit", type=int, default=100000, help="Limit debug entries")
    parser.add_argument("--skip-address-errors", action="store_true", help="Continue despite address errors")
    parser.add_argument("--debug-success-grants", action="store_true", help="Debug successful grants")
    parser.add_argument("--sample-xml", type=str, default=None, help="Directory to save failing XMLs")
    parser.add_argument("--log-zip-errors", action="store_true", help="Log invalid ZIP codes")
    args = parser.parse_args()

    verbose = args.verbose
    quiet = args.quiet
    debug_limit = args.debug_limit
    skip_address_errors = args.skip_address_errors
    debug_success_grants = args.debug_success_grants
    sample_xml = args.sample_xml
    log_zip_errors = args.log_zip_errors
    if args.no_threads:
        args.worker_threads = 1
    if not os.path.isdir(args.cache_dir):
        os.makedirs(args.cache_dir)
    if not os.path.isdir(args.output_dir):
        os.makedirs(args.output_dir)
    if not os.path.isdir(args.source_dir):
        raise ValueError(f"Source directory {args.source_dir} does not exist")
    if not os.path.isdir(args.zip_dir):
        raise ValueError(f"ZIP directory {args.zip_dir} does not exist")
    if args.charity_source is None:
        args.charity_source = os.path.join(args.source_dir, 'charity_latest.tsv')

    listener = setup_logging(args.output_dir)
    signal.signal(signal.SIGINT, signal_handler)
    try:
        cache_valid, cached_data = load_caches(args.cache_dir, args.start_year, args.end_year, args.zip_dir)
        rows = read_tsv_files(args.charity_source, args.start_year, args.end_year)
        if not rows:
            if not cache_valid or args.force_reprocess:
                save_caches(args.cache_dir, args.start_year, args.end_year, compute_zip_checksums(args.zip_dir), zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index)
            write_outputs(args.output_dir, cache_valid and not args.force_reprocess)
            log_error("No rows in {}. Ensure {} exists.", args.charity_source, args.charity_source)
            return

        if cache_valid and not args.force_reprocess:
            #global zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index
            zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index = cached_data
            log_error("Loaded caches: {} addresses, {} PO boxes, {} ZIP index entries", len(address_entries), len(po_box_entries), len(zip_code_index))
        else:
            zip_index = build_zip_index(args.zip_dir, args.start_year, args.end_year)
            if not zip_index:
                log_error("No ZIP files found in {} for years {}-{}", args.zip_dir, args.start_year, args.end_year + 1)
                return

        
        log_error("Processing {} rows from {}", len(rows), args.charity_source)
        process_rows(rows, args.worker_threads, zip_index, args.start_year, args.end_year, args.output_dir)
        
        if not cache_valid or args.force_reprocess:
            save_caches(args.cache_dir, args.start_year, args.end_year, compute_zip_checksums(args.zip_dir), zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index)
        write_outputs(args.output_dir, cache_valid and not args.force_reprocess)

        if not cache_valid or args.force_reprocess:
            save_caches(args.cache_dir, args.start_year, args.end_year, compute_zip_checksums(args.zip_dir), zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index)
    except Exception as e:
        log_error("Error during processing: {}", str(e), exc_info=True)
        write_outputs(args.output_dir, cache_valid and not args.force_reprocess)
    finally:
        if grants_queue:
            grants_queue.put(None)
        listener.stop()

    log_file = os.path.join(args.output_dir, 'extract_addresses_grants_log.txt')
    print(f"Log file written to: {log_file}")
    print(f"Total addresses extracted: {len(address_entries)}")
    print(f"Total grants extracted: {total_grants}")
    print(f"Total 990PF rows processed: {total_990pf_rows}")
    print(f"Total address errors: {total_address_errors}")
    print(f"Queue summary: {total_queue_puts} items put, {total_tasks_done} tasks done")
    print(f"Invalid EINs found: {len(invalid_ein_entries)}")
    print(f"PO Boxes found: {len(po_box_entries)}")
    print("\nProgress Summary:")
    print(f"- Total rows processed: {len(rows)}")
    print(f"- 990PF rows processed: {total_990pf_rows}")
    print(f"- Grants written to inferred_grants.tsv: {total_grants}")
    print(f"- Grants skipped: {status_counts.get('skipped', 0)}")
    print(f"- Output files in: {args.output_dir}")

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
    console_handler.setLevel(logging.DEBUG if verbose else logging.ERROR)
    console_handler.setFormatter(formatter)
    
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers = [queue_handler, console_handler] if not quiet else []
    return listener

def compute_zip_checksums(zip_dir):
    checksums = {}
    try:
        result = subprocess.run(['sha256sum'] + glob.glob(os.path.join(zip_dir, '*.zip')), capture_output=True, text=True)
        for line in result.stdout.splitlines():
            hash_val, path = line.split(None, 1)
            checksums[os.path.normpath(path)] = hash_val
    except (subprocess.CalledProcessError, FileNotFoundError):
        import hashlib
        for zip_path in glob.glob(os.path.join(zip_dir, '*.zip')):
            with open(zip_path, 'rb') as f:
                sha256 = hashlib.sha256()
                while chunk := f.read(8192):
                    sha256.update(chunk)
                checksums[os.path.normpath(zip_path)] = sha256.hexdigest()
    return checksums

def load_caches(cache_dir, start_year, end_year, zip_dir):
    cache_valid = False
    cached_data = None
    checksum_file = os.path.join(cache_dir, f'zip_checksums_{start_year}_{end_year}.json')
    print("loading caches")
    if os.path.exists(checksum_file):
        try:
            with open(checksum_file, 'r') as f:
                cached_checksums = json.load(f)
            current_checksums = compute_zip_checksums(zip_dir)
            if cached_checksums == current_checksums:
                zip_index_file = os.path.join(cache_dir, f'cached_zip_index_{start_year}_{end_year}.pkl')
                addresses_file = os.path.join(cache_dir, f'cached_addresses_{start_year}_{end_year}.pkl')
                mappings_file = os.path.join(cache_dir, f'cached_mappings_{start_year}_{end_year}.pkl')
                if all(os.path.exists(f) for f in [zip_index_file, addresses_file, mappings_file]):
                    with open(zip_index_file, 'rb') as f:
                        print("loading zip index caches")
                        zip_index = pickle.load(f)
                    with open(addresses_file, 'rb') as f:
                        print("loading address caches")
                        address_entries, debug_address_entries = pickle.load(f)
                    with open(mappings_file, 'rb') as f:
                        print("loading po box caches")
                        po_box_entries, zip_code_index, po_box_zip_index = pickle.load(f)
                    cached_data = (zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index)
                    cache_valid = True
        except Exception as e:
            log_error("Error loading caches: {}", str(e), exc_info=True)
    return cache_valid, cached_data

def save_caches(cache_dir, start_year, end_year, checksums, zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, f'zip_checksums_{start_year}_{end_year}.json'), 'w') as f:
            json.dump(checksums, f)
        with open(os.path.join(cache_dir, f'cached_zip_index_{start_year}_{end_year}.pkl'), 'wb') as f:
            pickle.dump(zip_index, f)
        with open(os.path.join(cache_dir, f'cached_addresses_{start_year}_{end_year}.pkl'), 'wb') as f:
            pickle.dump((address_entries, debug_address_entries), f)
        with open(os.path.join(cache_dir, f'cached_mappings_{start_year}_{end_year}.pkl'), 'wb') as f:
            pickle.dump((po_box_entries, zip_code_index, po_box_zip_index), f)
    except Exception as e:
        log_error("Error saving caches: {}", str(e), exc_info=True)

def build_zip_index(zip_dir, start_year, end_year):
    index = {}
    zip_files = []
    zip_names = set()
    for year in range(start_year, end_year + 2):
        zip_files.extend(glob.glob(os.path.join(zip_dir, f"{year}*.zip")))
    
    if not zip_files:
        log_error("No ZIP files found in {} for years {}-{}", zip_dir, start_year, end_year + 1)
        return index

    for zip_path in tqdm(zip_files, desc="Indexing ZIP files"):
        zip_names.add(os.path.basename(zip_path))
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for internal_path in zf.namelist():
                    if internal_path.endswith('.xml'):
                        filename = os.path.basename(internal_path)
                        if filename in index:
                            log_error("Duplicate XML filename {} in ZIP {}, overwriting with {}", filename, index[filename][0], zip_path)
                        index[filename] = (zip_path, internal_path)
        except Exception as e:
            log_error("Error indexing ZIP {}: {}", zip_path, str(e), exc_info=True)
    
    log_error("Built ZIP index with {} XML files from {} ZIPs", len(index), len(zip_names))
    return index

def read_tsv_files(charity_source, start_year, end_year):
    rows = []
    if not os.path.exists(charity_source):
        log_error("TSV file {} does not exist. Ensure it is in the source directory.", charity_source)
        sys.exit(1)
    
    try:
        with open(charity_source, 'r', encoding='utf-8') as f:
            header = f.readline().strip().split('\t')
            header_map = {col: idx for idx, col in enumerate(header)}
            required_cols = ['filer_ein', 'filer_name', 'tax_year', 'form_type', 'xml_name']
            if not all(col in header_map for col in required_cols):
                log_error("Missing required columns in TSV {}: {}", charity_source, required_cols)
                sys.exit(1)
            for line in f:
                fields = line.strip().split('\t')
                if len(fields) < len(header_map):
                    continue
                row = {col: fields[idx] for col, idx in header_map.items()}
                try:
                    row_year = int(row['tax_year'])
                    if start_year <= row_year <= end_year:
                        rows.append(row)
                except ValueError:
                    log_error("Invalid tax_year {} in TSV {}, skipping row", row.get('tax_year', ''), charity_source)
    except Exception as e:
        log_error("Error reading TSV {}: {}", charity_source, str(e), exc_info=True)
        sys.exit(1)
    
    if not rows:
        log_error("No valid rows read from {}. Check file format and year range {}-{}", charity_source, start_year, end_year)
        sys.exit(1)
    
    log_error("Read {} rows from {}", len(rows), charity_source)
    return rows

def compute_name_heuristic(grantee_name, filer_name):
    if not grantee_name or not filer_name:
        return 0
    words1 = {w.lower() for w in grantee_name.split() if w.lower() not in STOP_WORDS}
    words2 = {w.lower() for w in filer_name.split() if w.lower() not in STOP_WORDS}
    return len(words1 & words2)

def canonicalize_address(address_components, output_dir):
    if not address_components:
        return "", None, None, ""
    # Extract structured fields from XML
    address_line = ""
    address_line2 = ""
    city = ""
    state = ""
    zip_code = None
    po_box = None
    for elem in address_components:
        if elem.tag.endswith('AddressLine1Txt') and elem.text:
            address_line = elem.text.strip()
            match = PO_BOX_REGEX.search(address_line)
            if match:
                po_box_str = match.group(0)
                number_match = PO_BOX_NUMBER_REGEX.search(po_box_str)
                if number_match:
                    po_box = number_match.group(0)
                else:
                    log_error("Failed to extract PO box number from: {} in address: {}", po_box_str, address_line)
        elif elem.tag.endswith('AddressLine2Txt') and elem.text:
            address_line2 = elem.text.strip()
        elif elem.tag.endswith('CityNm') and elem.text:
            city = elem.text.strip()
        elif elem.tag.endswith('StateAbbreviationCd') and elem.text:
            state = elem.text.strip()
        elif elem.tag.endswith('ZIPCd') and elem.text:
            zip_code = elem.text.strip()

    # Validate and format address string
    address_parts = [comp for comp in [address_line, address_line2, city, state, zip_code] if comp]
    if not address_parts:
        log_error("No valid address components: {}", address_components)
        return "", None, None, ""
    address_str = " ".join(address_parts)

    try:
        
        if zip_code:
            zip_code_digits = re.sub(r'\D', '', zip_code)
            if len(zip_code_digits) >= 5:
                zip_code = zip_code_digits[:5]
            else:
                log_error("Invalid zip_code: {} in address: {}", zip_code, address_str)
                zip_code = None
        
        normalized = expand_address(address_str)
        canonical = normalized[0] if normalized else address_str
        
        if po_box and 'po box' not in canonical.lower():
            canonical = f"PO Box {po_box} {canonical}"
        
        return canonical, po_box, zip_code, ""
    except Exception as e:
        log_error("Error canonicalizing address '{}': {}", address_str, str(e), exc_info=True)
        return address_str, None, None, ""

def parse_addresses(xml_content, xml_filename, row, zip_index, output_dir):
    global total_addresses, total_address_errors, total_queue_puts
    if not hasattr(thread_local, 'result'):
        thread_local.result = {
            'address_entries': [],
            'debug_address_entries': [],
            'po_box_entries': [],
            'invalid_ein_entries': [],
            'ein_mismatch_set': set(),
            'total_addresses': 0,
            'total_queue_puts': 0,
            'total_address_errors': 0,
            'zip_code_index': {},
            'po_box_zip_index': {},
            'filer_eins': {}
        }
    result = thread_local.result
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        tsv_ein = row['filer_ein'].strip()
        filer_name = row['filer_name'].strip()
        tax_year = row['tax_year'].strip()
        
        xml_ein = None
        for xpath in FILER_EIN_XPATHS:
            elem = xpath(root)
            if elem and elem[0].text:
                xml_ein = elem[0].text.strip()
                break
        if not xml_ein or not EIN_REGEX.match(xml_ein):
            result['invalid_ein_entries'].append({
                'tsv_ein': tsv_ein,
                'xml_ein': xml_ein or '',
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'reason': 'No or invalid EIN in XML'
            })
            result['debug_address_entries'].append({
                'filer_ein': tsv_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'raw_components': '',
                'canonical_address': '',
                'raw_zip': '',
                'zip_code': '',
                'status': 'skipped',
                'reason': f"No or invalid EIN: {xml_ein or 'None'}"
            })
            return False, None
        
        if tsv_ein != xml_ein and tsv_ein not in result['ein_mismatch_set']:
            result['invalid_ein_entries'].append({
                'tsv_ein': tsv_ein,
                'xml_ein': xml_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'reason': 'TSV EIN differs from XML EIN'
            })
            result['ein_mismatch_set'].add(tsv_ein)
        
        filer_ein = xml_ein
        address_components = []
        for xpath in ADDRESS_XPATHS:
            elements = xpath(root)
            for elem in elements:
                if elem.text:
                    address_components.append(elem)
        canonical_address, po_box, zip_code, _ = canonicalize_address(address_components, output_dir)
        if canonical_address:
            for abbr, full in USPS_FIXES.items():
                canonical_address = canonical_address.replace(f'{abbr} ', f'{full} ')
        raw_components_str = ";".join(elem.text.strip() for elem in address_components if elem.text) 
        us_address = root.find(".//irs:Filer/irs:USAddress", namespaces=NAMESPACES)
        address_snippet = etree.tostring(us_address if us_address is not None else root, encoding='unicode', method='xml', pretty_print=True)[:500]
        
        if canonical_address:
            result['address_entries'].append({
                'filer_ein': filer_ein,
                'filer_name': filer_name,
                'canonical_address': canonical_address,
                'tax_year': tax_year,
                'po_box': po_box,
                'zip_code': zip_code
            })
            if po_box and zip_code and ZIP_REGEX.match(zip_code):
                result['po_box_entries'].append({
                    'po_box': po_box,
                    'zip_code': zip_code,
                    'ein': filer_ein,
                    'org_name': filer_name
                })
                po_box_key = (po_box, zip_code)
                if po_box_key not in result['po_box_zip_index']:
                    result['po_box_zip_index'][po_box_key] = set()
                result['po_box_zip_index'][po_box_key].add((filer_ein, filer_name))
            if zip_code and ZIP_REGEX.match(zip_code):
                if zip_code not in result['zip_code_index']:
                    result['zip_code_index'][zip_code] = set()
                result['zip_code_index'][zip_code].add((filer_ein, filer_name))
            result['total_addresses'] += 1
            result['total_queue_puts'] += 1
        else:
            result['total_address_errors'] += 1
            result['debug_address_entries'].append({
                'filer_ein': filer_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'raw_components': raw_components_str,
                'canonical_address': '',
                'raw_zip': '',
                'zip_code': '',
                'status': 'error',
                'reason': f"Invalid address; components={address_components}; snippet={address_snippet}"
            })
            if sample_xml:
                os.makedirs(sample_xml, exist_ok=True)
                with open(os.path.join(sample_xml, xml_filename), 'wb') as f:
                    f.write(xml_content)
            if not skip_address_errors:
                return False, None
        
        with zip_index_lock:
            filer_eins[xml_filename] = (filer_ein, row, canonical_address)
        return True, filer_ein
    except Exception as e:
        log_error("Error parsing XML {} for EIN={}: {}", xml_filename, row['filer_ein'], str(e), exc_info=True)
        result['total_address_errors'] += 1
        result['debug_address_entries'].append({
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
    global total_grants, total_990pf_rows, total_queue_puts, duplicate_grant_count, unique_grant_eins, grant_ein_counts
    if not hasattr(thread_local, 'result'):
        thread_local.result = {
            'grant_entries': [],
            'debug_grant_entries': [],
            'po_box_entries': [],
            'total_grants': 0,
            'total_queue_puts': 0,
            'total_990pf_rows': 0
        }
    result = thread_local.result
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        
        filer_name = row['filer_name'].strip()
        tax_year = row['tax_year'].strip()
        with zip_index_lock:
            filer_canonical_address = filer_eins.get(xml_filename, (None, None, ''))[2]
        status_counts = defaultdict(int)
        grant_map = []
        grant_map_keys = set()
        
        if row['form_type'] == '990PF':
            result['total_990pf_rows'] += 1
            for xpath_grant in GRANT_XPATHS_990PF:
                grant_elements = xpath_grant(root)
                if grant_elements:
                    nsmap = root.nsmap
                    element_count = 0
                    irs_ns = nsmap.get('irs', nsmap.get(None))
                    log_error("Parsing grants for XML {}, root tag: {}, namespace: {}", xml_filename, root.tag, irs_ns)
                    if irs_ns != 'http://www.irs.gov/efile':
                        log_error("Namespace mismatch: expected http://www.irs.gov/efile, got {}", irs_ns)
                    log_error("Found {} grant elements for XML {}, sample: {}", len(grant_elements), xml_filename, ET.tostring(grant_elements[0], encoding='unicode', method='xml')[:500])
                
                for element in grant_elements:
                    ein_elem = element.xpath(".//irs:EIN | .//irs:RecipientEIN", namespaces=NAMESPACES)
                    name_elem = None
                    for i, xpath_name in enumerate(GRANTEE_NAME_XPATHS):
                        names = xpath_name(element)
                        if names and names[0].text is not None:
                            name_elem = names[0]
                            break
                    amount_elem = None
                    for xpath_amount in AMOUNT_XPATHS:
                        amounts = xpath_amount(element)
                        if amounts and amounts[0].text is not None and amounts[0].text.strip():
                            amount_elem = amounts[0]
                            break
                    element_count += 1
                    skip_log_count = 0
                    if element_count <= 10 and ((name_elem is None and xpath_name(element)) or (amount_elem is None and xpath_amount(element))):
                        raw_amount_text = amounts[0].text if amounts and amounts[0].text else "None"
                        log_error("Grant element #{}, name_elem: {}, amount_elem: {}, xpath_name_index: {}, raw_names: {}, raw_amounts: {}, raw_amount_text: {}", element_count, name_elem is not None, amount_elem is not None, i if name_elem is not None else -1, [n.text.strip() if n.text else '' for n in xpath_name(element)], [a.text.strip() if a.text else '' for a in xpath_amount(element)], raw_amount_text)
                    if element_count == len(grant_elements):
                        log_error("Processed {} grant elements for XML {}, skipped: {}", element_count, xml_filename, status_counts.get('skipped', 0))
                    address_elems = None
                    for xpath_address in GRANTEE_ADDRESS_XPATHS:
                        addrs = xpath_address(element)
                        if addrs:
                            address_elems = addrs
                            break
                    is_foreign_address = any('RecipientForeignAddress' in elem.tag for elem in address_elems) if address_elems else False
                    if is_foreign_address:
                        grant_ein = "002"
                    else:
                        grant_ein = ein_elem[0].text.strip() if ein_elem and len(ein_elem) > 0 and ein_elem[0].text else "Unknown"
                    grantee_name = name_elem.text.strip() if name_elem is not None and name_elem.text and name_elem.text.strip().lower() != 'see attached schedule' else "Unknown"
                    grant_address_components = [elem for elem in address_elems if elem.text] if address_elems else []
                    grantee_canonical_address, grant_po_box, grant_zip_code, _ = canonicalize_address(grant_address_components, output_dir)
                    if grantee_canonical_address:
                        for abbr, full in USPS_FIXES.items():
                            grantee_canonical_address = grantee_canonical_address.replace(f'{abbr} ', f'{full} ')
                    if grantee_canonical_address or amount_elem is not None or name_elem is not None or len(ein_elem) > 0 or grant_ein != "Unknown":
                        try:
                            grant_amt = int(float(amount_elem.text.strip())) if amount_elem is not None and amount_elem.text else 0
                            if grantee_canonical_address or grant_amt > 0 or grantee_name != "Unknown" or ein_elem:
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
                                for cand_ein, cand_name in candidates:
                                    score = compute_name_heuristic(grantee_name, cand_name)
                                    if score > best_score:
                                        best_score = score
                                        best_filer = (cand_ein, cand_name)
                                        best_heuristic = heuristic_type
                                if best_filer and best_score >= 1:
                                    grant_ein = best_filer[0]
                                    status = f"success_{best_heuristic}_score_{best_score}"
                                elif grant_ein == "Unknown" and grantee_canonical_address:
                                    grant_ein = f"Address:{grantee_canonical_address}"
                                    status = 'success_address'
                                elif grantee_name != "Unknown" or grant_amt > 0:
                                    grant_ein = f"Name:{grantee_name}"
                                    status = 'success_name'
                                else:
                                    grant_ein = "Unknown"
                                    status = 'skipped'
                                status_counts[status] += 1
                            if grant_ein != "Unknown" or grantee_name != "Unknown" or grant_amt > 0:
                                grant_map.append({
                                    'filer_ein': filer_ein,
                                    'filer_name': filer_name,
                                    'grant_ein': grant_ein,
                                    'grant_amt': grant_amt,
                                    'tax_year': tax_year,
                                    'filer_canonical_address': filer_canonical_address,
                                    'grantee_canonical_address': grantee_canonical_address
                                })
                                seen_key = (filer_ein, grant_ein)
                                if seen_key in grant_map_keys:
                                    duplicate_grant_count += 1
                                    if skip_log_count < 10:
                                        log_error("Duplicate grant for filer_ein={}, grant_ein={} in XML {}, amount: {}, grantee_name={}, grant_address={}", filer_ein, grant_ein, xml_filename, grant_amt, grantee_name, grantee_canonical_address)
                                        skip_log_count += 1
                                else:
                                    grant_map_keys.add(seen_key)
                                    unique_grant_eins.add(grant_ein)
                                    grant_ein_counts[grant_ein] += 1
                                    result['debug_grant_entries'].append({
                                        'filer_ein': filer_ein,
                                        'filer_name': filer_name,
                                        'xml_filename': xml_filename,
                                        'grantee_name': grantee_name,
                                        'grant_ein': grant_ein,
                                        'grant_address': grantee_canonical_address,
                                        'grant_amt': grant_amt,
                                        'tax_year': tax_year,
                                        'status': status,
                                        'heuristic_score': best_score,
                                        'reason': "success"
                                    })
                                    result['total_grants'] += 1
                                    result['total_queue_puts'] += 1
                            else:
                                if skip_log_count < 10:
                                    raw_amount_text = amount_elem.text if amount_elem else "None"
                                    log_error("Skipped grant in XML {}: grantee_name={}, grant_amt={}, grant_address={}, raw_amount_text={}, reason=No valid EIN, address, or name", xml_filename, grantee_name, grant_amt, grantee_canonical_address, raw_amount_text)
                                    skip_log_count += 1
                                result['debug_grant_entries'].append({
                                    'filer_ein': filer_ein,
                                    'filer_name': filer_name,
                                    'xml_filename': xml_filename,
                                    'grantee_name': grantee_name,
                                    'grant_ein': grant_ein,
                                    'grant_address': grantee_canonical_address,
                                    'grant_amt': grant_amt,
                                    'tax_year': tax_year,
                                    'status': 'skipped',
                                    'heuristic_score': best_score,
                                    'reason': "No valid EIN, address, or name"
                                })
                        except (ValueError, TypeError) as e:
                            result['debug_grant_entries'].append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'xml_filename': xml_filename,
                                'grantee_name': grantee_name,
                                'grant_ein': grant_ein,
                                'grant_address': grantee_canonical_address,
                                'grant_amt': '',
                                'tax_year': tax_year,
                                'status': 'error',
                                'reason': f"Invalid grant amount: {str(e)}"
                            })
                    else:
                        result['debug_grant_entries'].append({
                            'filer_ein': filer_ein,
                            'filer_name': filer_name,
                            'xml_filename': xml_filename,
                            'grantee_name': grantee_name,
                            'grant_ein': grant_ein,
                            'grant_address': grantee_canonical_address,
                            'grant_amt': '',
                            'tax_year': tax_year,
                            'status': 'skipped',
                            'reason': f"No grant amount for grantee {grantee_name}"
                        })
        
        result['grant_entries'] = grant_map
        return True
    except Exception as e:
        log_error("Error parsing grants in XML {} for EIN={}: {}", xml_filename, filer_ein, str(e), exc_info=True)
        return False
    global total_grants, total_990pf_rows, total_queue_puts
    if not hasattr(thread_local, 'result'):
        thread_local.result = {
            'grant_entries': [],
            'debug_grant_entries': [],
            'po_box_entries': [],
            'total_grants': 0,
            'total_queue_puts': 0,
            'total_990pf_rows': 0
        }
    result = thread_local.result
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        
        filer_name = row['filer_name'].strip()
        tax_year = row['tax_year'].strip()
        with zip_index_lock:
            filer_canonical_address = filer_eins.get(xml_filename, (None, None, ''))[2]
        status_counts = defaultdict(int)
        grant_map = {}
        
        if row['form_type'] == '990PF':
            result['total_990pf_rows'] += 1
            for xpath_grant in GRANT_XPATHS_990PF:
                grant_elements = xpath_grant(root)
                if grant_elements:
                    nsmap = root.nsmap
                    element_count = 0
                    irs_ns = nsmap.get('irs', nsmap.get(None))
                    log_error("Parsing grants for XML {}, root tag: {}, namespace: {}", xml_filename, root.tag, irs_ns)
                    if irs_ns != 'http://www.irs.gov/efile':
                        log_error("Namespace mismatch: expected http://www.irs.gov/efile, got {}", irs_ns)
                    log_error("Found {} grant elements for XML {}, sample: {}", len(grant_elements), xml_filename, ET.tostring(grant_elements[0], encoding='unicode', method='xml')[:500])
                
                for element in grant_elements:
                    ein_elem = element.xpath(".//irs:EIN | .//irs:RecipientEIN", namespaces=NAMESPACES)
                    name_elem = None
                    for i, xpath_name in enumerate(GRANTEE_NAME_XPATHS):
                        names = xpath_name(element)
                        if names and names[0].text is not None:
                            name_elem = names[0]
                            break
                    amount_elem = None
                    for xpath_amount in AMOUNT_XPATHS:
                        amounts = xpath_amount(element)
                        if amounts is not None and amounts[0].text is not None and amounts[0].text.strip():
                            amount_elem = amounts[0]
                            break
                    element_count += 1
                    skip_log_count = 0
                    if element_count <= 10 and ((name_elem is None and xpath_name(element)) or (amount_elem is None and xpath_amount(element))):  # Log only issues for first 10 elements
                        raw_amount_text = amounts[0].text if amounts and amounts[0].text else "None"
                        log_error("Grant element #{}, name_elem: {}, amount_elem: {}, xpath_name_index: {}, raw_names: {}, raw_amounts: {}, raw_amount_text: {}", element_count, name_elem is not None, amount_elem is not None, i if name_elem is not None else -1, [n.text.strip() if n.text else '' for n in xpath_name(element)], [a.text.strip() if a.text else '' for a in xpath_amount(element)], raw_amount_text)
                    if element_count == len(grant_elements):
                        log_error("Processed {} grant elements for XML {}, skipped: {}", element_count, xml_filename, status_counts.get('skipped', 0))
                    address_elems = None
                    for xpath_address in GRANTEE_ADDRESS_XPATHS:
                        addrs = xpath_address(element)
                        if addrs:
                            address_elems = addrs
                            break
                    is_foreign_address = any('RecipientForeignAddress' in elem.tag for elem in address_elems) if address_elems else False
                    if is_foreign_address:
                        grant_ein = "002"
                    else:
                        grant_ein = ein_elem[0].text.strip() if ein_elem and len(ein_elem) > 0 and ein_elem[0].text else "Unknown"
                    grantee_name = name_elem.text.strip() if name_elem is not None and name_elem.text and name_elem.text.strip().lower() != 'see attached schedule' else "Unknown"
                    grant_address_components = [elem.text.strip() for elem in address_elems if elem.text] if address_elems else []
                    grantee_canonical_address, grant_po_box, grant_zip_code, _ = canonicalize_address(grant_address_components, output_dir)
                    if grantee_canonical_address:
                        for abbr, full in USPS_FIXES.items():
                            grantee_canonical_address = grantee_canonical_address.replace(f'{abbr} ', f'{full} ')

                    if grantee_canonical_address or amount_elem is not None or name_elem is not None or len(ein_elem) > 0 or grant_ein != "Unknown":
                        try:
                            grant_amt = int(float(amount_elem.text.strip())) if amount_elem is not None and amount_elem.text else 0
                            if grantee_canonical_address or grant_amt > 0 or grantee_name != "Unknown" or ein_elem:
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
                                
                                for cand_ein, cand_name in candidates:
                                    score = compute_name_heuristic(grantee_name, cand_name)
                                    if score > best_score:
                                        best_score = score
                                        best_filer = (cand_ein, cand_name)
                                        best_heuristic = heuristic_type
                                
                                if best_filer and best_score >= 1:
                                    grant_ein = best_filer[0]
                                    status = f"success_{best_heuristic}_score_{best_score}"
                                elif grant_ein == "Unknown" and grantee_canonical_address:
                                    grant_ein = f"Address:{grantee_canonical_address}"
                                    status = 'success_address'
                                elif grantee_name != "Unknown" or grant_amt > 0:
                                    grant_ein = f"Name:{grantee_name}"
                                    status = 'success_name'
                                else:
                                    grant_ein = "Unknown"
                                    status = 'skipped'
                                
                                status_counts[status] += 1
                            if grant_ein != "Unknown" or grantee_name != "Unknown" or grant_amt > 0:
                                grant_map.append({
                                         'filer_ein': filer_ein,
                                         'filer_name': filer_name,
                                         'grant_ein': grant_ein,
                                         'grant_amt': grant_amt,
                                         'tax_year': tax_year,
                                         'filer_canonical_address': filer_canonical_address,
                                         'grantee_canonical_address': grantee_canonical_address
                                     }
                                )
                                # Check for duplicates by (filer_ein, grant_ein)
                                seen_key = (filer_ein, grant_ein)
                                if seen_key in grant_map_keys:
                                    duplicate_grant_count += 1
                                    if skip_log_count < 10:
                                        log_error("Duplicate grant for filer_ein={}, grant_ein={} in XML {}, amount: {}, grantee_name={}, grant_address={}", filer_ein, grant_ein, xml_filename, grant_amt, grantee_name, grantee_canonical_address)
                                        skip_log_count += 1
                                else:
                                    grant_map_keys.add(seen_key)
                                    result['debug_grant_entries'].append({
                                        'filer_ein': filer_ein,
                                        'filer_name': filer_name,
                                        'xml_filename': xml_filename,
                                        'grantee_name': grantee_name,
                                        'grant_ein': grant_ein,
                                        'grant_address': grantee_canonical_address,
                                        'grant_amt': grant_amt,
                                        'tax_year': tax_year,
                                        'status': status,
                                        'heuristic_score': best_score,
                                        'reason': "No valid EIN, address, or name"
                                    })
                        except (ValueError, TypeError) as e:
                            result['debug_grant_entries'].append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'xml_filename': xml_filename,
                                'grantee_name': grantee_name,
                                'grant_ein': grant_ein,
                                'grant_address': grantee_canonical_address,
                                'grant_amt': '',
                                'tax_year': tax_year,
                                'status': 'error',
                                'reason': f"Invalid grant amount: {str(e)}"
                            })
                    else:
                        result['debug_grant_entries'].append({
                            'filer_ein': filer_ein,
                            'filer_name': filer_name,
                            'xml_filename': xml_filename,
                            'grantee_name': grantee_name,
                            'grant_ein': grant_ein,
                            'grant_address': grantee_canonical_address,
                            'grant_amt': '',
                            'tax_year': tax_year,
                            'status': 'skipped',
                            'reason': f"No grant amount for grantee {grantee_name}"
                        })
        
        result['grant_entries'] = grant_map  # Use full list instead of values()
        return True
    except Exception as e:
        log_error("Error parsing grants in XML {} for EIN={}: {}", xml_filename, filer_ein, str(e), exc_info=True)
        return False

def process_rows(rows, worker_threads, zip_index, start_year, end_year, output_dir):
    global total_addresses, total_address_errors, total_queue_puts, total_grants, total_990pf_rows
    global results_queue, grants_queue, total_tasks_done
    zip_cache = {}
    total_skipped = 0
    results_queue = queue.Queue(maxsize=20000)
    grants_queue = queue.Queue(maxsize=50000)
    
    def write_grants():
         try:
             with open(os.path.join(output_dir, 'inferred_grants.tsv'), 'w', encoding='utf-8', newline='') as f:
                 writer = csv.writer(f, delimiter='\t')
                 global total_tasks_done
                 writer.writerow(GRANT_COLUMNS)
                 written_grants = 0
                 with tqdm(total=total_grants, desc="Writing inferred_grants.tsv") as pbar:
                     while True:
                         grants = grants_queue.get()
                         if grants is None:
                             log_error("Grant writer received None, exiting")
                             break
                         for grant in grants:
                             writer.writerow([grant.get(col, '') for col in GRANT_COLUMNS])
                             written_grants += 1
                             pbar.update(1)
                         grants_queue.task_done()
                     log_error("Wrote {} grants to inferred_grants.tsv", written_grants)
         except Exception as e:
            log_error("Error in grant writer: {}", str(e), exc_info=True)
            raise
            
    grant_writer = threading.Thread(target=write_grants)
    grant_writer.start()
    
    log_error("Grants queue size before processing: {}", grants_queue.qsize())
    
    def process_address_row(row):
        nonlocal total_skipped
        if not hasattr(file_counter_local, 'value'):
            file_counter_local.value = 0
        if not hasattr(file_counter_local, 'skipped'):
            file_counter_local.skipped = 0
        thread_local.result = {
            'address_entries': [],
            'debug_address_entries': [],
            'po_box_entries': [],
            'invalid_ein_entries': [],
            'ein_mismatch_set': set(),
            'total_addresses': 0,
            'total_queue_puts': 0,
            'total_address_errors': 0,
            'zip_code_index': {},
            'po_box_zip_index': {},
            'filer_eins': {}
        }
        xml_path = row.get('xml_name', '')
        if not xml_path:
            log_error("No xml_name for EIN={}, skipping", row['filer_ein'])
            thread_local.result['debug_address_entries'].append({
                'filer_ein': row['filer_ein'], 'filer_name': row['filer_name'], 'xml_filename': '',
                'raw_components': '', 'canonical_address': '', 'raw_zip': '', 'zip_code': '', 'status': 'skipped',
                'reason': 'Missing xml_name'})
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result
        try:
            parts = xml_path.split('/')
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                log_error("No file {} in ZIP index for EIN={}, skipping (total missing: {})", xml_filename, row['filer_ein'], total_skipped + 1)
                thread_local.result['debug_address_entries'].append({
                    'filer_ein': row['filer_ein'], 'filer_name': row['filer_name'], 'xml_filename': xml_filename,
                    'raw_components': '', 'canonical_address': '', 'raw_zip': '', 'zip_code': '', 'status': 'skipped',
                    'reason': 'XML not in ZIP index'})
                file_counter_local.skipped += 1
                total_skipped += 1
                return thread_local.result
            zip_path, internal_path = zip_index[xml_filename]
            zip_year_match = re.match(r'.*(\d{4})', os.path.basename(zip_path))
            if not zip_year_match or not (start_year <= int(zip_year_match.group(1)) <= end_year + 1):
                log_error("Invalid/out-of-range ZIP year for {} and EIN={}, skipping", xml_path, row['filer_ein'])
                thread_local.result['debug_address_entries'].append({
                    'filer_ein': row['filer_ein'], 'filer_name': row['filer_name'], 'xml_filename': xml_filename,
                    'raw_components': '', 'canonical_address': '', 'raw_zip': '', 'zip_code': '', 'status': 'skipped',
                    'reason': 'Invalid/out-of-range ZIP year'})
                file_counter_local.skipped += 1
                total_skipped += 1
                return thread_local.result
            if zip_path not in zip_cache:
                zip_cache[zip_path] = zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                success, filer_ein = parse_addresses(xml_content, xml_filename, row, zip_index, output_dir)
                if not success:
                    file_counter_local.skipped += 1
                    total_skipped += 1
            return thread_local.result
        except Exception as e:
            log_error("Error processing address row for XML {} and EIN={}: {}", xml_path, row['filer_ein'], str(e))
            thread_local.result['debug_address_entries'].append({
                'filer_ein': row['filer_ein'], 'filer_name': row['filer_name'], 'xml_filename': xml_path,
                'raw_components': '', 'canonical_address': '', 'raw_zip': '', 'zip_code': '', 'status': 'error',
                'reason': str(e)})
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result

    def process_grant_row(xml_filename, filer_info):
        nonlocal total_skipped
        if not hasattr(file_counter_local, 'value'):
            file_counter_local.value = 0
        if not hasattr(file_counter_local, 'skipped'):
            file_counter_local.skipped = 0
        thread_local.result = {
            'grant_entries': [],
            'debug_grant_entries': [],
            'po_box_entries': [],
            'total_grants': 0,
            'total_queue_puts': 0,
            'total_990pf_rows': 0
        }
        if not filer_info:
            log_error("No filer_info for grant processing, skipping")
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result
        filer_ein, row, _ = filer_info
        xml_path = row.get('xml_name', '')
        try:
            parts = xml_path.split('/')
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                log_error("XML {} not in zip_index for EIN={}, skipping (total missing: {})", xml_filename, filer_ein, total_skipped + 1)
                file_counter_local.skipped += 1
                total_skipped += 1
                return thread_local.result
            zip_path, internal_path = zip_index[xml_filename]
            if zip_path not in zip_cache:
                zip_cache[zip_path] = zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                parse_grants(xml_content, xml_filename, row, filer_ein, output_dir)
            return thread_local.result
        except Exception as e:
            log_error("Error processing grant row for XML {} and EIN={}: {}", xml_path, filer_ein, str(e))
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result

    try:
        log_error("Found {} 990PF rows in {} total rows", sum(1 for row in rows if row['form_type'] == '990PF'), len(rows))
        batch_size = args.merge_batch_size
        if args.no_threads:
            for row in tqdm(rows, desc="Processing addresses"):
                result = process_address_row(row)
                with zip_index_lock:
                    address_entries.extend(result['address_entries'])
                    debug_address_entries.extend(result['debug_address_entries'])
                    po_box_entries.extend(result['po_box_entries'])
                    invalid_ein_entries.extend(result['invalid_ein_entries'])
                    ein_mismatch_set.update(result['ein_mismatch_set'])
                    total_addresses += result['total_addresses']
                    total_queue_puts += result['total_queue_puts']
                    total_address_errors += result['total_address_errors']
                    for zip_code, entries in result['zip_code_index'].items():
                        if zip_code not in zip_code_index:
                            zip_code_index[zip_code] = set()
                        zip_code_index[zip_code].update(entries)
                    for po_box_zip, entries in result['po_box_zip_index'].items():
                        if po_box_zip not in po_box_zip_index:
                            po_box_zip_index[po_box_zip] = set()
                        po_box_zip_index[po_box_zip].update(entries)
                results_queue.put([result])
            for xml_filename, filer_info in tqdm(filer_eins.items(), desc="Processing grants"):
                result = process_grant_row(xml_filename, filer_info)
                with zip_index_lock:
                    debug_grant_entries.extend(result['debug_grant_entries'])
                    po_box_entries.extend(result['po_box_entries'])
                    total_grants += result['total_grants']
                    total_queue_puts += result['total_queue_puts']
                    total_990pf_rows += result['total_990pf_rows']
                grants_queue.put(result['grant_entries'])
                results_queue.put([result])
        else:
            with ThreadPoolExecutor(max_workers=worker_threads) as executor:
                futures = []
                batch = []
                for row in rows:
                    batch.append(row)
                    if len(batch) >= batch_size:
                        futures.append(executor.submit(lambda b: [process_address_row(r) for r in b], batch))
                        batch = []
                if batch:
                    futures.append(executor.submit(lambda b: [process_address_row(r) for r in b], batch))
                with tqdm(total=len(rows), desc="Processing addresses") as pbar:
                    for future in as_completed(futures):
                        results = future.result()
                        with zip_index_lock:
                            for result in results:
                                address_entries.extend(result['address_entries'])
                                debug_address_entries.extend(result['debug_address_entries'])
                                po_box_entries.extend(result['po_box_entries'])
                                invalid_ein_entries.extend(result['invalid_ein_entries'])
                                ein_mismatch_set.update(result['ein_mismatch_set'])
                                total_addresses += result['total_addresses']
                                total_queue_puts += result['total_queue_puts']
                                total_address_errors += result['total_address_errors']
                                for zip_code, entries in result['zip_code_index'].items():
                                    if zip_code not in zip_code_index:
                                        zip_code_index[zip_code] = set()
                                    zip_code_index[zip_code].update(entries)
                                for po_box_zip, entries in result['po_box_zip_index'].items():
                                    if po_box_zip not in po_box_zip_index:
                                        po_box_zip_index[po_box_zip] = set()
                                    po_box_zip_index[po_box_zip].update(entries)
                        results_queue.put(results)
                        pbar.update(min(batch_size, len(rows) - pbar.n))
                
                futures = []
                batch = []
                for xml_filename, filer_info in [(k, v) for k, v in filer_eins.items() if v[1]['form_type'] == '990PF']:
                    batch.append((xml_filename, filer_info))
                    if len(batch) >= batch_size:
                        futures.append(executor.submit(lambda b: [process_grant_row(f[0], f[1]) for f in b], batch))
                        batch = []
                if batch:
                    futures.append(executor.submit(lambda b: [process_grant_row(f[0], f[1]) for f in b], batch))
                with tqdm(total=sum(1 for k, v in filer_eins.items() if v[1]['form_type'] == '990PF'), desc="Processing grants") as pbar:
                    for future in as_completed(futures):
                        results = future.result()
                        with zip_index_lock:
                            for result in results:
                                debug_grant_entries.extend(result['debug_grant_entries'])
                                po_box_entries.extend(result['po_box_entries'])
                                total_grants += result['total_grants']
                                total_queue_puts += result['total_queue_puts']
                                total_990pf_rows += result['total_990pf_rows']
                                grants_queue.put(result['grant_entries'])
                        results_queue.put(results)
                        log_error("Grants queue size after batch: {}", grants_queue.qsize())
                        pbar.update(min(batch_size, len(filer_eins) - pbar.n))
    except Exception as e:
        log_error("Error in process_rows: {}", str(e), exc_info=True)
        raise
    finally:
        log_error("Grant pass complete: {} grants, {} 990PF rows, total skipped: {}", total_grants, total_990pf_rows, total_skipped)
        results_queue.put(None)
        grants_queue.put(None)
        for zip_file in zip_cache.values():
            zip_file.close()
        # Flush remaining grants
        while not grants_queue.empty():
            grants_queue.get()
            grants_queue.task_done()
        grant_writer.join(timeout=300)
        log_error("Grant writer finished, final grants queue size: {}", grants_queue.qsize())

def write_outputs(output_dir, addresses_cached):
    global address_entries, debug_address_entries, invalid_ein_entries, po_box_entries, debug_grant_entries
    address_file = os.path.join(output_dir, "charity_addresses.tsv")
    debug_address_file = os.path.join(output_dir, "address_debug.tsv")
    debug_grant_file = os.path.join(output_dir, "grant_debug.tsv")
    invalid_ein_file = os.path.join(output_dir, "invalid_eins.tsv")
    po_box_file = os.path.join(output_dir, "po_box_matches.tsv")

    status_counts = defaultdict(int)
    for entry in debug_grant_entries:
        status_counts[entry['status']] += 1
    log_error("Grant status summary: {}", dict(status_counts))

    reason_counts = defaultdict(int)
    for entry in debug_address_entries:
        if entry['status'] == 'error':
            reason = entry['reason'].split(';')[0]
            reason_counts[reason] += 1
    log_error("Address error summary: {}", dict(reason_counts))

    if not addresses_cached:
        address_entries = deduplicate_sorted_dicts(address_entries, ['filer_ein', 'filer_name', 'canonical_address'])
        log_error("Opening TSV file: {}", address_file)
        with open(address_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerow(ADDRESS_COLUMNS)
            for entry in address_entries:
                writer.writerow([entry.get(col, '') for col in ADDRESS_COLUMNS])
        log_error("Wrote {} address rows to {}", len(address_entries), address_file)

    log_error("Opening TSV file: {}", debug_address_file)
    with open(debug_address_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(DEBUG_ADDRESS_COLUMNS)
        for entry in debug_address_entries:
            writer.writerow([entry.get(col, '') for col in DEBUG_ADDRESS_COLUMNS])
    log_error("Wrote {} address debug rows to {}", len(debug_address_entries), debug_address_file)

    log_error("Opening TSV file: {}", debug_grant_file)
    with open(debug_grant_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(DEBUG_GRANT_COLUMNS)
        with tqdm(total=len(debug_grant_entries), desc="Writing grant_debug.tsv") as pbar:
            for entry in debug_grant_entries:
                writer.writerow([entry.get(col, '') for col in DEBUG_GRANT_COLUMNS])
                pbar.update(1)
    log_error("Wrote {} grant debug rows to {}", len(debug_grant_entries), debug_grant_file)

    invalid_ein_entries = deduplicate_sorted_dicts(invalid_ein_entries, ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename'])
    sorted_invalid_eins = sorted(invalid_ein_entries, key=lambda x: (x['xml_ein'], x['tsv_ein'], x['filer_name'].lower()))
    log_error("Opening TSV file: {}", invalid_ein_file)
    with open(invalid_ein_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(INVALID_EIN_COLUMNS)
        for entry in sorted_invalid_eins:
            writer.writerow([entry.get(col, '') for col in INVALID_EIN_COLUMNS])
    log_error("Wrote {} invalid EIN rows to {}", len(sorted_invalid_eins), invalid_ein_file)

    po_box_entries = deduplicate_sorted_dicts(po_box_entries, ['po_box', 'zip_code', 'ein', 'org_name'])
    sorted_po_boxes = sorted(po_box_entries, key=lambda x: (x['zip_code'], x['po_box'], x['org_name'].lower()))
    log_error("Opening TSV file: {}", po_box_file)
    with open(po_box_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(PO_BOX_COLUMNS)
        for entry in sorted_po_boxes:
            writer.writerow([entry.get(col, '') for col in PO_BOX_COLUMNS])
    log_error("Wrote {} PO Box rows to {}", len(sorted_po_boxes), po_box_file)

    log_error("Outputs written: {} addresses, {} debug addresses, {} debug grants, {} invalid EINs, {} PO boxes",
              len(address_entries), len(debug_address_entries), len(debug_grant_entries),
              len(invalid_ein_entries), len(po_box_entries))
    log_error("Duplicate grants aggregated: {}", duplicate_grant_count)
    log_error("Unique grant EINs: {}", len(unique_grant_eins))
    # Log top 5 most frequent grant_ein values
    top_eins = sorted(grant_ein_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    log_error("Top 5 frequent grant EINs: {}", [(ein, count) for ein, count in top_eins])	

def deduplicate_sorted_dicts(entries, key_order):
    seen = set()
    deduped = []
    for entry in entries:
        key_tuple = tuple(str(entry.get(key, '')) for key in key_order)
        if key_tuple not in seen:
            seen.add(key_tuple)
            deduped.append(entry)
    return deduped

def signal_handler(sig, frame):
    log_error("Received interrupt signal, writing partial outputs")
    sys.exit(0)
    traceback.print_stack(frame)
    if grants_queue:
        grants_queue.put(None)
    write_outputs(args.output_dir, False)
        
if __name__ == "__main__":
    main()