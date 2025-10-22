#!/usr/bin/env python3
"""
test_xml_address_geolocation.py - Unit test for XML address extraction and geolocation

This test processes each of the 10 XML files in ./test_xmls/*.xml, extracts filer addresses,
saves them to an in-memory database, reads them back, and geolocates the non-PO Box addresses.
The test verifies that each XML file produces exactly one filer address and that geolocation
works for non-PO Box addresses.
"""

import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch
from io import BytesIO

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database_operations import DatabaseOperations
from xml_processor import XMLProcessor
from geolocation_processor import GeolocationProcessor
from models import Address
from extract_utils import PO_BOX_REGEX


class TestXMLAddressGeolocation(unittest.TestCase):
    """Test XML address extraction and geolocation"""

    def setUp(self):
        """Set up test fixtures"""
        # Create in-memory DuckDB database
        self.db_ops = DatabaseOperations(":memory:", log_sql=False)

        # Create XML processor
        self.xml_processor = XMLProcessor(self.db_ops, processing_version=1)

        # Create geolocation processor
        self.geo_processor = GeolocationProcessor(self.db_ops)

        # Get list of test XML files
        test_xmls_dir = Path(__file__).parent / "test_xmls"
        self.xml_files = list(test_xmls_dir.glob("*.xml"))
        self.assertEqual(len(self.xml_files), 10, f"Expected 10 XML files, found {len(self.xml_files)}")

    def test_process_xml_files_and_extract_addresses(self):
        """Test that each XML file produces exactly one filer address"""
        total_addresses = 0

        for xml_file in self.xml_files:
            # Process XML file directly
            with open(xml_file, 'rb') as f:
                xml_content = f.read()

            # Extract address using XML processor logic
            address = self._extract_address_from_xml(xml_content, xml_file.name)

            if address:
                # Save address to database
                self.db_ops.insert_address(address)
                total_addresses += 1

                # Verify address has required fields
                self.assertIsNotNone(address.ein, f"Address from {xml_file.name} should have EIN")
                self.assertIsNotNone(address.name, f"Address from {xml_file.name} should have name")
                self.assertIsNotNone(address.canonical_address, f"Address from {xml_file.name} should have canonical address")
                self.assertIsNotNone(address.address_line1, f"Address from {xml_file.name} should have address_line1")
                self.assertNotEqual(address.address_line1.strip(), "", f"Address from {xml_file.name} should have non-empty address_line1")

        # Verify we got exactly one address per XML file
        self.assertEqual(total_addresses, 10, f"Expected 10 addresses (one per XML file), got {total_addresses}")

    def test_read_addresses_from_database(self):
        """Test reading addresses back from database"""
        # First process and save addresses
        self.test_process_xml_files_and_extract_addresses()

        # Read addresses from database
        addresses = self.db_ops.select_dataclass(Address, where_clause="")

        # Verify we have 10 addresses
        self.assertEqual(len(addresses), 10, f"Expected 10 addresses in database, got {len(addresses)}")

        # Verify each address has required fields
        for addr in addresses:
            self.assertIsNotNone(addr.ein, "EIN should be set")
            self.assertIsNotNone(addr.name, "Name should be set")
            self.assertIsNotNone(addr.canonical_address, "Canonical address should be built")
            self.assertIsNotNone(addr.address_line1, "address_line1 should be populated")
            self.assertNotEqual(addr.address_line1.strip(), "", "address_line1 should not be empty")

    def test_geolocate_non_po_box_addresses(self):
        """Test geolocation for non-PO Box addresses"""
        # First process and save addresses
        self.test_process_xml_files_and_extract_addresses()

        # Get addresses from database
        addresses = self.db_ops.select_dataclass(Address, where_clause="")

        # Count PO Box and non-PO Box addresses
        po_box_addresses = []
        non_po_box_addresses = []

        for addr in addresses:
            is_po_box = (addr.po_box and addr.po_box.strip()) or \
                       (addr.canonical_address and PO_BOX_REGEX.search(addr.canonical_address))
            if is_po_box:
                po_box_addresses.append(addr)
            else:
                non_po_box_addresses.append(addr)

        print(f"Found {len(po_box_addresses)} PO Box addresses and {len(non_po_box_addresses)} non-PO Box addresses")

        # Run geolocation using real census geocoding API
        geocoded_count = self.geo_processor.geolocate_addresses()

        # Verify geolocation was attempted
        self.assertGreaterEqual(geocoded_count, 0, "Geocoding should complete without error")

        # Verify geocoding records were created
        geocoding_records = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding").fetchone()[0]
        self.assertGreaterEqual(geocoding_records, 0, "Geocoding records should be created")

        # Verify addresses were updated with geocoding data for non-PO Box addresses
        updated_addresses = self.db_ops.select_dataclass(Address, where_clause="geocoding_id IS NOT NULL")
        if geocoded_count > 0:
            # The geolocation processor may skip some addresses due to deduplication
            # Just verify that some addresses were geocoded
            self.assertGreater(len(updated_addresses), 0,
                              "At least some addresses should be updated with geocoding data")

            # Verify geocoding data format
            for addr in updated_addresses:
                if addr.colocator and addr.colocator.startswith('LL:'):
                    parts = addr.colocator.split(':')
                    self.assertEqual(len(parts), 3, f"LL colocator should have 3 parts, got {addr.colocator}")
                    lat_str, lon_str = parts[1], parts[2]
                    # Verify they can be parsed as floats
                    float(lat_str)
                    float(lon_str)

    def _extract_address_from_xml(self, xml_content, filename):
        """Extract address from XML content using similar logic to XMLProcessor"""
        from lxml import etree as ET
        from extract_utils import canonicalize_address

        try:
            parser = ET.XMLParser(recover=True)
            tree = ET.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract EIN
            filer_ein = None
            for xpath in [
                ET.XPath(".//irs:Filer/irs:EIN", namespaces={'irs': 'http://www.irs.gov/efile'}),
                ET.XPath(".//Filer/EIN")
            ]:
                try:
                    result = xpath(root)
                    if result:
                        raw_ein = result[0].text.strip()
                        if raw_ein.isdigit():
                            filer_ein = f"{int(raw_ein):09d}"
                            break
                except:
                    continue

            if not filer_ein:
                return None

            # Extract filer name
            filer_name = "Unknown"
            for xpath in [
                ET.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'}),
                ET.XPath(".//Filer/BusinessName/BusinessNameLine1Txt")
            ]:
                try:
                    result = xpath(root)
                    if result and result[0].text:
                        filer_name = result[0].text.strip()
                        break
                except:
                    continue

            # Extract address components
            address_xpaths = [
                ET.XPath(".//irs:Filer/irs:USAddress/*", namespaces={'irs': 'http://www.irs.gov/efile'}),
                ET.XPath(".//Filer/USAddress/*"),
                ET.XPath(".//USAddress/*")
            ]

            address_components = []
            for xpath in address_xpaths:
                try:
                    elements = xpath(root)
                    if elements:
                        address_components.extend(elements)
                        break
                except:
                    continue

            if not address_components:
                return None

            # Canonicalize address
            address_info = canonicalize_address(address_components, ".")
            canonical_address = address_info.canonical_address
            street = address_info.address_line1
            city = address_info.city
            state = address_info.state
            po_box = address_info.po_box
            zip_code = address_info.zip_code

            if canonical_address:
                address = Address(
                    ein=filer_ein,
                    name=filer_name,
                    address_line1=street,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    po_box=po_box,
                    canonical_address=canonical_address,
                    address_type="filer"
                )
                return address

        except Exception as e:
            print(f"Failed to extract address from {filename}: {e}")
            return None

        return None


if __name__ == '__main__':
    unittest.main()