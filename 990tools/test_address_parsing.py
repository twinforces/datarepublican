#!/usr/bin/env python3
"""
test_address_parsing.py - Test address parsing against sample XML files

Tests address extraction functionality for all form types (990, 990EZ, 990PF)
using the sample XML files in ./test_xmls directory.
"""

import sys
import os
from pathlib import Path
from lxml import etree  # type: ignore
from io import BytesIO
import logging
from logging_utils import get_logger, log_info, log_error as proper_log_error, log_debug as proper_log_debug, log_error, log_debug, log_warning

# Import parsing functions
from parse_990 import parse_address_990
from parse_990ez import parse_address_990ez
from parse_990pf import parse_address_990pf

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_address_parsing(xml_file_path):
    """Test address parsing for a single XML file"""
    try:
        if not quiet:
            logger.info(f"Testing address parsing for: {xml_file_path}")

        # Read XML file
        with open(xml_file_path, 'rb') as f:
            xml_content = f.read()

        # Parse XML
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()

        # Extract basic metadata
        form_type = extract_form_type(root)
        tax_year = extract_tax_year(root)
        filer_ein = extract_filer_ein(root)

        if not quiet:
            logger.info(f"Form type: {form_type}, Tax year: {tax_year}, EIN: {filer_ein}")

        # Parse address based on form type
        address = None
        context = {
            'filer_ein': filer_ein,
            'tax_year': tax_year,
            'form_type': form_type,
            'filer_name': 'Unknown'  # Add filer_name to context
        }
        if form_type == "990":
            address = parse_address_990(root, xml_file_path, context, {})
        elif form_type == "990EZ":
            address = parse_address_990ez(root, xml_file_path, context, {})
        elif form_type == "990PF":
            address = parse_address_990pf(root, xml_file_path, context, {})
        else:
            if not quiet:
                logger.error(f"Unsupported form type: {form_type}")
            return None

        if address:
            if not quiet:
                logger.info(f"Successfully parsed address: {address.canonical_address}")
                logger.info(f"ZIP code: {address.zip_code}, Address type: {address.address_type}")
            return {
                'file': xml_file_path,
                'form_type': form_type,
                'tax_year': tax_year,
                'ein': filer_ein,
                'address': address.canonical_address,
                'zip_code': address.zip_code,
                'address_type': address.address_type
            }
        else:
            if not quiet:
                logger.warning(f"No address found for {xml_file_path}")
            return {
                'file': xml_file_path,
                'form_type': form_type,
                'tax_year': tax_year,
                'ein': filer_ein,
                'address': None,
                'zip_code': None,
                'address_type': None
            }

    except Exception as e:
        if not quiet:
            logger.error(f"Failed to parse {xml_file_path}: {e}")
        return {
            'file': xml_file_path,
            'form_type': 'Unknown',
            'tax_year': 'Unknown',
            'ein': 'Unknown',
            'address': None,
            'zip_code': None,
            'address_type': None,
            'error': str(e)
        }

def extract_form_type(root):
    """Extract form type from XML"""
    from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF
    for xpath in XPATHS_990["form_type"] + XPATHS_990EZ["form_type"] + XPATHS_990PF["form_type"]:
        try:
            result = xpath(root)
            if result:
                return result[0].text
        except:
            continue
    return "Unknown"

def extract_tax_year(root):
    """Extract tax year from XML"""
    from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF
    for xpath in XPATHS_990["tax_year"] + XPATHS_990EZ["tax_year"] + XPATHS_990PF["tax_year"]:
        try:
            result = xpath(root)
            if result:
                year_str = result[0].text
                if year_str and year_str.isdigit():
                    return int(year_str)
        except:
            continue
    return "Unknown"

def extract_filer_ein(root):
    """Extract filer EIN from XML"""
    from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF
    for xpath in XPATHS_990["filer_ein"] + XPATHS_990EZ["filer_ein"] + XPATHS_990PF["filer_ein"]:
        try:
            result = xpath(root)
            if result:
                raw_ein = result[0].text.strip()
                if raw_ein.isdigit():
                    return f"{int(raw_ein):09d}"
                else:
                    return "Unknown"
        except:
            continue
    return "Unknown"

def main():
    """Main test function"""
    test_xmls_dir = Path("./test_xmls")

    if not test_xmls_dir.exists():
        if not quiet:
            logger.error(f"Test XMLs directory not found: {test_xmls_dir}")
        sys.exit(1)

    # Get all XML files
    xml_files = list(test_xmls_dir.glob("*.xml"))
    xml_files.sort()

    if not quiet:
        logger.info(f"Found {len(xml_files)} XML files to test")

    results = []

    for xml_file in xml_files:
        result = test_address_parsing(str(xml_file))
        results.append(result)
        print()  # Add spacing between results

    # Print summary
    print("=" * 80)
    print("ADDRESS PARSING TEST SUMMARY")
    print("=" * 80)

    successful = 0
    failed = 0

    for result in results:
        if result['address']:
            successful += 1
            status = "✓ SUCCESS"
        else:
            failed += 1
            status = "✗ FAILED"

        print(f"{status}: {result['file']}")
        print(f"  Form: {result['form_type']}, EIN: {result['ein']}, Year: {result['tax_year']}")
        if result['address']:
            print(f"  Address: {result['address']}")
            print(f"  ZIP: {result['zip_code']}")
        else:
            print("  Address: None")
        if 'error' in result:
            print(f"  Error: {result['error']}")
        print()

    print(f"Total files tested: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(".1f")

if __name__ == "__main__":
    main()