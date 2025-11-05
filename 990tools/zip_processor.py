#!/usr/bin/env python3
"""
zip_processor.py - ZIP file processing for IRS 990 data

This module handles the processing of IRS ZIP files, including
downloading, listing contents, and registering files in the database.
Uses producer-consumer pattern for safe batch processing with DuckDB.
"""

import os
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional

# Import producer-consumer pattern classes
from base_processor import BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models import ZipFile, XMLFile
from logging_utils import start_progress_reporting, stop_progress_reporting, update_progress, get_logger
from config import global_config


class ZipProducer(BaseProducer):
    """Producer for ZIP file processing operations"""

    def __init__(self, db_ops: DatabaseOperations, zips_dir: str):
        super().__init__(db_ops, batch_size=10)  # Process ZIPs in small batches
        self.zips_dir = zips_dir

    def _get_work_batch(self, offset: int) -> List[Path]:
        """Get a batch of ZIP files to process"""
        # For ZIP processing, we process all files in a directory
        # This is a simple implementation - get all ZIP files
        zip_files = []
        for zip_path in Path(self.zips_dir).glob("*.zip"):
            # Check if ZIP file is already processed
            zip_filename = zip_path.name
            existing_zip = self.db_ops.execute_query(
                "SELECT status FROM ZipFiles WHERE filename = ?",
                (zip_filename,)
            ).fetchone()

            if not existing_zip or existing_zip[0] != 'processed':
                zip_files.append(zip_path)

        # Apply offset and batch size
        start_idx = offset
        end_idx = start_idx + self.batch_size
        batch = zip_files[start_idx:end_idx] if start_idx < len(zip_files) else []

        return batch

    def _process_work_batch_to_contexts(self, batch: List[Path]) -> 'PendingDatabaseContext':
        """Process a batch of ZIP files into PendingDatabaseContext objects"""
        from pending_database_context import PendingDatabaseContext

        contexts = []

        for zip_path in batch:
            # Create context for this ZIP file
            context = PendingDatabaseContext()

            # Create ZipFile using factory method
            tax_year = int(zip_path.name[:4]) if zip_path.name[:4].isdigit() else 0
            zip_file = ZipFile.create_from_path(str(zip_path), tax_year)
            context.addObjectToDatabase(zip_file)

            contexts.append(context)

        return contexts


class ZipConsumer(BaseConsumer):
    """Consumer for ZIP file operations"""

    def __init__(self, db_ops: DatabaseOperations, zips_dir: str):
        super().__init__(db_ops)
        self.zips_dir = zips_dir

    def _process_operations_batch(self, operations_by_type):
        """Process ZIP file operations using standardized pattern"""
        # All operations are now handled by PendingDatabaseContext.save_to_database()
        # which executes all operations directly
        pass

    def _execute_zip_operation(self, operation):
        """Execute a ZIP file operation"""
        data = operation.data
        zip_path_str = data["zip_path"]
        zips_dir = data["zips_dir"]
        operation_type = data["operation"]

        if operation_type == "process_zip_contents":
            zip_path = Path(zip_path_str)
            self._process_single_zip(zip_path)

    def _execute_operations_batch(self, operations):
        """Execute a batch of operations using the consumer pattern"""
        # All operations are now handled by the base class execute_operations_batch method
        # which properly groups and executes operations in dependency order
        self.execute_operations_batch(operations)


