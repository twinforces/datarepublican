#!/bin/bash

# Usage: ./combine_grants.sh [input_file] [output_file]
# Defaults: input_file=inferred_grants.tsv, output_file=combined_grants.tsv

INPUT_FILE="${1:-inferred_grants.tsv}"
OUTPUT_FILE="${2:-combined_grants.tsv}"

if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: Input file '$INPUT_FILE' not found."
    exit 1
fi

# Read header and determine column indices
header=$(head -n 1 "$INPUT_FILE")
IFS=$'\t' read -ra cols <<< "$header"

# Find column indices (1-based for awk)
filer_ein_idx=0
filer_name_idx=0
grant_ein_idx=0
grant_amt_idx=0
tax_year_idx=0

for i in "${!cols[@]}"; do
    case "${cols[i]}" in
        "filer_ein") filer_ein_idx=$((i+1));;
        "filer_name") filer_name_idx=$((i+1));;
        "grant_ein") grant_ein_idx=$((i+1));;
        "grant_amt") grant_amt_idx=$((i+1));;
        "tax_year") tax_year_idx=$((i+1));;
    esac
done

# Check if all required columns were found
if [ $filer_ein_idx -eq 0 ] || [ $filer_name_idx -eq 0 ] || [ $grant_ein_idx -eq 0 ] || \
   [ $grant_amt_idx -eq 0 ] || [ $tax_year_idx -eq 0 ]; then
    echo "Error: Missing required columns in header"
    exit 1
fi

# Write header to output file
echo "$header" > "$OUTPUT_FILE"

# Sort and combine grants
sort -k${filer_ein_idx},${filer_ein_idx} -k${grant_ein_idx},${grant_ein_idx} -k${tax_year_idx},${tax_year_idx} "$INPUT_FILE" | awk -v filer_ein_idx="$filer_ein_idx" \
    -v filer_name_idx="$filer_name_idx" -v grant_ein_idx="$grant_ein_idx" \
    -v grant_amt_idx="$grant_amt_idx" -v tax_year_idx="$tax_year_idx" '
    BEGIN { FS="\t"; OFS="\t" }
    NR==1 { next }  # Skip header
    {
        if ($filer_ein_idx == prev_filer && $grant_ein_idx == prev_grant && $tax_year_idx == prev_year) {
            sum += $grant_amt_idx
        } else {
            if (NR > 2) print prev_filer, prev_name, prev_grant, sum, prev_year
            prev_filer = $filer_ein_idx
            prev_name = $filer_name_idx
            prev_grant = $grant_ein_idx
            sum = $grant_amt_idx
            prev_year = $tax_year_idx
        }
    }
    END { if (NR > 1) print prev_filer, prev_name, prev_grant, sum, prev_year }
' >> "$OUTPUT_FILE"

echo "Combined grants written to '$OUTPUT_FILE'."