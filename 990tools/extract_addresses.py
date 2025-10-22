import os
import glob
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import queue
from lxml import etree
from io import BytesIO
import sys
import extract_utils as cu
import re
from logging_utils import get_logger, log_info, log_error as proper_log_error, log_debug as proper_log_debug, log_error, log_debug, log_warning

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

def generate_acronym(name):
    """Generate an acronym from an organization name, handling special cases like universities."""
    if not name or len(name.split()) < 2:  # Skip single-word names
        return None
    # Common stop words to exclude
    stop_words = cu.STOP_WORDS | {'AT', 'FOR', 'WITH', 'BY'}
    # Handle special cases (e.g., universities)
    university_pattern = re.compile(r'university\s+of\s+([\w\s,]+)', re.IGNORECASE)
    match = university_pattern.match(name)
    if match:
        # For universities, use the main location (e.g., "University Of California, Los Angeles" -> "UCLA")
        location = match.group(1).strip()
        words = [w for w in location.split() if w.upper() not in stop_words and w]
        if words:
            return ''.join(w[0].upper() for w in words if w)
        return None
    # General case: take first letter of each significant word
    words = [w for w in name.split() if w.upper() not in stop_words and w]
    if not words:
        return None
    acronym = ''.join(w[0].upper() for w in words if w)
    return acronym if len(acronym) > 1 else None

def main():
    global verbose, quiet, total_addresses, total_address_errors, total_queue_puts, total_skipped
    global args, zip_index, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index
    global results_queue
    parser = argparse.ArgumentParser(description="Extract addresses from IRS 990 XML files and integrate backfill.tsv with acronym variations.")
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
    parser.add_argument("--backfill-source", type=str, default=None, help="Path to backfill.tsv or directory")
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
    # Normalize file path
    args.backfill_source = cu.normalize_file_path(args.backfill_source, 'backfill.tsv', args.output_dir)
    listener = cu.setup_logging(args.output_dir, 'extract_addresses_log.txt', verbose, quiet)
    cu.signal.signal(cu.signal.SIGINT, signal_handler)
    addr_cache_valid = True
    try:
        checksums = cu.compute_zip_checksums(args.zip_dir)
        zip_cache_valid, zip_cached_data = cu.load_zip_cache(args.cache_dir, args.start_year, args.end_year, args.zip_dir, checksums)
        if zip_cache_valid and not args.force_reprocess:
            zip_index = zip_cached_data
            if not quiet:
                cu.log_error("Loaded ZIP index cache: {} XML files", len(zip_index))
        else:
            zip_index = cu.build_zip_index(args.zip_dir, args.start_year, args.end_year)
            if not zip_index:
                if not quiet:
                    cu.log_error("No ZIP files found in {} for years {}-{}", args.zip_dir, args.start_year, args.end_year + 1)
                return
            cu.save_zip_cache(args.cache_dir, args.start_year, args.end_year, checksums, zip_index)
        addr_cache_valid, addr_cached_data = cu.load_address_cache(args.cache_dir, args.start_year, args.end_year, args.zip_dir)
        if addr_cache_valid and not args.force_reprocess:
            address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index = addr_cached_data
            if not quiet:
                cu.log_error("Loaded address cache: {} addresses, {} PO boxes, {} ZIP index entries", len(address_entries), len(po_box_entries), len(zip_code_index))
        # Load backfill.tsv
        if os.path.exists(args.backfill_source):
            backfill_rows = cu.read_tsv_files(args.backfill_source, args.start_year, args.end_year, expected_columns=cu.BACKFILL_COLUMNS)
            if backfill_rows:
                if not quiet:
                    cu.log_error("Loaded {} rows from {}", len(backfill_rows), args.backfill_source)
                seen_ein_name_zip = set()
                unique_backfill_rows = []
                acronym_count = 0
                for row in backfill_rows:
                    ein = row.get('grant_ein', '')
                    name = row.get('name', '').title()  # Title-case for consistency
                    zip_code = row.get('zip_code', '')
                    key = (ein, name, zip_code)
                    if key not in seen_ein_name_zip:
                        seen_ein_name_zip.add(key)
                        unique_backfill_rows.append(row)
                        # Generate acronym
                        acronym = generate_acronym(name)
                        if acronym and (ein, acronym, zip_code) not in seen_ein_name_zip:
                            seen_ein_name_zip.add((ein, acronym, zip_code))
                            acronym_row = {
                                'grant_ein': ein,
                                'name': acronym,
                                'street': None,
                                'city': None,
                                'state': None,
                                'canonical_address': row.get('canonical_address', ''),
                                'po_box': row.get('po_box', ''),
                                'zip_code': zip_code
                            }
                            unique_backfill_rows.append(acronym_row)
                            acronym_count += 1
                            if verbose:
                                if not quiet:
                                    cu.log_error("Generated acronym {} for EIN={}, original name={}", acronym, ein, name)
                if not quiet:
                    cu.log_error("Generated {} acronym entries", acronym_count)
                for row in unique_backfill_rows:
                    ein = row.get('grant_ein', '')
                    name = row.get('name', '').title()  # Ensure title-case for consistency
                    canonical_address = row.get('canonical_address', '')
                    po_box = row.get('po_box', '')
                    zip_code = row.get('zip_code', '')
                    if ein and canonical_address:
                        address_entries.append({
                            'filer_ein': ein,
                            'filer_name': name,
                            'street': None,
                            'city': None,
                            'state': None,
                            'canonical_address': canonical_address,
                            'po_box': po_box,
                            'zip_code': zip_code
                        })
                        if po_box and zip_code and cu.ZIP_REGEX.match(zip_code):
                            po_box_entries.append({
                                'po_box': po_box,
                                'zip_code': zip_code,
                                'ein': ein,
                                'org_name': name
                            })
                            po_box_key = (po_box, zip_code)
                            po_box_zip_index.setdefault(po_box_key, set()).add((ein, name))
                        if zip_code and cu.ZIP_REGEX.match(zip_code):
                            zip_code_index.setdefault(zip_code, set()).add((ein, name))
                if not quiet:
                    cu.log_error("Integrated {} unique backfill entries (including acronyms, after deduplication by EIN+name+zip_code) into address indices", len(unique_backfill_rows))
        else:
            if not quiet:
                cu.log_error("Backfill file {} does not exist, proceeding without backfill data", args.backfill_source)
        if not addr_cache_valid or args.force_reprocess:
            process_all_xml_addresses(args.worker_threads, zip_index, args.output_dir, args.sample_xml, args.skip_address_errors)
            cu.save_address_cache(args.cache_dir, args.start_year, args.end_year, address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index)
        write_outputs(args.output_dir, addr_cache_valid and not args.force_reprocess)
    except Exception as e:
        if not quiet:
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

