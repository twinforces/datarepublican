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
from lxml import etree  # type: ignore
from lxml import etree

from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from parse_utils import parse_grants, extract_address, parse_political_contribution_element
import parse_990
import parse_990ez
import parse_990pf
from logging_utils import log_info, log_error, log_debug, log_warning
from typing import Optional, List, Tuple

# Import XPath configurations from xpaths.py
from xpaths import COMMON_XPATHS


class XMLProducer:
    """
    XML Producer - Handles XML parsing and operation collection.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.

    If you need to add database writes here, you are violating the pattern.
    Instead, create a DatabaseOperation and return it for the consumer to handle.
    """

    # Class-level cache for ZIP file connections to avoid reopening
    # Each entry contains both the ZipFile and a per-ZIP lock for thread safety
    _zip_cache: Dict[str, Tuple[zipfile.ZipFile, threading.Lock]] = {}
    _zip_cache_lock = threading.Lock()

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1, quiet: bool = False):
        self.db_ops = db_ops
        self.processing_version = processing_version
        self.logger = logging.getLogger(__name__)
        self.quiet = quiet

    def process_single_xml_for_operations(self, xml_id: str, zip_id: str, filename: str, internal_path: str, file_size: int) -> Tuple[bool, List[DatabaseOperation]]:
        """
        Process a single XML file and return operations for producer-consumer pattern.

        PRODUCER-CONSUMER PATTERN: This method collects operations but does NOT execute them.
        All database operations are returned as DatabaseOperation objects for the consumer to handle.

        Args:
            xml_id: XML file ID
            zip_id: ZIP file ID containing the XML
            filename: XML filename
            internal_path: Path within ZIP file
            file_size: Size of XML file in bytes

        Returns:
            Tuple of (success: bool, operations: List[DatabaseOperation])
        """
        success, operations = self._process_single_xml(xml_id, zip_id, filename, internal_path)

        # Add XML file update operation at the end
        metadata = {
            "file_size": file_size,
            "ein": None,  # Will be set by processor
            "tax_year": None,  # Will be set by processor
            "form_type": None,  # Will be set by processor
            "error_message": None if success else "Processing failed",
            "processed": success
        }
        xml_update_operation = DatabaseOperation(
            DatabaseOperationType.XML_FILE_UPDATE,
            metadata,
            xml_id
        )
        operations.append(xml_update_operation)

        return success, operations

    def _process_single_xml(self, xml_id: str, zip_id: str, filename: str, internal_path: str) -> Tuple[bool, List[DatabaseOperation]]:
        """
        Process a single XML file and return operations instead of executing them.

        PRODUCER-CONSUMER PATTERN: This is a PRODUCER method.
        - DO NOT call any self.db_ops.insert_* methods here
        - DO NOT call any self.db_ops.update_* methods here
        - Instead, collect DatabaseOperation objects and return them

        Args:
            xml_id: XML file ID
            zip_id: ZIP file ID
            filename: XML filename
            internal_path: Path within ZIP

        Returns:
            Tuple of (success: bool, operations: List[DatabaseOperation])
        """
        operations = []
        try:
            if not self.quiet:
                log_debug(self.logger, f"Processing XML {filename} (ID: {xml_id})")

            # Get ZIP file path from database
            zip_result = self.db_ops.execute_query("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (zip_id,)).fetchone()
            if not zip_result:
                if not self.quiet:
                    log_error(self.logger, f"No ZIP file found for xml_id {xml_id}")
                return False, operations
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

            # Collect XML EIN update operation instead of writing directly
            if filer_ein and filer_ein != "Unknown":
                operations.append(DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_EIN,
                    {"ein": filer_ein},
                    xml_id
                ))

            if not filer_ein or filer_ein == "Unknown":
                if not self.quiet:
                    log_error(self.logger, f"Skipping XML {filename}: invalid EIN {filer_ein}")
                return False, operations

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
                return False, operations

            # Collect database operations instead of executing them
            if charity:
                if not self.quiet:
                    log_debug(self.logger, f"Collecting charity operation for EIN {filer_ein}, tax_year {tax_year}")

                # Add charity insert operation
                operations.append(DatabaseOperation(
                    DatabaseOperationType.INSERT_CHARITY,
                    charity,
                    xml_id
                ))

                # Extract and collect address operation
                if address:
                    if not self.quiet:
                        log_debug(self.logger, f"Collecting address operation for EIN {filer_ein}")
                        log_info(self.logger, f"DEBUG: Address to insert: ein={address.ein}, canonical='{address.canonical_address}', po_box='{address.po_box}', colocator='{address.colocator}'")
                        log_info(self.logger, f"DEBUG: Address fields - canonical_address='{address.canonical_address}', po_box='{address.po_box}', colocator='{address.colocator}'")
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_ADDRESS,
                        address,
                        xml_id
                    ))
                else:
                    if not self.quiet:
                        log_info(self.logger, f"DEBUG: No address extracted for EIN {filer_ein} - address is None")

                # Collect related data operations
                for officer in officers:
                    if not self.quiet:
                        log_debug(self.logger, f"Collecting officer operation for officer {officer.first_name} {officer.last_name}")
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_OFFICER,
                        officer,
                        xml_id
                    ))

                for grant in grants:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_GRANT,
                        grant,
                        xml_id
                    ))

                for contractor in contractors:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_CONTRACTOR,
                        contractor,
                        xml_id
                    ))

                for contribution in contributions:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION,
                        contribution,
                        xml_id
                    ))

            if not self.quiet:
                log_debug(self.logger, f"Successfully parsed {filename}: charity={filer_ein}, grants={len(grants)}, officers={len(officers)}")
            return True, operations

        except Exception as e:
            if not self.quiet:
                log_error(self.logger, f"Failed to process XML {filename}: {e}")
            return False, operations

    # ... existing methods for XML parsing ...

    # XMLProducer methods (shared implementation)
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
        import threading
        zip_key = str(zip_path)
        thread_id = threading.get_ident()

        log_debug(self.logger, f"Thread {thread_id}: Requesting ZIP access for {zip_path} (key: {zip_key})")

        with self._zip_cache_lock:
            log_debug(self.logger, f"Thread {thread_id}: Acquired ZIP cache lock for {zip_path}")
            if zip_key not in self._zip_cache:
                # Open ZIP file and cache the connection with per-ZIP lock
                log_info(self.logger, f"Thread {thread_id}: Opening new ZIP connection for {zip_path}")
                zip_ref = zipfile.ZipFile(zip_path, 'r')
                zip_lock = threading.Lock()
                self._zip_cache[zip_key] = (zip_ref, zip_lock)
                log_info(self.logger, f"Thread {thread_id}: Opened and cached ZIP connection for {zip_path}")
            else:
                log_debug(self.logger, f"Thread {thread_id}: Using cached ZIP connection for {zip_path}")

            zip_ref, zip_lock = self._zip_cache[zip_key]
            log_debug(self.logger, f"Thread {thread_id}: Retrieved ZIP reference from cache for {zip_path}")
            log_debug(self.logger, f"Thread {thread_id}: Releasing ZIP cache lock for {zip_path}")

        # Extract XML content from cached connection - NOW PROTECTED BY PER-ZIP LOCK
        log_debug(self.logger, f"Thread {thread_id}: Starting extraction of {internal_path} from {zip_path} (acquiring per-ZIP lock)")
        with zip_lock:
            log_debug(self.logger, f"Thread {thread_id}: Acquired per-ZIP lock for {zip_path}")
            try:
                log_debug(self.logger, f"Thread {thread_id}: Opening {internal_path} in ZIP file")
                with zip_ref.open(internal_path) as xml_file:
                    log_debug(self.logger, f"Thread {thread_id}: Reading content from {internal_path}")
                    content = xml_file.read()
                log_debug(self.logger, f"Thread {thread_id}: Successfully extracted {len(content)} bytes from {internal_path}")
                return content
            except Exception as e:
                log_error(self.logger, f"Thread {thread_id}: Error extracting {internal_path} from {zip_path}: {e}")
                raise
            finally:
                log_debug(self.logger, f"Thread {thread_id}: Releasing per-ZIP lock for {zip_path}")

    @classmethod
    def cleanup_zip_cache(cls):
        """Clean up cached ZIP connections"""
        with cls._zip_cache_lock:
            print(f"Cleaning up {len(cls._zip_cache)} cached ZIP connections")
            for zip_path, (zip_ref, zip_lock) in cls._zip_cache.items():
                try:
                    print(f"Closing ZIP connection for {zip_path}")
                    zip_ref.close()
                except Exception as e:
                    print(f"Error closing ZIP connection for {zip_path}: {e}")
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


