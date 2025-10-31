#!/usr/bin/env python3
"""
processing_strategy.py - Strategy pattern for IRS 990 processing phases

This module implements the Strategy pattern to organize different processing
phases of the IRS 990 pipeline, making the main processor more maintainable.
"""

import signal
import sys
import traceback
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Dict, Any
import logging
import time
import threading
import queue
from pathlib import Path
from io import BytesIO
from lxml import etree as ET  # type: ignore
from lxml import etree  # type: ignore
from dataclasses import fields
import zipfile
from threading import Lock
from enum import Enum
from uuid import UUID

from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config
from models import Address
from xml_processor import XMLProducer, XMLConsumer
from address_deduplication_processor import AddressDeduplicationProcessor
from geolocation_processor import GeolocationProcessor
from geolocation_processor import geolocate_addresses
from base_processor import ThreadPoolManager, ThreadPoolConfig, PoolConfig


class ProcessingStrategy(ABC):
    """Abstract base class for processing strategies"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        self.db_ops = db_ops
        self.logger = logger

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the processing strategy"""
        pass

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not global_config.is_quiet():
            log_info(self.logger, msg, *args, ein=ein)

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, *args, ein=ein, exc_info=exc_info)

    def format_error_with_traceback(self, error: Exception, context: str = "") -> str:
        """Format error message with full stack trace for better debugging"""
        import traceback
        error_msg = f"{context}: {str(error)}" if context else str(error)
        stack_trace = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        return f"{error_msg}\n\nStack Trace:\n{stack_trace}"

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not global_config.is_quiet():
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)


