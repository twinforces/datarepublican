import argparse
import os
import csv
import logging
import subprocess
import tempfile
import shutil
import extract_utils as cu
from extract_utils import perform_batch_geocoding, get_colocator_for_address

# Constants
BACKFILL_COLUMNS = ["grant_ein", "name", "canonical_address", "po_box", "zip_code"]
CSV_QUOTE_FIELDS = ['filer_name', 'org_type', 'form_type', 'xml_name', 'foreign_office', 'domestic_misrep_flag', 'colocator']

# Logging setup
logger = None

def setup_logging(output_dir, verbose, quiet):
    global logger
    return cu.setup_logging(output_dir, 'add_backfill_log.txt', verbose, quiet)

def log_error(msg_format, *args, exc_info=False):
    cu.log_error(msg_format, *args, exc_info=exc_info)

def sort_backfill_tsv(backfill_tsv, temp_dir):
    """Sort backfill.tsv by grant_ein and name length (descending), preserving the header."""
    try:
        # Read the header
        with open(backfill_tsv, 'r', encoding='utf-8') as f:
            header = f.readline().strip()
        # Create a temporary file for sorted output
        with tempfile.NamedTemporaryFile(delete=False, mode='w', dir=temp_dir, suffix='.tsv') as temp_file:
            sorted_file = temp_file.name
        # Sort data rows (skip header) by grant_ein (field 1) and name length (field 2, descending)
        sort_command = f"tail -n +2 {backfill_tsv} | sort -k1,1 -k2,2r > {sorted_file}"
        subprocess.run(sort_command, shell=True, check=True)
        # Prepend the header to the sorted file
        with open(sorted_file, 'r', encoding='utf-8') as f:
            sorted_content = f.read()
        with open(sorted_file, 'w', encoding='utf-8') as f:
            f.write(header + '\n' + sorted_content)
        return sorted_file
    except Exception as e:
        log_error("Error sorting backfill TSV {}: {}", backfill_tsv, str(e), exc_info=True)
        raise

def process_backfill_rows(sorted_backfill_file, charity_header):
    """Process sorted backfill.tsv to select the longest name per EIN."""
    unique_rows = []
    seen_eins = set()
    with open(sorted_backfill_file, 'r', encoding='utf-8') as f:
        header = f.readline().strip().split('\t')
        if not all(col in header for col in ['grant_ein', 'name']):
            log_error("Missing required columns in backfill TSV header: {}", header)
            return []
        backfill_count = 0
        for line in f:
            fields = line.strip().split('\t')
            if len(fields) < len(header):
                continue
            row = dict(zip(header, fields))
            ein = row.get('grant_ein', '')
            name = row.get('name', 'Unknown')
            if not ein:
                if args.verbose:
                    log_error("Skipping backfill row with missing EIN")
                continue
            backfill_count += 1
            if ein not in seen_eins:
                seen_eins.add(ein)
                new_row = {col: 'n/a' for col in charity_header}
                new_row['filer_ein'] = ein
                new_row['filer_name'] = name
                new_row['form_type'] = 'backfill'
                new_row['xml_name'] = 'backfill'

                # Generate colocator using geocoding for backfill entries
                canonical_address = row.get('canonical_address', '')
                po_box = row.get('po_box', '')
                zip_code = row.get('zip_code', '')
                if canonical_address or po_box or zip_code:
                    address_dict = {'canonical': canonical_address, 'po_box': po_box, 'zip_code': zip_code}
                    colocator = get_colocator_for_address(address_dict)
                    new_row['colocator'] = colocator
                else:
                    new_row['colocator'] = ''

                unique_rows.append(new_row)
                if args.verbose:
                    log_error("Selected backfill row for EIN={}, Name={}", ein, name)
        if args.verbose:
            log_error("Processed {} backfill rows, selected {} unique EINs", backfill_count, len(unique_rows))
    return unique_rows

