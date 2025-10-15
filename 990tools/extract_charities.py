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
import sqlite3
from parse_utils import ORG_TYPE_PATTERN
from extract_utils import get_colocator_for_address, perform_batch_geocoding
from zip_xml_db import ZipFileManager, XmlFileManager, IndexingManager

# Import parsing functions from new scripts
import parse_990
import parse_990ez
import parse_990pf
from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF
import extract_utils as cu

# Constants
DEBUG_EINS = set()
DB_PATH = None  # Will be set from args
TSV_COLUMNS = [
    "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
    "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "comp_ptile",
    "travel_pct", "travel_ptile", "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
    "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio", "total_assets", "form_type",
    "denominator", "foreign_office", "foreign_expenses", "grants_to_others", "domestic_misrep_flag", "xml_name",
    "canonical_address", "mailing_zip", "colocator"
]
OFFICER_MAPPING_FILE = "officer_mapping.json"
CONTRACTORS_FILE = "contractors.tsv"
POLITICAL_CONTRIBUTIONS_FILE = "political_contributions.tsv"
QUEUE_SIZE = 20000
BATCH_SIZE = 1000  # Database batch size

# Precompile XPaths used in parse_xml_file
FORM_TYPE_XPATHS = [
    etree.XPath(".//*[local-name()='ReturnHeader']/*[local-name()='ReturnTypeCd']"),
    etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//ReturnHeader/ReturnTypeCd")
]
TAX_YEAR_XPATHS = [
    etree.XPath(".//*[local-name()='ReturnHeader']/*[local-name()='TaxYr']"),
    etree.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//ReturnHeader/TaxYr")
]
FILER_EIN_XPATHS = [
    etree.XPath(".//*[local-name()='Filer']/*[local-name()='EIN']"),
    etree.XPath(".//irs:Filer/irs:EIN", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//Filer/EIN")
]
FILER_NAME_XPATHS = [
    etree.XPath(".//*[local-name()='Filer']/*[local-name()='BusinessName']/*[local-name()='BusinessNameLine1Txt']"),
    etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//Filer/BusinessName/BusinessNameLine1Txt")
]
ADDRESS_XPATHS = [
    etree.XPath(".//*[local-name()='Filer']/*[local-name()='USAddress']/*"),
    etree.XPath(".//irs:Filer/irs:USAddress/*", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//Filer/USAddress/*"),
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

# EIN to XML index
ein_xml_index = defaultdict(list)

# Queue for database writes with increased size
db_write_queue = queue.Queue(maxsize=20000)
officer_queue = queue.Queue(maxsize=QUEUE_SIZE)
contractors_queue = queue.Queue(maxsize=QUEUE_SIZE)
political_queue = queue.Queue(maxsize=QUEUE_SIZE)

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
parse_990.set_logger(logger, log_error, verbose, DEBUG_EINS)
parse_990ez.set_logger(logger, log_error, verbose, DEBUG_EINS)
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

def db_writer_thread(writer_id):
    global done_queuing, DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA foreign_keys = ON')
    batch_buffer = []

    while True:
        try:
            # Get the next write task from the queue with a short timeout
            item = db_write_queue.get(timeout=0.1)
            if item is None:  # Sentinel value to indicate shutdown
                log_error("DB writer thread {} received shutdown signal", writer_id)
                # Flush any remaining batch
                if batch_buffer:
                    _execute_batch_insert(conn, batch_buffer)
                    batch_buffer = []
                conn.commit()
                break
            batch_buffer.append(item)
        except queue.Empty:
            # If the queue is empty, check if main thread is done queuing
            if done_queuing and db_write_queue.empty():
                log_error("DB writer thread {} exiting, queue empty and main thread done queuing", writer_id)
                # Flush any remaining batch
                if batch_buffer:
                    _execute_batch_insert(conn, batch_buffer)
                    batch_buffer = []
                conn.commit()
                break
            continue

        try:
            # Check if batch is full
            if len(batch_buffer) >= BATCH_SIZE:
                _execute_batch_insert(conn, batch_buffer)
                batch_buffer = []
                conn.commit()

        except Exception as e:
            log_error("Error processing DB batch by writer {}: {}", writer_id, str(e), exc_info=True)
        finally:
            db_write_queue.task_done()

    conn.close()

def _execute_batch_insert(conn, batch):
    """Execute batch insert for charities data."""
    if not batch:
        return

    # Prepare data for batch insert
    charities_data = []
    addresses_data = []
    grants_data = []
    officers_data = []
    contractors_data = []
    political_data = []

    for item in batch:
        charity_data, address_data, grant_entries, officer_entries, contractor_entries, political_entries = item

        # Charities table
        charities_data.append((
            charity_data['ein'], charity_data['tax_year'], charity_data['filer_name'],
            charity_data['receipt_amt'], charity_data['govt_amt'], charity_data['contrib_amt'],
            charity_data['org_type'], charity_data['total_exp'], charity_data['prog_exp'],
            charity_data['travel_amt'], charity_data['conferences_amt'], charity_data['officer_comp'],
            charity_data['comp_pct'], charity_data['comp_ptile'], charity_data['travel_pct'],
            charity_data['travel_ptile'], charity_data['conferences_pct'], charity_data['conferences_ptile'],
            charity_data['grants_pct'], charity_data['grants_ptile'], charity_data['foreign_expenses_pct'],
            charity_data['foreign_expenses_ptile'], charity_data['grift_ratio'], charity_data['total_assets'],
            charity_data['form_type'], charity_data['denominator'], charity_data['foreign_office'],
            charity_data['foreign_expenses'], charity_data['grants_to_others'], charity_data['domestic_misrep_flag'],
            charity_data['xml_name'], charity_data['grift']
        ))

        # Addresses table
        if address_data:
            addresses_data.append((
                address_data['ein'], address_data['name'], address_data['canonical_address'],
                address_data['po_box'], address_data['zip_code'], address_data['address_type']
            ))

        # Grants table
        for grant in grant_entries:
            grants_data.append((
                grant['filer_ein'], grant['filer_name'], grant['grant_ein'],
                grant['grant_amt'], grant['tax_year'], grant['filer_colocator'], grant['grantee_colocator']
            ))

        # Officers table
        for officer in officer_entries:
            officers_data.append((
                officer['charity_id'], officer['first_name'], officer['last_name'],
                officer['compensation'], officer['tax_year']
            ))

        # Contractors table
        for contractor in contractor_entries:
            contractors_data.append((
                contractor['filer_ein'], contractor['name'], contractor['amount'],
                contractor['ein'], contractor['address'], contractor['zip_code'],
                contractor['po_box'], contractor['tax_year']
            ))

        # PoliticalContributions table
        for political in political_entries:
            political_data.append((
                political['filer_ein'], political['recipient'], political['amount'],
                political['recipient_address'], political['recipient_zip'], political['recipient_po_box'],
                political['tax_year']
            ))

    # Execute batch inserts
    try:
        if charities_data:
            conn.executemany('''
                INSERT OR REPLACE INTO Charities (
                    ein, tax_year, filer_name, receipt_amt, govt_amt, contrib_amt, org_type,
                    total_exp, prog_exp, travel_amt, conferences_amt, officer_comp, comp_pct, comp_ptile,
                    travel_pct, travel_ptile, conferences_pct, conferences_ptile, grants_pct, grants_ptile,
                    foreign_expenses_pct, foreign_expenses_ptile, grift_ratio, total_assets, form_type,
                    denominator, foreign_office, foreign_expenses, grants_to_others, domestic_misrep_flag,
                    xml_name, grift
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', charities_data)

        if addresses_data:
            conn.executemany('''
                INSERT OR REPLACE INTO Addresses (
                    ein, name, canonical_address, po_box, zip_code, address_type
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', addresses_data)

        if grants_data:
            conn.executemany('''
                INSERT INTO Grants (
                    filer_ein, filer_name, grant_ein, grant_amt, tax_year, filer_colocator, grantee_colocator
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', grants_data)

        if officers_data:
            conn.executemany('''
                INSERT INTO Officers (
                    charity_id, first_name, last_name, compensation, tax_year
                ) VALUES (?, ?, ?, ?, ?)
            ''', officers_data)

        if contractors_data:
            conn.executemany('''
                INSERT INTO Contractors (
                    filer_ein, name, amount, ein, address, zip_code, po_box, tax_year
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', contractors_data)

        if political_data:
            conn.executemany('''
                INSERT INTO PoliticalContributions (
                    filer_ein, recipient, amount, recipient_address, recipient_zip, recipient_po_box, tax_year
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', political_data)

    except Exception as e:
        log_error("Error in batch insert: {}", str(e), exc_info=True)
        raise

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
        with open(os.path.join(args.output_dir, OFFICER_MAPPING_FILE), "w", encoding="utf-8") as f:
            json.dump(result, f, indent=4)
        log_error("Wrote officer mapping to {}", OFFICER_MAPPING_FILE)
    except Exception as e:
        log_error("Error writing officer mapping file {}: {}", OFFICER_MAPPING_FILE, str(e), exc_info=True)

def contractors_writer_thread(writer_id):
    """Write contractors data to TSV file."""
    global done_queuing, contractors_queue
    contractors_data = []

    while True:
        item = None
        try:
            item = contractors_queue.get(timeout=0.1)
            if item is None:
                log_error("Contractors writer thread {} received shutdown signal", writer_id)
                break
            contractors_data.extend(item)
        except queue.Empty:
            if done_queuing and contractors_queue.empty():
                log_error("Contractors writer thread {} exiting, queue empty and main thread done queuing", writer_id)
                break
            continue
        finally:
            if item is not None:
                contractors_queue.task_done()

    # Write contractors data
    if contractors_data:
        try:
            contractors_path = os.path.join(args.output_dir, CONTRACTORS_FILE)
            log_error("Writing contractors header and {} records to {}", len(contractors_data), CONTRACTORS_FILE)
            if contractors_data:
                log_error("Sample contractor data: {}", contractors_data[0])
            with open(contractors_path, 'w', encoding='utf-8', newline='') as f:
                f.write('filer_ein\tname\tamount\tein\taddress\tzip_code\tpo_box\ttax_year\n')
                writer = csv.writer(f, delimiter='\t')
                for contractor in contractors_data:
                    row = [
                        contractor.get('filer_ein', ''),
                        contractor.get('name', ''),
                        contractor.get('amount', 0),
                        contractor.get('ein', ''),
                        contractor.get('address', ''),
                        contractor.get('zip_code', ''),
                        contractor.get('po_box', ''),
                        contractor.get('tax_year', '')
                    ]
                    writer.writerow(row)
                    if verbose:
                        log_error("Wrote contractor row: {}", row)
            log_error("Wrote {} contractor records to {}", len(contractors_data), CONTRACTORS_FILE)
        except Exception as e:
            log_error("Error writing contractors file {}: {}", CONTRACTORS_FILE, str(e), exc_info=True)

def political_contributions_writer_thread(writer_id):
    """Write political contributions data to TSV file."""
    global done_queuing, political_queue
    political_data = []

    while True:
        item = None
        try:
            item = political_queue.get(timeout=0.1)
            if item is None:
                log_error("Political contributions writer thread {} received shutdown signal", writer_id)
                break
            political_data.extend(item)
        except queue.Empty:
            if done_queuing and political_queue.empty():
                log_error("Political contributions writer thread {} exiting, queue empty and main thread done queuing", writer_id)
                break
            continue
        finally:
            if item is not None:
                political_queue.task_done()

    # Write political contributions data
    if political_data:
        try:
            political_path = os.path.join(args.output_dir, POLITICAL_CONTRIBUTIONS_FILE)
            with open(political_path, 'w', encoding='utf-8', newline='') as f:
                f.write('filer_ein\trecipient\tamount\trecipient_address\trecipient_zip\trecipient_po_box\ttax_year\n')
                writer = csv.writer(f, delimiter='\t')
                for contribution in political_data:
                    writer.writerow([
                        contribution.get('filer_ein', ''),
                        contribution.get('recipient', ''),
                        contribution.get('amount', 0),
                        contribution.get('recipient_address', ''),
                        contribution.get('recipient_zip', ''),
                        contribution.get('recipient_po_box', ''),
                        contribution.get('tax_year', '')
                    ])
            log_error("Wrote {} political contribution records to {}", len(political_data), POLITICAL_CONTRIBUTIONS_FILE)
        except Exception as e:
            log_error("Error writing political contributions file {}: {}", POLITICAL_CONTRIBUTIONS_FILE, str(e), exc_info=True)

def parse_xml_file(xml_content, xml_filename, zip_prefix, zip_path, zip_id, xml_id):
    global DB_PATH
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
            return None

        # Update XML file metadata
        xml_manager = XmlFileManager(DB_PATH)
        xml_manager.update_xml_processed(xml_id, False)  # Mark as processing

        if form_type == "990":
            row, officer_entries, contractors, political_contributions = parse_990.parse_990(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
        elif form_type == "990EZ":
            row, officer_entries, contractors, political_contributions = parse_990ez.parse_990ez(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
        elif form_type == "990PF":
            row, officer_entries, contractors, political_contributions = parse_990pf.parse_990pf(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
        else:
            log_error("Unsupported form type {} in {}, skipping", form_type, xml_filename)
            xml_manager.update_xml_processed(xml_id, False, f"Unsupported form type: {form_type}")
            file_counter_local.skipped += 1
            return None

        if row is None:
            log_error("Parsing returned None for {}, skipping", xml_filename)
            xml_manager.update_xml_processed(xml_id, False, "Parsing returned None")
            file_counter_local.skipped += 1
            return None

        # Debug: Log successful parsing
        log_error("Successfully parsed data from {}", xml_filename)
        tax_year = row[0]
        org_type = row[6]
        total_exp = float(row[7]) if row[7] else 0
        grift_ratio = float(row[22]) if row[22] else 0
        ein = row[1]
        if grift_ratio > 100 and total_exp > 0:
            log_error("Suspicious grift_ratio {}% for EIN {} in {}", grift_ratio, ein, xml_filename, ein=ein)

        # Extract address data from XML using parse_filer_address for consistency
        success, parsed_filer_ein, address_dict, canonical_address, po_box, mailing_zip = cu.parse_filer_address(xml_content, xml_filename, {}, {}, None, None, parse_type="filer", skip_address_errors=True)
        if not success:
            canonical_address, po_box, mailing_zip = "", None, None

        # Get geocoded colocator
        colocator_dict = {'canonical': canonical_address, 'po_box': po_box, 'zip_code': mailing_zip}
        colocator = get_colocator_for_address(colocator_dict)

        # Prepare charity data for database
        charity_data = {
            'ein': ein,
            'tax_year': tax_year,
            'filer_name': row[2],
            'receipt_amt': row[3],
            'govt_amt': row[4],
            'contrib_amt': row[5],
            'org_type': org_type,
            'total_exp': row[7],
            'prog_exp': row[8],
            'travel_amt': row[9],
            'conferences_amt': row[10],
            'officer_comp': row[11],
            'comp_pct': row[12],
            'comp_ptile': row[13],
            'travel_pct': row[14],
            'travel_ptile': row[15],
            'conferences_pct': row[16],
            'conferences_ptile': row[17],
            'grants_pct': row[18],
            'grants_ptile': row[19],
            'foreign_expenses_pct': row[20],
            'foreign_expenses_ptile': row[21],
            'grift_ratio': grift_ratio,
            'total_assets': row[23],
            'form_type': form_type,
            'denominator': row[25],
            'foreign_office': row[26],
            'foreign_expenses': row[27],
            'grants_to_others': row[28],
            'domestic_misrep_flag': row[29],
            'xml_name': f"{zip_path}/{xml_filename}",
            'grift': row[30] if len(row) > 30 else None
        }

        # Prepare address data
        address_data = {
            'ein': ein,
            'name': row[2],
            'canonical_address': canonical_address,
            'po_box': po_box,
            'zip_code': mailing_zip,
            'address_type': 'filer'
        } if canonical_address else None

        # Prepare grant entries (if any)
        grant_entries = []  # This would need to be extracted from parsing functions

        # Prepare officer entries with charity_id placeholder
        for officer in officer_entries:
            officer['charity_id'] = None  # Will be set after charity insert

        log_error("Finished processing XML {}, took {:.2f} seconds, memory usage: {}%", xml_filename, time.time() - start_time, psutil.virtual_memory().percent, ein=ein)

        # Mark XML as processed
        xml_manager.update_xml_processed(xml_id, True)

        return charity_data, address_data, grant_entries, officer_entries, contractors, political_contributions

    except Exception as e:
        log_error("Error processing {}: {}", xml_filename, str(e), exc_info=True, ein=None)
        xml_manager = XmlFileManager(DB_PATH)
        xml_manager.update_xml_processed(xml_id, False, str(e))
        file_counter_local.skipped += 1
        return None

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

    # Register ZIP file in database
    indexing_manager = IndexingManager(DB_PATH)
    try:
        zip_file, xml_files = indexing_manager.build_index_from_zip(zip_path, extract_metadata=True)
        zip_id = zip_file.zip_id
        log_error("Registered ZIP file {} with ID {}", zip_path, zip_id)
    except Exception as e:
        log_error("Error registering ZIP file {}: {}", zip_path, str(e), exc_info=True)
        return

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            xml_files_in_zip = [f for f in zip_ref.namelist() if f.endswith('.xml')]
            if not xml_files_in_zip:
                log_error("ZIP file {} contains no XML files, skipping", zip_path)
                return
            xml_file_counts = Counter(xml_files_in_zip)
            duplicates = {filename: count for filename, count in xml_file_counts.items() if count > 1}
            if duplicates:
                log_error("Found duplicate XML files in {}: {}", zip_path, duplicates)
            total_xml_files = len(xml_files_in_zip)
            log_error("Found {} XML files in {}", total_xml_files, zip_path)

            # Create mapping of filename to xml_id
            xml_id_map = {xml_file.filename: xml_file.xml_id for xml_file in xml_files}

            with ThreadPoolExecutor(max_workers=worker_threads) as executor:
                futures = []
                with tqdm(total=len(xml_files_in_zip), desc=f"Processing {zip_path}") as pbar:
                    for i, xml_filename in enumerate(xml_files_in_zip):
                        xml_id = xml_id_map.get(xml_filename)
                        if not xml_id:
                            log_error("XML file {} not found in database index, skipping", xml_filename)
                            pbar.update(1)
                            continue

                        try:
                            with zip_ref.open(xml_filename) as xml_file:
                                xml_content = xml_file.read()
                        except Exception as e:
                            log_error("Error reading XML {} from {}: {}", xml_filename, zip_path, str(e), exc_info=True)
                            pbar.update(1)
                            continue
                        future = executor.submit(parse_xml_file, xml_content, xml_filename, zip_prefix, zip_path, zip_id, xml_id)
                        futures.append((future, xml_filename))
                        if len(futures) >= batch_size or i == len(xml_files_in_zip) - 1:
                            for future, xml_filename in futures:
                                try:
                                    result = future.result(timeout=300)
                                    if result is None:
                                        log_error("Parsing failed for {}, skipping", xml_filename)
                                        file_counter_local.skipped += 1
                                        pbar.update(1)
                                        continue

                                    charity_data, address_data, grant_entries, officer_entries, contractors, political_contributions = result

                                    # Queue data for database insertion
                                    try:
                                        db_write_queue.put_nowait((charity_data, address_data, grant_entries, officer_entries, contractors, political_contributions))
                                        total_entries += 1
                                        log_error("Queued data for {} in year {}", xml_filename, charity_data['tax_year'])
                                    except queue.Full:
                                        log_error("DB write queue full, waiting to put data for {}", xml_filename)
                                        db_write_queue.put((charity_data, address_data, grant_entries, officer_entries, contractors, political_contributions), block=True)
                                        total_entries += 1
                                        log_error("Queued data for {} in year {} after waiting", xml_filename, charity_data['tax_year'])

                                    # Queue officer data
                                    for entry in officer_entries:
                                        try:
                                            officer_queue.put_nowait(entry)
                                        except queue.Full:
                                            log_error("Officer queue full, waiting to put entry for {} {}", entry["first_name"], entry["last_name"])
                                            officer_queue.put(entry, block=True)

                                    # Queue contractors data
                                    if contractors:
                                        try:
                                            contractors_queue.put_nowait(contractors)
                                        except queue.Full:
                                            log_error("Contractors queue full, waiting to put {} contractor records", len(contractors))
                                            contractors_queue.put(contractors, block=True)

                                    # Queue political contributions data
                                    if political_contributions:
                                        try:
                                            political_queue.put_nowait(political_contributions)
                                        except queue.Full:
                                            log_error("Political contributions queue full, waiting to put {} records", len(political_contributions))
                                            political_queue.put(political_contributions, block=True)
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

            # Mark ZIP file as processed
            zip_manager = ZipFileManager(DB_PATH)
            zip_manager.update_zip_status(zip_id, 'processed')

    except Exception as e:
        log_error("Error processing ZIP {}: {}", zip_path, str(e), exc_info=True)
        # Mark ZIP file as error
        zip_manager = ZipFileManager(DB_PATH)
        zip_manager.update_zip_status(zip_id, 'error')

def save_xpath_stats():
    log_error("Saving xpath_stats.json. Current xpath_match_stats: {}", dict(xpath_match_stats))
    with open(os.path.join(args.output_dir, "xpath_stats.json"), "w") as f:
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
    with open(os.path.join(args.output_dir, "xpaths_990_reordered.py"), "w") as f:
        f.write("from lxml import etree\n\n")
        f.write("NAMESPACES = {'irs': 'http://www.irs.gov/efile'}\n\n")
        f.write("XPATHS_990 = {\n")
        for field, xpaths in XPATHS_990.items():
            f.write(f'    "{field}": [\n')
            for xpath in xpaths:
                f.write(f'        etree.XPath("{xpath}", namespaces=NAMESPACES),\n')
            f.write("    ],\n")
        f.write("}\n")
    with open(os.path.join(args.output_dir, "xpaths_990ez_reordered.py"), "w") as f:
        f.write("from lxml import etree\n\n")
        f.write("NAMESPACES = {'irs': 'http://www.irs.gov/efile'}\n\n")
        f.write("XPATHS_990EZ = {\n")
        for field, xpaths in XPATHS_990EZ.items():
            f.write(f'    "{field}": [\n')
            for xpath in xpaths:
                f.write(f'        etree.XPath("{xpath}", namespaces=NAMESPACES),\n')
            f.write("    ],\n")
        f.write("}\n")
    with open(os.path.join(args.output_dir, "xpaths_990pf_reordered.py"), "w") as f:
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
            tsv_path = os.path.join(args.output_dir, f"charities_{org_type}_{year}.tsv")
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
        for tsv_path in glob.glob(os.path.join(args.output_dir, "charities_*.tsv")):
            line_count = sum(1 for _ in f)
        if line_count <= 1:
            os.remove(tsv_path)
            log_error("Removed empty TSV file {}", tsv_path)

def initialize_database(db_path=None):
    """Initialize database with schema if tables don't exist."""
    if db_path is None:
        db_path = DB_PATH
    if db_path is None:
        raise ValueError("Database path must be provided")

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON')

    # Read schema file and execute it
    with open('schema.sql', 'r') as f:
        schema_sql = f.read()

    # Execute the entire schema as one statement
    try:
        conn.executescript(schema_sql)
        conn.commit()
        log_error("Database initialized with schema")
    except sqlite3.OperationalError as e:
        if "already exists" not in str(e):
            log_error("Error executing schema: {}", str(e))
            raise
        else:
            log_error("Database tables already exist, skipping initialization")
    finally:
        conn.close()

def main(start_year=None, end_year=None, input_dir=".", output_dir=".", db_path="/Volumes/Data/final/pipeline_progress.db", eins=None,
          verbose=False, quiet=False, write_buffer_size=10000, worker_threads=16,
          batch_size=500, writer_threads=1):
    """Main function for extracting charity data from IRS 990 XML files."""
    global args, DB_PATH
    # Create a mock args object for compatibility
    class MockArgs:
        pass

    args = MockArgs()
    args.start_year = start_year
    args.end_year = end_year
    args.input_dir = input_dir
    args.output_dir = output_dir
    args.db_path = db_path
    args.eins = eins
    args.verbose = verbose
    args.quiet = quiet
    args.write_buffer_size = write_buffer_size
    args.worker_threads = worker_threads
    args.batch_size = batch_size
    args.writer_threads = writer_threads

    # Set global DB_PATH
    DB_PATH = db_path

    # Validate arguments
    if start_year is None or end_year is None:
        raise ValueError("start_year and end_year are required")
    if write_buffer_size < 1:
        raise ValueError("Write buffer size must be at least 1")
    if worker_threads < 1:
        raise ValueError("Number of worker threads must be at least 1")
    if batch_size < 1:
        raise ValueError("Batch size must be at least 1")
    if writer_threads < 1:
        raise ValueError("Number of writer threads must be at least 1")
    if not os.path.isdir(input_dir):
        raise ValueError(f"Input directory {input_dir} does not exist")
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        log_error("Created output directory {}", output_dir)

    # Initialize database
    initialize_database(db_path)

    # Set global variables
    global DEBUG_EINS
    if eins:
        DEBUG_EINS = set(eins.split(','))
        log_error("Set DEBUG_EINS for extra logging: {}", ",".join(DEBUG_EINS))

    if not quiet:
        print(f"DEBUG: verbose is set to {verbose}")

    parse_990.set_logger(logger, log_error, verbose, DEBUG_EINS)
    parse_990ez.set_logger(logger, log_error, verbose, DEBUG_EINS)
    parse_990pf.set_logger(logger, log_error, verbose, DEBUG_EINS)
    initialize_xpath_stats()

    zip_files = sorted(glob.glob(os.path.join(input_dir, "*.zip")))
    if not zip_files:
        if not quiet:
            print(f"No ZIP files found in {input_dir}")
        return

    # Start database writer threads
    db_writer_threads = []
    for i in range(writer_threads):
        thread = threading.Thread(target=db_writer_thread, args=(f"db-writer-{i}",))
        thread.start()
        db_writer_threads.append(thread)

    # Start writer threads for additional data
    officer_writer = threading.Thread(target=officer_writer_thread, args=(f"officer-writer-0",))
    officer_writer.start()

    contractors_writer = threading.Thread(target=contractors_writer_thread, args=(f"contractors-writer-0",))
    contractors_writer.start()

    political_writer = threading.Thread(target=political_contributions_writer_thread, args=(f"political-writer-0",))
    political_writer.start()

    try:
        for zip_path in zip_files:
            process_zip_file(zip_path, start_year, end_year, worker_threads, batch_size)
    finally:
        global done_queuing
        done_queuing = True
        for _ in range(writer_threads):
            db_write_queue.put(None)
        officer_queue.put(None)
        contractors_queue.put(None)
        political_queue.put(None)
        for thread in db_writer_threads:
            thread.join()
        officer_writer.join()
        contractors_writer.join()
        political_writer.join()
        save_xpath_stats()
        reorder_xpaths()

        listener.stop()

    if not quiet:
        print(f"Total entries processed: {total_entries}")

def main_cli():
    """Command-line interface for extract_charities."""
    global verbose, quiet, done_queuing, total_entries, DEBUG_EINS
    global officer_queue
    global args
    parser = argparse.ArgumentParser(description="Extract charity data from IRS 990 XML files.")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--input-dir", type=str, default=".", help="Directory containing ZIP files")
    parser.add_argument("--output-dir", type=str, default=".", help="Directory for output TSV and log files")
    parser.add_argument("--db-path", type=str, default="/Volumes/Data/final/pipeline_progress.db", help="Path to SQLite database")
    parser.add_argument("--eins", type=str, help="Comma-separated list of EINs for extra logging")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument("--write-buffer-size", type=int, default=10000, help="Number of rows to buffer before writing to database")
    parser.add_argument("--worker-threads", type=int, default=16, help="Number of worker threads for XML parsing")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for processing futures")
    parser.add_argument("--writer-threads", type=int, default=1, help="Number of database writer threads")
    args = parser.parse_args()

    main(
        start_year=args.start_year,
        end_year=args.end_year,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        db_path=args.db_path,
        eins=args.eins,
        verbose=args.verbose,
        quiet=args.quiet,
        write_buffer_size=args.write_buffer_size,
        worker_threads=args.worker_threads,
        batch_size=args.batch_size,
        writer_threads=args.writer_threads
    )

if __name__ == "__main__":
    main_cli()