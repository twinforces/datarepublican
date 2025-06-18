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
total_addresses = 0
total_address_errors = 0
total_queue_puts = 0
total_skipped = 0
results_queue = None
zip_index_lock = threading.Lock()
address_entries = []
debug_address_entries = []
invalid_ein_entries = []
po_box_entries = []
zip_index = {}
zip_code_index = {}
po_box_zip_index = {}
ein_mismatch_set = set()
thread_local = threading.local()
file_counter_local = threading.local()
filer_eins = {}

def main():
    global verbose, quiet, total_addresses, total_address_errors, total_queue_puts, total_skipped
    global args, zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index
    global results_queue
    parser = argparse.ArgumentParser(description="Extract addresses from IRS 990 XML files.")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--zip-dir", type=str, default="..", help="Directory containing ZIP files")
    parser.add_argument("--cache-dir", type=str, default="./_cache", help="Directory for cache files")
    parser.add_argument("--output-dir", type=str, default="./_output", help="Directory for output TSV files")
    parser.add_argument("--force-reprocess", action="store_true", help="Force reprocessing despite cache")
    parser.add_argument("--merge-batch-size", type=int, default=1000, help="Batch size for queuing results")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    parser.add_argument("--no-threads", action="store_true", help="Run single-threaded")
    parser.add_argument("--worker-threads", type=int, default=16, help="Number of worker threads")
    parser.add_argument("--skip-address-errors", action="store_true", help="Continue despite address errors")
    parser.add_argument("--sample-xml", type=str, default=None, help="Directory to save failing XMLs")
    parser.add_argument("--log-zip-errors", action="store_true", help="Log invalid ZIP codes")
    args = parser.parse_args()
    verbose = args.verbose
    quiet = args.quiet
    skip_address_errors = args.skip_address_errors
    sample_xml = args.sample_xml
    log_zip_errors = args.log_zip_errors
    if args.no_threads:
        args.worker_threads = 1
    os.makedirs(args.cache_dir, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)
    if not os.path.isdir(args.zip_dir):
        raise ValueError(f"ZIP directory {args.zip_dir} does not exist")
    listener = cu.setup_logging(args.output_dir, 'extract_addresses_log.txt', verbose, quiet)
    cu.signal.signal(cu.signal.SIGINT, signal_handler)
    try:
        checksums = cu.compute_zip_checksums(args.zip_dir)
        zip_cache_valid, zip_cached_data = cu.load_zip_cache(args.cache_dir, args.start_year, args.end_year, args.zip_dir, checksums)
        if zip_cache_valid and not args.force_reprocess:
            zip_index = zip_cached_data
            cu.log_error("Loaded ZIP index cache: {} XML files", len(zip_index))
        else:
            zip_index = cu.build_zip_index(args.zip_dir, args.start_year, args.end_year)
            if not zip_index:
                cu.log_error("No ZIP files found in {} for years {}-{}", args.zip_dir, args.start_year, args.end_year + 1)
                return
            cu.save_zip_cache(args.cache_dir, args.start_year, args.end_year, checksums, zip_index)
        addr_cache_valid, addr_cached_data = cu.load_address_cache(args.cache_dir, args.start_year, args.end_year, args.zip_dir)
        if addr_cache_valid and not args.force_reprocess:
            address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index = addr_cached_data
            cu.log_error("Loaded address cache: {} addresses, {} PO boxes, {} ZIP index entries", len(address_entries), len(po_box_entries), len(zip_code_index))
        else:
            process_all_xml_addresses(args.worker_threads, zip_index, args.output_dir)
            cu.save_address_cache(args.cache_dir, args.start_year, args.end_year, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index)
        write_outputs(args.output_dir, addr_cache_valid and not args.force_reprocess)
    except Exception as e:
        cu.log_error("Error during processing: {}", str(e), exc_info=True)
        write_outputs(args.output_dir, addr_cache_valid and not args.force_reprocess)
    finally:
        listener.stop()
    log_file = os.path.join(args.output_dir, 'extract_addresses_log.txt')
    print(f"Log file written to: {log_file}")
    print(f"Total addresses extracted: {len(address_entries)}")
    print(f"Total address errors: {total_address_errors}")
    print(f"Queue summary: {total_queue_puts} items put")
    print(f"Invalid EINs found: {len(invalid_ein_entries)}")
    print(f"PO Boxes found: {len(po_box_entries)}")
    print(f"Output files in: {args.output_dir}")

