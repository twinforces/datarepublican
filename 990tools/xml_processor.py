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
from base_processor import BaseProducer, BaseConsumer
import parse_990
import parse_990ez
import parse_990pf
from logging_utils import log_info, log_error, log_debug, log_warning, dump_traceback, get_logger
from typing import Optional, List, Tuple
from config import global_config
from constants import CURRENT_PROCESSING_VERSION
from datetime import datetime

# Import XPath configurations from xpaths.py
from xpaths import COMMON_XPATHS, XPATHS_990, XPATHS_990EZ, XPATHS_990PF, tostring


class XMLProducer(BaseProducer):
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
        super().__init__(db_ops, batch_size=100)  # XML processing uses smaller batches
        self.processing_version = processing_version

    def process_single_xml_for_operations(self, xml_id: str, zip_id: str, filename: str, internal_path: str, file_size: int) -> Tuple[bool, List[str]]:
        """
        Process a single XML file and execute operations directly.

        This method now directly executes database operations instead of returning
        DatabaseOperation objects, following the simplified PDC architecture.

        Args:
            xml_id: XML file ID
            zip_id: ZIP file ID containing the XML (UUID string)
            filename: XML filename
            internal_path: Path within ZIP file
            file_size: Size of XML file in bytes

        Returns:
            Tuple of (success: bool, ids: List[str] of inserted object IDs)
        """
        # Validate that zip_id is a UUID string, not a file path
        if zip_id and isinstance(zip_id, str) and ('/' in zip_id or '\\' in zip_id):
            raise ValueError(f"zip_id parameter contains file path instead of UUID: {zip_id}")

        # Create context for this XML file processing
        context = PendingDatabaseContext(xml_id=xml_id)

        success = self._process_single_xml_with_context(xml_id, zip_id, filename, internal_path, context)

        # Execute context objects directly to database and get IDs
        ids = context.save_to_database(self.db_ops)

        # Add XML_FILE_UPDATE operation to context instead of executing directly
        from database_operations import DatabaseOperation, DatabaseOperationType
        from constants import CURRENT_PROCESSING_VERSION

        metadata = {
            "file_size": file_size,
            "ein": context.getObjectsByType('charity')[0].ein if context.getObjectsByType('charity') else "Unknown",
            "tax_year": context.getObjectsByType('charity')[0].tax_year if context.getObjectsByType('charity') else None,
            "form_type": context.getObjectsByType('charity')[0].form_type if context.getObjectsByType('charity') else None,
            "error_message": context.error_message,  # Include error message if parsing failed
            "processed": True, # at least attempted
            "xml_id": xml_id,
            "processing_version": CURRENT_PROCESSING_VERSION
        }

        # If successful, populate metadata with actual parsed values from charity
        if not context.error_message and context.getObjectsByType('charity'):
            charity = context.getObjectsByType('charity')[0]
            metadata["form_type"] = charity.form_type
            metadata["tax_year"] = charity.tax_year
            metadata["ein"] = charity.ein
            metadata["org_type"] = charity.org_type
            metadata["error_message"] = "success"  # Set to "success" for successful processing

        # Create XML_FILE_UPDATE operation and add to context
        xml_update_op = DatabaseOperation(
            DatabaseOperationType.XML_FILE_UPDATE,
            metadata,
            xml_id=xml_id
        )
        context.addOperationToDatabase(xml_update_op)

        return success, ids

    def _get_work_batch(self, offset: int) -> List[Tuple[str, str, str, str, int]]:
        """Get a batch of XML files to process"""
        xml_files = self.db_ops.get_xml_files_to_process(
            processing_version=self.processing_version,
            max_files=self.batch_size,
            offset=offset
        )

        # Convert XMLFile objects to tuples for processing
        work_items = []
        for xml_file in xml_files:
            zip_path = self.db_ops.get_zip_file_path(xml_file.zip_id)
            if zip_path:
                work_items.append((
                    xml_file.xml_id,
                    xml_file.zip_id,
                    xml_file.filename,
                    xml_file.internal_path,
                    xml_file.file_size
                ))
        return work_items

    def _process_work_batch(self, batch: List[Tuple[str, str, str, str, int]]) -> List[str]:
        """Process a batch of XML files and return all inserted IDs"""
        all_ids = []
        for xml_id, zip_id, filename, internal_path, file_size in batch:
            success, ids = self.process_single_xml_for_operations(
                xml_id, zip_id, filename, internal_path, file_size
            )
            all_ids.extend(ids)
        return all_ids

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

            # Clear context at the start of each XML file processing to ensure isolation
            context.clear()

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
            if not global_config.is_quiet():
                log_info(self.logger, f"CREATING CHARITY: EIN {filer_ein}, tax_year {tax_year}, form_type {form_type}, xml_name {filename}")
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
                log_info(self.logger, f"PROCESSING COMPLETE: EIN {filer_ein} in {filename}: {counts['grant']} grants, {counts['contractor']} contractors, {counts['political_contribution']} contributions, {counts['officer']} officers, {counts['address']} addresses",
                          ein=filer_ein)

            if not global_config.is_quiet():
                log_debug(self.logger, f"SUCCESS: Parsed {filename}: charity={filer_ein}, grants={counts['grant']}, officers={counts['officer']}, contractors={counts['contractor']}, contributions={counts['political_contribution']}, addresses={counts['address']}")
            return True

        except Exception as e:
            if not global_config.is_quiet():
                log_error(self.logger, f"FAILED: XML {filename}: {e}")
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






