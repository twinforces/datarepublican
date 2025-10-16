#!/usr/bin/env python3
"""
test_float_parsing.py - Test float parsing functionality with various formats
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from parse_utils import parse_float_field

def test_float_parsing():
    """Test parse_float_field with various formats"""

    test_cases = [
        # (input, expected_output, description)
        ("39,051", 39051.0, "comma-separated number"),
        ("920,", 920.0, "trailing comma"),
        ("$1,234.56", 1234.56, "dollar sign with comma and decimal"),
        ("$500", 500.0, "dollar sign only"),
        ("123.45", 123.45, "decimal number"),
        ("0", 0.0, "zero"),
        ("", 0.0, "empty string"),
        (None, 0.0, "None value"),
        ("invalid", 0.0, "invalid text"),
        ("1,234,567.89", 1234567.89, "large number with commas"),
        ("$0.00", 0.0, "zero with dollar"),
        ("-123.45", -123.45, "negative number"),
        ("$ -1,234", -1234.0, "negative with dollar and comma"),
    ]

    print("Testing parse_float_field function:")
    print("=" * 50)

    passed = 0
    failed = 0

    for input_val, expected, description in test_cases:
        try:
            result = parse_float_field(input_val)
            if abs(result - expected) < 0.001:  # Use small epsilon for float comparison
                print(f"✓ PASS: {description} - '{input_val}' -> {result}")
                passed += 1
            else:
                print(f"✗ FAIL: {description} - '{input_val}' -> {result} (expected {expected})")
                failed += 1
        except Exception as e:
            print(f"✗ ERROR: {description} - '{input_val}' -> Exception: {e}")
            failed += 1

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")

    return failed == 0

if __name__ == "__main__":
    success = test_float_parsing()
    sys.exit(0 if success else 1)