class ParallelXMLProcessingStrategy(ProcessingStrategy):
    """
    Strategy for parallel XML file processing using producer-consumer pattern.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class coordinates producers and consumers. Producers parse XML and collect operations.
    Consumers execute database operations. Never mix these responsibilities!

    - Producers (XMLProducer): Parse XML, collect DatabaseOperation objects, NO database writes
    - Consumers (XMLConsumer): Execute DatabaseOperation objects, handle all database writes
    """

    MAX_WORKERS = 16
    QUEUE_SIZE = 1000
    BATCH_SIZE = 100
    STALL_THRESHOLD = 30
    OPTIMIZE_INTERVAL = 500000  # Optimize database every 500,000 XML files processed

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, workers: int = MAX_WORKERS, quiet: bool = False):
        super().__init__(db_ops, logger, global_config.is_quiet())
        self.workers = workers
        self.total_processed_xml = 0  # Counter for total processed XML files
        self.shutdown_event = threading.Event()  # Event for clean shutdown signaling

        # Create thread pool configuration
        producer_config = PoolConfig(max_workers=workers, queue_size=self.QUEUE_SIZE, batch_size=self.BATCH_SIZE)
        consumer_config = PoolConfig(max_workers=1, queue_size=self.QUEUE_SIZE, batch_size=self.BATCH_SIZE)  # Single consumer for DB safety
        self.thread_pool_config = ThreadPoolConfig(producer_config=producer_config, consumer_config=consumer_config)

        # Create producer and consumer instances with clear separation
        from constants import CURRENT_PROCESSING_VERSION
        self.producer = XMLProducer(db_ops, CURRENT_PROCESSING_VERSION, self.thread_pool_config)  # Producer: collects operations
        self.consumer = XMLConsumer(db_ops, CURRENT_PROCESSING_VERSION, self.thread_pool_config)     # Consumer: executes operations

        # Initialize thread pool manager
        self.thread_pool_manager = ThreadPoolManager(self.thread_pool_config, logger)

        # Set up SIGUSR1 handler for thread stack dumps
        signal.signal(signal.SIGUSR1, self.dump_threads_handler)

    # Lock-free queue implementation for single-file processing

    # Removed _get_xml_files_to_process - now using XMLFile.get_xml_files_to_process()

    def execute(self, max_files: Optional[int] = None) -> int:
        """Process XML files using generalized thread pool manager"""
        # Set up signal handlers for graceful shutdown and thread debugging
        def interrupt_handler(signum, frame):
            self.log_error("Received interrupt signal, shutting down gracefully...")
            self.shutdown_event.set()  # Signal threads to shutdown
            try:
                from logging_utils import stop_progress_reporting
                stop_progress_reporting()
            except:
                pass

        signal.signal(signal.SIGINT, interrupt_handler)

        from constants import CURRENT_PROCESSING_VERSION

        # Get total count of files to process for progress bar
        if max_files is None:
            # Count total unprocessed files for unlimited mode
            total_files_query = """
                SELECT COUNT(*) FROM XmlFiles
                WHERE processed = FALSE OR processing_version < ?
            """
            result = self.db_ops.execute_query(total_files_query, (CURRENT_PROCESSING_VERSION,))
            total_files = result.fetchone()[0] if result else 0
        else:
            total_files = max_files

        # Set up progress bar for XML processing
        from tqdm import tqdm
        from logging_utils import start_progress_reporting
        progress_unit = "file" if global_config.progress == "files" else "B"
        progress_desc = "Processing XML files" if global_config.progress == "files" else "XML bytes"
        pbar = start_progress_reporting(total=total_files, desc=progress_desc, unit=progress_unit)

        total_processed = 0

        # Process batches until we reach max_files or run out of files
        while True:
            # Calculate how many files we can still process in this batch
            remaining_files = None if max_files is None else max_files - total_processed
            batch_limit = self.BATCH_SIZE if remaining_files is None else min(remaining_files, self.BATCH_SIZE)

            # Get next batch of files to process
            xml_files = self.db_ops.get_xml_files_to_process(processing_version=CURRENT_PROCESSING_VERSION, max_files=batch_limit)
            if not xml_files:
                break  # No more files to process

            batch_size = len(xml_files)
            self.log_info(f"Processing batch of {batch_size} files with thread pool (total processed so far: {total_processed})")

            # Use thread pool manager for producer-consumer pattern
            try:
                # Start producer pool
                self.thread_pool_manager.start_producer_pool(
                    xml_files,
                    self._xml_producer_with_pool
                )

                # Start consumer pool (single consumer for database safety)
                self.thread_pool_manager.start_consumer_pool(
                    self._database_consumer_with_pool,
                    self.db_ops.db_conn,
                    pbar
                )

                # Wait for completion
                self.thread_pool_manager.wait_for_completion()

            except Exception as e:
                self.log_error(f"Thread pool processing error: {e}", exc_info=True)
                # Continue to next batch rather than failing completely

            # Update total processed count
            total_processed += batch_size

            # Check if we've reached the max_files limit
            if max_files and total_processed >= max_files:
                break

        # Close progress bar
        if pbar:
            pbar.close()

        self.log_info(f"XML processing complete: {total_processed} files processed")

        # Clean up ZIP cache after all XML processing is complete
        self.log_info("Starting ZIP cache cleanup...")
        try:
            # ZIP cache cleanup is handled by XMLProcessor if available
            self.log_info("ZIP cache cleanup complete")
        except Exception:
            self.log_warning("ZIP cache cleanup not available")

        return total_processed

    def _xml_producer_with_pool(self, xml_files, work_queue, result_queue, producer_id, num_producers) -> None:
        """Producer function for thread pool: parses XML and sends DatabaseOperation objects to consumer"""
        processed = 0
        total_bytes = 0
        start = producer_id
        self.log_info(f"Pool Producer {producer_id} starting: processing files {start} to {len(xml_files)-1} step {num_producers}")
        for i in range(start, len(xml_files), num_producers):
            if self.shutdown_event.is_set():
                self.log_info(f"Pool Producer {producer_id} received shutdown signal, stopping at file {i}")
                break

            xml_file = xml_files[i]
            xml_id = xml_file.xml_id
            zip_id = xml_file.zip_id
            # Get the zip path from cache
            path = self.db_ops.get_zip_file_path(zip_id)
            if not path:
                self.log_error(f"No ZIP file found for xml_id {xml_id}")
                continue
            filename = xml_file.filename
            internal = xml_file.internal_path
            file_size = xml_file.file_size
            try:
                self.log_debug(f"Pool Producer {producer_id} processing XML {filename} (ID: {xml_id})")
                # PRODUCER: Use XMLProducer to parse XML and collect operations (NO database writes)
                success, operations = self.producer.process_single_xml_for_operations(xml_id, zip_id, filename, internal, file_size)
                self.log_debug(f"Pool Producer {producer_id} completed processing XML {filename}, got {len(operations)} operations")

                # Send operations to result queue (which feeds to consumer)
                self.log_debug(f"Pool Producer {producer_id} sending {len(operations)} operations to result queue for {filename}")
                for operation in operations:
                    result_queue.put(operation, block=True)

                # Extract filer_ein from operations for logging
                filer_ein = "unknown"
                for operation in operations:
                    if operation.operation_type == DatabaseOperationType.INSERT_CHARITY:
                        filer_ein = operation.data.ein or "unknown"
                        break

                # Log producer queuing completion
                if not global_config.is_quiet():
                    log_info(self.logger, f"Pool Producer {producer_id} queued operations for EIN {filer_ein} in {filename}: {len(operations)} operations",
                              ein=filer_ein)

                processed += 1

                # Track progress based on config
                if global_config.progress == "bytes":
                    if file_size:
                        total_bytes += file_size

                if processed % 50 == 0:
                    progress_info = f"{processed} files" if global_config.progress == "files" else f"{total_bytes} bytes"
                    self.log_info(f"Pool Producer {producer_id}: {progress_info} queued")
            except Exception as e:
                self.log_error(f"Pool Producer {producer_id} error on {filename}: {e}", exc_info=True)
                error_msg = str(e)
                if not error_msg or error_msg == "":
                    error_msg = f"Unknown error processing {filename}"
                # Create error metadata for failed processing
                error_metadata = {
                    "file_size": file_size,
                    "ein": None,
                    "tax_year": None,
                    "form_type": None,
                    "error_message": error_msg,
                    "processed": True
                }
                # Send error operation
                error_operation = DatabaseOperation(
                    DatabaseOperationType.XML_FILE_UPDATE,
                    error_metadata,
                    xml_id
                )
                result_queue.put(error_operation, block=True)
        result_queue.put(None)  # Sentinel
        self.log_info(f"Pool Producer {producer_id} done: {processed} files processed")

    def dump_threads_handler(self, signum, frame):
        """Signal handler: Dumps formatted stack traces for all live threads."""
        try:
            import os
            # Get frame snapshots for all threads.
            frames = sys._current_frames()

            print(f"\n{'='*60}", file=sys.stderr)
            print(f"Stack traces for {len(frames)} threads (PID: {os.getpid()})", file=sys.stderr)
            print(f"Signal: {signum} at {time.ctime()}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)

            for thread_id, frame in frames.items():
                thread_name = threading.get_ident() == thread_id and "Main" or f"Thread-{thread_id}"
                print(f"\nThread: {thread_name} (ID: 0x{thread_id:x})", file=sys.stderr)

                # Extract and format the stack.
                stack_lines = traceback.format_stack(frame)
                print("".join(stack_lines), file=sys.stderr)

            print(f"{'='*60}\n", file=sys.stderr)
            sys.stderr.flush()  # Ensure output in signal context.
        except Exception as e:
            print(f"Error in thread dump handler: {e}", file=sys.stderr)
            sys.stderr.flush()

    def _database_consumer_with_pool(self, work_queue, result_queue, thread_id, conn, pbar) -> None:
        """
        CONSUMER function for thread pool: executes database operations (single-threaded for DuckDB safety).

        PRODUCER-CONSUMER PATTERN: This is the CONSUMER.
        - Receives DatabaseOperation objects from producers via work_queue
        - Executes all database operations safely in single-threaded context
        - Producers never touch the database - only collect operations
        """
        batch_operations = []
        total = 0
        processed_xml_count = 0  # Counter for processed XML files (each generates one XML_FILE_UPDATE)
        self.log_info(f"Pool Consumer {thread_id} starting")

        # Set up progress callback for consumer
        def progress_callback(count):
            if pbar:
                pbar.update(count)

        while not self.shutdown_event.is_set():
            # Check if we need to trigger database optimization
            if processed_xml_count > 0 and processed_xml_count % self.OPTIMIZE_INTERVAL == 0:
                self.log_info(f"Processed {processed_xml_count} XML files, triggering database optimization")
                try:
                    # Create and execute OPTIMIZE_DATABASE operation
                    optimize_op = DatabaseOperation(DatabaseOperationType.OPTIMIZE_DATABASE, None)
                    self.consumer._execute_optimize_operation(optimize_op)
                except Exception as e:
                    self.log_error(f"Database optimization failed: {e}", exc_info=True)

            try:
                self.log_debug(f"Pool Consumer {thread_id} waiting for work queue item")
                # Use short timeout and check shutdown event to allow quick shutdown
                item = work_queue.get(timeout=1.0)  # Short timeout to check shutdown event frequently
                self.log_debug(f"Pool Consumer {thread_id} got item from work queue: type={type(item)}, item={item}")

                if item is None:
                    self.log_info(f"Pool Consumer {thread_id} received sentinel signal")
                    work_queue.task_done()
                    break

                # Handle DatabaseOperation objects from producer
                if isinstance(item, DatabaseOperation):
                    batch_operations.append(item)
                    total += 1
                else:
                    self.log_error(f"Unexpected work queue item type: {type(item)}, item: {item}")
                    work_queue.task_done()
                    continue

                work_queue.task_done()

                if len(batch_operations) >= self.BATCH_SIZE:
                    try:
                        # Track progress before batch processing
                        xml_ids_before = set(op.xml_id for op in batch_operations if op.xml_id)
                        self.log_debug(f"Processing batch with {len(batch_operations)} operations from XML IDs: {sorted(xml_ids_before)}")

                        self.log_info(f"Starting bulk insert batch of {len(batch_operations)} operations...")
                        # CONSUMER: Execute operations using XMLConsumer with progress callback
                        self.consumer.execute_operations_batch(batch_operations, progress_callback)
                        self.log_info("Bulk insert batch completed successfully")

                        # Log consumer execution completion
                        if not global_config.is_quiet():
                            log_info(self.logger, f"Pool Consumer {thread_id} executed batch of {len(batch_operations)} operations")

                        # Count XML files processed in this batch for optimization interval
                        xml_files_updated = self._get_xml_files_updated_in_batch(batch_operations)
                        xml_count_in_batch = len(xml_files_updated)
                        processed_xml_count += xml_count_in_batch

                        # Send progress update operation
                        if xml_count_in_batch > 0:
                            progress_op = DatabaseOperation(
                                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                                data={"count": xml_count_in_batch}
                            )
                            # Execute progress update immediately
                            self.consumer.execute_operations_batch([progress_op])

                        batch_operations = []  # Clear batch after successful commit
                    except Exception as e:
                        self.log_error(f"Batch error: {e}", exc_info=True)
                        # Don't clear batch_operations on error - will retry in final batch
            except queue.Empty:
                # Timeout occurred, check if we should shutdown
                if self.shutdown_event.is_set():
                    self.log_info(f"Pool Consumer {thread_id} received shutdown signal during queue wait")
                    break
                continue  # Continue the loop to check for more items
            except Exception as e:
                self.log_error(f"Pool Consumer {thread_id} error: {e}", exc_info=True)
                break

        # Final batch - commit any remaining work
        if batch_operations:
            try:
                self.log_info(f"Pool Consumer {thread_id} committing final batch of {len(batch_operations)} operations")
                xml_ids_before = set(op.xml_id for op in batch_operations if op.xml_id)
                self.log_debug(f"Processing final batch with {len(batch_operations)} operations from XML IDs: {sorted(xml_ids_before)}")

                self.log_info("Starting final bulk insert batch...")
                # CONSUMER: Execute final operations using XMLConsumer with progress callback
                self.consumer.execute_operations_batch(batch_operations, progress_callback)
                self.log_info("Final bulk insert batch completed successfully")

                # Count XML files processed in final batch
                xml_files_updated = self._get_xml_files_updated_in_batch(batch_operations)
                xml_count_in_batch = len(xml_files_updated)
                processed_xml_count += xml_count_in_batch
            except Exception as e:
                self.log_error(f"Final batch error: {e}", exc_info=True)

        self.log_info(f"Pool Consumer {thread_id} done: {total} operations, processed {processed_xml_count} XML files")
        # Put result count in result queue
        result_queue.put(total)
def _get_xml_files_updated_in_batch(self, batch_operations):
    """Get set of XML IDs that were updated in this batch (XML_FILE_UPDATE operations)"""
    xml_files_updated = set()
    for op in batch_operations:
        if op.operation_type == DatabaseOperationType.XML_FILE_UPDATE:
            xml_files_updated.add(op.xml_id)
    return xml_files_updated


class AddressDeduplicationStrategy(ProcessingStrategy):
    """
    Strategy for address deduplication processing using producer-consumer pattern.

    Processes addresses in batches to create master-child relationships based on
    canonical_address matching, following the same pattern as XML processing.
    """

    DEFAULT_BATCH_SIZE = 1000

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, batch_size: int = DEFAULT_BATCH_SIZE, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.batch_size = batch_size

    def execute(self, max_files: Optional[int] = None) -> int:
        """
        Execute address deduplication processing.

        Args:
            max_files: Maximum number of canonical addresses to process (for testing)

        Returns:
            Number of addresses updated (children that were linked to masters)
        """
        self.log_info("Starting address deduplication strategy")

        try:
            # Create the address deduplication processor
            processor = AddressDeduplicationProcessor(self.db_ops, self.batch_size)

            # Set up progress bar for address deduplication
            from logging_utils import start_progress_reporting

            # Estimate total canonical addresses that need deduplication for progress bar
            if max_files:
                total_operations = max_files
            else:
                # Count total canonical addresses that have duplicates and need deduplication
                count_query = """
                    SELECT COUNT(DISTINCT canonical_address) FROM Addresses
                    WHERE canonical_address IS NOT NULL
                        AND canonical_address != ''
                    GROUP BY canonical_address
                    HAVING COUNT(*) > 1
                        AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
                """
                result = self.db_ops.execute_query(f"SELECT COUNT(*) FROM ({count_query})")
                total_operations = result.fetchone()[0] if result else 0

            progress_desc = "Deduplicating addresses"
            pbar = start_progress_reporting(total=total_operations, desc=progress_desc, unit="addrs")

            # Execute deduplication with progress bar
            total_updated = processor.deduplicate_addresses(progress_bar=pbar)

            # Close progress bar
            if pbar:
                pbar.close()

            self.log_info(f"Address deduplication strategy complete: {total_updated} addresses updated")
            return total_updated

        except Exception as e:
            self.log_error(f"Address deduplication strategy failed: {e}", exc_info=True)
            return 0


class GeolocationStrategy(ProcessingStrategy):
    """
    Strategy for address geocoding processing using producer-consumer pattern.

    Processes addresses in batches to geocode them using census API,
    following the same pattern as XML processing.
    """

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)

    def execute(self, max_files: Optional[int] = None) -> int:
        """
        Execute address geocoding processing.

        Args:
            max_files: Maximum number of addresses to process (for testing)

        Returns:
            Number of addresses geocoded
        """
        self.log_info("Starting geolocation strategy")

        try:
            # Create the geolocation processor
            processor = GeolocationProcessor(self.db_ops)

            # Set up progress bar for geocoding
            from logging_utils import start_progress_reporting

            # Estimate total addresses that need geocoding for progress bar
            if max_files:
                total_operations = max_files
            else:
                # Count distinct canonical addresses that need geocoding
                count_query = """
                    SELECT COUNT(DISTINCT canonical_address) FROM Addresses
                    WHERE master_id IS NULL
                        AND (colocator IS NULL OR colocator = '')
                        AND canonical_address IS NOT NULL
                        AND canonical_address != ''
                """
                result = self.db_ops.execute_query(count_query)
                address_count = result.fetchone()[0] if result else 0

                # Count geocoding records that need API calls
                geocoding_query = """
                    SELECT COUNT(*) FROM Geocoding
                    WHERE geocoding_status = 'pending' OR geocoding_status IS NULL
                """
                geocoding_result = self.db_ops.execute_query(geocoding_query)
                geocoding_count = geocoding_result.fetchone()[0] if geocoding_result else 0

                total_operations = address_count + geocoding_count

            progress_desc = "Geolocating addresses"
            pbar = start_progress_reporting(total=total_operations, desc=progress_desc, unit="addrs")

            # Execute geocoding with progress bar
            total_geocoded = processor.geolocate_addresses(progress_bar=pbar)

            # Close progress bar
            if pbar:
                pbar.close()

            self.log_info(f"Geolocation strategy complete: {total_geocoded} addresses geocoded")
            return total_geocoded

        except Exception as e:
            self.log_error(f"Geolocation strategy failed: {e}", exc_info=True)
            return 0


