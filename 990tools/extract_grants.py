import os
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import queue
from lxml import etree
from io import BytesIO
import sys
import extract_utils as cu

logger = None
verbose = False
quiet = False
debug_grant_entries = []
total_grants = 0
total_990pf_rows = 0
total_queue_puts = 0
total_tasks_done = 0
total_skipped = 0
duplicate_grant_count = 0
duplicate_ein_counts = cu.defaultdict(int)
unique_grant_eins = set()
grant_ein_counts = cu.defaultdict(int)
results_queue = None
grants_queue = None
zip_index_lock = threading.Lock()
filer_eins = {}
status_counts = cu.defaultdict(int)
po_box_entries = []
zip_code_index = {}
po_box_zip_index = {}
thread_local = threading.local()
file_counter_local = threading.local()

def main():
    global verbose, quiet, total_grants, total_990pf_rows, total_queue_puts, total_tasks_done, total_skipped
    global args, filer_eins, results_queue, grants_queue, po_box_entries, zip_code_index, po_box_zip_index
    parser = argparse.ArgumentParser(description="Extract grants from IRS 990 XML files using address mappings.")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--source-dir", type=str, default=".", help="Directory containing charity_latest.tsv")
    parser.add_argument("--zip-dir", type=str, default="..", help="Directory containing ZIP files")
    parser.add_argument("--cache-dir", type=str, default="./_cache", help="Directory for cache files")
    parser.add_argument("--output-dir", type=str, default="./_output", help="Directory for output TSV files")
    parser.add_argument("--charity-source", type=str, help="Path to charity_latest.tsv", default=None)
    parser.add_argument("--merge-batch-size", type=int, default=1000, help="Batch size for queuing results")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument("--no-threads", action="store_true", help="Run single-threaded")
    parser.add_argument("--worker-threads", type=int, default=16, help="Number of worker threads")
    args = parser.parse_args()
    verbose = args.verbose
    quiet = args.quiet
    if args.no_threads:
        args.worker_threads = 1
    os.makedirs(args.output_dir, exist_ok=True)
    if not os.path.isdir(args.source_dir):
        raise ValueError(f"Source directory {args.source_dir} does not exist")
    if not os.path.isdir(args.zip_dir):
        raise ValueError(f"ZIP directory {args.zip_dir} does not exist")
    if args.charity_source is None:
        args.charity_source = os.path.join(args.source_dir, 'charity_latest.tsv')
    listener = cu.setup_logging(args.output_dir, 'extract_grants_log.txt', verbose, quiet)
    cu.signal.signal(cu.signal.SIGINT, signal_handler)
    try:
        addr_cache_valid, addr_cached_data = cu.load_address_cache(args.cache_dir, args.start_year, args.end_year, args.zip_dir)
        if not addr_cache_valid:
            cu.log_error("Address cache not found in {}. Run extract_addresses.py first.", args.cache_dir)
            return
        po_box_entries, zip_code_index, po_box_zip_index = addr_cached_data[2:5]
        cu.log_error("Loaded address cache: {} PO boxes, {} ZIP index entries", len(po_box_entries), len(zip_code_index))
        rows = cu.read_tsv_files(args.charity_source, args.start_year, args.end_year)
        if not rows:
            write_outputs(args.output_dir)
            cu.log_error("No rows in {}. Ensure {} exists.", args.charity_source, args.charity_source)
            return
        cu.log_error("Processing {} rows from {}", len(rows), args.charity_source)
        process_charity_rows(rows, args.worker_threads, zip_index, args.start_year, args.end_year, args.output_dir)
        write_outputs(args.output_dir)
    except Exception as e:
        cu.log_error("Error during processing: {}", str(e), exc_info=True)
        write_outputs(args.output_dir)
    finally:
        if grants_queue:
            grants_queue.put(None)
        listener.stop()
    log_file = os.path.join(args.output_dir, 'extract_grants_log.txt')
    print(f"Log file written to: {log_file}")
    print(f"Total grants extracted: {total_grants}")
    print(f"Total 990PF rows processed: {total_990pf_rows}")
    print(f"Queue summary: {total_queue_puts} items put, {total_tasks_done} tasks done")
    print(f"Progress Summary:")
    print(f"- Total rows processed: {len(rows)}")
    print(f"- 990PF rows processed: {total_990pf_rows}")
    print(f"- Grants written to inferred_grants.tsv: {total_grants}")
    print(f"- Grants skipped: {status_counts.get('skipped', 0)}")
    print(f"- Output files in: {args.output_dir}")

