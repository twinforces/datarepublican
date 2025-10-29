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

from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from pending_database_context import PendingDatabaseContext
import parse_990
import parse_990ez
import parse_990pf
from logging_utils import log_info, log_error, log_debug, log_warning
from typing import Optional, List, Tuple
from config import global_config
from constants import CURRENT_PROCESSING_VERSION

# Import XPath configurations from xpaths.py
from xpaths import COMMON_XPATHS, XPATHS_990, XPATHS_990EZ, XPATHS_990PF, tostring


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

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        self.db_ops = db_ops
        self.processing_version = processing_version
        self.logger = logging.getLogger(__name__)

    def process_single_xml_for_operations(self, xml_id: str, zip_id: str, filename: str, internal_path: str, file_size: int) -> Tuple[bool, List[DatabaseOperation]]:
        """
        Process a single XML file and return operations for producer-consumer pattern.

        PRODUCER-CONSUMER PATTERN: This method collects operations but does NOT execute them.
        All database operations are returned as DatabaseOperation objects for the consumer to handle.

        Args:
            xml_id: XML file ID
            zip_id: ZIP file ID containing the XML (UUID string)
            filename: XML filename
            internal_path: Path within ZIP file
            file_size: Size of XML file in bytes

        Returns:
            Tuple of (success: bool, operations: List[DatabaseOperation])
        """
        # Validate that zip_id is a UUID string, not a file path
        if zip_id and isinstance(zip_id, str) and ('/' in zip_id or '\\' in zip_id):
            raise ValueError(f"zip_id parameter contains file path instead of UUID: {zip_id}")

        # Create context for this XML file processing
        context = PendingDatabaseContext(xml_id=xml_id)

        success = self._process_single_xml_with_context(xml_id, zip_id, filename, internal_path, context)

        # Convert context objects to database operations (including XML_FILE_UPDATE)
        operations = context.save_to_database(self.db_ops)

        # Update the XML_FILE_UPDATE operation with file_size from caller
        for operation in operations:
            if operation.operation_type == DatabaseOperationType.XML_FILE_UPDATE:
                operation.data["file_size"] = file_size
                break

        return success, operations

    def process_single_xml_with_context(self, xml_id: str, zip_id: str, filename: str, internal_path: str, file_size: int) -> Tuple[bool, PendingDatabaseContext]:
        """
        Process a single XML file using the new context-based approach.

        Args:
            xml_id: XML file ID
            zip_id: ZIP file ID containing the XML (UUID string)
            filename: XML filename
            internal_path: Path within ZIP file
            file_size: Size of XML file in bytes

        Returns:
            Tuple of (success: bool, context: PendingDatabaseContext)
        """
        # Validate that zip_id is a UUID string, not a file path
        if zip_id and isinstance(zip_id, str) and ('/' in zip_id or '\\' in zip_id):
            raise ValueError(f"zip_id parameter contains file path instead of UUID: {zip_id}")

        context = PendingDatabaseContext()
        success = self._process_single_xml_with_context(xml_id, zip_id, filename, internal_path, context)

        return success, context

    def _process_single_xml_with_context(self, xml_id: str, zip_id: str, filename: str, internal_path: str, context: PendingDatabaseContext) -> bool:
        """
        Process a single XML file using the context-based approach.

        Args:
            xml_id: XML file ID
            zip_id: ZIP file ID (UUID string)
            filename: XML filename
            internal_path: Path within ZIP
            context: PendingDatabaseContext to collect objects

        Returns:
            bool: Success status
        """
        try:
            if not global_config.is_quiet():
                log_debug(self.logger, f"Processing XML {filename} (ID: {xml_id})")

            # Validate zip_id is not a file path
            if zip_id and isinstance(zip_id, str) and ('/' in zip_id or '\\' in zip_id):
                raise ValueError(f"zip_id parameter contains file path instead of UUID: {zip_id}")

            # Get ZIP file path from cache
            zip_path = self.db_ops.get_zip_file_path(zip_id)
            if not zip_path:
                if not global_config.is_quiet():
                    log_error(self.logger, f"No ZIP file found for xml_id {xml_id}")
                return False

            # Extract XML content from ZIP using cached connection
            xml_content = self._extract_xml_from_zip(zip_path, internal_path)

            if not global_config.is_quiet():
                log_debug(self.logger, f"Extracted XML content for {filename}, size: {len(xml_content)} bytes")

            # Parse XML
            parser = etree.XMLParser(recover=True)
            tree = etree.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            if not global_config.is_quiet():
                log_debug(self.logger, f"Extracted metadata for {filename}: form_type={form_type}, tax_year={tax_year}, ein={filer_ein}")

            if not filer_ein or filer_ein == "Unknown":
                if not global_config.is_quiet():
                    log_error(self.logger, f"DEBUG: Skipping XML {filename}: invalid EIN {filer_ein} - this will create NULL EIN Charity!")
                return False

            # Create charity object first
            charity = Charity(
                ein=filer_ein,
                tax_year=tax_year,
                form_type=form_type,
                xml_name=filename
            )
            context.addObjectToDatabase(charity)

            # Use base parser factory to get the correct parser and parse the form
            from base_parser import BaseParser
            parser = BaseParser.create_parser(form_type)
            if parser:
                parser.parse_form(root, filename, {}, context)
            else:
                if not global_config.is_quiet():
                    log_info(self.logger, f"Unsupported form type {form_type} in {filename}")
                return False

            # Log grant collection results
            counts = context.getObjectCounts()
            if not global_config.is_quiet():
                log_info(self.logger, f"Grant collection completed for EIN {filer_ein} in {filename}: {counts['grant']} grants, {counts['contractor']} contractors, {counts['political_contribution']} contributions",
                          ein=filer_ein)

            if not global_config.is_quiet():
                log_debug(self.logger, f"Successfully parsed {filename}: charity={filer_ein}, grants={counts['grant']}, officers={counts['officer']}")
            return True

        except Exception as e:
            if not global_config.is_quiet():
                log_error(self.logger, f"Failed to process XML {filename}: {e}")
            # Store error message with stack trace in context instead of direct DB update
            import traceback
            error_msg = f"{str(e)}\n\nStack Trace:\n{traceback.format_exc()}"
            context.error_message = error_msg
            return False


    # XMLProducer methods (shared implementation)
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
                    if not global_config.is_quiet():
                        log_info(self.logger, f"TRACE: Found raw EIN: '{raw_ein}' using xpath: {xpath.path}")
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        if not global_config.is_quiet():
                            log_info(self.logger, f"TRACE: Formatted EIN: '{formatted_ein}' (valid 9-digit)")
                        return formatted_ein
                    else:
                        if not global_config.is_quiet():
                            log_error(self.logger, f"TRACE: Non-digit EIN found: '{raw_ein}', raising exception")
                        raise ValueError(f"Invalid EIN format: '{raw_ein}' - must be numeric")
            except Exception as e:
                self.logger.debug(f"XPath {xpath.path} failed: {e}")
                continue
        if not global_config.is_quiet():
            log_error(self.logger, "TRACE: No EIN found in XML, raising exception")
        raise ValueError("No EIN found in XML")


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