class XMLConsumer:
    """
    XML Consumer - Handles database operations execution.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.

    If you need to modify this class to handle new operation types,
    ensure all database writes go through the bulk operation methods.
    """

    def __init__(self, db_ops: DatabaseOperations, quiet: bool = False):
        self.db_ops = db_ops
        self.logger = logging.getLogger(__name__)
        self.quiet = quiet

    def execute_operations_batch(self, operations: List[DatabaseOperation]) -> None:
        """
        Execute a batch of database operations.

        PRODUCER-CONSUMER PATTERN: This is a CONSUMER method.
        This method safely executes database operations in a single-threaded context.

        Args:
            operations: List of DatabaseOperation objects to execute
        """
        if not operations:
            return

        # Group operations by type for efficient processing
        operations_by_type = {}
        for operation in operations:
            if not isinstance(operation, DatabaseOperation):
                log_error(self.logger, f"Invalid operation type: {type(operation)}, expected DatabaseOperation")
                continue
            op_type = operation.operation_type.value
            if op_type not in operations_by_type:
                operations_by_type[op_type] = []
            operations_by_type[op_type].append(operation)

        # Execute operations in dependency order
        try:
            # Handle OPTIMIZE_DATABASE operations first
            if DatabaseOperationType.OPTIMIZE_DATABASE.value in operations_by_type:
                for operation in operations_by_type[DatabaseOperationType.OPTIMIZE_DATABASE.value]:
                    self._execute_optimize_operation(operation)

            # 1. Handle XML file updates first
            self._process_xml_file_update_operations(operations_by_type)

            # 2. Insert charities
            charity_id_map = self._process_charity_operations(operations_by_type)

            # 3. Insert related data (officers, grants, etc.) using charity_id_map
            self._process_related_operations(operations_by_type, charity_id_map)

            # 4. Insert addresses
            self._process_address_operations(operations_by_type, charity_id_map)

        except Exception as e:
            log_error(self.logger, f"Failed to execute operations batch: {e}", exc_info=True)
            raise

    def _execute_optimize_operation(self, operation):
        """Execute database optimization operation"""
        if operation.operation_type != DatabaseOperationType.OPTIMIZE_DATABASE:
            return

        log_info(self.logger, "Starting database optimization...")
        try:
            # Call the optimize_database method from DatabaseOperations
            self.db_ops.optimize_database()
            log_info(self.logger, "Database optimization completed successfully")
        except Exception as e:
            log_error(self.logger, f"Database optimization failed: {e}", exc_info=True)
            raise

    def _process_xml_file_update_operations(self, operations_by_type):
        """Process XML file update operations using metadata"""
        if DatabaseOperationType.XML_FILE_UPDATE.value not in operations_by_type:
            return

        xml_updates = operations_by_type[DatabaseOperationType.XML_FILE_UPDATE.value]

        for operation in xml_updates:
            xml_id = operation.xml_id
            metadata = operation.data

            try:
                self._update_xml_file_with_metadata(xml_id, metadata)
            except Exception as e:
                log_error(self.logger, f"Failed to update XML file {xml_id} with metadata: {e}")

    def _process_charity_operations(self, operations_by_type):
        """Process charity insert operations and return charity_id mapping"""
        charity_id_map = {}  # xml_id -> charity_id

        if DatabaseOperationType.INSERT_CHARITY.value not in operations_by_type:
            return charity_id_map

        charities = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]]

        if not charities:
            return charity_id_map

        log_info(self.logger, f"Bulk insert: Processing {len(charities)} charities")

        # Set colocator to 'notyet' for new charities
        for charity in charities:
            charity.colocator = 'notyet'

        try:
            # Use database_operations bulk_insert method which calls prep_for_insert
            ids = self.db_ops.bulk_insert(charities)
            log_info(self.logger, f"Bulk insert: Inserted {len(charities)} charities")

            # Map xml_id to charity_id using the operations list and returned IDs
            charity_ops = operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]
            for i, charity_id in enumerate(ids):
                if i < len(charity_ops):
                    xml_id = charity_ops[i].xml_id
                    charity_id_map[xml_id] = charity_id

        except Exception as e:
            log_error(self.logger, f"Failed to insert charities: {e}", exc_info=True)
            raise

        return charity_id_map

    def _process_related_operations(self, operations_by_type, charity_id_map):
        """Process related data operations (officers, grants, contractors, contributions)"""

        # Process officers
        if DatabaseOperationType.INSERT_OFFICER.value in operations_by_type:
            officers = []
            for operation in operations_by_type[DatabaseOperationType.INSERT_OFFICER.value]:
                officer = operation.data
                # Set charity_id from mapping
                if operation.xml_id in charity_id_map:
                    officer.charity_id = charity_id_map[operation.xml_id]
                    officers.append(officer)

            if officers:
                try:
                    # Use database_operations bulk_insert method which calls prep_for_insert
                    ids = self.db_ops.bulk_insert(officers)
                    log_debug(self.logger, f"Inserted {len(officers)} officers")
                except Exception as e:
                    log_error(self.logger, f"Failed to insert officers: {e}", exc_info=True)

        # Process grants
        if DatabaseOperationType.INSERT_GRANT.value in operations_by_type:
            grants = []
            for operation in operations_by_type[DatabaseOperationType.INSERT_GRANT.value]:
                grant = operation.data
                # Ensure grantee_name is not None
                if grant.grantee_name is None:
                    grant.grantee_name = "Unknown"
                grants.append(grant)

            if grants:
                try:
                    # Use database_operations bulk_insert method which calls prep_for_insert
                    ids = self.db_ops.bulk_insert(grants)
                    log_debug(self.logger, f"Inserted {len(grants)} grants")
                except Exception as e:
                    log_error(self.logger, f"Failed to insert grants: {e}", exc_info=True)

        # Process contractors
        if DatabaseOperationType.INSERT_CONTRACTOR.value in operations_by_type:
            contractors = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_CONTRACTOR.value]]

            if contractors:
                try:
                    # Use database_operations bulk_insert method which calls prep_for_insert
                    ids = self.db_ops.bulk_insert(contractors)
                    log_debug(self.logger, f"Inserted {len(contractors)} contractors")
                except Exception as e:
                    log_error(self.logger, f"Failed to insert contractors: {e}", exc_info=True)

        # Process political contributions
        if DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION.value in operations_by_type:
            contributions = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION.value]]

            if contributions:
                try:
                    # Use database_operations bulk_insert method which calls prep_for_insert
                    ids = self.db_ops.bulk_insert(contributions)
                    log_debug(self.logger, f"Inserted {len(contributions)} contributions")
                except Exception as e:
                    log_error(self.logger, f"Failed to insert contributions: {e}", exc_info=True)

    def _process_address_operations(self, operations_by_type, charity_id_map):
        """Process address insert operations"""

        if DatabaseOperationType.INSERT_ADDRESS.value not in operations_by_type:
            return

        addresses = []
        for operation in operations_by_type[DatabaseOperationType.INSERT_ADDRESS.value]:
            address = operation.data

            # Set owner_id for charity addresses (link to charity that owns this address)
            if hasattr(address, 'address_type') and address.address_type == 'charity':
                if operation.xml_id in charity_id_map:
                    address.owner_id = charity_id_map[operation.xml_id]

            addresses.append(address)

        if addresses:
            try:
                # Use database_operations bulk_insert method which calls prep_for_insert
                ids = self.db_ops.bulk_insert(addresses)
                log_debug(self.logger, f"Inserted {len(addresses)} addresses")
            except Exception as e:
                log_error(self.logger, f"Failed to insert addresses: {e}", exc_info=True)

    def _update_xml_file_with_metadata(self, xml_id, metadata):
        """Update XmlFiles table with metadata from processing"""
        try:
            if metadata["error_message"]:
                # Error case
                if metadata["form_type"] == "990T":
                    self.db_ops.execute_query(
                        "UPDATE XmlFiles SET processed=?, processing_version=?, error_message=?, form_type=?, file_size=? WHERE xml_id=?",
                        (metadata["processed"], 1, metadata["error_message"], metadata["form_type"], metadata["file_size"], xml_id)
                    )
                else:
                    self.db_ops.execute_query(
                        "UPDATE XmlFiles SET processed=?, processing_version=?, error_message=?, file_size=? WHERE xml_id=?",
                        (metadata["processed"], 1, metadata["error_message"], metadata["file_size"], xml_id)
                    )
            else:
                # Success case
                self.db_ops.execute_query(
                    "UPDATE XmlFiles SET processed=?, processing_version=?, form_type=?, ein=?, tax_year=?, file_size=? WHERE xml_id=?",
                    (metadata["processed"], 1, metadata["form_type"], metadata["ein"], metadata["tax_year"], metadata["file_size"], xml_id)
                )
        except Exception as e:
            log_error(self.logger, f"Failed to update XML file {xml_id} with metadata: {e}")


