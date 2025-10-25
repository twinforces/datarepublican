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
from lxml import etree as ET
from lxml import etree
from dataclasses import fields
import zipfile
from threading import Lock
from enum import Enum

from database_operations import DatabaseOperations
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from parse_990 import parse_990
from parse_990ez import parse_990ez
from parse_990pf import parse_990pf
from parse_utils import parse_grants
from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF
from geolocation_processor import GeolocationProcessor
from address_matcher import AddressMatcher
from constants import VALID_STATES
from zip_processor import ZipProcessor
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config


class DatabaseOperationType(Enum):
    """Enumeration of database operation types for flexible processing"""
    INSERT_CHARITY = "insert_charity"
    INSERT_OFFICER = "insert_officer"
    INSERT_GRANT = "insert_grant"
    INSERT_CONTRACTOR = "insert_contractor"
    INSERT_POLITICAL_CONTRIBUTION = "insert_political_contribution"
    INSERT_ADDRESS = "insert_address"
    UPDATE_XML_FILE_SUCCESS = "update_xml_file_success"
    UPDATE_XML_FILE_ERROR = "update_xml_file_error"


class DatabaseOperation:
    """Represents a single database operation with its data and dependencies"""

    def __init__(self, operation_type: DatabaseOperationType, data: Any, xml_id: int = None,
                 dependencies: Optional[List[str]] = None):
        self.operation_type = operation_type
        self.data = data
        self.xml_id = xml_id
        self.dependencies = dependencies or []  # List of operation types this depends on


class ProcessingStrategy(ABC):
    """Abstract base class for processing strategies"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        self.db_ops = db_ops
        self.logger = logger
        self.quiet = quiet

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the processing strategy"""
        pass

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not self.quiet:
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
        if not self.quiet:
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)