def parse_addresses(xml_content, xml_filename, row, zip_index, output_dir):
    global total_addresses, total_address_errors, total_queue_puts
    if not hasattr(thread_local, 'result'):
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
    result = thread_local.result
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        tsv_ein = row['filer_ein'].strip()
        filer_name = row['filer_name'].strip()
        tax_year = row['tax_year'].strip()
        xml_ein = None
        for xpath in cu.FILER_EIN_XPATHS:
            elem = xpath(root)
            if elem and elem[0].text:
                xml_ein = elem[0].text.strip()
                break
        if not xml_ein or not cu.EIN_REGEX.match(xml_ein):
            result['invalid_ein_entries'].append({
                'tsv_ein': tsv_ein,
                'xml_ein': xml_ein or '',
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'reason': 'No or invalid EIN in XML'
            })
            result['debug_address_entries'].append({
                'filer_ein': tsv_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'raw_components': '',
                'canonical_address': '',
                'raw_zip': '',
                'zip_code': '',
                'status': 'skipped',
                'reason': f"No or invalid EIN: {xml_ein or 'None'}"
            })
            return False, None
        if tsv_ein != xml_ein and tsv_ein not in result['ein_mismatch_set']:
            result['invalid_ein_entries'].append({
                'tsv_ein': tsv_ein,
                'xml_ein': xml_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'reason': 'TSV EIN differs from XML EIN'
            })
            result['ein_mismatch_set'].add(tsv_ein)
        filer_ein = xml_ein
        address_components = []
        for xpath in cu.ADDRESS_XPATHS:
            elements = xpath(root)
            for elem in elements:
                if elem.text:
                    address_components.append(elem)
        canonical_address, po_box, zip_code, _ = cu.canonicalize_address(address_components, output_dir)
        if canonical_address:
            for abbr, full in cu.USPS_FIXES.items():
                canonical_address = canonical_address.replace(f'{abbr} ', f'{full} ')
        raw_components_str = ";".join(elem.text.strip() for elem in address_components if elem.text)
        us_address = root.find(".//irs:Filer/irs:USAddress", namespaces=cu.NAMESPACES)
        address_snippet = etree.tostring(us_address if us_address is not None else root, encoding='unicode', method='xml', pretty_print=True)[:500]
        if canonical_address:
            result['address_entries'].append({
                'filer_ein': filer_ein,
                'filer_name': filer_name,
                'canonical_address': canonical_address,
                'tax_year': tax_year,
                'po_box': po_box,
                'zip_code': zip_code
            })
            if po_box and zip_code and cu.ZIP_REGEX.match(zip_code):
                result['po_box_entries'].append({
                    'po_box': po_box,
                    'zip_code': zip_code,
                    'ein': filer_ein,
                    'org_name': filer_name
                })
                po_box_key = (po_box, zip_code)
                if po_box_key not in result['po_box_zip_index']:
                    result['po_box_zip_index'][po_box_key] = set()
                result['po_box_zip_index'][po_box_key].add((filer_ein, filer_name))
            if zip_code and cu.ZIP_REGEX.match(zip_code):
                if zip_code not in result['zip_code_index']:
                    result['zip_code_index'][zip_code] = set()
                result['zip_code_index'][zip_code].add((filer_ein, filer_name))
            result['total_addresses'] += 1
            result['total_queue_puts'] += 1
        else:
            result['total_address_errors'] += 1
            result['debug_address_entries'].append({
                'filer_ein': filer_ein,
                'filer_name': filer_name,
                'xml_filename': xml_filename,
                'raw_components': raw_components_str,
                'canonical_address': '',
                'raw_zip': '',
                'zip_code': '',
                'status': 'error',
                'reason': f"Invalid address; components={address_components}; snippet={address_snippet}"
            })
            if sample_xml:
                os.makedirs(sample_xml, exist_ok=True)
                with open(os.path.join(sample_xml, xml_filename), 'wb') as f:
                    f.write(xml_content)
            if not skip_address_errors:
                return False, None
        with zip_index_lock:
            filer_eins[xml_filename] = (filer_ein, row, canonical_address)
        return True, filer_ein
    except Exception as e:
        cu.log_error("Error parsing XML {} for EIN={}: {}", xml_filename, row['filer_ein'], str(e), exc_info=True)
        result['total_address_errors'] += 1
        result['debug_address_entries'].append({
            'filer_ein': row['filer_ein'],
            'filer_name': row['filer_name'],
            'xml_filename': xml_filename,
            'raw_components': '',
            'canonical_address': '',
            'raw_zip': '',
            'zip_code': '',
            'status': 'error',
            'reason': str(e)
        })
        return False, None

