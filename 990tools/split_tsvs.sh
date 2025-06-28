#!/bin/bash

# split_tsvs.sh: Splits TSV files into 10,000-row chunks with fixed-order columns by name and zips them

set -e

# Input TSV files and required columns in fixed order
TSV_FILES=("charities.tsv" "grants_final.tsv" "grants.pf.tsv")
CHARITY_COLUMNS="filer_ein,filer_name,xml_name,receipt_amt,govt_amt,contrib_amt,tax_year,org_type,total_assets,form_type,denominator"
GRANT_COLUMNS="filer_ein,grant_ein,grant_amt"
CHUNK_SIZE=10000
OUTPUT_DIR="tsv_chunks"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Function to get column indices from header
get_column_indices() {
  local tsv_file="$1"
  local required_columns="$2"
  local header
  header=$(head -n 1 "$tsv_file")
  IFS=$'\t' read -r -a header_array <<< "$header"
  local indices=""
  local IFS=","
  for col in $required_columns; do
    found=false
    for i in "${!header_array[@]}"; do
      if [ "${header_array[i]}" = "$col" ]; then
        indices="$indices,$((i+1))"
        found=true
        break
      fi
    done
    if [ "$found" = false ]; then
      echo "Error: Column $col not found in $tsv_file header"
      exit 1
    fi
  done
  # Remove leading comma and return
  echo "${indices#,}"
}

for TSV_FILE in "${TSV_FILES[@]}"; do
  if [ ! -f "$TSV_FILE" ]; then
    echo "Error: $TSV_FILE not found"
    exit 1
  fi

  # Select columns and header based on file type
  if [[ "$TSV_FILE" == "charities.tsv" ]]; then
    COLUMNS="$CHARITY_COLUMNS"
    HEADER="filer_ein\tfiler_name\txml_name\treceipt_amt\tgovt_amt\tcontrib_amt\ttax_year\torg_type\ttotal_assets\tform_type\tdenominator"
  else
    COLUMNS="$GRANT_COLUMNS"
    HEADER="filer_ein\tgrant_ein\tgrant_amt"
  fi

  # Get column indices
  COLUMN_INDICES=$(get_column_indices "$TSV_FILE" "$COLUMNS")
  if [ -z "$COLUMN_INDICES" ]; then
    echo "Error: Could not find required columns in $TSV_FILE"
    exit 1
  fi

  # Get number of lines (excluding header)
  TOTAL_LINES=$(wc -l < "$TSV_FILE")
  DATA_LINES=$((TOTAL_LINES - 1))

  # Calculate number of chunks
  CHUNKS=$(((DATA_LINES + CHUNK_SIZE - 1) / CHUNK_SIZE))

  echo "Splitting $TSV_FILE ($DATA_LINES data lines) into $CHUNKS chunks of $CHUNK_SIZE rows with columns $COLUMNS"

  # Filter data rows with selected columns (excluding header)
  tail -n +2 "$TSV_FILE" | awk -F'\t' -v cols="$COLUMN_INDICES" 'BEGIN {split(cols, arr, ",")} {for (i in arr) printf "%s%s", $arr[i], (i == length(arr) ? "\n" : "\t")}' > "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"

  # Split filtered file
  split -l "$CHUNK_SIZE" "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv" "${OUTPUT_DIR}/${TSV_FILE%.tsv}_chunk_"

  # Process each chunk
  i=0
  for CHUNK_FILE in "${OUTPUT_DIR}/${TSV_FILE%.tsv}_chunk_"*; do
    # Add fixed header to chunk
    CHUNK_NAME="${TSV_FILE%.tsv}_chunk_${i}.tsv"
    echo -e "$HEADER" > "${OUTPUT_DIR}/$CHUNK_NAME"
    cat "$CHUNK_FILE" >> "${OUTPUT_DIR}/$CHUNK_NAME"
    rm "$CHUNK_FILE"

    # Zip the chunk
    (cd "$OUTPUT_DIR" && zip "${CHUNK_NAME}.zip" "$CHUNK_NAME")
    rm "${OUTPUT_DIR}/$CHUNK_NAME"

    echo "Created ${OUTPUT_DIR}/${CHUNK_NAME}.zip"
    ((i++))
  done

  # Clean up filtered file
  rm "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"
done

echo "Splitting and zipping complete. Chunks are in $OUTPUT_DIR"