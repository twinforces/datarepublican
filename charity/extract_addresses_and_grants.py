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
ADDRESS_COLUMNS = ["filer_ein", "canonical_address"]
GRANT_COLUMNS = ["filer_ein", "filer_name", "grant_ein", "grant_amt", "tax_year"]
CSV_QUOTE_FIELDS = {
    'addresses': ['canonical_address'],
    'grants': ['filer_name', 'grant_ein']
}

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
    """Build an index of XML filenames to ZIP paths."""
    index = {}  # filename -> (zip_path, internal_path)
    zip_files = []
    zip_names = set()
    for year in range(start_year, end_year + 2):  # Include end_year + 1 for filing lag
        zip_files.extend(glob.glob(os.path.join(zip_dir, f"{year}*.zip")))
    
    for zip_path in zip_files:
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
    """Read TSV files including charity_latest.tsv and collect rows with xml_name."""
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
    """Canonicalize address using pypostal."""
    if not address_components:
        return ""
    address_str = " ".join(comp for comp in address_components if comp)
    try:
        parsed = parse_address(address_str)
        normalized = expand_address(address_str)
        canonical = normalized[0] if normalized else address_str
        return canonical
    except Exception as e:
        log_error("Error canonicalizing address '{}': {}", address_str, str(e), exc_info=True)
        return address_str

def parse_address_and_grants(xml_content, xml_filename, row, zip_index):
    """Parse address and grants (for 990PF) from XML file."""
    global total_addresses, total_grants, total_queue_puts
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        
        # Parse address
        address_components = []
        for xpath in ADDRESS_XPATHS:
            elements = xpath(root)
            if elements and elements[0].text:
                address_components.append(elements[0].text.strip())
        canonical_address = canonicalize_address(address_components)
        if canonical_address:
            address_entry = {
                'filer_ein': row['filer_ein'],
                'canonical_address': canonical_address
            }
            with queue_lock:
                tsv_write_queue.put(('address', address_entry))
                total_queue_puts += 1
            total_addresses += 1
            if verbose:
                log_error(
                    "Parsed address '{}' for EIN={} in {}",
                    canonical_address, row['filer_ein'], xml_filename, ein=row['filer_ein']
                )
        
        # Parse grants for 990PF
        grants = []
        if row['form_type'] == '990PF':
            for xpath_grant in GRANT_XPATHS_990PF:
                grant_elements = xpath_grant(root)
                for element in grant_elements:
                    ein_elem = element.xpath(".//irs:EIN | .//irs:RecipientEIN", namespaces=NAMESPACES)
                    amount_elem = element.xpath(
                        ".//irs:GrantOrContributionAmt | .//irs:TotalGrantOrContriPdDurYrAmt",
                        namespaces=NAMESPACES
                    )
                    address_elems = element.xpath(
                        ".//irs:USAddress/* | .//irs:ForeignAddress/*",
                        namespaces=NAMESPACES
                    )
                    grant_ein = ein_elem[0].text.strip() if ein_elem and ein_elem[0].text else "Unknown"
                    if amount_elem and amount_elem[0].text:
                        try:
                            grant_amt = int(float(amount_elem[0].text.strip()))
                            if grant_amt > 0:
                                if grant_ein == "Unknown" and address_elems:
                                    address_str = " ".join(
                                        elem.text.strip() for elem in address_elems if elem.text
                                    )
                                    grant_ein = f"Address:{canonicalize_address([address_str])}"
                                if grant_ein != "Unknown":
                                    grants.append({
                                        'filer_ein': row['filer_ein'],
                                        'filer_name': row['filer_name'],
                                        'grant_ein': grant_ein,
                                        'grant_amt': grant_amt,
                                        'tax_year': row['tax_year']
                                    })
                                    if verbose:
                                        log_error(
                                            "Found grant: EIN/Address={}, Amount={} for EIN={} in {}",
                                            grant_ein, grant_amt, row['filer_ein'], xml_filename,
                                            ein=row['filer_ein']
                                        )
                        except (ValueError, TypeError) as e:
                            log_error(
                                "Invalid grant amount '{}' in {} for EIN={}: {}",
                                amount_elem[0].text, xml_filename, row['filer_ein'], str(e),
                                ein=row['filer_ein']
                            )
            if grants:
                with queue_lock:
                    for grant in grants:
                        tsv_write_queue.put(('grant', grant))
                        total_queue_puts += 1
                total_grants += len(grants)
        
        return True
    except Exception as e:
        log_error(
            "Error parsing XML {} for EIN={}: {}", xml_filename, row['filer_ein'], str(e),
            exc_info=True, ein=row['filer_ein']
        )
        return False

def process_rows(rows, worker_threads, zip_index, start_year, end_year):
    """Process rows to extract addresses and grants."""
    zip_cache = {}
    initialize_thread_local_counters()
    total_skipped = 0

    def process_row(row):
        nonlocal total_skipped
        xml_path = row.get('xml_name', '')
        if not xml_path:
            log_error("No xml_name for EIN={}, skipping", row['filer_ein'], ein=row['filer_ein'])
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
                file_counter_local.skipped += 1
                total_skipped += 1
                return
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                log_error(
                    "No file named {} found in ZIP index for EIN={}, skipping",
                    xml_filename, row['filer_ein'], ein=row['filer_ein']
                )
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

