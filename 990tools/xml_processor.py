#!/usr/bin/env python3
"""
xml_processor.py - XML file processing for IRS 990 data

This module handles the parsing and processing of IRS 990 XML files,
extracting data into dataclasses and storing in the database.
"""

import time

import zipfile
import threading
import queue
from io import BytesIO
from typing import Optional, List, Tuple, Dict, Any
from lxml import etree  # type: ignore
import psutil

from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from pending_database_context import PendingDatabaseContext
from base_processor import BaseProcessor, BaseProducer, BaseConsumer
import parse_990
import parse_990ez
import parse_990pf
from logging_utils import log_info, log_error, log_debug, log_warning, dump_traceback
from typing import Optional, List, Tuple
from config import global_config
from constants import CURRENT_PROCESSING_VERSION, CONSUMER_BATCH_SIZE, MONITOR_INTERVAL_SECONDS
from datetime import datetime

from collections import deque

# Import XPath configurations from xpaths.py
from xpaths import COMMON_XPATHS, tostring
from xpaths_990 import XPATHS_990
from xpaths_990ez import XPATHS_990EZ
from xpaths_990pf import XPATHS_990PF


class XMLProducer(BaseProducer):
    """
    XML Producer - Handles XML parsing and operation collection.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect PendingDatabaseContext objects and send them to consumers.
    Only Consumer classes may execute database operations.

    If you need to add database writes here, you are violating the pattern.
    Instead, add objects/operations to the context for the consumer to handle.
    """

    # Class-level cache for ZIP file connections to avoid reopening
    # Each entry contains both the ZipFile and a per-ZIP lock for thread safety
    _zip_cache: Dict[str, Tuple[zipfile.ZipFile, threading.Lock]] = {}
    _zip_cache_lock = threading.Lock()
    _zip_cache_ref_count: Dict[str, int] = {}  # Reference counting for proper cleanup

    # Content cache removed - XML files are loaded once and discarded

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        super().__init__(db_ops, batch_size=100)  # XML processing uses smaller batches
        self.processing_version = processing_version

    def process_single_xml_for_operations(self, xml_id: str, zip_id: str, filename: str, internal_path: str, file_size: int) -> Tuple[bool, PendingDatabaseContext]:
        """
        Process a single XML file and return the PendingDatabaseContext.

        This method processes the XML and collects all database operations in a context
        for the consumer to execute.

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

        # Create context for this XML file processing
        context = PendingDatabaseContext(xml_id=xml_id)

        success = self._process_single_xml_with_context(xml_id, zip_id, filename, internal_path, context)

        # Add XML_FILE_UPDATE operation to context
        from database_operations import DatabaseOperation, DatabaseOperationType
        from constants import CURRENT_PROCESSING_VERSION
        from config import global_config

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

        # Add PROGRESS_UPDATE operation per XML file
        progress_data = {"bytes": file_size} if global_config.progress == "bytes" else {"count": 1}
        progress_op = DatabaseOperation(
            DatabaseOperationType.PROGRESS_UPDATE,
            progress_data
        )
        context.addOperationToDatabase(progress_op)

        return success, context

    def _get_work_batch(self, last_xml_id: Optional[str] = None) -> Tuple[List[Tuple[str, str, str, str, int]], Optional[str]]:
        """Get a batch of XML files to process with key-value paging using xml_id as primary key"""
        super()._get_work_batch(last_xml_id)  # Ensure base behavior if needed

        xml_files = self.db_ops.get_xml_files_to_process(
            processing_version=self.processing_version,
            max_files=self.batch_size,
            last_xml_id=last_xml_id
        )

        # Convert XMLFile objects to tuples for processing
        work_items = []
        max_xml_id = None
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
                if xml_file.xml_id > max_xml_id:
                    max_xml_id = xml_file.xml_id

        return work_items, max_xml_id

    def _process_work_batch_to_context(self, batch: List[Tuple[str, str, str, str, int]]) -> PendingDatabaseContext:
        """Process a batch of XML files into a single PendingDatabaseContext (no DB writes)"""
        super()._process_work_batch_to_context(batch)  # Ensure base behavior if needed

        # Create a single context for the batch
        from pending_database_context import PendingDatabaseContext
        successful_contexts = []

        for xml_id, zip_id, filename, internal_path, file_size in batch:
            success, single_context = self.process_single_xml_for_operations(
                xml_id, zip_id, filename, internal_path, file_size
            )
            if success:
                successful_contexts.append(single_context)

        if successful_contexts:
            return self.merge_pending_contexts(successful_contexts)
        else:
            return PendingDatabaseContext()

    def process_single_xml_with_context(self, xml_id: str, zip_id: str, filename: str, internal_path: str, file_size: int) -> Tuple[bool, PendingDatabaseContext]:
        """
        Process a single XML file using the new context-based approach.

        Args:
            xml_id: XML file ID
            zip_id: ZIP file ID (UUID string)
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
                log_debug(f"Processing XML {filename} (ID: {xml_id})")

            # Validate zip_id is not a file path
            if zip_id and isinstance(zip_id, str) and ('/' in zip_id or '\\' in zip_id):
                raise ValueError(f"zip_id parameter contains file path instead of UUID: {zip_id}")

            # Clear context at the start of each XML file processing to ensure isolation
            context.clear()

            # Get ZIP file path from cache
            zip_path = self.db_ops.get_zip_file_path(zip_id)
            if not zip_path:
                if not global_config.is_quiet():
                    log_error(f"No ZIP file found for xml_id {xml_id}")
                return False

            # Extract XML content from ZIP using cached connection
            xml_content = self._extract_xml_from_zip(zip_path, internal_path)

            if not global_config.is_quiet():
                log_debug(f"Extracted XML content for {filename}, size: {len(xml_content)} bytes")

            # Parse XML
            parser = etree.XMLParser(recover=True)
            tree = etree.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Check exit_processing flag before continuing with expensive operations
            if self.exit_processing:
                log_info(f"Exiting thread due to exit_processing flag")
                return False

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            if not global_config.is_quiet():
                log_debug(f"Extracted metadata for {filename}: form_type={form_type}, tax_year={tax_year}, ein={filer_ein}")

            if not filer_ein or filer_ein == "Unknown":
                if not global_config.is_quiet():
                    log_error(f"DEBUG: Skipping XML {filename}: invalid EIN {filer_ein} - this will create NULL EIN Charity!")
                return False

            # Create charity object first
            charity = Charity(
                ein=filer_ein,
                tax_year=tax_year,
                form_type=form_type,
                xml_name=filename
            )
            # Force ID generation before adding to context and parsing dependents
            charity.prep_for_insert()
            if not global_config.is_quiet():
                log_info(f"CREATING CHARITY: EIN {filer_ein}, tax_year {tax_year}, form_type {form_type}, xml_name {filename}")
            context.addObjectToDatabase(charity)

            # Use base parser factory to get the correct parser and parse the form
            from base_parser import BaseParser
            parser = BaseParser.create_parser(form_type)
            if parser:
                parser.parse_form(root, filename, {}, context)
            else:
                if not global_config.is_quiet():
                    log_info(f"Unsupported form type {form_type} in {filename}")
                return False

            # Explicit cleanup after parsing
            del root, tree

            # Log grant collection results
            counts = context.getObjectCounts()
            if not global_config.is_quiet():
                log_info(f"PROCESSING COMPLETE: EIN {filer_ein} in {filename}: {counts['grant']} grants, {counts['contractor']} contractors, {counts['political_contribution']} contributions, {counts['officer']} officers, {counts['address']} addresses",
                          ein=filer_ein)

            if not global_config.is_quiet():
                log_debug(f"SUCCESS: Parsed {filename}: charity={filer_ein}, grants={counts['grant']}, officers={counts['officer']}, contractors={counts['contractor']}, contributions={counts['political_contribution']}, addresses={counts['address']}")
            return True

        except etree.XMLSyntaxError as e:
            if not global_config.is_quiet():
                log_error(f"FAILED: XML {filename}: XML syntax error - {e}")
            # Store specific error message for XML parsing issues
            import traceback
            error_msg = f"XML Syntax Error: {str(e)}\n\nStack Trace:\n{traceback.format_exc()}"
            context.error_message = error_msg
            return False
        except etree.ParseError as e:
            if not global_config.is_quiet():
                log_error(f"FAILED: XML {filename}: XML parse error - {e}")
            # Store specific error message for XML parsing issues
            import traceback
            error_msg = f"XML Parse Error: {str(e)}\n\nStack Trace:\n{traceback.format_exc()}"
            context.error_message = error_msg
            return False
        except zipfile.BadZipFile as e:
            if not global_config.is_quiet():
                log_error(f"FAILED: XML {filename}: Bad ZIP file - {e}")
            # Store specific error message for ZIP file issues
            import traceback
            error_msg = f"ZIP File Error: {str(e)}\n\nStack Trace:\n{traceback.format_exc()}"
            context.error_message = error_msg
            return False
        except ValueError as e:
            if not global_config.is_quiet():
                log_error(f"FAILED: XML {filename}: Value error - {e}")
            # Store specific error message for validation issues
            import traceback
            error_msg = f"Validation Error: {str(e)}\n\nStack Trace:\n{traceback.format_exc()}"
            context.error_message = error_msg
            return False
        except Exception as e:
            if not global_config.is_quiet():
                log_error(f"FAILED: XML {filename}: Unexpected error - {e}")
            # Store error message with stack trace in context for unexpected errors
            import traceback
            error_msg = f"Unexpected Error: {str(e)}\n\nStack Trace:\n{traceback.format_exc()}"
            context.error_message = error_msg
            return False


    # XMLProducer methods (shared implementation)
    def _extract_form_type(self, root) -> str:
        """Extract form type from XML with validation"""
        for xpath in COMMON_XPATHS["form_type"]:
            try:
                result = xpath(root)
                if result and result[0].text:
                    form_type = result[0].text.strip()
                    # Validate against known IRS form types
                    valid_forms = {"990", "990EZ", "990PF", "990T"}
                    if form_type in valid_forms:
                        return form_type
                    else:
                        # Log invalid form type but continue processing
                        if not global_config.is_quiet():
                            log_warning(f"Invalid form type '{form_type}' found, treating as Unknown")
                        return "Unknown"
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
                        log_info(f"TRACE: Found raw EIN: '{raw_ein}' using xpath: {xpath.path}")
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        if not global_config.is_quiet():
                            log_info(f"TRACE: Formatted EIN: '{formatted_ein}' (valid 9-digit)")
                        return formatted_ein
                    else:
                        if not global_config.is_quiet():
                            log_error(f"TRACE: Non-digit EIN found: '{raw_ein}', raising exception")
                        raise ValueError(f"Invalid EIN format: '{raw_ein}' - must be numeric")
            except Exception as e:
                log_debug(f"XPath {xpath.path} failed: {e}")
                continue
        if not global_config.is_quiet():
            log_error("TRACE: No EIN found in XML, raising exception")
        raise ValueError("No EIN found in XML")


    def _extract_xml_from_zip(self, zip_path: str, internal_path: str) -> bytes:
        """Extract XML content from ZIP using cached connection with reference counting"""
        import threading
        zip_key = str(zip_path)
        thread_id = threading.get_ident()

        # Extract from ZIP - no content caching since XML files are processed once
        log_debug(f"Thread {thread_id}: Requesting ZIP access for {zip_path} (key: {zip_key})")

        with self._zip_cache_lock:
            log_debug(f"Thread {thread_id}: Acquired ZIP cache lock for {zip_path}")
            if zip_key not in self._zip_cache:
                # Open ZIP file and cache the connection with per-ZIP lock
                log_info(f"Thread {thread_id}: Opening new ZIP connection for {zip_path}")
                zip_ref = zipfile.ZipFile(zip_path, 'r')
                zip_lock = threading.Lock()
                self._zip_cache[zip_key] = (zip_ref, zip_lock)
                self._zip_cache_ref_count[zip_key] = 1
                log_info(f"Thread {thread_id}: Opened and cached ZIP connection for {zip_path}")
            else:
                # Increment reference count for existing connection
                self._zip_cache_ref_count[zip_key] += 1
                log_debug(f"Thread {thread_id}: Using cached ZIP connection for {zip_path} (ref_count: {self._zip_cache_ref_count[zip_key]})")

            zip_ref, zip_lock = self._zip_cache[zip_key]
            log_debug(f"Thread {thread_id}: Retrieved ZIP reference from cache for {zip_path}")
            log_debug(f"Thread {thread_id}: Releasing ZIP cache lock for {zip_path}")

        # Extract XML content from cached connection - NOW PROTECTED BY PER-ZIP LOCK
        log_debug(f"Thread {thread_id}: Starting extraction of {internal_path} from {zip_path} (acquiring per-ZIP lock)")
        with zip_lock:
            log_debug(f"Thread {thread_id}: Acquired per-ZIP lock for {zip_path}")
            try:
                log_debug(f"Thread {thread_id}: Opening {internal_path} in ZIP file")
                with zip_ref.open(internal_path) as xml_file:
                    log_debug(f"Thread {thread_id}: Reading content from {internal_path}")
                    content = xml_file.read()
                log_debug(f"Thread {thread_id}: Successfully extracted {len(content)} bytes from {internal_path}")
                return content
            except zipfile.BadZipFile as e:
                log_error(f"Thread {thread_id}: Bad ZIP file error extracting {internal_path} from {zip_path}: {e}")
                raise
            except KeyError as e:
                log_error(f"Thread {thread_id}: File not found in ZIP extracting {internal_path} from {zip_path}: {e}")
                raise
            except Exception as e:
                log_error(f"Thread {thread_id}: Unexpected error extracting {internal_path} from {zip_path}: {e}")
                raise
            finally:
                log_debug(f"Thread {thread_id}: Releasing per-ZIP lock for {zip_path}")

    @classmethod
    def cleanup_zip_cache(cls):
        """Clean up cached ZIP connections with reference counting"""
        with cls._zip_cache_lock:
            print(f"Cleaning up {len(cls._zip_cache)} cached ZIP connections")
            for zip_path, (zip_ref, zip_lock) in cls._zip_cache.items():
                try:
                    print(f"Closing ZIP connection for {zip_path} (ref_count was: {cls._zip_cache_ref_count.get(zip_path, 0)})")
                    zip_ref.close()
                except Exception as e:
                    print(f"Error closing ZIP connection for {zip_path}: {e}")
            cls._zip_cache.clear()
            cls._zip_cache_ref_count.clear()
            print("Cleaned up XML processor ZIP file cache")

    @classmethod
    def release_zip_connection(cls, zip_path: str):
        """Release a reference to a cached ZIP connection"""
        zip_key = str(zip_path)
        with cls._zip_cache_lock:
            if zip_key in cls._zip_cache_ref_count:
                cls._zip_cache_ref_count[zip_key] -= 1
                if cls._zip_cache_ref_count[zip_key] <= 0:
                    # No more references, close the connection
                    try:
                        zip_ref, zip_lock = cls._zip_cache[zip_key]
                        zip_ref.close()
                        del cls._zip_cache[zip_key]
                        del cls._zip_cache_ref_count[zip_key]
                        print(f"Closed ZIP connection for {zip_path} due to zero references")
                    except Exception as e:
                        print(f"Error closing ZIP connection for {zip_path}: {e}")





