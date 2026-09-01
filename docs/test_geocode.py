#!/usr/bin/env python3
"""
test_geocode.py - Simple script to test geocoding of a single address

Usage: python test_geocode.py "100 N Main St, Winston Salem, NC, 27101"
"""

import sys
import json
import re
from datetime import datetime

try:
    import censusgeocode as cg
except ImportError:
    print("Error: censusgeocode library not available. Install with: pip install censusgeocode")
    sys.exit(1)

# Add the 990tools directory to path so we can import modules
sys.path.insert(0, '990tools')

from models.address import Address
from models.geocoding import Geocoding


def parse_address_string(address_str):
    """
    Parse a comma-separated address string into components.
    Expected format: "street, city, state, zip"
    """
    parts = [part.strip() for part in address_str.split(',')]

    if len(parts) != 4:
        print(f"Error: Expected 4 comma-separated parts, got {len(parts)}")
        print("Format: 'street, city, state, zip'")
        sys.exit(1)

    street, city, state, zip_code = parts

    # Create Address object
    address = Address(
        address_line1=street,
        city=city,
        state=state,
        zip_code=zip_code
    )

    return address


def main():
    if len(sys.argv) != 2:
        print("Usage: python test_geocode.py 'street, city, state, zip'")
        print("Example: python test_geocode.py '100 N Main St, Winston Salem, NC, 27101'")
        sys.exit(1)

    address_str = sys.argv[1]
    print(f"Testing geocoding for address: {address_str}")

    # Parse the address
    address = parse_address_string(address_str)

    # Canonicalize the address
    address.prep_for_insert()

    print(f"Canonical address: {address.canonical_address}")

    # Create geocoding record
    geocoding = address.create_geocoding()

    print(f"Normalized address for API: {geocoding.normalized_address}")

    # Parse the normalized address for API call
    try:
        api_record = json.loads(geocoding.normalized_address)
    except json.JSONDecodeError as e:
        print(f"Error parsing normalized address: {e}")
        sys.exit(1)

    # Remove the 'id' field that censusgeocode doesn't expect
    api_record.pop('id', None)

    print(f"API record: {api_record}")

    # Make the API call
    try:
        print("Calling census geocoding API...")
        api_results = cg.addressbatch([api_record])

        if not api_results:
            print("No results returned from API")
            return

        result = api_results[0]
        print(f"API result: {result}")

        # Check if we got a match
        match = result.get('match', False)
        lat = result.get('lat')
        lon = result.get('lon')
        matchtype = result.get('matchtype', '')

        if match and lat is not None and lon is not None:
            print("✓ Match found!")
            print(f"Latitude: {lat}")
            print(f"Longitude: {lon}")
            print(f"Match type: {matchtype}")

            # Show matched address if available
            matched_address = f"{result.get('address', '')}, {result.get('city', '')}, {result.get('state', '')} {result.get('zip', '')}".strip()
            if matched_address and matched_address != ',':
                print(f"Matched address: {matched_address}")
        else:
            print("✗ No match found")
            print("This could be due to:")
            print("- Invalid address format")
            print("- Address not found in census database")
            print("- API rate limiting or service issues")

    except Exception as e:
        print(f"Error calling geocoding API: {e}")
        print("Make sure you have a stable internet connection and the censusgeocode library is properly installed.")


if __name__ == "__main__":
    main()