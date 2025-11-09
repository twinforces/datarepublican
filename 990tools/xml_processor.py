#!/usr/bin/env python3
"""
xml_processor.py - XML file processing for IRS 990 data

This module handles the parsing and processing of IRS 990 XML files,
extracting data into dataclasses and storing in the database.
"""

import time
import threading
import queue
from io import BytesIO
from typing import Optional, List, Tuple, Dict, Any
from lxml import etree  # type: ignore
import psutil
import zipfile
from enum import Enum

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
from queue_status_display import QueueStatusDisplay

from collections import deque
# Import XPath configurations from xpaths.py
from xpaths import COMMON_XPATHS, tostring
from xpaths_990 import XPATHS_990
from xpaths_990ez import XPATHS_990EZ
from xpaths_990pf import XPATHS_990PF

DEBUG_TASK_FLOW = False


class WorkQueueItemType(Enum):
    XML_FILES = "xml_files"
    SENTINEL = "sentinel"
    RESULT = "result"


class WorkQueueItem:
    """Uniform wrapper for items in the work queue"""

    def __init__(self, item_type: WorkQueueItemType, data=None):
        self.item_type = item_type
        self.data = data

    @classmethod
    def xml_file(cls, file):
        return cls(WorkQueueItemType.XML_FILES, file)

    @classmethod
    def sentinel(cls, producer_id: int):
        return cls(WorkQueueItemType.SENTINEL, producer_id)
    
    @classmethod
    def result(cls, pdc: PendingDatabaseContext):
        return cls(WorkQueueItemType,pdc)

    # Removed wake_feeder method

    def is_xml_files(self):
        return self.item_type == WorkQueueItemType.XML_FILES

    def is_sentinel_for(self, producer_id: int):
        return self.item_type == WorkQueueItemType.SENTINEL and self.data == producer_id
    
    def is_sentinel(self):
        return self.item_type == WorkQueueItemType.SENTINEL # consumer doesn't care

    # Removed is_wake_feeder method



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
    # ZipFile is thread-safe, so no per-ZIP locks needed
    _zip_cache: Dict[str, zipfile.ZipFile] = {}
    _zip_cache_lock = threading.Lock()
    _zip_cache_ref_count: Dict[str, int] = {}  # Reference counting for proper cleanup

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
                print("EXITING PROCESSING")
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
                parser.parse_form(root, filename, {}, context, cached_charity=charity)
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
            # Store error message with stack trace in context for unexpected errors
            import traceback
            error_msg = f"Unexpected Error: {str(e)}\n\nStack Trace:\n{traceback.format_exc()}"
            context.error_message = error_msg
            if not global_config.is_quiet():
                log_error("FAILED: XML {}: Unexpected error - {}\n{}", filename, str(e), traceback.format_exc())
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
                # Open ZIP file and cache the connection (ZipFile is thread-safe)
                log_info(f"Thread {thread_id}: Opening new ZIP connection for {zip_path}")
                zip_ref = zipfile.ZipFile(zip_path, 'r')
                self._zip_cache[zip_key] = zip_ref
                self._zip_cache_ref_count[zip_key] = 1
                log_info(f"Thread {thread_id}: Opened and cached ZIP connection for {zip_path}")
            else:
                # Increment reference count for existing connection
                self._zip_cache_ref_count[zip_key] += 1
                log_debug(f"Thread {thread_id}: Using cached ZIP connection for {zip_path} (ref_count: {self._zip_cache_ref_count[zip_key]})")

            zip_ref = self._zip_cache[zip_key]
            log_debug(f"Thread {thread_id}: Retrieved ZIP reference from cache for {zip_path}")
            log_debug(f"Thread {thread_id}: Releasing ZIP cache lock for {zip_path}")

        # Extract XML content from cached connection (ZipFile is thread-safe)
        log_debug(f"Thread {thread_id}: Starting extraction of {internal_path} from {zip_path}")
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

    @classmethod
    def cleanup_zip_cache(cls):
        """Clean up cached ZIP connections with reference counting"""
        with cls._zip_cache_lock:
            print(f"Cleaning up {len(cls._zip_cache)} cached ZIP connections")
            for zip_path, zip_ref in cls._zip_cache.items():
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
                        zip_ref = cls._zip_cache[zip_key]
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
        self.total_files_to_process = 0
        # Initialize PDC attributes for status gauges
        self.pdc_objects_count = 0
        self.pdc_operations_count = 0
        self.pdc_updates_count = 0

        # Initialize separate PDC gauges
        self.pdc_objects_gauge = None
        self.pdc_operations_gauge = None
        self.pdc_updates_gauge = None

        # Result queue for producer-consumer coordination
        self.result_queue = queue.Queue()

        # Work queue for producer-consumer coordination
        self.work_queue = queue.Queue()

        # Initialize QueueStatusDisplay for visual monitoring
        self.queue_status_display = QueueStatusDisplay(self.work_queue, update_interval=30.0, custom_metrics_func=self._get_custom_metrics)

        # Atomic counter for available items in result queue (XML-specific coordination)
        self._available_items = 0
        self._available_items_lock = threading.Lock()
        self._sentinel_count = 0
        self._sentinel_lock = threading.Lock()

        # Debug: Track XML IDs to detect duplicates
        if DEBUG_TASK_FLOW:
            self._processed_xml_ids = set()
            self._processed_xml_ids_lock = threading.Lock()

        # No coordination needed anymore

    def _get_custom_metrics(self) -> Dict[str, Any]:
        """Custom metrics for XMLProcessor."""
        try:
            return {
                'zip_cache_size': len(XMLProducer._zip_cache),
                'parsed_files_count': self.total_processed,
                'parsed_files_total': self.total_files_to_process,
                'pdc_objects_count': self.pdc_objects_count,
                'pdc_operations_count': self.pdc_operations_count,
                'pdc_operations_total': 10 * CONSUMER_BATCH_SIZE,
                'pdc_updates_count': self.pdc_updates_count,
                'pdc_updates_total': CONSUMER_BATCH_SIZE,
            }
        except AttributeError as e:
            # Handle missing attributes gracefully
            log_error(f"Error accessing custom metrics: {e}")
            return {
                'zip_cache_size': len(XMLProducer._zip_cache),
                'parsed_files_count': self.total_processed,
                'parsed_files_total': self.total_files_to_process,
            }

    def _setup_pdc_gauges(self):
        """Setup separate tqdm gauges for each PDC metric."""
        try:
            import tqdm
            if tqdm:
                self.pdc_objects_gauge = tqdm.tqdm(
                    total=100,  # Percentage
                    desc="PDC Objects",
                    unit="%",
                    bar_format='{desc}: {postfix}',
                    position=3,  # Position below memory gauge
                    leave=True
                )
                self.pdc_operations_gauge = tqdm.tqdm(
                    total=100,  # Percentage
                    desc="PDC Operations",
                    unit="%",
                    bar_format='{desc}: {postfix}',
                    position=4,  # Position below objects gauge
                    leave=True
                )
                self.pdc_updates_gauge = tqdm.tqdm(
                    total=100,  # Percentage
                    desc="PDC Updates",
                    unit="%",
                    bar_format='{desc}: {postfix}',
                    position=5,  # Position below operations gauge
                    leave=True
                )
        except ImportError:
            pass

    def _update_pdc_gauges(self):
        """Update the PDC gauges with current values."""
        if self.pdc_objects_gauge:
            postfix = f"Count: {self.pdc_objects_count}"
            self.pdc_objects_gauge.set_postfix({"info": postfix})
            self.pdc_objects_gauge.refresh()

        if self.pdc_operations_gauge:
            postfix = f"Count: {self.pdc_operations_count}"
            self.pdc_operations_gauge.set_postfix({"info": postfix})
            self.pdc_operations_gauge.refresh()

        if self.pdc_updates_gauge:
            postfix = f"Count: {self.pdc_updates_count}"
            self.pdc_updates_gauge.set_postfix({"info": postfix})
            self.pdc_updates_gauge.refresh()

    def _on_shutdown(self):
        """Override for XML-specific shutdown cleanup."""
        super()._on_shutdown()
        XMLProducer.cleanup_zip_cache()

        # Close PDC gauges
        if self.pdc_objects_gauge:
            self.pdc_objects_gauge.close()
        if self.pdc_operations_gauge:
            self.pdc_operations_gauge.close()
        if self.pdc_updates_gauge:
            self.pdc_updates_gauge.close()

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
        Process XML files using producer-consumer pattern with one feeder, N producers, one consumer.

        Args:
            max_files: Maximum number of files to process
            workers: Number of producer threads
        """
        from constants import CURRENT_PROCESSING_VERSION, BATCH_SIZE
        from logging_utils import start_progress_reporting

        # Use base shutdown event and handlers (already set up in BaseProcessor)

        # Get total count for progress bar
        total_files = self.db_ops.get_xml_files_to_process_count(CURRENT_PROCESSING_VERSION) if max_files is None else max_files
        self.total_files_to_process = total_files
        log_info(f"DEBUG: Total files to process: {total_files}, max_files parameter: {max_files}")

        # Setup progress bar
        progress_unit = "file" if global_config.progress == "files" else "B"
        progress_desc = "Processing XML files" if global_config.progress == "files" else "XML bytes"
        pbar = start_progress_reporting(total=total_files, desc=progress_desc, unit=progress_unit)

        # Create shared work queue with bound 10*BATCH_SIZE
        self.work_queue = queue.Queue(maxsize=10 * BATCH_SIZE)

        # Create producer and consumer instances
        producer = XMLProducer(self.db_ops, CURRENT_PROCESSING_VERSION)
        consumer = XMLConsumer(self.db_ops, CURRENT_PROCESSING_VERSION)

        # No coordination needed anymore

        # Start single feeder thread
        feeder_thread = threading.Thread(
            target=self._feeder_worker,
            args=(max_files, workers)
        )
        feeder_thread.daemon = False
        feeder_thread.start()

        # Start producer threads
        producer_threads = []
        for i in range(workers):
            t = threading.Thread(
                target=self._producer_worker,
                args=(producer, i, workers, collect_xpath_stats)
            )
            t.daemon = False
            t.start()
            producer_threads.append(t)

        # Start single consumer thread
        consumer_thread = threading.Thread(
            target=self._consumer_worker,
            args=(consumer, pbar, workers)
        )
        consumer_thread.daemon = False
        consumer_thread.start()

        # Setup status gauges for monitoring
        self.setup_status_gauges(interval=MONITOR_INTERVAL_SECONDS, queues=[self.work_queue])

        # Setup separate PDC gauges
        self._setup_pdc_gauges()

        # Start QueueStatusDisplay for visual monitoring
        self.queue_status_display.start()

        # Wait for completion - feeder first, then producers, then consumer
        log_info("Waiting for feeder thread to complete...")
        feeder_thread.join()
        log_info("Feeder thread completed")

        log_info("Waiting for producer threads to complete...")
        for i, t in enumerate(producer_threads):
            t.join()
            log_info("Producer thread {} completed".format(i))
        log_info("All producer threads completed")

        log_info("Waiting for consumer thread to complete...")
        consumer_thread.join()
        log_info("Consumer thread completed")

        # Cleanup
        if pbar:
            pbar.close()

        # Stop QueueStatusDisplay
        self.queue_status_display.stop()

        # Clean up ZIP cache with proper reference counting
        XMLProducer.cleanup_zip_cache()

        log_info(f"XML processing complete: {self.total_processed} files processed")
        return self.total_processed

    def _feeder_worker(self, max_files: Optional[int], num_producers: int):
        """Feeder thread: Fetches XML files and feeds into shared queue."""
        from constants import CURRENT_PROCESSING_VERSION, BATCH_SIZE
        total_fed = 0
        last_xml_id = None

        while True:
            # Calculate batch size
            remaining = max_files - total_fed if max_files else BATCH_SIZE
            batch_size = min(BATCH_SIZE, remaining) if max_files else BATCH_SIZE

            # Fetch batch
            xml_files = self.db_ops.get_xml_files_to_process(
                processing_version=CURRENT_PROCESSING_VERSION,
                max_files=batch_size,
                last_xml_id=last_xml_id
            )

            if not xml_files:
                break

            # Enqueue each file individually
            for xml_file in xml_files:
                self.work_queue.put(WorkQueueItem.xml_file(xml_file))
            total_fed += len(xml_files)

            # Update last_xml_id
            last_xml_id = max(f.xml_id for f in xml_files)

            if max_files and total_fed >= max_files:
                break

        # Send sentinels immediately after queuing all work
        log_info("Feeder completed queuing {} files, sending {} sentinels".format(total_fed, num_producers))
        for i in range(num_producers):
            self.work_queue.put(WorkQueueItem.sentinel(i))

    def _producer_worker(self, producer: XMLProducer, producer_id: int, num_producers: int, collect_xpath_stats: bool = False):
        """Producer worker: Pulls files from work queue, processes to contexts, puts to result queue."""
        log_info("PRODUCER THREAD {} STARTED".format(producer_id))
        processed_count = 0
        while True:
            # Get the next item (will block if queue is empty)
            item = self.work_queue.get()

            # Check if it's a sentinel for this producer
            if item.is_sentinel_for(producer_id):
                self.work_queue.task_done()
                # Send sentinel to result queue to signal completion
                log_info("Producer {} processed {} items, sending sentinel to consumer".format(producer_id, processed_count))
                self.result_queue.put(item)
                break
            elif item.item_type == WorkQueueItemType.SENTINEL:
                # It's a sentinel for another producer, put it back and wait
                self.work_queue.put(item)
                self.work_queue.task_done()
                time.sleep(0.1)  # Small delay to avoid busy waiting
                continue

            # Process file - only if this is an XML file item
            if item.is_xml_files():
                xml_file = item.data
                # Check for duplicate processing
                if DEBUG_TASK_FLOW:
                    with self._processed_xml_ids_lock:
                        if xml_file.xml_id in self._processed_xml_ids:
                            log_error("DUPLICATE XML_ID DETECTED: {} already processed, skipping".format(xml_file.xml_id))
                            self.work_queue.task_done()
                            continue
                        self._processed_xml_ids.add(xml_file.xml_id)

                success, context = producer.process_single_xml_for_operations(
                    xml_file.xml_id, xml_file.zip_id, xml_file.filename, xml_file.internal_path, xml_file.file_size
                )
                # Always put context to result queue, even if processing failed
                # The consumer will handle failed contexts appropriately
                self.result_queue.put(WorkQueueItem.result(context))
                processed_count += 1

            self.work_queue.task_done()

        log_info("PRODUCER THREAD {} COMPLETED".format(producer_id))
        
    def _pdc_metrics(self,pdc: PendingDatabaseContext):

        self.pdc_objects_count=pdc.getTotalObjectCount()
        self.pdc_operations_count = pdc.getOperationsCount()
        self.pdc_updates_count = pdc.getUpdatesCount()

        # Update PDC gauges with new values
        self._update_pdc_gauges()
        
    def _consumer_worker(self, consumer: XMLConsumer, pbar, num_producers: int):
        """Single consumer: Drains result queue and executes PendingDatabaseContexts."""
        log_info("CONSUMER THREAD STARTED")
        batch_contexts = []
        sentinels_received = 0

        # Process items until all producers are done
        while sentinels_received < num_producers or batch_contexts:
            try:
                item = self.result_queue.get_nowait()
            except queue.Empty:
                # If we've received all sentinels but still have contexts, process them
                if sentinels_received >= num_producers and batch_contexts:
                    break
                continue

            if item.is_sentinel():
                sentinels_received += 1
                log_info(f"Received sentinel {sentinels_received}/{num_producers}")
                self.result_queue.task_done()
                continue

            batch_contexts.append(item.data)
            self.result_queue.task_done()

            if len(batch_contexts) >= CONSUMER_BATCH_SIZE:
                merged = PendingDatabaseContext.merge(batch_contexts)
                log_info(f"merged context has {merged.getObjectCounts()} Stuff")
                log_info(f"Saving batch of {len(batch_contexts)} contexts to database")
                self._pdc_metrics(merged)
                merged.save_to_database(consumer.db_ops)
                log_info(f"Successfully saved batch of {len(batch_contexts)} contexts")
                # Progress updates are handled by operations in the PDC
                self.total_processed += len(batch_contexts)
                batch_contexts = []
 
        # Process remaining
        if len(batch_contexts):
            merged = PendingDatabaseContext.merge(batch_contexts)
            log_info(f"Saving final batch of {len(batch_contexts)} contexts to database" )
            log_info(f"merged context has {merged.getObjectCounts()} Stuff")
            self._pdc_metrics(merged)
            merged.save_to_database(consumer.db_ops)
            log_info(f"Successfully saved batch of {len(batch_contexts)} contexts")
            # Progress updates are handled by operations in the PDC
            self.total_processed += len(batch_contexts)

        log_info("CONSUMER THREAD COMPLETED")
