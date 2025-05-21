import os
import glob
import argparse
import logging
import zipfile
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from tqdm import tqdm
import time
from collections import Counter, defaultdict
import re
from contextlib import closing
import psutil
import queue
from logging.handlers import QueueHandler, QueueListener
import logging.handlers
import inspect
from lxml import etree  # Import for XML parsing
from io import BytesIO  # Import for BytesIO

# Import parsing functions from new scripts
import parse_990
import parse_990ez
import parse_990pf

# Constants
# DEBUG_EINS will be set by --eins argument
DEBUG_EINS = set()
TSV_COLUMNS = [
    "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
    "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "comp_ptile",
    "travel_pct", "travel_ptile", "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
    "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio", "total_assets", "form_type",
    "denominator", "foreign_office", "foreign_expenses", "grants_to_others", "domestic_misrep_flag", "xml_name"
]

# Thread-local counters
total_xml_files = 0
file_counter_local = threading.local()
ein_type_dict = {}
missing_taxyr_by_year = defaultdict(int)
invalid_taxyr_by_year = defaultdict(int)
missing_filer_by_year = defaultdict(int)
missing_revenue_by_year = defaultdict(int)
invalid_predicate_by_year = defaultdict(int)
verbose = False
pending_tasks = 0
xml_processed_count = 0  # Track total XMLs processed for sparse logging
log_counts = defaultdict(int)  # Dictionary to count log occurrences by preamble
total_entries = 0  # Global counter for total entries processed
tsv_write_counts = defaultdict(int)
done_queuing = False  # Flag to signal when main thread is done queuing tasks

# Queue for TSV writes with increased size
tsv_write_queue = queue.Queue(maxsize=10000)  # Increased queue size to 10000

# Setup queue-based logging
log_queue = queue.Queue(-1)  # Unlimited size
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
queue_handler = QueueHandler(log_queue)
queue_handler.setFormatter(formatter)

file_handler = logging.FileHandler('extract_log.txt')
file_handler.setFormatter(formatter)
listener = QueueListener(log_queue, file_handler)
listener.start()

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
console_handler.setFormatter(formatter)

# Custom logging handler to apply filtering
class FilteredHandler(logging.Handler):
    def emit(self, record):
        global xml_processed_count, log_counts

        # Extract the message, args, and ein
        msg = record.msg  # The format string
        args = record.args  # The arguments to the format string
        ein = getattr(record, 'ein', None) if hasattr(record, 'ein') else None
        exc_info = record.exc_info

        # Extract the preamble (everything up to the first '{')
        preamble_end = msg.find('{')
        if preamble_end == -1:
            preamble = msg
            data = ""
        else:
            preamble = msg[:preamble_end]
            data = msg[preamble_end:]

        # Increment the counter for this preamble
        log_counts[preamble] += 1

        # Apply filtering logic
        # Limit to 5 logs per preamble unless EIN is in DEBUG_EINS
        if ein and ein not in DEBUG_EINS and log_counts[preamble] > 5:
            return

        # Skip verbose logs unless EIN is in DEBUG_EINS
        if ein and ein not in DEBUG_EINS:
            if any(x in preamble for x in ["Raw ", "Parsed ", "Extracted ", "Assigned tax_year"]):
                return

        # Skip thread-related and TSV creation logs unless verbose or EIN is in DEBUG_EINS
        if any(x in preamble for x in ["Thread", "Wrote row", "Closed and flushed", "Created new TSV", "Periodic flush", "TSV writer thread ", "TSV writer "]):
            if not verbose and (not ein or ein not in DEBUG_EINS):
                return

        # Skip certain missing field logs for 990PF unless verbose or EIN is in DEBUG_EINS
        if "Missing" in preamble and any(field in msg for field in ["govt_grants", "contributions", "foreign_office"]) and "990PF" in msg:
            if not verbose and (not ein or ein not in DEBUG_EINS):
                return

        # For non-DEBUG_EINS, log "Processing XML" and "Finished processing XML" only every 100,000th XML
        if ein and ein not in DEBUG_EINS:
            if "Processing XML" in preamble or "Finished processing XML" in preamble:
                xml_processed_count += 1
                if xml_processed_count % 100000 != 0:
                    return

        # Now that we've passed filtering, format the message with the original arguments
        line_no = inspect.currentframe().f_back.f_lineno
        try:
            formatted_message = f"{line_no} {preamble}{data}"
            if args:
                formatted_message = msg.format(*args)
            formatted_message = f"{line_no} {formatted_message}"
        except (ValueError, TypeError) as e:
            # If formatting fails, log the raw message and arguments for debugging
            formatted_message = f"{line_no} [Formatting error: {str(e)}] {msg} with args {args}"

        # Create a new log record with the formatted message
        new_record = logging.LogRecord(
            name=record.name,
            level=record.levelno,
            pathname=record.pathname,
            lineno=record.lineno,
            msg=formatted_message,
            args=(),  # Clear args to avoid further formatting
            exc_info=exc_info
        )
        # Add ein attribute if present
        if ein:
            setattr(new_record, 'ein', ein)

        # Send the record to the queue handler
        queue_handler.emit(new_record)

        if verbose:
            print(f"Log: {formatted_message}")

