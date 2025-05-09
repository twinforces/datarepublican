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
    """Log errors with full context, limiting non-critical logs."""
    global high_conferences_count, high_comp_count, parsed_ein_count, grant_log_count, error_log_count, foreign_exp_log_count, chai_amnesty_grant_count
    if any(x in message for x in ["Thread", "Opening TSV", "Wrote row", "Closed and flushed", "Assigned tax_year"]) and not verbose:
        logging.info(message)
        return
    if "Parsed EIN" in message and parsed_ein_count >= 5 and not any(ein in message for ein in ["271414646", "520851555"]):
        return
    if "grants_to_others" in message and grant_log_count >= 5 and not any(ein in message for ein in ["271414646", "520851555"]):
        return
    if "High conferences_amt" in message and high_conferences_count >= 5:
        return
    if any(x in message for x in ["Error", "Missing", "Invalid", "Unparsed"]) and error_log_count >= 5:
        logging.error(message, exc_info=exc_info)
        error_log_count += 1
        return
    if "RegionTotalExpendituresAmt" in message and foreign_exp_log_count >= 5 and not any(ein in message for ein in ["271414646", "520851555"]):
        return
    if any(ein in message for ein in ["271414646", "520851555"]) and "Grant" in message and chai_amnesty_grant_count[message.split("EIN ")[1].split(",")[0]] >= 5:
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
    if any(ein in message for ein in ["271414646", "520851555"]) and "Grant" in message:
        ein = message.split("EIN ")[1].split(",")[0]
        chai_amnesty_grant_count[ein] += 1

