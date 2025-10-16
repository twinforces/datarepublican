#!/bin/bash

# IRS 990 Tools - Test Pipeline Script
# This script tests the pipeline with local directories and available test data

# Configuration - Local paths for testing
export ZIPS_DIR="$PWD/test_zips"
export OUT_DIR="$PWD/test_output"
export ANAL_DIR="$PWD/test_analyzed"
export FINAL_DIR="$PWD/test_final"
export DR_ROOT="$PWD/.."
export TOOLS="$PWD"
export BROWSE="$DR_ROOT/browse"

# Processing parameters
START_YEAR=2023
END_YEAR=2023
MINIMUM_D=0
WORKER_THREADS=2

echo "=== IRS 990 Tools Test Pipeline ==="
echo "Processing years: $START_YEAR to $END_YEAR"
echo "Zips directory: $ZIPS_DIR"
echo "Output directory: $FINAL_DIR"
echo "Worker threads: $WORKER_THREADS"
echo ""

# Create test directories
mkdir -p "$ZIPS_DIR"
mkdir -p "$OUT_DIR"
mkdir -p "$ANAL_DIR"
mkdir -p "$FINAL_DIR"

# Change to tools directory
cd "$TOOLS" || exit 1

echo "Testing individual components first..."

# Test 1: Test contractor extraction with our test XML
echo "1. Testing contractor extraction..."
python test_contractors.py

# Test 2: Test the unified tool help
echo "2. Testing irs990tools help..."
python irs990tools.py --help

# Test 3: Test individual commands
echo "3. Testing individual irs990tools commands..."

# Test extract-charities command with test XML
echo "   Testing extract-charities command..."
python irs990tools.py extract-charities --xml-dir "$PWD/test_xmls" --output-dir "$OUT_DIR" --verbose

# Test analyze-charities command
echo "   Testing analyze-charities command..."
python irs990tools.py analyze-charities --input-dir "$OUT_DIR" --output-dir "$ANAL_DIR" --verbose

# Test get-latest command
echo "   Testing get-latest command..."
python irs990tools.py get-latest --input-dir "$ANAL_DIR" --output-dir "$FINAL_DIR" --verbose

echo ""
echo "=== Test Results ==="
echo "Check the following directories for output:"
echo "  - $OUT_DIR (extracted data)"
echo "  - $ANAL_DIR (analyzed data)"
echo "  - $FINAL_DIR (final processed data)"

# List any generated files
echo ""
echo "Generated files:"
find "$OUT_DIR" -name "*.tsv" 2>/dev/null | head -10
find "$ANAL_DIR" -name "*.tsv" 2>/dev/null | head -10

echo ""
echo "Test pipeline completed!"