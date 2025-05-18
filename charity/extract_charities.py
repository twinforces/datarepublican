import os
import glob
import argparse
from lxml import etree
import logging
import zipfile
from io import BytesIO
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

try:
    from xpaths_990 import XPATHS_990
    from xpaths_990ez import XPATHS_990EZ
    from xpaths_990pf import XPATHS_990PF
except ImportError as e:
    print(f"Error importing xpaths: {e}")
    raise

# Combine XPaths
XPATHS_BY_FORM = {
    "990": XPATHS_990,
    "990EZ": XPATHS_990EZ,
    "990PF": XPATHS_990PF
}

# Constants
DEBUG_EINS = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486"}
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
seen_eins = defaultdict(set)  # Track (EIN, tax_year) to deduplicate entries
xml_processed_count = 0  # Track total XMLs processed for sparse logging
log_counts = defaultdict(int)  # Dictionary to count log occurrences by preamble
total_entries = 0
tsv_write_counts = defaultdict(int)
done_queuing = False  # Flag to signal when main thread is done queuing tasks

# Queue for TSV writes
tsv_write_queue = queue.Queue()

# Setup queue-based logging
log_queue = queue.Queue(-1)  # Unlimited size
queue_handler = QueueHandler(log_queue)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
queue_handler.setFormatter(formatter)

file_handler = logging.FileHandler('extract_log.txt')
file_handler.setFormatter(formatter)
listener = QueueListener(log_queue, file_handler)
listener.start()

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
console_handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers = []  # Clear default handlers
logger.addHandler(queue_handler)
logger.addHandler(console_handler)

def log_error(msg_format, *args, ein=None, exc_info=False):
    global xml_processed_count, log_counts

    # Extract the preamble (everything up to the first '{')
    preamble_end = msg_format.find('{')
    if preamble_end == -1:
        preamble = msg_format
        data = ""
    else:
        preamble = msg_format[:preamble_end]
        data = msg_format[preamble_end:]

    # Skip verbose logs unless EIN is in DEBUG_EINS
    if ein and ein not in DEBUG_EINS:
        if any(x in preamble for x in ["Raw ", "Parsed ", "Extracted ", "Assigned tax_year"]):
            return

    # Skip thread-related and TSV creation logs unless verbose
    if any(x in preamble for x in ["Thread", "Opening TSV", "Wrote row", "Closed and flushed", "Created new TSV", "Periodic flush"]):
        if not verbose:
            # Format the message now since we need to log it
            message = msg_format.format(*args) if args else msg_format
            line_no = inspect.currentframe().f_back.f_lineno
            formatted_message = f"{line_no} {preamble}{message[len(preamble):]}"
            logging.info(formatted_message)
            return

    # Skip certain missing field logs for 990PF unless verbose
    if not verbose and "Missing" in preamble and any(field in msg_format for field in ["govt_grants", "contributions", "foreign_office"]) and "990PF" in msg_format:
        return

    # For non-DEBUG_EINS, log "Processing XML" and "Finished processing XML" only every 100,000th XML
    if ein and ein not in DEBUG_EINS:
        if "Processing XML" in preamble or "Finished processing XML" in preamble:
            xml_processed_count += 1
            if xml_processed_count % 100000 != 0:
                return

    # Increment the counter for this preamble
    log_counts[preamble] += 1

    # Limit to 5 logs per preamble unless EIN is in DEBUG_EINS
    if ein and ein not in DEBUG_EINS and log_counts[preamble] > 5:
        return

    # Now that we've decided to log, get the line number and format the message
    line_no = inspect.currentframe().f_back.f_lineno
    message = msg_format.format(*args) if args else msg_format
    formatted_message = f"{line_no} {preamble}{message[len(preamble):]}"

    # Log the message
    if exc_info:
        logging.error(formatted_message, exc_info=exc_info)
    else:
        logging.info(formatted_message)

    if verbose:
        print(f"Log: {formatted_message}")

def clean_org_type(org_type, for_filename=False):
    # First, preserve the original for logging
    original_org_type = org_type
    # Remove all parentheses and spaces
    org_type = org_type.replace('(', '').replace(')', '').replace(' ', '')
    
    # Handle specific cases
    if org_type == "501c3":
        cleaned = "501c3"
        formatted = "501(c)(3)"
    elif org_type == "4947a1":
        cleaned = "4947a1"
        formatted = "4947(a)(1)"
    elif org_type.startswith("501c"):
        # Extract the number after "501c"
        match = re.match(r'501c(\d+)', org_type)
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
        cleaned = org_type.lower()
        formatted = org_type.lower()

    # Return the appropriate format based on the for_filename flag
    return cleaned if for_filename else formatted
    
def tsv_writer_thread(tsv_files, writer_id):
    global done_queuing
    while True:
        try:
            # Get the next write task from the queue (non-blocking with timeout)
            tax_year, org_type, row, xml_filename = tsv_write_queue.get(timeout=60)
        except queue.Empty:
            # If the queue is empty for 60 seconds, check if main thread is done queuing
            if done_queuing and tsv_write_queue.empty():
                log_error("TSV writer thread {} exiting, queue empty and main thread done queuing", writer_id)
                break
            log_error("TSV writer thread {} waiting, queue size: {}", writer_id, tsv_write_queue.qsize())
            continue

        try:
            start_write_time = time.time()
            # Use the cleaned version for the filename, but keep the original org_type in the row
            org_type_filename = clean_org_type(org_type, for_filename=True)
            org_type_clean = clean_org_type(org_type, for_filename=False)
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
                log_error("Opened TSV {} in mode {}, total open files: {}", tsv_path, mode, len(tsv_files))
                tsv_write_counts[tsv_key] = 0

            writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
            writer.writerow(row)
            # Flush less frequently to reduce disk I/O
            tsv_write_counts[tsv_key] += 1
            if tsv_write_counts[tsv_key] % 5000 == 0:
                tsv_files[tsv_key].flush()
                log_error("Periodic flush after {} rows to TSV {} by writer {}", tsv_write_counts[tsv_key], tsv_path, writer_id)
                # Close the file if we've written a lot to free up file handles
                if tsv_write_counts[tsv_key] % 50000 == 0:
                    tsv_files[tsv_key].close()
                    del tsv_files[tsv_key]
                    log_error("Closed TSV {} to free resources, will reopen on next write", tsv_path)
            if tsv_write_counts[tsv_key] % 1000 == 0:
                write_duration = time.time() - start_write_time
                log_error("TSV writer {} wrote row to TSV {} for EIN {}, org_type {}, tax_year {}, XML {}, write took {:.2f}s, queue size: {}", 
                          writer_id, tsv_path, row[1], org_type, tax_year, xml_filename, write_duration, tsv_write_queue.qsize())
        except Exception as e:
            log_error("Error writing TSV row for {} by writer {}: {}", xml_filename, writer_id, str(e), exc_info=True)
            # Ensure the task is marked as done even if there's an error
            tsv_write_queue.task_done()
            continue  # Continue to the next task
        finally:
            tsv_write_queue.task_done()

