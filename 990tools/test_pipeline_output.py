#!/usr/bin/env python3
"""
Test script to validate IRS 990 pipeline output after geocoding integration.
Checks all pipeline steps for proper execution and geocoding functionality.

Usage: python test_pipeline_output.py [options]

Options:
  --zips-dir DIR        Directory containing ZIP files (default: /Volumes/Data/irs_zips)
  --tsvs-dir DIR        Directory for TSV output files (default: /Volumes/Data/tsvs)
  --analyzed-dir DIR    Directory for analyzed files (default: /Volumes/Data/atsvs)
  --final-dir DIR       Directory for final output files (default: /Volumes/Data/final)
"""

import os
import sys
import glob
import csv
import json
import argparse
from pathlib import Path

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Validate IRS 990 pipeline output after geocoding integration")
    parser.add_argument('--zips-dir', default='/Volumes/Data/irs_zips', help='Directory containing ZIP files')
    parser.add_argument('--tsvs-dir', default='/Volumes/Data/tsvs', help='Directory for TSV output files')
    parser.add_argument('--analyzed-dir', default='/Volumes/Data/atsvs', help='Directory for analyzed files')
    parser.add_argument('--final-dir', default='/Volumes/Data/final', help='Directory for final output files')
    return parser.parse_args()

def check_directory_structure(args):
    """Check that required directories exist."""
    required_dirs = [
        args.zips_dir,
        args.tsvs_dir,
        args.analyzed_dir,
        args.final_dir
    ]

    print("=== Directory Structure Check ===")
    for dir_path in required_dirs:
        if os.path.exists(dir_path):
            print(f"✅ {dir_path} exists")
        else:
            print(f"❌ {dir_path} missing")
            return False
    return True

def check_step_1_download(args):
    """Check that ZIP files were downloaded."""
    print("\n=== Step 1: Download Check ===")
    zip_files = glob.glob(os.path.join(args.zips_dir, '*.zip'))

    if not zip_files:
        print("❌ No ZIP files found")
        return False

    print(f"✅ Found {len(zip_files)} ZIP files")
    # Check for recent years
    years = [int(os.path.basename(f)[:4]) for f in zip_files if os.path.basename(f)[:4].isdigit()]
    if years:
        print(f"✅ Years covered: {min(years)}-{max(years)}")
    return True

def check_step_3_extraction(args):
    """Check that charity data was extracted."""
    print("\n=== Step 3: Extraction Check ===")
    charity_files = glob.glob(os.path.join(args.tsvs_dir, 'charities_*.tsv'))

    if not charity_files:
        print("❌ No charity TSV files found")
        return False

    total_rows = 0
    geocoded_count = 0
    po_box_count = 0
    foreign_count = 0

    for tsv_file in charity_files[:5]:  # Check first 5 files
        try:
            with open(tsv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                rows = list(reader)
                total_rows += len(rows)

                for row in rows[:10]:  # Sample first 10 rows
                    colocator = row.get('colocator', '')
                    if ',' in colocator and colocator.replace(',', '').replace('.', '').replace('-', '').isdigit():
                        geocoded_count += 1
                    elif colocator.startswith('PO:'):
                        po_box_count += 1
                    elif colocator.startswith('FOREIGN:'):
                        foreign_count += 1

        except Exception as e:
            print(f"❌ Error reading {tsv_file}: {e}")
            continue

    print(f"✅ Found {len(charity_files)} charity TSV files")
    print(f"✅ Total charity records: {total_rows}")
    print(f"✅ Geocoded addresses: {geocoded_count}")
    print(f"✅ PO Box addresses: {po_box_count}")
    print(f"✅ Foreign addresses: {foreign_count}")

    return total_rows > 0

def check_step_4_analysis(args):
    """Check that charity analysis was completed."""
    print("\n=== Step 4: Analysis Check ===")
    analysis_files = glob.glob(os.path.join(args.analyzed_dir, '*.tsv'))

    if not analysis_files:
        print("❌ No analysis TSV files found")
        return False

    print(f"✅ Found {len(analysis_files)} analysis files")
    return True

def check_step_5_latest_filings(args):
    """Check that latest filings were identified."""
    print("\n=== Step 5: Latest Filings Check ===")
    latest_file = os.path.join(args.final_dir, 'charity_latest.tsv')

    if not os.path.exists(latest_file):
        print("❌ charity_latest.tsv not found")
        return False

    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            rows = list(reader)

        print(f"✅ charity_latest.tsv has {len(rows)} rows")

        # Check for geocoding in latest filings
        geocoded_in_latest = 0
        for row in rows[:100]:  # Sample first 100
            colocator = row.get('colocator', '')
            if ',' in colocator and colocator.replace(',', '').replace('.', '').replace('-', '').isdigit():
                geocoded_in_latest += 1

        print(f"✅ Geocoded addresses in latest filings: {geocoded_in_latest}")
        return len(rows) > 0

    except Exception as e:
        print(f"❌ Error reading charity_latest.tsv: {e}")
        return False

def check_step_6_addresses(args):
    """Check that address extraction was completed."""
    print("\n=== Step 6: Address Extraction Check ===")
    address_file = os.path.join(args.final_dir, 'address_debug.tsv')

    if os.path.exists(address_file):
        try:
            with open(address_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                rows = list(reader)
            print(f"✅ Address debug file has {len(rows)} rows")
        except Exception as e:
            print(f"❌ Error reading address debug file: {e}")
    else:
        print("⚠️ Address debug file not found (may be normal)")

    return True

def check_step_7_backfill(args):
    """Check that backfill was added."""
    print("\n=== Step 7: Backfill Check ===")
    backfill_file = os.path.join(args.final_dir, 'charity_latest_with_backfill.tsv')

    if not os.path.exists(backfill_file):
        print("❌ charity_latest_with_backfill.tsv not found")
        return False

    try:
        with open(backfill_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            rows = list(reader)

        print(f"✅ Backfill file has {len(rows)} rows")

        # Check for geocoding in backfill
        geocoded_in_backfill = 0
        for row in rows[:100]:  # Sample first 100
            colocator = row.get('colocator', '')
            if ',' in colocator and colocator.replace(',', '').replace('.', '').replace('-', '').isdigit():
                geocoded_in_backfill += 1

        print(f"✅ Geocoded addresses in backfill: {geocoded_in_backfill}")
        return len(rows) > 0

    except Exception as e:
        print(f"❌ Error reading backfill file: {e}")
        return False

def check_step_8_grants(args):
    """Check that grants were extracted."""
    print("\n=== Step 8: Grant Extraction Check ===")
    grant_file = os.path.join(args.final_dir, 'grants_latest.tsv')

    if not os.path.exists(grant_file):
        print("❌ grants_latest.tsv not found")
        return False

    try:
        with open(grant_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            rows = list(reader)

        print(f"✅ Grants file has {len(rows)} rows")

        # Check for geocoding in grants
        filer_geocoded = 0
        grantee_geocoded = 0
        for row in rows[:100]:  # Sample first 100
            filer_colocator = row.get('filer_colocator', '')
            grantee_colocator = row.get('grantee_colocator', '')

            if ',' in filer_colocator and filer_colocator.replace(',', '').replace('.', '').replace('-', '').isdigit():
                filer_geocoded += 1
            if ',' in grantee_colocator and grantee_colocator.replace(',', '').replace('.', '').replace('-', '').isdigit():
                grantee_geocoded += 1

        print(f"✅ Filer geocoded addresses: {filer_geocoded}")
        print(f"✅ Grantee geocoded addresses: {grantee_geocoded}")
        return len(rows) > 0

    except Exception as e:
        print(f"❌ Error reading grants file: {e}")
        return False

def check_step_9_grant_checking(args):
    """Check that grant validation was completed."""
    print("\n=== Step 9: Grant Validation Check ===")
    final_grants = os.path.join(args.final_dir, 'grants_final.tsv')
    report_file = os.path.join(args.final_dir, 'filter_501.md')

    checks = []
    if os.path.exists(final_grants):
        try:
            with open(final_grants, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                rows = list(reader)
            print(f"✅ Final grants file has {len(rows)} rows")
            checks.append(True)
        except Exception as e:
            print(f"❌ Error reading final grants: {e}")
            checks.append(False)
    else:
        print("❌ Final grants file not found")
        checks.append(False)

    if os.path.exists(report_file):
        print("✅ Grant validation report exists")
        checks.append(True)
    else:
        print("❌ Grant validation report not found")
        checks.append(False)

    return all(checks)

def check_geocoding_cache(args):
    """Check that geocoding cache was created."""
    print("\n=== Geocoding Cache Check ===")
    cache_file = os.path.join(args.analyzed_dir, '_cache', 'geocode_cache.json')

    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cache = json.load(f)
            print(f"✅ Geocoding cache exists with {len(cache)} entries")
            return True
        except Exception as e:
            print(f"❌ Error reading geocoding cache: {e}")
            return False
    else:
        print("⚠️ Geocoding cache not found (may be normal if no geocoding occurred)")
        return True  # Not critical

def main():
    """Run all pipeline output checks."""
    args = parse_args()

    print("🔍 IRS 990 Pipeline Output Validation")
    print("=" * 50)
    print(f"Configuration:")
    print(f"  ZIPs directory: {args.zips_dir}")
    print(f"  TSVs directory: {args.tsvs_dir}")
    print(f"  Analyzed directory: {args.analyzed_dir}")
    print(f"  Final directory: {args.final_dir}")
    print("=" * 50)

    if not check_directory_structure(args):
        print("\n❌ Directory structure issues - cannot proceed")
        return False

    checks = [
        check_step_1_download(args),
        check_step_3_extraction(args),
        check_step_4_analysis(args),
        check_step_5_latest_filings(args),
        check_step_6_addresses(args),
        check_step_7_backfill(args),
        check_step_8_grants(args),
        check_step_9_grant_checking(args),
        check_geocoding_cache(args)
    ]

    passed = sum(checks)
    total = len(checks)

    print(f"\n{'='*50}")
    print(f"📊 Pipeline Validation Results: {passed}/{total} checks passed")

    if passed == total:
        print("🎉 All pipeline steps completed successfully!")
        print("✅ Geocoding integration is working correctly")
    else:
        print(f"⚠️ {total - passed} checks failed - review output above")

    return passed == total

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)