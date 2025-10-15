#!/bin/bash

# split_tsvs.sh: Splits TSV files into chunks with selected columns by name

set -e

# Input TSV files and required columns
TSV_FILES=("charities.tsv" "grants_final.tsv" "grants.pf.tsv" "contractors.tsv" "political_contributions.tsv")
CHARITY_COLUMNS="filer_ein,filer_name,xml_name,receipt_amt,govt_amt,contrib_amt,tax_year,org_type,total_assets,form_type,denominator,canonical_address"
GRANT_COLUMNS="filer_ein,grant_ein,grant_amt,filer_colocator,grantee_colocator"
CONTRACTOR_COLUMNS="filer_ein,name,amount,ein,address,zip_code,po_box,tax_year"
POLITICAL_COLUMNS="filer_ein,recipient,amount,recipient_address,recipient_zip,recipient_po_box,tax_year"
CHARITY_CHUNK_SIZE=10000
GRANT_CHUNK_SIZE=50000
CONTRACTOR_CHUNK_SIZE=25000
POLITICAL_CHUNK_SIZE=25000
OUTPUT_DIR="tsv_chunks"
DATA_FILES_FILE="data_files.js"

# Clear output directory
rm -rf "$OUTPUT_DIR" && mkdir -p "$OUTPUT_DIR"

# Function to get column indices from header
get_column_indices() {
  local tsv_file="$1"
  local required_columns="$2"
  local header
  header=$(head -n 1 "$tsv_file")
  echo "Header for $tsv_file: $header" >&2
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
      echo "Error: Column $col not found in $tsv_file header" >&2
      exit 1
    fi
  done
  # Remove leading comma and return
  indices="${indices#,}"
  echo "Selected column indices for $tsv_file: $indices" >&2
  echo "$indices"
}

# Set dbVersion as current timestamp in ISO 8601 format
DB_VERSION=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "Calculated dbVersion: $DB_VERSION (current timestamp)" >&2

# Initialize DATA_FILES array
DATA_FILES=()