def find_element(root, xpaths, namespaces):
    for xpath in xpaths:
        try:
            elem = root.xpath(xpath, namespaces=namespaces)
            if elem:
                return elem[0]
        except etree.XPathEvalError as e:
            xml_snippet = etree.tostring(root, encoding='unicode', method='xml')[:2000]
            log_error("XPath error for {}: {}. XML snippet: {}", xpath, e, xml_snippet, exc_info=True)
            non_ns_xpath = xpath.replace('irs:', '').replace('{http://www.irs.gov/efile}', '')
            try:
                elem = root.xpath(non_ns_xpath, namespaces=None)
                if elem:
                    return elem[0]
            except etree.XPathEvalError as e:
                log_error("Non-namespaced XPath error for {}: {}. XML snippet: {}", non_ns_xpath, e, xml_snippet, exc_info=True)
    return None

def parse_int(value):
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError, AttributeError):
        return 0

def parse_field_990(root, field, namespaces, xml_filename, context):
    if field not in XPATHS_BY_FORM["990"]:
        log_error("Field {} not defined in XPATHS_BY_FORM for form 990 in {}", field, xml_filename, ein=context.get('filer_ein'))
        return 0 if field not in ["org_type", "form_type", "filer_ein", "filer_name", "tax_year"] else "Unknown"

    if field == "officer_comp":
        total = 0
        for xpath in XPATHS_BY_FORM["990"]["officer_comp_elements"]:
            officer_elems = root.xpath(xpath, namespaces=namespaces)
            for person in officer_elems:
                comp_elem = find_element(person, XPATHS_BY_FORM["990"]["officer_comp_value"], namespaces)
                if comp_elem is not None:
                    comp = parse_int(comp_elem.text)
                    log_error("Raw officer_comp value: {} for EIN {} in {}", comp_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                    if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                        log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}", comp, context['total_exp'], xml_filename, ein=context.get('filer_ein'))
                        continue
                    total += comp
        log_error("Parsed officer_comp ${} for EIN {} in {}", total, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        return total

    if field == "grants_to_others":
        total = 0
        for xpath in XPATHS_BY_FORM["990"]["grant_elements_f"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990"]["grant_sub_elements_f"]:
                    for grant in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            log_error("Raw grant value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            total += amount
                            if context.get('filer_ein', 'Unknown') in DEBUG_EINS:
                                log_error("{} Grant: ${} in ScheduleF for EIN {}, File {}", 
                                          'CHAI' if context.get('filer_ein', 'Unknown') == '271414646' else 'Amnesty', amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            elif amount > 5_000_000 and log_counts["Found CashGrantAmt "] < 5:
                                log_error("Found CashGrantAmt ${} in ScheduleF for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        for xpath in XPATHS_BY_FORM["990"]["grant_elements_i"]:
            schedule_i = find_element(root, [xpath], namespaces)
            if schedule_i is not None:
                for sub_xpath in XPATHS_BY_FORM["990"]["grant_sub_elements_i"]:
                    for grant in schedule_i.xpath(sub_xpath, namespaces=namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            log_error("Raw grant value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            total += amount
                            if context.get('filer_ein', 'Unknown') in DEBUG_EINS:
                                log_error("{} Grant: ${} in ScheduleI for EIN {}, File {}", 
                                          'CHAI' if context.get('filer_ein', 'Unknown') == '271414646' else 'Amnesty', amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            elif amount > 5_000_000 and log_counts["Found CashGrantAmt "] < 5:
                                log_error("Found CashGrantAmt ${} in ScheduleI for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        if total > 5_000_000 or context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}", 
                      total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        elif total == 0 and context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
            log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}", 
                      context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein'))
        return total

    if field == "foreign_expenses":
        total = 0
        for xpath in XPATHS_BY_FORM["990"]["foreign_exp_elements"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990"]["foreign_exp_sub_elements"]:
                    for activity in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                        amount_elem = find_element(activity, XPATHS_BY_FORM["990"]["foreign_exp_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            log_error("Raw foreign_exp value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            total += amount
                            if context.get('filer_ein', 'Unknown') in DEBUG_EINS or (amount > 5_000_000 and log_counts["Found RegionTotalExpendituresAmt "] < 5):
                                log_error("Found RegionTotalExpendituresAmt ${} in ScheduleF for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        if total == 0 and context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
            log_error("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}", 
                      context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein'))
        return total

    if field == "org_type":
        elem = find_element(root, XPATHS_BY_FORM["990"]["org_type"], namespaces)
        if elem is not None:
            if elem.tag.endswith("Organization501cInd"):
                type_num = elem.get("organization501cTypeTxt")
                if type_num and type_num.isdigit() and 1 <= int(type_num) <= 29:
                    return f"501(c)({type_num})"
                elif elem.text and "X" in elem.text.upper():
                    return "501(c)(3)"
            elif elem.tag.endswith("Organization501c3Ind"):
                return "501(c)(3)"
            elif elem.tag.endswith("Organization4947a1NotPFInd"):
                return "4947(a)(1)"
        log_error("Failed to parse org_type for EIN {} in {}", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None else []
        log_error("Form type: {}, Available org_type tags: {} in {}", context.get('form_type', 'Unknown'), org_tags, xml_filename, ein=context.get('filer_ein'))
        return "Unknown"

    if field == "receipt":
        elem = find_element(root, XPATHS_BY_FORM["990"]["receipt"], namespaces)
        if elem is not None:
            value = parse_int(elem.text)
            log_error("Parsed receipt ${} for EIN {} in {}", value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
            return value
        log_error("Missing receipt for EIN {} in {}. Tried XPaths: {}", context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_BY_FORM['990']['receipt'], ein=context.get('filer_ein'))
        return 0

    elem = find_element(root, XPATHS_BY_FORM["990"][field], namespaces)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_BY_FORM['990'][field], ein=context.get('filer_ein'))
        if field in ["tax_year", "filer_ein", "filer_name", "form_type"]:
            return "Unknown"
        if field == "org_type":
            return "Unknown"
        if field == "foreign_office":
            return False
        return 0

    if field == "foreign_office":
        return elem.text.strip().upper() == 'X'

    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
    return value if field not in ["tax_year", "filer_ein", "filer_name", "form_type"] else elem.text.strip()

def parse_field_990EZ(root, field, namespaces, xml_filename, context):
    if field not in XPATHS_BY_FORM["990EZ"]:
        log_error("Field {} not defined in XPATHS_BY_FORM for form 990EZ in {}", field, xml_filename, ein=context.get('filer_ein'))
        return 0 if field not in ["org_type", "form_type", "filer_ein", "filer_name", "tax_year"] else "Unknown"

    if field in ["travel", "conferences"]:
        total = 0
        for xpath in XPATHS_BY_FORM["990EZ"][field]:
            schedule_o = find_element(root, [xpath], namespaces)
            if schedule_o is not None:
                desc = find_element(schedule_o, XPATHS_BY_FORM["990EZ"]["schedule_o_value"], namespaces)
                if desc is not None:
                    desc_text = desc.text.upper()
                    if field == "travel" and "TRAVEL" in desc_text:
                        match = re.search(r'\$(\d+\.\d{2}|\d+)', desc.text)
                        if match:
                            amount = int(float(match.group(1).replace('$', '')))
                            total += amount
                            log_error("Parsed travel_amt ${} from Schedule O in {}", amount, xml_filename, ein=context.get('filer_ein'))
                    if field == "conferences" and ("CONFERENCE" in desc_text or "MEETING" in desc_text):
                        match = re.search(r'\$(\d+\.\d{2}|\d+)', desc.text)
                        if match:
                            amount = int(float(match.group(1).replace('$', '')))
                            total += amount
                            log_error("Parsed conferences_amt ${} from Schedule O in {}", amount, xml_filename, ein=context.get('filer_ein'))
        return total

    if field == "officer_comp":
        total = 0
        for xpath in XPATHS_BY_FORM["990EZ"]["officer_comp_elements"]:
            officer_elems = root.xpath(xpath, namespaces=namespaces)
            if officer_elems:
                for person in officer_elems:
                    comp_elem = find_element(person, XPATHS_BY_FORM["990EZ"]["officer_comp_value"], namespaces)
                    if comp_elem is not None:
                        comp = parse_int(comp_elem.text)
                        log_error("Raw officer_comp value: {} for EIN {} in {}", comp_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                        if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                            log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}", comp, context['total_exp'], xml_filename, ein=context.get('filer_ein'))
                            continue
                        total += comp
            else:
                log_error("No officer elements found for EIN {} in {}. Tried XPaths: {}", context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_BY_FORM['990EZ']['officer_comp_elements'], ein=context.get('filer_ein'))
        log_error("Parsed officer_comp ${} for EIN {} in {}", total, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        return total

    if field == "grants_to_others":
        total = 0
        for xpath in XPATHS_BY_FORM["990EZ"]["grant_elements_o"]:
            schedule_o = find_element(root, [xpath], namespaces)
            if schedule_o is not None:
                desc = find_element(schedule_o, XPATHS_BY_FORM["990EZ"]["schedule_o_value"], namespaces)
                if desc is not None and "DISBURSEMENT" in desc.text.upper():
                    match = re.search(r'\$(\d+\.\d{2}|\d+)', desc.text)
                    if match:
                        amount = int(float(match.group(1).replace('$', '')))
                        total += amount
                        log_error("Parsed grants_to_others ${} from Schedule O DISBURSEMENT in {}", amount, xml_filename, ein=context.get('filer_ein'))
        for xpath in XPATHS_BY_FORM["990EZ"]["grant_elements_i"]:
            schedule_i = find_element(root, [xpath], namespaces)
            if schedule_i is not None:
                for sub_xpath in XPATHS_BY_FORM["990EZ"]["grant_sub_elements_i"]:
                    for grant in schedule_i.xpath(sub_xpath, namespaces=namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990EZ"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            log_error("Raw grant value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            total += amount
                            if context.get('filer_ein', 'Unknown') in DEBUG_EINS:
                                log_error("{} Grant: ${} in ScheduleI for EIN {}, File {}", 
                                          'CHAI' if context.get('filer_ein', 'Unknown') == '271414646' else 'Amnesty', amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            elif amount > 5_000_000 and log_counts["Found CashGrantAmt "] < 5:
                                log_error("Found CashGrantAmt ${} in ScheduleI for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        for xpath in XPATHS_BY_FORM["990EZ"]["grant_elements_f"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990EZ"]["grant_sub_elements_f"]:
                    for grant in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990EZ"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            log_error("Raw grant value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            total += amount
                            if context.get('filer_ein', 'Unknown') in DEBUG_EINS:
                                log_error("{} Grant: ${} in ScheduleF for EIN {}, File {}", 
                                          'CHAI' if context.get('filer_ein', 'Unknown') == '271414646' else 'Amnesty', amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            elif amount > 5_000_000 and log_counts["Found CashGrantAmt "] < 5:
                                log_error("Found CashGrantAmt ${} in ScheduleF for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        if total > 5_000_000 or context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}", 
                      total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        elif total == 0 and context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
            log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}", 
                      context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein'))
        return total

    if field == "foreign_expenses":
        total = 0
        for xpath in XPATHS_BY_FORM["990EZ"]["foreign_exp_elements"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990EZ"]["foreign_exp_sub_elements"]:
                    for activity in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                        amount_elem = find_element(activity, XPATHS_BY_FORM["990EZ"]["foreign_exp_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            log_error("Raw foreign_exp value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            total += amount
                            if context.get('filer_ein', 'Unknown') in DEBUG_EINS or (amount > 5_000_000 and log_counts["Found RegionTotalExpendituresAmt "] < 5):
                                log_error("Found RegionTotalExpendituresAmt ${} in ScheduleF for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        if total == 0 and context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
            log_error("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}", 
                      context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein'))
        return total

    if field == "total_assets":
        elem = find_element(root, XPATHS_BY_FORM["990EZ"]["total_assets"], namespaces)
        if elem is not None:
            value = parse_int(elem.text)
            log_error("Raw total_assets value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
            log_error("Parsed total_assets ${} for EIN {} in {}", value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
            return value
        log_error("Missing total_assets for EIN {} in {}. Tried XPaths: {}", context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_BY_FORM['990EZ']['total_assets'], ein=context.get('filer_ein'))
        return 0

    if field == "org_type":
        elem = find_element(root, XPATHS_BY_FORM["990EZ"]["org_type"], namespaces)
        if elem is not None:
            log_error("Found org_type element: tag={}, text={}, attrib={} for EIN {} in {}", elem.tag, elem.text, elem.attrib, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
            if elem.tag.endswith("Organization501cInd"):
                type_num = elem.get("organization501cTypeTxt")
                if type_num and type_num.isdigit() and 1 <= int(type_num) <= 29:
                    return f"501(c)({type_num})"
                elif elem.text and "X" in elem.text.upper():
                    return "501(c)(3)"
            elif any(elem.tag.endswith(suffix) for suffix in [
                "Organization501c3Ind", "Organization501c4Ind", "Organization501c5Ind",
                "Organization501c6Ind", "Organization501c7Ind", "Organization501c8Ind",
                "Organization501c9Ind", "Organization501c10Ind", "Organization501c19Ind",
                "Organization501c12Ind", "Organization501c15Ind", "Organization501c25Ind"
            ]):
                type_code = elem.tag.split('Organization')[1].replace('Ind', '')
                return f"501(c)({type_code[3:]})"
            elif elem.tag.endswith("TaxExemptStatus") or elem.tag.endswith("ExemptStatusCd"):
                if elem.text and "501(c)" in elem.text:
                    return elem.text
                elif elem.text and "4947(a)(1)" in elem.text:
                    return "4947(a)(1)"
            elif elem.tag.endswith("Organization4947a1NotPFInd"):
                return "4947(a)(1)"
        log_error("Failed to parse org_type for EIN {} in {}", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        all_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("No org_type tags found, defaulting to 501(c)(3). All ReturnData tags: {} in {}", all_tags, xml_filename, ein=context.get('filer_ein'))
        return "501(c)(3)"  # Default for 990EZ when no org_type tags are found

    elem = find_element(root, XPATHS_BY_FORM["990EZ"][field], namespaces)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_BY_FORM['990EZ'][field], ein=context.get('filer_ein'))
        if field in ["tax_year", "filer_ein", "filer_name", "form_type"]:
            return "Unknown"
        if field == "org_type":
            return "Unknown"
        if field == "foreign_office":
            return False
        return 0

    if field == "foreign_office":
        return elem.text.strip().upper() == 'X'

    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
    return value if field not in ["tax_year", "filer_ein", "filer_name", "form_type"] else elem.text.strip()

def parse_field_990PF(root, field, namespaces, xml_filename, context):
    if field not in XPATHS_BY_FORM["990PF"]:
        log_error("Field {} not defined in XPATHS_BY_FORM for form 990PF in {}", field, xml_filename, ein=context.get('filer_ein'))
        return 0 if field not in ["org_type", "form_type", "filer_ein", "filer_name", "tax_year"] else "Unknown"

    if field == "receipt":
        total = 0
        for xpath in XPATHS_BY_FORM["990PF"]["receipt"]:
            elem = find_element(root, [xpath], namespaces)
            if elem is not None:
                total += parse_int(elem.text)
                log_error("Raw receipt value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        if total == 0:
            missing_revenue_by_year[context.get("tax_year", "Unknown")] += 1
            log_error("Missing revenue fields in {}. Tried XPaths: {}", xml_filename, XPATHS_BY_FORM['990PF']['receipt'], ein=context.get('filer_ein'))
        return total

    if field in ["travel", "conferences"]:
        total = 0
        for xpath in XPATHS_BY_FORM["990PF"][field]:
            expense_elem = find_element(root, [xpath], namespaces)
            if expense_elem is not None:
                desc_elem = find_element(expense_elem, XPATHS_BY_FORM["990PF"]["expense_desc"], namespaces)
                amount_elem = find_element(expense_elem, XPATHS_BY_FORM["990PF"]["expense_value"], namespaces)
                if desc_elem is not None and amount_elem is not None:
                    amount = parse_int(amount_elem.text)
                    log_error("Raw {} value: {} for EIN {} in {}", field, amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                    desc_text = desc_elem.text.upper()
                    if field == "travel" and "TRAVEL" in desc_text:
                        total += amount
                        log_error("Parsed travel_amt ${} from OtherExpensesSchedule in {}", total, xml_filename, ein=context.get('filer_ein'))
                    if field == "conferences" and ("CONFERENCE" in desc_text or "MEETING" in desc_text):
                        total += amount
                        log_error("Parsed conferences_amt ${} from OtherExpensesSchedule in {}", total, xml_filename, ein=context.get('filer_ein'))
        return total

    if field == "officer_comp":
        total = 0
        for xpath in XPATHS_BY_FORM["990PF"]["officer_comp_elements"]:
            officer_elems = root.xpath(xpath, namespaces=namespaces)
            for person in officer_elems:
                comp_elem = find_element(person, XPATHS_BY_FORM["990PF"]["officer_comp_value"], namespaces)
                if comp_elem is not None:
                    comp = parse_int(comp_elem.text)
                    log_error("Raw officer_comp value: {} for EIN {} in {}", comp_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                    if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                        log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}", comp, context['total_exp'], xml_filename, ein=context.get('filer_ein'))
                        continue
                    total += comp
        log_error("Parsed officer_comp ${} for EIN {} in {}", total, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        return total

    if field == "grants_to_others":
        total = 0
        elem = find_element(root, XPATHS_BY_FORM["990PF"]["grants_to_others"], namespaces)
        if elem is not None:
            total += parse_int(elem.text)
            log_error("Raw grants_to_others value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
            log_error("Parsed grants_to_others ${} from 990PF in {}", total, xml_filename, ein=context.get('filer_ein'))
        if total > 5_000_000 or context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}", 
                      total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        elif total == 0 and context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
            log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}", 
                      context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein'))
        return total

    if field == "foreign_expenses":
        total = 0
        for xpath in XPATHS_BY_FORM["990PF"]["foreign_expenses"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990"]["foreign_exp_sub_elements"]:
                    for activity in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                        amount_elem = find_element(activity, XPATHS_BY_FORM["990"]["foreign_exp_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            log_error("Raw foreign_exp value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
                            total += amount
                            if context.get('filer_ein', 'Unknown') in DEBUG_EINS or (amount > 5_000_000 and log_counts["Found RegionTotalExpendituresAmt "] < 5):
                                log_error("Found RegionTotalExpendituresAmt ${} in ScheduleF for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        if total == 0 and context.get('filer_ein', 'Unknown') in DEBUG_EINS:
            return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
            log_error("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}", 
                      context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein'))
        return total

    if field == "total_assets":
        elem = find_element(root, XPATHS_BY_FORM["990PF"]["total_assets"], namespaces)
        if elem is not None:
            value = parse_int(elem.text)
            log_error("Raw total_assets value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
            log_error("Parsed total_assets ${} for EIN {} in {}", value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
            return value
        log_error("Missing total_assets for EIN {} in {}. Tried XPaths: {}", context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_BY_FORM['990PF']['total_assets'], ein=context.get('filer_ein'))
        return 0

    if field == "org_type":
        elem = find_element(root, XPATHS_BY_FORM["990PF"]["org_type"], namespaces)
        if elem is not None:
            if elem.tag.endswith("Organization501c3ExemptPFInd") or elem.tag.endswith("Organization501c3TaxablePFInd"):
                return "501(c)(3)"
            elif any(tag in elem.tag for tag in ["Organization4947a1NotExemptCharitableTrustInd", "Organization4947a1Ind", "Organization4947a1TrtdPFInd"]):
                return "4947(a)(1)"
        log_error("Failed to parse org_type for EIN {} in {}", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None else []
        log_error("Form type: {}, Available org_type tags: {} in {}", context.get('form_type', 'Unknown'), org_tags, xml_filename, ein=context.get('filer_ein'))
        return "Unknown"

    elem = find_element(root, XPATHS_BY_FORM["990PF"][field], namespaces)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_BY_FORM['990PF'][field], ein=context.get('filer_ein'))
        if field in ["tax_year", "filer_ein", "filer_name", "form_type"]:
            return "Unknown"
        if field == "org_type":
            return "Unknown"
        if field == "foreign_office":
            return False
        return 0

    if field == "foreign_office":
        return elem.text.strip().upper() == 'X'

    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein'))
    return value if field not in ["tax_year", "filer_ein", "filer_name", "form_type"] else elem.text.strip()

PARSE_FIELD_METHODS = {
    "990": parse_field_990,
    "990EZ": parse_field_990EZ,
    "990PF": parse_field_990PF
}

def parse_field(root, field, form_type, namespaces, xml_filename, context):
    if form_type not in PARSE_FIELD_METHODS:
        log_error("Invalid form_type {} for field {} in {}", form_type, field, xml_filename, ein=context.get('filer_ein'))
        return "Unknown" if field in ["tax_year", "filer_ein", "filer_name", "form_type"] else 0
    return PARSE_FIELD_METHODS[form_type](root, field, namespaces, xml_filename, context)

def parse_xml_file(xml_content, xml_filename, zip_prefix):
    if not hasattr(file_counter_local, 'value'):
        file_counter_local.value = 0
        file_counter_local.entries = 0
        file_counter_local.skipped = 0

    start_time = time.time()
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
    except etree.ParseError as e:
        log_error("Parse error in XML file {}: {}", xml_filename, e, exc_info=True)
        file_counter_local.skipped += 1
        return []

    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    log_error("Processing XML: {}, thread {}", xml_filename, threading.get_ident(), ein=context.get('filer_ein') if 'context' in locals() else None)

    try:
        context = {}
        filer_ein_elem = find_element(root, XPATHS_BY_FORM["990"]["filer_ein"], namespaces)
        context["filer_ein"] = filer_ein_elem.text if filer_ein_elem is not None else "Unknown"
        log_error("Extracted filer_ein: {} for {}", context['filer_ein'], xml_filename, ein=context.get('filer_ein'))

        form_type_elem = find_element(root, XPATHS_BY_FORM["990"]["form_type"], namespaces)
        context["form_type"] = form_type_elem.text if form_type_elem is not None else "Unknown"
        if context["filer_ein"] == "680005486" and context["form_type"] not in PARSE_FIELD_METHODS:
            context["form_type"] = "990EZ"
            log_error("Forced form_type '990EZ' for EIN {} in {}", context['filer_ein'], xml_filename, ein=context.get('filer_ein'))
        log_error("Extracted form_type: {} for {}", context['form_type'], xml_filename, ein=context.get('filer_ein'))

        if context["form_type"] not in PARSE_FIELD_METHODS:
            xml_snippet = etree.tostring(root, encoding='unicode', method='xml')[:2000]
            log_error("Skipping {} due to invalid form_type: {}. XML snippet: {}", xml_filename, context['form_type'], xml_snippet, ein=context.get('filer_ein'))
            file_counter_local.skipped += 1
            return []

        if context["form_type"] == "990T":
            log_error("Skipping {} due to form_type 990T", xml_filename, ein=context.get('filer_ein'))
            file_counter_local.skipped += 1
            return []

        tax_year_elem = find_element(root, XPATHS_BY_FORM[context["form_type"]]["tax_year"], namespaces)
        context["tax_year"] = tax_year_elem.text if tax_year_elem is not None else "Unknown"
        log_error("Extracted tax_year: {} for {}", context['tax_year'], xml_filename, ein=context.get('filer_ein'))

        if context["tax_year"] == "Unknown":
            missing_taxyr_by_year["Unknown"] += 1
            return_header = find_element(root, [".//irs:ReturnHeader", ".//ReturnHeader"], namespaces)
            header_snippet = etree.tostring(return_header, encoding='unicode', method='xml')[:2000] if return_header is not None else "No ReturnHeader"
            log_error("Missing TaxYr element in {}, inferring from filename/zip. ReturnHeader: {}", xml_filename, header_snippet, ein=context.get('filer_ein'))
            context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix
        else:
            try:
                int(context["tax_year"])
            except ValueError:
                invalid_taxyr_by_year[context["tax_year"]] += 1
                log_error("Invalid tax year {} in {}, inferring from filename/zip", context['tax_year'], xml_filename, ein=context.get('filer_ein'))
                context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix

        filer_name_elem = find_element(root, XPATHS_BY_FORM[context["form_type"]]["filer_name"], namespaces)
        context["filer_name"] = filer_name_elem.text if filer_name_elem is not None else "Unknown"
        log_error("Extracted filer_name: {} for {}", context['filer_name'], xml_filename, ein=context.get('filer_ein'))

        if context["filer_ein"] == "Unknown":
            missing_filer_by_year[context["tax_year"]] += 1
            log_error("Missing Filer EIN in {}", xml_filename, ein=context.get('filer_ein'))
            file_counter_local.skipped += 1
            return []

        file_counter_local.value += 1
        fields = [
            "receipt", "govt_grants", "contributions", "total_exp", "prog_exp",
            "travel", "conferences", "officer_comp", "grants_to_others", "foreign_expenses",
            "total_assets", "org_type", "foreign_office"
        ]
        data = {field: parse_field(root, field, context["form_type"], namespaces, xml_filename, context) for field in fields}

        def calculate_percentage(value, denom):
            if denom == 0 or value is None or denom is None:
                return 0.0
            return round((value / denom) * 100, 2)

        data["comp_pct"] = calculate_percentage(data["officer_comp"], data["total_exp"])
        data["travel_pct"] = calculate_percentage(data["travel"], data["total_exp"])
        data["conferences_pct"] = calculate_percentage(data["conferences"], data["total_exp"])
        data["grants_pct"] = calculate_percentage(data["grants_to_others"], data["total_exp"])
        data["foreign_expenses_pct"] = calculate_percentage(data["foreign_expenses"], data["total_exp"])
        data["grift_ratio"] = calculate_percentage(data["officer_comp"] + data["travel"] + data["conferences"], data["total_exp"])

        if data["grift_ratio"] > 100 and data["total_exp"] > 0:
            log_error("Suspicious grift_ratio {}% for EIN {} in {}", data['grift_ratio'], context['filer_ein'], xml_filename, ein=context.get('filer_ein'))

        data["denominator"] = data["total_assets"] + data["receipt"]
        data["comp_ptile"] = "n/y"
        data["travel_ptile"] = "n/y"
        data["conferences_ptile"] = "n/y"
        data["grants_ptile"] = "n/y"
        data["foreign_expenses_ptile"] = "n/y"
        data["domestic_misrep_flag"] = data["grift_ratio"] > 10 and data["foreign_expenses_pct"] < 0.1 * 100 if data["total_exp"] > 0 else False

        results = []
        # Deduplicate entries: only process if this (EIN, tax_year) hasn't been seen
        ein_tax_year_key = (context["filer_ein"], context["tax_year"])
        if ein_tax_year_key in seen_eins:
            log_error("Skipping duplicate entry for EIN {}, tax_year {} in {}", context['filer_ein'], context['tax_year'], xml_filename, ein=context.get('filer_ein'))
            file_counter_local.skipped += 1
            return []
        seen_eins[ein_tax_year_key].add(xml_filename)

        try:
            row = [
                context["tax_year"], context["filer_ein"], context["filer_name"], data["receipt"], data["govt_grants"],
                data["contributions"], data["org_type"], data["total_exp"], data["prog_exp"], data["travel"],
                data["conferences"], data["officer_comp"], data["comp_pct"], data["comp_ptile"], data["travel_pct"],
                data["travel_ptile"], data["conferences_pct"], data["conferences_ptile"], data["grants_pct"],
                data["grants_ptile"], data["foreign_expenses_pct"], data["foreign_expenses_ptile"], data["grift_ratio"],
                data["total_assets"], context["form_type"], data["denominator"], data["foreign_office"],
                data["foreign_expenses"], data["grants_to_others"], data["domestic_misrep_flag"], xml_filename
            ]
            results.append(row)
            file_counter_local.entries += 1
            if (file_counter_local.value % 10000) == 0:
                log_error("Thread {} processed {} files, {} entries, {} skipped", threading.get_ident(), file_counter_local.value, file_counter_local.entries, file_counter_local.skipped)
        except Exception as e:
            log_error("Failed to append result for {}, EIN {}: {}", xml_filename, context.get('filer_ein', 'Unknown'), e, exc_info=True, ein=context.get('filer_ein'))
            return []
        log_error("Finished processing XML {}, took {:.2f} seconds, memory usage: {}%", xml_filename, time.time() - start_time, psutil.virtual_memory().percent, ein=context.get('filer_ein'))
        return results, data["org_type"], xml_filename

    except Exception as e:
        log_error("Error processing {}: {}", xml_filename, e, exc_info=True, ein=context.get('filer_ein'))
        file_counter_local.skipped += 1
        return []

def process_zip_files(start_year, end_year, max_workers=4):
    global total_xml_files, pending_tasks, total_entries, done_queuing
    global missing_taxyr_by_year, invalid_taxyr_by_year, missing_filer_by_year
    global missing_revenue_by_year, invalid_predicate_by_year
    start_time = time.time()
    year_prefixes = [str(y) for y in range(int(start_year), int(end_year) + 1)]
    tsv_files = {}
    total_xml_files = 0
    total_processed_files = 0
    total_entries = 0
    pending_tasks = 0

    # Start a single TSV writer thread to avoid disk I/O contention
    writer_thread = threading.Thread(target=tsv_writer_thread, args=(tsv_files, "writer-0"))
    writer_thread.daemon = True
    writer_thread.start()

    # First pass: count total XML files for the progress bar
    all_zip_files = []
    for year_prefix in year_prefixes:
        zip_pattern = f"{year_prefix}*.zip"
        zip_files = glob.glob(zip_pattern)
        log_error("Found {} ZIP files for pattern {}", len(zip_files), zip_pattern)
        all_zip_files.extend(zip_files)
    total_zip_files = len(all_zip_files)

    for zip_file in all_zip_files:
        log_error("Found ZIP: {}", zip_file)
        try:
            with zipfile.ZipFile(zip_file, 'r') as zf:
                xml_files = [f for f in zf.namelist() if f.lower().endswith('.xml')]
                total_xml_files += len(xml_files)
        except (NotImplementedError, zipfile.BadZipFile) as e:
            log_error("Error counting XMLs in {}: {}", zip_file, e, exc_info=True)
            continue

    log_error("Total XML files to process: {}", total_xml_files)

    zip_counter = 0
    try:
        with tqdm(total=total_xml_files, desc="Processing XMLs", unit="XML") as pbar:
            for year_prefix in year_prefixes:
                zip_pattern = f"{year_prefix}*.zip"
                zip_files = glob.glob(zip_pattern)
                if not zip_files:
                    print(f"No ZIP files found for pattern: {zip_pattern}")
                    log_error("No ZIP files found for pattern: {}", zip_pattern)
                    continue

                for zip_file_path in zip_files:
                    zip_counter += 1
                    print(f"\nProcessing ZIP {zip_counter}/{total_zip_files}: {os.path.basename(zip_file_path)}")
                    log_error("Processing ZIP: {}, Memory usage: {}%", zip_file_path, psutil.virtual_memory().percent)
                    try:
                        with closing(zipfile.ZipFile(zip_file_path, 'r')) as zf:
                            xml_files = [f for f in zf.namelist() if f.lower().endswith('.xml')]
                            total_xmls_in_zip = len(xml_files)
                            log_error("Found {} XML files in {}", total_xmls_in_zip, zip_file_path)

                            # Process XMLs in batches
                            batch_size = 100000  # Updated batch size
                            max_batches = (total_xmls_in_zip + batch_size - 1) // batch_size  # Ceiling division
                            processed_xmls = 0
                            batch_count = 0
                            for batch_start in range(0, total_xmls_in_zip, batch_size):
                                batch_count += 1
                                batch = xml_files[batch_start:batch_start + batch_size]
                                batch_size_actual = len(batch)
                                processed_xmls += batch_size_actual
                                if processed_xmls > total_xmls_in_zip:
                                    log_error("Error: Processed {} XMLs, exceeding total {} in {}", processed_xmls, total_xmls_in_zip, zip_file_path)
                                    break
                                # Print batch updates only every 5th batch (every 500,000 XMLs)
                                if batch_count % 5 == 1:
                                    print(f"Reading batch {batch_start//batch_size + 1}/{max_batches} ({batch_size_actual} XMLs)")
                                xml_contents = []
                                for xml_file in batch:
                                    try:
                                        with zf.open(xml_file) as file_obj:
                                            xml_content = file_obj.read()
                                            file_size = len(xml_content)
                                            xml_contents.append((xml_content, xml_file))
                                    except Exception as e:
                                        log_error("Failed to read {} from {}: {}", xml_file, zip_file_path, e, exc_info=True)
                                        continue
                                log_error("Read batch {}/{} of {} XMLs, total processed {}/{}, memory usage: {}%", 
                                          batch_start//batch_size + 1, max_batches, len(xml_contents), processed_xmls, total_xmls_in_zip, psutil.virtual_memory().percent)

                                with ThreadPoolExecutor(max_workers=2) as executor:
                                    futures = {}
                                    for xml_content, xml_file in xml_contents:
                                        future = executor.submit(parse_xml_file, xml_content, xml_file, year_prefix)
                                        futures[future] = (zip_file_path, xml_file)
                                        pending_tasks += 1
                                        if pending_tasks % 1000 == 0:
                                            log_error("Submitted {} tasks, active threads: {}", pending_tasks, threading.active_count())

                                    chunk_results = []
                                    completed_tasks = 0
                                    total_tasks = len(futures)
                                    for i, future in enumerate(as_completed(futures, timeout=300)):
                                        zip_file, xml_file = futures[future]
                                        try:
                                            completed_tasks += 1
                                            pending_tasks -= 1
                                            if completed_tasks % 1000 == 0:
                                                log_error("Completed {}/{} tasks in batch {}/{}, pending: {}, active threads: {}", 
                                                          completed_tasks, total_tasks, batch_start//batch_size + 1, max_batches, pending_tasks, threading.active_count())
                                            result = future.result(timeout=300)
                                            if result:
                                                chunk_results.append(result)
                                                results, org_type, xml_filename = result
                                                for res in results:
                                                    tax_year = int(res[0])
                                                    org_type = res[6]
                                                    # Queue the write task
                                                    tsv_write_queue.put((tax_year, org_type, res, xml_filename))
                                                    total_entries += 1
                                                    if total_entries % 1000 == 0:
                                                        log_error("Processed {} entries, memory usage: {}%, TSV queue size: {}", 
                                                                  total_entries, psutil.virtual_memory().percent, tsv_write_queue.qsize())
                                        except TimeoutError:
                                            log_error("Timeout processing {} in {}: Task took longer than 300 seconds", xml_file, zip_file)
                                            pending_tasks -= 1
                                            total_processed_files += 1
                                            continue
                                        except Exception as e:
                                            log_error("Error processing {} in {}: {}", xml_file, zip_file, e, exc_info=True)
                                            pending_tasks -= 1
                                        total_processed_files += 1
                                        pbar.update(1)
                                        if (i + 1) % 1000 == 0:
                                            del chunk_results[:]
                                            log_error("Cleared chunk_results to free memory, processed {} XMLs in batch {}/{}", 
                                                      i + 1, batch_start//batch_size + 1, max_batches)

                                    del chunk_results
                                # Print batch completion only every 5th batch
                                if batch_count % 5 == 0:
                                    print(f"Finished batch {batch_start//batch_size + 1}/{max_batches}, total processed {processed_xmls}/{total_xmls_in_zip} XMLs in {os.path.basename(zip_file_path)}")
                                log_error("Finished batch {}/{}, total processed {}/{} XMLs in {}", 
                                          batch_start//batch_size + 1, max_batches, processed_xmls, total_xmls_in_zip, zip_file_path)

                    except (NotImplementedError, zipfile.BadZipFile) as e:
                        log_error("Error processing {}: {}", zip_file_path, e, exc_info=True)
                        continue

    finally:
        # Signal that we're done queuing tasks
        done_queuing = True
        # Custom wait for the queue to empty with a timeout
        timeout = 600  # 10 minutes
        start_wait_time = time.time()
        while not tsv_write_queue.empty() and (time.time() - start_wait_time) < timeout:
            log_error("Waiting for TSV write queue to empty, remaining tasks: {}", tsv_write_queue.qsize())
            time.sleep(10)  # Check more frequently to catch issues earlier
        if not tsv_write_queue.empty():
            log_error("Timeout waiting for TSV write queue to empty after {} seconds, remaining tasks: {}", timeout, tsv_write_queue.qsize())
        else:
            log_error("TSV write queue emptied successfully")
        tsv_write_queue.join()  # Final join to ensure all tasks are done
        # Close all TSV files at the end
        for (tax_year, org_type), file_handle in list(tsv_files.items()):
            log_error("Closing TSV for org_type {}, tax_year {}, size {} bytes", org_type, tax_year, file_handle.tell())
            file_handle.flush()
            file_handle.close()
        tsv_files.clear()
        # Ensure writer thread has finished
        writer_thread.join()
        # Stop the logging listener
        listener.stop()

    print(f"\nTotal XML files across all ZIPs: {total_xml_files}")
    print(f"Total files processed: {total_processed_files}")
    print(f"Total entries: {total_entries}")
    print(f"Approximately {int(total_processed_files / max_workers)} files per worker")

    print("\nError Summary per Year:")
    print("| Tax Year | Missing TaxYr | Invalid TaxYr | Missing Filer | Missing Revenue | Invalid Predicate |")
    print("|----------|---------------|---------------|---------------|-----------------|-------------------|")
    all_years = set(missing_taxyr_by_year.keys()) | set(invalid_taxyr_by_year.keys()) | \
                set(missing_filer_by_year.keys()) | set(missing_revenue_by_year.keys()) | \
                set(invalid_predicate_by_year.keys())
    for tax_year in sorted(all_years):
        print(f"| {tax_year:<8} | {missing_taxyr_by_year[tax_year]:>13} | {invalid_taxyr_by_year[tax_year]:>13} | "
              f"{missing_filer_by_year[tax_year]:>13} | {missing_revenue_by_year[tax_year]:>15} | "
              f"{invalid_predicate_by_year[tax_year]:>17} |")

    end_time = time.time()
    print(f"Total processing time: {(end_time - start_time):.2f} seconds ({(end_time - start_time) / 60:.2f} minutes)")

def valid_tax_year(year):
    try:
        year = int(year)
        if 2016 <= year <= 2025:
            return year
        else:
            raise argparse.ArgumentTypeError("Tax year must be between 2016 and 2025 (inclusive).")
    except ValueError:
        raise argparse.ArgumentTypeError("Tax year must be an integer.")

if __name__ == "__main__":
    print("Starting extract_charities.py")
    log_error("Starting extract_charities.py")
    try:
        parser = argparse.ArgumentParser(description="Extract charity data from IRS XMLs to TSVs by org type and year.")
        parser.add_argument("start_year", type=valid_tax_year, help="Start year for ZIP prefixes (e.g., 2016).")
        parser.add_argument("end_year", type=valid_tax_year, help="End year for ZIP prefixes (e.g., 2024).")
        parser.add_argument("--verbose", action="store_true", help="Enable verbose logging for thread progress and operations.")
        parser.add_argument("--eins", type=str, help="Comma-separated EINs to debug (optional).")

        args = parser.parse_args()
        if args.start_year > args.end_year:
            raise argparse.ArgumentError(None, "Start year must be less than or equal to end year.")
        verbose = args.verbose
        if args.eins:
            DEBUG_EINS = set(args.eins.split(','))

        process_zip_files(str(args.start_year), str(args.end_year), max_workers=4)
    except Exception as e:
        log_error("Script failed to start: {}", e, exc_info=True)
        raise