def compute_name_heuristic(grantee_name, filer_name):
    if not grantee_name or not filer_name:
        return 0
    words1 = {w.lower() for w in grantee_name.split() if w.lower() not in cu.STOP_WORDS}
    words2 = {w.lower() for w in filer_name.split() if w.lower() not in cu.STOP_WORDS}
    common_words = len(words1 & words2)
    total_grantee_words = len(words1)
    if total_grantee_words == 0:
        return 0
    proportion = (common_words / total_grantee_words) * 100
    return round(proportion / 10) * 10

def parse_grants(xml_content, xml_filename, row, filer_ein, output_dir):
    global total_grants, total_990pf_rows, total_queue_puts, duplicate_grant_count, unique_grant_eins, grant_ein_counts
    if not hasattr(thread_local, 'result'):
        thread_local.result = {
            'grant_entries': [],
            'debug_grant_entries': [],
            'po_box_entries': [],
            'total_grants': 0,
            'total_queue_puts': 0,
            'total_990pf_rows': 0
        }
    result = thread_local.result
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        filer_name = row['filer_name'].strip()
        tax_year = row['tax_year'].strip()
        with zip_index_lock:
            filer_canonical_address = filer_eins.get(xml_filename, (None, None, ''))[2]
        status_counts = cu.defaultdict(int)
        grant_map = []
        grant_map_keys = set()
        if row['form_type'] == '990PF':
            result['total_990pf_rows'] += 1
            for xpath_grant in cu.GRANT_XPATHS_990PF:
                grant_elements = xpath_grant(root)
                if grant_elements:
                    nsmap = root.nsmap
                    element_count = 0
                    irs_ns = nsmap.get('irs', nsmap.get(None))
                    cu.log_error("Parsing grants for XML {}, root tag: {}, namespace: {}", xml_filename, root.tag, irs_ns)
                    if irs_ns != 'http://www.irs.gov/efile':
                        cu.log_error("Namespace mismatch: expected http://www.irs.gov/efile, got {}", irs_ns)
                    cu.log_error("Found {} grant elements for XML {}, sample: {}", len(grant_elements), xml_filename, cu.ET.tostring(grant_elements[0], encoding='unicode', method='xml')[:500])
                for element in grant_elements:
                    ein_elem = element.xpath(".//irs:EIN | .//irs:RecipientEIN", namespaces=cu.NAMESPACES)
                    name_elem = None
                    for i, xpath_name in enumerate(cu.GRANTEE_NAME_XPATHS):
                        names = xpath_name(element)
                        if names and names[0].text is not None:
                            name_elem = names[0]
                            break
                    amount_elem = None
                    for xpath_amount in cu.AMOUNT_XPATHS:
                        amounts = xpath_amount(element)
                        if amounts and amounts[0].text is not None and amounts[0].text.strip():
                            amount_elem = amounts[0]
                            break
                    element_count += 1
                    skip_log_count = 0
                    if element_count <= 10 and ((name_elem is None and xpath_name(element)) or (amount_elem is None and xpath_amount(element))):
                        raw_amount_text = amounts[0].text if amounts and amounts[0].text else "None"
                        cu.log_error("Grant element #{}, name_elem: {}, amount_elem: {}, xpath_name_index: {}, raw_names: {}, raw_amounts: {}, raw_amount_text: {}", element_count, name_elem is not None, amount_elem is not None, i if name_elem is not None else -1, [n.text.strip() if n.text else '' for n in xpath_name(element)], [a.text.strip() if a.text else '' for a in xpath_amount(element)], raw_amount_text)
                    if element_count == len(grant_elements):
                        cu.log_error("Processed {} grant elements for XML {}, skipped: {}", element_count, xml_filename, status_counts.get('skipped', 0))
                    address_elems = None
                    possibly_foreign = False
                    for xpath_address in cu.GRANTEE_ADDRESS_XPATHS:
                        addrs = xpath_address(element)
                        if addrs:
                            address_elems = addrs
                            if not any('RecipientForeignAddress' in elem.tag for elem in address_elems):
                                cu.log_error("No RecipientForeignAddress in address elements for XML {}, trying next XPath", xml_filename)
                            if b"Foreign" in xml_content:
                                cu.log_error("Found 'Foreign' in XML {}: {}", xml_filename, xml_content[:500].decode('utf-8', errors='ignore'))
                                possibly_foreign = True
                            break
                    is_foreign_address = any('RecipientForeignAddress' in elem.tag for elem in address_elems) if address_elems else False
                    if is_foreign_address or (possibly_foreign and address_elems and any('PR' in elem.text.upper() for elem in address_elems if elem.text)):
                        country_elem = element.xpath(".//irs:CountryCd", namespaces=cu.NAMESPACES)
                        country_code = country_elem[0].text.strip() if country_elem and country_elem[0].text else None
                        if country_code == 'RQ':
                            country_code = 'PR'
                        if country_code == 'PR':
                            grant_ein = ein_elem[0].text.strip() if ein_elem and len(ein_elem) > 0 and ein_elem[0].text else "Unknown"
                            grantee_canonical_address, grant_po_box, grant_zip_code, _ = cu.canonicalize_address(address_elems, output_dir)
                            cu.log_error("Puerto Rico address detected, treated as U.S. territory: EIN={}, address={}", grant_ein, grantee_canonical_address)
                        elif country_code and country_code in cu.iso3166_alpha2:
                            grant_ein = cu.iso3166_alpha2[country_code]["number"]
                            grantee_canonical_address = cu.iso3166_alpha2[country_code]["name"]
                            cu.log_error("Foreign address mapped to ISO 3166-1 number: {} for country: {}", grant_ein, country_code)
                        else:
                            grantee_canonical_address = "Foreign_" + (country_code or "None")
                            grant_ein = '999'
                            cu.log_error("Foreign address unmapped, using address-based EIN: {}, country: {}", grant_ein, country_code or "None")
                    else:
                        if possibly_foreign and address_elems:
                            cu.log_error("Foreign address no tag? {}", [elem.tag for elem in address_elems])
                        grant_ein = ein_elem[0].text.strip() if ein_elem and len(ein_elem) > 0 and ein_elem[0].text else "Unknown"
                    grantee_name = name_elem.text.strip() if name_elem is not None and name_elem.text and name_elem.text.strip().lower() != 'see attached schedule' else "Unknown"
                    grant_address_components = [elem for elem in address_elems if elem.text] if address_elems else []
                    grantee_canonical_address, grant_po_box, grant_zip_code, _ = cu.canonicalize_address(grant_address_components, output_dir)
                    if grantee_canonical_address:
                        for abbr, full in cu.USPS_FIXES.items():
                            grantee_canonical_address = grantee_canonical_address.replace(f'{abbr} ', f'{full} ')
                    if grantee_canonical_address or amount_elem is not None or name_elem is not None or len(ein_elem) > 0 or grant_ein != "Unknown":
                        try:
                            grant_amt = int(float(amount_elem.text.strip())) if amount_elem is not None and amount_elem.text else 0
                            if is_foreign_address:
                                status = "success_foreign"
                            elif grantee_canonical_address or grant_amt > 0 or grantee_name != "Unknown" or ein_elem:
                                best_score = 0
                                best_filer = None
                                best_heuristic = None
                                candidates = set()
                                if grant_po_box and grant_zip_code and cu.ZIP_REGEX.match(grant_zip_code):
                                    candidates = po_box_zip_index.get((grant_po_box, grant_zip_code), set())
                                    heuristic_type = 'po_box_name'
                                elif grant_zip_code and cu.ZIP_REGEX.match(grant_zip_code):
                                    candidates = zip_code_index.get(grant_zip_code, set())
                                    heuristic_type = 'zip_name'
                                for cand_ein, cand_name in candidates:
                                    score = compute_name_heuristic(grantee_name, cand_name)
                                    if score > best_score:
                                        best_score = score
                                        best_filer = (cand_ein, cand_name)
                                        best_heuristic = heuristic_type
                                if best_filer:
                                    if best_score >= 50:
                                        grant_ein = best_filer[0]
                                        status = f"success_{best_heuristic}_score_{best_score}"
                                    else:
                                        status = f"fail_{best_heuristic}_score_{best_score}"
                                elif grant_ein == "Unknown" and grantee_canonical_address:
                                    grant_ein = f"Address:{grantee_canonical_address}"
                                    status = 'fail_address'
                                elif grantee_name != "Unknown" or grant_amt > 0:
                                    grant_ein = f"Name:{grantee_name}"
                                    status = 'success_name'
                                else:
                                    grant_ein = "Unknown"
                                    status = 'skipped'
                                status_counts[status] += 1
                            if grant_ein != "Unknown" or grantee_name != "Unknown" or grant_amt > 0:
                                grant_map.append({
                                    'filer_ein': filer_ein,
                                    'filer_name': filer_name,
                                    'grant_ein': grant_ein,
                                    'grant_amt': grant_amt,
                                    'tax_year': tax_year,
                                    'filer_canonical_address': filer_canonical_address,
                                    'grantee_canonical_address': grantee_canonical_address
                                })
                                seen_key = (filer_ein, grant_ein)
                                if seen_key in grant_map_keys:
                                    duplicate_grant_count += 1
                                    duplicate_ein_counts[grant_ein] += 1
                                    if skip_log_count < 10:
                                        cu.log_error("Duplicate grant for filer_ein={}, grant_ein={} in XML {}, amount: {}, grantee_name={}, grant_address={}", filer_ein, grant_ein, xml_filename, grant_amt, grantee_name, grantee_canonical_address)
                                        skip_log_count += 1
                                else:
                                    grant_map_keys.add(seen_key)
                                    unique_grant_eins.add(grant_ein)
                                    grant_ein_counts[grant_ein] += 1
                                    result['debug_grant_entries'].append({
                                        'filer_ein': filer_ein,
                                        'filer_name': filer_name,
                                        'xml_filename': xml_filename,
                                        'grantee_name': grantee_name,
                                        'grant_ein': grant_ein,
                                        'grant_address': grantee_canonical_address,
                                        'grant_amt': grant_amt,
                                        'tax_year': tax_year,
                                        'status': status,
                                        'heuristic_score': best_score,
                                        'reason': status
                                    })
                                    result['total_grants'] += 1
                                    result['total_queue_puts'] += 1
                            else:
                                if skip_log_count < 10:
                                    raw_amount_text = amount_elem.text if amount_elem is not None else "None"
                                    cu.log_error("Skipped grant in XML {}: grantee_name={}, grant_amt={}, grant_address={}, raw_amount_text={}, reason=No valid EIN, address, or name", xml_filename, grantee_name, grant_amt, grantee_canonical_address, raw_amount_text)
                                    skip_log_count += 1
                                result['debug_grant_entries'].append({
                                    'filer_ein': filer_ein,
                                    'filer_name': filer_name,
                                    'xml_filename': xml_filename,
                                    'grantee_name': grantee_name,
                                    'grant_ein': grant_ein,
                                    'grant_address': grantee_canonical_address,
                                    'grant_amt': grant_amt,
                                    'tax_year': tax_year,
                                    'status': 'skipped',
                                    'heuristic_score': best_score,
                                    'reason': "No valid EIN, address, or name"
                                })
                        except (ValueError, TypeError) as e:
                            result['debug_grant_entries'].append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'xml_filename': xml_filename,
                                'grantee_name': grantee_name,
                                'grant_ein': grant_ein,
                                'grant_address': grantee_canonical_address,
                                'grant_amt': '',
                                'tax_year': tax_year,
                                'status': 'error',
                                'reason': f"Invalid grant amount: {str(e)}"
                            })
                    else:
                        result['debug_grant_entries'].append({
                            'filer_ein': filer_ein,
                            'filer_name': filer_name,
                            'xml_filename': xml_filename,
                            'grantee_name': grantee_name,
                            'grant_ein': grant_ein,
                            'grant_address': grantee_canonical_address,
                            'grant_amt': '',
                            'tax_year': tax_year,
                            'status': 'skipped',
                            'reason': f"No grant amount for grantee {grantee_name}"
                        })
        result['grant_entries'] = grant_map
        return True
    except Exception as e:
        cu.log_error("Error parsing grants in XML {} for EIN={}: {}", xml_filename, filer_ein, str(e), exc_info=True)
        return False

