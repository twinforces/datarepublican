#!/usr/bin/env python3
"""
Test script to process XML files in test_xmls directory using parse_filer_address.
Counts expected addresses (USAddress tags) and actual extracted addresses.
Logs results for each file.
"""

import os
import sys
import threading
from io import BytesIO
from lxml import etree

# Add the current directory to sys.path to import extract_utils
sys.path.insert(0, os.path.dirname(__file__))

from extract_utils import parse_filer_address, NAMESPACES

def count_expected_addresses(xml_content):
    """Count the number of USAddress tags in the XML content."""
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        us_addresses = root.findall(".//irs:USAddress", namespaces=NAMESPACES)
        return len(us_addresses)
    except Exception as e:
        print(f"Error parsing XML for expected count: {e}")
        return 0

def test_address_extraction(xml_dir):
    """Process each XML file and compare expected vs actual addresses."""
    results = []

    for filename in sorted(os.listdir(xml_dir)):
        if not filename.endswith('.xml'):
            continue

        xml_path = os.path.join(xml_dir, filename)
        print(f"Processing {filename}...")

        try:
            with open(xml_path, 'rb') as f:
                xml_content = f.read()
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            continue

        # Count expected addresses
        expected = count_expected_addresses(xml_content)

        # Reset thread_local for each file
        thread_local = threading.local()
        thread_local.result = {
            'address_entries': [],
            'debug_address_entries': [],
            'po_box_entries': [],
            'invalid_ein_entries': [],
            'ein_mismatch_set': set(),
            'total_addresses': 0,
            'total_queue_puts': 0,
            'total_address_errors': 0,
            'zip_code_index': {},
            'po_box_zip_index': {},
            'filer_eins': {}
        }

        # Temporarily replace the global thread_local
        import extract_utils
        original_thread_local = extract_utils.thread_local
        extract_utils.thread_local = thread_local

        try:
            # Call parse_filer_address with dummy parameters
            success, ein = parse_filer_address(
                xml_content=xml_content,
                xml_filename=filename,
                row={},  # dummy row
                zip_index={},  # dummy zip_index
                output_dir='.',  # dummy output_dir
                sample_xml=None,
                parse_type="filer",
                skip_address_errors=True
            )

            # Count actual extracted addresses
            actual = len(thread_local.result['address_entries'])

            result = {
                'filename': filename,
                'expected': expected,
                'actual': actual,
                'success': success,
                'ein': ein
            }
            results.append(result)

            print(f"  Expected: {expected}, Actual: {actual}, Success: {success}")

        except Exception as e:
            print(f"Error processing {filename}: {e}")
            result = {
                'filename': filename,
                'expected': expected,
                'actual': 0,
                'success': False,
                'ein': None,
                'error': str(e)
            }
            results.append(result)
        finally:
            # Restore original thread_local
            extract_utils.thread_local = original_thread_local

    return results

def main():
    xml_dir = os.path.join(os.path.dirname(__file__), 'test_xmls')
    if not os.path.exists(xml_dir):
        print(f"Directory {xml_dir} does not exist.")
        sys.exit(1)

    print("Testing address extraction from XML files...")
    results = test_address_extraction(xml_dir)

    print("\nResults Summary:")
    print("=" * 60)
    total_expected = 0
    total_actual = 0
    for result in results:
        print(f"{result['filename']}: Expected={result['expected']}, Actual={result['actual']}, Success={result['success']}")
        if 'error' in result:
            print(f"  Error: {result['error']}")
        total_expected += result['expected']
        total_actual += result['actual']

    print("=" * 60)
    print(f"Total Expected: {total_expected}, Total Actual: {total_actual}")

if __name__ == "__main__":
    main()