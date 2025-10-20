#!/usr/bin/env python3
"""
xml_processor.py - XML file processing for IRS 990 data

This module handles the parsing and processing of IRS 990 XML files,
extracting data into dataclasses and storing in the database.
"""

import zipfile
import logging
from io import BytesIO
from typing import Optional, List, Tuple, Dict, Any
from lxml import etree as ET

from database_operations import DatabaseOperations
from irs990processorDC import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from parse_utils import parse_grants
import parse_990
import parse_990ez
import parse_990pf

# Precompile XPaths used in parse_xml_file
FORM_TYPE_XPATHS = [
    ET.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces={'irs': 'http://www.irs.gov/efile'}),
    ET.XPath(".//ReturnHeader/ReturnTypeCd")
]
TAX_YEAR_XPATHS = [
    ET.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces={'irs': 'http://www.irs.gov/efile'}),
    ET.XPath(".//ReturnHeader/TaxYr")
]
FILER_EIN_XPATHS = [
    ET.XPath(".//irs:Filer/irs:EIN", namespaces={'irs': 'http://www.irs.gov/efile'}),
    ET.XPath(".//Filer/EIN")
]


class XMLProcessor:
    """Handles XML file parsing and processing"""

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        self.db_ops = db_ops
        self.processing_version = processing_version
        self.logger = logging.getLogger(__name__)

    def process_xml_files(self, max_files: Optional[int] = None) -> int:
        """Parse XML files and extract data to dataclasses (step 5)"""
        self.logger.info("Processing XML files and extracting data")

        # Get unprocessed XML files or files with outdated processing version
        xml_files = self.db_ops.get_unprocessed_xml_files(self.processing_version, max_files)
        if max_files is not None and len(xml_files) > max_files:
            xml_files = xml_files[:max_files]
            self.logger.info(f"Limited to {len(xml_files)} XML files (max_files={max_files})")
        else:
            self.logger.info(f"Found {len(xml_files)} unprocessed XML files")

        # Filter out already processed XML files based on EIN and tax_year to prevent reprocessing
        filtered_xml_files = []
        for xml_file in xml_files:
            # Check if this XML file has already been processed by looking for existing charity data
            # We need to extract EIN and tax_year first to check
            try:
                # Get ZIP file path from database
                zip_result = self.db_ops.execute_query("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (xml_file.zip_id,)).fetchone()
                if not zip_result:
                    self.logger.error(f"No ZIP file found for xml_id {xml_file.xml_id}")
                    continue
                zip_path = zip_result[0]

                # Extract XML content from ZIP
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    with zip_ref.open(xml_file.internal_path) as xml_file_handle:
                        xml_content = xml_file_handle.read()

                # Parse XML to get EIN and tax_year
                parser = ET.XMLParser(recover=True)
                tree = ET.parse(BytesIO(xml_content), parser)
                root = tree.getroot()

                form_type = self._extract_form_type(root)
                tax_year = self._extract_tax_year(root)
                filer_ein = self._extract_filer_ein(root)

                if not filer_ein or filer_ein == "Unknown":
                    # Skip files with invalid EIN
                    filtered_xml_files.append(xml_file)
                    continue

                # Check if charity data already exists for this EIN and tax_year
                existing_charity = self.db_ops.execute_query(
                    "SELECT charity_id FROM Charities WHERE ein = ? AND tax_year = ?",
                    (filer_ein, tax_year)
                ).fetchone()

                if existing_charity:
                    self.logger.info(f"Skipping already processed XML file: {xml_file.filename} (EIN: {filer_ein}, Year: {tax_year})")
                    # Mark as processed to avoid reprocessing
                    self.db_ops.mark_xml_processed(xml_file.xml_id, self.processing_version)
                    continue

                # Add to filtered list for processing
                filtered_xml_files.append(xml_file)

            except Exception as e:
                self.logger.warning(f"Could not check processing status for XML {xml_file.filename}: {e}")
                # Include in processing list if we can't determine status
                filtered_xml_files.append(xml_file)

        self.logger.info(f"After filtering already processed files: {len(filtered_xml_files)} XML files to process")

        total_processed = 0
        for xml_file in filtered_xml_files:
            try:
                result = self._process_single_xml(xml_file.xml_id, xml_file.zip_id, xml_file.filename, xml_file.internal_path)
                if result:
                    total_processed += 1
                    # Mark as processed with success message
                    self.db_ops.mark_xml_processed(xml_file.xml_id, self.processing_version)
                else:
                    # Mark as error
                    self.db_ops.mark_xml_error(xml_file.xml_id, self.processing_version, "Processing failed")
            except Exception as e:
                self.logger.error(f"Failed to process XML {xml_file.filename}: {e}")
                self.db_ops.mark_xml_error(xml_file.xml_id, self.processing_version, str(e))

        self.logger.info(f"XML processing complete: {total_processed} files processed")
        return total_processed

    def _process_single_xml(self, xml_id: int, zip_id: int, filename: str, internal_path: str) -> bool:
        """Process a single XML file"""
        try:
            self.logger.debug(f"Processing XML {filename} (ID: {xml_id})")

            # Get ZIP file path from database
            zip_result = self.db_ops.execute_query("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (zip_id,)).fetchone()
            if not zip_result:
                self.logger.error(f"No ZIP file found for xml_id {xml_id}")
                return False
            zip_path = zip_result[0]

            # Extract XML content from ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                with zip_ref.open(internal_path) as xml_file:
                    xml_content = xml_file.read()

            self.logger.debug(f"Extracted XML content for {filename}, size: {len(xml_content)} bytes")

            # Parse XML
            parser = ET.XMLParser(recover=True)
            tree = ET.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            self.logger.debug(f"Extracted metadata for {filename}: form_type={form_type}, tax_year={tax_year}, ein={filer_ein}")

            # Update XML file with EIN now that we have it
            if filer_ein and filer_ein != "Unknown":
                self.db_ops.update_xml_ein(xml_id, filer_ein)

            if not filer_ein or filer_ein == "Unknown":
                self.logger.error(f"Skipping XML {filename}: invalid EIN {filer_ein}")
                return False

            # Extract data based on form type
            if form_type == "990":
                charity, officers, grants, contractors, contributions = self._parse_990_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990EZ":
                charity, officers, grants, contractors, contributions = self._parse_990ez_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990PF":
                charity, officers, grants, contractors, contributions = self._parse_990pf_data(root, filename, filer_ein, tax_year, form_type)
            else:
                self.logger.error(f"Unsupported form type {form_type} in {filename}")
                return False

            # Store in database
            if charity:
                self.logger.debug(f"Inserting charity for EIN {filer_ein}, tax_year {tax_year}")
                self.db_ops.insert_charity(charity)
                charity_id = charity.charity_id
                self.logger.debug(f"Charity inserted with charity_id: {charity_id}")

                # Extract and store address
                address = self._extract_address(root, filename, filer_ein)
                if address:
                    self.logger.debug(f"Inserting address for EIN {filer_ein}")
                    self.logger.info(f"DEBUG: Address to insert: ein={address.ein}, canonical='{address.canonical_address}', po_box='{address.po_box}', colocator='{address.colocator}'")
                    self.db_ops.insert_address(address)

                # Insert related data
                for officer in officers:
                    self.logger.debug(f"Setting officer.charity_id to {charity_id} for officer {officer.first_name} {officer.last_name}")
                    officer.charity_id = charity_id
                    self.logger.debug(f"Inserting officer: charity_id={officer.charity_id}, first_name={officer.first_name}, last_name={officer.last_name}")
                    self.db_ops.insert_officer(officer)

                for grant in grants:
                    self.db_ops.insert_grant(grant)

                for contractor in contractors:
                    self.db_ops.insert_contractor(contractor)

                for contribution in contributions:
                    self.db_ops.insert_political_contribution(contribution)

            self.logger.debug(f"Successfully parsed {filename}: charity={filer_ein}, grants={len(grants)}, officers={len(officers)}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to process XML {filename}: {e}")
            return False

    def _extract_form_type(self, root) -> str:
        """Extract form type from XML"""
        for xpath in FORM_TYPE_XPATHS:
            try:
                result = xpath(root)
                if result:
                    return result[0].text
            except:
                continue
        return "Unknown"

    def _extract_tax_year(self, root) -> int:
        """Extract tax year from XML"""
        for xpath in TAX_YEAR_XPATHS:
            try:
                result = xpath(root)
                if result:
                    year_str = result[0].text
                    if year_str and year_str.isdigit():
                        return int(year_str)
            except:
                continue
        return 0  # Default fallback

    def _extract_filer_ein(self, root) -> str:
        """Extract filer EIN from XML"""
        for xpath in FILER_EIN_XPATHS:
            try:
                result = xpath(root)
                if result:
                    raw_ein = result[0].text.strip()
                    self.logger.info(f"TRACE: Found raw EIN: '{raw_ein}' using xpath: {xpath.path}")
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        self.logger.info(f"TRACE: Formatted EIN: '{formatted_ein}' (valid 9-digit)")
                        return formatted_ein
                    else:
                        self.logger.warning(f"TRACE: Non-digit EIN found: '{raw_ein}', returning 'Unknown'")
                        return "Unknown"
            except Exception as e:
                self.logger.debug(f"XPath {xpath.path} failed: {e}")
                continue
        self.logger.warning("TRACE: No EIN found in XML, returning 'Unknown'")
        return "Unknown"

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990 data"""
        xpath_cache: Dict = {}

        self.logger.info(f"TRACE: _parse_990_data() called with EIN: '{filer_ein}' for file {filename}")

        # Extract charity data using existing parsing functions
        row, officer_entries = parse_990.parse_990(root, filename, xpath_cache, filer_ein, tax_year, form_type, log_error=self.logger.error)

        if not row:
            self.logger.warning(f"TRACE: parse_990() returned None for EIN: '{filer_ein}' in file {filename}")
            return None, [], [], [], []
        else:
            self.logger.info(f"TRACE: parse_990() returned row with EIN: '{row[1]}' for file {filename}")

        # Convert row to Charity dataclass
        self.logger.info(f"DEBUG: Creating Charity object with row data: ein={row[1]}, business_name_line1={row[3]}")
        charity = Charity(
            ein=row[1],  # filer_ein
            tax_year=row[0],  # tax_year
            filer_name=row[2],  # filer_name
            business_name_line1=row[3],  # business_name_line1
            business_name_line2=row[4],  # business_name_line2
            receipt_amt=row[5],  # receipt
            govt_amt=row[6],  # govt_grants
            contrib_amt=row[7],  # contributions
            org_type=row[8],  # org_type
            total_exp=row[9],  # total_exp
            prog_exp=row[10],  # prog_exp
            travel_amt=row[11],  # travel
            conferences_amt=row[12],  # conferences
            officer_comp=row[13],  # officer_comp
            comp_pct=row[14],  # comp_pct
            comp_ptile=row[15],  # comp_ptile
            travel_pct=row[16],  # travel_pct
            travel_ptile=row[17],  # travel_ptile
            conferences_pct=row[18],  # conferences_pct
            conferences_ptile=row[19],  # conferences_ptile
            grants_pct=row[20],  # grants_pct
            grants_ptile=row[21],  # grants_ptile
            foreign_expenses_pct=row[22],  # foreign_expenses_pct
            foreign_expenses_ptile=row[23],  # foreign_expenses_ptile
            grift_ratio=row[24],  # grift_ratio
            total_assets=row[25],  # total_assets
            form_type=row[26],  # form_type
            denominator=row[27],  # denominator
            foreign_office=row[28],  # foreign_office
            foreign_expenses=row[29],  # foreign_expenses
            grants_to_others=row[30],  # grants_to_others
            domestic_misrep_flag=row[31],  # domestic_misrep_flag
            xml_name=row[32]  # xml_name
        )
        self.logger.info(f"DEBUG: Charity object created successfully: ein={charity.ein}, business_name_line1={charity.business_name_line1}")
        self.logger.info(f"TRACE: Created Charity dataclass with EIN: '{charity.ein}' from row[1] for file {filename}")

        # Convert officer entries
        officers = []
        for entry in officer_entries:
            officer = Officer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Extract grants, contractors, and political contributions
        grants = self._extract_grants_990(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _extract_grants_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990"""
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990EZ"""
        # Similar to 990 but using EZ-specific parsing
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990EZ")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990PF"""
        # Similar to 990 but using PF-specific parsing
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990PF")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_contractors_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990"""
        contractors = []
        # TODO: Implement contractor extraction from Schedule L or other sections
        return contractors

    def _extract_contractors_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990EZ"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_contractors_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990PF"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990"""
        contributions = []
        # TODO: Implement political contribution extraction
        return contributions

    def _extract_political_contributions_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990EZ"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990PF"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_address(self, root, filename: str, filer_ein: str) -> Optional[Address]:
        """Extract address from XML"""
        from extract_utils import canonicalize_address

        # Try to find US address elements
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
            self.logger.debug(f"No address components found in {filename}")
            return None

        # Extract filer name for address
        name_xpaths = [
            ET.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'}),
            ET.XPath(".//Filer/BusinessName/BusinessNameLine1Txt"),
            ET.XPath(".//irs:Filer/irs:Name/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'}),
            ET.XPath(".//Filer/Name/BusinessNameLine1Txt")
        ]

        filer_name = "Unknown"
        for xpath in name_xpaths:
            try:
                result = xpath(root)
                if result and result[0].text:
                    filer_name = result[0].text.strip()
                    break
            except:
                continue

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

        return None

    def _parse_990ez_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990EZ data"""
        xpath_cache: Dict = {}

        # Extract charity data using existing parsing functions
        row, officer_entries = parse_990ez.parse_990ez(root, filename, xpath_cache, filer_ein, tax_year, form_type, log_error=self.logger.error)

        if not row:
            return None, [], [], [], []

        # Convert row to Charity dataclass (similar to 990)
        self.logger.info(f"DEBUG: Creating Charity object with row data: ein={row[1]}, business_name_line1={row[3]}")
        charity = Charity(
            ein=row[1], tax_year=row[0], filer_name=row[2], business_name_line1=row[3],
            business_name_line2=row[4], receipt_amt=row[5], govt_amt=row[6],
            contrib_amt=row[7], org_type=row[8], total_exp=row[9], prog_exp=row[10],
            travel_amt=row[11], conferences_amt=row[12], officer_comp=row[13],
            comp_pct=row[14], comp_ptile=row[15], travel_pct=row[16],
            travel_ptile=row[17], conferences_pct=row[18], conferences_ptile=row[19],
            grants_pct=row[20], grants_ptile=row[21], foreign_expenses_pct=row[22],
            foreign_expenses_ptile=row[23], grift_ratio=row[24], total_assets=row[25],
            form_type=row[26], denominator=row[27], foreign_office=row[28],
            foreign_expenses=row[29], grants_to_others=row[30], domestic_misrep_flag=row[31],
            xml_name=row[32]
        )
        self.logger.info(f"DEBUG: Charity object created successfully: ein={charity.ein}, business_name_line1={charity.business_name_line1}")

        # Convert officer entries
        officers = []
        for entry in officer_entries:
            officer = Officer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Extract grants, contractors, and political contributions
        grants: List[Grant] = self._extract_grants_990ez(root, filename, filer_ein, tax_year)
        contractors: List[Contractor] = self._extract_contractors_990ez(root, filename, filer_ein, tax_year)
        contributions: List[PoliticalContribution] = self._extract_political_contributions_990ez(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _parse_990pf_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990PF data"""
        xpath_cache: Dict = {}

        # Extract charity data using existing parsing functions
        row, officer_entries = parse_990pf.parse_990pf(root, filename, xpath_cache, filer_ein, tax_year, form_type, log_error=self.logger.error)

        if not row:
            return None, [], [], [], []

        # Convert row to Charity dataclass (similar to 990)
        self.logger.info(f"DEBUG: Creating Charity object with row data: ein={row[1]}, business_name_line1={row[3]}")
        charity = Charity(
            ein=row[1], tax_year=row[0], filer_name=row[2], business_name_line1=row[3],
            business_name_line2=row[4], receipt_amt=row[5], govt_amt=row[6],
            contrib_amt=row[7], org_type=row[8], total_exp=row[9], prog_exp=row[10],
            travel_amt=row[11], conferences_amt=row[12], officer_comp=row[13],
            comp_pct=row[14], comp_ptile=row[15], travel_pct=row[16],
            travel_ptile=row[17], conferences_pct=row[18], conferences_ptile=row[19],
            grants_pct=row[20], grants_ptile=row[21], foreign_expenses_pct=row[22],
            foreign_expenses_ptile=row[23], grift_ratio=row[24], total_assets=row[25],
            form_type=row[26], denominator=row[27], foreign_office=row[28],
            foreign_expenses=row[29], grants_to_others=row[30], domestic_misrep_flag=row[31],
            xml_name=row[32]
        )
        self.logger.info(f"DEBUG: Charity object created successfully: ein={charity.ein}, business_name_line1={charity.business_name_line1}")

        # Convert officer entries
        officers = []
        for entry in officer_entries:
            officer = Officer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Extract grants, contractors, and political contributions
        grants = self._extract_grants_990pf(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990pf(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990pf(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions