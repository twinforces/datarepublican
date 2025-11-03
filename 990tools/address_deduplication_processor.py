#!/usr/bin/env python3
"""
address_deduplication_processor.py - Address deduplication processor

This module handles deduplication of addresses in the database by creating
master-child relationships based on canonical_address matching.

Now uses Producer-Consumer pattern for safe batch processing with DuckDB.
"""

import logging
import os
import time
from typing import Optional, List, Dict, Any
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning
from config import global_config
from constants import ADDRESS_BATCH_SIZE, ADDRESS_QUEUE_SIZE
from base_processor import BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig
from geocoding_record_creator import GeocodingRecordCreator
from models.address import Address


class AddressDeduplicationProducer(BaseProducer):
    """
    Producer for address deduplication - collects deduplication operations in batches.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000, thread_pool_config: Optional[ThreadPoolConfig] = None):
        super().__init__(db_ops, batch_size, thread_pool_config)
        self.last_address_id: Optional[str] = None

    def _get_work_batch(self, last_address_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get a batch of address deduplication work items"""
        # Use min(batch_size, max_files) if max_files is set, otherwise use batch_size
        effective_batch_size = min(self.batch_size, global_config.max_files) if global_config.max_files else self.batch_size

        batch, self.last_address_id = self.db_ops.get_address_deduplication_batch(last_address_id, effective_batch_size)

        return batch

    def _process_work_batch(self, batch: List[Dict[str, Any]]) -> List[DatabaseOperation]:
        """Process a batch of deduplication work into operations"""
        operations = []

        # Create deduplication operations for each canonical address
        for dedup_info in batch:
            operation = DatabaseOperation(
                operation_type=DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH,
                data=dedup_info
            )
            operations.append(operation)

        # Create single progress update operation for the entire batch
        if batch:
            canonical_addresses_processed = len(batch)
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": canonical_addresses_processed}  # Count the number of canonical addresses processed
            )
            operations.append(progress_op)
            self.log_debug(f"DEBUG: Created progress update operation for {canonical_addresses_processed} canonical addresses (batch size: {len(batch)})")

        return operations

    def _process_unified_work_batch(self, batch: List[Dict[str, Any]]) -> List[DatabaseOperation]:
        """Process a batch of unified deduplication + geocoding work into operations"""
        operations = []

        # Create unified operations for each canonical address group
        for dedup_info in batch:
            # Get the canonical address for geocoding
            canonical_address = dedup_info.get("canonical_address", "")

            # Create deduplication operation
            dedup_operation = DatabaseOperation(
                operation_type=DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH,
                data=dedup_info
            )
            operations.append(dedup_operation)

            # Create geocoding operation for the master address
            if canonical_address:
                from models.geocoding import Geocoding
                geocoding_record = Geocoding(
                    normalized_address=canonical_address,
                    geocoding_status='pending'
                )
                geocoding_operation = DatabaseOperation(
                    operation_type=DatabaseOperationType.INSERT_GEOCODING,
                    data={
                        'records': [geocoding_record],
                        'table': 'Geocoding'
                    }
                )
                operations.append(geocoding_operation)

        # Create single progress update operation for the entire batch
        if batch:
            work_items_processed = len(batch)
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": work_items_processed}
            )
            operations.append(progress_op)
            self.log_debug(f"DEBUG: Created unified progress update operation for {work_items_processed} work items")

        return operations

    def _get_geocoding_work_batch(self, last_address_id: Optional[str] = None) -> List[Address]:
        """Get a batch of addresses needing geocoding records"""
        # Use min(batch_size, max_files) if max_files is set, otherwise use batch_size
        effective_batch_size = min(self.batch_size, global_config.max_files) if global_config.max_files else self.batch_size

        # Get addresses that need geocoding records created
        addresses = self.db_ops.get_addresses_for_geocoding(limit=effective_batch_size, last_address_id=last_address_id)

        if addresses:
            self.last_address_id = addresses[-1].address_id

        return addresses

    def _process_geocoding_work_batch(self, batch: List[Address]) -> List[DatabaseOperation]:
        """Process a batch of geocoding work into operations"""
        operations = []

        if not batch:
            self.log_debug(f"DEBUG: No addresses in geocoding batch")
            return operations

        self.log_debug(f"DEBUG: Processing geocoding batch with {len(batch)} addresses")

        # Import here to avoid circular imports
        from geocoding_record_creator import GeocodingRecordCreator
        geocoding_creator = GeocodingRecordCreator(self.db_ops)

        # Create geocoding records and address updates for this batch
        result = geocoding_creator.create_geocoding_records_for_addresses(batch)

        geocoding_records = result['geocoding_records']
        address_updates = result['address_updates']
        progress_count = result['progress_count']

        self.log_debug(f"DEBUG: Created {len(geocoding_records)} geocoding records and {len(address_updates)} address updates from {len(batch)} addresses")

        # Create bulk insert operation for geocoding records
        if geocoding_records:
            bulk_insert_op = DatabaseOperation(
                operation_type=DatabaseOperationType.INSERT_GEOCODING,
                data={
                    'records': geocoding_records,
                    'table': 'Geocoding'
                }
            )
            operations.append(bulk_insert_op)
            self.log_debug(f"DEBUG: Created bulk insert operation for {len(geocoding_records)} geocoding records")

        # Create bulk update operation for address updates
        if address_updates:
            bulk_update_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Addresses',
                    'updates': address_updates,
                    'id_column': 'address_id'
                }
            )
            operations.append(bulk_update_op)
            self.log_debug(f"DEBUG: Created bulk update operation for {len(address_updates)} address updates")

        # Create progress update operation
        if progress_count > 0:
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": progress_count}
            )
            operations.append(progress_op)
            self.log_debug(f"DEBUG: Added progress update operation with count={progress_count}")

        self.log_debug(f"DEBUG: Returning {len(operations)} total operations from geocoding batch processing")
        return operations