def tsv_writer_thread(output_dir, writer_id):
    """Write addresses and grants to TSV files."""
    global total_tasks_done
    address_file = os.path.join(output_dir, "charity_addresses.tsv")
    grant_file = os.path.join(output_dir, "inferred_grants.tsv")
    address_writer = None
    grant_writer = None
    address_buffer = []
    grant_buffer = []
    write_buffer_size = 5000

    while True:
        try:
            item = tsv_write_queue.get(timeout=0.1)
            if item is None:
                log_error("TSV writer thread {} received shutdown signal", writer_id)
                break
            type_, entry = item
            if type_ == 'address':
                address_buffer.append([entry[col] for col in ADDRESS_COLUMNS])
            elif type_ == 'grant':
                grant_buffer.append([entry[col] for col in GRANT_COLUMNS])
            
            if len(address_buffer) >= write_buffer_size or len(grant_buffer) >= write_buffer_size:
                if address_buffer and not address_writer:
                    mode = 'w'
                    address_writer = open(address_file, mode, newline='', encoding='utf-8')
                    csv.writer(address_writer, delimiter='\t').writerow(ADDRESS_COLUMNS)
                    log_error("Opened address TSV {} in mode {}", address_file, mode)
                if grant_buffer and not grant_writer:
                    mode = 'w'
                    grant_writer = open(grant_file, mode, newline='', encoding='utf-8')
                    csv.writer(grant_writer, delimiter='\t').writerow(GRANT_COLUMNS)
                    log_error("Opened grant TSV {} in mode {}", grant_file, mode)
                
                if address_buffer:
                    csv.writer(address_writer, delimiter='\t').writerows(address_buffer)
                    address_writer.flush()
                    log_error("Flushed {} address rows by writer {}", len(address_buffer), writer_id)
                    address_buffer = []
                if grant_buffer:
                    csv.writer(grant_writer, delimiter='\t').writerows(grant_buffer)
                    grant_writer.flush()
                    log_error("Flushed {} grant rows by writer {}", len(grant_buffer), writer_id)
                    grant_buffer = []
            
            # Mark task as done only after successful processing
            with queue_lock:
                tsv_write_queue.task_done()
                total_tasks_done += 1
                if verbose:
                    log_error("Task done for item type {} by writer {}", type_, writer_id)
        except queue.Empty:
            if done_queuing and tsv_write_queue.empty():
                log_error("TSV writer thread {} exiting, queue empty", writer_id)
                break
        except Exception as e:
            log_error("Error in TSV writer {}: {}", writer_id, str(e), exc_info=True)
    
    # Final flush
    if address_buffer and not address_writer:
        mode = 'w'
        address_writer = open(address_file, mode, newline='', encoding='utf-8')
        csv.writer(address_writer, delimiter='\t').writerow(ADDRESS_COLUMNS)
    if grant_buffer and not grant_writer:
        mode = 'w'
        grant_writer = open(grant_file, mode, newline='', encoding='utf-8')
        csv.writer(grant_writer, delimiter='\t').writerow(GRANT_COLUMNS)
    
    if address_buffer:
        csv.writer(address_writer, delimiter='\t').writerows(address_buffer)
        address_writer.flush()
        log_error("Final flush of {} address rows by writer {}", len(address_buffer), writer_id)
    if grant_buffer:
        csv.writer(grant_writer, delimiter='\t').writerows(grant_buffer)
        grant_writer.flush()
        log_error("Final flush of {} grant rows by writer {}", len(grant_buffer), writer_id)
    
    if address_writer:
        address_writer.close()
    if grant_writer:
        grant_writer.close()

def main():
    global verbose, quiet, done_queuing, total_addresses, total_grants, total_queue_puts, total_tasks_done
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

    # Validate arguments
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

    try:
        zip_index = build_zip_index(zip_dir, start_year, end_year)
        if not zip_index:
            print("No ZIP files found in {} for years {}-{}", zip_dir, start_year, end_year + 1)
            return

        rows = read_tsv_files(source_dir, start_year, end_year)
        if not rows:
            print("No valid TSV files found in {}. Ensure 'charity_latest.tsv' or 'charities_<org_type>_<year>.tsv' exist.", source_dir)
            return

        for i in range(writer_threads):
            thread = threading.Thread(target=tsv_writer_thread, args=(output_dir, f"writer-{i}"))
            thread.start()
            writer_threads_list.append(thread)

        process_rows(rows, worker_threads, zip_index, start_year, end_year)

    finally:
        done_queuing = True
        for _ in range(writer_threads):
            tsv_write_queue.put(None)
        for thread in writer_threads_list:
            thread.join()
        listener.stop()
        log_error("Queue summary: {} items put, {} tasks done", total_queue_puts, total_tasks_done)

    print(f"Total addresses extracted: {total_addresses}")
    print(f"Total grants extracted: {total_grants}")
    print(f"Queue summary: {total_queue_puts} items put, {total_tasks_done} tasks done")

if __name__ == "__main__":
    main()