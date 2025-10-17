# get_latest.py
import os
import glob
import argparse
import logging
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import time
from collections import defaultdict
import queue
from logging.handlers import QueueHandler, QueueListener
import psutil
from lxml import etree
from io import BytesIO
import csv
import re
from countryCodes import lookupCC
import extract_utils as cu

# Constants
TSV_COLUMNS = [
    "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
    "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "comp_ptile",
    "travel_pct", "travel_ptile", "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
    "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio", "total_assets", "form_type",
    "denominator", "foreign_office", "foreign_expenses", "grants_to_others", "domestic_misrep_flag",
    "xml_name", "grift"
]
GRANT_COLUMNS = ["filer_ein", "filer_name", "grant_ein", "grant_amt", "tax_year"]
BACKFILL_COLUMNS = ["grant_ein", "name", "canonical_address", "po_box", "zip_code"]
NAMESPACES = {'irs': 'http://www.irs.gov/efile'}
CSV_QUOTE_FIELDS = {
    'charity': ['filer_name', 'org_type', "form_type", 'xml_name', 'foreign_office', 'domestic_misrep_flag'],
    'grants': ['filer_name', 'grant_ein'],
    'backfill': ['name', 'canonical_address', 'po_box', 'zip_code']
}

# Global variables
logger = None
backfill_entries = []
backfill_lock = threading.Lock()
seen_backfill_keys = set()  # For deduplication by grant_ein, name, zip_code

# Logging setup
def setup_logging(output_dir, verbose, quiet):
    global logger
    return cu.setup_logging(output_dir, 'integrate_log.txt', verbose, quiet)

def log_error(msg_format, *args, ein=None, exc_info=False):
    cu.log_error(msg_format, *args, ein=ein, exc_info=exc_info)

# Thread-local counters
file_counter_local = threading.local()
total_entries = 0
tsv_write_queue = queue.Queue(maxsize=20000)
verbose = False
quiet = False
done_queuing = False

def initialize_thread_local_counters():
    if not hasattr(file_counter_local, 'value'):
        file_counter_local.value = 0
    if not hasattr(file_counter_local, 'skipped'):
        file_counter_local.skipped = 0

def build_zip_index(zip_dir, start_year, end_year):
    """Build an index of XML filenames to ZIP paths, including end_year + 1."""
    return cu.build_zip_index(zip_dir, start_year, end_year)

def get_tsv_files(start_year, end_year, org_types, not_types, source_dir):
    """Collect TSV files matching the org_types or not_types filter within the year range, in descending order."""
    all_files = glob.glob(os.path.join(source_dir, "charities_*_analyzed.tsv"))
    filtered_files = []
    for tsv_file in all_files:
        match = re.match(r'charities_(.+)_(\d{4})_analyzed\.tsv', os.path.basename(tsv_file))
        if not match:
            continue
        org_type, year = match.groups()
        year = int(year)
        if start_year <= year <= end_year:
            org_type_formatted = org_type
            if org_type.startswith('501c'):
                num = org_type[4:]
                if num.isdigit():
                    org_type_formatted = f"501(c)({num})"
                elif org_type == '501c3':
                    org_type_formatted = "501(c)(3)"
            elif org_type == '4947a1':
                org_type_formatted = "4947(a)(1)"
            if org_types == ['all'] or (org_types and org_type_formatted in org_types) or (not_types and org_type_formatted not in not_types):
                filtered_files.append((tsv_file, org_type_formatted, year))
    return sorted(filtered_files, key=lambda x: x[2], reverse=True)  # Sort by year descending (2025, 2024, ..., 2018)

