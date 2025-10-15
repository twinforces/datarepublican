#!/usr/bin/env python3
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict
from countryCodes import iso3166_alpha2

def generate_report(input_file, report_file):
    try:
        # Initialize data structures
        grant_ein_counts = defaultdict(int)  # grant_ein -> count
        grant_ein_amts = defaultdict(float)  # grant_ein -> total grant_amt
        country_code_counts = defaultdict(int)  # 3-digit code -> count
        country_code_amts = defaultdict(float)  # 3-digit code -> total grant_amt
        pattern = re.compile(r'Address:|Unknown', re.IGNORECASE)
        total_rows = 0
        filtered_rows = 0

        # Map 3-digit codes to country names
        code_to_name = {v['number']: v['name'] for k, v in iso3166_alpha2.items()}

        # Get header and column indices
        with open(input_file, 'r') as f:
            header = f.readline().strip()
            columns = header.split('\t')
            try:
                filer_ein_idx = columns.index('filer_ein')
                grant_ein_idx = columns.index('grant_ein')
                grant_amt_idx = columns.index('grant_amt')
            except ValueError as e:
                raise ValueError("Required columns (filer_ein, grant_ein, grant_amt) not found in TSV")

            # Check if colocator column exists
            colocator_idx = None
            try:
                colocator_idx = columns.index('colocator')
            except ValueError:
                pass  # colocator column is optional

        # Process lines
        print("Reading TSV for report generation...")
        with open(input_file, 'r') as f:
            f.readline()  # Skip header
            for line in f:
                if pattern.search(line):
                    continue
                total_rows += 1
                fields = line.strip().split('\t')
                if len(fields) <= max(filer_ein_idx, grant_ein_idx, grant_amt_idx):
                    continue  # Skip malformed lines
                grant_ein = fields[grant_ein_idx]
                try:
                    grant_amt = float(fields[grant_amt_idx])
                except ValueError:
                    continue  # Skip lines with invalid grant_amt
                # Count and sum for grant_ein
                grant_ein_counts[grant_ein] += 1
                grant_ein_amts[grant_ein] += grant_amt
                # Count and sum for 3-digit country codes
                if re.match(r'^\d{3}$', grant_ein):
                    country_code_counts[grant_ein] += 1
                    country_code_amts[grant_ein] += grant_amt
                filtered_rows += 1

        # Generate Markdown report
        print("Generating report...")
        markdown = "# Analysis Report\n\n"

        # Top 10 grant_ein by count
        markdown += "## Top 10 grant_ein by Count\n"
        markdown += "| grant_ein | Count | Total Grant Amount |\n"
        markdown += "|-----------|-------|--------------------|\n"
        top_grant_eins = sorted(grant_ein_counts.items(), key=lambda x: (-x[1], x[0]))[:10]
        for grant_ein, count in top_grant_eins:
            grant_ein_esc = grant_ein.replace('|', '\\|')
            total_amt = grant_ein_amts[grant_ein]
            markdown += f"| {grant_ein_esc} | {count} | {total_amt:.2f} |\n"
        markdown += "\n"

        # Top 10 grant_ein by total grant_amt
        markdown += "## Top 10 grant_ein by Total Grant Amount\n"
        markdown += "| grant_ein | Count | Total Grant Amount |\n"
        markdown += "|-----------|-------|--------------------|\n"
        top_amt_grant_eins = sorted(grant_ein_amts.items(), key=lambda x: (-x[1], x[0]))[:10]
        for grant_ein, total_amt in top_amt_grant_eins:
            grant_ein_esc = grant_ein.replace('|', '\\|')
            count = grant_ein_counts[grant_ein]
            markdown += f"| {grant_ein_esc} | {count} | {total_amt:.2f} |\n"
        markdown += "\n"

        # Only include country code sections if at least one 3-digit code was found
        if country_code_counts:
            # 3-digit country codes by count
            markdown += "## 3-Digit grant_ein Country Codes\n"
            markdown += "| Country Code | Country Name | Count | Total Grant Amount |\n"
            markdown += "|--------------|--------------|-------|--------------------|\n"
            sorted_codes = sorted(country_code_counts.items(), key=lambda x: (-x[1], x[0]))[:10]
            for code, count in sorted_codes:
                name = code_to_name.get(code, "Unknown")
                total_amt = country_code_amts[code]
                markdown += f"| {code} | {name} | {count} | {total_amt:.2f} |\n"
            markdown += "\n"

            # Top 10 3-digit country codes by total grant_amt
            markdown += "## Top 10 3-Digit grant_ein Country Codes by Total Grant Amount\n"
            markdown += "| Country Code | Country Name | Count | Total Grant Amount |\n"
            markdown += "|--------------|--------------|-------|--------------------|\n"
            sorted_amt_codes = sorted(country_code_amts.items(), key=lambda x: (-x[1], x[0]))[:10]
            for code, total_amt in sorted_amt_codes:
                name = code_to_name.get(code, "Unknown")
                count = country_code_counts[code]
                markdown += f"| {code} | {name} | {count} | {total_amt:.2f} |\n"

        with open(report_file, 'w') as f:
            f.write(markdown)

        print(f"Report written to {report_file}")
        print(f"Original rows: {total_rows}, Processed rows: {filtered_rows}")

    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

def main(input_file, report_file='report.md', verbose=False, quiet=False):
    """Main function for generating grant reports."""
    from utils.args import find_file_path
    import os

    # Try to find file in multiple possible locations
    data_root = '/Volumes/Data'
    possible_dirs = [
        os.path.join(data_root, 'final'),
        os.path.join(data_root, 'tsvs'),
        os.path.join(data_root, 'atsvs'),
        data_root,
        '.'
    ]

    # Find actual file path
    actual_input_file = find_file_path(possible_dirs, os.path.basename(input_file), "input file")

    print(f"Looking for input_file: {input_file}")
    print(f"Found input_file: {actual_input_file}, exists: {Path(actual_input_file).is_file()}")

    if not Path(actual_input_file).is_file():
        raise FileNotFoundError(f"Input file '{actual_input_file}' not found")

    # Use the found file
    input_file = actual_input_file

    if not quiet:
        print(f"Generating grant report from {input_file}")
        print(f"Output: {report_file}")

    generate_report(input_file, report_file)

    if not quiet:
        print("Report generation complete.")

if __name__ == '__main__':
    # For backward compatibility when run directly
    parser = argparse.ArgumentParser(description='Generate report from TSV file')
    parser.add_argument('--input-file', help='Input TSV file', required=True)
    parser.add_argument('--report-file', help='Output Markdown file for report', default='report.md')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('--quiet', action='store_true', help='Suppress non-error output')

    args = parser.parse_args()
    main(args.input_file, args.report_file, args.verbose, args.quiet)