class XMLConsumer:
    """
    XML Consumer - Handles database operations execution.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.

    If you need to modify this class to handle new operation types,
    ensure all database writes go through the bulk operation methods.
    """

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        self.db_ops = db_ops
        self.logger = logging.getLogger(__name__)
        self.processing_version = processing_version

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

            # 5. Handle generic update operations
            self._process_generic_update_operations(operations_by_type)

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
        """Process XML file update operations using bulk_update"""
        if DatabaseOperationType.XML_FILE_UPDATE.value not in operations_by_type:
            return

        xml_updates = operations_by_type[DatabaseOperationType.XML_FILE_UPDATE.value]

        # Collect all updates for bulk operation
        updates = []
        for operation in xml_updates:
            xml_id = operation.xml_id
            metadata = operation.data

            # Prepare update dictionary for bulk_update
            update_dict = {
                "xml_id": xml_id,
                "processed": metadata["processed"],
                "processing_version": self.processing_version,
                "file_size": metadata["file_size"]
            }

            if metadata.get("error_message"):
                # Error case: set error_message and clear success fields
                update_dict["error_message"] = metadata["error_message"]
                update_dict["form_type"] = None
                update_dict["ein"] = None
                update_dict["tax_year"] = None
            else:
                # Success case: set success fields and clear error_message
                update_dict["error_message"] = None
                update_dict["form_type"] = metadata.get("form_type")
                update_dict["ein"] = metadata.get("ein")
                update_dict["tax_year"] = metadata.get("tax_year")

            updates.append(update_dict)

        # Execute bulk update
        if updates:
            try:
                self.db_ops.bulk_update("XmlFiles", updates, id_column="xml_id")
                log_debug(self.logger, f"Bulk updated {len(updates)} XML files")
            except Exception as e:
                log_error(self.logger, f"Failed to bulk update XML files: {e}")
                raise

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
            # Check if this is a duplicate constraint violation
            error_str = str(e)
            if "Constraint Error: Duplicate key" in error_str and "xml_name:" in error_str:
                log_warning(self.logger, f"Duplicate charity constraint violation detected: {e}")
                # Mark XML files as processed with duplicate status
                charity_ops = operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]
                for charity_op in charity_ops:
                    xml_id = charity_op.xml_id
                    # Create XML_FILE_UPDATE operation for duplicate
                    if DatabaseOperationType.XML_FILE_UPDATE.value not in operations_by_type:
                        operations_by_type[DatabaseOperationType.XML_FILE_UPDATE.value] = []
                    operations_by_type[DatabaseOperationType.XML_FILE_UPDATE.value].append(
                        DatabaseOperation(
                            operation_type=DatabaseOperationType.XML_FILE_UPDATE,
                            xml_id=xml_id,
                            data={
                                "processed": True,
                                "processing_version": CURRENT_PROCESSING_VERSION,
                                "error_message": "duplicate"
                            }
                        )
                    )
                log_info(self.logger, f"Marked {len(charity_ops)} XML files as duplicate")
                # Return empty map since no charities were inserted
                return {}
            else:
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

    def _process_generic_update_operations(self, operations_by_type):
        """Process generic update operations using bulk_update"""
        if DatabaseOperationType.GENERIC_UPDATE.value not in operations_by_type:
            return

        generic_updates = operations_by_type[DatabaseOperationType.GENERIC_UPDATE.value]

        # Group updates by table name
        updates_by_table = {}
        for operation in generic_updates:
            table_name = operation.data.get("table_name")
            update_data = operation.data.get("update_data", {})
            id_column = operation.data.get("id_column", "id")

            if table_name not in updates_by_table:
                updates_by_table[table_name] = {"updates": [], "id_column": id_column}

            updates_by_table[table_name]["updates"].append(update_data)

        # Execute bulk updates for each table
        for table_name, table_data in updates_by_table.items():
            updates = table_data["updates"]
            id_column = table_data["id_column"]

            if updates:
                try:
                    self.db_ops.bulk_update(table_name, updates, id_column=id_column)
                    log_debug(self.logger, f"Bulk updated {len(updates)} records in {table_name}")
                except Exception as e:
                    log_error(self.logger, f"Failed to bulk update {table_name}: {e}")
                    raise