class XMLConsumer(BaseConsumer):
    """
    XML Consumer - Handles database operations execution for XML processing.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.

    Inherits all execution logic from BaseConsumer; no custom overrides needed for XML-specific behavior.
    """

    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        super().__init__(db_ops)
        self.processing_version = processing_version

    def _process_operations_batch(self, operations_by_type):
        """Process operations batch for XML consumer using standardized pattern"""
        # DEPRECATED: All operations are now handled by PendingDatabaseContext.save_to_database()
        # which executes all operations directly. This method should not exist.
        # PDC migration complete - operation-based code removed.
        pass

    def _execute_optimize_operation(self, operation):
        """Execute database optimization operation"""
        # DEPRECATED: Database optimization should be handled by PDC, not individual operations
        # PDC migration complete - operation-based code removed.
        if operation.operation_type != DatabaseOperationType.OPTIMIZE_DATABASE:
            return

        log_info("Starting database optimization...")
        try:
            # Call the optimize_database method from DatabaseOperations
            self.db_ops.optimize_database()
            log_info("Database optimization completed successfully")
        except Exception as e:
            log_error(f"Database optimization failed: {e}", exc_info=True)
            raise

    def _execute_progress_update_operation(self, operation):
        """Execute progress update operation"""
        # DEPRECATED: Progress updates should be handled by PDC, not individual operations
        # PDC migration complete - operation-based code removed.
        if operation.operation_type != DatabaseOperationType.PROGRESS_UPDATE:
            return

        from logging_utils import update_progress
        progress_count = operation.data.get("count", 0)
        log_debug(f"DEBUG: Processing PROGRESS_UPDATE operation with count={progress_count}")
        update_progress(n=progress_count)

    def _process_xml_file_update_operations(self, operations_by_type):
        """Process XML file update operations using bulk_update"""
        # DEPRECATED: XML file updates should be handled by PDC, not individual operations
        # PDC migration complete - operation-based code removed.
        pass

    # _process_charity_operations method removed - now handled by BaseConsumer.execute_contexts_batch
    # PDC migration complete - operation-based code removed.

    def _process_related_operations(self, operations_by_type, charity_id_map):
        """Process related data operations (officers, grants, contractors, contributions)"""
        # DEPRECATED: All object insertions should be handled by PDC, not individual operations
        # PDC migration complete - operation-based code removed.
        pass

    def _process_address_operations(self, operations_by_type, charity_id_map):
        """Process address insert operations"""
        # DEPRECATED: All object insertions should be handled by PDC, not individual operations
        # PDC migration complete - operation-based code removed.
        pass

    def _process_generic_update_operations(self, operations_by_type):
        """Process generic update operations using bulk_update"""
        # DEPRECATED: All database updates should be handled by PDC, not individual operations
        # PDC migration complete - operation-based code removed.
        pass


