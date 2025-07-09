#!/bin/bash
# check_charities.sh: Analyze TSV file for column counts and xml_name position

if [ $# -ne 1 ]; then
  echo "Usage: $0 <input_file>"
  exit 1
fi

input_file="$1"
xml_regex=".*\.xml"

if [ ! -f "$input_file" ]; then
  echo "Error: $input_file not found"
  exit 1
fi

# Count total lines (excluding header)
total_lines=$(wc -l < "$input_file")
data_lines=$((total_lines - 1))
echo "Total data lines: $data_lines"

# Get header
header=$(head -n 1 "$input_file")
echo "Header: $header"

# Count columns in header
header_cols=$(echo "$header" | awk -F'\t' '{print NF}')
echo "Header columns: $header_cols"

# Find xml_name column index
xml_index=$(echo "$header" | awk -F'\t' '{for (i=1; i<=NF; i++) if ($i == "xml_name") print i}')
if [ -z "$xml_index" ]; then
  echo "Warning: xml_name column not found in header, searching for .*\\.xml"
  xml_index=$(head -n 2 "$input_file" | tail -n 1 | awk -F'\t' -v regex="$xml_regex" '{for (i=1; i<=NF; i++) if ($i ~ regex) print i}')
  if [ -z "$xml_index" ]; then
    echo "Error: No column matches .*\\.xml"
    xml_index=0
  else
    echo "Found .*\\.xml in column $xml_index"
  fi
else
  echo "xml_name found in column $xml_index"
fi

# Count rows by column count and check xml_name
awk -F'\t' -v xml_regex="$xml_regex" -v xml_index="$xml_index" '
BEGIN {
  for (i=1; i<=100; i++) col_counts[i]=0;
  xml_miss=0;
  xml_positions[xml_index]=0;
}
NR>1 {
  col_counts[NF]++;
  if (xml_index > 0 && xml_index <= NF && $xml_index !~ xml_regex) {
    if (NR <= 10 || NR % 100000 == 0) {
      print "Invalid xml_name in row " NR ": " $xml_index;
    }
    xml_miss++;
  }
  if (xml_index == 0) {
    for (i=1; i<=NF; i++) {
      if ($i ~ xml_regex) {
        xml_positions[i]++;
        break;
      }
    }
  }
}
END {
  print "Column count distribution:"
  for (i in col_counts) {
    if (col_counts[i] > 0) {
      print "  " i " columns: " col_counts[i] " rows"
    }
  }
  print "Rows with invalid xml_name (when xml_index found): " xml_miss;
  if (xml_index == 0) {
    print "xml_name position distribution:"
    for (i in xml_positions) {
      if (xml_positions[i] > 0) {
        print "  Column " i ": " xml_positions[i] " rows"
      }
    }
  }
}' "$input_file"

# Check for 996081402
grep 996081402 "$input_file" || echo "EIN 996081402 not found in $input_file"