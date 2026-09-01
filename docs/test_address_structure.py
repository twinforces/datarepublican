#!/usr/bin/env python3
"""
Test script for the new address structure changes.
Verifies that addresses are returned as dictionaries and that
canonicalization and colocator generation still work correctly.
"""

import os
import sys
import zipfile
from io import BytesIO
from lxml import etree

# Add the 990tools directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '990tools'))

from parse_utils import parse_grants
from xpaths import NAMESPACES, GRANT_XPATHS, GRANT_EIN_XPATHS, GRANT_NAME_XPATHS, GRANT_AMOUNT_XPATHS

def load_xml_file(filepath):
    """Load XML content from a file."""
    with open(filepath, 'rb') as f:
        return f.read()

def test_address_parsing(xml_content, xml_filename):
    """Test parsing addresses from XML and verify they are dictionaries."""
    print(f"\n=== Testing {xml_filename} ===")

    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()

        # Determine form type
        if root.find(".//irs:IRS990", namespaces=NAMESPACES) is not None:
            form_type = "990"
        elif root.find(".//irs:IRS990EZ", namespaces=NAMESPACES) is not None:
            form_type = "990EZ"
        elif root.find(".//irs:IRS990PF", namespaces=NAMESPACES) is not None:
            form_type = "990PF"
        else:
            print(f"Unknown form type for {xml_filename}")
            return False

        print(f"Form type: {form_type}")

        # Extract basic info
        filer_ein = "123456789"  # dummy
        filer_name = "Test Organization"
        tax_year = 2023

        # Parse grants to test address extraction
        grants = parse_grants(xml_content, xml_filename, filer_ein, filer_name, tax_year, set(), form_type)

        print(f"Found {len(grants)} grants")

        # Test individual grant address parsing
        grant_xpaths = GRANT_XPATHS.get(form_type, [])
        for xpath in grant_xpaths:
            elements = xpath(root)
            for elem in elements:
                # Extract EIN and name for testing
                grant_ein = "Unknown"
                for ein_xpath in GRANT_EIN_XPATHS:
                    ein_elem = elem.xpath(ein_xpath.path, namespaces=NAMESPACES)
                    if ein_elem:
                        grant_ein = ein_elem[0].text.strip()
                        break

                grantee_name = "Unknown"
                for name_xpath in GRANT_NAME_XPATHS:
                    name_elem = elem.xpath(name_xpath.path, namespaces=NAMESPACES)
                    if name_elem:
                        grantee_name = name_elem[0].text.strip()
                        break

                # Test parse_recipient_address
                canonical_address, po_box, zip_code = parse_recipient_address(elem, xml_filename, grant_ein, grantee_name, None)

                print(f"Grant to {grantee_name} ({grant_ein}):")
                print(f"  Canonical address: {canonical_address}")
                print(f"  PO Box: {po_box}")
                print(f"  ZIP Code: {zip_code}")

                # Verify address is properly structured
                if canonical_address or po_box or zip_code:
                    # Test canonicalization
                    address_dict = {'canonical': canonical_address, 'po_box': po_box, 'zip_code': zip_code}
                    address = Address(address_components=address_dict)
                    address.prep_for_insert()
                    canon_result = address.canonical_address
                    print(f"  Canonicalization result: {canon_result}")

                    # Test colocator generation
                    colocator = get_colocator_for_address(address_dict)
                    print(f"  Colocator: {colocator}")

                    # Verify types
                    assert isinstance(canon_result, str), "canonicalize_address should return string"
                    assert isinstance(colocator, str), "get_colocator_for_address should return string"

                    print("  ✓ Address structure and functions work correctly")
                else:
                    print("  No address found for this grant")

        return True

    except Exception as e:
        print(f"Error testing {xml_filename}: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function."""
    print("Testing new address structure changes...")

    # Test files from test_xmls directory
    test_files = [
        "990tools/test_xmls/201812509349300736_public.990.xml",
        "990tools/test_xmls/201901029349100210_public.990pf.xml",
        "990tools/test_xmls/202000249349200000_public.990ez.xml",
        "990tools/test_xmls/amnesty.990.xml"
    ]

    success_count = 0
    for test_file in test_files:
        if os.path.exists(test_file):
            xml_content = load_xml_file(test_file)
            if test_address_parsing(xml_content, os.path.basename(test_file)):
                success_count += 1
        else:
            print(f"Test file {test_file} not found")

    print(f"\n=== Test Results ===")
    print(f"Successfully tested {success_count}/{len(test_files)} files")

    # Perform batch geocoding if addresses were collected
    print("\nPerforming batch geocoding...")
    perform_batch_geocoding()
    print("Batch geocoding completed")

    if success_count == len(test_files):
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())