def read_tsv_file(tsv_file, minimum_d):
    """Read a TSV file and return rows meeting the minimum denominator requirement."""
    rows = []
    total_rows = 0
    with open(tsv_file, 'r', encoding='utf-8') as f:
        header = f.readline().strip().split('\t')
        header_map = {col: idx for idx, col in enumerate(header) if col in TSV_COLUMNS}
        if not header_map:
            log_error("No valid columns found in TSV header for {}", tsv_file)
            return [], 0
        sample_paths = []
        for line in f:
            total_rows += 1
            fields = line.strip().split('\t')
            if len(fields) < len(header_map):
                continue
            row = {col: fields[idx] for col, idx in header_map.items()}
            rows.append(row)
            # Sample xml_name paths
            if len(sample_paths) < 10:
                xml_path = row.get('xml_name', '')
                if xml_path:
                    sample_paths.append(xml_path)
        if total_rows < 1000:
            log_error("Warning: Low row count ({}) in TSV {}, possible incomplete data", total_rows, tsv_file)
        if sample_paths:
            log_error("Sample xml_name paths in TSV {}: {}", tsv_file, sample_paths)
    # Filter rows by minimum_d
    filtered_rows = []
    for row in rows:
        try:
            denominator = float(row.get('denominator', '0'))
            if denominator >= minimum_d:
                filtered_rows.append(row)
        except (ValueError, KeyError):
            continue
    return filtered_rows, total_rows, rows