# Configure the master logger with the filtered handler
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers = []  # Clear default handlers
filtered_handler = FilteredHandler()
logger.addHandler(filtered_handler)
logger.addHandler(console_handler)

# Assign the master logger and log_error to each parse module
parse_990.set_logger(logger, log_error)
parse_990ez.set_logger(logger, log_error)
parse_990pf.set_logger(logger, log_error)

def initialize_thread_local_counters():
    """Initialize thread-local counters if they haven't been set in the current thread."""
    if not hasattr(file_counter_local, 'value'):
        file_counter_local.value = 0
    if not hasattr(file_counter_local, 'entries'):
        file_counter_local.entries = 0
    if not hasattr(file_counter_local, 'skipped'):
        file_counter_local.skipped = 0

def log_error(msg_format, *args, ein=None, exc_info=False):
    # Format the message and log it through the master logger
    if ein:
        logging.info(msg_format, *args, extra={'ein': ein}, exc_info=exc_info)
    else:
        logging.info(msg_format, *args, exc_info=exc_info)

def clean_org_type(org_type, for_filename=False):
    # First, preserve the original for logging
    original_org_type = org_type
    # Remove all parentheses and spaces for filename or initial check
    org_type_cleaned = org_type.replace('(', '').replace(')', '').replace(' ', '')
    
    # Handle specific cases
    if org_type_cleaned in ["501c3", "501cc3"]:
        cleaned = "501c3"
        formatted = "501(c)(3)"
    elif org_type_cleaned == "4947a1":
        cleaned = "4947a1"
        formatted = "4947(a)(1)"
    elif org_type_cleaned.startswith("501c"):
        # Extract the number after "501c"
        match = re.match(r'501c(\d+)', org_type_cleaned)
        if match:
            num = match.group(1)
            # Validate the number
            if num.isdigit() and 1 <= int(num) <= 29:
                cleaned = f"501c{num}"
                formatted = f"501(c)({num})"
            else:
                log_error("Invalid org_type number in {}: {}", original_org_type, num)
                cleaned = "501c3"
                formatted = "501(c)(3)"  # Default to 501(c)(3) for invalid numbers
        else:
            log_error("Failed to parse org_type number from {}, defaulting to 501(c)(3)", original_org_type)
            cleaned = "501c3"
            formatted = "501(c)(3)"
    else:
        cleaned = org_type_cleaned.lower()
        formatted = org_type.lower()

    # Return the appropriate format based on the for_filename flag
    return cleaned if for_filename else formatted

def tsv_writer_thread(tsv_files, writer_id):
    global done_queuing
    while True:
        try:
            # Get the next write task from the queue (non-blocking with timeout)
            tax_year, org_type, row, xml_filename = tsv_write_queue.get(timeout=10)
            log_error("TSV writer thread {} dequeued row for EIN {} in {}", writer_id, row[1], xml_filename, ein=row[1])
        except queue.Empty:
            # If the queue is empty for 10 seconds, check if main thread is done queuing
            if done_queuing and tsv_write_queue.empty():
                log_error("TSV writer thread {} exiting, queue empty and main thread done queuing", writer_id)
                break
            log_error("TSV writer thread {} waiting, queue size: {}", writer_id, tsv_write_queue.qsize())
            continue

        try:
            # Write the row immediately
            write_buffered_rows(tsv_files, tax_year, org_type, [(row, xml_filename)], writer_id)
        except Exception as e:
            log_error("Error processing TSV row for {} by writer {}: {}", xml_filename, writer_id, str(e), exc_info=True)
        finally:
            tsv_write_queue.task_done()

