#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path
from collections import defaultdict
import re

def format_ein(ein):
    """Format a 9-digit EIN as XX-XXXXXXX, return unchanged if not 9 digits."""
    if re.match(r'^\d{9}$', ein):
        return f"{ein[:2]}-{ein[2:]}"
    return ein

def google_search_link(ein):
    """Return a Google search hyperlink for the EIN, or plain text if not a 9-digit EIN."""
    formatted_ein = format_ein(ein)
    if re.match(r'^\d{9}$', ein):
        # Escape quotes and encode spaces for URL
        search_query = f"{formatted_ein}+ein".replace(" ", "+")
        return f'<a href="https://www.google.com/search?q={search_query}">{formatted_ein}</a>'
    return formatted_ein

def normalize_ein(ein):
    """Remove hyphens and whitespace from EIN for consistent matching."""
    return re.sub(r'[- ]', '', ein)

def index_and_filter(index_file, input_file, output_file, report_file=None, report_depth=10):
    try:
        # Build index of filer_ein
        filer_ein_set = set()
        with open(index_file, 'r') as f:
            header = f.readline().strip()
            columns = header.split('\t')
            try:
                filer_ein_idx = columns.index('filer_ein')
            except ValueError:
                raise ValueError("filer_ein column not found in index file")

            for line in f:
                fields = line.strip().split('\t')
                if len(fields) > filer_ein_idx and fields[filer_ein_idx]:
                    filer_ein_set.add(normalize_ein(fields[filer_ein_idx]))

        # Initialize reporting data structures if report_file is specified
        grant_ein_counts = defaultdict(int) if report_file else None
        grant_ein_amts = defaultdict(float) if report_file else None
        total_rows = 0
        filtered_rows = 0

        # Filter input file and collect report data for filtered-out rows
        with open(input_file, 'r') as f, open(output_file, 'w') as out:
            header = f.readline().strip()
            columns = header.split('\t')
            try:
                grant_ein_idx = columns.index('grant_ein')
                grant_amt_idx = columns.index('grant_amt')
            except ValueError:
                raise ValueError("grant_ein or grant_amt column not found in input file")

            # Write header to output
            out.write(header + '\n')

            # Process and filter rows
            for line in f:
                total_rows += 1
                fields = line.strip().split('\t')
                if len(fields) <= max(grant_ein_idx, grant_amt_idx):
                    continue
                grant_ein_normalized = normalize_ein(fields[grant_ein_idx])
                # Keep rows where grant_ein is in filer_ein_set or is a 3-digit numeric code
                if (grant_ein_normalized in filer_ein_set or
                    (len(grant_ein_normalized) == 3 and grant_ein_normalized.isdigit())):
                    out.write(line)
                else:
                    filtered_rows += 1
                    # Log filtered-out rows for debugging
                    if report_file:
                        print(f"Filtered out grant_ein: {fields[grant_ein_idx]} (normalized: {grant_ein_normalized})", file=sys.stderr)
                        grant_ein_counts[fields[grant_ein_idx]] += 1
                        try:
                            grant_amt = float(fields[grant_amt_idx])
                            grant_ein_amts[fields[grant_ein_idx]] += grant_amt
                        except ValueError:
                            continue

        print(f"Filtered TSV written to {output_file}")
        print(f"Original rows: {total_rows}, Rows not in index: {filtered_rows}")

        # Generate report if requested
        if report_file:
            print("Generating report...")
            markdown = "# Filtered-Out Grants Report\n\n"

            # Top 10 grant_ein by count
            markdown += f"## Top {report_depth} grant_ein by Count\n"
            markdown += "| grant_ein | Count | Total Grant Amount |\n"
            markdown += "|-----------|-------|--------------------|\n"
            top_grant_eins = sorted(grant_ein_counts.items(), key=lambda x: (-x[1], x[0]))[:report_depth]
            for grant_ein, count in top_grant_eins:
                grant_ein_esc = google_search_link(grant_ein).replace('|', '\\|')
                total_amt = grant_ein_amts[grant_ein]
                markdown += f"| {grant_ein_esc} | {count} | {total_amt:.2f} |\n"
            markdown += "\n"

            # Top 10 grant_ein by total grant_amt
            markdown += f"## Top {report_depth} grant_ein by Total Grant Amount\n"
            markdown += "| grant_ein | Count | Total Grant Amount |\n"
            markdown += "|-----------|-------|--------------------|\n"
            top_amt_grant_eins = sorted(grant_ein_amts.items(), key=lambda x: (-x[1], x[0]))[:report_depth]
            for grant_ein, total_amt in top_amt_grant_eins:
                grant_ein_esc = google_search_link(grant_ein).replace('|', '\\|')
                count = grant_ein_counts[grant_ein]
                markdown += f"| {grant_ein_esc} | {count} | {total_amt:.2f} |\n"

            with open(report_file, 'w') as f:
                f.write(markdown)
            print(f"Report written to {report_file}")

    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

def main(index_file, input_file, output_file, report_file=None, report_depth=1000, verbose=False, quiet=False):
    """Main function for checking and filtering grant data."""
    # Validate input files exist
    if not Path(index_file).is_file():
        raise FileNotFoundError(f"Index file '{index_file}' not found")
    if not Path(input_file).is_file():
        raise FileNotFoundError(f"Input file '{input_file}' not found")

    if not quiet:
        print(f"Checking and filtering grants from {input_file}")
        print(f"Using index file: {index_file}")
        print(f"Output: {output_file}")

    index_and_filter(index_file, input_file, output_file, report_file, report_depth)

    if not quiet:
        print("Grant checking complete.")

if __name__ == '__main__':
    # For backward compatibility when run directly
    parser = argparse.ArgumentParser(description='Filter TSV based on filer_ein index and optionally generate report for filtered-out rows')
    parser.add_argument('--index-file', help='TSV file containing filer_ein to index', required=True)
    parser.add_argument('--input-file', help='TSV file to filter based on grant_ein', required=True)
    parser.add_argument('--output-file', help='Output filtered TSV file', required=True)
    parser.add_argument('--report-file', help='Output Markdown file for report of filtered-out rows', default=None)
    parser.add_argument('--report-depth', help="Top N", default=1000, type=int)
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('--quiet', action='store_true', help='Suppress non-error output')

    args = parser.parse_args()
    main(args.index_file, args.input_file, args.output_file, args.report_file, args.report_depth, args.verbose, args.quiet)