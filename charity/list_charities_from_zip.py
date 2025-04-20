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
            int(tax_year)  # Validate that tax_year is a valid integer
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

    # Increment file counter after passing checks
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
        "irs:GrossReceiptsAmt",  # Form 990
        "irs:TotalRevenueAmt",  # Form 990-EZ
        "irs:CYTotalRevenueAmt",  # Form 990 current year
        "irs:RevenueAndExpensesPerBooksAmt",  # Form 990-PF
        "irs:TotalRevenue",  # Generic fallback
        "irs:AnalysisOfRevenueAndExpenses/irs:TotalRevAndExpnssAmt",  # Form 990-PF
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
            if total_exp == 0:  # Fallback
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
    travel_pct = (travel_amt / total_exp * 100) if total_exp > 0 else 0
    comp_travel_pct = ((officer_comp + travel_amt) / receipt_amt * 100) if receipt_amt > 0 else 0

    results = []
    try:
        results.append((tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt, org_type,
                        total_exp, prog_exp, travel_amt, officer_comp, comp_pct, travel_pct, comp_travel_pct, prog_exp == 0, filer_address, avg_hours))
        file_counter_local.entries += 1  # Increment entries only after successful append
        # Log progress after processing the file
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
    comp_travel_pcts = []
    totals = {"receipt_amt": 0, "govt_amt": 0, "contrib_amt": 0, "total_exp": 0, "prog_exp": 0, "travel_amt": 0, "officer_comp": 0}
    total_xml_files = 0
    total_processed_files = 0
    total_entries = 0
    debug_entries_by_year = defaultdict(list)

    # Count total ZIP files for the log
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
                        tax_year = results[0][0]  # Tax year from the XML
                        results_by_year[tax_year].extend(results)
                        type_counts_by_year[tax_year] = type_counts_by_year.get(tax_year, Counter())
                        type_counts_by_year[tax_year].update([r[6] for r in results])
                        # Collect debug entries
                        debug_entries_by_year[tax_year].append(results[0])
                        for r in results:
                            totals["receipt_amt"] += r[3]
                            totals["govt_amt"] += r[4]
                            totals["contrib_amt"] += r[5]
                            totals["total_exp"] += r[7]
                            totals["prog_exp"] += r[8]
                            totals["travel_amt"] += r[9]
                            totals["officer_comp"] += r[10]
                            comp_pcts.append(r[11])
                            travel_pcts.append(r[12])
                            comp_travel_pcts.append(r[13])
                        total_entries += len(results)
                except Exception as e:
                    log_error(f"Error processing {xml_file} in {zip_file}: {e}", exc_info=True)
                total_processed_files += 1

    print(f"\nTotal XML files across all ZIPs: {total_xml_files}")
    print(f"Total files processed: {total_processed_files}")
    print(f"Total entries: {total_entries}")
    print(f"Approximately {int(total_processed_files / max_workers)} files per worker")

    # Print final counts for each thread
    print("\nFinal Thread Counts:")
    print("| Thread ID      | Files Processed | Entries | Skipped |")
    print("|----------------|-----------------|---------|---------|")
    for thread_id in set(threading.current_thread().ident for _ in range(max_workers)):
        if hasattr(file_counter_local, 'value'):
            print(f"| {thread_id:<14} | {file_counter_local.value:>15} | {file_counter_local.entries:>7} | {file_counter_local.skipped:>7} |")
            break  # Since file_counter_local is thread-local, we only need one instance per thread context

    # Print debug logs for first 2 and last 2 entries per year
    for tax_year, entries in sorted(debug_entries_by_year.items()):
        print(f"\nDebug for Tax Year {tax_year}:")
        # First 2 entries
        for entry in entries[:2]:
            print(f"DEBUG: EIN={entry[1]}, Receipts={entry[3]}, TotalExp={entry[7]}, Travel={entry[9]}, OfficerComp={entry[10]}, Type={entry[6]}")
        # Last 2 entries
        for entry in entries[-2:]:
            print(f"DEBUG: EIN={entry[1]}, Receipts={entry[3]}, TotalExp={entry[7]}, Travel={entry[9]}, OfficerComp={entry[10]}, Type={entry[6]}")

    # Generate table of charities per year
    print("\nCharities per Year:")
    print("| Tax Year | Number of Charities |")
    print("|----------|---------------------|")
    for tax_year, results in sorted(results_by_year.items()):
        print(f"| {tax_year:<8} | {len(results):>19} |")

    # Generate error summary table
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
            output_csv = f"charities_{tax_year}.csv"
            with open(output_csv, mode="w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
                                 "total_exp", "prog_exp", "travel_amt", "officer_comp", "comp_pct", "travel_pct", "comp_travel_pct",
                                 "no_prog_exp", "address", "avg_hours_per_week"])
                for row in results:
                    writer.writerow(row)
            print(f"Wrote {len(results)} entries to {output_csv}")

            # Compute percentiles
            comp_pcts = [r[11] for r in results]
            travel_pcts = [r[12] for r in results]
            comp_travel_pcts = [r[13] for r in results]
            total_ngos = len(comp_pcts)
            comp_pcts = np.array(comp_pcts)
            travel_pcts = np.array(travel_pcts)
            comp_travel_pcts = np.array(comp_travel_pcts)

            # Grift detection
            grift_candidates = []
            for result in results:
                tax_year, filer_ein, filer_name, receipt_amt, gov_grants, _, org_type, total_exp, prog_exp, travel_amt, officer_comp, comp_pct, travel_pct, comp_travel_pct, no_prog_exp, address, avg_hours = result
                if comp_pct > 10 or travel_pct > 10 or (gov_grants > 1_000_000 and tax_year == "2021"):
                    comp_pct_percentile = 100 * (1 - np.sum(comp_pcts < comp_pct) / total_ngos)
                    comp_pct_rank = np.sum(comp_pcts >= comp_pct)
                    travel_pct_percentile = 100 * (1 - np.sum(travel_pcts < travel_pct) / total_ngos)
                    travel_pct_rank = np.sum(travel_pcts >= travel_pct)
                    comp_travel_pct_percentile = 100 * (1 - np.sum(comp_travel_pcts < comp_travel_pct) / total_ngos)
                    comp_travel_pct_rank = np.sum(comp_travel_pcts >= comp_travel_pct)
                    grift_candidates.append((
                        tax_year, filer_ein, filer_name, comp_pct, travel_pct, gov_grants, officer_comp, total_exp, org_type,
                        receipt_amt, prog_exp, no_prog_exp, address, avg_hours,
                        comp_pct_percentile, comp_pct_rank, travel_pct_percentile, travel_pct_rank,
                        comp_travel_pct_percentile, comp_travel_pct_rank
                    ))
            if grift_candidates:
                with open(f"grift_candidates_{tax_year}.csv", mode="w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow([
                        "tax_year", "filer_ein", "filer_name", "comp_pct", "travel_pct", "GovernmentGrantsAmt", "officer_comp",
                        "total_exp", "org_type", "receipt_amt", "prog_exp", "no_prog_exp", "address", "avg_hours_per_week",
                        "comp_pct_percentile", "comp_pct_rank", "travel_pct_percentile", "travel_pct_rank",
                        "comp_travel_pct_percentile", "comp_travel_pct_rank"
                    ])
                    for candidate in grift_candidates:
                        writer.writerow(candidate)
                print(f"Wrote {len(grift_candidates)} grift candidates to grift_candidates_{tax_year}.csv")
                top_offenders = sorted(grift_candidates, key=lambda x: x[3], reverse=True)[:5]
                print(f"Top 5 grift candidates for {tax_year} (by comp_pct):")
                for cand in top_offenders:
                    print(f"  EIN: {cand[1]}, Name: {cand[2]}, Comp%: {cand[3]:.2f} (Top {cand[14]:.2f}%, Rank {cand[15]}/{total_ngos}), "
                          f"Travel%: {cand[4]:.2f} (Top {cand[16]:.2f}%, Rank {cand[17]}/{total_ngos}), "
                          f"Comp+Travel%: {cand[4] + cand[3]:.2f} (Top {cand[18]:.2f}%, Rank {cand[19]}/{total_ngos}), Grants: {cand[5]}")

    # Recovery estimation for 2021
    if "2021" in results_by_year:
        df = pd.read_csv("grift_candidates_2021.csv")
        reasonable_comp_pct = np.percentile(df["comp_pct"], 90)
        total_ngos = len(df)

        def estimate_recovery(row):
            recovery = 0
            # Private Inurement
            reasonable_comp = (reasonable_comp_pct / 100) * row["receipt_amt"]
            excess_comp = max(0, row["officer_comp"] - reasonable_comp)
            excise_tax = 0.25 * excess_comp + 2.0 * excess_comp
            back_taxes = 0.21 * row["receipt_amt"]
            recovery += excise_tax + back_taxes
            # Unreported Income
            unreported_travel = max(0, row["travel_amt"] - 50_000)
            income_tax = 0.35 * unreported_travel
            penalties = 0.20 * income_tax + 0.75 * income_tax
            interest = 0.05 * income_tax
            recovery += income_tax + penalties + interest
            # Shell Org Fraud
            if row["GovernmentGrantsAmt"] > 1_000_000 and row["prog_exp"] < 0.1 * row["total_exp"]:
                unreported = row["GovernmentGrantsAmt"] - row["prog_exp"]
                tax = 0.21 * unreported
                penalties = 0.75 * tax
                recovery += tax + penalties
            return recovery

        df["estimated_recovery"] = df.apply(estimate_recovery, axis=1)
        top_10 = df.sort_values("estimated_recovery", ascending=False).head(10)
        top_10.to_csv("grift_candidates_2021_top_10.csv", index=False)
        print("Top 10 NGOs by estimated recovery for 2021:")
        print(top_10[["filer_ein", "filer_name", "comp_pct", "GovernmentGrantsAmt", "estimated_recovery", "officer_comp", "travel_amt", "prog_exp"]])

    # Histogram and totals
    if comp_pcts:
        comp_bins, bin_edges = np.histogram(comp_pcts, bins=20, range=(0, 100))
        print("\nOfficer Compensation % Histogram (0-100%, 20 bins):")
        print("| Range     | Count    |")
        print("|-----------|----------|")
        for i, count in enumerate(comp_bins):
            lower = i * 5
            upper = (i + 1) * 5
            print(f"| {lower:3d}-{upper:3d}% | {count:>8} |")

    if travel_pcts:
        travel_bins, bin_edges = np.histogram(travel_pcts, bins=20, range=(0, 100))
        print("\nTravel Expenses % Histogram (0-100%, 20 bins):")
        print("| Range     | Count    |")
        print("|-----------|----------|")
        for i, count in enumerate(travel_bins):
            lower = i * 5
            upper = (i + 1) * 5
            print(f"| {lower:3d}-{upper:3d}% | {count:>8} |")

    if comp_travel_pcts:
        comp_travel_bins, bin_edges = np.histogram(comp_travel_pcts, bins=20, range=(0, 100))
        print("\nOfficer Comp + Travel % Histogram (0-100%, 20 bins):")
        print("| Range     | Count    |")
        print("|-----------|----------|")
        for i, count in enumerate(comp_travel_bins):
            lower = i * 5
            upper = (i + 1) * 5
            print(f"| {lower:3d}-{upper:3d}% | {count:>8} |")

    if comp_pcts and travel_pcts and comp_travel_pcts:
        comp_threshold = np.percentile(comp_pcts, 90)
        travel_threshold = np.percentile(travel_pcts, 90)
        comp_travel_threshold = np.percentile(comp_travel_pcts, 90)
        print(f"\nSuggested thresholds (90th percentile):")
        print(f"  Officer Comp %: {comp_threshold:.2f}%")
        print(f"  Travel %: {travel_threshold:.2f}%")
        print(f"  Officer Comp + Travel %: {comp_travel_threshold:.2f}%")

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
        if 2019 <= year <= 2025:
            return year
        else:
            raise argparse.ArgumentTypeError("Tax year must be between 2019 and 2025 (inclusive).")
    except ValueError:
        raise argparse.ArgumentTypeError("Tax year must be an integer.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List all 501(c) orgs with namespace fix, EIN dict, and Markdown.")
    parser.add_argument("start_year", type=valid_tax_year, help="Start year for ZIP prefixes (e.g., 2019).")
    parser.add_argument("end_year", type=valid_tax_year, help="End year for ZIP prefixes (e.g., 2025).")

    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise argparse.ArgumentError(None, "Start year must be less than or equal to end year.")

    process_zip_files(str(args.start_year), str(args.end_year), max_workers=8)