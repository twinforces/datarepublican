#!/usr/bin/env python3
"""
xml_processor.py - XML file processing for IRS 990 data

This module handles the parsing and processing of IRS 990 XML files,
extracting data into dataclasses and storing in the database.
"""

import zipfile
import logging
import threading
from io import BytesIO
from typing import Optional, List, Tuple, Dict, Any
from lxml import etree as ET  # type: ignore

from database_operations import DatabaseOperations
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from parse_utils import parse_grants, extract_address, parse_political_contribution_element
import parse_990
import parse_990ez
import parse_990pf
from logging_utils import log_info, log_error, log_debug, log_warning
from typing import Optional, List, Tuple

# Precompile XPaths used in parse_xml_file
FORM_TYPE_XPATHS = [
    etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//ReturnHeader/ReturnTypeCd")
]
TAX_YEAR_XPATHS = [
    etree.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//ReturnHeader/TaxYr")
]
FILER_EIN_XPATHS = [
    etree.XPath(".//irs:Filer/irs:EIN", namespaces={'irs': 'http://www.irs.gov/efile'}),
    etree.XPath(".//Filer/EIN")
]


class XMLProcessor:
    """Handles XML file parsing and processing"""

    # Class-level cache for ZIP file connections to avoid reopening
    _zip_cache: Dict[str, zipfile.ZipFile] = {}
    _zip_cache_lock = threading.Lock()

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1, quiet: bool = False):
        self.db_ops = db_ops
        self.processing_version = processing_version
        self.logger = logging.getLogger(__name__)
        self.quiet = quiet


    def process_xml_files(self, max_files: Optional[int] = None) -> int:
        """Parse XML files and extract data to dataclasses (step 5)"""
        if not self.quiet:
            log_info(self.logger, "Processing XML files and extracting data")

        # Get unprocessed XML files or files with outdated processing version
        xml_files = self.db_ops.get_unprocessed_xml_files(self.processing_version, max_files)
        if max_files is not None and len(xml_files) > max_files:
            xml_files = xml_files[:max_files]
            if not self.quiet:
                log_info(self.logger, f"Limited to {len(xml_files)} XML files (max_files={max_files})")
        else:
            if not self.quiet:
                log_info(self.logger, f"Found {len(xml_files)} unprocessed XML files")

        # Filter out already processed XML files based on EIN and tax_year to prevent reprocessing
        filtered_xml_files = []
        for xml_file in xml_files:
            # Check if this XML file has already been processed by looking for existing charity data
            # We need to extract EIN and tax_year first to check
            try:
                # Get ZIP file path from database
                zip_result = self.db_ops.execute_query("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (xml_file.zip_id,)).fetchone()
                if not zip_result:
                    if not self.quiet:
                        log_error(self.logger, f"No ZIP file found for xml_id {xml_file.xml_id}")
                    continue
                zip_path = zip_result[0]

                # Extract XML content from ZIP using cached connection
                xml_content = self._extract_xml_from_zip(zip_path, xml_file.internal_path)

                # Parse XML to get EIN and tax_year
                parser = etree.XMLParser(recover=True)
                tree = etree.parse(BytesIO(xml_content), parser)
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
                    if not self.quiet:
                        log_info(self.logger, f"Skipping already processed XML file: {xml_file.filename} (EIN: {filer_ein}, Year: {tax_year})")
                    # Mark as processed to avoid reprocessing
                    self.db_ops.mark_xml_processed(xml_file.xml_id, self.processing_version)
                    continue

                # Add to filtered list for processing
                filtered_xml_files.append(xml_file)

            except Exception as e:
                if not self.quiet:
                    log_warning(self.logger, f"Could not check processing status for XML {xml_file.filename}: {e}")
                # Include in processing list if we can't determine status
                filtered_xml_files.append(xml_file)

        if not self.quiet:
            log_info(self.logger, f"After filtering already processed files: {len(filtered_xml_files)} XML files to process")

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
                if not self.quiet:
                    log_error(self.logger, f"Failed to process XML {xml_file.filename}: {e}")
                self.db_ops.mark_xml_error(xml_file.xml_id, self.processing_version, str(e))

        if not self.quiet:
            log_info(self.logger, f"XML processing complete: {total_processed} files processed")
        return total_processed

    def _process_single_xml(self, xml_id: int, zip_id: int, filename: str, internal_path: str) -> bool:
        """Process a single XML file"""
        try:
            if not self.quiet:
                log_debug(self.logger, f"Processing XML {filename} (ID: {xml_id})")

            # Get ZIP file path from database
            zip_result = self.db_ops.execute_query("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (zip_id,)).fetchone()
            if not zip_result:
                if not self.quiet:
                    log_error(self.logger, f"No ZIP file found for xml_id {xml_id}")
                return False
            zip_path = zip_result[0]

            # Extract XML content from ZIP using cached connection
            xml_content = self._extract_xml_from_zip(zip_path, internal_path)

            if not self.quiet:
                log_debug(self.logger, f"Extracted XML content for {filename}, size: {len(xml_content)} bytes")

            # Parse XML
            parser = etree.XMLParser(recover=True)
            tree = etree.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            if not self.quiet:
                log_debug(self.logger, f"Extracted metadata for {filename}: form_type={form_type}, tax_year={tax_year}, ein={filer_ein}")

            # Update XML file with EIN now that we have it
            if filer_ein and filer_ein != "Unknown":
                self.db_ops.update_xml_ein(xml_id, filer_ein)

            if not filer_ein or filer_ein == "Unknown":
                if not self.quiet:
                    log_error(self.logger, f"Skipping XML {filename}: invalid EIN {filer_ein}")
                return False

            # Extract data based on form type
            if form_type == "990":
                charity, officers, grants, contractors, contributions, address = self._parse_990_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990EZ":
                charity, officers, grants, contractors, contributions, address = self._parse_990ez_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990PF":
                charity, officers, grants, contractors, contributions, address = self._parse_990pf_data(root, filename, filer_ein, tax_year, form_type)
            else:
                if not self.quiet:
                    log_error(self.logger, f"Unsupported form type {form_type} in {filename}")
                return False

            # Store in database
            if charity:
                if not self.quiet:
                    log_debug(self.logger, f"Inserting charity for EIN {filer_ein}, tax_year {tax_year}")
                self.db_ops.insert_charity(charity)
                charity_id = charity.charity_id
                if not self.quiet:
                    log_debug(self.logger, f"Charity inserted with charity_id: {charity_id}")

                # Extract and store address
                if address:
                    if not self.quiet:
                        log_debug(self.logger, f"Inserting address for EIN {filer_ein}")
                        log_info(self.logger, f"DEBUG: Address to insert: ein={address.ein}, canonical='{address.canonical_address}', po_box='{address.po_box}', colocator='{address.colocator}'")
                        log_info(self.logger, f"DEBUG: Address fields - canonical_address='{address.canonical_address}', po_box='{address.po_box}', colocator='{address.colocator}'")
                    self.db_ops.insert_address(address)
                else:
                    if not self.quiet:
                        log_info(self.logger, f"DEBUG: No address extracted for EIN {filer_ein} - address is None")

                # Insert related data
                for officer in officers:
                    if not self.quiet:
                        log_debug(self.logger, f"Setting officer.charity_id to {charity_id} for officer {officer.first_name} {officer.last_name}")
                    officer.charity_id = charity_id
                    if not self.quiet:
                        log_debug(self.logger, f"Inserting officer: charity_id={officer.charity_id}, first_name={officer.first_name}, last_name={officer.last_name}")
                    self.db_ops.insert_officer(officer)

                for grant in grants:
                    self.db_ops.insert_grant(grant)

                for contractor in contractors:
                    self.db_ops.insert_contractor(contractor)

                for contribution in contributions:
                    self.db_ops.insert_political_contribution(contribution)

            if not self.quiet:
                log_debug(self.logger, f"Successfully parsed {filename}: charity={filer_ein}, grants={len(grants)}, officers={len(officers)}")
            return True

        except Exception as e:
            if not self.quiet:
                log_error(self.logger, f"Failed to process XML {filename}: {e}")
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
                    if not self.quiet:
                        log_info(self.logger, f"TRACE: Found raw EIN: '{raw_ein}' using xpath: {xpath.path}")
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        if not self.quiet:
                            log_info(self.logger, f"TRACE: Formatted EIN: '{formatted_ein}' (valid 9-digit)")
                        return formatted_ein
                    else:
                        if not self.quiet:
                            log_warning(self.logger, f"TRACE: Non-digit EIN found: '{raw_ein}', returning 'Unknown'")
                        return "Unknown"
            except Exception as e:
                self.logger.debug(f"XPath {xpath.path} failed: {e}")
                continue
        if not self.quiet:
            log_warning(self.logger, "TRACE: No EIN found in XML, returning 'Unknown'")
        return "Unknown"

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution], Optional[Address]]:
        """Parse Form 990 data"""
        if not self.quiet:
            log_info(self.logger, f"TRACE: _parse_990_data() called with EIN: '{filer_ein}' for file {filename}")

        # Extract charity data using existing parsing functions - now returns model instances
        charity, officers, grants, contractors, contributions, address = parse_990.parse_990(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.logger.error)

        if not charity:
            if not self.quiet:
                log_warning(self.logger, f"TRACE: parse_990() returned None for EIN: '{filer_ein}' in file {filename}")
            return None, [], [], [], [], None

        if not self.quiet:
            log_info(self.logger, f"TRACE: parse_990() returned Charity with EIN: '{charity.ein}' for file {filename}")

        # Extract grants, contractors, and political contributions (override the empty lists from parse_990)
        grants = self._extract_grants_990(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions, address

    def _extract_xml_from_zip(self, zip_path: str, internal_path: str) -> bytes:
        """Extract XML content from ZIP using cached connection"""
        zip_key = str(zip_path)

        with self._zip_cache_lock:
            if zip_key not in self._zip_cache:
                # Open ZIP file and cache the connection
                self._zip_cache[zip_key] = zipfile.ZipFile(zip_path, 'r')
                if not self.quiet:
                    print(f"Opened and cached ZIP connection for {zip_path}")

            zip_ref = self._zip_cache[zip_key]

        # Extract XML content from cached connection
        with zip_ref.open(internal_path) as xml_file:
            return xml_file.read()

    @classmethod
    def cleanup_zip_cache(cls):
        """Clean up cached ZIP connections"""
        with cls._zip_cache_lock:
            for zip_ref in cls._zip_cache.values():
                try:
                    zip_ref.close()
                except:
                    pass  # Ignore errors during cleanup
            cls._zip_cache.clear()
            print("Cleaned up XML processor ZIP file cache")

    def _extract_grants_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990"""
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(etree.tostring(root))
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
        xml_content = BytesIO(etree.tostring(root))
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
        xml_content = BytesIO(etree.tostring(root))
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
        # Extract contractors from Schedule L (Independent Contractors)
        contractor_xpaths = [
            etree.XPath(".//irs:IRS990ScheduleL/irs:IndepContractorGrp", namespaces={'irs': 'http://www.irs.gov/efile'}),
            etree.XPath(".//irs:IRS990ScheduleL/irs:IndependentContractorGrp", namespaces={'irs': 'http://www.irs.gov/efile'}),
            etree.XPath(".//irs:IRS990ScheduleL/irs:ContractorCompensationGrp", namespaces={'irs': 'http://www.irs.gov/efile'}),
            etree.XPath(".//IRS990ScheduleL/IndepContractorGrp"),
            etree.XPath(".//IRS990ScheduleL/IndependentContractorGrp"),
            etree.XPath(".//IRS990ScheduleL/ContractorCompensationGrp"),
        ]

        for xpath in contractor_xpaths:
            try:
                contractor_elements = xpath(root)
                for elem in contractor_elements:
                    contractor = self._parse_contractor_element(elem, filer_ein, tax_year)
                    if contractor:
                        contractors.append(contractor)
            except:
                continue

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
        # Extract political contributions from Schedule C
        contribution_xpaths = [
            etree.XPath(".//irs:IRS990ScheduleC/irs:PoliticalCampaignActyGrp", namespaces={'irs': 'http://www.irs.gov/efile'}),
            etree.XPath(".//IRS990ScheduleC/PoliticalCampaignActyGrp"),
        ]

        for xpath in contribution_xpaths:
            try:
                contribution_elements = xpath(root)
                for elem in contribution_elements:
                    contribution = self._parse_political_contribution_element(elem, filer_ein, tax_year)
                    if contribution:
                        contributions.append(contribution)
            except:
                continue

        return contributions

    def _extract_political_contributions_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990EZ"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990PF"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_address(self, root, filename: str, filer_ein: str) -> Optional[Address]:
        """Extract address from XML - moved to parse_utils.py"""
        from parse_utils import extract_address
        return extract_address(root, filename, filer_ein, self.quiet, self.logger)

    def _parse_political_contribution_element(self, elem, filer_ein: str, tax_year: int) -> Optional[PoliticalContribution]:
        """Parse a single political contribution element from XML - moved to parse_utils.py"""
        from parse_utils import parse_political_contribution_element
        return parse_political_contribution_element(elem, filer_ein, tax_year, self.quiet, self.logger)
    def _parse_contractor_element(self, elem, filer_ein: str, tax_year: int) -> Optional[Contractor]:
        """Parse a single contractor element from XML"""
        from models.contractor import Contractor

        try:
            # Extract contractor name
            name_xpaths = [
                etree.XPath(".//irs:BusinessName/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'}),
                etree.XPath(".//BusinessName/BusinessNameLine1Txt"),
                etree.XPath(".//irs:PersonNm", namespaces={'irs': 'http://www.irs.gov/efile'}),
                etree.XPath(".//PersonNm"),
            ]

            contractor_name = None
            for xpath in name_xpaths:
                try:
                    result = xpath(elem)
                    if result and result[0].text:
                        contractor_name = result[0].text.strip()
                        break
                except:
                    continue

            # Extract compensation amount
            amount_xpaths = [
                etree.XPath(".//irs:CompensationAmt", namespaces={'irs': 'http://www.irs.gov/efile'}),
                etree.XPath(".//CompensationAmt"),
                etree.XPath(".//irs:TotalAmt", namespaces={'irs': 'http://www.irs.gov/efile'}),
                etree.XPath(".//TotalAmt"),
            ]

            compensation = 0.0
            for xpath in amount_xpaths:
                try:
                    result = xpath(elem)
                    if result and result[0].text:
                        try:
                            compensation = float(result[0].text.strip().replace(',', ''))
                            break
                        except ValueError:
                            continue
                except:
                    continue

            if contractor_name and compensation > 0:
                contractor = Contractor(
                    filer_ein=filer_ein,
                    name=contractor_name,
                    amount=compensation,
                    tax_year=tax_year
                )
                return contractor

        except Exception as e:
            if not self.quiet:
                log_error(self.logger, f"Error parsing contractor element: {e}")

        return None

    def _parse_990ez_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution], Optional[Address]]:
        """Parse Form 990EZ data"""
        # Extract charity data using existing parsing functions - now returns model instances
        charity, officers, grants, contractors, contributions, address = parse_990ez.parse_990ez(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.logger.error)

        if not charity:
            return None, [], [], [], [], None

        # Extract grants, contractors, and political contributions (override the empty lists from parse_990ez)
        grants = self._extract_grants_990ez(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990ez(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990ez(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions, address

    def _parse_990pf_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution], Optional[Address]]:
        """Parse Form 990PF data"""
        # Extract charity data using existing parsing functions - now returns model instances
        charity, officers, grants, contractors, contributions, address = parse_990pf.parse_990pf(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.logger.error)

        if not charity:
            return None, [], [], [], [], None

        # Extract grants, contractors, and political contributions (override the empty lists from parse_990pf)
        grants = self._extract_grants_990pf(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990pf(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990pf(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions, address