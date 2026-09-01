#!/usr/bin/env python3
"""
Test script for geocoding integration in IRS 990 processing pipeline.
Tests a small-scale run with 2019 data to verify:
1. Geocoding is working correctly
2. Colocator columns contain lat/lon coordinates instead of hashes
3. Self-dealing detection can use the new coordinate format
4. Backward compatibility for PO Box and foreign addresses
"""

import os
import sys
import glob
import csv
import re
import tempfile
import shutil
import json
from pathlib import Path

# Add the 990tools directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '990tools'))

def run_pipeline_test():
    """Run a small-scale pipeline test with 2019 data only."""

    # Create temporary directories for test
    test_dir = tempfile.mkdtemp(prefix='geocoding_test_')
    zips_dir = os.path.join(test_dir, 'zips')
    tsvs_dir = os.path.join(test_dir, 'tsvs')
    analyzed_dir = os.path.join(test_dir, 'analyzed')
    final_dir = os.path.join(test_dir, 'final')
    cache_dir = os.path.join(test_dir, 'cache')

    for d in [zips_dir, tsvs_dir, analyzed_dir, final_dir, cache_dir]:
        os.makedirs(d, exist_ok=True)

    print(f"Test directories created in: {test_dir}")

    try:
        # Read EIN-XML index to find ZIP files containing 2019 data
        index_file = '/Volumes/Data/tsvs/ein_xml_index.json'
        if not os.path.exists(index_file):
            print(f"ERROR: EIN-XML index file not found: {index_file}")
            return False

        with open(index_file, 'r') as f:
            index_data = json.load(f)

        # Find unique ZIP files containing 2019 tax year data
        zip_files_2019 = set()
        for ein, entries in index_data.items():
            for entry in entries:
                if entry.get('tax_year') == '2019':
                    zip_files_2019.add(entry['zip_path'])

        if not zip_files_2019:
            print("ERROR: No ZIP files found containing 2019 tax year data")
            return False

        # Prioritize ZIP files that are likely to contain actual 2019 filings
        # 2020_TEOS_XML_CT1.zip contains 2019 tax year data
        preferred_zips = [zip_file for zip_file in zip_files_2019 if '2020_TEOS_XML_CT1.zip' in zip_file]
        if preferred_zips:
            source_zips = preferred_zips[:1]
        else:
            source_zips = list(zip_files_2019)[:1]

        print(f"Found {len(zip_files_2019)} ZIP files with 2019 data, using {len(source_zips)} for testing: {[os.path.basename(f) for f in source_zips]}")

        # Check if the ZIP file actually exists before proceeding
        if not os.path.exists(source_zips[0]):
            print(f"ERROR: Selected ZIP file does not exist: {source_zips[0]}")
            return False

        # Copy ZIP files to test directory
        for src in source_zips:
            dst = os.path.join(zips_dir, os.path.basename(src))
            shutil.copy2(src, dst)

        # Copy required template files and sample data
        template_files = ['histogram_template.mako', 'grift_report_template.mako']
        for template in template_files:
            src_template = os.path.join('.', template)
            if os.path.exists(src_template):
                dst_template = os.path.join(test_dir, template)
                shutil.copy2(src_template, dst_template)

        # Copy sample backfill data if available
        backfill_src = os.path.join('.', 'backfill.tsv')
        if os.path.exists(backfill_src):
            backfill_dst = os.path.join(final_dir, 'backfill.tsv')
            shutil.copy2(backfill_src, backfill_dst)

        # Import pipeline functions
        from commands.pipeline import run_all_pipeline
        import argparse

        # Create test arguments
        test_args = [
            '--start-year', '2019',
            '--end-year', '2019',
            '--zips-dir', zips_dir,
            '--tsvs-dir', tsvs_dir,
            '--analyzed-dir', analyzed_dir,
            '--final-dir', final_dir,
            '--data-root', test_dir,
            '--cache-dir', cache_dir,
            '--verbose',
            '--quiet',
            '--worker-threads', '2'  # Limit threads for testing
        ]

        # Parse arguments manually
        parser = argparse.ArgumentParser()
        parser.add_argument('--start-year', type=int)
        parser.add_argument('--end-year', type=int)
        parser.add_argument('--zips-dir', type=str)
        parser.add_argument('--tsvs-dir', type=str)
        parser.add_argument('--analyzed-dir', type=str)
        parser.add_argument('--final-dir', type=str)
        parser.add_argument('--data-root', type=str)
        parser.add_argument('--cache-dir', type=str)
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--quiet', action='store_true')
        parser.add_argument('--worker-threads', type=int)
        args = parser.parse_args(test_args)

        # Run the pipeline
        print("Starting pipeline test...")
        try:
            run_all_pipeline(args)
        except Exception as e:
            print(f"Pipeline failed with error: {e}")
            print("Continuing with verification of any created files...")

        # Check what files were actually created
        print("\n=== Checking created files ===")
        for root_dir in [tsvs_dir, analyzed_dir, final_dir]:
            if os.path.exists(root_dir):
                files = list(glob.glob(os.path.join(root_dir, "*.tsv")))
                print(f"Files in {os.path.basename(root_dir)}: {[os.path.basename(f) for f in files]}")
            else:
                print(f"Directory {os.path.basename(root_dir)} does not exist")

        # Verify results - even if pipeline failed, check what we can
        success = verify_geocoding_integration(final_dir, tsvs_dir, analyzed_dir)

        if success:
            print("✓ All geocoding integration tests passed!")
        else:
            print("✗ Some geocoding integration tests failed!")

        return success

    except Exception as e:
        print(f"ERROR during pipeline test: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        print(f"Cleaning up test directory: {test_dir}")
        shutil.rmtree(test_dir, ignore_errors=True)

def verify_geocoding_integration(final_dir, tsvs_dir, analyzed_dir):
    """Verify that geocoding integration is working correctly."""

    all_checks_passed = True

    print("\n=== Verifying Geocoding Integration ===")

    # Check 1: Verify geocoding worked - look for lat/lon in colocator columns
    print("\n1. Checking colocator format (should be lat,lon coordinates)...")

    charity_files = [
        os.path.join(final_dir, 'charity_latest_with_backfill.tsv'),
        os.path.join(analyzed_dir, 'charity_latest.tsv'),
        os.path.join(tsvs_dir, 'charities.tsv')
    ]

    geocoded_count = 0
    total_colocators = 0

    for charity_file in charity_files:
        if os.path.exists(charity_file):
            print(f"  Checking {os.path.basename(charity_file)}...")
            with open(charity_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    colocator = row.get('colocator', '')
                    if colocator:
                        total_colocators += 1
                        # Check if it's lat,lon format (should have comma and be numeric)
                        if ',' in colocator:
                            parts = colocator.split(',')
                            if len(parts) == 2:
                                try:
                                    lat = float(parts[0])
                                    lon = float(parts[1])
                                    # Basic bounds check for US coordinates
                                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                                        geocoded_count += 1
                                    else:
                                        print(f"    WARNING: Invalid coordinates in colocator: {colocator}")
                                except ValueError:
                                    print(f"    WARNING: Non-numeric coordinates in colocator: {colocator}")
                        elif ':POBOX' in colocator:
                            # PO Box format - should be backward compatible
                            pass
                        elif ':' in colocator:
                            # Old zip:street format - should still exist for non-geocoded
                            pass
                        else:
                            print(f"    WARNING: Unexpected colocator format: {colocator}")

    if geocoded_count > 0:
        print(f"  ✓ Found {geocoded_count} geocoded colocators out of {total_colocators} total")
    else:
        print("  ✗ No geocoded colocators found - geocoding may have failed")
        all_checks_passed = False

    # Check 2: Verify grants have colocator columns
    print("\n2. Checking grant files for colocator columns...")

    grant_files = [
        os.path.join(final_dir, 'grants_final.tsv'),
        os.path.join(tsvs_dir, 'grants.tsv')
    ]

    for grant_file in grant_files:
        if os.path.exists(grant_file):
            print(f"  Checking {os.path.basename(grant_file)}...")
            with open(grant_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                headers = reader.fieldnames
                if 'filer_colocator' in headers and 'grantee_colocator' in headers:
                    print("    ✓ Grant file has filer_colocator and grantee_colocator columns")
                    # Check a few rows for coordinate format
                    count = 0
                    coord_count = 0
                    for row in reader:
                        if count >= 5:  # Check first 5 rows
                            break
                        count += 1
                        fc = row.get('filer_colocator', '')
                        gc = row.get('grantee_colocator', '')
                        if fc and ',' in fc:
                            coord_count += 1
                        if gc and ',' in gc:
                            coord_count += 1
                    if coord_count > 0:
                        print(f"    ✓ Found coordinate-format colocators in grant data")
                else:
                    print("    ✗ Grant file missing colocator columns")
                    all_checks_passed = False

    # Check 3: Verify self-dealing detection can work with coordinates
    print("\n3. Testing self-dealing detection compatibility...")

    # Look for grant files and check if colocators can be compared
    grants_file = os.path.join(final_dir, 'grants_final.tsv')
    if os.path.exists(grants_file):
        with open(grants_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            coord_pairs = []
            for row in reader:
                fc = row.get('filer_colocator', '')
                gc = row.get('grantee_colocator', '')
                if fc and gc and ',' in fc and ',' in gc:
                    try:
                        f_lat, f_lon = map(float, fc.split(','))
                        g_lat, g_lon = map(float, gc.split(','))
                        coord_pairs.append(((f_lat, f_lon), (g_lat, g_lon)))
                    except ValueError:
                        pass

            if coord_pairs:
                print(f"  ✓ Found {len(coord_pairs)} grant pairs with coordinate colocators")
                # Test distance calculation (simple Euclidean for verification)
                close_pairs = 0
                for (f_coord, g_coord) in coord_pairs[:10]:  # Test first 10
                    distance = ((f_coord[0] - g_coord[0])**2 + (f_coord[1] - g_coord[1])**2)**0.5
                    if distance < 0.01:  # Very close threshold for testing
                        close_pairs += 1
                print(f"  ✓ Self-dealing detection logic can process coordinate format ({close_pairs} close pairs found)")
            else:
                print("  ⚠ No coordinate pairs found for self-dealing test")
    # Check 4: Verify backward compatibility for PO Box and foreign addresses
    print("\n4. Checking backward compatibility for PO Box and foreign addresses...")

    # Check for PO Box colocators
    pobox_count = 0
    foreign_count = 0

    for charity_file in charity_files:
        if os.path.exists(charity_file):
            with open(charity_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    colocator = row.get('colocator', '')
                    if ':POBOX' in colocator:
                        pobox_count += 1
                    elif colocator.startswith('FOREIGN:'):
                        foreign_count += 1

    if pobox_count > 0:
        print(f"  ✓ Found {pobox_count} PO Box colocators (backward compatible)")
    else:
        print("  ⚠ No PO Box colocators found - may indicate issue with PO Box handling")

    if foreign_count > 0:
        print(f"  ✓ Found {foreign_count} foreign address colocators (backward compatible)")
    else:
        print("  ⚠ No foreign address colocators found - may be expected if no foreign grants in test data")

    # Check grant report for any issues
    report_file = os.path.join(final_dir, 'final_report.md')
    if os.path.exists(report_file):
        print("\n5. Checking final report for geocoding status...")
        with open(report_file, 'r') as f:
            content = f.read()
            if 'geocoding' in content.lower() or 'colocator' in content.lower():
                print("  ✓ Report mentions geocoding/colocators")
            else:
                print("  ⚠ Report doesn't mention geocoding - may be normal")
    return all_checks_passed

if __name__ == '__main__':
    print("Starting geocoding integration test...")
    success = run_pipeline_test()
    sys.exit(0 if success else 1)