class XMLConsumer(BaseConsumer):
    """
    XML Consumer - Handles database operations execution for XML processing.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.

    Uses the standardized BaseConsumer._process_operations_batch pattern.
    """

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        super().__init__(db_ops)
        self.processing_version = processing_version

    def _process_operations_batch(self, operations_by_type):
        """Process operations batch for XML consumer using standardized pattern"""
        # DEPRECATED: All operations are now handled by PendingDatabaseContext.save_to_database()
        # which executes all operations directly. This method should not exist.
        pass

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

    def _execute_progress_update_operation(self, operation):
        """Execute progress update operation"""
        # DEPRECATED: Progress updates should be handled by PDC, not individual operations
        # This method should not exist in the new PDC architecture.
        if operation.operation_type != DatabaseOperationType.PROGRESS_UPDATE:
            return

        from logging_utils import update_progress
        progress_count = operation.data.get("count", 0)
        self.log_debug(f"DEBUG: Processing PROGRESS_UPDATE operation with count={progress_count}")
        update_progress(n=progress_count)

    def _process_xml_file_update_operations(self, operations_by_type):
        """Process XML file update operations using bulk_update"""
        # DEPRECATED: XML file updates should be handled by PDC, not individual operations
        # This method should not exist in the new PDC architecture.
        if DatabaseOperationType.XML_FILE_UPDATE.value not in operations_by_type:
            return

        xml_updates = operations_by_type[DatabaseOperationType.XML_FILE_UPDATE.value]

        # Collect all updates for bulk operation
        updates = []
        update_dict={
                "xml_id": "A",
                "processed": False,
                "processing_version": self.processing_version,
                "file_size": -1,
                "processed_at": datetime.now().isoformat() ,
                "org_type": "None",
                "form_type": "no-form",
                "error_message": "whiz"
        }

        for operation in xml_updates:
            xml_id = operation.xml_id
            metadata = operation.data
            prev_update_dict = update_dict
            # Prepare update dictionary for bulk_update - include all required fields
            update_dict = {
                "xml_id": xml_id,
                "processed": metadata["processed"] or prev_update_dict["processed"],
                "processing_version": self.processing_version,
                "file_size": metadata["file_size"]  or prev_update_dict["file_size"],
                "processed_at": metadata.get("processed_at", prev_update_dict["processed_at"]) ,
                "org_type": metadata.get("org_type",  prev_update_dict["processed_at"]),
                "form_type": metadata.get("form_type",  prev_update_dict["form_type"]),
                "error_message": metadata.get("error_message")
            }

            # Set processed_at timestamp if not already set and processing is complete
            if update_dict["processed"] and update_dict["processed_at"] is None:
                update_dict["processed_at"] = datetime.now().isoformat()

            if metadata.get("error_message") != "success":
                # Error case: set error_message and clear success fields
                ##update_dict["form_type"] = None
                update_dict["ein"] = None
                ##update_dict["tax_year"] = None
            else:
                # Success case: set success fields and clear error_message
                update_dict["form_type"] = metadata.get("form_type")
                update_dict["ein"] = metadata.get("ein")
                update_dict["tax_year"] = metadata.get("tax_year")
            prev_update_dict={}
            updates.append(update_dict)

        # Execute bulk update
        if updates:
            try:
                self.db_ops.bulk_update("XmlFiles", updates, id_column="xml_id")
                log_debug(self.logger, f"Bulk updated {len(updates)} XML files")
            except Exception as e:
                log_error(self.logger, f"Failed to bulk update XML files: {e}")
                raise

    # _process_charity_operations method removed - now handled by BaseConsumer.execute_contexts_batch

    def _process_related_operations(self, operations_by_type, charity_id_map):
        """Process related data operations (officers, grants, contractors, contributions)"""
        # DEPRECATED: All object insertions should be handled by PDC, not individual operations
        # This method should not exist in the new PDC architecture.

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
                    # Use INSERT_BY_TYPE for officers (single type, no sorting needed)
                    ids = self.db_ops.INSERT_BY_TYPE(officers, 'Officer')
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
                    # Use INSERT_BY_TYPE for grants (single type, no sorting needed)
                    ids = self.db_ops.INSERT_BY_TYPE(grants, 'Grant')
                    log_debug(self.logger, f"Inserted {len(grants)} grants")
                except Exception as e:
                    log_error(self.logger, f"Failed to insert grants: {e}", exc_info=True)

        # Process contractors
        if DatabaseOperationType.INSERT_CONTRACTOR.value in operations_by_type:
            contractors = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_CONTRACTOR.value]]

            if contractors:
                try:
                    # Use INSERT_BY_TYPE for contractors (single type, no sorting needed)
                    ids = self.db_ops.INSERT_BY_TYPE(contractors, 'Contractor')
                    log_debug(self.logger, f"Inserted {len(contractors)} contractors")
                except Exception as e:
                    log_error(self.logger, f"Failed to insert contractors: {e}", exc_info=True)

        # Process political contributions
        if DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION.value in operations_by_type:
            contributions = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION.value]]

            if contributions:
                try:
                    # Use INSERT_BY_TYPE for political contributions (single type, no sorting needed)
                    ids = self.db_ops.INSERT_BY_TYPE(contributions, 'PoliticalContribution')
                    log_debug(self.logger, f"Inserted {len(contributions)} contributions")
                except Exception as e:
                    log_error(self.logger, f"Failed to insert contributions: {e}", exc_info=True)

    def _process_address_operations(self, operations_by_type, charity_id_map):
        """Process address insert operations"""
        # DEPRECATED: All object insertions should be handled by PDC, not individual operations
        # This method should not exist in the new PDC architecture.

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
                # Use INSERT_BY_TYPE for addresses (single type, no sorting needed)
                ids = self.db_ops.INSERT_BY_TYPE(addresses, 'Address')
                log_debug(self.logger, f"Inserted {len(addresses)} addresses")
            except Exception as e:
                log_error(self.logger, f"Failed to insert addresses: {e}", exc_info=True)

    def _process_generic_update_operations(self, operations_by_type):
        """Process generic update operations using bulk_update"""
        # DEPRECATED: All database updates should be handled by PDC, not individual operations
        # This method should not exist in the new PDC architecture.
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