def collect_latest_rows(tsv_files, minimum_d, worker_threads, zip_index, year_range):
    """Collect the most recent qualifying row for each EIN, processing TSVs year by year in descending order."""
    latest_rows = {}  # EIN -> (row, org_type, year)
    rows_lock = threading.Lock()  # Lock for thread-safe updates
    summary = []  # List of (org_type, year, total_rows, filtered_rows, selected_rows, mismatches, missing_xmls, potential_mismatches)
    mismatches = 0
    missing_xmls = 0
    potential_mismatches = 0
    mismatch_details = []  # List of (ein, xml_name, tsv_zip, index_zip)
    missing_xml_details = []  # List of (ein, xml_name)
    potential_mismatch_details = []  # List of (ein, xml_name, tsv_zip)
    rows_checked = 0
    tsv_zips = set()

    def process_tsv(tsv_file, org_type, year):
        try:
            filtered_rows, total_rows, all_rows = read_tsv_file(tsv_file, minimum_d)
            local_rows = []
            selected_count = 0
            local_mismatches = 0
            local_missing_xmls = 0
            local_potential_mismatches = 0
            # Check all rows for mismatches, missing XMLs, and potential mismatches
            for row in all_rows:
                nonlocal rows_checked, missing_xmls, potential_mismatches
                rows_checked += 1
                ein = row['filer_ein']
                xml_path = row.get('xml_name', '')
                log_error(
                    "Checking xml_name: {} for EIN={} in TSV {}",
                    xml_path, ein, tsv_file, ein=ein
                )
                if xml_path:
                    parts = xml_path.split('/')
                    if len(parts) >= 2:
                        xml_filename = parts[-1]
                        tsv_zip = parts[0]  # Raw ZIP name
                        tsv_zips.add(tsv_zip)
                        # Extract ZIP year
                        zip_year_match = re.match(r'(\d{4})', tsv_zip)
                        if zip_year_match:
                            zip_year = int(zip_year_match.group(1))
                            if zip_year < year_range[0] or zip_year > year_range[1]:
                                log_error(
                                    "ZIP year {} outside range {}-{} for xml_path {} and EIN={}, skipping",
                                    zip_year, year_range[0], year_range[1], xml_path, ein, ein=ein
                                )
                                continue
                        else:
                            log_error(
                                "Invalid ZIP year in xml_path {} for EIN={}, skipping",
                                xml_path, ein, ein=ein
                            )
                            continue
                        if xml_filename in zip_index:
                            zip_path, internal_path = zip_index[xml_filename]
                            index_zip = os.path.basename(zip_path)
                            log_error(
                                "Comparison: TSV ZIP={} vs. Index ZIP={} for xml_filename={} in TSV {}",
                                tsv_zip, index_zip, xml_filename, tsv_file, ein=ein
                            )
                            if tsv_zip != index_zip:
                                local_mismatches += 1
                                mismatch_details.append((ein, xml_path, tsv_zip, index_zip))
                                log_error(
                                    "xml_name mismatch for EIN={} in {}: TSV ZIP={} vs. Index ZIP={}",
                                    ein, xml_path, tsv_zip, index_zip, ein=ein
                                )
                        else:
                            local_missing_xmls += 1
                            missing_xml_details.append((ein, xml_path))
                            local_potential_mismatches += 1
                            potential_mismatch_details.append((ein, xml_path, tsv_zip))
                            log_error(
                                "Potential mismatch (missing XML): EIN={}, xml_name={}, TSV ZIP={}, no index entry for xml_filename {}",
                                ein, xml_path, tsv_zip, xml_filename, ein=ein
                            )
            # Select qualifying rows - handle duplicates by taking latest XML file
            for row in filtered_rows:
                ein = row['filer_ein']
                try:
                    denominator = float(row.get('denominator', '0'))
                except (ValueError, KeyError):
                    continue
                if denominator >= minimum_d:
                    with rows_lock:
                        # For duplicates, compare XML filenames to determine which is "latest"
                        # Use filename as tiebreaker when years are equal
                        xml_name = row.get('xml_name', '')
                        existing_xml = latest_rows.get(ein, (None, None, None))[0]
                        existing_xml_name = existing_xml.get('xml_name', '') if existing_xml else ''
                        existing_year = latest_rows.get(ein, (None, None, 0))[2]

                        should_update = False
                        if ein not in latest_rows:
                            should_update = True
                        elif year > existing_year:
                            should_update = True
                        elif year == existing_year:
                            # Same year, compare XML filenames (simple string comparison)
                            if xml_name > existing_xml_name:
                                should_update = True
                                if verbose:
                                    log_error(
                                        "Replacing duplicate EIN={} year {} with newer XML {} (was {})",
                                        ein, year, xml_name, existing_xml_name, ein=ein
                                    )

                        if should_update:
                            local_rows.append((row, org_type, year))
                            latest_rows[ein] = (row, org_type, year)
                            selected_count += 1
                            if verbose:
                                log_error(
                                    "Selected row for EIN={} from year {} in TSV {}",
                                    ein, year, tsv_file, ein=ein
                                )
                        elif verbose:
                            log_error(
                                "Keeping existing row for EIN={} from year {} in TSV {}, ignoring year {}",
                                ein, existing_year, tsv_file, year, ein=ein
                            )
            return local_rows, total_rows, len(filtered_rows), selected_count, local_mismatches, local_missing_xmls, local_potential_mismatches
        except Exception as e:
            log_error("Error in process_tsv for {}: {}", tsv_file, str(e), exc_info=True)
            return [], 0, 0, 0, 0, 0, 0

    # Group TSVs by year and process sequentially
    tsvs_by_year = defaultdict(list)
    for tsv_file, org_type, year in tsv_files:
        tsvs_by_year[year].append((tsv_file, org_type, year))
    
    # Process years in descending order (2025, 2024, ..., 2018)
    for year in sorted(tsvs_by_year.keys(), reverse=True):
        year_tsvs = tsvs_by_year[year]
        with ThreadPoolExecutor(max_workers=worker_threads) as executor:
            futures = {executor.submit(process_tsv, tsv_file, org_type, y): (tsv_file, org_type, y) for tsv_file, org_type, y in year_tsvs}
            with tqdm(total=len(year_tsvs), desc=f"Collecting rows for year {year}") as pbar:
                for future in as_completed(futures):
                    tsv_file, org_type, _ = futures[future]
                    try:
                        local_rows, total_rows, filtered_rows, selected_rows, local_mismatches, local_missing_xmls, local_potential_mismatches = future.result()
                        mismatches += local_mismatches
                        missing_xmls += local_missing_xmls
                        potential_mismatches += local_potential_mismatches
                        summary.append((org_type, year, total_rows, filtered_rows, selected_rows, local_mismatches, local_missing_xmls, local_potential_mismatches))
                    except Exception as e:
                        log_error("Error processing TSV {}: {}", tsv_file, str(e), exc_info=True)
                    pbar.update(1)

    # Log summaries
    log_error(
        "Checked {} rows for xml_name mismatches, found {} mismatches, {} missing XMLs, {} potential mismatches",
        rows_checked, len(mismatch_details), len(missing_xml_details), len(potential_mismatch_details)
    )
    log_error("Unique TSV ZIPs found: {}", sorted(tsv_zips))
    if mismatch_details:
        log_error("Summary of xml_name mismatches: {} total", len(mismatch_details))
        for ein, xml_path, tsv_zip, index_zip in mismatch_details[:10]:
            log_error(
                "Mismatch: EIN={}, xml_name={}, TSV ZIP={}, Index ZIP={}",
                ein, xml_path, tsv_zip, index_zip, ein=ein
            )
        if len(mismatch_details) > 10:
            log_error("Additional {} mismatches not logged", len(mismatch_details) - 10)
    if missing_xml_details:
        log_error("Summary of missing XMLs: {} total", len(missing_xml_details))
        for ein, xml_path in missing_xml_details[:10]:
            log_error(
                "Missing XML: EIN={}, xml_name={}",
                ein, xml_path, ein=ein
            )
        if len(missing_xml_details) > 10:
            log_error("Additional {} missing XMLs not logged", len(missing_xml_details) - 10)
    if potential_mismatch_details:
        log_error("Summary of potential mismatches: {} total", len(potential_mismatch_details))
        for ein, xml_path, tsv_zip in potential_mismatch_details[:10]:
            log_error(
                "Potential mismatch: EIN={}, xml_name={}, TSV ZIP={}",
                ein, xml_path, tsv_zip, ein=ein
            )
        if len(potential_mismatch_details) > 10:
            log_error("Additional {} potential mismatches not logged", len(potential_mismatch_details) - 10)

    return list(latest_rows.values()), summary, mismatches, missing_xmls, potential_mismatches

