#!/bin/bash

# Usage: ./combine_grants.sh [input_file] [output_file]
# Defaults: input_file=inferred_grants.tsv, output_file=combined_grants.tsv

INPUT_FILE="${1:-inferred_grants.tsv}"
OUTPUT_FILE="${2:-combined_grants.tsv}"

if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: Input file '$INPUT_FILE' not found."
    exit 1
fi

sort -k1,1 -k3,3 -k5,5 "$INPUT_FILE" | awk '
    BEGIN { FS="\t"; OFS="\t" }
    NR>1 {
        if ($1 == prev_filer && $3 == prev_grant && $5 == prev_year) {
            sum += $4
        } else {
            if (NR > 2) print prev_filer, prev_name, prev_grant, sum, prev_year
            prev_filer = $1
            prev_name = $2
            prev_grant = $3
            sum = $4
            prev_year = $5
        }
    }
    END { print prev_filer, prev_name, prev_grant, sum, prev_year }
' > "$OUTPUT_FILE"

echo "Combined grants written to '$OUTPUT_FILE'."