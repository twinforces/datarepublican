#!/usr/bin/env python3

import sys
sys.path.append('.')
from lxml import etree
from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF, NAMESPACES
import os
import logging

# Enable verbose logging
logging.basicConfig(level=logging.DEBUG)

def test_xpath_with_without_namespace(root, xpath_obj, field_name, xml_filename):
    """Test an XPath with and without the irs: namespace prefix"""
    results = {}

    # Test with namespace
    try:
        result_with_ns = xpath_obj(root)
        results['with_ns'] = len(result_with_ns) if result_with_ns else 0
    except Exception as e:
        results['with_ns'] = f"Error: {str(e)}"

    # Test without namespace by creating a new XPath without irs: prefix
    try:
        xpath_str = xpath_obj.path
        # Remove irs: prefix from the XPath
        xpath_no_ns_str = xpath_str.replace('irs:', '')
        xpath_no_ns = etree.XPath(xpath_no_ns_str)
        result_no_ns = xpath_no_ns(root)
        results['without_ns'] = len(result_no_ns) if result_no_ns else 0
    except Exception as e:
        results['without_ns'] = f"Error: {str(e)}"

    return results

def test_all_xpaths(xml_file):
    """Test all XPath definitions on a given XML file"""
    print(f"\n=== Testing XPaths on {xml_file} ===")

    with open(xml_file, 'rb') as f:
        xml_content = f.read()

    root = etree.fromstring(xml_content)

    # Determine form type
    form_type = None
    for xpath in [
        etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces=NAMESPACES),
        etree.XPath(".//ReturnHeader/ReturnTypeCd")
    ]:
        try:
            result = xpath(root)
            if result:
                form_type = result[0].text
                break
        except:
            continue

    if not form_type:
        print("Could not determine form type")
        return

    print(f"Form type: {form_type}")

    # Select appropriate XPath dictionary
    if form_type == "990":
        xpaths_dict = XPATHS_990
    elif form_type == "990EZ":
        xpaths_dict = XPATHS_990EZ
    elif form_type == "990PF":
        xpaths_dict = XPATHS_990PF
    else:
        print(f"Unknown form type: {form_type}")
        return

    results = {}

    for field_name, xpath_list in xpaths_dict.items():
        if field_name in ['contractor_elements', 'contractors_schedule_l']:  # Skip these for now
            continue

        print(f"\nTesting field: {field_name}")
        field_results = []

        for i, xpath_obj in enumerate(xpath_list):
            print(f"  XPath {i+1}: {xpath_obj.path}")
            test_results = test_xpath_with_without_namespace(root, xpath_obj, field_name, xml_file)
            field_results.append(test_results)
            print(f"    With irs: prefix: {test_results['with_ns']}")
            print(f"    Without irs: prefix: {test_results['without_ns']}")

        results[field_name] = field_results

    return results

def main():
    # Test on available XML files
    test_files = [
        'test_xmls/CHAI.990.xml',
        'test_xmls/amnesty.990.xml'
    ]

    for xml_file in test_files:
        if os.path.exists(xml_file):
            test_all_xpaths(xml_file)
        else:
            print(f"File not found: {xml_file}")

if __name__ == "__main__":
    main()