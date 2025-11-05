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
from models.address import Address


class GeocodingRecordCreator:
    """
    Creates geocoding records for addresses that need geocoding.

    This class is integrated into the address deduplication process to create
    geocoding records for addresses that don't have them yet. It performs
    PO Box detection and creates initial geocoding records with normalized
    addresses for later API processing.
    """

    def __init__(self, db_ops):
        self.db_ops = db_ops
        self.logger = get_logger("geocoding_record_creator")

    def create_geocoding_records_for_addresses(self, addresses):
        """
        Create geocoding records for addresses that need them.

        Args:
            addresses: List of Address objects that may need geocoding records

        Returns:
            Dictionary containing:
            - 'geocoding_records': List of Geocoding objects to insert
            - 'address_updates': List of address update dictionaries for bulk_update
            - 'progress_count': Number of addresses processed
        """
        geocoding_records = []
        address_updates = []

        if not addresses:
            log_debug(self.logger, f"DEBUG: No addresses provided to create_geocoding_records_for_addresses")
            return {
                'geocoding_records': geocoding_records,
                'address_updates': address_updates,
                'progress_count': 0
            }

        log_debug(self.logger, f"DEBUG: Starting geocoding record creation for {len(addresses)} addresses")

        # DEBUG: Log what addresses we're processing
        for addr in addresses[:5]:  # Log first 5 addresses
            log_debug(self.logger, f"DEBUG: Processing address {addr.address_id}: geocoding_id={addr.geocoding_id}, canonical='{addr.canonical_address[:50] if addr.canonical_address else None}'")
        if len(addresses) > 5:
            log_debug(self.logger, f"DEBUG: ... and {len(addresses) - 5} more addresses")

        # Process addresses sequentially
        geocoding_records_created = 0
        address_updates_created = 0

        for address in addresses:
            # Check if address already has a geocoding record
            if address.geocoding_id:
                log_debug(self.logger, f"DEBUG: Address {address.address_id} already has geocoding_id {address.geocoding_id}, skipping")
                continue

            # Validate address has required fields
            if not address.canonical_address:
                log_debug(self.logger, f"DEBUG: Address {address.address_id} missing canonical_address, skipping")
                continue

            # Check for PO Box detection
            if address.is_po_box():
                # Create PO Box update
                log_debug(self.logger, f"DEBUG: Address {address.address_id} detected as PO Box: {address.po_box}")
                log_debug(self.logger, f"DEBUG: Creating PO Box update for address {address.address_id}")

                address_updates.append({
                    'address_id': address.address_id,
                    'po_box': address.po_box,
                    'colocator': f"PO:{address.po_box}:{address.zip_code or ''}"
                })
                address_updates_created += 1
            else:
                # Create geocoding record for API processing
                log_debug(self.logger, f"DEBUG: Creating geocoding record for address {address.address_id}")
                geocoding_op = address.create_geocoding_operation()

                # Validate the geocoding operation was created properly
                if not geocoding_op or not geocoding_op.data:
                    log_debug(self.logger, f"DEBUG: Failed to create geocoding operation for address {address.address_id}")
                    continue

                # Validate operation data integrity
                if 'geocoding' not in geocoding_op.data or 'address_id' not in geocoding_op.data:
                    log_debug(self.logger, f"DEBUG: Geocoding operation missing required keys for address {address.address_id}")
                    continue

                geocoding_obj = geocoding_op.data['geocoding']
                log_debug(self.logger, f"DEBUG: Geocoding record created for address {address.address_id}: normalized_address='{geocoding_obj.normalized_address[:50]}'")

                geocoding_records.append(geocoding_obj)
                geocoding_records_created += 1

                # Create address update to link geocoding_id after insertion
                address_updates.append({
                    'address_id': address.address_id,
                    'geocoding_id': geocoding_obj.geocoding_id  # Will be set after bulk insert
                })
                address_updates_created += 1
                log_debug(self.logger, f"DEBUG: Created address update to link geocoding_id to address {address.address_id}")

        progress_count = geocoding_records_created + address_updates_created

        log_debug(self.logger, f"DEBUG: Batch processing complete - {geocoding_records_created} geocoding records, {address_updates_created} address updates, total progress: {progress_count}")

        # DEBUG: Log records being created
        for i, geocoding in enumerate(geocoding_records[:3]):  # Log first 3 geocoding records
            log_debug(self.logger, f"DEBUG: Created geocoding record {i+1}: geocoding_id={geocoding.geocoding_id}, normalized_address='{geocoding.normalized_address[:50]}'")
        if len(geocoding_records) > 3:
            log_debug(self.logger, f"DEBUG: ... and {len(geocoding_records) - 3} more geocoding records")

        return {
            'geocoding_records': geocoding_records,
            'address_updates': address_updates,
            'progress_count': progress_count
        }

    def get_addresses_needing_geocoding_records(self, limit=None):
        """
        Get addresses that need geocoding records created.

        Args:
            limit: Maximum number of addresses to return

        Returns:
            List of Address objects that need geocoding records
        """
        # Get addresses that need geocoding (no geocoding_id and not PO Box)
        addresses = self.db_ops.get_addresses_for_geocoding(limit=limit)

        if not global_config.is_quiet():
            log_debug(self.logger, f"PHASE 1: Retrieved {len(addresses)} addresses from get_addresses_for_geocoding")

        return addresses


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

    def _process_work_batch_to_contexts(self, batch: List[Dict[str, Any]]) -> 'PendingDatabaseContext':
        """Process a batch of deduplication work into PendingDatabaseContext objects"""
        from pending_database_context import PendingDatabaseContext

        # Create a single context for the entire batch
        context = PendingDatabaseContext()

        for dedup_info in batch:
            # Get the canonical address for geocoding
            canonical_address = dedup_info.get("canonical_address", "")

            # Create geocoding operation for the master address
            if canonical_address:
                # Get the master address directly from database instead of creating temporary object
                master_address_id = dedup_info.get("master_address_id")
                if master_address_id:
                    # Fetch the actual Address object from database
                    addresses = self.db_ops.select_dataclass(Address, where_clause="address_id = ?", params=(master_address_id,))
                    if addresses:
                        master_address = addresses[0]
                        # Create geocoding record using factory method
                        geocoding = master_address.create_geocoding()
                        context.addObjectToDatabase(geocoding)

            # Add deduplication operation
            operation = DatabaseOperation(
                operation_type=DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH,
                data=dedup_info
            )
            context.addOperationToDatabase(operation)

            # Add progress operation
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": 1}  # Count each canonical address processed
            )
            context.addOperationToDatabase(progress_op)

        return context

    def _process_work_batch_to_context(self, batch: List[Dict[str, Any]]) -> Optional['PendingDatabaseContext']:
        """Process a batch of deduplication work into a single PendingDatabaseContext"""
        from pending_database_context import PendingDatabaseContext

        if not batch:
            return None

        # Create a single context for this batch
        context = PendingDatabaseContext()

        # Process each deduplication item in the batch
        for dedup_info in batch:
            # Get the canonical address for geocoding
            canonical_address = dedup_info.get("canonical_address", "")

            # Create deduplication operation
            dedup_operation = DatabaseOperation(
                operation_type=DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH,
                data=dedup_info
            )
            context.addOperationToDatabase(dedup_operation)

            # Create geocoding operation for the master address
            if canonical_address:
                # Get the master address directly from database instead of creating temporary object
                master_address_id = dedup_info.get("master_address_id")
                if master_address_id:
                    # Fetch the actual Address object from database
                    addresses = self.db_ops.select_dataclass(Address, where_clause="address_id = ?", params=(master_address_id,))
                    if addresses:
                        master_address = addresses[0]
                        # Create geocoding record using factory method
                        geocoding = master_address.create_geocoding()
                        context.addObjectToDatabase(geocoding)

        # Create progress update operation for the entire batch
        work_items_processed = len(batch)
        progress_op = DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={"count": work_items_processed}
        )
        context.addOperationToDatabase(progress_op)
        self.log_debug(f"DEBUG: Created PDC context with {work_items_processed} work items")

        return context

    def _get_geocoding_work_batch(self, last_address_id: Optional[str] = None) -> List[Address]:
        """Get a batch of addresses needing geocoding records"""
        # Use min(batch_size, max_files) if max_files is set, otherwise use batch_size
        effective_batch_size = min(self.batch_size, global_config.max_files) if global_config.max_files else self.batch_size

        # Get addresses that need geocoding records created
        addresses = self.db_ops.get_addresses_for_geocoding(limit=effective_batch_size, last_address_id=last_address_id)

        if addresses:
            self.last_address_id = addresses[-1].address_id

        return addresses

    def _process_geocoding_work_batch_to_context(self, batch: List[Address], context: 'PendingDatabaseContext') -> None:
        """Process a batch of geocoding work into a PendingDatabaseContext"""
        if not batch:
            self.log_debug(f"DEBUG: No addresses in geocoding batch")
            return

        self.log_debug(f"DEBUG: Processing geocoding batch with {len(batch)} addresses")

        # Use integrated geocoding record creator
        geocoding_creator = GeocodingRecordCreator(self.db_ops)

        # Create geocoding records and address updates for this batch
        result = geocoding_creator.create_geocoding_records_for_addresses(batch)

        geocoding_records = result['geocoding_records']
        address_updates = result['address_updates']
        progress_count = result['progress_count']

        self.log_debug(f"DEBUG: Created {len(geocoding_records)} geocoding records and {len(address_updates)} address updates from {len(batch)} addresses")

        # Add geocoding records to context
        for geocoding_record in geocoding_records:
            context.addObjectToDatabase(geocoding_record)
            self.log_debug(f"DEBUG: Added geocoding record to context")

        # Add address update operations to context
        if address_updates:
            bulk_update_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Addresses',
                    'updates': address_updates,
                    'id_column': 'address_id'
                }
            )
            context.addOperationToDatabase(bulk_update_op)
            self.log_debug(f"DEBUG: Added bulk update operation for {len(address_updates)} address updates to context")

        # Add progress update operation
        if progress_count > 0:
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": progress_count}
            )
            context.addOperationToDatabase(progress_op)
            self.log_debug(f"DEBUG: Added progress update operation with count={progress_count} to context")


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

                        # Use INSERT_BY_TYPE for geocoding records (single type, no sorting needed)
                        geocoding_ids = self.db_ops.INSERT_BY_TYPE(geocoding_records, 'Geocoding')
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
        """Process address deduplication batch operations - DEPRECATED: Use _process_operations_batch instead"""
        # This method is now redundant since _process_operations_batch handles all operation types
        pass

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
        Deduplicate addresses by creating master-child relationships using PendingDatabaseContext.

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of addresses processed (children updated)
        """
        self.log_info("Starting address deduplication using PendingDatabaseContext")

        try:
            # Collect contexts using the PendingDatabaseContext approach
            contexts = self.producer.collect_contexts()

            if not contexts:
                self.log_info("No deduplication work found")
                return 0

            # Execute contexts using consumer
            total_processed = self.consumer.execute_contexts_batch(contexts, progress_bar)

            self.log_info(f"Address deduplication completed: {total_processed} operations processed")
            return total_processed

        except Exception as e:
            self.log_error(f"Address deduplication failed: {e}", exc_info=True)
            return 0

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
        """Geocoding producer worker for ThreadPoolManager: processes geocoding work items into context"""
        try:
            # Create a single context for this thread's work
            from pending_database_context import PendingDatabaseContext
            thread_context = PendingDatabaseContext()

            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                work_item = work_items[i]

                self.log_debug(f"DEBUG: Geocoding Producer {thread_id}: processing work item {i+1}/{len(work_items)} - address_id={work_item.address_id}")

                # Process single work item into geocoding context
                self.producer._process_geocoding_work_batch_to_context([work_item], thread_context)

                self.log_debug(f"DEBUG: Geocoding Producer {thread_id}: added geocoding work to context for address {work_item.address_id}")

                # Log progress
                if (i + 1) % 50 == 0:
                    self.log_info(f"Geocoding Producer {thread_id}: processed {i + 1}/{len(work_items)} work items")

            # Put the single merged context in result queue
            result_queue.put(thread_context)
            self.log_debug(f"DEBUG: Geocoding Producer {thread_id}: queued context, queue size now: {result_queue.qsize()}")

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