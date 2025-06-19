import os
import glob
import logging
import zipfile
import csv
import re
import queue
from logging.handlers import QueueHandler, QueueListener
from lxml import etree
import subprocess
import signal
import sys
import pickle
import json
import xml.etree.ElementTree as ET
from tqdm import tqdm
from collections import defaultdict

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
FILER_NAME_XPATHS = [
    etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt | .//Filer/BusinessName/BusinessNameLine1Txt", namespaces=NAMESPACES),
    etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine2Txt | .//Filer/BusinessName/BusinessNameLine2Txt", namespaces=NAMESPACES),
]
AMOUNT_XPATHS = [
    etree.XPath("./irs:Amt | .//*[local-name()='Amt']", namespaces=NAMESPACES),
    etree.XPath("./irs:GrantOrContributionAmt | ./irs:ContributionAmt", namespaces=NAMESPACES),
    etree.XPath("./irs:TotalGrantOrContriPdDurYrAmt", namespaces=NAMESPACES),
    etree.XPath(".//Amt", namespaces={}),
]
GRANTEE_ADDRESS_XPATHS = [
    etree.XPath(".//irs:RecipientUSAddress/* | .//irs:RecipientForeignAddress/* | .//irs:RecipientForeignAddress/irs:CountryCd", namespaces=NAMESPACES),
    etree.XPath(".//RecipientUSAddress/* | .//RecipientForeignAddress/* | .//RecipientForeignAddress/CountryCd", namespaces={}),
    etree.XPath(".//*[local-name()='RecipientForeignAddress']/* | .//*[local-name()='CountryCd']", namespaces={}),
]
ZIP_REGEX = re.compile(r'^\d{5}$')
PO_BOX_REGEX = re.compile(r'P(?:.*?\bBOX\b\s+)([-\w\d]+)', re.IGNORECASE)
PO_BOX_NUMBER_REGEX = re.compile(r'\b[-\w\d]+\b')
STOP_WORDS = {'and', 'the', 'of', 'for', 'in', 'to', 'a', 'an'}
USPS_FIXES = {
    'Saint': 'Street', 'St': 'Street', 'Ave': 'Avenue', 'Av': 'Avenue',
    'Blvd': 'Boulevard', 'Dr': 'Drive', 'Ln': 'Lane', 'Rd': 'Road',
    'Cir': 'Circle', 'Ct': 'Court', 'Pl': 'Place', 'Ter': 'Terrace',
    'Pkwy': 'Parkway', 'Hwy': 'Highway', 'Sq': 'Square'
}
VALID_STATES = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW', 'AA', 'AE', 'AP'}
ADDRESS_COLUMNS = ['filer_ein', 'filer_name', 'canonical_address', 'zip_code', 'po_box']
GRANT_COLUMNS = ['filer_ein', 'filer_name', 'grant_ein', 'grantee_name', 'grant_amt', 'tax_year', 'filer_canonical_address', 'grantee_canonical_address']
DEBUG_ADDRESS_COLUMNS = ['filer_ein', 'filer_name', 'xml_filename', 'raw_components', 'canonical_address', 'raw_zip', 'zip_code', 'status', 'reason']
DEBUG_GRANT_COLUMNS = ['filer_ein', 'grant_ein', 'filer_name', 'grantee_name', 'xml_filename', 'grant_address', 'grant_amt', 'tax_year', 'status', 'heuristic_score', 'reason']
INVALID_EIN_COLUMNS = ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename', 'reason']
PO_BOX_COLUMNS = ['po_box', 'zip_code', 'ein', 'org_name']
CSV_QUOTE_FIELDS = {
    'addresses': ['filer_name', 'canonical_address'],
    'grants': ['filer_name', 'grant_ein'],
    'debug_address': ['filer_name', 'xml_filename', 'raw_components', 'canonical_address', 'reason'],
    'debug_grant': ['filer_name', 'xml_filename', 'grantee_name', 'grant_ein', 'grant_address', 'reason'],
    'invalid_eins': ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename', 'reason'],
    'po_box_matches': ['org_name']
}
EIN_REGEX = re.compile(r'^\d{9}$')

logger = None
quiet = False

def log_error(msg_format, *args, ein=None, exc_info=False):
    if logger and not quiet:
        try:
            logger.info(msg_format.format(*args), extra={'ein': ein} if ein else None, exc_info=exc_info)
        except Exception as e:
            logger.info("Log formatting error: {}; args: {}", str(e), args)