class AddressDeduplicationConsumer(BaseConsumer):
    """
    Consumer for address deduplication - executes deduplication operations.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.
    """

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)

    def _process_operations_batch(self, operations_by_type) -> int:
        """Process operations batch for address deduplication consumer"""
        total_updated = 0

        # Handle ADDRESS_DEDUPLICATION_BATCH operations
        if DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value]:
                batch_data = operation.data
                master_address_id = batch_data.get("master_address_id")
                child_address_ids = batch_data.get("child_address_ids", [])
                canonical_address = batch_data.get("canonical_address", "")

                if master_address_id and child_address_ids:
                    try:
                        updated_count = self.db_ops.execute_address_deduplication_batch(master_address_id, child_address_ids)
                        total_updated += 1

                        self.log_debug(f"Updated {updated_count} child addresses to point to master {master_address_id} for '{canonical_address[:50]}...'")

                    except Exception as e:
                        self.log_error(f"Failed to execute deduplication for master {master_address_id}: {e}")
                        raise
                else:
                    self.log_warning(f"Invalid deduplication operation data: master={master_address_id}, children={len(child_address_ids) if child_address_ids else 0}")

        # Handle geocoding operations (INSERT_GEOCODING, GENERIC_UPDATE)
        geocoding_operations = []
        if DatabaseOperationType.INSERT_GEOCODING.value in operations_by_type:
            geocoding_operations.extend(operations_by_type[DatabaseOperationType.INSERT_GEOCODING.value])
        if DatabaseOperationType.GENERIC_UPDATE.value in operations_by_type:
            geocoding_operations.extend(operations_by_type[DatabaseOperationType.GENERIC_UPDATE.value])

        if geocoding_operations:
            self.log_debug(f"DEBUG: Processing {len(geocoding_operations)} geocoding operations")
            try:
                geocoding_updated = 0
                for operation in geocoding_operations:
                    if operation.operation_type == DatabaseOperationType.INSERT_GEOCODING:
                        self.log_debug(f"DEBUG: Executing bulk INSERT_GEOCODING operation")

                        # Handle bulk geocoding record insertion
                        geocoding_records = operation.data.get('records', [])
                        if not geocoding_records:
                            self.log_error(f"CRITICAL: INSERT_GEOCODING operation missing records: {operation.data}")
                            continue

                        # Use bulk_insert for geocoding records
                        geocoding_ids = self.db_ops.bulk_insert(geocoding_records)
                        self.log_debug(f"DEBUG: Bulk inserted {len(geocoding_records)} geocoding records, got {len(geocoding_ids)} IDs")

                        # Update geocoding objects with generated IDs for dependent operations
                        for i, geocoding_obj in enumerate(geocoding_records):
                            if i < len(geocoding_ids):
                                geocoding_obj.geocoding_id = geocoding_ids[i]

                        geocoding_updated += len(geocoding_records)

                    elif operation.operation_type == DatabaseOperationType.GENERIC_UPDATE:
                        self.log_debug(f"DEBUG: Executing bulk GENERIC_UPDATE operation")

                        # Handle bulk address updates
                        table = operation.data.get('table')
                        updates = operation.data.get('updates', [])
                        id_column = operation.data.get('id_column', 'id')

                        if not table or not updates:
                            self.log_error(f"CRITICAL: GENERIC_UPDATE operation missing required fields: table={table}, updates_count={len(updates) if updates else 0}")
                            continue

                        # Use bulk_update for address updates
                        updated_rows = self.db_ops.bulk_update(table, updates, id_column=id_column)
                        geocoding_updated += updated_rows
                        self.log_debug(f"DEBUG: Bulk updated {updated_rows} rows in {table}")

                total_updated += geocoding_updated
                self.log_debug(f"DEBUG: Executed {len(geocoding_operations)} geocoding operations, updated {geocoding_updated} records")
            except Exception as e:
                self.log_error(f"Failed to execute geocoding operations: {e}")
                raise

        return total_updated

    def _process_address_deduplication_batch_operations(self, operations_by_type):
        """Process address deduplication batch operations"""
        if DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value not in operations_by_type:
            return

        dedup_operations = operations_by_type[DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value]

        for operation in dedup_operations:
            batch_data = operation.data
            master_address_id = batch_data.get("master_address_id")
            child_address_ids = batch_data.get("child_address_ids", [])

            if master_address_id and child_address_ids:
                try:
                    updated_count = self.db_ops.execute_address_deduplication_batch(master_address_id, child_address_ids)
                    log_debug(self.logger, f"Updated {updated_count} child addresses to point to master {master_address_id}")
                except Exception as e:
                    log_error(self.logger, f"Failed to execute address deduplication batch for master {master_address_id}: {e}")
                    raise

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not global_config.is_quiet():
            log_info(self.logger, msg, *args, ein=ein)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not global_config.is_quiet():
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)


    def get_progress_scope(self, bytes: bool = False) -> Dict[str, Any]:
        """
        Get the estimated total work scope for address deduplication.

        Args:
            bytes: If True, return total in bytes instead of addresses

        Returns:
            Dictionary with 'total' (estimated work scope) and 'unit' ('addrs' or 'bytes')
        """
        if bytes:
            # Estimate total bytes by summing lengths of canonical addresses needing deduplication
            query = """
                SELECT SUM(LENGTH(canonical_address)) as total_bytes
                FROM (
                    SELECT canonical_address
                    FROM Addresses
                    WHERE canonical_address IS NOT NULL
                        AND canonical_address != ''
                    GROUP BY canonical_address
                    HAVING COUNT(*) > 1
                        AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
                )
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            self.log_debug(f"DEBUG: get_progress_scope(bytes=True) returning total={total} bytes")
            return {'total': total, 'unit': 'bytes'}
        else:
            # Count total master addresses needing deduplication (each canonical address group is 1 unit of work)
            query = """
                SELECT COUNT(*) as total_master_addresses
                FROM (
                    SELECT 1
                    FROM Addresses
                    WHERE canonical_address IS NOT NULL
                        AND canonical_address != ''
                    GROUP BY canonical_address
                    HAVING COUNT(*) > 1
                        AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
                )
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            self.log_debug(f"DEBUG: get_progress_scope(bytes=False) returning total={total} master addresses")
            return {'total': total, 'unit': 'addrs'}

    def get_geocoding_progress_scope(self) -> Dict[str, Any]:
        """
        Get the estimated total work scope for geocoding record creation.

        Returns:
            Dictionary with 'total' (estimated work scope) and 'unit' ('records')
        """
        # Count total addresses that need geocoding records created
        query = """
            SELECT COUNT(*) as total_addresses_needing_geocoding
            FROM Addresses
            WHERE geocoding_id IS NULL
                AND canonical_address IS NOT NULL
                AND canonical_address != ''
        """
        result = self.db_ops.execute_query(query)
        row = result.fetchone() if result else None
        total = int(row[0]) if row and row[0] is not None else 0
        self.log_debug(f"DEBUG: get_geocoding_progress_scope() returning total={total} addresses needing geocoding")
        return {'total': total, 'unit': 'records'}

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, *args, ein=ein, exc_info=exc_info)