class ZipProcessor:
    """Main processor for ZIP file processing using producer-consumer pattern"""

    def __init__(self, db_ops: DatabaseOperations, zips_dir: str):
        self.db_ops = db_ops
        self.zips_dir = zips_dir
        self.logger = get_logger("zip_processor")

        # Initialize producer and consumer
        self.producer = ZipProducer(db_ops, zips_dir)
        self.consumer = ZipConsumer(db_ops, zips_dir)

        # Initialize thread pool manager
        thread_config = ThreadPoolConfig(
            producer_config=PoolConfig(max_workers=2, queue_size=20),  # Process 2 ZIPs at a time
            consumer_config=PoolConfig(max_workers=1, queue_size=10)   # Single consumer for DB safety
        )
        self.thread_pool_manager = ThreadPoolManager(thread_config, self.logger)

    def process_zip_files(self, start_year: int, end_year: int) -> List[Path]:
        """Process ZIP files and register XML files using producer-consumer pattern with PendingDatabaseContext"""
        self.log_info(f"Processing ZIP files from {start_year} to {end_year} using producer-consumer pattern with PendingDatabaseContext")

        try:
            # Collect contexts using the new PendingDatabaseContext approach
            contexts = self.producer.collect_contexts()

            if not contexts:
                self.log_info("No ZIP files to process")
                return []

            self.log_info(f"Collected {len(contexts)} ZIP processing contexts")

            # Execute contexts using consumer
            total_processed = self.consumer.execute_contexts_batch(contexts)

            self.log_info(f"ZIP file processing complete: {total_processed} operations processed")
            return [Path(ctx._operations[0].data["zip_path"]) for ctx in contexts if ctx._operations]

        except Exception as e:
            self.log_error(f"ZIP processing failed: {e}", exc_info=True)
            return []

    def _producer_wrapper(self, zip_files: List[Path], work_queue, result_queue, thread_id: int, num_threads: int):
        """Wrapper for producer thread execution"""
        try:
            self.log_debug(f"ZIP Producer thread {thread_id} starting")

            # Distribute work items among threads
            for i in range(thread_id, len(zip_files), num_threads):
                zip_path = zip_files[i]

                # Create operation to process this ZIP file
                operation = DatabaseOperation(
                    operation_type=DatabaseOperationType.INSERT_ZIP_FILE,
                    data={
                        "zip_path": str(zip_path),
                        "zips_dir": self.zips_dir,
                        "operation": "process_zip_contents"
                    }
                )

                # Put operation in result queue for consumer
                result_queue.put(operation)

                self.log_debug(f"ZIP Producer {thread_id}: queued operation for {zip_path.name}")

        except Exception as e:
            self.log_error(f"ZIP Producer thread {thread_id} error: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)

    def _consumer_wrapper(self, result_queue, thread_id: int, num_producers: int, progress_bar=None):
        """Wrapper for consumer thread execution"""
        try:
            self.log_debug(f"ZIP Consumer thread {thread_id} starting")
            sentinels_received = 0

            while True:
                try:
                    operation = result_queue.get(timeout=1.0)
                    if operation is None:  # Sentinel
                        sentinels_received += 1
                        if sentinels_received >= num_producers:
                            break
                        continue

                    # Execute the operation
                    if isinstance(operation, DatabaseOperation):
                        self.consumer._execute_zip_operation(operation)

                        # Update progress
                        if progress_bar:
                            progress_bar.update(1)

                    result_queue.task_done()

                except:
                    continue

        except Exception as e:
            self.log_error(f"ZIP Consumer thread {thread_id} error: {e}", exc_info=True)

    def _process_single_zip(self, zip_path: Path):
        """Process a single ZIP file"""
        zip_filename = zip_path.name
        zip_year = int(zip_filename[:4]) if zip_filename[:4].isdigit() else 0

        # Create ZipFile object
        zip_file = ZipFile(
            filename=zip_filename,
            file_path=str(zip_path),
            tax_year=zip_year,
            file_size=zip_path.stat().st_size if zip_path.exists() else None,
            status='downloaded'
        )

        # Use filename + size as simple integrity check (no checksum needed)
        if zip_path.exists():
            zip_file.checksum = f"{zip_path.name}:{zip_path.stat().st_size}"

        # Create PDC context for this ZIP processing
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()

        # Add ZIP file to context
        context.addObjectToDatabase(zip_file)

        # Extract XML file listing and sizes in one pass
        xml_sizes = self._get_all_xml_sizes(zip_path)
        xml_files = list(xml_sizes.keys())
        print(f"Found {len(xml_files)} XML files")

        # DEBUG: Log XML file details for validation
        print(f"DEBUG: ZIP {zip_filename} - XML files found: {len(xml_files)}")
        if xml_files:
            print(f"DEBUG: First 5 XML files: {xml_files[:5]}")
            print(f"DEBUG: XML file sizes: {list(xml_sizes.values())[:5]}")

        # Create XML file objects and add to context
        for xml_filename in xml_files:
            xml_file = zip_file.create_xml_file(
                filename=xml_filename,
                internal_path=xml_filename,
                file_size=xml_sizes[xml_filename]
            )
            context.addObjectToDatabase(xml_file)

        # Add ZIP status update operation
        from database_operations import DatabaseOperation, DatabaseOperationType
        # Note: We need the zip_id, but we don't have it yet since PDC hasn't executed
        # This is a limitation - for now we'll handle status updates separately
        # In a full PDC refactor, this would be handled by PDC as well

        # Execute the context operations directly
        context.save_to_database(self.db_ops)

        # Get the zip_id from the first operation (INSERT_ZIP_FILE)
        zip_id = None
        for op in operations:
            if hasattr(op, 'data') and 'zip_id' in str(op.data):
                # This is a hack - in a proper refactor, PDC would return IDs
                # For now, query the database to get the zip_id
                result = self.db_ops.execute_query(
                    "SELECT zip_id FROM ZipFiles WHERE filename = ? ORDER BY zip_id DESC LIMIT 1",
                    (zip_filename,)
                ).fetchone()
                if result:
                    zip_id = result[0]
                break

        if zip_id:
            # Update ZIP status (this should eventually be handled by PDC too)
            self.db_ops.update_zip_status(zip_id, 'processed')
            print(f"DEBUG: Updated ZIP {zip_filename} status to 'processed'")
            print(f"Registered ZIP file: {zip_filename} (ID: {zip_id})")
        else:
            print(f"WARNING: Could not determine zip_id for {zip_filename}")

    def _get_xml_files_from_zip(self, zip_path: Path) -> List[str]:
        """Get XML files from ZIP"""
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Get XML files from ZIP
            xml_files = [f for f in zip_ref.namelist() if f.endswith('.xml')]
        return xml_files

    def _get_all_xml_sizes(self, zip_path: Path) -> dict:
        """Get all XML file sizes from ZIP in one pass, handling corrupt files gracefully"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                xml_sizes = {}
                for info in zip_ref.filelist:
                    if info.filename.endswith('.xml'):
                        xml_sizes[info.filename] = info.file_size
                print(f"DEBUG: ZIP {zip_path.name} - Extracted {len(xml_sizes)} XML files from ZIP filelist")
                return xml_sizes
        except zipfile.BadZipFile as e:
            print(f"Corrupt ZIP file {zip_path}: {e}")
            return {}
        except Exception as e:
            print(f"Error reading ZIP file {zip_path}: {e}")
            return {}

