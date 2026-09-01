#!/usr/bin/env python3
"""
test_zip_parsing.py - Test ZIP code parsing logic for splitting into zip_code and zip4 components
"""

def test_zip_parsing(zip_code):
    """Test the ZIP code splitting logic used in parse_990.py, parse_990ez.py, parse_990pf.py"""
    print(f"Testing ZIP code: '{zip_code}'")

    # Current logic from the parsing files
    zip5 = None
    zip4 = None
    if zip_code:
        stripped = zip_code.strip()
        print(f"Stripped: '{stripped}'")
        if len(stripped) >= 5:
            zip5 = stripped[:5]
            print(f"zip5 (first 5 chars): '{zip5}'")
            if len(stripped) >= 9:
                zip4 = stripped[5:9]
                print(f"zip4 (chars 5-9): '{zip4}'")
            else:
                print("Not enough characters for zip4")
        else:
            print("Not enough characters for zip5")

    print(f"Result: zip_code='{zip5}', zip4='{zip4}'")
    return zip5, zip4

def test_corrected_zip_parsing(zip_code):
    """Test corrected ZIP code splitting logic that strips non-digits first"""
    print(f"\nTesting corrected ZIP code: '{zip_code}'")

    zip5 = None
    zip4 = None
    if zip_code:
        # Strip non-digits first
        digits_only = ''.join(c for c in zip_code if c.isdigit())
        print(f"Digits only: '{digits_only}'")
        if len(digits_only) >= 5:
            zip5 = digits_only[:5]
            print(f"zip5 (first 5 digits): '{zip5}'")
            if len(digits_only) >= 9:
                zip4 = digits_only[5:9]
                print(f"zip4 (digits 6-9): '{zip4}'")
            else:
                print("Not enough digits for zip4")
        else:
            print("Not enough digits for zip5")

    print(f"Corrected result: zip_code='{zip5}', zip4='{zip4}'")
    return zip5, zip4

if __name__ == "__main__":
    # Test cases
    test_cases = [
        "12345-6789",  # Formatted ZIP with dash
        "123456789",   # 9-digit ZIP without dash
        "12345",       # 5-digit ZIP
        "12345-67",    # Invalid format (only 7 digits total)
        "12345-67890", # Invalid format (10 digits)
        "  12345-6789  ", # With whitespace
        "ABC12345-6789DEF", # With letters
    ]

    print("Testing current ZIP parsing logic:")
    print("=" * 50)

    for test_zip in test_cases:
        test_zip_parsing(test_zip)
        print()

    print("\nTesting corrected ZIP parsing logic:")
    print("=" * 50)

    for test_zip in test_cases:
        test_corrected_zip_parsing(test_zip)
        print()