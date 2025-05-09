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
import numpy as np
from tqdm import tqdm
import logging
import pandas as pd
import re
from mako.template import Template
from mako.exceptions import MakoException

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

logging.basicConfig(
    filename='error_log.txt',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - Line %(lineno)d - File %(filename)s - %(message)s'
)

def log_error(message, exc_info=False):
    global high_conferences_count, high_comp_count
    logging.error(message, exc_info=exc_info)
    if "High conferences_amt" in message and high_conferences_count < 5:
        high_conferences_count += 1
        print(f"Error Log: {message}")
    elif "Potential high grift org" in message and high_comp_count < 5:
        high_comp_count += 1
        print(f"Error Log: {message}")

def assign_grift_rating(percentile):
    if percentile < 50:
        return 'A'
    elif percentile < 70:
        return 'B'
    elif percentile < 85:
        return 'C'
    elif percentile < 95:
        return 'D'
    else:
        return 'F'

ORG_TYPE_DESCRIPTIONS = {
    "501(c)(3)": "Charitable organizations, including religious, educational, scientific, and public safety organizations.",
    "501(c)(4)": "Civic leagues, social welfare organizations, and local associations of employees.",
    "501(c)(5)": "Labor, agricultural, and horticultural organizations.",
    "501(c)(6)": "Business leagues, chambers of commerce, and real estate boards.",
    "501(c)(7)": "Social and recreational clubs.",
    "501(c)(8)": "Fraternal beneficiary societies and associations.",
    "501(c)(9)": "Voluntary employees’ beneficiary associations.",
    "501(c)(10)": "Domestic fraternal societies and associations.",
    "501(c)(12)": "Benevolent life insurance associations, mutual ditch or irrigation companies, and mutual or cooperative telephone companies.",
    "501(c)(14)": "State-chartered credit unions and mutual reserve funds.",
    "Unknown": "Organization type not specified or not recognized as a 501(c) entity."
}

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
    namespaces = {'irs': 'http://www.irs.gov/efile'}

    form_type_elem = root.find(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces=namespaces)
    form_type = form_type_elem.text.strip() if form_type_elem is not None else "Unknown"

    tax_year_elem = root.find(".//irs:ReturnHeader/irs:TaxYr", namespaces=namespaces)
    if tax_year_elem is None:
        missing_taxyr_by_year["Unknown"] += 1
        log_error(f"Missing TaxYr element in {xml_filename}, inferring from filename/zip", exc_info=True)
        tax_year = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix
    else:
        tax_year = tax_year_elem.text.strip()
        try:
            int(tax_year)
        except ValueError:
            invalid_taxyr_by_year[tax_year] += 1
            log_error(f"Invalid tax year {tax_year} in {xml_filename}, inferring from filename/zip", exc_info=True)
            tax_year = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix

    filer = root.find(".//irs:Filer", namespaces=namespaces)
    if filer is None:
        missing_filer_by_year[tax_year] += 1
        log_error(f"Missing Filer element in {xml_filename}, using placeholders", exc_info=True)
        filer_ein = "Unknown"
        filer_name = "Unknown"
    else:
        filer_ein_elem = filer.find("irs:EIN", namespaces=namespaces)
        filer_name_elem = filer.find("./irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=namespaces)
        filer_ein = filer_ein_elem.text.strip() if filer_ein_elem is not None else "Unknown"
        filer_name = filer_name_elem.text.strip() if filer_name_elem is not None else "Unknown"

    file_counter_local.value += 1

    receipt_amt = 0
    for revenue_field in [
        "irs:GrossReceiptsAmt",
        "irs:TotalRevenueAmt",
        "irs:CYTotalRevenueAmt",
        "irs:RevenueAndExpensesPerBooksAmt",
        "irs:TotalRevenue",
        "irs:AnalysisOfRevenueAndExpenses/irs:TotalRevAndExpnssAmt",
    ]:
        try:
            receipt_elem = root.find(f".//{revenue_field}", namespaces=namespaces)
            if receipt_elem is not None:
                try:
                    receipt_amt = int(float(receipt_elem.text.strip()))
                    break
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid value in {revenue_field}: {receipt_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                    receipt_amt = 0
                    break
        except Exception as e:
            log_error(f"Error during {revenue_field} lookup in {xml_filename}: {e}", exc_info=True)
            continue
    else:
        missing_revenue_by_year[tax_year] += 1

    total_assets = 0
    try:
        assets_amt = None
        assets_eoy = None
        assets_elem = root.find(".//irs:TotalAssetsAmt", namespaces=namespaces)
        if assets_elem is not None:
            try:
                assets_amt = int(float(assets_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid TotalAssetsAmt value: {assets_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        assets_elem = root.find(".//irs:TotalAssetsEOYAmt", namespaces=namespaces)
        if assets_elem is not None:
            try:
                assets_eoy = int(float(assets_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid TotalAssetsEOYAmt value: {assets_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        total_assets = max(assets_amt or 0, assets_eoy or 0)
    except Exception as e:
        log_error(f"Error during TotalAssets lookup in {xml_filename}: {e}", exc_info=True)

    govt_amt = 0
    try:
        grant_elem = root.find(".//irs:GovernmentGrantsAmt", namespaces=namespaces)
        if grant_elem is not None:
            try:
                govt_amt = int(float(grant_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid GovernmentGrantsAmt value: {grant_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                govt_amt = 0
    except Exception as e:
        log_error(f"Error during GovernmentGrantsAmt lookup in {xml_filename}: {e}", exc_info=True)

    contrib_amt = 0
    try:
        contrib_elem = root.find(".//irs:AllOtherContributionsAmt", namespaces=namespaces)
        if contrib_elem is not None:
            try:
                contrib_amt = int(float(contrib_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid AllOtherContributionsAmt value: {contrib_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                contrib_amt = 0
    except Exception as e:
        log_error(f"Error during AllOtherContributionsAmt lookup in {xml_filename}: {e}", exc_info=True)

    total_exp = 0
    if form_type == "990PF":
        try:
            total_exp_elem = root.find(".//irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt", namespaces=namespaces)
            if total_exp_elem is not None:
                try:
                    total_exp = int(float(total_exp_elem.text.strip()))
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid TotalExpensesRevAndExpnssAmt value: {total_exp_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                    total_exp = 0
        except Exception as e:
            log_error(f"Error during TotalExpensesRevAndExpnssAmt lookup in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            total_exp_elem = root.find(".//irs:TotalFunctionalExpensesGrp/irs:TotalAmt", namespaces=namespaces)
            if total_exp_elem is not None:
                try:
                    total_exp = int(float(total_exp_elem.text.strip()))
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid TotalFunctionalExpensesGrp value: {total_exp_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                    total_exp = 0
            if total_exp == 0:
                total_exp_elem = root.find(".//irs:TotalExpensesAmt", namespaces=namespaces)
                if total_exp_elem is not None:
                    try:
                        total_exp = int(float(total_exp_elem.text.strip()))
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid TotalExpensesAmt value: {total_exp_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                        total_exp = 0
        except Exception as e:
            log_error(f"Error during TotalExpenses lookup in {xml_filename}: {e}", exc_info=True)

    prog_exp = 0
    try:
        prog_exp_elem = root.find(".//irs:TotalProgramServiceExpensesAmt", namespaces=namespaces)
        if prog_exp_elem is not None:
            try:
                prog_exp = int(float(prog_exp_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid TotalProgramServiceExpensesAmt value: {prog_exp_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                prog_exp = 0
    except Exception as e:
        log_error(f"Error during TotalProgramServiceExpensesAmt lookup in {xml_filename}: {e}", exc_info=True)

    travel_amt = 0
    if form_type == "990PF":
        try:
            other_exp_schedule = root.find(".//irs:OtherExpensesSchedule", namespaces=namespaces)
            if other_exp_schedule is not None:
                for expense in other_exp_schedule.findall("irs:OtherExpensesScheduleGrp", namespaces=namespaces):
                    desc_elem = expense.find("irs:Desc", namespaces=namespaces)
                    if desc_elem is not None and "TRAVEL" in desc_elem.text.upper():
                        amount_elem = expense.find("irs:RevenueAndExpensesPerBooksAmt", namespaces=namespaces)
                        if amount_elem is not None:
                            try:
                                travel_amt = int(float(amount_elem.text.strip()))
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid travel amount in OtherExpensesSchedule: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                                travel_amt = 0
                            break
        except Exception as e:
            log_error(f"Error during OtherExpensesSchedule lookup for travel in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            travel_elem = root.find(".//irs:TravelGrp/irs:TotalAmt", namespaces=namespaces)
            if travel_elem is not None:
                try:
                    travel_amt = int(float(travel_elem.text.strip()))
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid TravelGrp value: {travel_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                    travel_amt = 0
        except Exception as e:
            log_error(f"Error during TravelGrp lookup in {xml_filename}: {e}", exc_info=True)

    conferences_amt = 0
    if form_type == "990PF":
        try:
            other_exp_schedule = root.find(".//irs:OtherExpensesSchedule", namespaces=namespaces)
            if other_exp_schedule is not None:
                for expense in other_exp_schedule.findall("irs:OtherExpensesScheduleGrp", namespaces=namespaces):
                    desc_elem = expense.find("irs:Desc", namespaces=namespaces)
                    if desc_elem is not None and ("CONFERENCE" in desc_elem.text.upper() or "MEETING" in desc_elem.text.upper()):
                        amount_elem = expense.find("irs:RevenueAndExpensesPerBooksAmt", namespaces=namespaces)
                        if amount_elem is not None:
                            try:
                                conferences_amt += int(float(amount_elem.text.strip()))
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid conference amount in OtherExpensesSchedule: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during OtherExpensesSchedule lookup for conferences in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            conferences_elem = root.find(".//irs:ConferencesMeetingsGrp/irs:TotalAmt", namespaces=namespaces)
            if conferences_elem is not None:
                try:
                    conferences_amt = int(float(conferences_elem.text.strip()))
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid ConferencesMeetingsGrp value: {conferences_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                    conferences_amt = 0
        except Exception as e:
            log_error(f"Error during ConferencesMeetingsGrp lookup in {xml_filename}: {e}", exc_info=True)

    if conferences_amt > 1_000_000 and high_conferences_count < 5:
        log_error(f"High conferences_amt ${conferences_amt} for EIN {filer_ein}, Name {filer_name}, TaxYear {tax_year}")

    officer_comp = 0
    if form_type == "990PF":
        try:
            officer = root.find(".//irs:OfficerDirTrstKeyEmplGrp", namespaces=namespaces)
            if officer is not None:
                comp_elem = officer.find("irs:CompensationAmt", namespaces=namespaces)
                if comp_elem is not None and comp_elem.text.strip():
                    try:
                        officer_comp = int(float(comp_elem.text.strip()))
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid CompensationAmt value: {comp_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during OfficerDirTrstKeyEmplGrp lookup in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            for person in root.findall(".//irs:Form990PartVIISectionAGrp", namespaces=namespaces):
                comp_elem = person.find("irs:ReportableCompFromOrgAmt", namespaces=namespaces)
                if comp_elem is not None and comp_elem.text.strip():
                    try:
                        officer_comp += int(float(comp_elem.text.strip()))
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid ReportableCompFromOrgAmt value: {comp_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during Form990PartVIISectionAGrp lookup in {xml_filename}: {e}", exc_info=True)

    grants_to_others = 0
    try:
        schedule_f = root.find(".//irs:ScheduleF", namespaces=namespaces)
        if schedule_f is not None:
            for grant in schedule_f.findall(".//irs:GrantsToOrgOutsideUSGrp", namespaces=namespaces):
                for field in ["irs:CashGrantAmt", "irs:TotalAmt"]:
                    amount_elem = grant.find(field, namespaces=namespaces)
                    if amount_elem is not None:
                        try:
                            amount = int(float(amount_elem.text.strip()))
                            grants_to_others += amount
                            log_error(f"Found {field} ${amount} in ScheduleF for EIN {filer_ein}, File {xml_filename}")
                        except (ValueError, TypeError) as e:
                            log_error(f"Invalid {field} in ScheduleF: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
            for grant in schedule_f.findall(".//irs:GrantsToOrganizationsOutsideUS", namespaces=namespaces):
                amount_elem = grant.find("irs:TotalAmt", namespaces=namespaces)
                if amount_elem is not None:
                    try:
                        amount = int(float(amount_elem.text.strip()))
                        grants_to_others += amount
                        log_error(f"Found irs:GrantsToOrganizationsOutsideUS/irs:TotalAmt ${amount} in ScheduleF for EIN {filer_ein}, File {xml_filename}")
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid irs:GrantsToOrganizationsOutsideUS/irs:TotalAmt: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        schedule_i = root.find(".//irs:ScheduleI", namespaces=namespaces)
        if schedule_i is not None:
            for grant in schedule_i.findall(".//irs:GrantOrContributionPdDurYrGrp", namespaces=namespaces):
                for field in ["irs:CashGrantAmt", "irs:Amount", "irs:TotalAmt"]:
                    amount_elem = grant.find(field, namespaces=namespaces)
                    if amount_elem is not None:
                        try:
                            amount = int(float(amount_elem.text.strip()))
                            grants_to_others += amount
                            log_error(f"Found {field} ${amount} in ScheduleI for EIN {filer_ein}, File {xml_filename}")
                        except (ValueError, TypeError) as e:
                            log_error(f"Invalid {field} in ScheduleI: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        if grants_to_others > 0:
            log_error(f"Non-zero grants_to_others ${grants_to_others} for EIN {filer_ein}, Name {filer_name}, TaxYear {tax_year}")
        elif filer_ein in ["271414646", "520851555"]:
            log_error(f"Zero grants_to_others for EIN {filer_ein}, Name {filer_name}, File {xml_filename}. Parent XML: {ET.tostring(root, encoding='unicode')[:1000]}")
    except Exception as e:
        log_error(f"Error during ScheduleF/I lookup for grants in {xml_filename}: {e}", exc_info=True)

    foreign_expenses = 0
    try:
        schedule_f = root.find(".//irs:ScheduleF", namespaces=namespaces)
        if schedule_f is not None:
            for activity in schedule_f.findall(".//irs:StmtOfActyOutsdUSGrp", namespaces=namespaces):
                for field in ["irs:RegionTotalExpendituresAmt", "irs:TotalAmt"]:
                    amount_elem = activity.find(field, namespaces=namespaces)
                    if amount_elem is not None:
                        try:
                            amount = int(float(amount_elem.text.strip()))
                            foreign_expenses += amount
                            log_error(f"Found {field} ${amount} in ScheduleF for EIN {filer_ein}, File {xml_filename}")
                        except (ValueError, TypeError) as e:
                            log_error(f"Invalid {field} in ScheduleF: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        if foreign_expenses == 0 and filer_ein in ["271414646", "520851555"]:
            log_error(f"Zero foreign_expenses for EIN {filer_ein}, Name {filer_name}, File {xml_filename}. Parent XML: {ET.tostring(root, encoding='unicode')[:1000]}")
    except Exception as e:
        log_error(f"Error during ScheduleF lookup for foreign expenses in {xml_filename}: {e}", exc_info=True)

    org_type = "Unknown"
    org_type_debug = "Not found"
    if form_type == "990PF":
        try:
            org_type_elem = root.find(".//irs:Organization501c3ExemptPFInd", namespaces=namespaces)
            if org_type_elem is not None and org_type_elem.text.strip().upper() == 'X':
                org_type = "501(c)(3)"
                org_type_debug = ET.tostring(org_type_elem, encoding='unicode', method='xml').strip()
                ein_type_dict[filer_ein] = org_type
            elif filer_ein in ein_type_dict:
                org_type = ein_type_dict[filer_ein]
        except Exception as e:
            log_error(f"Error during Organization501c3ExemptPFInd lookup in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            org_type_elem = root.find(".//irs:Organization501cInd", namespaces=namespaces)
            if org_type_elem is not None:
                type_num = org_type_elem.get("organization501cTypeTxt")
                org_type_debug = ET.tostring(org_type_elem, encoding='unicode', method='xml').strip()
                if type_num and org_type_elem.text.strip().upper() == 'X':
                    org_type = f"501(c)({type_num})"
                    ein_type_dict[filer_ein] = org_type
            else:
                org_type_elem = root.find(".//irs:Organization501c3Ind", namespaces=namespaces)
                if org_type_elem is not None and org_type_elem.text.strip().upper() == 'X':
                    org_type = "501(c)(3)"
                    org_type_debug = ET.tostring(org_type_elem, encoding='unicode', method='xml').strip()
                    ein_type_dict[filer_ein] = org_type
                elif filer_ein in ein_type_dict:
                    org_type = ein_type_dict[filer_ein]
        except Exception as e:
            log_error(f"Error during Organization type lookup in {xml_filename}: {e}", exc_info=True)

    foreign_office = False
    try:
        foreign_office_elem = root.find(".//irs:ForeignOfficeInd", namespaces=namespaces)
        if foreign_office_elem is not None and foreign_office_elem.text.strip().upper() == 'X':
            foreign_office = True
    except Exception as e:
        log_error(f"Error during ForeignOfficeInd lookup in {xml_filename}: {e}", exc_info=True)

    comp_pct = (officer_comp / receipt_amt * 100) if receipt_amt > 0 else 0
    travel_pct = ((travel_amt + conferences_amt) / total_exp * 100) if total_exp > 0 else 0
    denominator = (total_assets + receipt_amt) if form_type == "990PF" else receipt_amt
    grift_pct = ((officer_comp + travel_amt + conferences_amt) / denominator * 100) if denominator > 0 else 0
    grift_pct = min(grift_pct, 100.0)
    us_expenses = officer_comp + travel_amt + conferences_amt
    total_relevant_exp = foreign_expenses + us_expenses
    grift_ratio = (us_expenses / total_relevant_exp * 100) if total_relevant_exp > 0 else grift_pct
    external_grants_ratio = (grants_to_others / total_exp * 100) if total_exp > 0 else 0
    domestic_misrep_flag = grift_ratio > 10 and foreign_expenses < 0.1 * total_exp if total_exp > 0 else False

    results = []
    try:
        if denominator > 2_000_000:
            results.append([tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt, org_type, total_exp, prog_exp,
                            travel_amt, conferences_amt, officer_comp, comp_pct, travel_pct, grift_pct, total_assets, form_type,
                            denominator, foreign_office, foreign_expenses, grants_to_others, external_grants_ratio, domestic_misrep_flag])
            file_counter_local.entries += 1
        if (file_counter_local.value % 10000) == 0:
            tqdm.write("Thread %s processed %s files, %s entries, %s skipped" % (
                threading.get_ident(), file_counter_local.value, file_counter_local.entries, file_counter_local.skipped))
    except Exception as e:
        log_error(f"Failed to append result for {xml_filename}: {e}", exc_info=True)
        return []

    return results

def process_zip_files(start_year, end_year, max_workers=4):
    global total_xml_files
    global missing_taxyr_by_year
    global invalid_taxyr_by_year
    global missing_filer_by_year
    global missing_revenue_by_year
    global invalid_predicate_by_year
    global high_conferences_count, high_comp_count
    start_time = time.time()
    year_prefixes = [str(y) for y in range(int(start_year), int(end_year) + 1)]
    results_by_year = defaultdict(list)
    type_counts_by_year = {}
    total_xml_files = 0
    total_processed_files = 0
    total_entries = 0
    debug_entries_by_year = defaultdict(list)

    all_zip_files = []
    for year_prefix in year_prefixes:
        zip_patterns = [f"recompressed_zips/{year_prefix}*.zip", f"{year_prefix}*.zip"]
        for pattern in zip_patterns:
            all_zip_files.extend(glob.glob(pattern))
    total_zip_files = len(all_zip_files)

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
            try:
                with zipfile.ZipFile(zip_file_path, 'r') as zf:
                    xml_files = [f for f in zf.namelist() if f.lower().endswith('.xml')]
                    total_xml_files += len(xml_files)
                    print(f"Found {len(xml_files)} XML files in {zip_file_path}")

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
                    results = future.result()
                    if results:
                        tax_year = results[0][0]
                        results_by_year[tax_year].extend(results)
                        type_counts_by_year[tax_year] = type_counts_by_year.get(tax_year, Counter())
                        type_counts_by_year[tax_year].update([r[6] for r in results])
                        debug_entries_by_year[tax_year].append(results[0])
                        total_entries += len(results)
                except Exception as e:
                    log_error(f"Error processing {xml_file} in {zip_file}: {e}", exc_info=True)
                total_processed_files += 1

    print(f"\nTotal XML files across all ZIPs: {total_xml_files}")
    print(f"Total files processed: {total_processed_files}")
    print(f"Total entries: {total_entries}")
    print(f"Approximately {int(total_processed_files / max_workers)} files per worker")

    print("\nFinal Thread Counts:")
    print("| Thread ID      | Files Processed | Entries | Skipped |")
    print("|----------------|-----------------|---------|---------|")
    for thread_id in set(threading.current_thread().ident for _ in range(max_workers)):
        if hasattr(file_counter_local, 'value'):
            print(f"| {thread_id:<14} | {file_counter_local.value:>15} | {file_counter_local.entries:>7} | {file_counter_local.skipped:>7} |")
            break

    for tax_year, entries in sorted(debug_entries_by_year.items()):
        print(f"\nDebug for Tax Year {tax_year}:")
        for entry in entries[:2]:
            print(f"DEBUG: EIN={entry[1]}, Receipts={entry[3]}, TotalExp={entry[7]}, Travel={entry[9]}, Conferences={entry[10]}, OfficerComp={entry[11]}, Type={entry[6]}, ForeignOffice={entry[18]}, ForeignExpenses={entry[19]}, GrantsToOthers={entry[20]}")
        for entry in entries[-2:]:
            print(f"DEBUG: EIN={entry[1]}, Receipts={entry[3]}, TotalExp={entry[7]}, Travel={entry[9]}, Conferences={entry[10]}, OfficerComp={entry[11]}, Type={entry[6]}, ForeignOffice={entry[18]}, ForeignExpenses={entry[19]}, GrantsToOthers={entry[20]}")

    print("\nCharities per Year:")
    print("| Tax Year | Number of Charities |")
    print("|----------|---------------------|")
    for tax_year, results in sorted(results_by_year.items()):
        print(f"| {tax_year:<8} | {len(results):>19} |")

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

    histogram_template = Template("""
## Org Type ${org_type}
${description}

### ${title} Histogram (0-100%, 100 bins)
| Range         | Count    | Percentile |
|---------------|----------|------------|
% for start, end, count, percentile in data:
| ${start}-${end}% | ${'{:>8}'.format(count)} | ${'{:>10.2f}'.format(percentile)}% |
% endfor
""")

    report_template = Template("""
# Grift Report for Tax Year ${tax_year}

## Top Organizations by Foreign Grift Ratio
| EIN | Name | Type | Grift Ratio (%) | Type Percentile | Rating | External Grants Ratio (%) | Grants Type Percentile | Grants Rating | Domestic Misrepresentation |
|-----|------|------|-----------------|-----------------|--------|---------------------------|------------------------|---------------|---------------------------|
% for org in top_grift:
| ${org['filer_ein']} | ${org['filer_name']} | ${org['org_type']} | ${'{:.2f}'.format(org['grift_ratio'])} | ${'{:.2f}'.format(org['grift_ratio_type_percentile'])} | ${org['grift_rating']} | ${'{:.2f}'.format(org['external_grants_ratio'])} | ${'{:.2f}'.format(org['external_grants_type_percentile'])} | ${org['external_grants_rating']} | ${org['domestic_misrep_flag']} |
% endfor

## Top Organizations by External Grants Ratio
| EIN | Name | Type | Grift Ratio (%) | Type Percentile | Rating | External Grants Ratio (%) | Grants Type Percentile | Grants Rating | Domestic Misrepresentation |
|-----|------|------|-----------------|-----------------|--------|---------------------------|------------------------|---------------|---------------------------|
% for org in top_grants:
| ${org['filer_ein']} | ${org['filer_name']} | ${org['org_type']} | ${'{:.2f}'.format(org['grift_ratio'])} | ${'{:.2f}'.format(org['grift_ratio_type_percentile'])} | ${org['grift_rating']} | ${'{:.2f}'.format(org['external_grants_ratio'])} | ${'{:.2f}'.format(org['external_grants_type_percentile'])} | ${org['external_grants_rating']} | ${org['domestic_misrep_flag']} |
% endfor

## Calculation Methodology
- **Grift Ratio (%)**: Measures domestic spending (officer compensation, travel, and conferences) as a percentage of total relevant expenses (domestic + foreign expenses). For organizations with a foreign office and foreign expenses > $0, calculated as `(officer_comp + travel_amt + conferences_amt) / (foreign_expenses + officer_comp + travel_amt + conferences_amt) * 100`. Otherwise, equals `grift_pct` (domestic spending / denominator * 100, where denominator is receipts for Form 990 or receipts + assets for Form 990PF). Rounded to 2 decimal places.
- **Grift Rating**: Assigned based on `grift_ratio_type_percentile` (percentile rank among organizations of the same type): <50% = A, <70% = B, <85% = C, <95% = D, ≥95% = F. Higher percentiles indicate higher domestic spending, suggesting potential grift.
- **External Grants Ratio (%)**: Measures grants to other organizations or individuals (domestic via Schedule I, foreign via Schedule F) as a percentage of total expenses. Calculated as `grants_to_others / total_exp * 100`. Rounded to 2 decimal places. High ratios may indicate pass-through funding.
- **Grants Rating**: Assigned based on `external_grants_type_percentile` (percentile rank among organizations of the same type): <50% = A, <70% = B, <85% = C, <95% = D, ≥95% = F. Higher percentiles indicate higher grant activity, potentially signaling risk of funds being funneled inappropriately.
- **Domestic Misrepresentation**: Flag set to `True` if `grift_ratio > 10%` and `foreign_expenses < 10% of total_exp`, indicating an organization may be misrepresenting its international focus by spending heavily on domestic activities.
""")

    for tax_year, results in results_by_year.items():
        if results:
            high_conferences_count = 0
            high_comp_count = 0

            grants_to_others_vals = [r[20] for r in results]
            total_exp_vals = [r[7] for r in results]
            external_grants_ratios = [r[21] for r in results]
            non_zero_grants = sum(1 for g in grants_to_others_vals if g > 0)
            print(f"\nStats for Tax Year {tax_year}:")
            print(f"Orgs with grants_to_others > 0: {non_zero_grants}/{len(grants_to_others_vals)}")
            print(f"grants_to_others: min={min(grants_to_others_vals, default=0)}, max={max(grants_to_others_vals, default=0)}, mean={np.mean(grants_to_others_vals) if grants_to_others_vals else 0:.2f}")
            print(f"total_exp: min={min(total_exp_vals, default=0)}, max={max(total_exp_vals, default=0)}, mean={np.mean(total_exp_vals) if total_exp_vals else 0:.2f}")
            print(f"external_grants_ratio: min={min(external_grants_ratios, default=0):.2f}%, max={max(external_grants_ratios, default=0):.2f}%, mean={np.mean(external_grants_ratios) if external_grants_ratios else 0:.2f}%")

            # Deduplicate results by EIN, keeping highest total_exp
            dedup_results = {}
            for r in results:
                ein = r[1]
                if ein not in dedup_results or r[7] > dedup_results[ein][7]:
                    dedup_results[ein] = r
            results = list(dedup_results.values())

            # Generate histograms by org_type in batches
            histogram_report = []
            results_by_type = defaultdict(list)
            for r in results:
                results_by_type[r[6]].append(r)

            batch_size = 10000
            for org_type, type_results in sorted(results_by_type.items()):
                if len(type_results) > 0:
                    description = ORG_TYPE_DESCRIPTIONS.get(org_type, "No description available.")
                    for batch_start in range(0, len(type_results), batch_size):
                        batch_results = type_results[batch_start:batch_start + batch_size]
                        # Officer Compensation % Histogram
                        type_comp_pcts = [r[12] for r in batch_results]
                        if type_comp_pcts:
                            comp_bins, bin_edges = np.histogram(type_comp_pcts, bins=100, range=(0, 100))
                            cumulative_comp = np.cumsum(comp_bins)
                            total_comp = sum(comp_bins)
                            percentiles_comp = (cumulative_comp / total_comp * 100) if total_comp > 0 else np.zeros_like(cumulative_comp)
                            data = []
                            i = 0
                            while i < len(comp_bins):
                                start = i
                                count = comp_bins[i]
                                percentile = percentiles_comp[i]
                                if count == 0:
                                    while i < len(comp_bins) - 1 and comp_bins[i + 1] == 0:
                                        i += 1
                                    end = i + 1
                                else:
                                    end = i + 1
                                    i += 1
                                data.append((f"{start}", f"{end}", count, percentile))
                            try:
                                histogram_output = histogram_template.render(title=f"Officer Compensation % (Tax Year {tax_year})", org_type=org_type, description=description, data=data)
                                histogram_report.append(histogram_output)
                            except MakoException as e:
                                log_error(f"Error rendering histogram for comp_pcts in {tax_year}, {org_type}: {e}", exc_info=True)

                        # Travel and Conferences % Histogram
                        type_travel_pcts = [r[13] for r in batch_results]
                        if type_travel_pcts:
                            travel_bins, bin_edges = np.histogram(type_travel_pcts, bins=100, range=(0, 100))
                            cumulative_travel = np.cumsum(travel_bins)
                            total_travel = sum(travel_bins)
                            percentiles_travel = (cumulative_travel / total_travel * 100) if total_travel > 0 else np.zeros_like(cumulative_travel)
                            data = []
                            i = 0
                            while i < len(travel_bins):
                                start = i
                                count = travel_bins[i]
                                percentile = percentiles_travel[i]
                                if count == 0:
                                    while i < len(travel_bins) - 1 and travel_bins[i + 1] == 0:
                                        i += 1
                                    end = i + 1
                                else:
                                    end = i + 1
                                    i += 1
                                data.append((f"{start}", f"{end}", count, percentile))
                            try:
                                histogram_output = histogram_template.render(title=f"Travel and Conferences % (Tax Year {tax_year})", org_type=org_type, description=description, data=data)
                                histogram_report.append(histogram_output)
                            except MakoException as e:
                                log_error(f"Error rendering histogram for travel_pcts in {tax_year}, {org_type}: {e}", exc_info=True)

                        # Domestic Grift % Histogram
                        type_grift_pcts = [r[14] for r in batch_results]
                        if type_grift_pcts:
                            grift_bins, bin_edges = np.histogram(type_grift_pcts, bins=100, range=(0, 100))
                            cumulative_grift = np.cumsum(grift_bins)
                            total_grift = sum(grift_bins)
                            percentiles_grift = (cumulative_grift / total_grift * 100) if total_grift > 0 else np.zeros_like(cumulative_grift)
                            data = []
                            i = 0
                            while i < len(grift_bins):
                                start = i
                                count = grift_bins[i]
                                percentile = percentiles_grift[i]
                                if count == 0:
                                    while i < len(grift_bins) - 1 and grift_bins[i + 1] == 0:
                                        i += 1
                                    end = i + 1
                                else:
                                    end = i + 1
                                    i += 1
                                data.append((f"{start}", f"{end}", count, percentile))
                            try:
                                histogram_output = histogram_template.render(title=f"Domestic Grift % (Tax Year {tax_year})", org_type=org_type, description=description, data=data)
                                histogram_report.append(histogram_output)
                            except MakoException as e:
                                log_error(f"Error rendering histogram for grift_pcts in {tax_year}, {org_type}: {e}", exc_info=True)

                        # External Grants Ratio % Histogram
                        type_external_grants_ratios = [r[21] for r in batch_results]
                        if type_external_grants_ratios:
                            grants_bins, bin_edges = np.histogram(type_external_grants_ratios, bins=100, range=(0, 100))
                            cumulative_grants = np.cumsum(grants_bins)
                            total_grants = sum(grants_bins)
                            percentiles_grants = (cumulative_grants / total_grants * 100) if total_grants > 0 else np.zeros_like(cumulative_grants)
                            data = []
                            i = 0
                            while i < len(grants_bins):
                                start = i
                                count = grants_bins[i]
                                percentile = percentiles_grants[i]
                                if count == 0:
                                    while i < len(grants_bins) - 1 and grants_bins[i + 1] == 0:
                                        i += 1
                                    end = i + 1
                                else:
                                    end = i + 1
                                    i += 1
                                data.append((f"{start}", f"{end}", count, percentile))
                            try:
                                histogram_output = histogram_template.render(title=f"External Grants Ratio % (Tax Year {tax_year})", org_type=org_type, description=description, data=data)
                                histogram_report.append(histogram_output)
                            except MakoException as e:
                                log_error(f"Error rendering histogram for external_grants_ratios in {tax_year}, {org_type}: {e}", exc_info=True)

            with open(f"histogram_report_{tax_year}.md", "w", encoding="utf-8") as md_file:
                md_file.write("\n\n".join(histogram_report))
            print(f"Wrote histogram report to histogram_report_{tax_year}.md")

            output_tsv = f"charities_{tax_year}.tsv"
            with open(output_tsv, mode="w", newline="", encoding="utf-8") as tsvfile:
                writer = csv.writer(tsvfile, delimiter='\t')
                writer.writerow([
                    "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
                    "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "travel_pct",
                    "grift_pct", "grift_pct_percentile", "grift_pct_rating", "grift_ratio", "grift_ratio_percentile",
                    "grift_ratio_rating", "external_grants_ratio", "external_grants_percentile", "external_grants_rating",
                    "grift_pct_type_percentile", "grift_ratio_type_percentile", "external_grants_type_percentile",
                    "total_assets", "form_type", "denominator", "foreign_office", "foreign_expenses", "grants_to_others",
                    "domestic_misrep_flag"
                ])

                # Cache percentiles for all orgs
                comp_pcts = np.array([r[12] for r in results], dtype=np.float32)
                travel_pcts = np.array([r[13] for r in results], dtype=np.float32)
                grift_pcts = np.array([r[14] for r in results], dtype=np.float32)
                grift_ratios = np.array([r[14] if r[18] else r[14] for r in results], dtype=np.float32)
                external_grants_ratios = np.array([r[21] for r in results], dtype=np.float32)
                total_ngos = len(comp_pcts)

                # Cache type-specific percentiles
                results_by_type = defaultdict(list)
                for r in tqdm(results, desc=f"Grouping results by type for {tax_year}"):
                    results_by_type[r[6]].append(r)

                type_percentiles = {}
                for org_type, type_results in tqdm(results_by_type.items(), desc=f"Computing type percentiles for {tax_year}"):
                    type_total_ngos = len(type_results)
                    if type_total_ngos > 0:
                        type_comp_pcts = np.fromiter((r[12] for r in type_results), dtype=np.float32, count=type_total_ngos)
                        type_travel_pcts = np.fromiter((r[13] for r in type_results), dtype=np.float32, count=type_total_ngos)
                        type_grift_pcts = np.fromiter((r[14] for r in type_results), dtype=np.float32, count=type_total_ngos)
                        type_grift_ratios = np.fromiter((r[14] if r[18] else r[14] for r in type_results), dtype=np.float32, count=type_total_ngos)
                        type_external_grants_ratios = np.fromiter((r[21] for r in type_results), dtype=np.float32, count=type_total_ngos)
                        type_percentiles[org_type] = (type_grift_pcts, type_grift_ratios, type_external_grants_ratios, type_total_ngos)

                for batch_start in range(0, len(results), batch_size):
                    batch_results = results[batch_start:batch_start + batch_size]
                    for result in tqdm(batch_results, desc=f"Writing TSV batch for {tax_year}"):
                        tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt, org_type, total_exp, prog_exp, \
                        travel_amt, conferences_amt, officer_comp, comp_pct, travel_pct, grift_pct, total_assets, form_type, \
                        denominator, foreign_office, foreign_expenses, grants_to_others, external_grants_ratio, domestic_misrep_flag = result

                        if denominator > 2_000_000 and "501(c)" in org_type:
                            grift_pct_percentile = 100 * (1 - np.sum(grift_pcts < grift_pct) / total_ngos) if total_ngos > 0 else 0
                            grift_pct_rating = assign_grift_rating(grift_pct_percentile)
                            if foreign_office and foreign_expenses > 0:
                                grift_ratio = (officer_comp + travel_amt + conferences_amt) / (foreign_expenses + officer_comp + travel_amt + conferences_amt) * 100
                                grift_ratio_percentile = 100 * (1 - np.sum(grift_ratios < grift_ratio) / total_ngos) if total_ngos > 0 else 0
                            else:
                                grift_ratio = grift_pct
                                grift_ratio_percentile = grift_pct_percentile
                            grift_ratio_rating = assign_grift_rating(grift_ratio_percentile)
                            external_grants_percentile = 100 * (1 - np.sum(external_grants_ratios < external_grants_ratio) / total_ngos) if total_ngos > 0 else 0
                            external_grants_rating = assign_grift_rating(external_grants_percentile)

                            if org_type in type_percentiles:
                                type_grift_pcts, type_grift_ratios, type_external_grants_ratios, type_total_ngos = type_percentiles[org_type]
                                grift_pct_type_percentile = 100 * (1 - np.sum(type_grift_pcts < grift_pct) / type_total_ngos) if type_total_ngos > 0 else 0
                                grift_ratio_type_percentile = 100 * (1 - np.sum(type_grift_ratios < grift_ratio) / type_total_ngos) if type_total_ngos > 0 else 0
                                external_grants_type_percentile = 100 * (1 - np.sum(type_external_grants_ratios < external_grants_ratio) / type_total_ngos) if type_total_ngos > 0 else 0
                            else:
                                grift_pct_type_percentile = grift_ratio_type_percentile = external_grants_type_percentile = 0

                            receipt_amt = round(receipt_amt, 2)
                            govt_amt = round(govt_amt, 2)
                            contrib_amt = round(contrib_amt, 2)
                            total_exp = round(total_exp, 2)
                            prog_exp = round(prog_exp, 2)
                            travel_amt = round(travel_amt, 2)
                            conferences_amt = round(conferences_amt, 2)
                            officer_comp = round(officer_comp, 2)
                            total_assets = round(total_assets, 2)
                            foreign_expenses = round(foreign_expenses, 2)
                            grants_to_others = round(grants_to_others, 2)
                            denominator = round(denominator, 2)

                            comp_pct = round(comp_pct, 2)
                            travel_pct = round(travel_pct, 2)
                            grift_pct = round(grift_pct, 2)
                            grift_pct_percentile = round(grift_pct_percentile, 2)
                            grift_ratio = round(grift_ratio, 2)
                            grift_ratio_percentile = round(grift_ratio_percentile, 2)
                            external_grants_ratio = round(external_grants_ratio, 2)
                            external_grants_percentile = round(external_grants_percentile, 2)
                            grift_pct_type_percentile = round(grift_pct_type_percentile, 2)
                            grift_ratio_type_percentile = round(grift_ratio_type_percentile, 2)
                            external_grants_type_percentile = round(external_grants_type_percentile, 2)

                            writer.writerow([
                                tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt, org_type, total_exp,
                                prog_exp, travel_amt, conferences_amt, officer_comp, comp_pct, travel_pct, grift_pct,
                                grift_pct_percentile, grift_pct_rating, grift_ratio, grift_ratio_percentile, grift_ratio_rating,
                                external_grants_ratio, external_grants_percentile, external_grants_rating,
                                grift_pct_type_percentile, grift_ratio_type_percentile, external_grants_type_percentile,
                                total_assets, form_type, denominator, foreign_office, foreign_expenses, grants_to_others,
                                domestic_misrep_flag
                            ])

                            if officer_comp > 500_000 and high_comp_count < 5:
                                log_error(f"Potential high grift org: EIN {filer_ein}, Name {filer_name}, OfficerComp ${officer_comp}, GriftRatio {grift_ratio:.2f}%, GriftPercentile {grift_ratio_percentile:.2f}%, GrantsRatio {external_grants_ratio:.2f}%")
                print(f"Wrote entries to {output_tsv}")

                # Generate Markdown report in batches
                grift_candidates = []
                type_percentiles_cache = type_percentiles
                for batch_start in range(0, len(results), batch_size):
                    batch_results = results[batch_start:batch_start + batch_size]
                    for result in tqdm(batch_results, desc=f"Generating grift report batch for {tax_year}"):
                        tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt, org_type, total_exp, prog_exp, \
                        travel_amt, conferences_amt, officer_comp, comp_pct, travel_pct, grift_pct, total_assets, form_type, \
                        denominator, foreign_office, foreign_expenses, grants_to_others, external_grants_ratio, domestic_misrep_flag = result

                        if denominator > 2_000_000 and "501(c)" in org_type:
                            grift_pct_percentile = 100 * (1 - np.sum(grift_pcts < grift_pct) / total_ngos) if total_ngos > 0 else 0
                            grift_pct_rating = assign_grift_rating(grift_pct_percentile)
                            if foreign_office and foreign_expenses > 0:
                                grift_ratio = (officer_comp + travel_amt + conferences_amt) / (foreign_expenses + officer_comp + travel_amt + conferences_amt) * 100
                                grift_ratio_percentile = 100 * (1 - np.sum(grift_ratios < grift_ratio) / total_ngos) if total_ngos > 0 else 0
                            else:
                                grift_ratio = grift_pct
                                grift_ratio_percentile = grift_pct_percentile
                            grift_ratio_rating = assign_grift_rating(grift_ratio_percentile)
                            external_grants_percentile = 100 * (1 - np.sum(external_grants_ratios < external_grants_ratio) / total_ngos) if total_ngos > 0 else 0
                            external_grants_rating = assign_grift_rating(external_grants_percentile)

                            if org_type in type_percentiles_cache:
                                type_grift_pcts, type_grift_ratios, type_external_grants_ratios, type_total_ngos = type_percentiles_cache[org_type]
                                grift_pct_type_percentile = 100 * (1 - np.sum(type_grift_pcts < grift_pct) / type_total_ngos) if type_total_ngos > 0 else 0
                                grift_ratio_type_percentile = 100 * (1 - np.sum(type_grift_ratios < grift_ratio) / type_total_ngos) if type_total_ngos > 0 else 0
                                external_grants_type_percentile = 100 * (1 - np.sum(type_external_grants_ratios < external_grants_ratio) / type_total_ngos) if type_total_ngos > 0 else 0
                            else:
                                grift_pct_type_percentile = grift_ratio_type_percentile = external_grants_type_percentile = 0

                            grift_candidates.append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'org_type': org_type,
                                'grift_ratio': round(grift_ratio, 2),
                                'grift_rating': assign_grift_rating(grift_ratio_type_percentile),
                                'external_grants_ratio': round(external_grants_ratio, 2),
                                'external_grants_rating': assign_grift_rating(external_grants_type_percentile),
                                'grift_ratio_percentile': round(grift_ratio_percentile, 2),
                                'external_grants_percentile': round(external_grants_percentile, 2),
                                'grift_ratio_type_percentile': round(grift_ratio_type_percentile, 2),
                                'external_grants_type_percentile': round(external_grants_type_percentile, 2),
                                'domestic_misrep_flag': domestic_misrep_flag
                            })

                if grift_candidates:
                    top_grift = sorted(grift_candidates, key=lambda x: x['grift_ratio_type_percentile'], reverse=True)[:10]
                    top_grants = sorted(grift_candidates, key=lambda x: x['external_grants_type_percentile'], reverse=True)[:10]
                    try:
                        report_output = report_template.render(tax_year=tax_year, top_grift=top_grift, top_grants=top_grants)
                        with open(f"grift_report_{tax_year}.md", "w", encoding="utf-8") as md_file:
                            md_file.write(report_output)
                        print(f"Wrote grift report to grift_report_{tax_year}.md")
                    except MakoException as e:
                        log_error(f"Error rendering Mako template for tax year {tax_year}: {e}", exc_info=True)
                        print(f"Failed to generate grift_report_{tax_year}.md due to template error")

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
    parser = argparse.ArgumentParser(description="List all 501(c) orgs with TSV outputs for grift ratings.")
    parser.add_argument("start_year", type=valid_tax_year, help="Start year for ZIP prefixes (e.g., 2016).")
    parser.add_argument("end_year", type=valid_tax_year, help="End year for ZIP prefixes (e.g., 2024).")

    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise argparse.ArgumentError(None, "Start year must be less than or equal to end year.")

    process_zip_files(str(args.start_year), str(args.end_year), max_workers=4)