def process_charity_rows(rows, worker_threads, zip_index, start_year, end_year, output_dir):
    global total_addresses, total_address_errors, total_queue_puts, total_grants, total_990pf_rows
    global results_queue, grants_queue, total_tasks_done
    zip_cache = {}
    total_skipped = 0
    results_queue = queue.Queue(maxsize=20000)
    grants_queue = queue.Queue(maxsize=50000)
    def write_grants():
        try:
            with open(os.path.join(output_dir, 'inferred_grants.tsv'), 'w', encoding='utf-8', newline='') as f:
                writer = cu.csv.writer(f, delimiter='\t')
                global total_tasks_done
                writer.writerow(cu.GRANT_COLUMNS)
                written_grants = 0
                unique_grants = set()
                with tqdm(total=total_grants, desc="Writing inferred_grants.tsv") as pbar:
                    while True:
                        grants = grants_queue.get()
                        if grants is None:
                            cu.log_error("Grant writer received None, exiting")
                            break
                        for grant in grants:
                            grant_key = (grant['filer_ein'], grant['grant_ein'], grant['grant_amt'], grant['grantee_canonical_address'])
                            if grant_key not in unique_grants:
                                unique_grants.add(grant_key)
                                writer.writerow([grant.get(col, '') for col in cu.GRANT_COLUMNS])
                                written_grants += 1
                                pbar.update(1)
                        grants_queue.task_done()
                    cu.log_error("Wrote {} unique grants to inferred_grants.tsv", written_grants)
        except Exception as e:
            cu.log_error("Error in grant writer: {}", str(e), exc_info=True)
            raise
    grant_writer = threading.Thread(target=write_grants)
    grant_writer.start()
    cu.log_error("Grants queue size before processing: {}", grants_queue.qsize())
    def process_address_row(row):
        nonlocal total_skipped
        if not hasattr(file_counter_local, 'value'):
            file_counter_local.value = 0
        if not hasattr(file_counter_local, 'skipped'):
            file_counter_local.skipped = 0
        thread_local.result = {
            'address_entries': [],
            'debug_address_entries': [],
            'po_box_entries': [],
            'invalid_ein_entries': [],
            'ein_mismatch_set': set(),
            'total_addresses': 0,
            'total_queue_puts': 0,
            'total_address_errors': 0,
            'zip_code_index': {},
            'po_box_zip_index': {},
            'filer_eins': {}
        }
        xml_path = row.get('xml_name', '')
        if not xml_path:
            cu.log_error("No xml_name for EIN={}, skipping", row['filer_ein'])
            thread_local.result['debug_address_entries'].append({
                'filer_ein': row['filer_ein'], 'filer_name': row['filer_name'], 'xml_filename': '',
                'raw_components': '', 'canonical_address': '', 'raw_zip': '', 'zip_code': '', 'status': 'skipped',
                'reason': 'Missing xml_name'})
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result
        try:
            parts = xml_path.split('/')
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                cu.log_error("No file {} in ZIP index for EIN={}, skipping (total missing: {})", xml_filename, row['filer_ein'], total_skipped + 1)
                thread_local.result['debug_address_entries'].append({
                    'filer_ein': row['filer_ein'], 'filer_name': row['filer_name'], 'xml_filename': xml_filename,
                    'raw_components': '', 'canonical_address': '', 'raw_zip': '', 'zip_code': '', 'status': 'skipped',
                    'reason': 'XML not in ZIP index'})
                file_counter_local.skipped += 1
                total_skipped += 1
                return thread_local.result
            zip_path, internal_path = zip_index[xml_filename]
            if zip_path not in zip_cache:
                zip_cache[zip_path] = cu.zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                success, filer_ein = parse_addresses(xml_content, xml_filename, row, zip_index, output_dir)
                if not success:
                    file_counter_local.skipped += 1
                    total_skipped += 1
            return thread_local.result
        except Exception as e:
            cu.log_error("Error processing address row for XML {} and EIN={}: {}", xml_path, row['filer_ein'], str(e))
            thread_local.result['debug_address_entries'].append({
                'filer_ein': row['filer_ein'], 'filer_name': row['filer_name'], 'xml_filename': xml_path,
                'raw_components': '', 'canonical_address': '', 'raw_zip': '', 'zip_code': '', 'status': 'error',
                'reason': str(e)})
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result
    def process_grant_row(xml_filename, filer_info):
        nonlocal total_skipped
        if not hasattr(file_counter_local, 'value'):
            file_counter_local.value = 0
        if not hasattr(file_counter_local, 'skipped'):
            file_counter_local.skipped = 0
        thread_local.result = {
            'grant_entries': [],
            'debug_grant_entries': [],
            'po_box_entries': [],
            'total_grants': 0,
            'total_queue_puts': 0,
            'total_990pf_rows': 0
        }
        if not filer_info:
            cu.log_error("No filer_info for grant processing, skipping")
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result
        filer_ein, row, _ = filer_info
        xml_path = row.get('xml_name', '')
        try:
            parts = xml_path.split('/')
            xml_filename = parts[-1]
            if xml_filename not in zip_index:
                cu.log_error("XML {} not in zip_index for EIN={}, skipping (total missing: {})", xml_filename, filer_ein, total_skipped + 1)
                file_counter_local.skipped += 1
                total_skipped += 1
                return thread_local.result
            zip_path, internal_path = zip_index[xml_filename]
            if zip_path not in zip_cache:
                zip_cache[zip_path] = cu.zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                parse_grants(xml_content, xml_filename, row, filer_ein, output_dir)
            return thread_local.result
        except Exception as e:
            cu.log_error("Error processing grant row for XML {} and EIN={}: {}", xml_path, filer_ein, str(e))
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result
    try:
        cu.log_error("Found {} 990PF rows in {} total rows", sum(1 for row in rows if row['form_type'] == '990PF'), len(rows))
        batch_size = args.merge_batch_size
        if args.no_threads:
            for row in tqdm(rows, desc="Processing charity addresses"):
                result = process_address_row(row)
                with zip_index_lock:
                    address_entries.extend(result['address_entries'])
                    debug_address_entries.extend(result['debug_address_entries'])
                    po_box_entries.extend(result['po_box_entries'])
                    invalid_ein_entries.extend(result['invalid_ein_entries'])
                    ein_mismatch_set.update(result['ein_mismatch_set'])
                    total_addresses += result['total_addresses']
                    total_queue_puts += result['total_queue_puts']
                    total_address_errors += result['total_address_errors']
                    for zip_code, entries in result['zip_code_index'].items():
                        if zip_code not in zip_code_index:
                            zip_code_index[zip_code] = set()
                        zip_code_index[zip_code].update(entries)
                    for po_box_zip, entries in result['po_box_zip_index'].items():
                        if po_box_zip not in po_box_zip_index:
                            po_box_zip_index[po_box_zip] = set()
                        po_box_zip_index[po_box_zip].update(entries)
                results_queue.put([result])
            for xml_filename, filer_info in tqdm(filer_eins.items(), desc="Processing grants"):
                if not filer_info[1]['form_type'] == '990PF':
                    continue
                result = process_grant_row(xml_filename, filer_info)
                with zip_index_lock:
                    debug_grant_entries.extend(result['debug_grant_entries'])
                    po_box_entries.extend(result['po_box_entries'])
                    total_grants += result['total_grants']
                    total_queue_puts += result['total_queue_puts']
                    total_990pf_rows += result['total_990pf_rows']
                grants_queue.put(result['grant_entries'])
                results_queue.put([result])
        else:
            with ThreadPoolExecutor(max_workers=worker_threads) as executor:
                futures = []
                batch = []
                for row in rows:
                    batch.append(row)
                    if len(batch) >= batch_size:
                        futures.append(executor.submit(lambda b: [process_address_row(r) for r in b], batch))
                        batch = []
                if batch:
                    futures.append(executor.submit(lambda b: [process_address_row(r) for r in b], batch))
                with tqdm(total=len(rows), desc="Processing charity addresses") as pbar:
                    for future in as_completed(futures):
                        results = future.result()
                        with zip_index_lock:
                            for result in results:
                                address_entries.extend(result['address_entries'])
                                debug_address_entries.extend(result['debug_address_entries'])
                                po_box_entries.extend(result['po_box_entries'])
                                invalid_ein_entries.extend(result['invalid_ein_entries'])
                                ein_mismatch_set.update(result['ein_mismatch_set'])
                                total_addresses += result['total_addresses']
                                total_queue_puts += result['total_queue_puts']
                                total_address_errors += result['total_address_errors']
                                for zip_code, entries in result['zip_code_index'].items():
                                    if zip_code not in zip_code_index:
                                        zip_code_index[zip_code] = set()
                                    zip_code_index[zip_code].update(entries)
                                for po_box_zip, entries in result['po_box_zip_index'].items():
                                    if po_box_zip not in po_box_zip_index:
                                        po_box_zip_index[po_box_zip] = set()
                                    po_box_zip_index[po_box_zip].update(entries)
                        results_queue.put(results)
                        pbar.update(min(batch_size, len(rows) - pbar.n))
                futures = []
                batch = []
                for xml_filename, filer_info in [(k, v) for k, v in filer_eins.items() if v[1]['form_type'] == '990PF']:
                    batch.append((xml_filename, filer_info))
                    if len(batch) >= batch_size:
                        futures.append(executor.submit(lambda b: [process_grant_row(f[0], f[1]) for f in b], batch))
                        batch = []
                if batch:
                    futures.append(executor.submit(lambda b: [process_grant_row(f[0], f[1]) for f in b], batch))
                with tqdm(total=sum(1 for k, v in filer_eins.items() if v[1]['form_type'] == '990PF'), desc="Processing grants") as pbar:
                    for future in as_completed(futures):
                        results = future.result()
                        with zip_index_lock:
                            for result in results:
                                debug_grant_entries.extend(result['debug_grant_entries'])
                                po_box_entries.extend(result['po_box_entries'])
                                total_grants += result['total_grants']
                                total_queue_puts += result['total_queue_puts']
                                total_990pf_rows += result['total_990pf_rows']
                                grants_queue.put(result['grant_entries'])
                        results_queue.put(results)
                        cu.log_error("Grants queue size after batch: {}", grants_queue.qsize())
                        pbar.update(min(batch_size, len(filer_eins) - pbar.n))
    except Exception as e:
        cu.log_error("Error in process_charity_rows: {}", str(e), exc_info=True)
        raise
    finally:
        cu.log_error("Grant pass complete: {} grants, {} 990PF rows, total skipped: {}", total_grants, total_990pf_rows, total_skipped)
        results_queue.put(None)
        grants_queue.put(None)
        for zip_file in zip_cache.values():
            zip_file.close()
        while not grants_queue.empty():
            grants_queue.get()
            grants_queue.task_done()
        grant_writer.join(timeout=300)
        cu.log_error("Grant writer finished, final grants queue size: {}", grants_queue.qsize())

def write_outputs(output_dir):
    global debug_grant_entries
    cu.write_tsv(os.path.join(output_dir, "grant_debug.tsv"), debug_grant_entries, cu.DEBUG_GRANT_COLUMNS, 'debug_grant')

def signal_handler(sig, frame):
    cu.log_error("Received interrupt signal, writing partial outputs")
    sys.exit(0)
    write_outputs(args.output_dir)

if __name__ == "__main__":
    main()