def main(charity_tsv=None, backfill_tsv=None, output_dir=".", verbose=False, quiet=False):
    """Main function for adding backfill data to charity records."""
    global args

    # Create a mock args object for compatibility with existing functions
    class MockArgs:
        def __init__(self, charity_tsv, backfill_tsv, output_dir, verbose, quiet):
            self.charity_tsv = cu.normalize_file_path(charity_tsv, 'charity_latest.tsv', output_dir)
            self.backfill_tsv = cu.normalize_file_path(backfill_tsv, 'backfill.tsv', output_dir)
            self.output_dir = output_dir
            self.verbose = verbose
            self.quiet = quiet

    args = MockArgs(charity_tsv, backfill_tsv, output_dir, verbose, quiet)

    # Validate arguments
    if not os.path.isfile(args.charity_tsv):
        raise ValueError(f"Charity TSV file {args.charity_tsv} does not exist")
    if not os.path.isfile(args.backfill_tsv):
        raise ValueError(f"Backfill TSV file {args.backfill_tsv} does not exist")
    if not os.path.isdir(args.output_dir):
        os.makedirs(args.output_dir)

    logger = setup_logging(args.output_dir, args.verbose, args.quiet)

    if not quiet:
        print("Adding backfill data to charity records...")

    # Perform batch geocoding before processing backfill
    perform_batch_geocoding()

    try:
        # Read charity_latest.tsv header
        with open(args.charity_tsv, 'r', encoding='utf-8') as f:
            charity_header = f.readline().strip().split('\t')
        if not charity_header:
            log_error("No header found in {}. Exiting.", args.charity_tsv)
            return

        # Sort backfill.tsv by grant_ein and name length
        temp_dir = os.path.join(args.output_dir, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        sorted_backfill_file = sort_backfill_tsv(args.backfill_tsv, temp_dir)

        # Process sorted backfill rows to select longest name per EIN
        unique_rows = process_backfill_rows(sorted_backfill_file, charity_header)
        backfill_count = len(unique_rows)

        # Copy charity_latest.tsv to output TSV and append backfill rows
        output_tsv = os.path.join(args.output_dir, 'charity_latest_with_backfill.tsv')
        shutil.copyfile(args.charity_tsv, output_tsv)
        with open(output_tsv, 'a', encoding='utf-8') as f:
            for row in unique_rows:
                f.write('\t'.join(str(row.get(col, '')) for col in charity_header) + '\n')
        log_error("Appended {} backfill rows to {}", backfill_count, output_tsv)

        # Write output CSV
        output_csv = os.path.join(args.output_dir, 'charity_latest_with_backfill.csv')
        shutil.copyfile(args.charity_tsv, output_csv)
        with open(output_csv, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            for row in unique_rows:
                csv_row = []
                for col in charity_header:
                    value = str(row.get(col, ''))
                    if col in CSV_QUOTE_FIELDS:
                        csv_row.append(value)
                    else:
                        csv_row.append(value)
                writer.writerow(csv_row)
        log_error("Appended {} backfill rows to {}", backfill_count, output_csv)

        # Clean up temporary file
        os.remove(sorted_backfill_file)
        if os.path.exists(temp_dir) and not os.listdir(temp_dir):
            os.rmdir(temp_dir)

        if not quiet:
            print(f"Appended {backfill_count} backfill rows to charity data")
            print(f"Output written to {output_tsv} and {output_csv}")
            print(f"Log file written to {os.path.join(args.output_dir, 'add_backfill_log.txt')}")

    except Exception as e:
        log_error("Error during processing: {}", str(e), exc_info=True)
        raise

if __name__ == "__main__":
    # For backward compatibility when run directly
    parser = argparse.ArgumentParser(
        description=(
            "Combine charity_latest.tsv with backfill.tsv to produce charity_latest_with_backfill.tsv/csv.\n"
            "Selects the longest name per EIN from backfill.tsv and appends to the output without loading charity_latest.tsv into memory."
        )
    )
    parser.add_argument("--charity-tsv", type=str, default=None, help="Path to charity_latest.tsv or directory")
    parser.add_argument("--backfill-tsv", type=str, default=None, help="Path to backfill.tsv or directory")
    parser.add_argument("--output-dir", type=str, default=".", help="Directory for output TSV and CSV files")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")

    parsed_args = parser.parse_args()
    main(parsed_args.charity_tsv, parsed_args.backfill_tsv, parsed_args.output_dir,
         parsed_args.verbose, parsed_args.quiet)