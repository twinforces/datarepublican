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

# Run the complete pipeline using the new unified tool with performance timing
echo "Running complete IRS 990 processing pipeline with performance monitoring..."
LOG_FILE="$FINAL_DIR/pipeline_$(date +%Y%m%d_%H%M%S).log"
echo "Logging to: $LOG_FILE"

# Start overall timing
OVERALL_START=$(date +%s)
echo "=== Pipeline Performance Test Started: $(date) ===" >> "$LOG_FILE"
echo "Processing years: $START_YEAR-$END_YEAR (full dataset for bottleneck analysis)" >> "$LOG_FILE"
echo "Worker threads: $WORKER_THREADS" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

python irs990tools.py --command run-all \
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
  --verbose 2>&1 | tee -a "$LOG_FILE"

# Calculate overall duration
OVERALL_END=$(date +%s)
OVERALL_DURATION=$((OVERALL_END - OVERALL_START))
echo "" >> "$LOG_FILE"
echo "=== Pipeline Performance Summary ===" >> "$LOG_FILE"
echo "Total execution time: $OVERALL_DURATION seconds" >> "$LOG_FILE"
echo "Pipeline completed at: $(date)" >> "$LOG_FILE"

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
echo "Combining grants..."
./combine_grants.sh $FINAL_DIR/grants_latest.tsv $FINAL_DIR/grants_combined.tsv 2>&1 | tee -a "$LOG_FILE"
./combine_grants.sh $FINAL_DIR/inferred_grants.tsv  $FINAL_DIR/grants_pf_combined.tsv 2>&1 | tee -a "$LOG_FILE"

#there are EINs that have gotten money that don't file 990s, (state/local govt i.e. state unis, churches, etc)
echo "Checking grants..."
python grant_check.py --index-file $FINAL_DIR/charity_latest_with_backfill.tsv --input-file  $FINAL_DIR/grants_combined.tsv --output-file $FINAL_DIR/grants_final.tsv --report-file $FINAL_DIR/filter_501.md 2>&1 | tee -a "$LOG_FILE"
python grant_check.py --index-file $FINAL_DIR/charity_latest_with_backfill.tsv --input-file  $FINAL_DIR/grants_pf_combined.tsv --output-file $FINAL_DIR/grants_pf.tsv --report-file $FINAL_DIR/filter_pf.md 2>&1 | tee -a "$LOG_FILE"

echo "Generating reports..."
python grant_report.py --input-file $FINAL_DIR/grants_final.tsv --report-file $FINAL_DIR/final_report.md 2>&1 | tee -a "$LOG_FILE"
python grant_report.py --input-file $FINAL_DIR/grants_pf.tsv --report-file $FINAL_DIR/pf_report.md 2>&1 | tee -a "$LOG_FILE"

# so all that work to filtered out the charities by size? guess what? We over filtered have to copy some charities back so the grants have a destination to go to! Need to do that for both sets of grants, thanks for playing!

#./unfilter_from_grants.py --master $FINAL_DIR/charity_latest.tsv --filtered $FINAL_DIR/charites_1M.tsv --grants $FINAL_DIR/grants_pf.tsv --output $FINAL_DIR/charity_semifinal.tsv
#sed script to put them back
#./extract_rows.sh 
#./unfilter_from_grants.py --master $FINAL_DIR/charity_latest.tsv --filtered $FINAL_DIR/charity_semifinal.tsv --grants $FINAL_DIR/grants_final.tsv --output $FINAL_DIR/charity_final.tsv
#sed script to put them back
#./extract_rows.sh 
wc -l $FINAL_DIR/*.tsv 

# zip everything and move it into place
echo "Finalizing files..."
mv $FINAL_DIR/charity_latest.tsv $FINAL_DIR/charity_latest_without_backfill.tsv 2>&1 | tee -a "$LOG_FILE"
mv $FINAL_DIR/charity_latest_with_backfill.tsv $FINAL_DIR/charity_latest.tsv 2>&1 | tee -a "$LOG_FILE"
cp $FINAL_DIR/grants_final.tsv $BROWSE 2>&1 | tee -a "$LOG_FILE"
cp $FINAL_DIR/grants_pf.tsv $BROWSE/grants.pf.tsv 2>&1 | tee -a "$LOG_FILE"
cp $FINAL_DIR/charity_latest.tsv $BROWSE/charities.tsv 2>&1 | tee -a "$LOG_FILE"
cp $FINAL_DIR/contractors.tsv $BROWSE 2>/dev/null || echo "contractors.tsv not found, skipping..." 2>&1 | tee -a "$LOG_FILE"
cp $FINAL_DIR/political_contributions.tsv $BROWSE 2>/dev/null || echo "political_contributions.tsv not found, skipping..." 2>&1 | tee -a "$LOG_FILE"

echo "Splitting TSVs for web interface..."
pushd $BROWSE
rm -rf $BROWSE/tsv_chunks 2>&1 | tee -a "$LOG_FILE"
$TOOLS/split_tsvs.sh 2>&1 | tee -a "$LOG_FILE"
popd

echo "Pipeline complete! Log saved to: $LOG_FILE"




 
