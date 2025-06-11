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
import json
from parse_utils import ORG_TYPE_PATTERN

# Import parsing functions from new scripts
import parse_990
import parse_990ez
import parse_990pf
from xpaths_990 import XPATHS_990
from xpaths_990ez import XPATHS_990EZ
from xpaths_990pf import XPATHS_990PF

# Constants
DEBUG_EINS = set()
TSV_COLUMNS = [
    "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
    "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "comp_ptile",
    "travel_pct", "travel_ptile", "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
    "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio", "total_assets", "form_type",
    "denominator", "foreign_office", "foreign_expenses", "grants_to_others", "domestic_misrep_flag", "xml_name"
]
OFFICER_MAPPING_FILE = "officer_mapping.json"
QUEUE_SIZE = 20000

# Precompile XPaths used in parse_xml_file
FORM_TYPE_XPATHS = [
    etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//ReturnHeader/ReturnTypeCd")
]
TAX_YEAR_XPATHS = [
    etree.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//ReturnHeader/TaxYr")
]
FILER_EIN_XPATHS = [
    etree.XPath(".//irs:Filer/irs:EIN", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//Filer/EIN")
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
quiet = False  # Flag to disable all logs
pending_tasks = 0
xml_processed_count = 0  # Track total XMLs processed for sparse logging
log_counts = defaultdict(int)  # Dictionary to count log occurrences by preamble
total_entries = 0  # Global counter for total entries processed
tsv_write_counts = defaultdict(int)
done_queuing = False  # Flag to signal when main thread is done queuing tasks

# Queue for TSV writes with increased size
tsv_write_queue = queue.Queue(maxsize=20000)
officer_queue = queue.Queue(maxsize=QUEUE_SIZE)

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

# Global dictionary to track XPath match statistics
xpath_match_stats = defaultdict(int)

# Initialize xpath_match_stats with all possible combinations
def initialize_xpath_stats():
    form_types = {
        "990": XPATHS_990,
        "990EZ": XPATHS_990EZ,
        "990PF": XPATHS_990PF
    }
    for form_type, xpaths_dict in form_types.items():
        for field, xpaths in xpaths_dict.items():
            for xpath in xpaths:
                # Convert etree.XPath object to its string representation
                xpath_str = xpath.path if isinstance(xpath, etree.XPath) else xpath
                key = f"{form_type}:{field}:{xpath_str}"
                xpath_match_stats[key] = 0

# Custom logging handler to apply filtering
class FilteredHandler(logging.Handler):
    def emit(self, record):
        global xml_processed_count, log_counts

        # If quiet mode is enabled, suppress all logs
        if quiet:
            return

        # Extract the message, args, and ein
        msg = record.msg
        args = record.args
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
        # Limit to 5 logs per preamble unless verbose is enabled
        if not verbose and log_counts[preamble] > 5:
            return

        # Skip verbose logs unless verbose is enabled
        if not verbose:
            if any(x in preamble for x in [
                "Raw ", "Parsed ", "Extracted ", "Assigned tax_year", "Found org_type element",
                "Suspicious", "Parsing", "Non-zero", "Zero"
            ]):
                return
        if any(x in preamble for x in [
            "Thread", "Wrote row", "Closed and flushed", "Created new TSV", "Periodic flush",
            "TSV writer thread ", "TSV writer ", "Officer writer thread ", "Officer writer "
        ]):
            if not verbose:
                return
        if "Missing" in preamble and any(field in msg for x in [
            "govt_grants", "contributions", "foreign_office"
        ]) and "990PF" in msg:
            if not verbose:
                return

        # For all EINs, log "Processing XML" and "Finished processing XML" only every 1,000th XML when verbose is on
        if "Processing XML" in preamble or "Finished processing XML" in preamble:
            if not verbose:
                return
            xml_processed_count += 1
            if xml_processed_count % 1000 != 0:
                return

        # Suppress non-EIN messages unless verbose
        if ein is None and not verbose:
            if any(preamble.startswith(x) for x in [
                "Processing ZIP", "Found ", "Set DEBUG_EINS", "Extracted ", "ZIP file year"
            ]):
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
            args=(),
            exc_info=exc_info
        )
        if ein:
            setattr(new_record, 'ein', ein)

        # Send the record to the queue handler
        queue_handler.emit(new_record)

        if verbose:
            print(f"Log: {formatted_message}")

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers = []  # Clear default handlers
filtered_handler = FilteredHandler()
logger.addHandler(filtered_handler)
logger.addHandler(console_handler)

def log_error(msg_format, *args, ein=None, exc_info=False):
    # If quiet mode is enabled, suppress all logs
    if quiet:
        return

    # Format the message and log it through the master logger
    if ein:
        logging.info(msg_format, *args, extra={'ein': ein}, exc_info=exc_info)
    else:
        logging.info(msg_format, *args, exc_info=exc_info)

# Assign the master logger and log_error to each parse module
parse_990.set_logger(logger, log_error)
parse_990ez.set_logger(logger, log_error)
parse_990pf.set_logger(logger, log_error, verbose, DEBUG_EINS)

def initialize_thread_local_counters():
    """Initialize thread-local counters if they haven't been set in the current thread."""
    if not hasattr(file_counter_local, 'value'):
        file_counter_local.value = 0
    if not hasattr(file_counter_local, 'entries'):
        file_counter_local.entries = 0
    if not hasattr(file_counter_local, 'skipped'):
        file_counter_local.skipped = 0

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

def tsv_writer_thread(tsv_files, writer_id, buffers, write_buffer_size):
    global done_queuing
    while True:
        try:
            # Get the next write task from the queue with a short timeout
            item = tsv_write_queue.get(timeout=0.1)
            if item is None:  # Sentinel value to indicate shutdown
                log_error("TSV writer thread {} received shutdown signal", writer_id)
                # Flush any remaining buffers
                for tsv_key, buffer in buffers.items():
                    if buffer:
                        tax_year, org_type = tsv_key
                        tsv_path = f"charities_{clean_org_type(org_type, for_filename=True)}_{tax_year}.tsv"
                        # Ensure the TSV file exists
                        if tsv_key not in tsv_files:
                            file_exists = os.path.exists(tsv_path)
                            mode = 'a' if file_exists else 'w'
                            tsv_files[tsv_key] = open(tsv_path, mode=mode, newline="", encoding="utf-8", buffering=8192)
                            writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
                            if not file_exists:
                                writer.writerow(TSV_COLUMNS)
                                tsv_files[tsv_key].flush()
                                log_error("Created new TSV {} with header by writer {}", tsv_path, writer_id)
                            log_error("Opened TSV {} in mode {} by writer {}", tsv_path, mode, writer_id)
                            tsv_write_counts[tsv_key] = 0
                        writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
                        writer.writerows([row for row, _ in buffer])
                        tsv_files[tsv_key].flush()
                        log_error("Final flush of {} rows to TSV {} by writer {}", len(buffer), tsv_path, writer_id)
                break
            tax_year, org_type, row, xml_name = item
        except queue.Empty:
            # If the queue is empty, check if main thread is done queuing
            if done_queuing and tsv_write_queue.empty():
                log_error("TSV writer thread {} exiting, queue empty and main thread done queuing", writer_id)
                # Flush any remaining buffers
                for tsv_key, buffer in buffers.items():
                    if buffer:
                        tax_year, org_type = tsv_key
                        tsv_path = f"charities_{clean_org_type(org_type, for_filename=True)}_{tax_year}.tsv"
                        # Ensure the TSV file exists
                        if tsv_key not in tsv_files:
                            file_exists = os.path.exists(tsv_path)
                            mode = 'a' if file_exists else 'w'
                            tsv_files[tsv_key] = open(tsv_path, mode=mode, newline="", encoding="utf-8", buffering=8192)
                            writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
                            if not file_exists:
                                writer.writerow(TSV_COLUMNS)
                                tsv_files[tsv_key].flush()
                                log_error("Created new TSV {} with header by writer {}", tsv_path, writer_id)
                            log_error("Opened TSV {} in mode {} by writer {}", tsv_path, mode, writer_id)
                            tsv_write_counts[tsv_key] = 0
                        writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
                        writer.writerows([row for row, _ in buffer])
                        tsv_files[tsv_key].flush()
                        log_error("Final flush of {} rows to TSV {} by writer {}", len(buffer), tsv_path, writer_id)
                break
            continue

        try:
            # Buffer the row
            tsv_key = (tax_year, org_type)
            if tsv_key not in buffers:
                buffers[tsv_key] = []
            buffers[tsv_key].append((row, xml_name))
            if len(buffers[tsv_key]) >= write_buffer_size:
                tsv_path = f"charities_{clean_org_type(org_type, for_filename=True)}_{tax_year}.tsv"
                # Ensure the TSV file exists
                if tsv_key not in tsv_files:
                    file_exists = os.path.exists(tsv_path)
                    mode = 'a' if file_exists else 'w'
                    tsv_files[tsv_key] = open(tsv_path, mode=mode, newline="", encoding="utf-8", buffering=8192)
                    writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
                    if not file_exists:
                        writer.writerow(TSV_COLUMNS)
                        tsv_files[tsv_key].flush()
                        log_error("Created new TSV {} with header by writer {}", tsv_path, writer_id)
                    log_error("Opened TSV {} in mode {} by writer {}", tsv_path, mode, writer_id)
                    tsv_write_counts[tsv_key] = 0
                writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
                writer.writerows([row for row, _ in buffers[tsv_key]])
                tsv_write_counts[tsv_key] += len(buffers[tsv_key])
                tsv_files[tsv_key].flush()
                log_error("Flushed {} rows to TSV {} by writer {}", len(buffers[tsv_key]), tsv_path, writer_id)
                buffers[tsv_key] = []

        except Exception as e:
            log_error("Error processing TSV row for {} by writer {}: {}", xml_name, writer_id, str(e), exc_info=True)
        finally:
            tsv_write_queue.task_done()

def officer_writer_thread(writer_id):
    global done_queuing, officer_queue
    officer_data = defaultdict(lambda: defaultdict(list))
    while True:
        try:
            item = officer_queue.get(timeout=0.1)
            if item is None:
                log_error("Officer writer thread {} received shutdown signal", writer_id)
                break
            entry = item
        except queue.Empty:
            if done_queuing and officer_queue.empty():
                log_error("Officer writer thread {} exiting, queue empty and main thread done queuing", writer_id)
                break
            continue
        try:
            last_name = entry["last_name"]
            first_name = entry["first_name"]
            officer_data[last_name][first_name].append({
                "amount": entry["amount"],
                "ein": entry["ein"],
                "charity_name": entry["charity_name"],
                "tax_year": entry["tax_year"]
            })
            if verbose:
                log_error("Officer writer {} processed entry for {} {} in {}", writer_id, first_name, last_name, entry["tax_year"])
        except Exception as e:
            log_error("Error processing officer entry by writer {}: {}", writer_id, str(e), exc_info=True)
        finally:
            officer_queue.task_done()
    
    # Write officer mapping file
    result = {}
    grand_total = 0
    for last_name in sorted(officer_data.keys()):
        result[last_name] = {}
        last_name_total = 0
        for first_name in sorted(officer_data[last_name].keys()):
            entries = officer_data[last_name][first_name]
            first_name_total = sum(entry["amount"] for entry in entries)
            result[last_name][first_name] = entries
            result[last_name]["subtotal"] = first_name_total
            last_name_total += first_name_total
        result[last_name]["subtotal"] = last_name_total
        grand_total += last_name_total
    result["total"] = grand_total
    
    try:
        with open(OFFICER_MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=4)
        log_error("Wrote officer mapping to {}", OFFICER_MAPPING_FILE)
    except Exception as e:
        log_error("Error writing officer mapping file {}: {}", OFFICER_MAPPING_FILE, str(e), exc_info=True)

def parse_xml_file(xml_content, xml_filename, zip_prefix, zip_path):
    initialize_thread_local_counters()
    xpath_cache = {}
    start_time = time.time()
    form_type = None
    try:
        # Parse the XML once
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()

        # Extract form_type, tax_year, and filer_ein using precompiled XPaths
        form_type = None
        for xpath in FORM_TYPE_XPATHS:
            elem = xpath(root)
            if elem:
                form_type = elem[0].text
                break
        form_type = form_type if form_type is not None else "Unknown"

        tax_year = None
        for xpath in TAX_YEAR_XPATHS:
            elem = xpath(root)
            if elem:
                tax_year = elem[0].text
                break
        tax_year = tax_year if tax_year is not None else "Unknown"
        if tax_year == "Unknown":
            tax_year = xml_filename[:4] if xml_filename[:4].isdigit() else "Unknown"
        else:
            try:
                int(tax_year)
            except ValueError:
                tax_year = xml_filename[:4] if xml_filename[:4].isdigit() else "Unknown"

        filer_ein = None
        for xpath in FILER_EIN_XPATHS:
            elem = xpath(root)
            if elem:
                raw_ein = elem[0].text.strip()
                try:
                    filer_ein = f"{int(raw_ein):09d}"
                except ValueError:
                    filer_ein = "Unknown"
                    break
        filer_ein = filer_ein if filer_ein is not None else "Unknown"

        if filer_ein == "Unknown":
            log_error("Missing Filer EIN in {}", xml_filename)
            file_counter_local.skipped += 1
            return [], None, xml_filename, []
        if form_type == "990":
            row, officer_entries = parse_990.parse_990(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
        elif form_type == "990EZ":
            row, officer_entries = parse_990ez.parse_990ez(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
        elif form_type == "990PF":
            row, officer_entries = parse_990pf.parse_990pf(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
        else:
            log_error("Unsupported form type {} in {}, skipping", form_type, xml_filename)
            file_counter_local.skipped += 1
            return [], None, xml_filename

        if row is None:
            log_error("Parsing returned None for {}, skipping", xml_filename)
            file_counter_local.skipped += 1
            return [], None, xml_filename, []
        tax_year = row[0]
        org_type = row[6]
        total_exp = float(row[7]) if row[7] else 0
        grift_ratio = float(row[22]) if row[22] else 0
        ein = row[1]
        if grift_ratio > 100 and total_exp > 0:
            log_error("Suspicious grift_ratio {}% for EIN {} in {}", grift_ratio, ein, xml_filename, ein=ein)
        row[-1] = f"{zip_path}/{xml_filename}"
        results = [row]
        log_error("Finished processing XML {}, took {:.2f} seconds, memory usage: {}%", xml_filename, time.time() - start_time, psutil.virtual_memory().percent, ein=ein)
        return results, org_type, f"{zip_path}/{xml_filename}", officer_entries
    except Exception as e:
        log_error("Error processing {}: {}", xml_filename, str(e), exc_info=True, ein=None)
        file_counter_local.skipped += 1
        return [], None, xml_filename, []

def process_zip_file(zip_path, start_year, end_year, worker_threads, batch_size):
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
            if not xml_files:
                log_error("ZIP file {} contains no XML files, skipping", zip_path)
                return
            xml_file_counts = Counter(xml_files)
            duplicates = {filename: count for filename, count in xml_file_counts.items() if count > 1}
            if duplicates:
                log_error("Found duplicate XML files in {}: {}", zip_path, duplicates)
            total_xml_files = len(xml_files)
            log_error("Found {} XML files in {}", total_xml_files, zip_path)
            with ThreadPoolExecutor(max_workers=worker_threads) as executor:
                futures = []
                with tqdm(total=len(xml_files), desc=f"Processing {zip_path}") as pbar:
                    for i, xml_filename in enumerate(xml_files):
                        try:
                            with zip_ref.open(xml_filename) as xml_file:
                                xml_content = xml_file.read()
                        except Exception as e:
                            log_error("Error reading XML {} from {}: {}", xml_filename, zip_path, str(e), exc_info=True)
                            pbar.update(1)
                            continue
                        future = executor.submit(parse_xml_file, xml_content, xml_filename, zip_prefix, zip_path)
                        futures.append((future, xml_filename))
                        if len(futures) >= batch_size or i == len(xml_files) - 1:
                            for future, xml_filename in futures:
                                try:
                                    result = future.result(timeout=300)
                                    if not isinstance(result, tuple) or len(result) != 4:
                                        log_error("Invalid return value from parse_xml_file for {}: expected 4-tuple, got {}", xml_filename, result)
                                        file_counter_local.skipped += 1
                                        pbar.update(1)
                                        continue
                                    results, org_type, xml_name, officer_entries = result
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
                                        try:
                                            tsv_write_queue.put_nowait((tax_year, org_type, row, xml_name))
                                            total_entries += 1
                                        except queue.Full:
                                            log_error("TSV write queue full, waiting to put row for {}", xml_name)
                                            tsv_write_queue.put((tax_year, org_type, row, xml_name), block=True)
                                            total_entries += 1
                                    for entry in officer_entries:
                                        try:
                                            officer_queue.put_nowait(entry)
                                        except queue.Full:
                                            log_error("Officer queue full, waiting to put entry for {} {}", entry["first_name"], entry["last_name"])
                                            officer_queue.put(entry, block=True)
                                    pbar.update(1)
                                except TimeoutError:
                                    log_error("Timeout processing XML {} in {}", xml_filename, zip_path)
                                    file_counter_local.skipped += 1
                                    pbar.update(1)
                                except Exception as e:
                                    log_error("Error processing XML {} in {}: {}", xml_filename, zip_path, str(e), exc_info=True)
                                    file_counter_local.skipped += 1
                                    pbar.update(1)
                            futures = []
    except Exception as e:
        log_error("Error processing ZIP {}: {}", zip_path, str(e), exc_info=True)

def save_xpath_stats():
    log_error("Saving xpath_stats.json. Current xpath_match_stats: {}", dict(xpath_match_stats))
    with open("xpath_stats.json", "w") as f:
        json.dump(dict(xpath_match_stats), f, indent=4)

def reorder_xpaths():
    def reorder_dict(xpaths_dict, form_type):
        for field, xpaths in xpaths_dict.items():
            xpaths_with_counts = [(xpath, xpath_match_stats.get(f"{form_type}:{field}:{xpath}", 0)) for xpath in xpaths]
            xpaths_with_counts.sort(key=lambda x: x[1], reverse=True)
            xpaths_dict[field] = [xpath for xpath, count in xpaths_with_counts]
    reorder_dict(XPATHS_990, "990")
    reorder_dict(XPATHS_990EZ, "990EZ")
    reorder_dict(XPATHS_990PF, "990PF")
    with open("xpaths_990_reordered.py", "w") as f:
        f.write("from lxml import etree\n\n")
        f.write("NAMESPACES = {'irs': 'http://www.irs.gov/efile'}\n\n")
        f.write("XPATHS_990 = {\n")
        for field, xpaths in XPATHS_990.items():
            f.write(f'    "{field}": [\n')
            for xpath in xpaths:
                f.write(f'        etree.XPath("{xpath}", namespaces=NAMESPACES),\n')
            f.write("    ],\n")
        f.write("}\n")
    with open("xpaths_990ez_reordered.py", "w") as f:
        f.write("from lxml import etree\n\n")
        f.write("NAMESPACES = {'irs': 'http://www.irs.gov/efile'}\n\n")
        f.write("XPATHS_990EZ = {\n")
        for field, xpaths in XPATHS_990EZ.items():
            f.write(f'    "{field}": [\n')
            for xpath in xpaths:
                f.write(f'        etree.XPath("{xpath}", namespaces=NAMESPACES),\n')
            f.write("    ],\n")
        f.write("}\n")
    with open("xpaths_990pf_reordered.py", "w") as f:
        f.write("from lxml import etree\n\n")
        f.write("NAMESPACES = {'irs': 'http://www.irs.gov/efile'}\n\n")
        f.write("XPATHS_990PF = {\n")
        for field, xpaths in XPATHS_990PF.items():
            f.write(f'    "{field}": [\n')
            for xpath in xpaths:
                f.write(f'        etree.XPath("{xpath}", namespaces=NAMESPACES),\n')
            f.write("    ],\n")
        f.write("}\n")

def preallocate_tsv_files(start_year, end_year):
    org_types = [
        "501c2", "501c3", "501c4", "501c5", "501c6", "501c7", "501c8", "501c9",
        "501c10", "501c11", "501c12", "501c13", "501c14", "501c15", "501c16",
        "501c17", "501c18", "501c19", "501c20", "501c21", "501c22", "501c23",
        "501c25", "501c26", "501c27", "501c29", "4947a1"
    ]
    tsv_files = {}
    for year in range(start_year - 3, end_year + 2):
        for org_type in org_types:
            tsv_key = (str(year), f"501(c)({org_type[4:]})" if org_type.startswith("501c") else org_type)
            tsv_path = f"charities_{org_type}_{year}.tsv"
            if os.path.exists(tsv_path):
                os.remove(tsv_path)
            tsv_files[tsv_key] = open(tsv_path, mode='w', newline="", encoding="utf-8", buffering=8192)
            writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
            writer.writerow(TSV_COLUMNS)
            tsv_files[tsv_key].flush()
            log_error("Preallocated TSV {}", tsv_path)
    return tsv_files

def cleanup_empty_tsv_files():
    for tsv_path in glob.glob("charities_*.tsv"):
        with open(tsv_path, 'r', encoding='utf-8') as f:
            line_count = sum(1 for _ in f)
        if line_count <= 1:
            os.remove(tsv_path)
            log_error("Removed empty TSV file {}", tsv_path)

def main():
    global verbose, quiet, done_queuing, total_entries, DEBUG_EINS
    global officer_queue
    parser = argparse.ArgumentParser(description="Extract charity data from IRS 990 XML files.")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--eins", type=str, help="Comma-separated list of EINs for extra logging")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument("--write-buffer-size", type=int, default=10000, help="Number of rows to buffer before writing to TSV")
    parser.add_argument("--worker-threads", type=int, default=16, help="Number of worker threads for XML parsing")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for processing futures")
    parser.add_argument("--writer-threads", type=int, default=1, help="Number of TSV writer threads")
    args = parser.parse_args()
    verbose = args.verbose
    quiet = args.quiet
    start_year = args.start_year
    end_year = args.end_year
    write_buffer_size = args.write_buffer_size
    worker_threads = args.worker_threads
    batch_size = args.batch_size
    writer_threads = args.writer_threads
    if write_buffer_size < 1:
        raise ValueError("Write buffer size must be at least 1")
    if worker_threads < 1:
        raise ValueError("Number of worker threads must be at least 1")
    if batch_size < 1:
        raise ValueError("Batch size must be at least 1")
    if writer_threads < 1:
        raise ValueError("Number of writer threads must be at least 1")
    if args.eins:
        DEBUG_EINS = set(args.eins.split(','))
        log_error("Set DEBUG_EINS for extra logging: {}", ",".join(DEBUG_EINS))
    print(f"DEBUG: verbose is set to {verbose}")
    parse_990.set_logger(logger, log_error, verbose, DEBUG_EINS)
    parse_990ez.set_logger(logger, log_error, verbose, DEBUG_EINS)
    parse_990pf.set_logger(logger, log_error, verbose, DEBUG_EINS)
    initialize_xpath_stats()
    zip_files = sorted(glob.glob("*.zip"))
    if not zip_files:
        print("No ZIP files found in the current directory")
        return
    tsv_files = preallocate_tsv_files(start_year, end_year)
    writer_threads_list = []
    buffers = [defaultdict(list) for _ in range(writer_threads)]
    for i in range(writer_threads):
        thread = threading.Thread(target=tsv_writer_thread, args=(tsv_files, f"writer-{i}", buffers[i], write_buffer_size))
        thread.start()
        writer_threads_list.append(thread)
    
    # Start officer writer thread
    officer_writer = threading.Thread(target=officer_writer_thread, args=(f"officer-writer-0",))
    officer_writer.start()
    
    try:
        for zip_path in zip_files:
            process_zip_file(zip_path, start_year, end_year, worker_threads, batch_size)
    finally:
        done_queuing = True
        for _ in range(writer_threads):
            tsv_write_queue.put(None)
        officer_queue.put(None)
        for thread in writer_threads_list:
            thread.join()
        officer_writer.join()
        for tsv_file in tsv_files.values():
            tsv_file.close()
        cleanup_empty_tsv_files()
        save_xpath_stats()
        reorder_xpaths()
        listener.stop()
    print(f"Total entries processed: {total_entries}")

if __name__ == "__main__":
    main()