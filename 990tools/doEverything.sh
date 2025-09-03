#!/bin/bash

# IRS 990 Tools - Unified Pipeline Script
# This script uses the new irs990tools module instead of calling individual scripts

# Configuration - Edit these paths as needed
export ZIPS_DIR="/Volumes/Data/irs_zips"
export OUT_DIR="/Volumes/Data/tsvs"
export ANAL_DIR="/Volumes/Data/atsvs"
export FINAL_DIR="/Volumes/Data/final"
export DR_ROOT="$HOME/Development/datarepublican"
export TOOLS=$DR_ROOT/990tools
export BROWSE=$DR_ROOT/browse

# Processing parameters
START_YEAR=2017
END_YEAR=2025
MINIMUM_D=0
WORKER_THREADS=16

echo "=== IRS 990 Tools Pipeline ==="
echo "Processing years: $START_YEAR to $END_YEAR"
echo "Zips directory: $ZIPS_DIR"
echo "Output directory: $FINAL_DIR"
echo "Worker threads: $WORKER_THREADS"
echo ""

# Change to tools directory
cd "$TOOLS" || exit 1

# Clean up old TSV files to ensure they get recreated with correct headers
echo "Cleaning up old TSV files..."
rm -f "$OUT_DIR"/*.tsv
rm -f "$ANAL_DIR"/*.tsv
rm -f "$FINAL_DIR"/*.tsv
rm -f "$BROWSE"/*.tsv

# Run the complete pipeline using the new unified tool
echo "Running complete IRS 990 processing pipeline..."
python irs990tools.py run-all \
  --start-year $START_YEAR \
  --end-year $END_YEAR \
  --zips-dir "$ZIPS_DIR" \
  --tsvs-dir "$OUT_DIR" \
  --analyzed-dir "$ANAL_DIR" \
  --final-dir "$FINAL_DIR" \
  --browse-dir "$BROWSE" \
  --cache-dir "$ANAL_DIR/_cache" \
  --minimum-d $MINIMUM_D \
  --worker-threads $WORKER_THREADS \
  --verbose

# Check if pipeline completed successfully
if [ $? -ne 0 ]; then
    echo "Error: Pipeline failed!"
    exit 1
fi

echo ""
echo "=== Post-processing ==="

# Note: Some shell scripts may still be needed for specific operations
# that haven't been fully integrated into the Python module yet


# this will aggregate grants by the same filer to the same grantee in the same tax year, which usually cuts the file by a half to a third.
./combine_grants.sh $FINAL_DIR/grants_latest.tsv $FINAL_DIR/grants_combined.tsv
./combine_grants.sh $FINAL_DIR/inferred_grants.tsv  $FINAL_DIR/grants_pf_combined.tsv

#there are EINs that have gotten money that don't file 990s, (state/local govt i.e. state unis, churches, etc)
python grant_check.py --index-file $FINAL_DIR/charity_latest_with_backfill.tsv --input-file  $FINAL_DIR/grants_combined.tsv --output-file $FINAL_DIR/grants_final.tsv --report-file $FINAL_DIR/filter_501.md
python grant_check.py --index-file $FINAL_DIR/charity_latest_with_backfill.tsv --input-file  $FINAL_DIR/grants_pf_combined.tsv --output-file $FINAL_DIR/grants_pf.tsv --report-file $FINAL_DIR/filter_pf.md


python grant_report.py --input-file $FINAL_DIR/grants_final.tsv --report-file $FINAL_DIR/final_report.md
python grant_report.py --input-file $FINAL_DIR/grants_pf.tsv --report-file $FINAL_DIR/pf_report.md

# so all that work to filtered out the charities by size? guess what? We over filtered have to copy some charities back so the grants have a destination to go to! Need to do that for both sets of grants, thanks for playing!

#./unfilter_from_grants.py --master $FINAL_DIR/charity_latest.tsv --filtered $FINAL_DIR/charites_1M.tsv --grants $FINAL_DIR/grants_pf.tsv --output $FINAL_DIR/charity_semifinal.tsv
#sed script to put them back
#./extract_rows.sh 
#./unfilter_from_grants.py --master $FINAL_DIR/charity_latest.tsv --filtered $FINAL_DIR/charity_semifinal.tsv --grants $FINAL_DIR/grants_final.tsv --output $FINAL_DIR/charity_final.tsv
#sed script to put them back
#./extract_rows.sh 
wc -l $FINAL_DIR/*.tsv 

# zip everything and move it into place
mv $FINAL_DIR/charity_latest.tsv $FINAL_DIR/charity_latest_without_backfill.tsv
mv $FINAL_DIR/charity_latest_with_backfill.tsv $FINAL_DIR/charity_latest.tsv
cp $FINAL_DIR/grants_final.tsv $BROWSE
cp $FINAL_DIR/grants_pf.tsv $BROWSE/grants.pf.tsv
cp $FINAL_DIR/charity_latest.tsv $BROWSE/charities.tsv
cp $FINAL_DIR/contractors.tsv $BROWSE 2>/dev/null || echo "contractors.tsv not found, skipping..."
cp $FINAL_DIR/political_contributions.tsv $BROWSE 2>/dev/null || echo "political_contributions.tsv not found, skipping..."


pushd $BROWSE
rm -rf $BROWSE/tsv_chunks
$TOOLS/split_tsvs.sh
popd




 