def write_buffered_rows(tsv_files, tax_year, org_type, rows, writer_id):
    if not rows:
        log_error("TSV writer thread {} received empty rows for tax_year {}, org_type {}", writer_id, tax_year, org_type)
        return  # Nothing to write

    start_write_time = time.time()
    # Use the cleaned version for the filename, but keep the original org_type in the row
    org_type_filename = clean_org_type(org_type, for_filename=True)
    tsv_key = (tax_year, org_type)
    tsv_path = f"charities_{org_type_filename}_{tax_year}.tsv"

    # Open the TSV file if it’s not already open
    if tsv_key not in tsv_files:
        file_exists = os.path.exists(tsv_path)
        mode = 'a' if file_exists else 'w'
        tsv_files[tsv_key] = open(tsv_path, mode=mode, newline="", encoding="utf-8", buffering=8192)
        writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
        if not file_exists:
            writer.writerow(TSV_COLUMNS)
            tsv_files[tsv_key].flush()
            log_error("Created new TSV {} with header", tsv_path)
        log_error("Opened TSV {} in mode {}", tsv_path, mode)
        tsv_write_counts[tsv_key] = 0

    writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
    for row, xml_filename in rows:
        writer.writerow(row)
        tsv_write_counts[tsv_key] += 1
        log_error("TSV writer {} wrote row to TSV {} for EIN {}, org_type {}, tax_year {}, XML {}", 
                  writer_id, tsv_path, row[1], org_type, tax_year, xml_filename, ein=row[1])
        if tsv_write_counts[tsv_key] % 5000 == 0:
            tsv_files[tsv_key].flush()
            log_error("Periodic flush after {} rows to TSV {} by writer {}", tsv_write_counts[tsv_key], tsv_path, writer_id)

def parse_xml_file(xml_content, xml_filename, zip_prefix):
    # Initialize thread-local counters for this thread
    initialize_thread_local_counters()

    start_time = time.time()
    form_type = None
    try:
        log_error("Processing XML: {}", xml_filename)
        # Determine form type by parsing the XML
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        namespaces = {'irs': 'http://www.irs.gov/efile'}

        # Extract form type (minimal parsing to determine which script to use)
        form_type_paths = [
            ".//irs:ReturnHeader/irs:ReturnTypeCd",
            ".//ReturnHeader/ReturnTypeCd"
        ]
        form_type_elem = find_element(root, form_type_paths, namespaces)
        form_type = form_type_elem.text if form_type_elem is not None else "Unknown"

        # Delegate parsing to the appropriate script
        if form_type == "990":
            row = parse_990.parse_990(xml_content, xml_filename)
        elif form_type == "990EZ":
            row = parse_990ez.parse_990ez(xml_content, xml_filename)
        elif form_type == "990PF":
            row = parse_990pf.parse_990pf(xml_content, xml_filename)
        else:
            log_error("Unsupported form type {} in {}, skipping", form_type, xml_filename)
            file_counter_local.skipped += 1
            return [], None, xml_filename

        if row is None:
            file_counter_local.skipped += 1
            return [], None, xml_filename

        # Extract tax_year and org_type from the row for TSV writing
        tax_year = row[0]  # First column: tax_year
        org_type = row[6]  # Seventh column: org_type
        total_exp = float(row[7]) if row[7] else 0  # Eighth column: total_exp
        grift_ratio = float(row[22]) if row[22] else 0  # Twenty-third column: grift_ratio
        ein = row[1]  # Second column: filer_ein

        # Check for suspicious grift_ratio and log using log_error
        if grift_ratio > 100 and total_exp > 0:
            log_error("Suspicious grift_ratio {}% for EIN {} in {}", grift_ratio, ein, xml_filename, ein=ein)

        results = [row]

        log_error("Finished processing XML {}, took {:.2f} seconds, memory usage: {}%", xml_filename, time.time() - start_time, psutil.virtual_memory().percent, ein=ein)
        return results, org_type, xml_filename

    except Exception as e:
        log_error("Error processing {}: {}", xml_filename, str(e), exc_info=True, ein=None)
        file_counter_local.skipped += 1
        return [], None, xml_filename

def find_element(root, xpaths, namespaces):
    for xpath in xpaths:
        try:
            elem = root.xpath(xpath, namespaces=namespaces)
            if elem:
                return elem[0]
        except etree.XPathEvalError as e:
            xml_snippet = etree.tostring(root, encoding='unicode', method='xml')[:2000]
            log_error("XPath error for {}: {}. XML snippet: {}", xpath, str(e), xml_snippet)
            non_ns_xpath = xpath.replace('irs:', '').replace('{http://www.irs.gov/efile}', '')
            try:
                elem = root.xpath(non_ns_xpath, namespaces=None)
                if elem:
                    return elem[0]
            except etree.XPathEvalError as e:
                log_error("Non-namespaced XPath error for {}: {}. XML snippet: {}", non_ns_xpath, str(e), xml_snippet)
    return None

