import os
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import argparse
import threading
import zipfile
from io import BytesIO
import glob
import time
from collections import Counter, defaultdict
from tqdm import tqdm
import logging
import re
from xpaths import XPATHS_BY_FORM

# Constants
SEARCH_EINS = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486"}
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
high_conferences_count = 0
high_comp_count = 0
parsed_ein_count = 0
grant_log_count = 0
error_log_count = 0
foreign_exp_log_count = 0
chai_amnesty_grant_count = defaultdict(int)
verbose = False

logging.basicConfig(
    filename='error_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - Line %(lineno)d - File %(filename)s - %(message)s'
)

def log_error(message, exc_info=False):
    global high_conferences_count, high_comp_count, parsed_ein_count, grant_log_count, error_log_count, foreign_exp_log_count, chai_amnesty_grant_count
    if any(x in message for x in ["Thread", "Opening TSV", "Wrote row", "Closed and flushed", "Assigned tax_year"]) and not verbose:
        logging.info(message)
        return
    if "Parsed EIN" in message and parsed_ein_count >= 5 and not any(ein in message for ein in SEARCH_EINS):
        return
    if "grants_to_others" in message and grant_log_count >= 5 and not any(ein in message for ein in SEARCH_EINS):
        return
    if "High conferences_amt" in message and high_conferences_count >= 5:
        return
    if any(x in message for x in ["Error", "Missing", "Invalid", "Unparsed"]) and error_log_count >= 5:
        logging.error(message, exc_info=exc_info)
        error_log_count += 1
        return
    if "RegionTotalExpendituresAmt" in message and foreign_exp_log_count >= 5 and not any(ein in message for ein in SEARCH_EINS):
        return
    if any(ein in message for ein in SEARCH_EINS) and "Grant" in message and chai_amnesty_grant_count[message.split("EIN ")[1].split(",")[0]] >= 5:
        return

    logging.error(message, exc_info=exc_info)
    if verbose:
        print(f"Log: {message}")
    if "Parsed EIN" in message:
        parsed_ein_count += 1
    if "grants_to_others" in message:
        grant_log_count += 1
    if "High conferences_amt" in message:
        high_conferences_count += 1
    if any(x in message for x in ["Error", "Missing", "Invalid", "Unparsed"]):
        error_log_count += 1
    if "RegionTotalExpendituresAmt" in message:
        foreign_exp_log_count += 1
    if any(ein in message for ein in SEARCH_EINS) and "Grant" in message:
        ein = message.split("EIN ")[1].split(",")[0]
        chai_amnesty_grant_count[ein] += 1

def clean_org_type(org_type):
    org_type = org_type.replace('(', '').replace(')', '').replace(' ', '')
    if org_type == "501c3":
        return "501c3"
    elif org_type == "4947a1":
        return "4947a1"
    elif org_type.startswith("501c"):
        return org_type.replace("c", "c")
    return org_type.lower()

def write_tsv_row(tax_year, org_type, row, tsv_files, xml_filename):
    org_type_clean = clean_org_type(org_type)
    tsv_key = (tax_year, org_type)
    tsv_path = f"charities_{org_type_clean}_{tax_year}.tsv"

    if tsv_key not in tsv_files:
        file_exists = os.path.exists(tsv_path)
        mode = 'w' if not file_exists else 'r+'
        tsv_files[tsv_key] = open(tsv_path, mode=mode, newline="", encoding="utf-8")
        writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
        log_error(f"Opening TSV {tsv_path} in mode {mode}")
        if not file_exists:
            writer.writerow(TSV_COLUMNS)
            tsv_files[tsv_key].flush()
            log_error(f"Wrote header to new TSV {tsv_path}")
        else:
            reader = csv.reader(tsv_files[tsv_key], delimiter='\t')
            next(reader, None)
            first_data_row = next(reader, None)
            tsv_files[tsv_key].seek(0)
            if first_data_row and first_data_row[0] == str(row[0]) and first_data_row[1] == row[1]:
                tsv_files[tsv_key].truncate(0)
                writer.writerow(TSV_COLUMNS)
                log_error(f"Restarted TSV {tsv_path} with new header")
            else:
                tsv_files[tsv_key].seek(0, os.SEEK_END)
                log_error(f"Appending to existing TSV {tsv_path}")

    writer = csv.writer(tsv_files[tsv_key], delimiter='\t')
    writer.writerow(row)
    tsv_files[tsv_key].flush()
    log_error(f"Wrote row to TSV {tsv_path} for EIN {row[1]}, org_type {org_type}, tax_year {tax_year}, XML {xml_filename}")