for TSV_FILE in "${TSV_FILES[@]}"; do
  if [ ! -f "$TSV_FILE" ]; then
    echo "Warning: $TSV_FILE not found, skipping" >&2
    continue
  fi

  # Select columns, header, and chunk size based on file type
  if [[ "$TSV_FILE" == "charities.tsv" ]]; then
    COLUMNS="$CHARITY_COLUMNS"
    HEADER="filer_ein\tfiler_name\txml_name\treceipt_amt\tgovt_amt\tcontrib_amt\ttax_year\torg_type\ttotal_assets\tform_type\tdenominator\tcanonical_address"
    CHUNK_SIZE="$CHARITY_CHUNK_SIZE"
    TYPE="charities"
    GRANT_TYPE=""
  elif [[ "$TSV_FILE" == "contractors.tsv" ]]; then
    COLUMNS="$CONTRACTOR_COLUMNS"
    HEADER="filer_ein\tname\tamount\tein\taddress\tzip_code\tpo_box\ttax_year"
    CHUNK_SIZE="$CONTRACTOR_CHUNK_SIZE"
    TYPE="contractors"
    GRANT_TYPE=""
  elif [[ "$TSV_FILE" == "political_contributions.tsv" ]]; then
    COLUMNS="$POLITICAL_COLUMNS"
    HEADER="filer_ein\trecipient\tamount\trecipient_address\trecipient_zip\trecipient_po_box\ttax_year"
    CHUNK_SIZE="$POLITICAL_CHUNK_SIZE"
    TYPE="political"
    GRANT_TYPE=""
  else
    COLUMNS="$GRANT_COLUMNS"
    HEADER="filer_ein\tgrant_ein\tgrant_amt\tfiler_colocator\tgrantee_colocator"
    CHUNK_SIZE="$GRANT_CHUNK_SIZE"
    TYPE="grants"
    GRANT_TYPE=$( [[ "$TSV_FILE" == "grants_final.tsv" ]] && echo "regular" || echo "private" )
  fi

  # Get column indices
  COLUMN_INDICES=$(get_column_indices "$TSV_FILE" "$COLUMNS")
  if [ -z "$COLUMN_INDICES" ]; then
    echo "Error: Could not find required columns in $TSV_FILE" >&2
    exit 1
  fi

  # Get number of lines (excluding header)
  TOTAL_LINES=$(wc -l < "$TSV_FILE")
  DATA_LINES=$((TOTAL_LINES - 1))

  # Calculate expected number of chunks
  if [ "$DATA_LINES" -le 0 ]; then
    CHUNKS=0
  else
    CHUNKS=$(((DATA_LINES + CHUNK_SIZE - 1) / CHUNK_SIZE))
  fi
  echo "Expected chunks for $TSV_FILE: $CHUNKS (DATA_LINES=$DATA_LINES, CHUNK_SIZE=$CHUNK_SIZE)" >&2

  # Filter data rows
  if [[ "$TSV_FILE" == "charities.tsv" ]]; then
    tail -n +2 "$TSV_FILE" | awk -F'\t' -v cols="$COLUMN_INDICES" '
      BEGIN {
        split(cols, arr, ",");
        for (i in arr) {
          if (arr[i] == 0) {
            print "Error: Invalid column index 0 for column " i > "/dev/stderr";
            exit 1;
          }
        }
      }
      {
        for (i=1; i<=length(arr); i++) {
          idx = arr[i];
          if (idx > NF) {
            print "Error: Row " NR " has " NF " columns, expected at least " idx " for column " i > "/dev/stderr";
            printf "";
          } else {
            printf "%s", ($idx == "" ? "" : $idx);
          }
          printf "%s", (i == length(arr) ? "\n" : "\t");
        }
      }' > "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"
  elif [[ "$TSV_FILE" == "contractors.tsv" ]]; then
    # Filter out contractors with zero amounts or empty names
    tail -n +2 "$TSV_FILE" | awk -F'\t' -v cols="$COLUMN_INDICES" '
      BEGIN {
        split(cols, arr, ",");
        for (i in arr) {
          if (arr[i] == 0) {
            print "Error: Invalid column index 0 for column " i > "/dev/stderr";
            exit 1;
          }
        }
      }
      {
        name = $arr[2];
        amount = $arr[3];
        if (name != "" && amount != "0" && amount != "") {
          for (i=1; i<=length(arr); i++) {
            idx = arr[i];
            if (idx > NF) {
              print "Error: Row " NR " has " NF " columns, expected at least " idx " for column " i > "/dev/stderr";
              printf "";
            } else {
              printf "%s", ($idx == "" ? "" : $idx);
            }
            printf "%s", (i == length(arr) ? "\n" : "\t");
          }
        }
      }' > "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"
  elif [[ "$TSV_FILE" == "political_contributions.tsv" ]]; then
    # Filter out political contributions with zero amounts or empty recipients
    tail -n +2 "$TSV_FILE" | awk -F'\t' -v cols="$COLUMN_INDICES" '
      BEGIN {
        split(cols, arr, ",");
        for (i in arr) {
          if (arr[i] == 0) {
            print "Error: Invalid column index 0 for column " i > "/dev/stderr";
            exit 1;
          }
        }
      }
      {
        recipient = $arr[2];
        amount = $arr[3];
        if (recipient != "" && amount != "0" && amount != "") {
          for (i=1; i<=length(arr); i++) {
            idx = arr[i];
            if (idx > NF) {
              print "Error: Row " NR " has " NF " columns, expected at least " idx " for column " i > "/dev/stderr";
              printf "";
            } else {
              printf "%s", ($idx == "" ? "" : $idx);
            }
            printf "%s", (i == length(arr) ? "\n" : "\t");
          }
        }
      }' > "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"
  else
    # Filter out grants where filer_ein equals grant_ein or grant_amt is zero
    tail -n +2 "$TSV_FILE" | awk -F'\t' -v cols="$COLUMN_INDICES" '
      BEGIN {
        split(cols, arr, ",");
        for (i in arr) {
          if (arr[i] == 0) {
            print "Error: Invalid column index 0 for column " i > "/dev/stderr";
            exit 1;
          }
        }
      }
      {
        filer_ein = $arr[1];
        grant_ein = $arr[2];
        grant_amt = $arr[3];
        if (filer_ein != grant_ein && grant_amt != "0" && grant_amt != "") {
          for (i=1; i<=length(arr); i++) {
            idx = arr[i];
            if (idx > NF) {
              print "Error: Row " NR " has " NF " columns, expected at least " idx " for column " i > "/dev/stderr";
              printf "";
            } else {
              printf "%s", ($idx == "" ? "" : $idx);
            }
            printf "%s", (i == length(arr) ? "\n" : "\t");
          }
        }
      }' > "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"
  fi

  # Verify filtered file exists and is non-empty
  if [ ! -s "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv" ]; then
    if [ "$CHUNKS" -eq 0 ]; then
      echo "Warning: No data lines after filtering for $TSV_FILE, skipping chunk creation" >&2
      rm -f "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"
      continue
    else
      echo "Error: Filtered file ${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv is empty or not created" >&2
      exit 1
    fi
  fi

  # Verify filtered file column count
  filtered_cols=$(head -n 1 "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv" | awk -F'\t' '{print NF}' || echo 0)
  expected_cols=$(echo "$COLUMNS" | awk -F',' '{print NF}')
  if [ "$filtered_cols" != "$expected_cols" ]; then
    echo "Error: Filtered file for $TSV_FILE has $filtered_cols columns, expected $expected_cols" >&2
    exit 1
  fi

  # Split filtered file (BSD-compatible)
  split -l "$CHUNK_SIZE" "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv" "${OUTPUT_DIR}/${TSV_FILE%.tsv}_chunk_"

  # Process each chunk
  i=0
  find "$OUTPUT_DIR" -type f -name "${TSV_FILE%.tsv}_chunk_*" | sort | while read -r CHUNK_FILE; do
    CHUNK_NAME="${TSV_FILE%.tsv}_chunk_${i}.tsv"
    mv "$CHUNK_FILE" "${OUTPUT_DIR}/$CHUNK_NAME"
    echo -e "$HEADER" > "${OUTPUT_DIR}/${CHUNK_NAME}.tmp"
    cat "${OUTPUT_DIR}/$CHUNK_NAME" >> "${OUTPUT_DIR}/${CHUNK_NAME}.tmp"
    mv "${OUTPUT_DIR}/${CHUNK_NAME}.tmp" "${OUTPUT_DIR}/$CHUNK_NAME"

    # Zip the chunk
    (cd "$OUTPUT_DIR" && zip "${CHUNK_NAME}.zip" "$CHUNK_NAME")
    rm "${OUTPUT_DIR}/$CHUNK_NAME"

    echo "Created ${OUTPUT_DIR}/${CHUNK_NAME}.zip" >&2
    ((i++))
  done

  # Verify chunk count
  actual_chunks=$(find "$OUTPUT_DIR" -type f -name "${TSV_FILE%.tsv}_chunk_*.tsv.zip" | wc -l)
  actual_chunks=$((actual_chunks))
  if [ "$actual_chunks" -ne "$CHUNKS" ]; then
    echo "Error: Created $actual_chunks chunks, expected $CHUNKS for $TSV_FILE" >&2
    exit 1
  fi

  # Add to DATA_FILES
  if [[ "$TSV_FILE" == "charities.tsv" ]]; then
    STATUS_TEXT="Charities"
  elif [[ "$TSV_FILE" == "contractors.tsv" ]]; then
    STATUS_TEXT="Contractors"
  elif [[ "$TSV_FILE" == "political_contributions.tsv" ]]; then
    STATUS_TEXT="Political Contributions"
  elif [[ "$TSV_FILE" == "grants_final.tsv" ]]; then
    STATUS_TEXT="501 Grants"
  else
    STATUS_TEXT="Private Foundation Grants"
  fi

  DATA_FILES+=("{
    status: \"Loading $STATUS_TEXT\",
    baseFile: \"./tsv_chunks/${TSV_FILE%.tsv}_chunk_\",
    tsvFilePrefix: \"${TSV_FILE%.tsv}_chunk_\",
    type: \"$TYPE\",
    chunkCount: $actual_chunks$( [[ -n "$GRANT_TYPE" ]] && echo ", grantType: \"$GRANT_TYPE\"" || echo "" )
  }")

  # Clean up filtered file
  rm -f "${OUTPUT_DIR}/${TSV_FILE%.tsv}_filtered.tsv"
done

# Write DATA_FILES to file
printf "export const DATA_FILES = {\n  dbVersion: \"%s\",\n  files: [\n" "$DB_VERSION" > "$DATA_FILES_FILE"
for ((i=0; i<${#DATA_FILES[@]}; i++)); do
  printf "    %s" "${DATA_FILES[i]}" >> "$DATA_FILES_FILE"
  if [ $i -lt $((${#DATA_FILES[@]}-1)) ]; then
    printf ",\n" >> "$DATA_FILES_FILE"
  else
    printf "\n" >> "$DATA_FILES_FILE"
  fi
done
printf "  ]\n};\n" >> "$DATA_FILES_FILE"

echo "Splitting and zipping complete. Chunks are in $OUTPUT_DIR"
echo "Generated $DATA_FILES_FILE with dbVersion: $DB_VERSION"