class ParallelXMLProcessingStrategy(ProcessingStrategy):
    """Strategy for parallel XML file processing using producer-consumer pattern"""

    MAX_WORKERS = 16
    QUEUE_SIZE = 1000
    BATCH_SIZE = 100
    STALL_THRESHOLD = 30

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, workers: int = MAX_WORKERS, quiet: bool = False):
        super().__init__(db_ops, logger, global_config.is_quiet())
        self.workers = workers
        # Set up SIGUSR1 handler for thread stack dumps
        signal.signal(signal.SIGUSR1, self.dump_threads_handler)

    # Lock-free queue implementation for single-file processing

    def _get_xml_files_to_process(self) -> List[Tuple]:
        """Get list of XML files to process from database"""
        result = self.db_ops.execute_query("""
            SELECT xf.xml_id, zf.file_path, xf.filename, xf.internal_path, xf.file_size
            FROM XmlFiles xf
            JOIN ZipFiles zf ON xf.zip_id = zf.zip_id
            WHERE xf.processed = FALSE
            ORDER BY xf.xml_id
        """)
        return result.fetchall()

    def execute(self, max_files: Optional[int] = None) -> int:
        """Process XML files using producer-consumer pattern with thread-safe queues"""
        # Set up signal handlers for graceful shutdown and thread debugging
        def interrupt_handler(signum, frame):
            self.log_error("Received interrupt signal, shutting down gracefully...")
            try:
                from logging_utils import stop_progress_reporting
                stop_progress_reporting()
            except:
                pass
            sys.exit(1)

        signal.signal(signal.SIGINT, interrupt_handler)

        xml_files = self._get_xml_files_to_process()
        if max_files:
            xml_files = xml_files[:max_files]
        if not xml_files:
            return 0

        num_producers = min(self.workers, len(xml_files))
        self.log_info(f"Processing {len(xml_files)} files with {num_producers} producers")

        # Set up progress bar for XML processing
        from tqdm import tqdm
        progress_unit = "file" if global_config.progress == "files" else "B"
        progress_desc = "Processing XML files" if global_config.progress == "files" else "Processing XML bytes"
        pbar = tqdm(total=len(xml_files), desc=progress_desc, unit=progress_unit)

        # Use thread-safe queues with backpressure
        xml_queue = queue.Queue(maxsize=self.QUEUE_SIZE)

        # Start consumer thread (single writer to database)
        consumer_thread = threading.Thread(
            target=self._database_consumer,
            args=(xml_queue, num_producers, self.db_ops.db_conn, pbar)
        )
        consumer_thread.daemon = True
        consumer_thread.start()

        # Start producer threads
        producer_threads = []
        for i in range(num_producers):
            t = threading.Thread(target=self._xml_producer, args=(xml_files, xml_queue, i, num_producers))
            t.daemon = True
            producer_threads.append(t)
            t.start()

        # Wait for producers to finish (parse-heavy operations)
        for i, t in enumerate(producer_threads):
            t.join(timeout=300.0)  # Increased timeout to 5 minutes for large XML files
            if t.is_alive():
                self.log_info(f"Producer {i} timeout after 5 minutes - XML processing may be stuck")
                # Don't kill the thread, let it continue in background
            else:
                self.log_info(f"Producer {i} done")

        # Drain queue and wait for consumer
        xml_queue.join()
        consumer_thread.join(timeout=30.0)
        if consumer_thread.is_alive():
            self.log_error("Consumer timeout - attempting graceful shutdown")
            # Signal consumer to stop by putting a special shutdown message
            try:
                xml_queue.put(('shutdown',), block=False)
                consumer_thread.join(timeout=10.0)
                if consumer_thread.is_alive():
                    self.log_error("Consumer still alive after shutdown signal - some data may be lost")
            except:
                self.log_error("Failed to signal consumer shutdown - some data may be lost")

        # Close progress bar
        pbar.close()

        self.log_info(f"XML processing complete: {len(xml_files)} files processed")
        return len(xml_files)

    def _xml_producer(self, xml_files, xml_queue, producer_id, num_producers):
        """Producer thread: parses XML and sends operations list to consumer"""
        processed = 0
        total_bytes = 0
        start = producer_id
        for i in range(start, len(xml_files), num_producers):
            xml_id, path, filename, internal, file_size = xml_files[i]
            try:
                operations = self._process_single_xml(xml_id, path, filename, internal, file_size)
                xml_queue.put(operations, block=True)
                processed += 1

                # Track progress based on config
                if global_config.progress == "bytes":
                    if file_size:
                        total_bytes += file_size

                if processed % 50 == 0:
                    progress_info = f"{processed} files" if global_config.progress == "files" else f"{total_bytes} bytes"
                    self.log_info(f"Producer {producer_id}: {progress_info} queued")
            except Exception as e:
                self.log_error(f"Producer {producer_id} error on {filename}: {e}", exc_info=True)
                error_msg = str(e)
                if not error_msg or error_msg == "":
                    error_msg = f"Unknown error processing {filename}"
                # Create error operation for failed processing
                error_operation = DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_FILE_ERROR,
                    {"error_message": error_msg},
                    xml_id
                )
                xml_queue.put([error_operation], block=True)
        xml_queue.put(None)  # Sentinel
        self.log_info(f"Producer {producer_id} done: {processed} files")

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

    def _database_consumer(self, xml_queue, num_expected, conn, pbar):
        """Consumer thread: writes operations to database (single-threaded for DuckDB safety)"""
        batch_operations = []
        total = 0
        signals = 0
        while signals < num_expected:
            try:
                item = xml_queue.get(timeout=30.0)  # Add timeout to prevent hanging
                if item is None:
                    signals += 1
                    xml_queue.task_done()
                    continue
                if isinstance(item, tuple) and item[0] == 'shutdown':
                    self.log_info("Consumer received shutdown signal")
                    xml_queue.task_done()
                    break

                # Handle list of operations from producer
                if isinstance(item, list):
                    batch_operations.extend(item)
                    total += len(item)
                else:
                    # Legacy support for single operations (shouldn't happen with new code)
                    batch_operations.append(item)
                    total += 1

                xml_queue.task_done()

                if len(batch_operations) >= self.BATCH_SIZE:
                    try:
                        self._bulk_insert_batch(batch_operations, conn)
                        # Update progress bar based on config
                        if global_config.progress == "files":
                            # Count unique XML files processed in this batch
                            xml_ids_in_batch = set(op.xml_id for op in batch_operations if op.xml_id)
                            pbar.update(len(xml_ids_in_batch))
                        else:
                            # For bytes mode, we need to track file sizes differently now
                            # This will be handled in the bulk_insert_batch method
                            pbar.update(len(batch_operations))  # Temporary - will be fixed
                        batch_operations = []  # Clear batch after successful commit
                    except Exception as e:
                        self.log_error(f"Batch error: {e}", exc_info=True)
                        # Don't clear batch_operations on error - will retry in final batch
            except Exception as e:
                self.log_error(f"Consumer error: {e}", exc_info=True)
                break

        # Final batch - commit any remaining work
        if batch_operations:
            try:
                self.log_info(f"Committing final batch of {len(batch_operations)} operations")
                self._bulk_insert_batch(batch_operations, conn)
                # Update progress bar for final batch based on config
                if global_config.progress == "files":
                    xml_ids_in_batch = set(op.xml_id for op in batch_operations if op.xml_id)
                    pbar.update(len(xml_ids_in_batch))
                else:
                    # For bytes mode, we need to track file sizes differently now
                    pbar.update(len(batch_operations))  # Temporary - will be fixed
            except Exception as e:
                self.log_error(f"Final batch error: {e}", exc_info=True)

        self.log_info(f"Consumer done: {total} operations, {signals} signals")
        return total

    def _build_insert_from_dataclass(self, obj, table_name: str, exclude_fields: Optional[List[str]] = None) -> Tuple[str, Tuple]:
        """Build INSERT statement and values tuple from dataclass using reflection"""
        if exclude_fields is None:
            exclude_fields = []

        # Get all fields from the dataclass, excluding specified ones
        field_names = [f.name for f in fields(obj) if f.name not in exclude_fields]

        # Build column list and placeholders
        columns = ', '.join(field_names)
        placeholders = ', '.join('?' for _ in field_names)

        # Build values tuple
        values = tuple(getattr(obj, field_name) for field_name in field_names)

        # Debug logging: show fields and values being inserted
        self.log_debug(f"INSERT {table_name}: fields={field_names}, values={values}")

        # Use INSERT OR IGNORE for reference/master tables to preserve first inserted record,
        # INSERT OR REPLACE for detail/transaction tables to handle legitimate duplicates
        if table_name in ('Charities', 'Addresses', 'XmlFiles', 'ZipFiles'):
            sql = f"INSERT OR IGNORE INTO {table_name} ({columns}) VALUES ({placeholders})"
        else:
            sql = f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})"

        return sql, values

    def _bulk_insert_batch(self, batch_operations, conn=None):
        """Bulk insert a batch of database operations"""
        if conn is None:
            conn = self.db_ops.db_conn

        # Check database connection state
        try:
            # Test connection with a simple query
            test_result = conn.execute("SELECT 1").fetchone()
            self.log_info(f"Database connection test: OK (result={test_result})")
        except Exception as e:
            self.log_error(f"Database connection test FAILED: {e}")
            return

        self.log_info(f"Bulk insert batch: STARTING with {len(batch_operations)} operations")

        # Group operations by type for efficient processing
        operations_by_type = {}
        xml_ids_in_batch = set()

        for operation in batch_operations:
            if not isinstance(operation, DatabaseOperation):
                self.log_error(f"Invalid operation type: {type(operation)}, expected DatabaseOperation")
                continue

            xml_ids_in_batch.add(operation.xml_id)
            op_type = operation.operation_type.value
            if op_type not in operations_by_type:
                operations_by_type[op_type] = []
            operations_by_type[op_type].append(operation)

        self.log_info(f"Operations by type: { {k: len(v) for k, v in operations_by_type.items()} }")

        # Process operations in dependency order
        processed_xml_ids = set()

        try:
            # 1. Handle XML file updates (errors and successes) first
            self._process_xml_file_operations(operations_by_type, conn, processed_xml_ids)

            # 2. Insert charities
            charity_id_map = self._process_charity_operations(operations_by_type, conn)

            # 3. Insert related data (officers, grants, etc.) using charity_id_map
            self._process_related_operations(operations_by_type, conn, charity_id_map)

            # 4. Insert addresses
            self._process_address_operations(operations_by_type, conn, charity_id_map)

            # Commit all changes
            self.log_info("Bulk insert: STARTING batch commit")
            conn.commit()
            self.log_info("Bulk insert: FINISHED batch commit successful")

        except Exception as e:
            self.log_error(f"Failed to process batch: {e}", exc_info=True)
            try:
                conn.rollback()
                self.log_info("Bulk insert: Rollback completed")
            except Exception as rollback_e:
                self.log_error(f"Failed to rollback: {rollback_e}", exc_info=True)

            # Mark unprocessed XML files as having errors
            unprocessed_xml_ids = xml_ids_in_batch - processed_xml_ids
            for xml_id in unprocessed_xml_ids:
                try:
                    error_msg = self.format_error_with_traceback(e, "Batch processing failed")
                    self.log_debug(f"Marking XmlFile {xml_id} as batch error: {error_msg[:100]}...")
                    self.db_ops.execute_query(
                        "UPDATE XmlFiles SET processed=TRUE, processing_version=2, error_message=? WHERE xml_id=?",
                        (error_msg, xml_id)
                    )
                    self.db_ops.commit()
                except Exception as mark_error_e:
                    self.log_error(f"Failed to mark xml_id {xml_id} as error: {mark_error_e}")
            raise  # Re-raise to propagate the error

    def _process_xml_file_operations(self, operations_by_type, conn, processed_xml_ids):
        """Process XML file update operations (success/error)"""
        # Handle error updates
        if DatabaseOperationType.UPDATE_XML_FILE_ERROR.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.UPDATE_XML_FILE_ERROR.value]:
                data = operation.data
                error_msg = data.get("error_message", "Unknown error")
                form_type = data.get("form_type")
                file_size = data.get("file_size")

                try:
                    if form_type == "990T":
                        self.db_ops.execute_query(
                            "UPDATE XmlFiles SET processed=TRUE, processing_version=2, error_message=?, form_type=?, file_size=? WHERE xml_id=?",
                            (error_msg, form_type, file_size, operation.xml_id)
                        )
                    else:
                        self.db_ops.execute_query(
                            "UPDATE XmlFiles SET processed=TRUE, processing_version=2, error_message=? WHERE xml_id=?",
                            (error_msg, operation.xml_id)
                        )
                    processed_xml_ids.add(operation.xml_id)
                except Exception as e:
                    self.log_error(f"Failed to update XML file {operation.xml_id} with error: {e}")

        # Handle success updates
        if DatabaseOperationType.UPDATE_XML_FILE_SUCCESS.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.UPDATE_XML_FILE_SUCCESS.value]:
                data = operation.data
                form_type = data.get("form_type", "Unknown")
                ein = data.get("ein")
                tax_year = data.get("tax_year")

                try:
                    self.db_ops.execute_query(
                        "UPDATE XmlFiles SET processed=TRUE, processing_version=2, form_type=?, ein=?, tax_year=? WHERE xml_id=?",
                        (form_type, ein, tax_year, operation.xml_id)
                    )
                    processed_xml_ids.add(operation.xml_id)
                except Exception as e:
                    self.log_error(f"Failed to update XML file {operation.xml_id} with success: {e}")

    def _process_charity_operations(self, operations_by_type, conn):
        """Process charity insert operations and return charity_id mapping"""
        charity_id_map = {}  # xml_id -> charity_id

        if DatabaseOperationType.INSERT_CHARITY.value not in operations_by_type:
            return charity_id_map

        charities = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]]

        if not charities:
            return charity_id_map

        self.log_info(f"Bulk insert: Processing {len(charities)} charities")

        # Set colocator to 'notyet' for new charities
        for charity in charities:
            charity.colocator = 'notyet'

        charity_data = []
        sql = None
        for charity in charities:
            sql, values = self._build_insert_from_dataclass(charity, 'Charities', ['charity_id'])
            charity_data.append(values)

        try:
            if sql is not None:
                conn.executemany(sql, charity_data)
                self.log_info(f"Bulk insert: Inserted {len(charity_data)} charities")

                # Get the charity IDs for mapping
                ein_list = [c.ein for c in charities]
                tax_year_list = [c.tax_year for c in charities]
                placeholders = ','.join('?' for _ in ein_list)
                result = conn.execute(f"""
                    SELECT charity_id, ein, tax_year FROM Charities
                    WHERE ein IN ({placeholders}) AND tax_year IN ({placeholders})
                    ORDER BY charity_id
                """, ein_list + tax_year_list)

                # Map xml_id to charity_id using the operations list
                charity_ops = operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]
                for i, row in enumerate(result.fetchall()):
                    if i < len(charity_ops):
                        xml_id = charity_ops[i].xml_id
                        charity_id_map[xml_id] = row[0]

        except Exception as e:
            self.log_error(f"Failed to insert charities: {e}", exc_info=True)
            raise

        return charity_id_map

    def _process_related_operations(self, operations_by_type, conn, charity_id_map):
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
                officer_data = []
                sql = None
                for officer in officers:
                    sql, values = self._build_insert_from_dataclass(officer, 'Officers', ['officer_id'])
                    officer_data.append(values)

                try:
                    if sql is not None:
                        conn.executemany(sql, officer_data)
                        self.log_debug(f"Inserted {len(officer_data)} officers")
                except Exception as e:
                    self.log_error(f"Failed to insert officers: {e}", exc_info=True)

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
                grant_data = []
                sql = None
                for grant in grants:
                    sql, values = self._build_insert_from_dataclass(grant, 'Grants', ['grant_id'])
                    grant_data.append(values)

                try:
                    if sql is not None:
                        conn.executemany(sql, grant_data)
                        self.log_debug(f"Inserted {len(grant_data)} grants")
                except Exception as e:
                    self.log_error(f"Failed to insert grants: {e}", exc_info=True)

        # Process contractors
        if DatabaseOperationType.INSERT_CONTRACTOR.value in operations_by_type:
            contractors = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_CONTRACTOR.value]]

            if contractors:
                contractor_data = []
                sql = None
                for contractor in contractors:
                    sql, values = self._build_insert_from_dataclass(contractor, 'Contractors', ['contractor_id'])
                    contractor_data.append(values)

                try:
                    if sql is not None:
                        conn.executemany(sql, contractor_data)
                        self.log_debug(f"Inserted {len(contractor_data)} contractors")
                except Exception as e:
                    self.log_error(f"Failed to insert contractors: {e}", exc_info=True)

        # Process political contributions
        if DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION.value in operations_by_type:
            contributions = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION.value]]

            if contributions:
                contribution_data = []
                sql = None
                for contribution in contributions:
                    sql, values = self._build_insert_from_dataclass(contribution, 'PoliticalContributions', ['political_id'])
                    contribution_data.append(values)

                try:
                    if sql is not None:
                        conn.executemany(sql, contribution_data)
                        self.log_debug(f"Inserted {len(contribution_data)} contributions")
                except Exception as e:
                    self.log_error(f"Failed to insert contributions: {e}", exc_info=True)

    def _process_address_operations(self, operations_by_type, conn, charity_id_map):
        """Process address insert operations"""

        if DatabaseOperationType.INSERT_ADDRESS.value not in operations_by_type:
            return

        addresses = []
        for operation in operations_by_type[DatabaseOperationType.INSERT_ADDRESS.value]:
            address = operation.data

            # Compute colocator for Address objects if not already set
            if hasattr(address, 'colocator') and address.colocator is None:
                if address.po_box and address.zip_code:
                    po_box_stripped = address.po_box.strip()
                    if po_box_stripped:
                        address.colocator = f"PO:{po_box_stripped}:{address.zip_code}"
                elif address.state and address.state.upper() not in VALID_STATES:
                    address.colocator = f"FA:{address.state}"
                else:
                    address.colocator = None

            # Set owner_id for charity addresses (link to charity that owns this address)
            if hasattr(address, 'address_type') and address.address_type == 'charity':
                if operation.xml_id in charity_id_map:
                    address.owner_id = charity_id_map[operation.xml_id]

            addresses.append(address)

        if addresses:
            address_data = []
            sql = None
            for address in addresses:
                sql, values = self._build_insert_from_dataclass(address, 'Addresses', ['address_id'])
                address_data.append(values)

            try:
                if sql is not None:
                    conn.executemany(sql, address_data)
                    self.log_debug(f"Inserted {len(address_data)} addresses")
            except Exception as e:
                self.log_error(f"Failed to insert addresses: {e}", exc_info=True)


    def _process_single_xml(self, xml_id: int, zip_path: str, filename: str, internal_path: str, file_size: int = None):
        """Process a single XML file and return a list of database operations"""
        operations = []
        try:
            self.log_debug(f"Processing XML {filename} (ID: {xml_id})")

            # Read XML content directly from ZIP file using cached connection
            xml_content = self._extract_xml_from_zip(zip_path, internal_path)

            # Update file_size in XmlFiles table if not already set
            if file_size is None:
                file_size = len(xml_content)
                try:
                    self.log_debug(f"Updating XmlFile {xml_id} with file_size={file_size}")
                    self.db_ops.execute_query(
                        "UPDATE XmlFiles SET file_size=? WHERE xml_id=?",
                        (file_size, xml_id)
                    )
                    self.db_ops.commit()
                except Exception as size_error:
                    self.log_error(f"Failed to update file_size for {xml_id}: {size_error}", exc_info=True)

            self.log_debug(f"Retrieved XML content for {filename}, size: {len(xml_content)} bytes")

            # Parse XML
            parser = ET.XMLParser(recover=True)
            tree = ET.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Check for malformed XML
            if root is None or len(root) == 0:
                raise ValueError("Malformed XML: unable to parse root element")

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            self.log_debug(f"Extracted metadata for {filename}: form_type={form_type}, tax_year={tax_year}, ein={filer_ein}")

            if not filer_ein or filer_ein == "Unknown":
                self.log_error(f"Skipping XML {filename}: invalid EIN {filer_ein}")
                error_msg = f"Invalid EIN {filer_ein} for {filename}"
                operations.append(DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_FILE_ERROR,
                    {"error_message": error_msg, "file_size": file_size},
                    xml_id
                ))
                return operations

            # Update XmlFiles with extracted metadata before processing
            try:
                self.log_debug(f"Updating XmlFile {xml_id} with extracted metadata: ein={filer_ein}, tax_year={tax_year}, form_type={form_type}")
                self.db_ops.execute_query(
                    "UPDATE XmlFiles SET ein=?, tax_year=?, form_type=? WHERE xml_id=?",
                    (filer_ein, tax_year, form_type, xml_id)
                )
                self.db_ops.commit()
            except Exception as update_error:
                self.log_error(f"Failed to update XmlFile metadata for {xml_id}: {update_error}", exc_info=True)

            # Extract data based on form type
            if form_type == "990T":
                # Form 990T is a tax return for unrelated business income, not a charity filing
                self.log_info(f"Ignoring Form 990T file {filename} (tax return for unrelated business income)")
                operations.append(DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_FILE_ERROR,
                    {"error_message": "skipped: 990T", "form_type": "990T", "file_size": file_size},
                    xml_id
                ))
                return operations
            elif form_type == "990":
                charity, officers, grants, contractors, contributions, address = self._parse_990_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990EZ":
                charity, officers, grants, contractors, contributions, address = self._parse_990ez_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990PF":
                charity, officers, grants, contractors, contributions, address = self._parse_990pf_data(root, filename, filer_ein, tax_year, form_type)
            else:
                self.log_info(f"Unsupported form type {form_type} in {filename}")
                error_msg = f"Unsupported form type {form_type} for {filename}"
                operations.append(DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_FILE_ERROR,
                    {"error_message": error_msg},
                    xml_id
                ))
                return operations

            if charity:
                self.log_debug(f"Successfully parsed {filename}: charity={charity.ein}, grants={len(grants)}, officers={len(officers)}, address={address is not None}")

                # Create operations for all extracted data
                operations.append(DatabaseOperation(
                    DatabaseOperationType.INSERT_CHARITY,
                    charity,
                    xml_id
                ))

                for officer in officers:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_OFFICER,
                        officer,
                        xml_id,
                        [DatabaseOperationType.INSERT_CHARITY]  # Depends on charity being inserted first
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

                if address:
                    operations.append(DatabaseOperation(
                        DatabaseOperationType.INSERT_ADDRESS,
                        address,
                        xml_id
                    ))

                # Success operation to mark XML file as processed
                operations.append(DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_FILE_SUCCESS,
                    {"form_type": form_type, "ein": filer_ein, "tax_year": tax_year},
                    xml_id
                ))
            else:
                self.log_error(f"Failed to extract charity data from {filename}")
                error_msg = f"No Charity object created for {filename}"
                operations.append(DatabaseOperation(
                    DatabaseOperationType.UPDATE_XML_FILE_ERROR,
                    {"error_message": error_msg},
                    xml_id
                ))

        except Exception as e:
            self.log_error(f"Failed to process XML {filename}: {e}", exc_info=True)
            error_msg = str(e)
            if not error_msg or error_msg == "":
                error_msg = f"Unknown error processing {filename}"
            operations.append(DatabaseOperation(
                DatabaseOperationType.UPDATE_XML_FILE_ERROR,
                {"error_message": error_msg},
                xml_id
            ))

        return operations

    def _extract_form_type(self, root) -> str:
        """Extract form type from XML"""
        for xpath in XPATHS_990["form_type"] + XPATHS_990EZ["form_type"] + XPATHS_990PF["form_type"]:
            try:
                result = xpath(root)
                if result:
                    return result[0].text
            except:
                continue
        return "Unknown"

    def _extract_tax_year(self, root) -> int:
        """Extract tax year from XML"""
        for xpath in XPATHS_990["tax_year"] + XPATHS_990EZ["tax_year"] + XPATHS_990PF["tax_year"]:
            try:
                result = xpath(root)
                if result:
                    year_str = result[0].text
                    if year_str and year_str.isdigit():
                        return int(year_str)
            except:
                continue
        return 0

    def _extract_filer_ein(self, root) -> str:
        """Extract filer EIN from XML"""
        for xpath in XPATHS_990["filer_ein"] + XPATHS_990EZ["filer_ein"] + XPATHS_990PF["filer_ein"]:
            try:
                result = xpath(root)
                if result:
                    raw_ein = result[0].text.strip()
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        return formatted_ein
                    else:
                        return "Unknown"
            except:
                continue
        return "Unknown"

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str):
        """Parse Form 990 data"""
        charity, officers, grants, contractors, contributions, address = parse_990(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not charity:
            return None, [], [], [], [], None

        return charity, officers, grants, contractors, contributions, address

    def _parse_990ez_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str):
        """Parse Form 990EZ data"""
        charity, officers, grants, contractors, contributions, address = parse_990ez(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not charity:
            return None, [], [], [], [], None

        return charity, officers, grants, contractors, contributions, address

    def _parse_990pf_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str):
        """Parse Form 990PF data"""
        charity, officers, grants, contractors, contributions, address = parse_990pf(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not charity:
            return None, [], [], [], [], None

        return charity, officers, grants, contractors, contributions, address

    def _extract_grants_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990"""
        grants = []
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year,
                grantee_name=grant_data.get("grantee_name") or "Unknown"
            )
            grants.append(grant)
        return grants

    def _extract_xml_from_zip(self, zip_path: str, internal_path: str) -> bytes:
        """Extract XML content from ZIP file using cached connection"""
        zip_key = str(zip_path)

        with ZipProcessor._zip_cache_lock:
            if zip_key not in ZipProcessor._zip_cache:
                # Open ZIP file and cache the connection
                ZipProcessor._zip_cache[zip_key] = zipfile.ZipFile(zip_path, 'r')
                self.log_debug(f"Opened and cached ZIP connection for {zip_path}")

            zip_ref = ZipProcessor._zip_cache[zip_key]

        # Extract XML content from cached connection
        with zip_ref.open(internal_path) as f:
            return f.read()

    def _extract_grants_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990EZ"""
        grants = []
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990EZ")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year,
                grantee_name=grant_data.get("grantee_name") or "Unknown"
            )
            grants.append(grant)
        return grants

    def _extract_grants_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990PF"""
        grants = []
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990PF")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year,
                grantee_name=grant_data.get("grantee_name") or "Unknown"
            )
            grants.append(grant)
        return grants

    def _extract_contractors_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990"""
        return []

    def _extract_contractors_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990EZ"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_contractors_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990PF"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990"""
        return []

    def _extract_political_contributions_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990EZ"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990PF"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)


class GeocodingBatchStrategy(ProcessingStrategy):
    """Strategy for batch geocoding addresses - DEPRECATED: Use GeolocationProcessor instead"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.log_warning("GeocodingBatchStrategy is deprecated. Use GeolocationProcessor instead.")

    def execute(self, batch: List[Address]) -> int:
        """Geolocate a batch of addresses - DEPRECATED"""
        self.log_warning("GeocodingBatchStrategy.execute() is deprecated. Use GeolocationProcessor.geolocate_addresses() instead.")
        processor = GeolocationProcessor(self.db_ops)
        return processor._geolocate_batch(batch)


