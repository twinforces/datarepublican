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

for i in "${!cols[@]}"; do
    case "${cols[i]}" in
        "filer_ein") filer_ein_idx=$((i+1));;
        "filer_name") filer_name_idx=$((i+1));;
        "grant_ein") grant_ein_idx=$((i+1));;
        "grant_amt") grant_amt_idx=$((i+1));;
    esac
done

# Check if all required columns were found
if [ $filer_ein_idx -eq 0 ] || [ $filer_name_idx -eq 0 ] || [ $grant_ein_idx -eq 0 ] || \
   [ $grant_amt_idx -eq 0 ]; then
    echo "Error: Missing required columns in header"
    exit 1
fi

# Write reduced header to output file
echo -e "filer_ein\tfiler_name\tgrant_ein\tgrant_amt" > "$OUTPUT_FILE"

# Filter out rows where grant_ein starts with "Address:" or is "Unknown", sort, and combine grants
tail -n +2 "$INPUT_FILE" | awk -v grant_ein_idx="$grant_ein_idx" -F'\t' '$grant_ein_idx !~ /^Address:/ && $grant_ein_idx != "Unknown"' | \
sort -k${filer_ein_idx},${filer_ein_idx} -k${grant_ein_idx},${grant_ein_idx} | awk -v filer_ein_idx="$filer_ein_idx" \
    -v filer_name_idx="$filer_name_idx" -v grant_ein_idx="$grant_ein_idx" \
    -v grant_amt_idx="$grant_amt_idx" '
    BEGIN { FS="\t"; OFS="\t" }
    {
        # Validate grant_amt is non-negative numeric
        if ($grant_amt_idx !~ /^[0-9]+(\.[0-9]+)?$/) {
            if ($grant_amt_idx ~ /^-/) {
                print "Warning: Skipping row with negative grant_amt: " $0 > "/dev/stderr"
            } else {
                print "Warning: Skipping row with non-numeric grant_amt: " $0 > "/dev/stderr"
            }
            next
        }
        if ($filer_ein_idx == prev_filer && $grant_ein_idx == prev_grant) {
            sum += $grant_amt_idx
        } else {
            if (NR > 1 && prev_filer != "") {
                print prev_filer, prev_name, prev_grant, sum
            }
            prev_filer = $filer_ein_idx
            prev_name = $filer_name_idx
            prev_grant = $grant_ein_idx
            sum = $grant_amt_idx
        }
    }
    END {
        if (NR > 0 && prev_filer != "") {
            print prev_filer, prev_name, prev_grant, sum
        }
    }
' >> "$OUTPUT_FILE"

echo "Combined grants written to '$OUTPUT_FILE'."