def print_summary_table(summary, total_mismatches, total_missing_xmls, total_potential_mismatches):
    """Print a markdown table summarizing TSV row counts, mismatches, missing XMLs, and potential mismatches."""
    if not summary:
        print("No TSV files processed.")
        return

    print("\n### TSV Processing Summary\n")
    print("| Org Type | Year | Total Rows | Filtered Rows (≥ MinimumD) | Selected Rows (Latest) | xml_name Mismatches | Mismatch % | Missing XMLs | Potential Mismatches |")
    print("|----------|-----:|-----------:|--------------------------:|-----------------------:|--------------------:|-----------:|-------------:|---------------------:|")
    total_rows_sum = 0
    filtered_rows_sum = 0
    selected_rows_sum = 0
    mismatches_sum = 0
    missing_xmls_sum = 0
    potential_mismatches_sum = 0
    for org_type, year, total_rows, filtered_rows, selected_rows, mismatches, missing_xmls, potential_mismatches in sorted(summary, key=lambda x: (x[1], x[0])):
        mismatch_pct = (mismatches / selected_rows * 100) if selected_rows > 0 else 0
        print(f"| {org_type:<8} | {year:<4} | {total_rows:<10} | {filtered_rows:<25} | {selected_rows:<21} | {mismatches:<18} | {mismatch_pct:<9.2f}% | {missing_xmls:<11} | {potential_mismatches:<19} |")
        total_rows_sum += total_rows
        filtered_rows_sum += filtered_rows
        selected_rows_sum += selected_rows
        mismatches_sum += mismatches
        missing_xmls_sum += missing_xmls
        potential_mismatches_sum += potential_mismatches
    total_mismatch_pct = (mismatches_sum / selected_rows_sum * 100) if selected_rows_sum > 0 else 0
    print(f"| **Total**|      | **{total_rows_sum:<10}** | **{filtered_rows_sum:<25}** | **{selected_rows_sum:<21}** | **{mismatches_sum:<18}** | **{total_mismatch_pct:<9.2f}%** | **{missing_xmls_sum:<11}** | **{potential_mismatches_sum:<19}** |")
    print(f"\nTotal xml_name mismatches across all files: {total_mismatches}")
    print(f"Total missing XMLs across all files: {total_missing_xmls}")
    print(f"Total potential mismatches across all files: {total_potential_mismatches}")