def setup_logging(output_dir, log_filename, verbose, quiet_global):
    global logger, quiet
    quiet = quiet_global
    log_queue = queue.Queue(-1)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(os.path.join(output_dir, log_filename))
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
    print("Computing Zip File Checksums...")
    try:
        result = subprocess.run(['sha256sum'] + glob.glob(os.path.join(zip_dir, '*.zip')), capture_output=True, text=True)
        for line in result.stdout.splitlines():
            hash_val, path = line.split(None, 1)
            checksums[os.path.normpath(path)] = hash_val
    except (subprocess.CalledProcessError, FileNotFoundError):
        for zip_path in glob.glob(os.path.join(zip_dir, '*.zip')):
            with open(zip_path, 'rb') as f:
                sha256 = hashlib.sha256()
                while chunk := f.read(8192):
                    sha256.update(chunk)
                checksums[os.path.normpath(zip_path)] = sha256.hexdigest()
    return checksums

def build_zip_index(zip_dir, start_year, end_year):
    index = {}
    zip_files = []
    zip_names = set()
    for year in range(start_year, end_year + 2):
        zip_files.extend(glob.glob(os.path.join(zip_dir, f"{year}*.zip")))
    log_error("Found {} ZIP files in {}", len(zip_files), zip_dir)
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

def load_zip_cache(cache_dir, start_year, end_year, zip_dir, checksums):
    cache_valid = False
    cached_data = None
    checksum_file = os.path.join(cache_dir, f'zip_checksums_{start_year}_{end_year}.json')
    print("Loading ZIP index cache...")
    if os.path.exists(checksum_file):
        try:
            with open(checksum_file, 'r') as f:
                cached_checksums = json.load(f)
            if cached_checksums == checksums:
                zip_index_file = os.path.join(cache_dir, f'cached_zip_index_{start_year}_{end_year}.pkl')
                if os.path.exists(zip_index_file):
                    with open(zip_index_file, 'rb') as f:
                        cached_data = pickle.load(f)
                    cache_valid = True
        except Exception as e:
            log_error("Error loading ZIP index cache: {}", str(e), exc_info=True)
    return cache_valid, cached_data

def save_zip_cache(cache_dir, start_year, end_year, checksums, zip_index):
    try:
        print("Saving ZIP index cache...")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, f'zip_checksums_{start_year}_{end_year}.json'), 'w') as f:
            json.dump(checksums, f)
        with open(os.path.join(cache_dir, f'cached_zip_index_{start_year}_{end_year}.pkl'), 'wb') as f:
            pickle.dump(zip_index, f)
    except Exception as e:
        log_error("Error saving ZIP index cache: {}", str(e), exc_info=True)

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

