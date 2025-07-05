import argparse
import os
import csv
import logging
from collections import defaultdict

# Constants
BACKFILL_COLUMNS = ["grant_ein", "name", "canonical_address", "po_box", "zip_code"]
CSV_QUOTE_FIELDS = ['filer_name', 'org_type', 'form_type', 'xml_name', 'foreign_office', 'domestic_misrep_flag']

# Logging setup
logger = None

def setup_logging(output_dir, verbose, quiet):
    global logger
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(os.path.join(output_dir, 'add_backfill_log.txt'))
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR if not verbose else logging.INFO)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers = [file_handler, console_handler] if not quiet else [file_handler]
    return logger

def log_error(msg_format, *args, exc_info=False):
    if args:
        logger.info(msg_format.format(*args), exc_info=exc_info)
    else:
        logger.info(msg_format, exc_info=exc_info)

def read_tsv(tsv_file):
    """Read a TSV file and return header and rows."""
    rows = []
    header = []
    try:
        with open(tsv_file, 'r', encoding='utf-8') as f:
            header = f.readline().strip().split('\t')
            for line in f:
                fields = line.strip().split('\t')
                if len(fields) >= len(header):
                    rows.append(dict(zip(header, fields)))
        log_error("Read {} rows from {}", len(rows), tsv_file)
        return header, rows
    except Exception as e:
        log_error("Error reading TSV {}: {}", tsv_file, str(e), exc_info=True)
        return [], []

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Combine charity_latest.tsv with backfill.tsv to produce charity_latest_with_backfill.tsv/csv.\n"
            "Backfill rows are added with grant_ein as filer_ein, name as filer_name, form_type and xml_name as 'backfill', and other columns as 'n/a'."
        )
    )
    parser.add_argument("--charity-tsv", type=str, default="./charity_latest.tsv", help="Path to charity_latest.tsv")
    parser.add_argument("--backfill-tsv", type=str, default="./backfill.tsv", help="Path to backfill.tsv")
    parser.add_argument("--output-dir", type=str, default=".", help="Directory for output TSV and CSV files")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Disable all logging")
    args = parser.parse_args()

    # Validate arguments
    if not os.path.isfile(args.charity_tsv):
        raise ValueError(f"Charity TSV file {args.charity_tsv} does not exist")
    if not os.path.isfile(args.backfill_tsv):
        raise ValueError(f"Backfill TSV file {args.backfill_tsv} does not exist")
    if not os.path.isdir(args.output_dir):
        os.makedirs(args.output_dir)

    logger = setup_logging(args.output_dir, args.verbose, args.quiet)

    try:
        # Read charity_latest.tsv
        charity_header, charity_rows = read_tsv(args.charity_tsv)
        if not charity_header:
            log_error("No header found in {}. Exiting.", args.charity_tsv)
            return

        # Read backfill.tsv
        _, backfill_rows = read_tsv(args.backfill_tsv)
        
        # Track existing EINs to avoid duplicates
        existing_eins = {row['filer_ein'] for row in charity_rows}
        
        # Create combined rows
        combined_rows = charity_rows.copy()
        backfill_count = 0
        for backfill_row in backfill_rows:
            ein = backfill_row.get('grant_ein', '')
            if not ein or ein in existing_eins:
                if args.verbose:
                    log_error("Skipping backfill EIN {}: already exists or invalid", ein)
                continue
            new_row = {col: 'n/a' for col in charity_header}
            new_row['filer_ein'] = ein
            new_row['filer_name'] = backfill_row.get('name', 'Unknown')
            new_row['form_type'] = 'backfill'
            new_row['xml_name'] = 'backfill'
            combined_rows.append(new_row)
            existing_eins.add(ein)
            backfill_count += 1
            if args.verbose:
                log_error("Added backfill row for EIN={}", ein)

        # Write output TSV
        output_tsv = os.path.join(args.output_dir, 'charity_latest_with_backfill.tsv')
        with open(output_tsv, 'w', encoding='utf-8') as f:
            f.write('\t'.join(charity_header) + '\n')
            for row in combined_rows:
                f.write('\t'.join(str(row.get(col, '')) for col in charity_header) + '\n')
        log_error("Wrote {} rows (including {} backfill rows) to {}", len(combined_rows), backfill_count, output_tsv)

        # Write output CSV
        output_csv = os.path.join(args.output_dir, 'charity_latest_with_backfill.csv')
        with open(output_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(charity_header)
            for row in combined_rows:
                csv_row = []
                for col in charity_header:
                    value = str(row.get(col, ''))
                    if col in CSV_QUOTE_FIELDS:
                        csv_row.append(value)
                    else:
                        csv_row.append(value)
                writer.writerow(csv_row)
        log_error("Wrote {} rows (including {} backfill rows) to {}", len(combined_rows), backfill_count, output_csv)

        print(f"Combined {len(charity_rows)} charity rows with {backfill_count} backfill rows")
        print(f"Output written to {output_tsv} and {output_csv}")
        print(f"Log file written to {os.path.join(args.output_dir, 'add_backfill_log.txt')}")

    except Exception as e:
        log_error("Error during processing: {}", str(e), exc_info=True)
        raise

if __name__ == "__main__":
    main()