def process_all_xml_addresses(worker_threads, zip_index, output_dir, sample_xml, skip_address_errors):
    global total_addresses, total_address_errors, total_queue_puts, total_skipped, results_queue
    global address_entries, debug_address_entries, po_box_entries, zip_code_index, po_box_zip_index
    results_queue = queue.Queue(maxsize=20000)
    zip_cache = {}
    def process_address_row(xml_filename):
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
        try:
            if xml_filename not in zip_index:
                if not quiet:
                    cu.log_error("No file {} in ZIP index, skipping (total missing: {})", xml_filename, total_skipped + 1)
                thread_local.result['debug_address_entries'].append({
                    'filer_ein': '',
                    'filer_name': 'Unknown',
                    'xml_filename': xml_filename,
                    'raw_components': '',
                    'canonical_address': '',
                    'raw_zip': '',
                    'zip_code': '',
                    'status': 'skipped',
                    'reason': 'XML not in ZIP index'
                })
                file_counter_local.skipped += 1
                total_skipped += 1
                return thread_local.result
            zip_path, internal_path = zip_index[xml_filename]
            if zip_path not in zip_cache:
                zip_cache[zip_path] = cu.zipfile.ZipFile(zip_path, 'r')
            with zip_cache[zip_path].open(internal_path) as xml_file:
                xml_content = xml_file.read()
                success, filer_ein = cu.parse_filer_address(xml_content, xml_filename, {}, zip_index, output_dir, sample_xml, parse_type="filer", skip_address_errors=skip_address_errors)
                if not success:
                    file_counter_local.skipped += 1
                    total_skipped += 1
            return thread_local.result
        except Exception as e:
            if not quiet:
                cu.log_error("Error processing XML {}: {}", xml_filename, str(e))
            thread_local.result['debug_address_entries'].append({
                'filer_ein': '',
                'filer_name': 'Unknown',
                'xml_filename': xml_filename,
                'raw_components': '',
                'canonical_address': '',
                'raw_zip': '',
                'zip_code': '',
                'status': 'error',
                'reason': str(e)
            })
            file_counter_local.skipped += 1
            total_skipped += 1
            return thread_local.result
    try:
        if args.no_threads:
            for xml_filename in tqdm(zip_index.keys(), desc="Processing addresses from all XMLs"):
                result = process_address_row(xml_filename)
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
                    batch.append(xml_filename)
                    if len(batch) >= batch_size:
                        futures.append(executor.submit(lambda b: [process_address_row(f) for f in b], batch))
                        batch = []
                if batch:
                    futures.append(executor.submit(lambda b: [process_address_row(f) for f in b], batch))
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
        if not quiet:
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
    if not quiet:
        cu.log_error("Received interrupt signal, writing partial outputs")
    sys.exit(0)
    write_outputs(args.output_dir, False)

if __name__ == "__main__":
    main()