def canonicalize_address(address_components, output_dir):
    if not address_components:
        return "", None, None, ""
    address_line = ""
    address_line2 = ""
    address_line3 = ""
    city = ""
    state = ""
    zip_code = None
    po_box = None
    STREET_FIXES = {
        'St': 'Street', 'Saint': 'Street', 'Ave': 'Avenue', 'Av': 'Avenue',
        'Blvd': 'Boulevard', 'Dr': 'Drive', 'Ln': 'Lane', 'Rd': 'Road',
        'Cir': 'Circle', 'Ct': 'Court', 'Pl': 'Place', 'Ter': 'Terrace',
        'Pkwy': 'Parkway', 'Hwy': 'Highway', 'Sq': 'Square',
        'Cres': 'Crescent', 'Plz': 'Plaza', 'Xing': 'Crossing', 'Way': 'Way',
        'Aly': 'Alley', 'Loop': 'Loop', 'Rdg': 'Ridge', 'Trl': 'Trail'
    }
    UNIT_FIXES = {
        'Ste': 'Suite', 'Apt': 'Apartment', 'Unit': 'Unit', 'Bldg': 'Building',
        'Fl': 'Floor', 'Rm': 'Room', 'Dept': 'Department', 'Ofc': 'Office',
        'SPC': 'Space', 'LOT': 'Lot', 'TRLR': 'Trailer', 'BSMT': 'Basement'
    }
    for elem in address_components:
        if elem.tag.endswith('AddressLine1Txt') and elem.text:
            parts = [p.strip() for p in elem.text.split(',') if p.strip()]
            address_line = parts[0] if parts else ""
            if len(parts) > 1:
                address_line2 = parts[1]
            if len(parts) > 2:
                address_line3 = parts[2]
            match = PO_BOX_REGEX.search(address_line)
            if match:
                po_box_str = match.group(1)
                number_match = PO_BOX_NUMBER_REGEX.match(po_box_str)
                if number_match:
                    po_box = number_match.group(0)
                else:
                    log_error("Failed to extract PO box number from: {} in address: {}", po_box_str, address_line)
        elif elem.tag.endswith('AddressLine2Txt') and elem.text:
            address_line2 = elem.text.strip()
        elif elem.tag.endswith('AddressLine3Txt') and elem.text:
            address_line3 = elem.text.strip()
        elif elem.tag.endswith('CityNm') and elem.text:
            city = elem.text.strip()
        elif elem.tag.endswith('StateAbbreviationCd') and elem.text:
            state = elem.text.strip()
        elif elem.tag.endswith('ZIPCd') and elem.text:
            zip_code = elem.text.strip()
    def expand_street(line):
        if not line:
            return line
        words = line.split()
        if not words:
            return line
        if words[-1] in STREET_FIXES:
            words[-1] = STREET_FIXES[words[-1]]
        return " ".join(words)
    def expand_unit(line):
        if not line:
            return line
        words = line.split()
        if len(words) < 2:
            return line
        if words[-2] in UNIT_FIXES:
            words[-2] = UNIT_FIXES[words[-2]]
        return " ".join(words)
    address_line = expand_street(address_line)
    address_line2 = expand_unit(address_line2)
    address_line3 = expand_unit(address_line3)
    address_parts = [comp for comp in [address_line, address_line2, address_line3, city, state, zip_code] if comp]
    if not address_parts:
        log_error("No valid address components: {}", address_components)
        return "", None, None, ""
    canonical = " ".join(address_parts).title()
    if state and state.upper() not in VALID_STATES:
        log_error("Invalid state '{}' in address: {}; resetting state", state, canonical)
        state = None
        canonical = " ".join(comp for comp in [address_line, address_line2, address_line3, city, zip_code] if comp).title()
    if zip_code:
        zip_code_digits = re.sub(r'\D', '', zip_code)
        if len(zip_code_digits) >= 5:
            zip_code = zip_code_digits[:5]
        else:
            log_error("Invalid zip_code: {} in address: {}", zip_code, canonical)
            zip_code = None
    if po_box and 'po box' not in canonical.lower():
        canonical = f"PO Box {po_box} {canonical}"
    return canonical, po_box, zip_code, ""

def write_tsv(file_path, entries, columns, quote_key, sort_keys=None):
    if sort_keys:
        entries = deduplicate_sorted_dicts(entries, sort_keys)
    log_error("Opening TSV file: {}", file_path)
    with open(file_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(columns)
        for entry in entries:
            writer.writerow([entry.get(col, '') for col in columns])
    log_error("Wrote {} rows to {}", len(entries), file_path)

def deduplicate_sorted_dicts(entries, key_order):
    seen = set()
    deduped = []
    for entry in entries:
        key_tuple = tuple(str(entry.get(key, '')) for key in key_order)
        if key_tuple not in seen:
            seen.add(key_tuple)
            deduped.append(entry)
    return sorted(deduped, key=lambda x: tuple(str(x.get(k, '')).lower() for k in key_order))

def load_address_cache(cache_dir, start_year, end_year, zip_dir):
    cache_valid = False
    cached_data = None
    checksum_file = os.path.join(cache_dir, f'zip_checksums_{start_year}_{end_year}.json')
    print("Loading address cache...")
    if os.path.exists(checksum_file):
        try:
            with open(checksum_file, 'r') as f:
                cached_checksums = json.load(f)
            addresses_file = os.path.join(cache_dir, f'cached_addresses_{start_year}_{end_year}.pkl')
            mappings_file = os.path.join(cache_dir, f'cached_mappings_{start_year}_{end_year}.pkl')
            if all(os.path.exists(f) for f in [addresses_file, mappings_file]):
                with open(addresses_file, 'rb') as f:
                    address_entries, debug_address_entries = pickle.load(f)
                with open(mappings_file, 'rb') as f:
                    po_box_entries, zip_code_index, po_box_zip_index = pickle.load(f)
                cached_data = (address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index)
                cache_valid = True
        except Exception as e:
            log_error("Error loading address cache: {}", str(e), exc_info=True)
    return cache_valid, cached_data

def save_address_cache(cache_dir, start_year, end_year, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index):
    try:
        print("Saving address cache...")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, f'cached_addresses_{start_year}_{end_year}.pkl'), 'wb') as f:
            pickle.dump((address_entries, debug_address_entries), f)
        with open(os.path.join(cache_dir, f'cached_mappings_{start_year}_{end_year}.pkl'), 'wb') as f:
            pickle.dump((po_box_entries, zip_code_index, po_box_zip_index), f)
    except Exception as e:
        log_error("Error saving address cache: {}", str(e), exc_info=True)