class XMLProcessor:
    """
    XML Processor - Legacy wrapper for backward compatibility.

    DEPRECATED: This class is maintained for backward compatibility.
    New code should use XMLProducer and XMLConsumer classes directly.

    This class combines producer and consumer functionality for single-threaded processing.
    """

    # Class-level cache for ZIP file connections to avoid reopening
    # Each entry contains both the ZipFile and a per-ZIP lock for thread safety
    _zip_cache: Dict[str, Tuple[zipfile.ZipFile, threading.Lock]] = {}
    _zip_cache_lock = threading.Lock()

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1, quiet: bool = False):
        self.db_ops = db_ops
        self.processing_version = processing_version
        self.logger = logging.getLogger(__name__)
        self.quiet = quiet

        # Create producer and consumer instances
        self.producer = XMLProducer(db_ops, processing_version, quiet)
        self.consumer = XMLConsumer(db_ops, quiet)

    def process_single_xml_for_operations(self, xml_id: str, zip_id: str, filename: str, internal_path: str, file_size: int) -> Tuple[bool, List[DatabaseOperation]]:
        """
        DEPRECATED: Use XMLProducer.process_single_xml_for_operations instead.

        This method is maintained for backward compatibility.
        New code should use XMLProducer and XMLConsumer classes directly.
        """
        return self.producer.process_single_xml_for_operations(xml_id, zip_id, filename, internal_path, file_size)


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
                success, operations = self._process_single_xml(xml_file.xml_id, xml_file.zip_id, xml_file.filename, xml_file.internal_path)
                if success:
                    total_processed += 1
                    # Execute operations directly (for non-parallel processing)
                    for operation in operations:
                        if operation.operation_type == DatabaseOperationType.INSERT_CHARITY:
                            self.db_ops.insert_charity(operation.data)
                        elif operation.operation_type == DatabaseOperationType.INSERT_OFFICER:
                            self.db_ops.insert_officer(operation.data)
                        elif operation.operation_type == DatabaseOperationType.INSERT_GRANT:
                            self.db_ops.insert_grant(operation.data)
                        elif operation.operation_type == DatabaseOperationType.INSERT_CONTRACTOR:
                            self.db_ops.insert_contractor(operation.data)
                        elif operation.operation_type == DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION:
                            self.db_ops.insert_political_contribution(operation.data)
                        elif operation.operation_type == DatabaseOperationType.INSERT_ADDRESS:
                            self.db_ops.insert_address(operation.data)
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

    def _process_single_xml(self, xml_id: str, zip_id: str, filename: str, internal_path: str) -> Tuple[bool, List[DatabaseOperation]]:
        """Process a single XML file and return operations instead of executing them"""
        operations = []
        try:
            if not self.quiet:
                log_debug(self.logger, f"Processing XML {filename} (ID: {xml_id})")

            # Get ZIP file path from database
            zip_result = self.db_ops.execute_query("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (zip_id,)).fetchone()
            if not zip_result:
                if not self.quiet:
                    log_error(self.logger, f"No ZIP file found for xml_id {xml_id}")
                return False, operations
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

            # Collect XML EIN update operation instead of writing directly
            if filer_ein and filer_ein != "Unknown":
                operations.append(DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_EIN,
                    {"ein": filer_ein},
                    xml_id
                ))

            if not filer_ein or filer_ein == "Unknown":
                if not self.quiet:
                    log_error(self.logger, f"Skipping XML {filename}: invalid EIN {filer_ein}")
                return False, operations

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
                return False, operations

            # Collect database operations instead of executing them
            if charity:
                if not self.quiet:
                    log_debug(self.logger, f"Collecting charity operation for EIN {filer_ein}, tax_year {tax_year}")

                # Add charity insert operation
                operations.append(DatabaseOperation(
                    DatabaseOperationType.INSERT_CHARITY,
                    charity,
                    xml_id
                ))

                # Extract and collect address operation
                if address:
                    if not self.quiet:
                        log_debug(self.logger, f"Collecting address operation for EIN {filer_ein}")
                        log_info(self.logger, f"DEBUG: Address to insert: ein={address.ein}, canonical='{address.canonical_address}', po_box='{address.po_box}', colocator='{address.colocator}'")
                        log_info(self.logger, f"DEBUG: Address fields - canonical_address='{address.canonical_address}', po_box='{address.po_box}', colocator='{address.colocator}'")
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_ADDRESS,
                        address,
                        xml_id
                    ))
                else:
                    if not self.quiet:
                        log_info(self.logger, f"DEBUG: No address extracted for EIN {filer_ein} - address is None")

                # Collect related data operations
                for officer in officers:
                    if not self.quiet:
                        log_debug(self.logger, f"Collecting officer operation for officer {officer.first_name} {officer.last_name}")
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_OFFICER,
                        officer,
                        xml_id
                    ))

                for grant in grants:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_GRANT,
                        grant,
                        xml_id
                    ))

                for contractor in contractors:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_CONTRACTOR,
                        contractor,
                        xml_id
                    ))

                for contribution in contributions:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION,
                        contribution,
                        xml_id
                    ))

            if not self.quiet:
                log_debug(self.logger, f"Successfully parsed {filename}: charity={filer_ein}, grants={len(grants)}, officers={len(officers)}")
            return True, operations

        except Exception as e:
            if not self.quiet:
                log_error(self.logger, f"Failed to process XML {filename}: {e}")
            return False, operations

    def _extract_form_type(self, root) -> str:
        """Extract form type from XML"""
        for xpath in COMMON_XPATHS["form_type"]:
            try:
                result = xpath(root)
                if result:
                    return result[0].text
            except:
                continue
        return "Unknown"

    def _extract_tax_year(self, root) -> int:
        """Extract tax year from XML"""
        for xpath in COMMON_XPATHS["tax_year"]:
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
        for xpath in COMMON_XPATHS["filer_ein"]:
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
                # Open ZIP file and cache the connection with per-ZIP lock
                zip_ref = zipfile.ZipFile(zip_path, 'r')
                zip_lock = threading.Lock()
                self._zip_cache[zip_key] = (zip_ref, zip_lock)
                if not self.quiet:
                    print(f"Opened and cached ZIP connection for {zip_path}")

            zip_ref, zip_lock = self._zip_cache[zip_key]

        # Extract XML content from cached connection - NOW PROTECTED BY PER-ZIP LOCK
        with zip_lock:
            with zip_ref.open(internal_path) as xml_file:
                return xml_file.read()

    @classmethod
    def cleanup_zip_cache(cls):
        """Clean up cached ZIP connections"""
        with cls._zip_cache_lock:
            for zip_ref, zip_lock in cls._zip_cache.values():
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