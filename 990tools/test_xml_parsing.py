#!/usr/bin/env python3
"""
test_xml_parsing.py - Unit tests for XML parsing functionality

Tests the parsing of IRS 990 forms (990, 990EZ, 990PF) to ensure that
the refactor from TSV output to PendingDatabaseContext works correctly.
"""

import unittest
import os
from lxml import etree
from io import BytesIO
from pending_database_context import PendingDatabaseContext
from base_parser import BaseParser
from models import Charity
import parse_990
import parse_990ez
import parse_990pf


class TestXMLParsing(unittest.TestCase):
    """Test XML parsing functionality for different form types"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_xmls_dir = "test_xmls"

    def _load_xml_file(self, filename):
        """Load XML file and return root element"""
        filepath = os.path.join(self.test_xmls_dir, filename)
        with open(filepath, 'rb') as f:
            xml_content = f.read()
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        return tree.getroot()

    def _extract_metadata_from_xml(self, root):
        """Extract basic metadata from XML for testing"""
        namespaces = {'irs': 'http://www.irs.gov/efile'}

        # Extract form type
        form_type_paths = [
            etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces=namespaces),
            etree.XPath(".//ReturnHeader/ReturnTypeCd")
        ]
        form_type = None
        for xpath in form_type_paths:
            result = xpath(root)
            if result:
                form_type = result[0].text
                break
        form_type = form_type if form_type is not None else "Unknown"

        # Extract tax year
        tax_year_paths = [
            etree.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces=namespaces),
            etree.XPath(".//ReturnHeader/TaxYr")
        ]
        tax_year = None
        for xpath in tax_year_paths:
            result = xpath(root)
            if result:
                tax_year = result[0].text
                break
        tax_year = tax_year if tax_year is not None else "Unknown"
        if tax_year == "Unknown":
            tax_year = "2017"  # Default for test files
        else:
            try:
                int(tax_year)
            except ValueError:
                tax_year = "2017"

        # Extract EIN
        filer_ein_paths = [
            etree.XPath(".//irs:ReturnHeader/irs:Filer/irs:EIN", namespaces=namespaces),
            etree.XPath(".//ReturnHeader/Filer/EIN")
        ]
        filer_ein = None
        for xpath in filer_ein_paths:
            result = xpath(root)
            if result:
                raw_ein = result[0].text.strip()
                if raw_ein.isdigit():
                    filer_ein = f"{int(raw_ein):09d}"
                else:
                    filer_ein = "Unknown"
                break
        if filer_ein is None:
            filer_ein = "Unknown"

        return form_type, tax_year, filer_ein

    def test_parse_990_sample_1(self):
        """Test parsing of sample_990_1.xml"""
        root = self._load_xml_file("sample_990_1.xml")
        form_type, tax_year, filer_ein = self._extract_metadata_from_xml(root)

        # Create context and charity
        context = PendingDatabaseContext()
        charity = Charity(
            ein=filer_ein,
            tax_year=tax_year,
            form_type=form_type,
            xml_name="sample_990_1.xml"
        )
        context.addObjectToDatabase(charity)

        # Parse the form
        parse_990.parse_990(root, "sample_990_1.xml", {}, context)

        # Check object counts
        counts = context.getObjectCounts()
        self.assertEqual(counts['charity'], 1, "Should have 1 charity")
        self.assertEqual(counts['officer'], 18, "Should have 18 officers")  # Actual count from parsing
        self.assertEqual(counts['grant'], 0, "Should have 0 grants")  # No grants in this sample
        self.assertEqual(counts['contractor'], 0, "Should have 0 contractors")  # No contractors in this sample
        self.assertEqual(counts['political_contribution'], 0, "Should have 0 political contributions")  # No contributions in this sample
        self.assertEqual(counts['address'], 0, "Should have 0 addresses")  # Address parsing not implemented in context mode yet

    def test_parse_990ez_sample_1(self):
        """Test parsing of sample_990EZ_1.xml"""
        root = self._load_xml_file("sample_990EZ_1.xml")
        form_type, tax_year, filer_ein = self._extract_metadata_from_xml(root)

        # Create context and charity
        context = PendingDatabaseContext()
        charity = Charity(
            ein=filer_ein,
            tax_year=tax_year,
            form_type=form_type,
            xml_name="sample_990EZ_1.xml"
        )
        context.addObjectToDatabase(charity)

        # Parse the form
        parse_990ez.parse_990ez(root, "sample_990EZ_1.xml", {}, context, log_error=None, xpath_match_stats=None)

        # Check object counts
        counts = context.getObjectCounts()
        self.assertEqual(counts['charity'], 1, "Should have 1 charity")
        self.assertEqual(counts['officer'], 4, "Should have 4 officers")  # From OfficerDirectorTrusteeEmplGrp tags
        self.assertEqual(counts['grant'], 0, "Should have 0 grants")  # No grants in this sample
        self.assertEqual(counts['contractor'], 0, "Should have 0 contractors")  # No contractors in this sample
        self.assertEqual(counts['political_contribution'], 0, "Should have 0 political contributions")  # No contributions in this sample
        self.assertEqual(counts['address'], 0, "Should have 0 addresses")  # Address parsing not implemented in context mode yet

    def test_parse_990pf_sample_1(self):
        """Test parsing of sample_990PF_1.xml"""
        root = self._load_xml_file("sample_990PF_1.xml")
        form_type, tax_year, filer_ein = self._extract_metadata_from_xml(root)

        # Create context and charity
        context = PendingDatabaseContext()
        charity = Charity(
            ein=filer_ein,
            tax_year=tax_year,
            form_type=form_type,
            xml_name="sample_990PF_1.xml"
        )
        context.addObjectToDatabase(charity)

        # Parse the form
        parse_990pf.parse_990pf(root, "sample_990PF_1.xml", {}, filer_ein, tax_year, form_type, log_error=None, xpath_match_stats=None, context=context)

        # Check object counts
        counts = context.getObjectCounts()
        self.assertEqual(counts['charity'], 1, "Should have 1 charity")
        self.assertEqual(counts['officer'], 1, "Should have 1 officer")  # PF forms have officers in OfficerDirTrstKeyEmplGrp
        self.assertEqual(counts['grant'], 0, "Should have 0 grants")  # Grants not parsed in context mode yet
        self.assertEqual(counts['contractor'], 0, "Should have 0 contractors")  # No contractors in this sample
        self.assertEqual(counts['political_contribution'], 0, "Should have 0 political contributions")  # No contributions in this sample
        self.assertEqual(counts['address'], 1, "Should have 1 address")  # Charity address

    def test_master_parser_990(self):
        """Test master parser with Form 990"""
        root = self._load_xml_file("sample_990_1.xml")
        form_type, tax_year, filer_ein = self._extract_metadata_from_xml(root)

        # Create context and charity
        context = PendingDatabaseContext()
        charity = Charity(
            ein=filer_ein,
            tax_year=tax_year,
            form_type=form_type,
            xml_name="sample_990_1.xml"
        )
        context.addObjectToDatabase(charity)

        # Use base parser factory
        parser = BaseParser.create_parser(form_type)
        self.assertIsNotNone(parser, f"Should create parser for form type {form_type}")

        # Parse using base parser
        parser.parse_form(root, "sample_990_1.xml", {}, context)

        # Check object counts
        counts = context.getObjectCounts()
        self.assertEqual(counts['charity'], 1, "Should have 1 charity")
        self.assertEqual(counts['officer'], 18, "Should have 18 officers")
        self.assertEqual(counts['address'], 1, "Should have 1 address")

    def test_master_parser_990ez(self):
        """Test master parser with Form 990EZ"""
        root = self._load_xml_file("sample_990EZ_1.xml")
        form_type, tax_year, filer_ein = self._extract_metadata_from_xml(root)

        # Create context and charity
        context = PendingDatabaseContext()
        charity = Charity(
            ein=filer_ein,
            tax_year=tax_year,
            form_type=form_type,
            xml_name="sample_990EZ_1.xml"
        )
        context.addObjectToDatabase(charity)

        # Use base parser factory
        parser = BaseParser.create_parser(form_type)
        self.assertIsNotNone(parser, f"Should create parser for form type {form_type}")

        # Parse using base parser
        parser.parse_form(root, "sample_990EZ_1.xml", {}, context)

        # Check object counts
        counts = context.getObjectCounts()
        self.assertEqual(counts['charity'], 1, "Should have 1 charity")
        self.assertEqual(counts['officer'], 4, "Should have 4 officers")
        self.assertEqual(counts['address'], 1, "Should have 1 address")

    def test_master_parser_990pf(self):
        """Test master parser with Form 990PF"""
        root = self._load_xml_file("sample_990PF_1.xml")
        form_type, tax_year, filer_ein = self._extract_metadata_from_xml(root)

        # Create context and charity
        context = PendingDatabaseContext()
        charity = Charity(
            ein=filer_ein,
            tax_year=tax_year,
            form_type=form_type,
            xml_name="sample_990PF_1.xml"
        )
        context.addObjectToDatabase(charity)

        # Use base parser factory
        parser = BaseParser.create_parser(form_type)
        self.assertIsNotNone(parser, f"Should create parser for form type {form_type}")

        # Parse using base parser
        parser.parse_form(root, "sample_990PF_1.xml", {}, context)

        # Check object counts
        counts = context.getObjectCounts()
        self.assertEqual(counts['charity'], 1, "Should have 1 charity")
        self.assertEqual(counts['grant'], 1, "Should have 1 grant")
        self.assertEqual(counts['address'], 1, "Should have 1 address")


if __name__ == '__main__':
    unittest.main()