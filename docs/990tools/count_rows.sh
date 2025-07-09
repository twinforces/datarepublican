#!/bin/bash

# Check if correct number of arguments are provided
if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <tsv_file> <column_name> <threshold>"
    exit 1
fi

# Assign parameters to variables
file="$1"
column_name="$2"
threshold="$3"

# Check if file exists
if [ ! -f "$file" ]; then
    echo "Error: File '$file' not found."
    exit 1
fi

# Find column number for column_name
header=$(head -n 1 "$file")
col_number=$(echo "$header" | tr '\t' '\n' | grep -n "^$column_name$" | cut -d: -f1)

# Check if column_name was found
if [ -z "$col_number" ]; then
    echo "Error: Column '$column_name' not found in file."
    exit 1
fi

# Count rows where the column value exceeds threshold, skipping header
awk -F'\t' -v col="$col_number" -v thresh="$threshold" 'NR>1 && $col > thresh {count++} END {print count}' "$file"