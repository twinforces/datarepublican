#!/usr/bin/env python3
"""
test_990processor.py - Unit tests for the 990processor module

Tests the core functionality using the test_xmls directory.
"""

import unittest
import tempfile
import os
import shutil
from pathlib import Path
import zipfile
import sqlite3
import sys
import importlib.util

# Import the 990processor module
spec = importlib.util.spec_from_file_location("nine_nine_zero_processor", "990processor.py")
processor_module = importlib.util.module_from_spec(spec)
sys.modules["nine_nine_zero_processor"] = processor_module
spec.loader.exec_module(processor_module)

IRS990Processor = processor_module.IRS990Processor
ZipFile = processor_module.ZipFile
XMLFile = processor_module.XMLFile
Charity = processor_module.Charity
Address = processor_module.Address


class TestIRS990Processor(unittest.TestCase):
    """Test cases for IRS990Processor"""

    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.zips_dir = os.path.join(self.temp_dir, "zips")
        self.out_dir = os.path.join(self.temp_dir, "out")
        self.anal_dir = os.path.join(self.temp_dir, "anal")
        self.final_dir = os.path.join(self.temp_dir, "final")

        # Create directories
        for dir_path in [self.zips_dir, self.out_dir, self.anal_dir, self.final_dir]:
            os.makedirs(dir_path, exist_ok=True)

        # Initialize processor
        self.processor = IRS990Processor(
            db_path=self.db_path,
            zips_dir=self.zips_dir,
            out_dir=self.out_dir,
            anal_dir=self.anal_dir,
            final_dir=self.final_dir,
            verbose=True
        )

    def tearDown(self):
        """Clean up test environment"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_database_initialization(self):
        """Test that database is properly initialized"""
        # Check that tables exist
        self.processor.db_cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table'
        """)
        tables = [row[0] for row in self.processor.db_cursor.fetchall()]

        expected_tables = [
            'Charities', 'Grants', 'Contributions', 'Addresses', 'Geocoding',
            'ZipFiles', 'XmlFiles', 'Backfill', 'PipelineProgress',
            'Officers', 'Contractors', 'PoliticalContributions'
        ]

        for table in expected_tables:
            self.assertIn(table, tables, f"Table {table} not found in database")

    def test_zip_file_registration(self):
        """Test ZIP file registration"""
        # Create a dummy ZIP file
        zip_path = Path(self.zips_dir) / "2019.zip"
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("test.xml", "<xml>test</xml>")

        zip_file = ZipFile(
            filename="2019.zip",
            file_path=str(zip_path),
            tax_year=2019,
            file_size=zip_path.stat().st_size
        )

        zip_id = self.processor.insert_zip_file(zip_file)

        # Verify insertion
        self.processor.db_cursor.execute("SELECT * FROM ZipFiles WHERE zip_id = ?", (zip_id,))
        result = self.processor.db_cursor.fetchone()

        self.assertIsNotNone(result)
        self.assertEqual(result[1], "2019.zip")  # filename
        self.assertEqual(result[3], 2019)  # tax_year

    def test_xml_file_registration(self):
        """Test XML file registration"""
        # First create a ZIP file
        zip_file = ZipFile(filename="2019.zip", file_path="/fake/path/2019.zip", tax_year=2019)
        zip_id = self.processor.insert_zip_file(zip_file)

        xml_file = XMLFile(
            zip_id=zip_id,
            filename="test.xml",
            internal_path="test.xml",
            ein="123456789",
            tax_year=2019,
            form_type="990",
            processed=True
        )

        xml_id = self.processor.insert_xml_file(xml_file)

        # Verify insertion
        self.processor.db_cursor.execute("SELECT * FROM XmlFiles WHERE xml_id = ?", (xml_id,))
        result = self.processor.db_cursor.fetchone()

        self.assertIsNotNone(result)
        self.assertEqual(result[1], zip_id)  # zip_id
        self.assertEqual(result[2], "test.xml")  # filename
        self.assertEqual(result[4], "123456789")  # ein

    def test_address_deduplication(self):
        """Test address deduplication"""
        address1 = Address(
            ein="123456789",
            name="Test Charity",
            canonical_address="520 Eighth Avenue 7Th Floor New York Ny 10018",
            zip_code="10018",
            address_type="filer"
        )

        address2 = Address(
            ein="123456789",
            name="Test Charity",
            canonical_address="520 Eighth Avenue 7Th Floor New York Ny 10018",  # Same address
            zip_code="10018",
            address_type="filer"
        )

        id1 = self.processor.insert_address(address1)
        id2 = self.processor.insert_address(address2)

        # Should return the same ID for duplicate addresses
        self.assertEqual(id1, id2)

    def test_charity_insertion(self):
        """Test charity data insertion"""
        charity = Charity(
            ein="123456789",
            tax_year=2019,
            filer_name="Test Charity",
            receipt_amt=100000.0,
            total_exp=80000.0,
            org_type="501(c)(3)",
            form_type="990",
            xml_name="test.xml"
        )

        charity_id = self.processor.insert_charity(charity)

        # Verify insertion
        self.processor.db_cursor.execute("SELECT * FROM Charities WHERE charity_id = ?", (charity_id,))
        result = self.processor.db_cursor.fetchone()

        self.assertIsNotNone(result)
        self.assertEqual(result[1], "123456789")  # ein
        self.assertEqual(result[2], 2019)  # tax_year
        self.assertEqual(result[3], "Test Charity")  # filer_name

    def test_percentile_calculation(self):
        """Test percentile calculation"""
        # Insert test data
        charities = [
            Charity(ein="111111111", tax_year=2019, filer_name="Charity A", org_type="501(c)(3)",
                   comp_pct=1.0, total_exp=100000, denominator=200000),
            Charity(ein="222222222", tax_year=2019, filer_name="Charity B", org_type="501(c)(3)",
                   comp_pct=2.0, total_exp=100000, denominator=200000),
            Charity(ein="333333333", tax_year=2019, filer_name="Charity C", org_type="501(c)(3)",
                   comp_pct=3.0, total_exp=100000, denominator=200000),
        ]

        for charity in charities:
            self.processor.insert_charity(charity)

        # Calculate percentiles
        self.processor.calculate_percentiles()

        # Check results
        self.processor.db_cursor.execute("""
            SELECT ein, comp_ptile_value FROM Charities
            WHERE tax_year = 2019 AND org_type = '501(c)(3)'
            ORDER BY ein
        """)
        results = self.processor.db_cursor.fetchall()

        # Should have percentiles assigned
        for ein, ptile in results:
            self.assertIsNotNone(ptile)
            self.assertGreaterEqual(ptile, 0.0)
            self.assertLessEqual(ptile, 100.0)


if __name__ == '__main__':
    unittest.main()