def process_all_xml_addresses(worker_threads, zip_index, output_dir):
    global total_addresses, total_address_errors, total_queue_puts, total_skipped, results_queue
    global address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index
    results_queue = queue.Queue(maxsize=20000)
    zip_cache = {}
    def process_address_row(row):
        global total_skipped
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
            cu.log_error("No xml_name, skipping")
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
                cu.log_error("No file {} in ZIP index, skipping (total missing: {})", xml_filename, total_skipped + 1)
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
    try:
        if args.no_threads:
            for xml_filename in tqdm(zip_index.keys(), desc="Processing addresses from all XMLs"):
                result = process_address_row({'xml_name': xml_filename, 'filer_ein': 'UNKNOWN', 'filer_name': 'UNKNOWN', 'tax_year': 'UNKNOWN'})
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
        else:
            with ThreadPoolExecutor(max_workers=worker_threads) as executor:
                futures = []
                batch = []
                batch_size = args.merge_batch_size
                for xml_filename in zip_index.keys():
                    batch.append({'xml_name': xml_filename, 'filer_ein': 'UNKNOWN', 'filer_name': 'UNKNOWN', 'tax_year': 'UNKNOWN'})
                    if len(batch) >= batch_size:
                        futures.append(executor.submit(lambda b: [process_address_row(r) for r in b], batch))
                        batch = []
                if batch:
                    futures.append(executor.submit(lambda b: [process_address_row(r) for r in b], batch))
                with tqdm(total=len(zip_index), desc="Processing addresses from all XMLs") as pbar:
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
                        pbar.update(min(batch_size, len(zip_index) - pbar.n))
    except Exception as e:
        cu.log_error("Error in process_all_xml_addresses: {}", str(e), exc_info=True)
        raise
    finally:
        for zip_file in zip_cache.values():
            zip_file.close()

def write_outputs(output_dir, addresses_cached):
    global address_entries, debug_address_entries, invalid_ein_entries, po_box_entries
    if not addresses_cached:
        cu.write_tsv(os.path.join(output_dir, "charity_addresses.tsv"), address_entries, cu.ADDRESS_COLUMNS, 'addresses', ['filer_ein', 'filer_name', 'canonical_address'])
    cu.write_tsv(os.path.join(output_dir, "address_debug.tsv"), debug_address_entries, cu.DEBUG_ADDRESS_COLUMNS, 'debug_address')
    cu.write_tsv(os.path.join(output_dir, "invalid_eins.tsv"), invalid_ein_entries, cu.INVALID_EIN_COLUMNS, 'invalid_eins', ['tsv_ein', 'xml_ein', 'filer_name', 'xml_filename'])
    cu.write_tsv(os.path.join(output_dir, "po_box_matches.tsv"), po_box_entries, cu.PO_BOX_COLUMNS, 'po_box_matches', ['po_box', 'zip_code', 'ein', 'org_name'])

def signal_handler(sig, frame):
    cu.log_error("Received interrupt signal, writing partial outputs")
    sys.exit(0)
    write_outputs(args.output_dir, False)

if __name__ == "__main__":
    main()
