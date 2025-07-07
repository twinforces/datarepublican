#!/usr/bin/env python3
import re
import sys
import argparse
from pathlib import Path
from tqdm import tqdm
from collections import defaultdict
from countryCodes import iso3166_alpha2

def clean_tsv(input_file, output_file, report_md):
    try:
        # Initialize data structures
        agg_data = defaultdict(float)  # (filer_ein, grant_ein) -> grant_amt sum
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

        # Process lines
        print("Reading and filtering TSV...")
        with open(input_file, 'r') as f:
            f.readline()  # Skip header
            for line in tqdm(f, desc="Processing lines"):
                if pattern.search(line):
                    continue
                total_rows += 1
                fields = line.strip().split('\t')
                if len(fields) <= max(filer_ein_idx, grant_ein_idx, grant_amt_idx):
                    continue  # Skip malformed lines
                filer_ein = fields[filer_ein_idx]
                grant_ein = fields[grant_ein_idx]
                try:
                    grant_amt = float(fields[grant_amt_idx])
                except ValueError:
                    continue  # Skip lines with invalid grant_amt
                # Aggregate
                agg_data[(filer_ein, grant_ein)] += grant_amt
                # Count and sum for grant_ein
                grant_ein_counts[grant_ein] += 1
                grant_ein_amts[grant_ein] += grant_amt
                # Count and sum for 3-digit country codes
                if re.match(r'^\d{3}$', grant_ein):
                    country_code_counts[grant_ein] += 1
                    country_code_amts[grant_ein] += grant_amt
                filtered_rows += 1

        # Prepare output TSV
        print("Writing output TSV...")
        output_lines = ['filer_ein\tgrant_ein\tgrant_amt']
        sorted_keys = sorted(agg_data.keys(), key=lambda x: (x[0], x[1]))
        for filer_ein, grant_ein in sorted_keys:
            output_lines.append(f'{filer_ein}\t{grant_ein}\t{agg_data[(filer_ein, grant_ein)]}')
        
        with open(output_file, 'w') as f:
            f.write('\n'.join(output_lines) + '\n')

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

        # 3-digit country codes by count
        markdown += "## 3-Digit grant_ein Country Codes\n"
        markdown += "| Country Code | Country Name | Count | Total Grant Amount |\n"
        markdown += "|--------------|--------------|-------|--------------------|\n"
        if country_code_counts:
            sorted_codes = sorted(country_code_counts.items(), key=lambda x: (-x[1], x[0]))
            for code, count in sorted_codes[:10]:
                name = code_to_name.get(code, "Unknown")
                total_amt = country_code_amts[code]
                markdown += f"| {code} | {name} | {count} | {total_amt:.2f} |\n"
        else:
            markdown += "| None | None | 0 | 0.00 |\n"
        markdown += "\n"

        # Top 10 3-digit country codes by total grant_amt
        markdown += "## Top 10 3-Digit grant_ein Country Codes by Total Grant Amount\n"
        markdown += "| Country Code | Country Name | Count | Total Grant Amount |\n"
        markdown += "|--------------|--------------|-------|--------------------|\n"
        if country_code_amts:
            sorted_amt_codes = sorted(country_code_amts.items(), key=lambda x: (-x[1], x[0]))[:10]
            for code, total_amt in sorted_amt_codes:
                name = code_to_name.get(code, "Unknown")
                count = country_code_counts[code]
                markdown += f"| {code} | {name} | {count} | {total_amt:.2f} |\n"
        else:
            markdown += "| None | None | 0 | 0.00 |\n"

        with open(report_md, 'w') as f:
            f.write(markdown)

        print(f"Cleaned TSV written to {output_file}")
        print(f"Original rows: {total_rows}, Filtered rows: {filtered_rows}")
        print(f"Report written to {report_md}")

    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description='Clean TSV file by aggregating grant_amt and filtering rows')
    parser.add_argument('--input-file', help='Input TSV file', required=True)
    parser.add_argument('--output-file', help='Output TSV file', required=True)
    parser.add_argument('--report-md', help='Output Markdown file for report', default='report.md')

    args = parser.parse_args()

    # Validate input file exists
    if not Path(args.input_file).is_file():
        print(f"Error: Input file '{args.input_file}' not found")
        sys.exit(1)

    clean_tsv(args.input_file, args.output_file, args.report_md)

if __name__ == '__main__':
    main()