def process_zip_file(zip_path, start_year, end_year):
    # Initialize thread-local counters for this thread
    initialize_thread_local_counters()

    global pending_tasks, total_entries
    log_error("Processing ZIP: {}", zip_path)
    zip_prefix = os.path.basename(zip_path)[:4]

    if not zip_prefix.isdigit():
        log_error("ZIP file {} does not start with a valid year, skipping", zip_path)
        return

    zip_year = int(zip_prefix)
    if zip_year < start_year or zip_year > end_year:
        log_error("ZIP file year {} outside range {} to {}, skipping", zip_year, start_year, end_year)
        return

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            xml_files = [f for f in zip_ref.namelist() if f.endswith('.xml')]
            total_xml_files = len(xml_files)
            log_error("Found {} XML files in {}", total_xml_files, zip_path)

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = []
                batch_size = 10  # Process up to 10 XML files at a time
                with tqdm(total=len(xml_files), desc=f"Processing {zip_path}") as pbar:
                    for i, xml_filename in enumerate(xml_files):
                        # Read one XML file at a time to reduce memory usage
                        try:
                            with zip_ref.open(xml_filename) as xml_file:
                                xml_content = xml_file.read()
                        except Exception as e:
                            log_error("Error reading XML {} from {}: {}", xml_filename, zip_path, str(e), exc_info=True)
                            pbar.update(1)
                            continue

                        future = executor.submit(parse_xml_file, xml_content, xml_filename, zip_prefix)
                        futures.append((future, xml_filename))

                        # Process futures in batches
                        if len(futures) >= batch_size or i == len(xml_files) - 1:
                            for future, xml_filename in futures:
                                try:
                                    result = future.result(timeout=300)  # 5-minute timeout per XML
                                    if not isinstance(result, tuple) or len(result) != 3:
                                        log_error("Invalid return value from parse_xml_file for {}: expected 3-tuple, got {}", xml_filename, result)
                                        file_counter_local.skipped += 1
                                        pbar.update(1)
                                        continue
                                    results, org_type, xml_filename = result
                                    if results is None or org_type is None:
                                        log_error("Parsing failed for {}, skipping", xml_filename)
                                        file_counter_local.skipped += 1
                                        pbar.update(1)
                                        continue
                                    for row in results:
                                        tax_year = row[0]
                                        if tax_year == "Unknown":
                                            tax_year = zip_prefix
                                            row[0] = tax_year
                                        # Use non-blocking put to avoid deadlock
                                        try:
                                            tsv_write_queue.put_nowait((tax_year, org_type, row, xml_filename))
                                            total_entries += 1
                                        except queue.Full:
                                            log_error("TSV write queue full, waiting to put row for {}", xml_filename)
                                            tsv_write_queue.put((tax_year, org_type, row, xml_filename), block=True)
                                            total_entries += 1
                                    pbar.update(1)
                                except TimeoutError:
                                    log_error("Timeout processing XML {} in {}", xml_filename, zip_path)
                                    file_counter_local.skipped += 1
                                    pbar.update(1)
                                except Exception as e:
                                    log_error("Error processing XML {} in {}: {}", xml_filename, zip_path, str(e), exc_info=True)
                                    file_counter_local.skipped += 1
                                    pbar.update(1)
                            futures = []  # Clear the futures list for the next batch

    except Exception as e:
        log_error("Error processing ZIP {}: {}", zip_path, str(e), exc_info=True)

def main():
    global verbose, done_queuing, total_entries, DEBUG_EINS
    parser = argparse.ArgumentParser(description="Extract charity data from IRS 990 XML files.")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--eins", type=str, help="Comma-separated list of EINs for extra logging")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    verbose = args.verbose
    start_year = args.start_year
    end_year = args.end_year
    # Set DEBUG_EINS based on --eins argument
    if args.eins:
        DEBUG_EINS = set(args.eins.split(','))
        log_error("Set DEBUG_EINS for extra logging: {}", ",".join(DEBUG_EINS))

    zip_files = sorted(glob.glob("*.zip"))
    if not zip_files:
        print("No ZIP files found in the current directory")
        return

    # Start TSV writer threads
    tsv_files = {}
    writer_threads = []
    for i in range(5):  # 5 TSV writer threads
        thread = threading.Thread(target=tsv_writer_thread, args=(tsv_files, f"writer-{i}"))
        thread.start()
        writer_threads.append(thread)

    try:
        for zip_path in zip_files:
            process_zip_file(zip_path, start_year, end_year)

    finally:
        done_queuing = True  # Signal writer threads to exit
        for thread in writer_threads:
            thread.join()

        # Close all open TSV files
        for tsv_file in tsv_files.values():
            tsv_file.close()

        # Stop the logging listener
        listener.stop()

    print(f"Total entries processed: {total_entries}")

if __name__ == "__main__":
    main()