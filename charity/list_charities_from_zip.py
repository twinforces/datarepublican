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

total_xml_files = 0
file_counter_local = threading.local()
ein_type_dict = {}
missing_taxyr_by_year = defaultdict(int)
invalid_taxyr_by_year = defaultdict(int)
missing_filer_by_year = defaultdict(int)
missing_revenue_by_year = defaultdict(int)
invalid_predicate_by_year = defaultdict(int)

# Set up logging to a file
logging.basicConfig(
    filename='error_log.txt',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - Line %(lineno)d - File %(filename)s - %(message)s'
)

def log_error(message, exc_info=False):
    logging.error(message, exc_info=exc_info)

def assign_grift_rating(percentile):
    """Assign a grift rating based on grift_pct_percentile."""
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

def parse_xml_file(xml_content, xml_filename, zip_prefix):
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

    # Determine form type
    form_type_elem = root.find(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces=namespaces)
    form_type = form_type_elem.text.strip() if form_type_elem is not None else "Unknown"

    # Attempt to get TaxYr, with fallbacks
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

    filer_address_elem = root.find(".//irs:Filer/irs:USAddress", namespaces=namespaces)
    filer_address = ""
    if filer_address_elem is not None:
        address_line = filer_address_elem.find("irs:AddressLine1Txt", namespaces=namespaces)
        city = filer_address_elem.find("irs:CityNm", namespaces=namespaces)
        state = filer_address_elem.find("irs:StateAbbreviationCd", namespaces=namespaces)
        zip_code = filer_address_elem.find("irs:ZIPCd", namespaces=namespaces)
        filer_address = ", ".join([x.text.strip() for x in [address_line, city, state, zip_code] if x is not None and x.text])

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

    if conferences_amt > 1_000_000:
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

    avg_hours = 0
    if form_type == "990PF":
        try:
            officer = root.find(".//irs:OfficerDirTrstKeyEmplGrp", namespaces=namespaces)
            if officer is not None:
                hours_elem = officer.find("irs:AverageHrsPerWkDevotedToPosRt", namespaces=namespaces)
                if hours_elem is not None and hours_elem.text.strip():
                    try:
                        avg_hours = float(hours_elem.text.strip())
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid AverageHrsPerWkDevotedToPosRt value: {hours_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during OfficerDirTrstKeyEmplGrp lookup for avg_hours in {xml_filename}: {e}", exc_info=True)
    else:
        try:
            for person in root.findall(".//irs:Form990PartVIISectionAGrp", namespaces=namespaces):
                hours_elem = person.find("irs:AverageHoursPerWeekRt", namespaces=namespaces)
                if hours_elem is not None and hours_elem.text.strip():
                    try:
                        avg_hours += float(hours_elem.text.strip())
                    except (ValueError, TypeError) as e:
                        log_error(f"Invalid AverageHoursPerWeekRt value: {hours_elem.text} in {xml_filename}, error: {e}", exc_info=True)
        except Exception as e:
            log_error(f"Error during AverageHoursPerWeekRt lookup in {xml_filename}: {e}", exc_info=True)

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

    comp_pct = (officer_comp / receipt_amt * 100) if receipt_amt > 0 else 0
    travel_pct = ((travel_amt + conferences_amt) / total_exp * 100) if total_exp > 0 else 0
    denominator = (total_assets + receipt_amt) if form_type == "990PF" else receipt_amt
    grift_pct = ((officer_comp + travel_amt + conferences_amt) / denominator * 100) if denominator > 0 else 0
    grift_pct = min(grift_pct, 100.0)  # Cap at 100% to avoid outliers
    if grift_pct > 100.0:
        log_error(f"High grift_pct {grift_pct:.2f}% for EIN {filer_ein}, Name {filer_name}, Denominator {denominator}, OfficerComp {officer_comp}, Travel {travel_amt}, Conferences {conferences_amt}")

    results = []
    try:
        results.append((tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt, org_type,
                        total_exp, prog_exp, travel_amt, conferences_amt, officer_comp, comp_pct, travel_pct, grift_pct,
                        prog_exp == 0, filer_address, avg_hours, total_assets, form_type, denominator))
        file_counter_local.entries += 1
        if (file_counter_local.value % 10000) == 0:
            tqdm.write("Thread %s processed %s files, %s entries, %s skipped" % (
                threading.get_ident(), file_counter_local.value, file_counter_local.entries, file_counter_local.skipped))
    except Exception as e:
        log_error(f"Failed to append result for {xml_filename}: {e}", exc_info=True)
        return []

    return results

def process_zip_files(start_year, end_year, max_workers=8):
    global total_xml_files
    global missing_taxyr_by_year
    global invalid_taxyr_by_year
    global missing_filer_by_year
    global missing_revenue_by_year
    global invalid_predicate_by_year
    start_time = time.time()
    year_prefixes = [str(y) for y in range(int(start_year), int(end_year) + 1)]
    results_by_year = defaultdict(list)
    type_counts_by_year = {}
    comp_pcts = []
    travel_pcts = []
    grift_pcts = []
    totals = {"receipt_amt": 0, "govt_amt": 0, "contrib_amt": 0, "total_exp": 0, "prog_exp": 0, "travel_amt": 0, "conferences_amt": 0, "officer_comp": 0, "total_assets": 0}
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
                        for r in results:
                            totals["receipt_amt"] += r[3]
                            totals["govt_amt"] += r[4]
                            totals["contrib_amt"] += r[5]
                            totals["total_exp"] += r[7]
                            totals["prog_exp"] += r[8]
                            totals["travel_amt"] += r[9]
                            totals["conferences_amt"] += r[10]
                            totals["officer_comp"] += r[11]
                            totals["total_assets"] += r[18]
                            comp_pcts.append(r[12])
                            travel_pcts.append(r[13])
                            grift_pcts.append(r[14])
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
            print(f"DEBUG: EIN={entry[1]}, Receipts={entry[3]}, TotalExp={entry[7]}, Travel={entry[9]}, Conferences={entry[10]}, OfficerComp={entry[11]}, Type={entry[6]}")
        for entry in entries[-2:]:
            print(f"DEBUG: EIN={entry[1]}, Receipts={entry[3]}, TotalExp={entry[7]}, Travel={entry[9]}, Conferences={entry[10]}, OfficerComp={entry[11]}, Type={entry[6]}")

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

    for tax_year, results in results_by_year.items():
        if results:
            output_tsv = f"grift_ratings_{tax_year}.tsv"
            comp_pcts = [r[12] for r in results]
            travel_pcts = [r[13] for r in results]
            grift_pcts = [r[14] for r in results]
            total_ngos = len(comp_pcts)
            comp_pcts = np.array(comp_pcts)
            travel_pcts = np.array(travel_pcts)
            grift_pcts = np.array(grift_pcts)

            grift_candidates = []
            for result in results:
                tax_year, filer_ein, filer_name, receipt_amt, gov_grants, _, org_type, total_exp, prog_exp, travel_amt, \
                conferences_amt, officer_comp, comp_pct, travel_pct, grift_pct, no_prog_exp, address, avg_hours, total_assets, form_type, denominator = result
                if denominator > 2_000_000:
                    grift_pct_percentile = 100 * (1 - np.sum(grift_pcts < grift_pct) / total_ngos) if total_ngos > 0 else 0
                    grift_pct_rank = np.sum(grift_pcts >= grift_pct)
                    comp_pct_percentile = 100 * (1 - np.sum(comp_pcts < comp_pct) / total_ngos) if total_ngos > 0 else 0
                    comp_pct_rank = np.sum(comp_pcts >= comp_pct)
                    travel_pct_percentile = 100 * (1 - np.sum(travel_pcts < travel_pct) / total_ngos) if total_ngos > 0 else 0
                    travel_pct_rank = np.sum(travel_pcts >= travel_pct)
                    grift_candidates.append((
                        filer_ein, filer_name, org_type, grift_pct, grift_pct_percentile, assign_grift_rating(grift_pct_percentile),
                        travel_amt, conferences_amt, officer_comp, total_exp, prog_exp, denominator
                    ))

            if grift_candidates:
                with open(output_tsv, mode="w", newline="", encoding="utf-8") as tsvfile:
                    writer = csv.writer(tsvfile, delimiter='\t')
                    writer.writerow([
                        "filer_ein", "filer_name", "org_type", "grift_pct", "grift_pct_percentile", "grift_rating",
                        "travel_amt", "conferences_amt", "officer_comp", "total_exp", "prog_exp", "denominator"
                    ])
                    for candidate in sorted(grift_candidates, key=lambda x: x[4], reverse=True):
                        writer.writerow(candidate)
                print(f"Wrote {len(grift_candidates)} grift ratings to {output_tsv}")
                top_offenders = sorted(grift_candidates, key=lambda x: x[4], reverse=True)[:5]
                print(f"Top 5 grift ratings for {tax_year} (by grift_pct_percentile):")
                for cand in top_offenders:
                    print(f"  EIN: {cand[0]}, Name: {cand[1]}, Type: {cand[2]}, Grift%: {cand[3]:.2f}, "
                          f"Percentile: {cand[4]:.2f}%, Rating: {cand[5]}, Travel: ${cand[6]}, Conferences: ${cand[7]}")

    if len(comp_pcts) > 0:
        comp_bins, bin_edges = np.histogram(comp_pcts, bins=100, range=(0, 100))
        cumulative_comp = np.cumsum(comp_bins)
        total_comp = sum(comp_bins)
        percentiles_comp = (cumulative_comp / total_comp * 100) if total_comp > 0 else np.zeros_like(cumulative_comp)
        print("\nOfficer Compensation % Histogram (0-100%, 100 bins):")
        print("| Range     | Count    | Percentile |")
        print("|-----------|----------|------------|")
        for i, (count, percentile) in enumerate(zip(comp_bins, percentiles_comp)):
            lower = i
            upper = i + 1
            print(f"| {lower:3d}-{upper:3d}% | {count:>8} | {percentile:>10.2f}% |")

    if len(travel_pcts) > 0:
        travel_bins, bin_edges = np.histogram(travel_pcts, bins=100, range=(0, 100))
        cumulative_travel = np.cumsum(travel_bins)
        total_travel = sum(travel_bins)
        percentiles_travel = (cumulative_travel / total_travel * 100) if total_travel > 0 else np.zeros_like(cumulative_travel)
        print("\nTravel and Conferences % Histogram (0-100%, 100 bins):")
        print("| Range     | Count    | Percentile |")
        print("|-----------|----------|------------|")
        for i, (count, percentile) in enumerate(zip(travel_bins, percentiles_travel)):
            lower = i
            upper = i + 1
            print(f"| {lower:3d}-{upper:3d}% | {count:>8} | {percentile:>10.2f}% |")

    if len(grift_pcts) > 0:
        grift_bins, bin_edges = np.histogram(grift_pcts, bins=100, range=(0, 100))
        cumulative_grift = np.cumsum(grift_bins)
        total_grift = sum(grift_bins)
        percentiles_grift = (cumulative_grift / total_grift * 100) if total_grift > 0 else np.zeros_like(cumulative_grift)
        print("\nGrift % Histogram (0-100%, 100 bins):")
        print("| Range     | Count    | Percentile |")
        print("|-----------|----------|------------|")
        for i, (count, percentile) in enumerate(zip(grift_bins, percentiles_grift)):
            lower = i
            upper = i + 1
            print(f"| {lower:3d}-{upper:3d}% | {count:>8} | {percentile:>10.2f}% |")

    if len(comp_pcts) > 0 and len(travel_pcts) > 0 and len(grift_pcts) > 0:
        comp_threshold = np.percentile(comp_pcts, 90)
        travel_threshold = np.percentile(travel_pcts, 90)
        grift_threshold = np.percentile(grift_pcts, 90)
        print(f"\nSuggested thresholds (90th percentile):")
        print(f"  Officer Comp %: {comp_threshold:.2f}%")
        print(f"  Travel and Conferences %: {travel_threshold:.2f}%")
        print(f"  Grift %: {grift_threshold:.2f}%")

    print("\nTotals Across All Organizations:")
    print("| Metric             | Total Value    |")
    print("|--------------------|----------------|")
    for key, value in totals.items():
        print(f"| {key:<18} | {value:>14} |")

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
    parser = argparse.ArgumentParser(description="List all 501(c) orgs with namespace fix, EIN dict, and TSV grift ratings.")
    parser.add_argument("start_year", type=valid_tax_year, help="Start year for ZIP prefixes (e.g., 2016).")
    parser.add_argument("end_year", type=valid_tax_year, help="End year for ZIP prefixes (e.g., 2024).")

    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise argparse.ArgumentError(None, "Start year must be less than or equal to end year.")

    process_zip_files(str(args.start_year), str(args.end_year), max_workers=8)