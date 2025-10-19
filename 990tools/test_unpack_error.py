#!/usr/bin/env python3
"""
test_unpack_error.py - Test script to debug the "too many values to unpack (expected 7)" error

This script parses sample XML files and attempts to insert them into a test database
to reproduce and debug the unpacking error.
"""

import os
import sys
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path
from lxml import etree as ET

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(__file__))

from xml_processor import XMLProcessor
from database_operations import DatabaseOperations
from irs990processorDC import Charity, Officer, Grant, Contractor, PoliticalContribution

def create_test_database(db_path):
    """Create a test database with the required schema"""
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Read and execute schema.sql
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    cursor.executescript(schema_sql)
    conn.commit()
    conn.close()

    print(f"Created test database: {db_path}")

def test_xml_parsing():
    """Test parsing sample XML files"""
    test_db = "test_unpack_error.db"
    sample_xml_dir = "test_xmls"

    # Create test database
    create_test_database(test_db)

    # Initialize components
    db_ops = DatabaseOperations(test_db)
    xml_processor = XMLProcessor(db_ops, processing_version=2)

    # Get sample XML files
    xml_files = []
    for xml_file in os.listdir(sample_xml_dir):
        if xml_file.endswith('.xml'):
            xml_files.append(xml_file)

    print(f"Found {len(xml_files)} sample XML files")

    # Process each XML file
    for xml_filename in xml_files[:3]:  # Test with first 3 files
        xml_path = os.path.join(sample_xml_dir, xml_filename)
        print(f"\nProcessing {xml_filename}...")

        try:
            # Read XML content
            with open(xml_path, 'rb') as f:
                xml_content = f.read()

            # Parse XML
            parser = ET.XMLParser(recover=True)
            tree = ET.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract basic metadata
            form_type = xml_processor._extract_form_type(root)
            tax_year = xml_processor._extract_tax_year(root)
            filer_ein = xml_processor._extract_filer_ein(root)

            print(f"  Form type: {form_type}, Tax year: {tax_year}, EIN: {filer_ein}")

            if not filer_ein or filer_ein == "Unknown":
                print(f"  Skipping {xml_filename}: invalid EIN {filer_ein}")
                continue

            # Extract data based on form type
            if form_type == "990":
                charity, officers, grants, contractors, contributions = xml_processor._parse_990_data(root, xml_filename, filer_ein, tax_year, form_type)
            elif form_type == "990EZ":
                charity, officers, grants, contractors, contributions = xml_processor._parse_990ez_data(root, xml_filename, filer_ein, tax_year, form_type)
            elif form_type == "990PF":
                charity, officers, grants, contractors, contributions = xml_processor._parse_990pf_data(root, xml_filename, filer_ein, tax_year, form_type)
            else:
                print(f"  Unsupported form type {form_type} in {xml_filename}")
                continue

            print(f"  Parsed charity: {charity.ein if charity else None}")
            print(f"  Officers: {len(officers)}")
            print(f"  Grants: {len(grants)}")
            print(f"  Contractors: {len(contractors)}")
            print(f"  Contributions: {len(contributions)}")

            # Try to insert into database
            if charity:
                print("  Inserting into database...")
                db_ops.insert_charity(charity)
                for officer in officers:
                    officer.charity_id = charity.charity_id
                    db_ops.insert_officer(officer)
                for grant in grants:
                    db_ops.insert_grant(grant)
                for contractor in contractors:
                    db_ops.insert_contractor(contractor)
                for contribution in contributions:
                    db_ops.insert_political_contribution(contribution)
                print("  Successfully inserted data")

        except Exception as e:
            print(f"  ERROR processing {xml_filename}: {e}")
            import traceback
            traceback.print_exc()

    # Clean up
    if hasattr(db_ops, 'db_conn'):
        db_ops.db_conn.close()
    if os.path.exists(test_db):
        os.remove(test_db)
        print(f"\nCleaned up test database: {test_db}")

if __name__ == "__main__":
    test_xml_parsing()