def clean_org_type(org_type):
    """Map organization type to clean file name format."""
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
            writer.writerow([
                "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
                "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "travel_pct",
                "conferences_pct", "grants_pct", "foreign_exp_pct", "grift_ratio", "total_assets", "form_type",
                "denominator", "foreign_office", "foreign_expenses", "grants_to_others", "domestic_misrep_flag"
            ])
            tsv_files[tsv_key].flush()
            log_error(f"Wrote header to new TSV {tsv_path}")
        else:
            reader = csv.reader(tsv_files[tsv_key], delimiter='\t')
            next(reader, None)  # Skip header
            first_data_row = next(reader, None)
            tsv_files[tsv_key].seek(0)
            if first_data_row and first_data_row[0] == str(row[0]) and first_data_row[1] == row[1]:
                tsv_files[tsv_key].truncate(0)
                writer.writerow([
                    "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
                    "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "travel_pct",
                    "conferences_pct", "grants_pct", "foreign_exp_pct", "grift_ratio", "total_assets", "form_type",
                    "denominator", "foreign_office", "foreign_expenses", "grants_to_others", "domestic_misrep_flag"
                ])
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

    form_type_elem = root.find(".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}ReturnTypeCd")
    if form_type_elem is None:
        form_type_elem = root.find(".//ReturnHeader/ReturnTypeCd")
    form_type = form_type_elem.text.strip() if form_type_elem is not None else "Unknown"
    if form_type == "990T":
        file_counter_local.skipped += 1
        return []

    tax_year_elem = root.find(".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}TaxYr")
    if tax_year_elem is None:
        tax_year_elem = root.find(".//ReturnHeader/TaxYr")
    if tax_year_elem is None:
        missing_taxyr_by_year["Unknown"] += 1
        log_error(f"Missing TaxYr element in {xml_filename}, inferring from filename/zip")
        tax_year = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix
    else:
        tax_year = tax_year_elem.text.strip()
        try:
            int(tax_year)
        except ValueError:
            invalid_taxyr_by_year[tax_year] += 1
            log_error(f"Invalid tax year {tax_year} in {xml_filename}, inferring from filename/zip")
            tax_year = xml_filename[:4] if xml_filename[:4].isdigit() else zip_prefix

    filer = root.find(".//{http://www.irs.gov/efile}Filer")
    if filer is None:
        filer = root.find(".//Filer")
    if filer is None:
        missing_filer_by_year[tax_year] += 1
        log_error(f"Missing Filer element in {xml_filename}")
        filer_ein = "Unknown"
        filer_name = "Unknown"
    else:
        filer_ein_elem = filer.find("{http://www.irs.gov/efile}EIN") if filer.find("{http://www.irs.gov/efile}EIN") is not None else filer.find("EIN")
        filer_name_elem = filer.find(".//{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1Txt") if \
                          filer.find(".//{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1Txt") is not None else \
                          filer.find(".//BusinessName/BusinessNameLine1Txt") if \
                          filer.find(".//BusinessName/BusinessNameLine1Txt") is not None else \
                          filer.find(".//{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1") if \
                          filer.find(".//{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1") is not None else \
                          filer.find(".//BusinessName/BusinessNameLine1")
        filer_ein = filer_ein_elem.text.strip() if filer_ein_elem is not None else "Unknown"
        filer_name = filer_name_elem.text.strip() if filer_name_elem is not None else "Unknown"

    if filer_ein in ["271414646", "520851555"] or parsed_ein_count < 5:
        log_error(f"Assigned tax_year {tax_year} for {xml_filename}, EIN {filer_ein}")

    file_counter_local.value += 1

    receipt_amt = 0
    for revenue_field in [
        "{http://www.irs.gov/efile}GrossReceiptsAmt",
        "{http://www.irs.gov/efile}TotalRevenueAmt",
        "{http://www.irs.gov/efile}CYTotalRevenueAmt",
        "{http://www.irs.gov/efile}RevenueAndExpensesPerBooksAmt",
        "{http://www.irs.gov/efile}TotalRevenue",
        "{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}TotalRevAndExpnssAmt",
        "GrossReceiptsAmt",
        "TotalRevenueAmt",
        "CYTotalRevenueAmt",
        "RevenueAndExpensesPerBooksAmt",
        "TotalRevenue",
        "AnalysisOfRevenueAndExpenses/TotalRevAndExpnssAmt"
    ]:
        try:
            receipt_elem = root.find(f".//{revenue_field}", namespaces)
            if receipt_elem is not None:
                try:
                    receipt_amt = int(float(receipt_elem.text.strip()))
                    break
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid value in {revenue_field} in {xml_filename}, error: {e}", exc_info=True)
                    receipt_amt = 0
                    break
        except Exception as e:
            log_error(f"Error during {revenue_field} lookup in {xml_filename}: {e}", exc_info=True)
            continue
    else:
        missing_revenue_by_year[tax_year] += 1
        log_error(f"Missing revenue fields in {xml_filename}")

    total_assets = 0
    try:
        assets_amt = None
        assets_eoy = None
        assets_elem = root.find(".//{http://www.irs.gov/efile}TotalAssetsAmt", namespaces) if \
                      root.find(".//{http://www.irs.gov/efile}TotalAssetsAmt", namespaces) is not None else \
                      root.find(".//TotalAssetsAmt")
        if assets_elem is not None:
            try:
                assets_amt = int(float(assets_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid TotalAssetsAmt value in {xml_filename}, error: {e}", exc_info=True)
        assets_elem = root.find(".//{http://www.irs.gov/efile}TotalAssetsEOYAmt", namespaces) if \
                      root.find(".//{http://www.irs.gov/efile}TotalAssetsEOYAmt", namespaces) is not None else \
                      root.find(".//TotalAssetsEOYAmt")
        if assets_elem is not None:
            try:
                assets_eoy = int(float(assets_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid TotalAssetsEOYAmt value in {xml_filename}, error: {e}", exc_info=True)
        total_assets = max(assets_amt or 0, assets_eoy or 0)
    except Exception as e:
        log_error(f"Error during TotalAssets lookup in {xml_filename}: {e}", exc_info=True)

    govt_amt = 0
    try:
        grant_elem = root.find(".//{http://www.irs.gov/efile}GovernmentGrantsAmt", namespaces) if \
                     root.find(".//{http://www.irs.gov/efile}GovernmentGrantsAmt", namespaces) is not None else \
                     root.find(".//GovernmentGrantsAmt")
        if grant_elem is not None:
            try:
                govt_amt = int(float(grant_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid GovernmentGrantsAmt value in {xml_filename}, error: {e}", exc_info=True)
                govt_amt = 0
    except Exception as e:
        log_error(f"Error during GovernmentGrantsAmt lookup in {xml_filename}: {e}", exc_info=True)

    contrib_amt = 0
    try:
        contrib_elem = root.find(".//{http://www.irs.gov/efile}AllOtherContributionsAmt", namespaces) if \
                       root.find(".//{http://www.irs.gov/efile}AllOtherContributionsAmt", namespaces) is not None else \
                       root.find(".//AllOtherContributionsAmt")
        if contrib_elem is not None:
            try:
                contrib_amt = int(float(contrib_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid AllOtherContributionsAmt value in {xml_filename}, error: {e}", exc_info=True)
                contrib_amt = 0
    except Exception as e:
        log_error(f"Error during AllOtherContributionsAmt lookup in {xml_filename}: {e}", exc_info=True)

    total_exp = 0
    if form_type == "990PF":
        try:
            total_exp_elem = root.find(".//{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}TotalExpensesRevAndExpnssAmt", namespaces) if \
                             root.find(".//{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}TotalExpensesRevAndExpnssAmt", namespaces) is not None else \
                             root.find(".//AnalysisOfRevenueAndExpenses/TotalExpensesRevAndExpnssAmt")
            if total_exp_elem is not None:
                try:
                    total_exp = int(float(total_exp_elem.text.strip()))
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid TotalExpensesRevAndExpnssAmt value in {xml_filename}, error: {e}", exc_info=True)
                    total_exp = 0
        except Exception as e:
            log_error(f"Error during TotalExpensesRevAndExpnssAmt lookup in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            total_exp_elem = root.find(".//{http://www.irs.gov/efile}TotalFunctionalExpensesGrp/{http://www.irs.gov/efile}TotalAmt", namespaces) if \
                             root.find(".//{http://www.irs.gov/efile}TotalFunctionalExpensesGrp/{http://www.irs.gov/efile}TotalAmt", namespaces) is not None else \
                             root.find(".//TotalFunctionalExpensesGrp/TotalAmt")
            if total_exp_elem is not None:
                try:
                    total_exp = int(float(total_exp_elem.text.strip()))
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid TotalFunctionalExpensesGrp value in {xml_filename}, error: {e}", exc_info=True)
                    total_exp = 0
            if total_exp == 0:
                total_exp_elem = root.find(".//{http://www.irs.gov/efile}TotalExpensesAmt", namespaces) if \
                                 root.find(".//{http://www.irs.gov/efile}TotalExpensesAmt", namespaces) is not None else \
                                 root.find(".//TotalExpensesAmt")
                if total_exp_elem is not None:
                    try:
                        total_exp = int(float(total_exp_elem.text.strip()))
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid TotalExpensesAmt value in {xml_filename}, error: {e}", exc_info=True)
                        total_exp = 0
        except Exception as e:
            log_error(f"Error during TotalExpenses lookup in {xml_filename}: {e}", exc_info=True)

    prog_exp = 0
    try:
        prog_exp_elem = root.find(".//{http://www.irs.gov/efile}TotalProgramServiceExpensesAmt", namespaces) if \
                        root.find(".//{http://www.irs.gov/efile}TotalProgramServiceExpensesAmt", namespaces) is not None else \
                        root.find(".//TotalProgramServiceExpensesAmt")
        if prog_exp_elem is not None:
            try:
                prog_exp = int(float(prog_exp_elem.text.strip()))
            except (ValueError, TypeError) as e:
                log_error(f"Invalid TotalProgramServiceExpensesAmt value in {xml_filename}, error: {e}", exc_info=True)
                prog_exp = 0
    except Exception as e:
        log_error(f"Error during TotalProgramServiceExpensesAmt lookup in {xml_filename}: {e}", exc_info=True)

    travel_amt = 0
    if form_type == "990PF":
        try:
            other_exp_schedule = root.find(".//{http://www.irs.gov/efile}OtherExpensesSchedule", namespaces) if \
                                 root.find(".//{http://www.irs.gov/efile}OtherExpensesSchedule", namespaces) is not None else \
                                 root.find(".//OtherExpensesSchedule")
            if other_exp_schedule is not None:
                for expense in (other_exp_schedule.findall("{http://www.irs.gov/efile}OtherExpensesScheduleGrp", namespaces) or
                                other_exp_schedule.findall("OtherExpensesScheduleGrp")):
                    desc_elem = expense.find("{http://www.irs.gov/efile}Desc", namespaces) if \
                                expense.find("{http://www.irs.gov/efile}Desc", namespaces) is not None else \
                                expense.find("Desc")
                    if desc_elem is not None and "TRAVEL" in desc_elem.text.upper():
                        amount_elem = expense.find("{http://www.irs.gov/efile}RevenueAndExpensesPerBooksAmt", namespaces) if \
                                      expense.find("{http://www.irs.gov/efile}RevenueAndExpensesPerBooksAmt", namespaces) is not None else \
                                      expense.find("RevenueAndExpensesPerBooksAmt")
                        if amount_elem is not None:
                            try:
                                travel_amt = int(float(amount_elem.text.strip()))
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid travel amount in OtherExpensesSchedule in {xml_filename}, error: {e}", exc_info=True)
                                travel_amt = 0
                            break
        except Exception as e:
            log_error(f"Error during OtherExpensesSchedule lookup for travel in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            travel_elem = root.find(".//{http://www.irs.gov/efile}TravelGrp/{http://www.irs.gov/efile}TotalAmt", namespaces) if \
                          root.find(".//{http://www.irs.gov/efile}TravelGrp/{http://www.irs.gov/efile}TotalAmt", namespaces) is not None else \
                          root.find(".//TravelGrp/TotalAmt")
            if travel_elem is not None:
                try:
                    travel_amt = int(float(travel_elem.text.strip()))
                except ( =
                    log_error(f"Invalid TravelGrp value in {xml_filename}, error: {e}", exc_info=True)
                    travel_amt = 0
        except Exception as e:
            log_error(f"Error during TravelGrp lookup in {xml_filename}: {e}", exc_info=True)

    conferences_amt = 0
    if form_type == "990PF":
        try:
            other_exp_schedule = root.find(".//{http://www.irs.gov/efile}OtherExpensesSchedule", namespaces) if \
                                 root.find(".//{http://www.irs.gov/efile}OtherExpensesSchedule", namespaces) is not None else \
                                 root.find(".//OtherExpensesSchedule")
            if other_exp_schedule is not None:
                for expense in (other_exp_schedule.findall("{http://www.irs.gov/efile}OtherExpensesScheduleGrp", namespaces) or
                                other_exp_schedule.findall("OtherExpensesScheduleGrp")):
                    desc_elem = expense.find("{http://www.irs.gov/efile}Desc", namespaces) if \
                                expense.find("{http://www.irs.gov/efile}Desc", namespaces) is not None else \
                                expense.find("Desc")
                    if desc_elem is not None and ("CONFERENCE" in desc_elem.text.upper() or "MEETING" in desc_elem.text.upper()):
                        amount_elem = expense.find("{http://www.irs.gov/efile}RevenueAndExpensesPerBooksAmt", namespaces) if \
                                      expense.find("{http://www.irs.gov/efile}RevenueAndExpensesPerBooksAmt", namespaces) is not None else \
                                      expense.find("RevenueAndExpensesPerBooksAmt")
                        if amount_elem is not None:
                            try:
                                conferences_amt += int(float(amount_elem.text.strip()))
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid conference amount in OtherExpensesSchedule in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during OtherExpensesSchedule lookup for conferences in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            conferences_elem = root.find(".//{http://www.irs.gov/efile}ConferencesMeetingsGrp/{http://www.irs.gov/efile}TotalAmt", namespaces) if \
                               root.find(".//{http://www.irs.gov/efile}ConferencesMeetingsGrp/{http://www.irs.gov/efile}TotalAmt", namespaces) is not None else \
                               root.find(".//ConferencesMeetingsGrp/TotalAmt")
            if conferences_elem is not None:
                try:
                    conferences_amt = int(float(conferences_elem.text.strip()))
                except (ValueError, TypeError) as e:
                    log_error(f"Invalid ConferencesMeetingsGrp value in {xml_filename}, error: {e}", exc_info=True)
                    conferences_amt = 0
        except Exception as e:
            log_error(f"Error during ConferencesMeetingsGrp lookup in {xml_filename}: {e}", exc_info=True)

    if conferences_amt > 1_000_000 and high_conferences_count < 5:
        log_error(f"High conferences_amt ${conferences_amt} for EIN {filer_ein}, Name {filer_name}, TaxYear {tax_year}, XML {xml_filename}")

    officer_comp = 0
    if form_type == "990PF":
        try:
            officer = root.find(".//{http://www.irs.gov/efile}OfficerDirTrstKeyEmplGrp", namespaces) if \
                      root.find(".//{http://www.irs.gov/efile}OfficerDirTrstKeyEmplGrp", namespaces) is not None else \
                      root.find(".//OfficerDirTrstKeyEmplGrp")
            if officer is not None:
                comp_elem = officer.find("{http://www.irs.gov/efile}CompensationAmt", namespaces) if \
                            officer.find("{http://www.irs.gov/efile}CompensationAmt", namespaces) is not None else \
                            officer.find("CompensationAmt")
                if comp_elem is not None and comp_elem.text.strip():
                    try:
                        officer_comp = int(float(comp_elem.text.strip()))
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid CompensationAmt value in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during OfficerDirTrstKeyEmplGrp lookup in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            for person in (root.findall(".//{http://www.irs.gov/efile}Form990PartVIISectionAGrp", namespaces) or
                           root.findall(".//Form990PartVIISectionAGrp")) + \
                          (root.findall(".//{http://www.irs.gov/efile}OfficerDirectorTrusteeEmplGrp", namespaces) or
                           root.findall(".//OfficerDirectorTrusteeEmplGrp")):
                comp_elem = person.find("{http://www.irs.gov/efile}ReportableCompFromOrgAmt", namespaces) if \
                            person.find("{http://www.irs.gov/efile}ReportableCompFromOrgAmt", namespaces) is not None else \
                            person.find("ReportableCompFromOrgAmt") if \
                            person.find("ReportableCompFromOrgAmt") is not None else \
                            person.find("{http://www.irs.gov/efile}CompensationAmt", namespaces) if \
                            person.find("{http://www.irs.gov/efile}CompensationAmt", namespaces) is not None else \
                            person.find("CompensationAmt")
                if comp_elem is not None and comp_elem.text.strip():
                    try:
                        officer_comp += int(float(comp_elem.text.strip()))
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid ReportableCompFromOrgAmt/CompensationAmt value in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during Form990PartVIISectionAGrp/OfficerDirectorTrusteeEmplGrp lookup in {xml_filename}: {e}", exc_info=True)

    grants_to_others = 0
    try:
        return_data = root.find(".//{http://www.irs.gov/efile}ReturnData", namespaces) if \
                      root.find(".//{http://www.irs.gov/efile}ReturnData", namespaces) is not None else \
                      root.find(".//ReturnData")
        if return_data is not None:
            schedule_f_paths = [
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990ScheduleF",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}ScheduleF",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990/{http://www.irs.gov/efile}IRS990ScheduleF",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990EZ/{http://www.irs.gov/efile}IRS990ScheduleF",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990/{http://www.irs.gov/efile}ScheduleF",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990EZ/{http://www.irs.gov/efile}ScheduleF",
                ".//ReturnData/IRS990ScheduleF",
                ".//ReturnData/ScheduleF",
                ".//ReturnData/IRS990/IRS990ScheduleF",
                ".//ReturnData/IRS990EZ/IRS990ScheduleF",
                ".//ReturnData/IRS990/ScheduleF",
                ".//ReturnData/IRS990EZ/ScheduleF"
            ]
            for path in schedule_f_paths:
                for schedule_f in root.findall(path, namespaces):
                    for grant in (schedule_f.findall(".//{http://www.irs.gov/efile}GrantsToOrgOutsideUSGrp", namespaces) or
                                  schedule_f.findall(".//GrantsToOrgOutsideUSGrp")) + \
                                 (schedule_f.findall(".//{http://www.irs.gov/efile}GrantsToOrganizationsOutsideUS", namespaces) or
                                  schedule_f.findall(".//GrantsToOrganizationsOutsideUS")) + \
                                 (schedule_f.findall(".//{http://www.irs.gov/efile}GrantsToOrgsOutsideUS", namespaces) or
                                  schedule_f.findall(".//GrantsToOrgsOutsideUS")):
                        amount_elem = grant.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) if \
                                      grant.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) is not None else \
                                      grant.find("CashGrantAmt")
                        if amount_elem is not None:
                            try:
                                amount = int(float(amount_elem.text.strip()))
                                grants_to_others += amount
                                if filer_ein in ["271414646", "520851555"]:
                                    log_error(f"{'CHAI' if filer_ein == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleF GrantsToOrg for EIN {filer_ein}, File {xml_filename}")
                                elif amount > 5_000_000 and grant_log_count < 5:
                                    log_error(f"Found CashGrantAmt ${amount} in ScheduleF GrantsToOrg for EIN {filer_ein}, File {xml_filename}")
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid CashGrantAmt in ScheduleF: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                    for grant in (schedule_f.findall(".//{http://www.irs.gov/efile}ForeignIndividualsGrantsGrp", namespaces) or
                                  schedule_f.findall(".//ForeignIndividualsGrantsGrp")):
                        amount_elem = grant.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) if \
                                      grant.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) is not None else \
                                      grant.find("CashGrantAmt")
                        if amount_elem is not None:
                            try:
                                amount = int(float(amount_elem.text.strip()))
                                grants_to_others += amount
                                if filer_ein in ["271414646", "520851555"]:
                                    log_error(f"{'CHAI' if filer_ein == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleF ForeignIndividuals for EIN {filer_ein}, File {xml_filename}")
                                elif amount > 5_000_000 and grant_log_count < 5:
                                    log_error(f"Found CashGrantAmt ${amount} in ScheduleF ForeignIndividuals for EIN {filer_ein}, File {xml_filename}")
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid CashGrantAmt in ScheduleF ForeignIndividuals: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
            schedule_i_paths = [
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990ScheduleI",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}ScheduleI",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990/{http://www.irs.gov/efile}IRS990ScheduleI",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990EZ/{http://www.irs.gov/efile}IRS990ScheduleI",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990/{http://www.irs.gov/efile}ScheduleI",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990EZ/{http://www.irs.gov/efile}ScheduleI",
                ".//ReturnData/IRS990ScheduleI",
                ".//ReturnData/ScheduleI",
                ".//ReturnData/IRS990/IRS990ScheduleI",
                ".//ReturnData/IRS990EZ/IRS990ScheduleI",
                ".//ReturnData/IRS990/ScheduleI",
                ".//ReturnData/IRS990EZ/ScheduleI"
            ]
            for path in schedule_i_paths:
                for schedule_i in root.findall(path, namespaces):
                    for recipient in (schedule_i.findall(".//{http://www.irs.gov/efile}RecipientTable", namespaces) or
                                      schedule_i.findall(".//RecipientTable")):
                        amount_elem = recipient.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) if \
                                      recipient.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) is not None else \
                                      recipient.find("CashGrantAmt")
                        if amount_elem is not None:
                            try:
                                amount = int(float(amount_elem.text.strip()))
                                grants_to_others += amount
                                if filer_ein in ["271414646", "520851555"]:
                                    log_error(f"{'CHAI' if filer_ein == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleI RecipientTable for EIN {filer_ein}, File {xml_filename}")
                                elif amount > 5_000_000 and grant_log_count < 5:
                                    log_error(f"Found CashGrantAmt ${amount} in ScheduleI RecipientTable for EIN {filer_ein}, File {xml_filename}")
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid CashGrantAmt in ScheduleI RecipientTable: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
                    for individual_grant in (schedule_i.findall(".//{http://www.irs.gov/efile}GrantsOtherAsstToIndivInUSGrp", namespaces) or
                                             schedule_i.findall(".//GrantsOtherAsstToIndivInUSGrp")):
                        amount_elem = individual_grant.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) if \
                                      individual_grant.find("{http://www.irs.gov/efile}CashGrantAmt", namespaces) is not None else \
                                      individual_grant.find("CashGrantAmt")
                        if amount_elem is not None:
                            try:
                                amount = int(float(amount_elem.text.strip()))
                                grants_to_others += amount
                                if filer_ein in ["271414646", "520851555"]:
                                    log_error(f"{'CHAI' if filer_ein == '271414646' else 'Amnesty'} Grant: ${amount} in ScheduleI GrantsOtherAsstToIndivInUSGrp for EIN {filer_ein}, File {xml_filename}")
                                elif amount > 5_000_000 and grant_log_count < 5:
                                    log_error(f"Found CashGrantAmt ${amount} in ScheduleI GrantsOtherAsstToIndivInUSGrp for EIN {filer_ein}, File {xml_filename}")
                            except (ValueError, TypeError) as e:
                                log_error(f"Invalid CashGrantAmt in ScheduleI GrantsOtherAsstToIndivInUSGrp: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
            if grants_to_others > 5_000_000 or filer_ein in ["271414646", "520851555"]:
                log_error(f"Non-zero grants_to_others ${grants_to_others} for EIN {filer_ein}, Name {filer_name}, TaxYear {tax_year}, XML {xml_filename}")
            elif grants_to_others == 0 and filer_ein in ["271414646", "520851555"]:
                children = root.findall(".//{http://www.irs.gov/efile}ReturnData/*", namespaces) or root.findall(".//ReturnData/*")
                child_tags = [child.tag for child in children]
                log_error(f"Zero grants_to_others for EIN {filer_ein}, Name {filer_name}, File {xml_filename}. ReturnData children: {child_tags}")
    except Exception as e:
        log_error(f"Error during ScheduleF/I lookup for grants in {xml_filename}: {e}", exc_info=True)

    foreign_expenses = 0
    try:
        if return_data is not None:
            for schedule_f in (root.findall(".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990ScheduleF", namespaces) or
                               root.findall(".//ReturnData/IRS990ScheduleF")) + \
                              (root.findall(".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}ScheduleF", namespaces) or
                               root.findall(".//ReturnData/ScheduleF")) + \
                              (root.findall(".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990/{http://www.irs.gov/efile}IRS990ScheduleF", namespaces) or
                               root.findall(".//ReturnData/IRS990/IRS990ScheduleF")):
                for activity in (schedule_f.findall(".//{http://www.irs.gov/efile}StmtOfActyOutsdUSGrp", namespaces) or
                                 schedule_f.findall(".//StmtOfActyOutsdUSGrp")) + \
                                (schedule_f.findall(".//{http://www.irs.gov/efile}AccountActivitiesOutsideUSGrp", namespaces) or
                                 schedule_f.findall(".//AccountActivitiesOutsideUSGrp")):
                    amount_elem = activity.find("{http://www.irs.gov/efile}RegionTotalExpendituresAmt", namespaces) if \
                                  activity.find("{http://www.irs.gov/efile}RegionTotalExpendituresAmt", namespaces) is not None else \
                                  activity.find("RegionTotalExpendituresAmt")
                    if amount_elem is not None:
                        try:
                            amount = int(float(amount_elem.text.strip()))
                            foreign_expenses += amount
                            if filer_ein in ["271414646", "520851555"] or (amount > 5_000_000 and foreign_exp_log_count < 5):
                                log_error(f"Found RegionTotalExpendituresAmt ${amount} in ScheduleF for EIN {filer_ein}, File {xml_filename}")
                        except (ValueError, TypeError) as e:
                            log_error(f"Invalid RegionTotalExpendituresAmt in ScheduleF: {amount_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        if foreign_expenses == 0 and filer_ein in ["271414646", "520851555"]:
            children = root.findall(".//{http://www.irs.gov/efile}ReturnData/*", namespaces) or root.findall(".//ReturnData/*")
            child_tags = [child.tag for child in children]
            log_error(f"Zero foreign_expenses for EIN {filer_ein}, Name {filer_name}, File {xml_filename}. ReturnData children: {child_tags}")
    except Exception as e:
        log_error(f"Error during ScheduleF lookup for foreign expenses in {xml_filename}: {e}", exc_info=True)

    org_type = "Unknown"
    org_type_debug = "Not found"
    if form_type == "990PF":
        try:
            org_type_elem = None
            for path in [
                ".//{http://www.irs.gov/efile}Organization501c3ExemptPFInd",
                ".//Organization501c3ExemptPFInd",
                ".//{http://www.irs.gov/efile}IRS990PF/{http://www.irs.gov/efile}Organization501c3ExemptPFInd",
                ".//IRS990PF/Organization501c3ExemptPFInd",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990PF/{http://www.irs.gov/efile}Organization501c3ExemptPFInd",
                ".//ReturnData/IRS990PF/Organization501c3ExemptPFInd",
                ".//{http://www.irs.gov/efile}Organization501c3TaxablePFInd",
                ".//Organization501c3TaxablePFInd",
                ".//{http://www.irs.gov/efile}IRS990PF/{http://www.irs.gov/efile}Organization501c3TaxablePFInd",
                ".//IRS990PF/Organization501c3TaxablePFInd",
                ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990PF/{http://www.irs.gov/efile}Organization501c3TaxablePFInd",
                ".//ReturnData/IRS990PF/Organization501c3TaxablePFInd"
            ]:
                org_type_elem = root.find(path, namespaces)
                if org_type_elem is not None and org_type_elem.text.strip().upper() == 'X':
                    org_type = "501(c)(3)"
                    org_type_debug = ET.tostring(org_type_elem, encoding='unicode', method='xml').strip()
                    ein_type_dict[filer_ein] = org_type
                    break
            if org_type == "Unknown":
                for path in [
                    ".//{http://www.irs.gov/efile}Organization4947a1NotExemptCharitableTrustInd",
                    ".//Organization4947a1NotExemptCharitableTrustInd",
                    ".//{http://www.irs.gov/efile}IRS990PF/{http://www.irs.gov/efile}Organization4947a1NotExemptCharitableTrustInd",
                    ".//IRS990PF/Organization4947a1NotExemptCharitableTrustInd",
                    ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990PF/{http://www.irs.gov/efile}Organization4947a1NotExemptCharitableTrustInd",
                    ".//ReturnData/IRS990PF/Organization4947a1NotExemptCharitableTrustInd",
                    ".//{http://www.irs.gov/efile}Organization4947a1Ind",
                    ".//Organization4947a1Ind",
                    ".//{http://www.irs.gov/efile}IRS990PF/{http://www.irs.gov/efile}Organization4947a1Ind",
                    ".//IRS990PF/Organization4947a1Ind"
                ]:
                    org_type_elem = root.find(path, namespaces)
                    if org_type_elem is not None and org_type_elem.text.strip().upper() == 'X':
                        org_type = "4947(a)(1)"
                        org_type_debug = ET.tostring(org_type_elem, encoding='unicode', method='xml').strip()
                        ein_type_dict[filer_ein] = org_type
                        break
            if org_type == "Unknown" and filer_ein in ein_type_dict:
                org_type = ein_type_dict[filer_ein]
            if org_type == "Unknown":
                return_data = root.find(".//{http://www.irs.gov/efile}ReturnData", namespaces) or root.find(".//ReturnData")
                org_type_debug = ET.tostring(return_data, encoding='unicode', method='xml')[:2000] if return_data is not None else "No ReturnData"
                log_error(f"Unparsed org type for 990PF in {xml_filename}, EIN {filer_ein}: {org_type_debug}")
        except Exception as e:
            log_error(f"Error during Organization501c3ExemptPFInd/4947a1 lookup in {xml_filename}, EIN {filer_ein}: {e}", exc_info=True)
    else:
        try:
            org_type_elem = root.find(".//{http://www.irs.gov/efile}Organization501cInd", namespaces) if \
                            root.find(".//{http://www.irs.gov/efile}Organization501cInd", namespaces) is not None else \
                            root.find(".//Organization501cInd")
            if org_type_elem is not None:
                type_num = org_type_elem.get("organization501cTypeTxt")
                org_type_debug = ET.tostring(org_type_elem, encoding='unicode', method='xml').strip()
                if type_num and org_type_elem.text.strip().upper() == 'X' and type_num.isdigit() and 1 <= int(type_num) <= 29:
                    org_type = f"501(c)({type_num})"
                    ein_type_dict[filer_ein] = org_type
                else:
                    log_error(f"Unparsed org type in {xml_filename}, EIN {filer_ein}: {org_type_debug}")
            else:
                org_type_elem = root.find(".//{http://www.irs.gov/efile}Organization501c3Ind", namespaces) if \
                                root.find(".//{http://www.irs.gov/efile}Organization501c3Ind", namespaces) is not None else \
                                root.find(".//Organization501c3Ind")
                if org_type_elem is not None and org_type_elem.text.strip().upper() == 'X':
                    org_type = "501(c)(3)"
                    org_type_debug = ET.tostring(org_type_elem, encoding='unicode', method='xml').strip()
                    ein_type_dict[filer_ein] = org_type
                elif filer_ein in ein_type_dict:
                    org_type = ein_type_dict[filer_ein]
                else:
                    return_data = root.find(".//{http://www.irs.gov/efile}ReturnData", namespaces) or root.find(".//ReturnData")
                    org_type_debug = ET.tostring(return_data, encoding='unicode', method='xml')[:2000] if return_data is not None else "No ReturnData"
                    log_error(f"Unparsed org type in {xml_filename}, EIN {filer_ein}: No Organization501cInd or Organization501c3Ind, XML: {org_type_debug}")
        except Exception as e:
            log_error(f"Error during Organization type lookup in {xml_filename}, EIN {filer_ein}: {e}", exc_info=True)

    foreign_office = False
    try:
        foreign_office_elem = root.find(".//{http://www.irs.gov/efile}ForeignOfficeInd", namespaces) if \
                              root.find(".//{http://www.irs.gov/efile}ForeignOfficeInd", namespaces) is not None else \
                              root.find(".//ForeignOfficeInd")
        if foreign_office_elem is not None and foreign_office_elem.text.strip().upper() == 'X':
            foreign_office = True
    except Exception as e:
        log_error(f"Error during ForeignOfficeInd lookup in {xml_filename}: {e}", exc_info=True)

    comp_pct = (officer_comp / total_exp * 100) if total_exp > 0 else 0
    travel_pct = (travel_amt / total_exp * 100) if total_exp > 0 else 0
    conferences_pct = (conferences_amt / total_exp * 100) if total_exp > 0 else 0
    grants_pct = (grants_to_others / total_exp * 100) if total_exp > 0 else 0
    foreign_exp_pct = (foreign_expenses / total_exp * 100) if total_exp > 0 else 0
    grift_ratio = ((officer_comp + travel_amt + conferences_amt) / total_exp * 100) if total_exp > 0 else 0
    denominator = (total_assets + receipt_amt) if form_type == "990PF" else receipt_amt
    domestic_misrep_flag = grift_ratio > 10 and foreign_expenses < 0.1 * total_exp if total_exp > 0 else False

    if parsed_ein_count < 5 or filer_ein in ["271414646", "520851555"]:
        log_error(f"Parsed EIN {filer_ein}: denominator {denominator}, receipt_amt {receipt_amt}, total_assets {total_assets}, org_type {org_type}, grants_to_others {grants_to_others}, XML {xml_filename}")

    results = []
    try:
        row = [
            tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt, org_type, total_exp, prog_exp,
            travel_amt, conferences_amt, officer_comp, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_exp_pct,
            grift_ratio, total_assets, form_type, denominator, foreign_office, foreign_expenses, grants_to_others,
            domestic_misrep_flag
        ]
        results.append(row)
        file_counter_local.entries += 1
        if (file_counter_local.value % 10000) == 0:
            log_error(f"Thread {threading.get_ident()} processed {file_counter_local.value} files, {file_counter_local.entries} entries, {file_counter_local.skipped} skipped")
    except Exception as e:
        log_error(f"Failed to append result for {xml_filename}, EIN {filer_ein}: {e}", exc_info=True)
        return []

    return results, org_type, xml_filename

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