class AddressMatchingStrategy(ProcessingStrategy):
    """Strategy for matching grants by address/colocator - DEPRECATED: Use AddressMatcher instead"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.log_warning("AddressMatchingStrategy is deprecated. Use AddressMatcher instead.")

    def execute(self) -> int:
        """Match grants with unknown EINs by address/colocator - DEPRECATED"""
        self.log_warning("AddressMatchingStrategy.execute() is deprecated. Use AddressMatcher.match_grants_by_address() instead.")
        matcher = AddressMatcher(self.db_ops)
        return matcher.match_grants_by_address()


class StubCharityCreationStrategy(ProcessingStrategy):
    """Strategy for creating stub charities for unmatched grants - DEPRECATED: Use AddressMatcher instead"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.log_warning("StubCharityCreationStrategy is deprecated. Use AddressMatcher instead.")

    def execute(self, name: str, address: str, zip_code: str, po_box: str, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants - DEPRECATED"""
        self.log_warning("StubCharityCreationStrategy.execute() is deprecated. Use AddressMatcher._create_stub_charity_for_grant() instead.")
        # Generate a pseudo-EIN for stub records
        stub_ein = f"STUB{hash(name + (address or '') + str(tax_year)) % 1000000000:09d}"

        # Check if stub already exists
        result = self.db_ops.execute_query("SELECT 1 FROM Charities WHERE ein = ?", (stub_ein,))
        if result.fetchone():
            return stub_ein

        # Create stub charity
        from models import Charity as DBCharity
        charity = DBCharity(
            ein=stub_ein,
            tax_year=tax_year,
            filer_name=name or "Unknown",
            xml_name=f"stub_{stub_ein}_{tax_year}"
        )
        charity_id = self.db_ops.insert_charity(charity)

        # Create address record if we have address info
        if address or zip_code:
            from models import Address as DBAddress
            addr = DBAddress(
                ein=stub_ein,
                name=name or "Unknown",
                zip_code=zip_code,
                po_box=po_box,
                address_line1=address or "",
                address_type="grantee",
                colocator=None
            )
            self.db_ops.insert_address(addr)

        return stub_ein