class GeolocationStrategy(ProcessingStrategy):
    """
    Strategy for address geocoding processing using producer-consumer pattern.

    Processes addresses in batches to geocode them using the census API,
    following the same pattern as XML processing.
    """

    DEFAULT_BATCH_SIZE = 1000

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, batch_size: int = DEFAULT_BATCH_SIZE, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.batch_size = batch_size

    def execute(self, max_files: Optional[int] = None) -> int:
        """
        Execute address geocoding processing.

        Args:
            max_files: Maximum number of addresses to process (for testing)

        Returns:
            Number of addresses processed
        """
        self.log_info("Starting address geocoding strategy")

        try:
            # Set up progress bar for geocoding
            from logging_utils import start_progress_reporting

            # Estimate total canonical addresses that need geocoding for progress bar
            if max_files:
                total_operations = max_files
            else:
                # Count total canonical addresses that need geocoding (master addresses without colocator)
                count_query = """
                    SELECT COUNT(DISTINCT canonical_address) FROM Addresses
                    WHERE master_id IS NULL
                        AND (colocator IS NULL OR colocator = '')
                        AND canonical_address IS NOT NULL
                        AND canonical_address != ''
                """
                result = self.db_ops.execute_query(count_query)
                total_operations = result.fetchone()[0] if result else 0

            progress_desc = "Geocoding addresses"
            pbar = start_progress_reporting(total=total_operations, desc=progress_desc, unit="addrs")

            # Execute geocoding with progress bar
            total_processed = geolocate_addresses(self.db_ops, progress_bar=pbar)

            # Close progress bar
            if pbar:
                pbar.close()

            self.log_info(f"Address geocoding strategy complete: {total_processed} addresses processed")
            return total_processed

        except Exception as e:
            self.log_error(f"Address geocoding strategy failed: {e}", exc_info=True)
            return 0

