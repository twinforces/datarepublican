#!/usr/bin/env python3
"""
test_related_entities.py - Test script to verify that Grant, Contractor, and Political Contribution records are being created
"""

import sys
import os
from lxml import etree
from io import BytesIO

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(__file__))

from parse_990 import parse_990
from parse_990ez import parse_990ez
from parse_990pf import parse_990pf
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning
quiet = False

def test_file(parser_func, xml_file, expected_grants=0, expected_contractors=0, expected_contributions=0):
    """Test a single XML file"""
    print(f"\n=== Testing {xml_file} ===")

    try:
        with open(xml_file, 'rb') as f:
            xml_content = f.read()
    except IOError as e:
        print(f"Error reading XML file {xml_file}: {e}")
        return False

    parser = etree.XMLParser(recover=True)
    tree = etree.parse(BytesIO(xml_content), parser)
    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile'}

    # Extract metadata
    form_type_paths = [
        etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces=namespaces),
        etree.XPath(".//ReturnHeader/ReturnTypeCd")
    ]
    form_type = None
    for xpath in form_type_paths:
        result = xpath(root)
        if result:
            form_type = result[0].text
            break
    form_type = form_type if form_type is not None else "Unknown"

    tax_year_paths = [
        etree.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces=namespaces),
        etree.XPath(".//ReturnHeader/TaxYr")
    ]
    tax_year = None
    for xpath in tax_year_paths:
        result = xpath(root)
        if result:
            tax_year = result[0].text
            break
    tax_year = tax_year if tax_year is not None else "Unknown"

    filer_ein_paths = [
        etree.XPath(".//irs:ReturnHeader/irs:Filer/irs:EIN", namespaces=namespaces),
        etree.XPath(".//ReturnHeader/Filer/EIN")
    ]
    filer_ein = None
    for xpath in filer_ein_paths:
        result = xpath(root)
        if result:
            raw_ein = result[0].text.strip()
            if raw_ein.isdigit():
                filer_ein = f"{int(raw_ein):09d}"
            else:
                filer_ein = "Unknown"
            break
    if filer_ein is None:
        filer_ein = "Unknown"

    print(f"Form Type: {form_type}, Tax Year: {tax_year}, EIN: {filer_ein}")

    # Set up logging
    logger = get_logger("test_related_entities")
    def log_error_func(msg_format, *args, ein=None, exc_info=False):
        if not quiet:
            if exc_info:
                logger.error(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
            else:
                logger.error(msg_format.format(*args) if args else msg_format)

    def log_debug_func(msg_format, *args, ein=None, exc_info=False):
        if not quiet:
            if exc_info:
                logger.debug(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
            else:
                logger.debug(msg_format.format(*args) if args else msg_format)

    # Parse the file
    try:
        charity, officers, grants, contractors, contributions, address = parser_func(
            root, xml_file, xpath_cache={}, filer_ein=filer_ein, tax_year=tax_year, form_type=form_type,
            log_error=log_error_func, xpath_match_stats=None
        )

        print(f"Parsed successfully:")
        print(f"  - Grants: {len(grants)} (expected: {expected_grants})")
        print(f"  - Contractors: {len(contractors)} (expected: {expected_contractors})")
        print(f"  - Political Contributions: {len(contributions)} (expected: {expected_contributions})")

        # Log details of each record
        if grants:
            print("  Grant details:")
            for i, grant in enumerate(grants):
                print(f"    {i+1}. EIN: {grant.grant_ein}, Amount: ${grant.grant_amt}")

        if contractors:
            print("  Contractor details:")
            for i, contractor in enumerate(contractors):
                print(f"    {i+1}. Name: {contractor.contractor_name}, Amount: ${contractor.amount}")

        if contributions:
            print("  Political Contribution details:")
            for i, contrib in enumerate(contributions):
                print(f"    {i+1}. Recipient: {contrib.recipient}, Amount: ${contrib.amount}")

        # Check expectations
        success = (len(grants) == expected_grants and
                  len(contractors) == expected_contractors and
                  len(contributions) == expected_contributions)

        if success:
            print("✓ PASS: Record counts match expectations")
        else:
            print("✗ FAIL: Record counts do not match expectations")

        return success

    except Exception as e:
        print(f"Error parsing {xml_file}: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function"""
    print("Testing Related Entities Parsing")
    print("=" * 50)

    test_files = [
        # Form 990 files
        ("test_xmls/sample_990_1.xml", parse_990, 0, 0, 0),  # No grants/contributions expected
        ("test_xmls/sample_990_2.xml", parse_990, 0, 0, 0),  # No grants/contributions expected
        ("test_xmls/sample_990_3.xml", parse_990, 0, 0, 0),  # Expected 0 political contributions (debugging)

        # Form 990EZ files
        ("test_xmls/sample_990EZ_1.xml", parse_990ez, 0, 0, 0),  # No grants/contributions expected
        ("test_xmls/sample_990EZ_2.xml", parse_990ez, 0, 0, 0),  # No grants/contributions expected
        ("test_xmls/sample_990EZ_3.xml", parse_990ez, 0, 0, 0),  # No grants/contributions expected

        # Form 990PF files
        ("test_xmls/sample_990PF_1.xml", parse_990pf, 1, 0, 0),  # Has grants
        ("test_xmls/sample_990PF_2.xml", parse_990pf, 1, 0, 0),  # Has grants
        ("test_xmls/sample_990PF_3.xml", parse_990pf, 2, 0, 0),  # Has grants
    ]

    total_tests = len(test_files)
    passed_tests = 0

    for xml_file, parser_func, exp_grants, exp_contractors, exp_contributions in test_files:
        if os.path.exists(xml_file):
            if test_file(parser_func, xml_file, exp_grants, exp_contractors, exp_contributions):
                passed_tests += 1
        else:
            print(f"File not found: {xml_file}")

    print(f"\n{'='*50}")
    print(f"Test Results: {passed_tests}/{total_tests} tests passed")

    if passed_tests == total_tests:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())