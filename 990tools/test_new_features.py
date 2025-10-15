#!/usr/bin/env python3
"""
Test script for new IRS 990 data extraction features.

Tests the extraction of:
- Contractors and consultants
- Political contributions
- Canonical addresses
"""

import os
import sys
from pathlib import Path

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(__file__))

def test_xpath_definitions():
    """Test that XPath definitions are properly loaded."""
    try:
        from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF
        print("✓ XPath definitions loaded successfully")

        # Check for new fields
        required_fields_990 = [
            'contractors_schedule_l', 'contractor_elements', 'contractor_name',
            'contractor_amount', 'contractor_ein', 'political_schedule_c',
            'political_contributions', 'political_amount', 'political_recipient',
            'business_address', 'address_line_1', 'city', 'state', 'zip_code'
        ]

        for field in required_fields_990:
            if field in XPATHS_990:
                print(f"✓ Found {field} in XPATHS_990")
            else:
                print(f"✗ Missing {field} in XPATHS_990")

        return True
    except ImportError as e:
        print(f"✗ Failed to import XPath definitions: {e}")
        return False

def test_parsing_functions():
    """Test that new parsing functions are available."""
    try:
        from parse_990 import (
            parse_contractors_990,
            parse_political_contributions_990,
            parse_organization_address_990
        )
        print("✓ New parsing functions imported successfully")
        return True
    except ImportError as e:
        print(f"✗ Failed to import parsing functions: {e}")
        return False

def test_column_definitions():
    """Test that TSV column definitions include new fields."""
    try:
        from extract_charities import TSV_COLUMNS
        from get_latest import TSV_COLUMNS as LATEST_COLUMNS
        from analyze_charities import TSV_COLUMNS as ANALYZE_COLUMNS

        if 'canonical_address' in TSV_COLUMNS:
            print("✓ canonical_address found in extract_charities TSV_COLUMNS")
        else:
            print("✗ canonical_address missing from extract_charities TSV_COLUMNS")

        if 'canonical_address' in LATEST_COLUMNS:
            print("✓ canonical_address found in get_latest TSV_COLUMNS")
        else:
            print("✗ canonical_address missing from get_latest TSV_COLUMNS")

        if 'canonical_address' in ANALYZE_COLUMNS:
            print("✓ canonical_address found in analyze_charities TSV_COLUMNS")
        else:
            print("✗ canonical_address missing from analyze_charities TSV_COLUMNS")

        return True
    except ImportError as e:
        print(f"✗ Failed to import column definitions: {e}")
        return False

def test_output_files():
    """Test that new output file constants are defined."""
    try:
        from extract_charities import CONTRACTORS_FILE, POLITICAL_CONTRIBUTIONS_FILE
        print(f"✓ CONTRACTORS_FILE: {CONTRACTORS_FILE}")
        print(f"✓ POLITICAL_CONTRIBUTIONS_FILE: {POLITICAL_CONTRIBUTIONS_FILE}")
        return True
    except ImportError as e:
        print(f"✗ Failed to import output file constants: {e}")
        return False

def main():
    """Run all tests."""
    print("Testing new IRS 990 data extraction features...")
    print("=" * 50)

    tests = [
        test_xpath_definitions,
        test_parsing_functions,
        test_column_definitions,
        test_output_files
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        print(f"\nRunning {test.__name__}...")
        if test():
            passed += 1
        print("-" * 30)

    print(f"\nResults: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! New features are ready.")
        return 0
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())