class XMLProcessor:
    """
    XML Processor - Main entry point for XML processing operations.

    This class coordinates XML processing using the producer-consumer pattern.
    It can operate in both single-threaded and multi-threaded modes.
    """

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        self.db_ops = db_ops
        self.processing_version = processing_version
        self.logger = get_logger(self.__class__.__name__)

    def process_xml_files(self, max_files: Optional[int] = None, workers: int = 4) -> int:
        """
        Process XML files using parallel producer-consumer pattern.

        Args:
            max_files: Maximum number of files to process (for testing)
            workers: Number of worker threads for parallel processing

        Returns:
            Number of XML files processed
        """
        return self._process_xml_files_parallel(max_files, workers)

    def _process_xml_files_parallel(self, max_files: Optional[int] = None, workers: int = 4) -> int:
        """
        Process XML files using parallel producer-consumer pattern.

        This method implements the threading and coordination logic directly,
        eliminating the artificial processor/strategy split.
        """
        from constants import CURRENT_PROCESSING_VERSION
        from logging_utils import start_progress_reporting
        import signal
        import threading
        import queue

        # Setup signal handlers for graceful shutdown
        shutdown_event = threading.Event()
        def interrupt_handler(signum, frame):
            self.log_error("Received interrupt signal, shutting down gracefully...")
            shutdown_event.set()

        signal.signal(signal.SIGINT, interrupt_handler)

        # Get total count for progress bar
        if max_files is None:
            total_files_query = """
                SELECT COUNT(*) FROM XmlFiles
                WHERE processed = FALSE OR processing_version < ?
            """
            result = self.db_ops.execute_query(total_files_query, (CURRENT_PROCESSING_VERSION,))
            total_files = result.fetchone()[0] if result else 0
        else:
            total_files = max_files

        # Setup progress bar
        progress_unit = "file" if global_config.progress == "files" else "B"
        progress_desc = "Processing XML files" if global_config.progress == "files" else "XML bytes"
        pbar = start_progress_reporting(total=total_files, desc=progress_desc, unit=progress_unit)

        # Create producer and consumer instances
        producer = XMLProducer(self.db_ops, CURRENT_PROCESSING_VERSION)
        consumer = XMLConsumer(self.db_ops, CURRENT_PROCESSING_VERSION)

        # Setup thread pool configuration
        from base_processor import ThreadPoolConfig, PoolConfig
        producer_config = PoolConfig(max_workers=workers, queue_size=1000, batch_size=100)
        thread_pool_config = ThreadPoolConfig(producer_config=producer_config)

        # Initialize thread pool manager
        from base_processor import ThreadPoolManager
        thread_pool_manager = ThreadPoolManager(thread_pool_config, self.logger)

        total_processed = 0

        try:
            # Process batches until we reach max_files or run out of files
            while True:
                # Calculate remaining files for this batch
                remaining_files = None if max_files is None else max_files - total_processed
                batch_limit = 100 if remaining_files is None else min(remaining_files, 100)

                # Get next batch of files
                xml_files = self.db_ops.get_xml_files_to_process(
                    processing_version=CURRENT_PROCESSING_VERSION,
                    max_files=batch_limit
                )
                if not xml_files:
                    break

                batch_size = len(xml_files)
                self.log_info(f"Processing batch of {batch_size} files with {workers} workers")

                # Start producer pool
                thread_pool_manager.start_producer_pool(
                    xml_files,
                    self._xml_producer_worker
                )

                # Start single consumer thread
                consumer_thread = threading.Thread(
                    target=self._xml_consumer_worker,
                    args=(thread_pool_manager.result_queue, consumer, pbar, shutdown_event)
                )
                consumer_thread.daemon = False
                consumer_thread.start()

                # Wait for completion
                self._wait_for_completion(thread_pool_manager, consumer_thread)

                # Update total processed
                total_processed += batch_size

                # Check limits
                if max_files and total_processed >= max_files:
                    break

        finally:
            # Cleanup
            if pbar:
                pbar.close()

            # Clean up ZIP cache
            XMLProducer.cleanup_zip_cache()

        self.log_info(f"XML processing complete: {total_processed} files processed")
        return total_processed

    def _xml_producer_worker(self, xml_files, work_queue, result_queue, producer_id, num_producers):
        """Producer worker function for thread pool"""
        processed = 0
        total_bytes = 0

        # Create producer instance for this thread
        from constants import CURRENT_PROCESSING_VERSION
        producer = XMLProducer(self.db_ops, CURRENT_PROCESSING_VERSION)

        for i in range(producer_id, len(xml_files), num_producers):
            if shutdown_event.is_set():
                break

            xml_file = xml_files[i]
            xml_id = xml_file.xml_id
            zip_id = xml_file.zip_id
            zip_path = self.db_ops.get_zip_file_path(zip_id)
            if not zip_path:
                continue

            filename = xml_file.filename
            internal_path = xml_file.internal_path
            file_size = xml_file.file_size

            try:
                # Parse XML and collect operations
                success, operations = producer.process_single_xml_for_operations(
                    xml_id, zip_id, filename, internal_path, file_size
                )

                # Send operations to consumer
                for operation in operations:
                    result_queue.put(operation)

                processed += 1

                # Track progress
                if global_config.progress == "bytes" and file_size:
                    total_bytes += file_size

                if processed % 50 == 0:
                    progress_info = f"{processed} files" if global_config.progress == "files" else f"{total_bytes} bytes"
                    self.log_info(f"Producer {producer_id}: {progress_info} queued")

            except Exception as e:
                self.log_error(f"Producer {producer_id} error on {filename}: {e}")

        # Send sentinel
        result_queue.put(None)

    def _xml_consumer_worker(self, operations_queue, consumer, pbar, shutdown_event):
        """Consumer worker function"""
        batch_operations = []
        processed_xml_count = 0

        sentinels_received = 0
        num_producers = len(thread_pool_manager.producer_threads) if thread_pool_manager else 1

        while sentinels_received < num_producers:
            try:
                item = operations_queue.get(timeout=1.0)
                if item is None:
                    sentinels_received += 1
                    continue

                if isinstance(item, DatabaseOperation):
                    batch_operations.append(item)

                if len(batch_operations) >= 100:
                    # Execute batch
                    consumer.execute_operations_batch(batch_operations)
                    batch_operations = []

                operations_queue.task_done()

            except queue.Empty:
                if shutdown_event.is_set():
                    break

        # Final batch
        if batch_operations:
            consumer.execute_operations_batch(batch_operations)

    def _wait_for_completion(self, thread_pool_manager, consumer_thread):
        """Wait for producer and consumer threads to complete"""
        # Wait for producers
        for thread in thread_pool_manager.producer_threads:
            thread.join()

        # Wait for consumer
        if consumer_thread and consumer_thread.is_alive():
            consumer_thread.join()