def write_charity_latest(rows, output_tsv, output_csv, zip_index):
    """Write charity data to TSV and CSV with quoted string fields in CSV."""
    output_tsv = cu.normalize_file_path(output_tsv, 'charity_latest.tsv')
    output_csv = cu.normalize_file_path(output_csv, 'charity_latest.csv')
    with open(output_tsv, 'w', encoding='utf-8') as f:
        f.write('\t'.join(TSV_COLUMNS) + '\n')
        for row, _, _ in rows:
            xml_path = row.get('xml_name', '')
            if xml_path:
                parts = xml_path.split('/')
                if len(parts) >= 2:
                    xml_filename = parts[-1]
                    if xml_filename in zip_index:
                        zip_path, internal_path = zip_index[xml_filename]
                        new_xml_name = f"{os.path.basename(zip_path)}/{internal_path}"
                        row['xml_name'] = new_xml_name
                    else:
                        log_error(
                            "No index entry for xml_filename {} in xml_path {} for EIN={}",
                            xml_filename, xml_path, row['filer_ein'], ein=row['filer_ein']
                        )
            f.write('\t'.join(str(row.get(col, '')) for col in TSV_COLUMNS) + '\n')
    log_error("Wrote {} rows to {}", len(rows), output_tsv)

    with open(output_csv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(TSV_COLUMNS)
        for row, _, _ in rows:
            csv_row = []
            for col in TSV_COLUMNS:
                value = str(row.get(col, ''))
                if col in CSV_QUOTE_FIELDS['charity']:
                    csv_row.append(value)
                else:
                    csv_row.append(value)
            writer.writerow(csv_row)
    log_error("Wrote {} rows to {}", len(rows), output_csv)

def write_grants_latest(grants, output_tsv, output_csv):
    """Write grant data to TSV and CSV with quoted string fields in CSV."""
    output_tsv = cu.normalize_file_path(output_tsv, 'grants_latest.tsv')
    output_csv = cu.normalize_file_path(output_csv, 'grants_latest.csv')
    with open(output_tsv, 'w', encoding='utf-8') as f:
        f.write('\t'.join(GRANT_COLUMNS) + '\n')
        for grant in grants:
            f.write('\t'.join(str(grant[col]) for col in GRANT_COLUMNS) + '\n')
    log_error("Wrote {} grant rows to {}", len(grants), output_tsv)

    with open(output_csv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(GRANT_COLUMNS)
        for grant in grants:
            csv_row = []
            for col in GRANT_COLUMNS:
                value = str(grant[col])
                if col in CSV_QUOTE_FIELDS['grants']:
                    csv_row.append(value)
                else:
                    csv_row.append(value)
            writer.writerow(csv_row)
    log_error("Wrote {} grant rows to {}", len(grants), output_csv)

def write_backfill(backfill_entries, output_tsv, output_csv):
    """Write backfill data to TSV and CSV with quoted string fields in CSV, allowing multiple names per EIN."""
    output_tsv = cu.normalize_file_path(output_tsv, 'backfill.tsv')
    output_csv = cu.normalize_file_path(output_csv, 'backfill.csv')
    with open(output_tsv, 'w', encoding='utf-8') as f:
        f.write('\t'.join(BACKFILL_COLUMNS) + '\n')
        for entry in backfill_entries:
            f.write('\t'.join(str(entry.get(col, '')) for col in BACKFILL_COLUMNS) + '\n')
    log_error("Wrote {} backfill rows to {}", len(backfill_entries), output_tsv)

    with open(output_csv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(BACKFILL_COLUMNS)
        for entry in backfill_entries:
            csv_row = []
            for col in BACKFILL_COLUMNS:
                value = str(entry.get(col, ''))
                if col in CSV_QUOTE_FIELDS['backfill']:
                    csv_row.append(value)
                else:
                    csv_row.append(value)
            writer.writerow(csv_row)
    log_error("Wrote {} backfill rows to {}", len(backfill_entries), output_csv)

def parse_grants(xml_content, xml_filename, filer_ein, filer_name, tax_year, known_eins, backfill_entries=None, seen_backfill_keys=None):
    """Parse grant data from an XML file, handling foreign addresses as country-level grants and collecting backfill for domestic unknown EINs."""
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        grants = []
        
        grant_xpaths = [
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI/irs:RecipientTable", namespaces=NAMESPACES),
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI/irs:GrantsOtherAsstToIndivInUSGrp", namespaces=NAMESPACES),
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp", namespaces=NAMESPACES),
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS", namespaces=NAMESPACES),
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS", namespaces=NAMESPACES),
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp", namespaces=NAMESPACES),
            etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:GrantOrContributionPdDurYrGrp", namespaces=NAMESPACES),
            etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp", namespaces=NAMESPACES),
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI//irs:CashGrantAmt/..", namespaces=NAMESPACES),
            etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF//irs:CashGrantAmt/..", namespaces=NAMESPACES),
        ]
        
        total_elements = 0
        for xpath in grant_xpaths:
            elements = xpath(root)
            total_elements += len(elements)
            for elem in elements:
                ein_elem = elem.xpath("irs:EIN | irs:RecipientEIN | irs:RecipientBusinessName/irs:EIN", namespaces=NAMESPACES)
                name_elem = elem.xpath(
                    "irs:RecipientNameBusiness | irs:RecipientBusinessName/irs:BusinessNameLine1Txt | irs:BusinessName/irs:BusinessNameLine1Txt",
                    namespaces=NAMESPACES
                )
                amount_elem = elem.xpath(
                    "irs:CashGrantAmt | irs:TotalGrantOrContriPdDurYrAmt | irs:GrantOrContributionAmt | irs:Amount",
                    namespaces=NAMESPACES
                )
                grantee_name = name_elem[0].text.strip() if name_elem and name_elem[0].text else "Unknown"
                
                # Check for foreign address
                is_foreign = elem.xpath("irs:RecipientForeignAddress", namespaces=NAMESPACES)
                if is_foreign:
                    country_elem = elem.xpath("irs:RecipientForeignAddress/irs:CountryCd", namespaces=NAMESPACES)
                    country_code = country_elem[0].text.strip() if country_elem and country_elem[0].text else None
                    if country_code and lookupCC(country_code):
                        country = lookupCC(country_code)
                        grant_ein = country["number"]
                        grantee_name = country["name"]
                        if verbose:
                            log_error(
                                "Foreign grant to country {} with EIN {} in {} for filer EIN={}",
                                country_code, grant_ein, xml_filename, filer_ein, ein=filer_ein
                            )
                    else:
                        grant_ein = "999"
                        grantee_name = "Foreign_" + (country_code or "Unknown")
                        if verbose:
                            log_error(
                                "Unmapped foreign address, using EIN 999 for country {} in {} for filer EIN={}",
                                country_code or "None", xml_filename, filer_ein, ein=filer_ein
                            )
                else:
                    grant_ein = ein_elem[0].text.strip() if ein_elem and ein_elem[0].text else "Unknown"
                
                if grant_ein == "Unknown" and not is_foreign:
                    if verbose:
                        log_error(
                            "Skipping grant with Unknown EIN in {} for filer EIN={}",
                            xml_filename, filer_ein, ein=filer_ein
                        )
                    continue
                if amount_elem and amount_elem[0].text:
                    try:
                        grant_amt = int(parse_float_field(amount_elem[0].text.strip()))
                        if grant_amt > 0:
                            grants.append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'grant_ein': grant_ein,
                                'grant_amt': grant_amt,
                                'tax_year': tax_year
                            })
                            if verbose:
                                log_error(
                                    "Found grant: EIN={}, Amount=${} in {} for filer EIN={}",
                                    grant_ein, grant_amt, xml_filename, filer_ein, ein=filer_ein
                                )
                            # Backfill for domestic grants with unknown EINs
                            if backfill_entries is not None and not is_foreign and grant_ein not in known_eins and grant_ein.isdigit() and grant_ein != "999":
                                # Validate EIN
                                is_valid, reason = cu.validate_ein(grant_ein)
                                if not is_valid:
                                    if verbose:
                                        log_error(
                                            "Skipping invalid EIN {} in {} for filer EIN={}: {}",
                                            grant_ein, xml_filename, filer_ein, reason, ein=filer_ein
                                        )
                                    continue
                                # Extract address components using XPath
                                address_components = []
                                us_address = elem.xpath("irs:USAddress/*", namespaces=NAMESPACES)
                                address_components.extend([comp for comp in us_address if comp.text])
                                # Canonicalize address
                                canonical_address, street, city, state, po_box, zip_code, _ = cu.canonicalize_address(address_components, output_dir=None)
                                if canonical_address or po_box or zip_code:
                                    backfill_key = (grant_ein, grantee_name, zip_code)
                                    with backfill_lock:
                                        if backfill_key not in seen_backfill_keys:
                                            seen_backfill_keys.add(backfill_key)
                                            backfill_entry = {
                                                'grant_ein': grant_ein,
                                                'name': grantee_name,
                                                'canonical_address': canonical_address,
                                                'po_box': po_box,
                                                'zip_code': zip_code
                                            }
                                            backfill_entries.append(backfill_entry)
                                            if verbose:
                                                log_error(
                                                    "Added backfill: EIN={}, Name={}, Address={}, PO Box={}, ZIP={}",
                                                    grant_ein, grantee_name, canonical_address, po_box, zip_code, ein=filer_ein
                                                )
                    except (ValueError, TypeError) as e:
                        if verbose:
                            log_error(
                                "Invalid grant amount '{}' in {} for EIN={}: {}",
                                amount_elem[0].text, xml_filename, filer_ein, str(e), ein=filer_ein
                            )
        
        if verbose:
            log_error(
                "Processed {} grant elements in {} for EIN={}, grants found: {}, backfill entries: {}",
                total_elements, xml_filename, filer_ein, len(grants), len(backfill_entries), ein=filer_ein
            )
        return grants
    except Exception as e:
        log_error("Error parsing grants from {}: {}", xml_filename, str(e), exc_info=True, ein=filer_ein)
        return []

def process_grants(row_data, worker_threads, zip_index, start_year, end_year, backfill_entries=None, seen_backfill_keys=None):
    """Process XML files to extract grant data using the ZIP index."""
    grants = []
    zip_cache = {}
    known_eins = {row['filer_ein'] for row, _, _ in row_data}

    def process_row(row, org_type, year):
        xml_path = row['xml_name']
        try:
            parts = xml_path.split('/')
            if len(parts) < 2:
                log_error("Invalid xml_path format {} for EIN={}, skipping", xml_path, row['filer_ein'], ein=row['filer_ein'])
                return []
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                log_error(
                    "No file named {} found in any ZIP for EIN={}, skipping. Index sample: {}",
                    xml_filename, row['filer_ein'], list(zip_index.keys())[:10], ein=row['filer_ein']
                )
                return []
            zip_path, internal_path = zip_index[xml_filename]
            zip_year_match = re.match(r'.*(\d{4})', os.path.basename(zip_path))
            if not zip_year_match:
                log_error("Invalid ZIP path format {} for EIN={}, skipping", zip_path, row['filer_ein'], ein=row['filer_ein'])
                return []
            zip_year = int(zip_year_match.group(1))
            if zip_year < start_year or zip_year > end_year + 1:
                log_error(
                    "ZIP year {} outside range {}-{} for xml_path {} and EIN={}, skipping",
                    zip_year, start_year, end_year + 1, xml_path, row['filer_ein'], ein=row['filer_ein']
                )
                return []
            if verbose:
                log_error(
                    "Opening ZIP {} with internal_path {} (from xml_path {}) for EIN={}",
                    zip_path, internal_path, xml_path, row['filer_ein'], ein=row['filer_ein']
                )
            if zip_path not in zip_cache:
                zip_cache[zip_path] = zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                return parse_grants(xml_content, internal_path, row['filer_ein'], row['filer_name'], row['tax_year'], known_eins, backfill_entries, seen_backfill_keys)
        except Exception as e:
            log_error(
                "Error accessing XML {} for EIN={}: {}", xml_path, row['filer_ein'], str(e),
                exc_info=True, ein=row['filer_ein']
            )
            return []

    with ThreadPoolExecutor(max_workers=worker_threads) as executor:
        futures = {executor.submit(process_row, row, org_type, year): row['xml_name'] for row, org_type, year in row_data}
        with tqdm(total=len(row_data), desc="Extracting grants") as pbar:
            for future in as_completed(futures):
                xml_name = futures[future]
                try:
                    grant_list = future.result()
                    if grant_list is not None:
                        grants.extend(grant_list)
                except Exception as e:
                    log_error("Error processing grants for {}: {}", xml_name, str(e), exc_info=True)
                pbar.update(1)
    
    for zip_file in zip_cache.values():
        zip_file.close()
    
    return grants

def main():
    global verbose, quiet, done_queuing, total_entries
    parser = argparse.ArgumentParser(
        description=(
            "Integrate IRS 990 data to produce latest charity and grant data.\n"
            "Note: Use --minimumD (not --miniumD) to set the minimum denominator value.\n"
            "Note: ZIP files are indexed for start_year to end_year + 1 to account for 990 filing lag.\n"
            "Output: Produces charity_latest.tsv/csv, grants_latest.tsv/csv, and backfill.tsv/csv.\n"
            "Processing: Selects latest qualifying filing per EIN and backfills grantee data for unknown domestic EINs."
        )
    )
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument(
        "--orgTypes",
        type=str,
        default="all",
        help="Comma-separated list of org types to include (or 'all')"
    )
    parser.add_argument(
        "--NOTTypes",
        type=str,
        default="",
        help="Comma-separated list of org types to exclude"
    )
    parser.add_argument(
        "--minimumD",
        type=float,
        default=10_000_000,
        help="Minimum denominator value (default: 10M)"
    )
    parser.add_argument(
        "--source-dir",
        type=str,
        default=".",
        help="Directory containing analyzed TSV files"
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
        help="Directory for output TSV and CSV files"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument(
        "--worker-threads",
        type=int,
        default=16,
        help="Number of worker threads for processing"
    )
    args = parser.parse_args()

    verbose = args.verbose
    quiet = args.quiet
    start_year = args.start_year
    end_year = args.end_year
    minimum_d = args.minimumD
    worker_threads = args.worker_threads
    source_dir = args.source_dir
    zip_dir = args.zip_dir
    output_dir = args.output_dir
    org_types = args.orgTypes.split(',') if args.orgTypes else ['all']
    not_types = args.NOTTypes.split(',') if args.NOTTypes else []

    # Validate arguments
    if worker_threads < 1:
        raise ValueError("Number of worker threads must be at least 1")
    if minimum_d < 0:
        raise ValueError("Minimum denominator must be non-negative")
    if org_types != ['all'] and not_types:
        raise ValueError("Cannot specify both --orgTypes and --NOTTypes")
    if not os.path.isdir(source_dir):
        raise ValueError(f"Source directory {source_dir} does not exist")
    if not os.path.isdir(zip_dir):
        raise ValueError(f"ZIP directory {zip_dir} does not exist")
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    listener = setup_logging(output_dir, verbose, quiet)

    try:
        # Build ZIP index
        zip_index = build_zip_index(zip_dir, start_year, end_year)
        if not zip_index:
            print("No ZIP files found in {} for years {}-{}", zip_dir, start_year, end_year + 1)
            return

        # Step 1: Collect latest rows
        tsv_files = get_tsv_files(start_year, end_year, org_types, not_types, source_dir)
        if not tsv_files:
            print("No TSV files found matching the criteria")
            return
        year_range = (start_year, end_year + 1)
        latest_rows, summary, total_mismatches, total_missing_xmls, total_potential_mismatches = collect_latest_rows(tsv_files, minimum_d, worker_threads, zip_index, year_range)
        write_charity_latest(latest_rows, os.path.join(output_dir, "charity_latest.tsv"), os.path.join(output_dir, "charity_latest.csv"), zip_index)
        total_entries = len(latest_rows)
        print(f"Total charity entries processed: {total_entries}")
        print_summary_table(summary, total_mismatches, total_missing_xmls, total_potential_mismatches)

        # Step 2: Extract grants and backfill
        grants = process_grants(latest_rows, worker_threads, zip_index, start_year, end_year, backfill_entries, seen_backfill_keys)
        write_grants_latest(grants, os.path.join(output_dir, "grants_latest.tsv"), os.path.join(output_dir, "grants_latest.csv"))
        write_backfill(backfill_entries, os.path.join(output_dir, "backfill.tsv"), os.path.join(output_dir, "backfill.csv"))
        print(f"Total grant entries extracted: {len(grants)}")
        print(f"Total backfill entries written: {len(backfill_entries)}")
    finally:
        done_queuing = True
        listener.stop()

if __name__ == "__main__":
    main()