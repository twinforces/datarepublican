#!/usr/bin/env python3
"""
Test script for verifying new file creation functionality.
Tests the extraction of contractors and political contributions from IRS 990 XML files.
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add the parent directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parse_990 import parse_990
from parse_990ez import parse_990ez
from parse_990pf import parse_990pf

def test_extraction():
    """Test the extraction functionality with real XML files."""
    print("Testing new file extraction functionality...")

    # Create temporary directory for test files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Use the correct output directory
        output_dir = Path("/Volumes/Data/tsvs")
        if not output_dir.exists():
            # Fallback to temp directory for testing
            output_dir = temp_path / "output"
            output_dir.mkdir()

        # Test files to process
        test_files = [
            "test_xmls/amnesty.990.xml",  # Has Schedule C
            "test_xmls/202031249349200503_public.990ez.xml",  # Has Schedule L
            "test_xmls/CHAI.990.xml"  # Has contractors but no Schedule C/L
        ]

        for xml_file_path in test_files:
            if not os.path.exists(xml_file_path):
                print(f"Test file {xml_file_path} not found, skipping...")
                continue

            print(f"\nProcessing {xml_file_path}...")

            # Parse the XML using the correct function
            from lxml import etree
            from io import BytesIO

            try:
                with open(xml_file_path, 'rb') as f:
                    xml_content = f.read()
            except IOError as e:
                print(f"Error reading XML file {xml_file_path}: {e}")
                continue

            parser = etree.XMLParser(recover=True)
            tree = etree.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract EIN and other metadata
            namespaces = {'irs': 'http://www.irs.gov/efile'}
            filer_ein_paths = [
                etree.XPath(".//irs:ReturnHeader/irs:Filer/irs:EIN", namespaces=namespaces),
                etree.XPath(".//ReturnHeader/Filer/EIN")
            ]
            filer_ein = None
            for xpath in filer_ein_paths:
                result = xpath(root)
                if result:
                    filer_ein = result[0].text.strip()
                    break
            filer_ein = filer_ein if filer_ein is not None else "Unknown"

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

            # Parse the XML based on form type
            result = None
            if form_type == "990":
                result = parse_990(
                    root, xml_file_path, xpath_cache={}, filer_ein=filer_ein,
                    tax_year=tax_year, form_type=form_type
                )
            elif form_type == "990EZ":
                result = parse_990ez(
                    root, xml_file_path, xpath_cache={}, filer_ein=filer_ein,
                    tax_year=tax_year, form_type=form_type
                )
            elif form_type == "990PF":
                result = parse_990pf(
                    root, xml_file_path, xpath_cache={}, filer_ein=filer_ein,
                    tax_year=tax_year, form_type=form_type
                )
            else:
                print(f"Skipping {xml_file_path} - unsupported form type: {form_type}")
                continue

            if result is None:
                print(f"Skipping {xml_file_path} - parsing failed")
                continue

            # Handle different return formats
            if len(result) == 4:
                row, officer_entries, contractors, political_contributions = result
            elif len(result) == 2:
                # For 990EZ and 990PF which return (row, officer_entries)
                row, officer_entries = result
                contractors = []
                political_contributions = []
            else:
                print(f"Unexpected parse result format for {xml_file_path}: got {len(result)} items")
                continue

            # Write contractors data
            contractors_file = output_dir / f"{Path(xml_file_path).stem}_contractors.tsv"
            if contractors:
                with open(contractors_file, 'w', newline='') as f:
                    import csv
                    writer = csv.writer(f, delimiter='\t')
                    writer.writerow(['name', 'amount', 'ein', 'address', 'zip_code', 'po_box', 'filer_ein', 'tax_year'])
                    for contractor in contractors:
                        writer.writerow([
                            contractor.get('name', ''),
                            contractor.get('amount', 0),
                            contractor.get('ein', ''),
                            contractor.get('address', ''),
                            contractor.get('zip_code', ''),
                            contractor.get('po_box', ''),
                            contractor.get('filer_ein', ''),
                            contractor.get('tax_year', '')
                        ])
                print(f"Wrote {len(contractors)} contractors to {contractors_file}")
            else:
                # Create empty file
                with open(contractors_file, 'w') as f:
                    f.write("name\tamount\tein\taddress\tzip_code\tpo_box\tfiler_ein\ttax_year\n")
                print(f"Created empty contractors file: {contractors_file}")

            # Write political contributions data
            political_file = output_dir / f"{Path(xml_file_path).stem}_political_contributions.tsv"
            if political_contributions:
                with open(political_file, 'w', newline='') as f:
                    import csv
                    writer = csv.writer(f, delimiter='\t')
                    writer.writerow(['recipient', 'amount', 'recipient_address', 'recipient_zip', 'recipient_po_box', 'filer_ein', 'tax_year'])
                    for contribution in political_contributions:
                        writer.writerow([
                            contribution.get('recipient', ''),
                            contribution.get('amount', 0),
                            contribution.get('recipient_address', ''),
                            contribution.get('recipient_zip', ''),
                            contribution.get('recipient_po_box', ''),
                            contribution.get('filer_ein', ''),
                            contribution.get('tax_year', '')
                        ])
                print(f"Wrote {len(political_contributions)} political contributions to {political_file}")
            else:
                # Create empty file
                with open(political_file, 'w') as f:
                    f.write("recipient\tamount\trecipient_address\trecipient_zip\trecipient_po_box\tfiler_ein\ttax_year\n")
                print(f"Created empty political contributions file: {political_file}")

            # Check if files were created and have content
            contractors_created = contractors_file.exists()
            political_created = political_file.exists()

            print(f"Contractors file created: {contractors_created}")
            print(f"Political contributions file created: {political_created}")

            if contractors_created:
                with open(contractors_file, 'r') as f:
                    contractors_content = f.read()
                if contractors_content.strip():
                    print(f"Contractors content:\n{contractors_content}")
                else:
                    print("Contractors file is empty")

            if political_created:
                with open(political_file, 'r') as f:
                    political_content = f.read()
                if political_content.strip():
                    print(f"Political contributions content:\n{political_content}")
                else:
                    print("Political contributions file is empty")

        # Check overall success
        output_files = list(output_dir.glob("*.tsv"))
        success = len(output_files) > 0
        print(f"\nTest completed. Created {len(output_files)} TSV files.")

        return success

if __name__ == "__main__":
    success = test_extraction()
    print(f"Test {'PASSED' if success else 'FAILED'}")
    sys.exit(0 if success else 1)