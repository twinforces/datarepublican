#!/usr/bin/env python3
import csv
import argparse
import os
import sys
from collections import defaultdict

def is_valid_ein(ein):
    """Check if EIN is valid (not 3 digits and not empty)."""
    return isinstance(ein, str) and len(ein) != 3 and ein.strip()

def get_header(file_path):
    """Read the header line from a TSV file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader, None)
        if not header:
            print(f"Error: No header found in {file_path}", file=sys.stderr)
            return None
        return header

def build_ein_index(file_path, ein_col, skip_header=True):
    """Build a mapping of EIN to row numbers from a TSV file."""
    ein_to_rows = defaultdict(list)
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if skip_header and not reader.fieldnames:
            print(f"Error: No header found in {file_path}", file=sys.stderr)
            return None
        if ein_col not in reader.fieldnames:
            print(f"Error: Column '{ein_col}' not found in {file_path}. Found: {reader.fieldnames}", file=sys.stderr)
            return None
        for row_num, row in enumerate(reader, start=1):
            ein = row.get(ein_col, '')
            if is_valid_ein(ein):
                ein_to_rows[ein].append(row_num)
    return ein_to_rows

def get_grant_eins(file_path, ein_col):
    """Get set of valid grant_ein from grants.tsv."""
    grant_eins = set()
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if ein_col not in reader.fieldnames:
            print(f"Error: Column '{ein_col}' not found in {file_path}. Found: {reader.fieldnames}", file=sys.stderr)
            return None
        for row in reader:
            ein = row.get(ein_col, '')
            if is_valid_ein(ein):
                grant_eins.add(ein)
    return grant_eins

def generate_sed_script(master_path, filtered_path, output_path, row_numbers, script_path):
    """Generate a .sh file to run sed and awk for row extraction in chunks of 100."""
    # Read headers to dynamically generate awk command
    filtered_header = get_header(filtered_path)
    master_header = get_header(master_path)
    if not filtered_header or not master_header:
        return

    # Map filtered columns to master indices (1-based for awk)
    column_indices = []
    for col in filtered_header:
        try:
            idx = master_header.index(col) + 1  # 1-based for awk
            column_indices.append(idx)
        except ValueError:
            print(f"Error: Column '{col}' from {filtered_path} not found in {master_path}", file=sys.stderr)
            return

    # Generate awk command
    awk_fields = '"\t"'.join(f'${idx}' for idx in column_indices)
    awk_cmd = f"awk -F'\\t' '{{print {awk_fields}}}'"

    if not row_numbers:
        print("No rows to extract. Generating script to copy filtered.tsv to output.")
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write("#!/bin/bash\n")
            f.write(f"# Generated sed script to copy {filtered_path} to {output_path}\n")
            f.write(f"cp \"{filtered_path}\" \"{output_path}\"\n")
        return

    # Sort and deduplicate row numbers
    row_numbers = sorted(set(row_numbers))
    print(f"Generating sed script for {len(row_numbers)} rows...")

    # Split into chunks of 100
    chunks = [row_numbers[i:i + 100] for i in range(0, len(row_numbers), 100)]

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write("#!/bin/bash\n")
        f.write(f"# Generated sed script to extract rows from {master_path}\n")
        f.write(f"# Starts with {filtered_path}, appends {len(row_numbers)} rows in chunks of 100\n")
        f.write(f"# Selects columns matching {filtered_path} using awk\n")
        f.write("\n")
        f.write("# Check if sed is installed\n")
        f.write("if ! command -v sed &> /dev/null; then\n")
        f.write("    echo \"Error: sed is required but not installed.\"\n")
        f.write("    exit 1\n")
        f.write("fi\n")
        f.write("# Check if awk is installed\n")
        f.write("if ! command -v awk &> /dev/null; then\n")
        f.write("    echo \"Error: awk is required but not installed.\"\n")
        f.write("    exit 1\n")
        f.write("fi\n")
        f.write("\n")
        f.write("# Initialize output file by copying filtered.tsv\n")
        f.write(f"cp \"{filtered_path}\" \"{output_path}\"\n")
        f.write("\n")
        f.write("# Append chunks of rows from master.tsv with selected columns\n")
        
        for i, chunk in enumerate(chunks, 1):
            # Create sed expression for this chunk
            sed_expr = ";".join(f"{row}p" for row in chunk)
            f.write(f"# Chunk {i}: {len(chunk)} rows\n")
            f.write(f"sed -n '{sed_expr}' \"{master_path}\" | {awk_cmd} >> \"{output_path}\"\n")
            f.write(f"echo \"Appended {len(chunk)} rows from chunk {i} to {output_path}\"\n")
        
        f.write("\n")
        f.write(f"echo \"Copied {filtered_path} and appended {len(row_numbers)} rows to {output_path}\"\n")
    
    # Make the script executable
    os.chmod(script_path, 0o755)
    print(f"Generated executable sed script: {script_path}")

def merge_tsv_files(master_path, filtered_path, grants_path, output_path):
    """Merge rows from master.tsv to output.tsv by generating a sed script."""
    # Build filer_ein to row number indices
    print("Building index for filtered.tsv...")
    filtered_index = build_ein_index(filtered_path, 'filer_ein')
    if filtered_index is None:
        return

    print("Building index for master.tsv...")
    master_index = build_ein_index(master_path, 'filer_ein')
    if master_index is None:
        return

    print("Reading grant_ein from grants.tsv...")
    grant_eins = get_grant_eins(grants_path, 'grant_ein')
    if grant_eins is None:
        return

    # Find master EINs not in filtered
    filtered_eins = set(filtered_index.keys())
    master_eins = set(master_index.keys())
    missing_eins = master_eins - filtered_eins

    # Intersect with grant_ein
    eins_to_copy = missing_eins & grant_eins
    if not eins_to_copy:
        print("No matching EINs found.")
        script_path = "extract_rows.sh"
        generate_sed_script(master_path, filtered_path, output_path, [], script_path)
        return

    # Collect row numbers to copy
    row_numbers = []
    for ein in eins_to_copy:
        row_numbers.extend(master_index[ein])
    
    print(f"Found {len(eins_to_copy)} EINs with {len(row_numbers)} rows to copy.")
    
    # Generate sed script
    script_path = "extract_rows.sh"
    generate_sed_script(master_path, filtered_path, output_path, row_numbers, script_path)

def main():
    parser = argparse.ArgumentParser(description="Generate sed script to merge TSV files using filer_ein and grant_ein.")
    parser.add_argument('--master', required=True, help="Path to master.tsv")
    parser.add_argument('--filtered', required=True, help="Path to filtered.tsv")
    parser.add_argument('--grants', required=True, help="Path to grants.tsv")
    parser.add_argument('--output', required=True, help="Path to output.tsv")
    args = parser.parse_args()

    # Verify all input files exist
    for file_path in [args.master, args.filtered, args.grants]:
        if not os.path.exists(file_path):
            print(f"Error: {file_path} not found.", file=sys.stderr)
            return

    print("Starting TSV merge process...")
    merge_tsv_files(args.master, args.filtered, args.grants, args.output)
    print(f"Processing complete. Run the generated script './extract_rows.sh' to extract rows.")

if __name__ == "__main__":
    main()