class XMLProcessor(BaseProcessor):
    """
    XML Processor - Main entry point for XML processing operations.
    
    This class coordinates XML processing using the producer-consumer pattern.
    It can operate in both single-threaded and multi-threaded modes.
    """
    
    def __init__(self, db_ops: DatabaseOperations, processing_version: int = 1):
        super().__init__(db_ops)
        self.processing_version = processing_version
        self.total_processed = 0
        
        # Atomic counter for available items in result queue (XML-specific coordination)
        self._available_items = 0
        self._available_items_lock = threading.Lock()
        self._sentinel_count = 0
        self._sentinel_lock = threading.Lock()

    def _get_custom_metrics(self) -> Dict[str, Any]:
        """Custom metrics for XMLProcessor."""
        return {
            'zip_cache_size': len(XMLProducer._zip_cache),
            'parsed_files_count': self.total_processed,
            'pdc_objects_count': self.pdc_objects_count,
            'pdc_operations_count': self.pdc_operations_count,
            'pdc_updates_count': self.pdc_updates_count,
        }

    def _on_shutdown(self):
        """Override for XML-specific shutdown cleanup."""
        super()._on_shutdown()
        XMLProducer.cleanup_zip_cache()

    def process_xml_files(self, max_files: Optional[int] = None, workers: int = 4, collect_xpath_stats: bool = False) -> int:
        """
        Process XML files using parallel producer-consumer pattern.

        Args:
            max_files: Maximum number of files to process (for testing)
            workers: Number of worker threads for parallel processing

        Returns:
            Number of XML files processed
        """
        return self._process_xml_files_parallel(max_files, workers, collect_xpath_stats)

    def _process_xml_files_parallel(self, max_files: Optional[int] = None, workers: int = 4, collect_xpath_stats: bool = False) -> int:
        """
        Process XML files using parallel producer-consumer pattern.

        This method implements the threading and coordination logic directly,
        eliminating the artificial processor/strategy split.

        Args:
            max_files: Maximum number of files to process
            workers: Number of worker threads
        """
        from constants import CURRENT_PROCESSING_VERSION
        from logging_utils import start_progress_reporting

        # Use base shutdown event and handlers (already set up in BaseProcessor)

        # Get total count for progress bar
        total_files = self.db_ops.get_xml_files_to_process_count(CURRENT_PROCESSING_VERSION) if max_files is None else max_files
        log_info(f"DEBUG: Total files to process: {total_files}, max_files parameter: {max_files}")

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

        # DEBUG: Log thread pool configuration
        log_info(f"DEBUG: Thread pool config - max_workers={producer_config.max_workers}, queue_size={producer_config.queue_size}, batch_size={producer_config.batch_size}")

        # Initialize thread pool manager
        from base_processor import ThreadPoolManager
        thread_pool_manager = ThreadPoolManager(thread_pool_config,self)

        # Setup status gauges for monitoring
        self.setup_status_gauges(interval=MONITOR_INTERVAL_SECONDS, queues=[thread_pool_manager.result_queue])

        # Reset counters for this processing run
        self._available_items = 0
        self._sentinel_count = 0
        self.total_processed = 0

        total_processed = 0
        last_xml_id = None  # Initialize paging state

        # Control queue removed - backpressure eliminated entirely

        try:
            # Process batches until we reach max_files or run out of files
            while True:
                # Calculate remaining files for this batch
                remaining_files = None if max_files is None else max_files - total_processed
                batch_limit = 100 if remaining_files is None else min(remaining_files, 100)

                # Get next batch of files with key-value paging
                xml_files = self.db_ops.get_xml_files_to_process(
                    processing_version=CURRENT_PROCESSING_VERSION,
                    max_files=batch_limit,
                    last_xml_id=last_xml_id
                )
                if not xml_files:
                    log_info(f"DEBUG: No more XML files to process (last_xml_id: {last_xml_id})")
                    break

                batch_size = len(xml_files)
                log_info(f"Processing batch of {batch_size} files with {workers} workers (last_xml_id: {last_xml_id})")

                # DEBUG: Log ZIP file distribution in batch
                unique_zips = len(set(xml_file.zip_id for xml_file in xml_files))
                log_info(f"DEBUG: Batch contains {batch_size} files from {unique_zips} unique ZIP files")

                # Update paging state after fetching batch
                if xml_files:
                    last_xml_id = max(xml_file.xml_id for xml_file in xml_files)

                # Start producer pool with atomic counters
                log_info(f"DEBUG: Starting producer pool with {len(xml_files)} files, workers={workers}")
                thread_pool_manager.start_producer_pool(
                    xml_files,
                    lambda *args: self._xml_producer_worker(*args, collect_xpath_stats)
                )
                log_info(f"DEBUG: Producer pool started with {len(thread_pool_manager.producer_threads)} threads")

                # Start single consumer thread with atomic counters
                consumer_thread = threading.Thread(
                    target=self._xml_consumer_worker,
                    args=(thread_pool_manager.result_queue, consumer, pbar, thread_pool_manager)
                )
                consumer_thread.daemon = False
                consumer_thread.start()

                # Wait for completion
                self._wait_for_completion(thread_pool_manager, consumer_thread)

                # Update total processed
                total_processed += batch_size

                # Check limits - break immediately if we've reached the max_files limit
                if max_files and total_processed >= max_files:
                    break

        finally:
            # Cleanup
            if pbar:
                pbar.close()

            # Clean up ZIP cache with proper reference counting
            XMLProducer.cleanup_zip_cache()

        log_info(f"XML processing complete: {total_processed} files processed")
        return total_processed

    def _xml_producer_worker(self, xml_files, work_queue, result_queue, producer_id, num_producers, collect_xpath_stats=False):
        """Producer worker function for thread pool"""
        processed = 0
        total_bytes = 0

        # DEBUG: Log thread creation
        log_info(f"DEBUG: Producer thread {producer_id} starting (num_producers={num_producers}, files={len(xml_files)})")

        # Create producer instance for this thread
        from constants import CURRENT_PROCESSING_VERSION
        producer = XMLProducer(self.db_ops, CURRENT_PROCESSING_VERSION)

        # Set thread name to identify producer thread 0 for xpath stats collection
        if collect_xpath_stats and producer_id == 0:
            threading.current_thread().name = "XPathStatsProducer"
        elif collect_xpath_stats:
            threading.current_thread().name = f"Producer-{producer_id}"

        try:
            for i in range(producer_id, len(xml_files), num_producers):
                try:
                    if self.exit_processing:
                        break

                    # Backpressure logic removed entirely - consumer is the bottleneck, not producers

                    xml_file = xml_files[i]
                    xml_id = xml_file.xml_id
                    zip_id = xml_file.zip_id
                    zip_path = self.db_ops.get_zip_file_path(zip_id)
                    if not zip_path:
                        log_info(f"DEBUG: Producer {producer_id} skipping {xml_id} - no ZIP path for {zip_id}")
                        continue

                    filename = xml_file.filename
                    internal_path = xml_file.internal_path
                    file_size = xml_file.file_size

                    # DEBUG: Log file assignment to producer
                    log_info(f"DEBUG: Producer {producer_id} processing file {i+1}/{len(xml_files)}: {filename} (ID: {xml_id})")

                    # Parse XML and collect context
                    success, context = producer.process_single_xml_for_operations(
                        xml_id, zip_id, filename, internal_path, file_size
                    )

                    # Send context to consumer if successful
                    if success:
                        result_queue.put(context)
                        # Increment available items counter
                        with self._available_items_lock:
                            self._available_items += 1

                    processed += 1

                    # Track progress
                    if global_config.progress == "bytes" and file_size:
                        total_bytes += file_size

                    if processed % 50 == 0:
                        progress_info = f"{processed} files" if global_config.progress == "files" else f"{total_bytes} bytes"
                        log_info(f"Producer {producer_id}: {progress_info} queued")

                except Exception as e:
                    thread_id = threading.get_ident()
                    exception_type = type(e).__name__
                    import traceback
                    detailed_traceback = traceback.format_exc()
                    log_error(f"Producer {producer_id} (Thread ID: {thread_id}) failed on {filename}: {exception_type} - {e}")
                    log_error(f"DETAILED TRACEBACK: Producer {producer_id} (Thread ID: {thread_id}) exception details:\n{detailed_traceback}")
                    # Continue processing other files in the batch to prevent thread death
        finally:
            # DEBUG: Log thread completion
            log_info(f"DEBUG: Producer thread {producer_id} completed (processed {processed} files)")

            # Ensure sentinel is always sent, even if thread exits early due to exception or shutdown
            result_queue.put(None)
            # Increment sentinel counter
            with self._sentinel_lock:
                self._sentinel_count += 1

    def _xml_consumer_worker(self, operations_queue, consumer, pbar, thread_pool_manager):
        """Consumer worker function"""
        batch_contexts = []
        processed_xml_count = 0

        sentinels_received = 0
        num_producers = len(thread_pool_manager.producer_threads) if thread_pool_manager else 1

        # Consumer performance tracking (for monitoring only, no backpressure)
        consumer_performance = {
            'objects_processed': 0,
            'batches_processed': 0,
            'avg_objects_per_batch': 0.0,
            'avg_processing_time': 0.0,
            'last_batch_time': time.time(),
            'last_batch_objects': 0
        }

        # DEBUG: Track queue depth and batch fetching capability
        max_queue_depth_seen = 0
        total_items_fetched = 0
        batch_fetch_attempts = 0
        successful_batch_fetches = 0
        
        while True:
            with self._available_items_lock:
                items_left = self._available_items > 0
            # Only exit when all producers are done AND no items remain in queue
            if sentinels_received >= num_producers and not items_left:
                break

            # Check shutdown event before continuing
            if self.exit_processing:
                log_info(f"Consumer thread exiting due to exit_processing flag")
                break

            # Check exit_processing flag before continuing
            if self.exit_processing:
                log_info(f"Consumer thread exiting due to exit_processing flag")
                break

            # Monitor queue depth for debugging (no backpressure actions)
            current_size = operations_queue.qsize()
            max_queue_depth_seen = max(max_queue_depth_seen, current_size)
            try:
                # DEBUG: Attempt batch fetching up to 100 items without sleeping
                batch_fetched = []
                batch_fetch_attempts += 1

                # Use atomic counter to determine how many items to fetch
                with self._available_items_lock:
                    available_count = self._available_items

                # If no items available, check if all producers are done
                if available_count == 0:
                    with self._sentinel_lock:
                        if self._sentinel_count >= num_producers:
                            # All producers done, but still check for any remaining items in queue
                            try:
                                # Try to get any remaining items without timeout first
                                while True:
                                    item = operations_queue.get_nowait()
                                    if item is None:
                                        sentinels_received += 1
                                    else:
                                        batch_fetched.append(item)
                                        with self._available_items_lock:
                                            self._available_items -= 1
                            except queue.Empty:
                                # No more items, safe to exit
                                pass
                    continue

                # Fetch up to CONSUMER_BATCH_SIZE items or available count, whichever is smaller
                items_to_fetch = min(CONSUMER_BATCH_SIZE, available_count)
                for _ in range(items_to_fetch):
                    try:
                        item = operations_queue.get_nowait()
                        if item is None:
                            sentinels_received += 1
                            break
                        batch_fetched.append(item)
                    except queue.Empty:
                        break

                # Decrement the atomic counter by how many we actually fetched
                if batch_fetched:
                    with self._available_items_lock:
                        self._available_items -= len(batch_fetched)
        
                # Process the batch
                if batch_fetched:
                    successful_batch_fetches += 1
                    total_items_fetched += len(batch_fetched)
        
                    # Add non-sentinel items to batch_contexts
                    for item in batch_fetched:
                        if isinstance(item, PendingDatabaseContext):
                            batch_contexts.append(item)
        
                # If we got sentinels, continue to next iteration
                if sentinels_received >= num_producers:
                    continue
        
                if len(batch_contexts) >= CONSUMER_BATCH_SIZE:
                    try:
                        # Track batch processing performance
                        batch_start_time = time.time()
                        batch_objects = sum(context.getTotalObjectCount() for context in batch_contexts)

                        # Merge all contexts into a single giant PendingDatabaseContext
                        merged_context = self.merge_pending_contexts(batch_contexts)

                        # Update PDC size gauge before save
                        consumer.update_pdc_size_gauge(merged_context)

                        # Update XMLProcessor's gauge counters from consumer
                        self.pdc_objects_count = consumer.pdc_objects_count
                        self.pdc_operations_count = consumer.pdc_operations_count
                        self.pdc_updates_count = consumer.pdc_updates_count

                        # Execute merged batch
                        batch_size = len(batch_contexts)
                        merged_context.save_to_database(consumer.db_ops)

                        # Explicitly clear merged context to free memory immediately
                        merged_context.clear()
                        del merged_context

                        # Clear batch contexts list
                        del batch_contexts[:]
                        batch_contexts = []

                        # Update performance metrics
                        batch_time = time.time() - batch_start_time
                        consumer_performance['objects_processed'] += batch_objects
                        consumer_performance['batches_processed'] += 1
                        consumer_performance['avg_objects_per_batch'] = (
                            (consumer_performance['avg_objects_per_batch'] * (consumer_performance['batches_processed'] - 1) +
                             batch_objects) / consumer_performance['batches_processed']
                        )
                        consumer_performance['avg_processing_time'] = (
                            (consumer_performance['avg_processing_time'] * (consumer_performance['batches_processed'] - 1) +
                             batch_time) / consumer_performance['batches_processed']
                        )
                        consumer_performance['last_batch_time'] = time.time()
                        consumer_performance['last_batch_objects'] = batch_objects
                    except Exception as e:
                        log_error(f"Consumer batch processing error: {e}")
                        # Continue processing without crashing - don't clear batch_contexts on error
                        continue
        
                # Mark all fetched items as done
                for _ in batch_fetched:
                    operations_queue.task_done()
        
            except queue.Empty:
                if self.exit_processing:
                    break

        # Final batch
        if batch_contexts:
            try:
                batch_size = len(batch_contexts)
                final_batch_objects = sum(context.getTotalObjectCount() for context in batch_contexts)
    
                # Merge all remaining contexts into a single giant PendingDatabaseContext
                merged_context = self.merge_pending_contexts(batch_contexts)  # Change: merge() is already efficient; batch size reduced to 50 to minimize overhead
    
                # Update PDC size gauge before save
                consumer.update_pdc_size_gauge(merged_context)

                # Update XMLProcessor's gauge counters from consumer
                self.pdc_objects_count = consumer.pdc_objects_count
                self.pdc_operations_count = consumer.pdc_operations_count
                self.pdc_updates_count = consumer.pdc_updates_count

                merged_context.save_to_database(consumer.db_ops)
    
                # Explicitly clear final merged context to free memory immediately
                merged_context.clear()
                del merged_context
    
                # Clear batch contexts list
                del batch_contexts[:]
                batch_contexts = []
            except Exception as e:
                log_error(f"Consumer final batch processing error: {e}")
                # Don't clear batch_contexts on error to avoid losing data
    
        # DEBUG: Log final batch fetching statistics
        log_info(f"DEBUG: Consumer batch fetching stats - Max queue depth: {max_queue_depth_seen}, "
                         f"Total items fetched: {total_items_fetched}, Batch fetch attempts: {batch_fetch_attempts}, "
                         f"Successful batch fetches: {successful_batch_fetches}")
    
    def merge_pending_contexts(self, contexts: List[PendingDatabaseContext]) -> PendingDatabaseContext:
        merged = PendingDatabaseContext()
        for ctx in contexts:
            for obj_type, objs in ctx.objects.items():
                merged.objects[obj_type].extend(objs)
            merged.operations.extend(ctx.operations)
            if ctx.error_message:
                merged.error_message = ctx.error_message  # Preserve error messages if any
        return merged

    def _wait_for_completion(self, thread_pool_manager, consumer_thread):
        """Wait for producer and consumer threads to complete"""
        # Wait for producers
        for thread in thread_pool_manager.producer_threads:
            thread.join()

        # Wait for consumer
        if consumer_thread and consumer_thread.is_alive():
            consumer_thread.join()