class AddressDeduplicationProcessor:
    """
    Main processor for address deduplication using Producer-Consumer pattern.

    This processor coordinates producers and consumers to safely deduplicate
    addresses in batches, following the same pattern as XMLProcessor.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: Optional[int] = None):
        self.db_ops = db_ops
        self.batch_size = batch_size or ADDRESS_BATCH_SIZE
        self.logger = get_logger("address_dedup")

        # Throttle batch_size to global_config.max_files if specified (like XML processor does with --max_files)
        if global_config.max_files and global_config.max_files < self.batch_size:
            self.batch_size = global_config.max_files

        # Create thread pool configuration for address deduplication
        producer_config = PoolConfig(max_workers=4, queue_size=ADDRESS_QUEUE_SIZE, batch_size=self.batch_size)
        consumer_config = PoolConfig(max_workers=1, queue_size=ADDRESS_QUEUE_SIZE, batch_size=self.batch_size)  # Single consumer for DB safety
        self.thread_pool_config = ThreadPoolConfig(producer_config=producer_config, consumer_config=consumer_config)

        # Create producer and consumer instances
        self.producer = AddressDeduplicationProducer(db_ops, self.batch_size, self.thread_pool_config)
        self.consumer = AddressDeduplicationConsumer(db_ops)
        self.thread_pool_manager = None

    def deduplicate_addresses(self, progress_bar=None) -> int:
        """
        Deduplicate addresses by creating master-child relationships and geocoding records in a single unified process.

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of addresses processed (children updated)
        """
        self.log_info("Starting unified address deduplication with geocoding record creation")

        # DEBUG: Log current database state before processing
        try:
            geocoding_count = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding").fetchone()[0]
            addresses_count = self.db_ops.execute_query("SELECT COUNT(*) FROM Addresses WHERE canonical_address IS NOT NULL AND canonical_address != ''").fetchone()[0]
            master_addresses_count = self.db_ops.execute_query("SELECT COUNT(*) FROM Addresses WHERE master_id IS NULL AND canonical_address IS NOT NULL").fetchone()[0]
            self.log_info(f"DEBUG: Before deduplication - Geocoding records: {geocoding_count}, Addresses with canonical: {addresses_count}, Master addresses: {master_addresses_count}")
        except Exception as e:
            self.log_warning(f"DEBUG: Could not query database state: {e}")

        try:
            # Initialize thread pool manager
            thread_pool_manager = ThreadPoolManager(self.thread_pool_config, self.logger)

            # Initialize progress bar if not provided
            if progress_bar is None:
                from logging_utils import start_progress_reporting
                # Get progress scope for unified process
                progress_scope = self.consumer.get_progress_scope()
                total = progress_scope.get("total", 0)
                unit = progress_scope.get("unit", "addrs")
                start_progress_reporting(
                    total=total,
                    desc="Unified: Deduplicating addresses with geocoding",
                    unit=unit
                )
                self.log_debug(f"Initialized unified progress bar with total={total} {unit}")
                self.log_info(f"Unified progress bar initialized: {total} {unit} to process")

            total_updated = 0

            # Collect all deduplication work items with key-value pagination
            all_work_items = []
            last_address_id = None

            while True:
                batch, next_last_address_id = self.db_ops.get_address_deduplication_batch(last_address_id, self.batch_size)
                if not batch:
                    break
                all_work_items.extend(batch)
                self.log_debug(f"Collected batch of {len(batch)} deduplication work items, total so far: {len(all_work_items)}")

                # Check global limit
                if global_config.max_files and len(all_work_items) >= global_config.max_files:
                    all_work_items = all_work_items[:global_config.max_files]
                    self.log_info(f"Reached max_files limit: {global_config.max_files} work items")
                    break

                if last_address_id is None:
                    last_address_id = next_last_address_id
                elif next_last_address_id is not None:
                    last_address_id = max(last_address_id, next_last_address_id)

            if not all_work_items:
                self.log_info("No deduplication work found")
                return 0

            self.log_info(f"Collected total of {len(all_work_items)} unified work items for processing")

            # Start producer pool to process unified work items
            thread_pool_manager.start_producer_pool(
                all_work_items,
                self._unified_producer_worker_threaded
            )

            # Start consumer pool to execute unified operations
            thread_pool_manager.start_consumer_pool(
                self._consumer_worker_threaded,
                len(thread_pool_manager.producer_threads),
                progress_bar
            )
            self.log_debug(f"Started consumer pool with {len(thread_pool_manager.consumer_threads)} threads")

            # Wait for unified processing completion
            thread_pool_manager.wait_for_completion()

            # Collect results
            unified_results = 0
            self.log_debug(f"Starting unified result collection, result_queue size: {thread_pool_manager.result_queue.qsize()}")
            while not thread_pool_manager.result_queue.empty():
                try:
                    result = thread_pool_manager.result_queue.get_nowait()
                    if isinstance(result, int):
                        unified_results += result
                        self.log_debug(f"Collected unified result: {result}, total so far: {unified_results}")
                    thread_pool_manager.result_queue.task_done()
                except:
                    break
            self.log_info(f"Unified deduplication + geocoding completed: {unified_results} work items processed")

            # Cleanup thread pool
            thread_pool_manager.shutdown()

            # DEBUG: Log final database state after processing
            try:
                final_geocoding_count = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding").fetchone()[0]
                final_addresses_with_geocoding = self.db_ops.execute_query("SELECT COUNT(*) FROM Addresses WHERE geocoding_id IS NOT NULL").fetchone()[0]
                final_master_addresses = self.db_ops.execute_query("SELECT COUNT(*) FROM Addresses WHERE master_id IS NULL AND canonical_address IS NOT NULL").fetchone()[0]
                self.log_info(f"DEBUG: After unified processing - Geocoding records: {final_geocoding_count}, Addresses with geocoding_id: {final_addresses_with_geocoding}, Master addresses: {final_master_addresses}")
            except Exception as e:
                self.log_warning(f"DEBUG: Could not query final database state: {e}")

            total_updated = unified_results
            self.log_info(f"Unified address deduplication + geocoding record creation completed: {unified_results} work items processed")
            return total_updated

        except Exception as e:
            self.log_error(f"Unified address deduplication failed: {e}", exc_info=True)
            return 0

        finally:
            # Ensure progress bar is cleaned up
            if progress_bar:
                from logging_utils import stop_progress_reporting
                stop_progress_reporting()
                self.log_debug("Progress bar cleanup completed")
            else:
                # Stop unified progress bar if we initialized it
                from logging_utils import stop_progress_reporting
                stop_progress_reporting()
                self.log_debug("Unified progress bar cleanup completed")

    def _producer_worker_threaded(self, work_items: List[Dict[str, Any]], work_queue, result_queue, thread_id: int, num_threads: int) -> None:
        """Producer worker for ThreadPoolManager: processes work items into operations"""
        try:
            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                work_item = work_items[i]

                # Process single work item into operations
                operations = self.producer._process_work_batch([work_item])

                # Put operations in result queue for consumer
                result_queue.put(operations)
                self.log_debug(f"Producer {thread_id}: put {len(operations)} operations in queue, queue size now: {result_queue.qsize()}")

                # Log progress
                if (i + 1) % 50 == 0:
                    self.log_info(f"Producer {thread_id}: processed {i + 1}/{len(work_items)} work items")

        except Exception as e:
            self.log_error(f"Producer worker {thread_id} failed: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)
            self.log_debug(f"Producer {thread_id}: sent sentinel, queue size now: {result_queue.qsize()}")

    def _unified_producer_worker_threaded(self, work_items: List[Dict[str, Any]], work_queue, result_queue, thread_id: int, num_threads: int) -> None:
        """Unified producer worker for ThreadPoolManager: processes work items into operations"""
        try:
            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                work_item = work_items[i]

                # Process single work item into unified operations (deduplication + geocoding)
                operations = self.producer._process_unified_work_batch([work_item])

                # Put operations in result queue for consumer
                result_queue.put(operations)
                self.log_debug(f"Unified Producer {thread_id}: put {len(operations)} operations in queue, queue size now: {result_queue.qsize()}")

                # Log progress
                if (i + 1) % 50 == 0:
                    self.log_info(f"Unified Producer {thread_id}: processed {i + 1}/{len(work_items)} work items")

        except Exception as e:
            self.log_error(f"Unified Producer worker {thread_id} failed: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)
            self.log_debug(f"Unified Producer {thread_id}: sent sentinel, queue size now: {result_queue.qsize()}")

    def _consumer_worker_threaded(self, result_queue, thread_id: int, num_producers: int, progress_bar=None) -> None:
        """Consumer worker for ThreadPoolManager: executes operations"""
        try:
            batch_operations = []
            total_updated = 0
            sentinels_received = 0

            while True:
                try:
                    # Get operations from result queue
                    self.log_debug(f"DEBUG: Consumer {thread_id}: waiting for operations, queue size: {result_queue.qsize()}")
                    operations = result_queue.get(timeout=1.0)
                    self.log_debug(f"DEBUG: Consumer {thread_id}: got operations from queue, queue size now: {result_queue.qsize()}")

                    if operations is None:  # Sentinel
                        sentinels_received += 1
                        self.log_debug(f"DEBUG: Consumer {thread_id}: received sentinel {sentinels_received}/{num_producers}")
                        if sentinels_received >= num_producers:
                            break
                        continue

                    if isinstance(operations, list):
                        self.log_debug(f"DEBUG: Consumer {thread_id}: received {len(operations)} operations")

                        # Log what types of operations we received
                        op_types = {}
                        for op in operations:
                            op_type = op.operation_type.value if hasattr(op, 'operation_type') else str(type(op))
                            op_types[op_type] = op_types.get(op_type, 0) + 1
                        self.log_debug(f"DEBUG: Consumer {thread_id}: operation types: {op_types}")

                        batch_operations.extend(operations)

                        # Process batch when it reaches a reasonable size
                        if len(batch_operations) >= 50:  # Smaller batch size for address deduplication
                            self.log_debug(f"DEBUG: Consumer {thread_id}: processing batch of {len(batch_operations)} operations")
                            batch_updated = self.consumer.execute_operations_batch(batch_operations)
                            total_updated += batch_updated
                            self.log_debug(f"DEBUG: Consumer {thread_id}: batch processed, updated {batch_updated}, total so far: {total_updated}")
                            batch_operations = []  # Clear for next batch

                    result_queue.task_done()

                except:
                    if self.thread_pool_manager is not None and self.thread_pool_manager.shutdown_event.is_set():
                        break
                    continue

            # Process remaining operations
            if batch_operations:
                self.log_debug(f"DEBUG: Consumer {thread_id}: processing final batch of {len(batch_operations)} operations")
                batch_updated = self.consumer.execute_operations_batch(batch_operations)
                total_updated += batch_updated
                self.log_debug(f"DEBUG: Consumer {thread_id}: final batch processed, updated {batch_updated}, total: {total_updated}")

            # Put final result in result queue
            result_queue.put(total_updated)
            self.log_debug(f"DEBUG: Consumer {thread_id}: put final result {total_updated} in result queue")

        except Exception as e:
            self.log_error(f"Consumer worker {thread_id} failed: {e}", exc_info=True)
            result_queue.put(0)

    def _geocoding_producer_worker_threaded(self, work_items: List[Address], work_queue, result_queue, thread_id: int, num_threads: int) -> None:
        """Geocoding producer worker for ThreadPoolManager: processes geocoding work items into operations"""
        try:
            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                work_item = work_items[i]

                self.log_debug(f"DEBUG: Geocoding Producer {thread_id}: processing work item {i+1}/{len(work_items)} - address_id={work_item.address_id}")

                # Process single work item into geocoding operations
                operations = self.producer._process_geocoding_work_batch([work_item])

                self.log_debug(f"DEBUG: Geocoding Producer {thread_id}: created {len(operations)} operations for address {work_item.address_id}")

                # Put operations in result queue for consumer
                result_queue.put(operations)
                self.log_debug(f"DEBUG: Geocoding Producer {thread_id}: queued {len(operations)} operations, queue size now: {result_queue.qsize()}")

                # Log progress
                if (i + 1) % 50 == 0:
                    self.log_info(f"Geocoding Producer {thread_id}: processed {i + 1}/{len(work_items)} work items")

        except Exception as e:
            self.log_error(f"Geocoding Producer worker {thread_id} failed: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)
            self.log_debug(f"DEBUG: Geocoding Producer {thread_id}: sent sentinel, queue size now: {result_queue.qsize()}")

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, *args, ein=ein, exc_info=exc_info)

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not global_config.is_quiet():
            log_info(self.logger, msg, *args, ein=ein)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not global_config.is_quiet():
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)