def close_year_files(year, tsv_files):
    for (tax_year, org_type), file_handle in list(tsv_files.items()):
        if tax_year == year:
            file_handle.flush()
            file_handle.close()
            del tsv_files[(tax_year, org_type)]
            log_error(f"Closed and flushed TSV for org_type {org_type}, tax_year {tax_year}")

def find_element(root, xpaths, namespaces):
    for xpath in xpaths:
        elem = root.find(xpath, namespaces)
        if elem is not None:
            return elem
    return None

def parse_int(value):
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError, AttributeError):
        return 0

def parse_field_990(root, field, namespaces, xml_filename, context):
    if field not in XPATHS_BY_FORM["990"]:
        return 0 if field not in ["org_type", "form_type", "filer_ein", "filer_name", "tax_year"] else "Unknown"

    if field == "officer_comp":
        total = 0
        for xpath in XPATHS_BY_FORM["990"]["officer_comp_elements"]:
            officer_elem = find_element(root, [xpath], namespaces)
            if officer_elem is not None:
                for person in officer_elem.findall(".//*", namespaces):
                    comp_elem = find_element(person, XPATHS_BY_FORM["990"]["officer_comp_value"], namespaces)
                    if comp_elem is not None:
                        comp = parse_int(comp_elem.text)
                        if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                            log_error(f"Suspicious officer_comp ${comp} exceeds total_exp ${context['total_exp']} in {xml_filename}")
                            continue
                        total += comp
        return total

    if field == "grants":
        total = 0
        for xpath in XPATHS_BY_FORM["990"]["grant_elements_f"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990"]["grant_sub_elements_f"]:
                    for grant in schedule_f.findall(sub_xpath, namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            total += amount
                            if context["filer_ein"] in SEARCH_EINS:
                                log_error(f"{'CHAI' if context['filer_ein'] == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleF for EIN {context['filer_ein']}, File {xml_filename}")
                            elif amount > 5_000_000 and grant_log_count < 5:
                                log_error(f"Found CashGrantAmt ${amount} in ScheduleF for EIN {context['filer_ein']}, File {xml_filename}")
        for xpath in XPATHS_BY_FORM["990"]["grant_elements_i"]:
            schedule_i = find_element(root, [xpath], namespaces)
            if schedule_i is not None:
                for sub_xpath in XPATHS_BY_FORM["990"]["grant_sub_elements_i"]:
                    for grant in schedule_i.findall(sub_xpath, namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            total += amount
                            if context["filer_ein"] in SEARCH_EINS:
                                log_error(f"{'CHAI' if context['filer_ein'] == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleI for EIN {context['filer_ein']}, File {xml_filename}")
                            elif amount > 5_000_000 and grant_log_count < 5:
                                log_error(f"Found CashGrantAmt ${amount} in ScheduleI for EIN {context['filer_ein']}, File {xml_filename}")
        if total > 5_000_000 or context["filer_ein"] in SEARCH_EINS:
            log_error(f"Non-zero grants_to_others ${total} for EIN {context['filer_ein']}, Name {context['filer_name']}, TaxYear {context['tax_year']}, XML {xml_filename}")
        elif total == 0 and context["filer_ein"] in SEARCH_EINS:
            return_data = find_element(root, [".//{http://www.irs.gov/efile}ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.findall("*")] if return_data is not None else []
            log_error(f"Zero grants_to_others for EIN {context['filer_ein']}, Name {context['filer_name']}, File {xml_filename}. ReturnData children: {child_tags}")
        return total

    if field == "foreign_exp":
        total = 0
        for xpath in XPATHS_BY_FORM["990"]["foreign_exp_elements"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990"]["foreign_exp_sub_elements"]:
                    for activity in schedule_f.findall(sub_xpath, namespaces):
                        amount_elem = find_element(activity, XPATHS_BY_FORM["990"]["foreign_exp_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            total += amount
                            if context["filer_ein"] in SEARCH_EINS or (amount > 5_000_000 and foreign_exp_log_count < 5):
                                log_error(f"Found RegionTotalExpendituresAmt ${amount} in ScheduleF for EIN {context['filer_ein']}, File {xml_filename}")
        if total == 0 and context["filer_ein"] in SEARCH_EINS:
            return_data = find_element(root, [".//{http://www.irs.gov/efile}ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.findall("*")] if return_data is not None else []
            log_error(f"Zero foreign_expenses for EIN {context['filer_ein']}, Name {context['filer_name']}, File {xml_filename}. ReturnData children: {child_tags}")
        return total

    elem = find_element(root, XPATHS_BY_FORM["990"][field], namespaces)
    if elem is None:
        if field in ["tax_year", "filer_ein", "filer_name", "form_type"]:
            return "Unknown"
        if field == "org_type":
            return "Unknown"
        if field == "foreign_office":
            return False
        return 0

    if field == "org_type":
        for xpath in XPATHS_BY_FORM["990"]["org_type"]:
            if elem.tag in xpath:
                if "501c3" in xpath:
                    return "501(c)(3)"
                if "4947a1" in xpath:
                    return "4947(a)(1)"
                if "501cInd" in xpath:
                    type_num = elem.get("organization501cTypeTxt")
                    if type_num and type_num.isdigit() and 1 <= int(type_num) <= 29:
                        return f"501(c)({type_num})"
        return "Unknown"

    if field == "foreign_office":
        return elem.text.strip().upper() == 'X'

    return parse_int(elem.text) if field not in ["tax_year", "filer_ein", "filer_name", "form_type"] else elem.text.strip()

def parse_field_990EZ(root, field, namespaces, xml_filename, context):
    if field not in XPATHS_BY_FORM["990EZ"]:
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
                            log_error(f"Parsed travel_amt ${amount} from Schedule O in {xml_filename}")
                    if field == "conferences" and ("CONFERENCE" in desc_text or "MEETING" in desc_text):
                        match = re.search(r'\$(\d+\.\d{2}|\d+)', desc.text)
                        if match:
                            amount = int(float(match.group(1).replace('$', '')))
                            total += amount
                            log_error(f"Parsed conferences_amt ${amount} from Schedule O in {xml_filename}")
        return total

    if field == "officer_comp":
        total = 0
        for xpath in XPATHS_BY_FORM["990EZ"]["officer_comp_elements"]:
            officer_elem = find_element(root, [xpath], namespaces)
            if officer_elem is not None:
                comp_elem = find_element(officer_elem, XPATHS_BY_FORM["990EZ"]["officer_comp_value"], namespaces)
                if comp_elem is not None:
                    comp = parse_int(comp_elem.text)
                    if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                        log_error(f"Suspicious officer_comp ${comp} exceeds total_exp ${context['total_exp']} in {xml_filename}")
                        continue
                    total += comp
        return total

    if field == "grants":
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
                        log_error(f"Parsed grants_to_others ${amount} from Schedule O DISBURSEMENT in {xml_filename}")
        for xpath in XPATHS_BY_FORM["990EZ"]["grant_elements_i"]:
            schedule_i = find_element(root, [xpath], namespaces)
            if schedule_i is not None:
                for sub_xpath in XPATHS_BY_FORM["990EZ"]["grant_sub_elements_i"]:
                    for grant in schedule_i.findall(sub_xpath, namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990EZ"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            total += amount
                            if context["filer_ein"] in SEARCH_EINS:
                                log_error(f"{'CHAI' if context['filer_ein'] == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleI for EIN {context['filer_ein']}, File {xml_filename}")
                            elif amount > 5_000_000 and grant_log_count < 5:
                                log_error(f"Found CashGrantAmt ${amount} in ScheduleI for EIN {context['filer_ein']}, File {xml_filename}")
        for xpath in XPATHS_BY_FORM["990EZ"]["grant_elements_f"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990EZ"]["grant_sub_elements_f"]:
                    for grant in schedule_f.findall(sub_xpath, namespaces):
                        amount_elem = find_element(grant, XPATHS_BY_FORM["990EZ"]["grant_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            total += amount
                            if context["filer_ein"] in SEARCH_EINS:
                                log_error(f"{'CHAI' if context['filer_ein'] == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleF for EIN {context['filer_ein']}, File {xml_filename}")
                            elif amount > 5_000_000 and grant_log_count < 5:
                                log_error(f"Found CashGrantAmt ${amount} in ScheduleF for EIN {context['filer_ein']}, File {xml_filename}")
        if total > 5_000_000 or context["filer_ein"] in SEARCH_EINS:
            log_error(f"Non-zero grants_to_others ${total} for EIN {context['filer_ein']}, Name {context['filer_name']}, TaxYear {context['tax_year']}, XML {xml_filename}")
        elif total == 0 and context["filer_ein"] in SEARCH_EINS:
            return_data = find_element(root, [".//{http://www.irs.gov/efile}ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.findall("*")] if return_data is not None else []
            log_error(f"Zero grants_to_others for EIN {context['filer_ein']}, Name {context['filer_name']}, File {xml_filename}. ReturnData children: {child_tags}")
        return total

    if field == "foreign_exp":
        total = 0
        for xpath in XPATHS_BY_FORM["990EZ"]["foreign_exp_elements"]:
            schedule_f = find_element(root, [xpath], namespaces)
            if schedule_f is not None:
                for sub_xpath in XPATHS_BY_FORM["990EZ"]["foreign_exp_sub_elements"]:
                    for activity in schedule_f.findall(sub_xpath, namespaces):
                        amount_elem = find_element(activity, XPATHS_BY_FORM["990EZ"]["foreign_exp_value"], namespaces)
                        if amount_elem is not None:
                            amount = parse_int(amount_elem.text)
                            total += amount
                            if context["filer_ein"] in SEARCH_EINS or (amount > 5_000_000 and foreign_exp_log_count < 5):
                                log_error(f"Found RegionTotalExpendituresAmt ${amount} in ScheduleF for EIN {context['filer_ein']}, File {xml_filename}")
        if total == 0 and context["filer_ein"] in SEARCH_EINS:
            return_data = find_element(root, [".//{http://www.irs.gov/efile}ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.findall("*")] if return_data is not None else []
            log_error(f"Zero foreign_expenses for EIN {context['filer_ein']}, Name {context['filer_name']}, File {xml_filename}. ReturnData children: {child_tags}")
        return total

    elem = find_element(root, XPATHS_BY_FORM["990EZ"][field], namespaces)
    if elem is None:
        if field in ["tax_year", "filer_ein", "filer_name", "form_type"]:
            return "Unknown"
        if field == "org_type":
            return "Unknown"
        if field == "foreign_office":
            return False
        return 0

    if field == "org_type":
        for xpath in XPATHS_BY_FORM["990EZ"]["org_type"]:
            if elem.tag in xpath:
                if "501c3" in xpath:
                    return "501(c)(3)"
                if "4947a1" in xpath:
                    return "4947(a)(1)"
        return "Unknown"

    if field == "foreign_office":
        return elem.text.strip().upper() == 'X'

    return parse_int(elem.text) if field not in ["tax_year", "filer_ein", "filer_name", "form_type"] else elem.text.strip()

def parse_field_990PF(root, field, namespaces, xml_filename, context):
    if field not in XPATHS_BY_FORM["990PF"]:
        return 0 if field not in ["org_type", "form_type", "filer_ein", "filer_name", "tax_year"] else "Unknown"

    if field == "receipt":
        total = 0
        for xpath in XPATHS_BY_FORM["990PF"]["receipt"]:
            elem = find_element(root, [xpath], namespaces)
            if elem is not None:
                total += parse_int(elem.text)
        if total == 0:
            missing_revenue_by_year[context.get("tax_year", "Unknown")] += 1
            log_error(f"Missing revenue fields in {xml_filename}")
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
                    desc_text = desc_elem.text.upper()
                    if field == "travel" and "TRAVEL" in desc_text:
                        total += amount
                        log_error(f"Parsed travel_amt ${total} from OtherExpensesSchedule in {xml_filename}")
                    if field == "conferences" and ("CONFERENCE" in desc_text or "MEETING" in desc_text):
                        total += amount
                        log_error(f"Parsed conferences_amt ${total} from OtherExpensesSchedule in {xml_filename}")
        return total

    if field == "officer_comp":
        total = 0
        for xpath in XPATHS_BY_FORM["990PF"]["officer_comp_elements"]:
            officer_elem = find_element(root, [xpath], namespaces)
            if officer_elem is not None:
                comp_elem = find_element(officer_elem, XPATHS_BY_FORM["990PF"]["officer_comp_value"], namespaces)
                if comp_elem is not None:
                    comp = parse_int(comp_elem.text)
                    if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                        log_error(f"Suspicious officer_comp ${comp} exceeds total_exp ${context['total_exp']} in {xml_filename}")
                        continue
                    total += comp
        return total

    if field == "grants":
        total = 0
        elem = find_element(root, XPATHS_BY_FORM["990PF"]["grants"], namespaces)
        if elem is not None:
            total += parse_int(elem.text)
            log_error(f"Parsed grants_to_others ${total} from 990PF in {xml_filename}")
        if total > 5_000_000 or context["filer_ein"] in SEARCH_EINS:
            log_error(f"Non-zero grants_to_others ${total} for EIN {context['filer_ein']}, Name {context['filer_name']}, TaxYear {context['tax_year']}, XML {xml_filename}")
        elif total == 0 and context["filer_ein"] in SEARCH_EINS:
            return_data = find_element(root, [".//{http://www.irs.gov/efile}ReturnData", ".//ReturnData"], namespaces)
            child_tags = [child.tag for child in return_data.findall("*")] if return_data is not None else []
            log_error(f"Zero grants_to_others for EIN {context['filer_ein']}, Name {context['filer_name']}, File {xml_filename}. ReturnData children: {child_tags}")
        return total

    elem = find_element(root, XPATHS_BY_FORM["990PF"][field], namespaces)
    if elem is None:
        if field in ["tax_year", "filer_ein", "filer_name", "form_type"]:
            return "Unknown"
        if field == "org_type":
            return "Unknown"
        if field == "foreign_office":
            return False
        return 0

    if field == "org_type":
        for xpath in XPATHS_BY_FORM["990PF"]["org_type"]:
            if elem.tag in xpath:
                if "501c3" in xpath:
                    return "501(c)(3)"
                if "4947a1" in xpath:
                    return "4947(a)(1)"
        return "Unknown"

    if field == "foreign_office":
        return elem.text.strip().upper() == 'X'

    return parse_int(elem.text) if field not in ["tax_year", "filer_ein", "filer_name", "form_type"] else elem.text.strip()

PARSE_FIELD_METHODS = {
    "990": parse_field_990,
    "990EZ": parse_field_990EZ,
    "990PF": parse_field_990PF
}

def parse_field(root, field, form_type, namespaces, xml_filename, context):
    return PARSE_FIELD_METHODS[form_type](root, field, namespaces, xml_filename, context)

def parse_xml_file(xml_content, xml_filename, zip_prefix):
    global high_conferences_count, high_comp_count
    if not hasattr(file_counter_local, 'value'):
        file_counter_local.value = 0
        file_counter_local.entries = 0
        file_counter_local.skipped = 0

    try:
        tree = ET.parse(BytesIO(xml_content))
    except ET.ParseError as e:
        log_error(f"Parse error in XML file {xml_filename}: {e}", exc_info=True)
        file_counter_local.skipped += 1
        return []

    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile', '': 'http://www.irs.gov/efile'}

    # Parse basic fields
    context = {}
    context["form_type"] = parse_field(root, "form_type", "990", namespaces, xml_filename, context)
    if context["form_type"] == "990T":
        file_counter_local.skipped += 1
        return []

    context["tax_year"] = parse_field(root, "tax_year", context["form_type"], namespaces, xml_filename, context)
    if context["tax_year"] == "Unknown":
        missing_taxyr_by_year["Unknown"] += 1
        return_header = find_element(root, [".//{http://www.irs.gov/efile}ReturnHeader", ".//ReturnHeader"], namespaces)
        header_snippet = ET.tostring(return_header, encoding='unicode', method='xml')[:2000] if return_header is not None else "No ReturnHeader"
        log_error(f"Missing TaxYr element in {xml_filename}, inferring from filename/zip. ReturnHeader: {header_snippet}")
        context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix
    else:
        try:
            int(context["tax_year"])
        except ValueError:
            invalid_taxyr_by_year[context["tax_year"]] += 1
            log_error(f"Invalid tax year {context['tax_year']} in {xml_filename}, inferring from filename/zip")
            context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix

    context["filer_ein"] = parse_field(root, "filer_ein", context["form_type"], namespaces, xml_filename, context)
    context["filer_name"] = parse_field(root, "filer_name", context["form_type"], namespaces, xml_filename, context)
    if context["filer_ein"] == "Unknown" or context["filer_name"] == "Unknown":
        missing_filer_by_year[context["tax_year"]] += 1
        log_error(f"Missing Filer element in {xml_filename}")

    if context["filer_ein"] in SEARCH_EINS or parsed_ein_count < 5:
        log_error(f"Assigned tax_year {context['tax_year']} for {xml_filename}, EIN {context['filer_ein']}")

    file_counter_local.value += 1

    # Parse financial fields
    fields = [
        "receipt", "govt_grants", "contributions", "total_exp", "prog_exp",
        "travel", "conferences", "officer_comp", "grants", "foreign_exp",
        "total_assets", "org_type", "foreign_office"
    ]
    data = {field: parse_field(root, field, context["form_type"], namespaces, xml_filename, context) for field in fields}

    # Calculate percentages
    def calculate_percentage(value, denom):
        if denom == 0 or value is None or denom is None:
            return 0.0
        return round((value / denom) * 100, 2)

    data["comp_pct"] = calculate_percentage(data["officer_comp"], data["total_exp"])
    data["travel_pct"] = calculate_percentage(data["travel"], data["total_exp"])
    data["conferences_pct"] = calculate_percentage(data["conferences"], data["total_exp"])
    data["grants_pct"] = calculate_percentage(data["grants"], data["total_exp"])
    data["foreign_expenses_pct"] = calculate_percentage(data["foreign_exp"], data["total_exp"])
    data["grift_ratio"] = calculate_percentage(data["officer_comp"] + data["travel"] + data["conferences"], data["total_exp"])

    # Denominator for other purposes
    data["denominator"] = data["total_assets"] + data["receipt"]

    # Placeholder percentile columns
    data["comp_ptile"] = "n/y"
    data["travel_ptile"] = "n/y"
    data["conferences_ptile"] = "n/y"
    data["grants_ptile"] = "n/y"
    data["foreign_expenses_ptile"] = "n/y"

    # Calculate domestic_misrep_flag
    data["domestic_misrep_flag"] = data["grift_ratio"] > 10 and data["foreign_expenses_pct"] < 0.1 * 100 if data["total_exp"] > 0 else False

    if parsed_ein_count < 5 or context["filer_ein"] in SEARCH_EINS:
        log_error(f"Parsed EIN {context['filer_ein']}: denominator {data['denominator']}, receipt_amt {data['receipt']}, total_assets {data['total_assets']}, org_type {data['org_type']}, grants_to_others {data['grants']}, XML {xml_filename}")

    results = []
    try:
        row = [
            context["tax_year"], context["filer_ein"], context["filer_name"], data["receipt"], data["govt_grants"],
            data["contributions"], data["org_type"], data["total_exp"], data["prog_exp"], data["travel"],
            data["conferences"], data["officer_comp"], data["comp_pct"], data["comp_ptile"], data["travel_pct"],
            data["travel_ptile"], data["conferences_pct"], data["conferences_ptile"], data["grants_pct"],
            data["grants_ptile"], data["foreign_expenses_pct"], data["foreign_expenses_ptile"], data["grift_ratio"],
            data["total_assets"], context["form_type"], data["denominator"], data["foreign_office"],
            data["foreign_exp"], data["grants"], data["domestic_misrep_flag"], xml_filename
        ]
        results.append(row)
        file_counter_local.entries += 1
        if (file_counter_local.value % 10000) == 0:
            log_error(f"Thread {threading.get_ident()} processed {file_counter_local.value} files, {file_counter_local.entries} entries, {file_counter_local.skipped} skipped")
    except Exception as e:
        log_error(f"Failed to append result for {xml_filename}, EIN {context['filer_ein']}: {e}", exc_info=True)
        return []

    return results, data["org_type"], xml_filename

def process_zip_files(start_year, end_year, max_workers=4):
    global total_xml_files, parsed_ein_count, grant_log_count, error_log_count, foreign_exp_log_count, chai_amnesty_grant_count
    global missing_taxyr_by_year, invalid_taxyr_by_year, missing_filer_by_year
    global missing_revenue_by_year, invalid_predicate_by_year
    global high_conferences_count, high_comp_count
    start_time = time.time()
    year_prefixes = [str(y) for y in range(int(start_year), int(end_year) + 1)]
    tsv_files = {}
    total_xml_files = 0
    total_processed_files = 0
    total_entries = 0
    parsed_ein_count = 0
    grant_log_count = 0
    error_log_count = 0
    foreign_exp_log_count = 0
    chai_amnesty_grant_count = defaultdict(int)

    all_zip_files = []
    for year_prefix in year_prefixes:
        zip_patterns = [f"recompressed_zips/{year_prefix}*.zip", f"{year_prefix}*.zip"]
        for pattern in zip_patterns:
            all_zip_files.extend(glob.glob(pattern))
    total_zip_files = len(all_zip_files)

    for zip_file in all_zip_files:
        log_error(f"Found ZIP: {zip_file}")

    zip_counter = 0
    for year_prefix in year_prefixes:
        zip_patterns = [f"recompressed_zips/{year_prefix}*.zip", f"{year_prefix}*.zip"]
        zip_files = []
        for pattern in zip_patterns:
            zip_files.extend(glob.glob(pattern))
        if not zip_files:
            print(f"No ZIP files found for patterns: {zip_patterns}")
            continue

        for zip_file_path in zip_files:
            zip_counter += 1
            print(f"\nProcessing {os.path.basename(zip_file_path)}")
            print(f"{zip_counter} of {total_zip_files}")
            log_error(f"Processing ZIP: {zip_file_path}")
            parsed_ein_count = 0
            try:
                with zipfile.ZipFile(zip_file_path, 'r') as zf:
                    xml_files = [f for f in zf.namelist() if f.lower().endswith('.xml')]
                    total_xml_files += len(xml_files)
                    log_error(f"Found {len(xml_files)} XML files in {zip_file_path}")

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {}
                        for xml_file in tqdm(xml_files, desc="Submitting XML files"):
                            try:
                                with zf.open(xml_file) as file_obj:
                                    xml_content = file_obj.read()
                            except Exception as e:
                                log_error(f"Error reading {xml_file} from {zip_file_path}: {e}", exc_info=True)
                                continue

                            futures[executor.submit(parse_xml_file, xml_content, xml_file, year_prefix)] = (zip_file_path, xml_file)

            except (NotImplementedError, zipfile.BadZipFile) as e:
                log_error(f"Error processing {zip_file_path}: {e}", exc_info=True)
                continue

            for future in as_completed(futures):
                zip_file, xml_file = futures[future]
                try:
                    result = future.result()
                    if result:
                        results, org_type, xml_filename = result
                        for res in results:
                            tax_year = int(res[0])
                            org_type = res[6]
                            write_tsv_row(tax_year, org_type, res, tsv_files, xml_filename)
                            total_entries += 1
                except Exception as e:
                    log_error(f"Error processing {xml_file} in {zip_file}: {e}", exc_info=True)
                total_processed_files += 1

        close_year_files(int(year_prefix), tsv_files)

    for file_handle in tsv_files.values():
        file_handle.flush()
        file_handle.close()

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
    parser = argparse.ArgumentParser(description="Extract charity data from IRS XMLs to TSVs by org type and year.")
    parser.add_argument("start_year", type=valid_tax_year, help="Start year for ZIP prefixes (e.g., 2016).")
    parser.add_argument("end_year", type=valid_tax_year, help="End year for ZIP prefixes (e.g., 2024).")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging for thread progress and operations.")

    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise argparse.ArgumentError(None, "Start year must be less than or equal to end year.")
    verbose = args.verbose

    process_zip_files(str(args.start_year), str(args.end_year), max_workers=4)