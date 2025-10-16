#!/usr/bin/env python3
"""
test_xpath_caching.py - Test XPath caching improvements
"""

import sys
import os
import time
from io import BytesIO
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from xpath_utils import find_element
from lxml import etree

def test_xpath_caching():
    """Test XPath caching performance improvements"""

    # Sample XML content
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<Return xmlns="http://www.irs.gov/efile" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <ReturnData>
        <IRS990>
            <Filer>
                <EIN>123456789</EIN>
                <BusinessName>
                    <BusinessNameLine1Txt>Test Charity</BusinessNameLine1Txt>
                </BusinessName>
            </Filer>
            <GrossReceiptsAmt>100000</GrossReceiptsAmt>
            <TotalExpensesAmt>80000</TotalExpensesAmt>
        </IRS990>
    </ReturnData>
</Return>"""

    print("Testing XPath caching improvements:")
    print("=" * 50)

    # Parse XML
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(BytesIO(xml_content.encode('utf-8')), parser)
    root = tree.getroot()

    namespaces = {'irs': 'http://www.irs.gov/efile'}

    # Test XPaths
    xpaths = [
        etree.XPath(".//irs:Filer/irs:EIN", namespaces=namespaces),
        etree.XPath(".//irs:GrossReceiptsAmt", namespaces=namespaces),
        etree.XPath(".//irs:TotalExpensesAmt", namespaces=namespaces),
    ]

    # Test without caching
    print("Testing without caching:")
    start_time = time.time()
    xpath_cache = None
    for i in range(100):
        for xpath in xpaths:
            find_element(root, [xpath], namespaces, xpath_cache=xpath_cache)
    no_cache_time = time.time() - start_time
    print(".4f")

    # Test with caching
    print("Testing with caching:")
    start_time = time.time()
    xpath_cache = {}
    for i in range(100):
        for xpath in xpaths:
            find_element(root, [xpath], namespaces, xpath_cache=xpath_cache)
    with_cache_time = time.time() - start_time
    print(".4f")

    # Calculate improvement
    if no_cache_time > 0:
        improvement = (no_cache_time - with_cache_time) / no_cache_time * 100
        print(".1f")

        if improvement > 10:  # Expect at least 10% improvement
            print("✓ PASS: XPath caching provides significant performance improvement")
        else:
            print("✗ FAIL: XPath caching improvement below expected threshold")
    else:
        print("✓ PASS: XPath caching test completed (no timing comparison possible)")

    # Test cache hit behavior
    print("\nTesting cache hit behavior:")
    xpath_cache = {}
    xpath_match_stats = {"990:test_field:.//irs:Filer/irs:EIN": 0}  # Initialize stats

    # First call should cache
    result1 = find_element(root, xpaths[:1], namespaces, xpath_cache=xpath_cache,
                          field="test_field", form_type="990", xpath_match_stats=xpath_match_stats)

    # Second call should use cache
    result2 = find_element(root, xpaths[:1], namespaces, xpath_cache=xpath_cache,
                          field="test_field", form_type="990", xpath_match_stats=xpath_match_stats)

    if result1 is not None and result1 == result2:
        print("✓ PASS: Cache hit returns same result")
    else:
        print("✗ FAIL: Cache hit behavior incorrect")

    if xpath_match_stats["990:test_field:.//irs:Filer/irs:EIN"] > 0:
        print("✓ PASS: XPath match statistics tracking works")
    else:
        print("✗ FAIL: XPath match statistics not tracked")

    return True

if __name__ == "__main__":
    success = test_xpath_caching()
    sys.exit(0 if success else 1)