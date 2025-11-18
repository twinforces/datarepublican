#!/usr/bin/env python3
"""
address_deduplication_processor.py - Address deduplication processor

This module handles deduplication of addresses in the database by creating
master-child relationships based on canonical_address matching.

Now uses Producer-Consumer pattern for safe batch processing with DuckDB.
"""

import os
import time
import queue
from typing import Optional, List, Dict, Any, Tuple
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config
from constants import ADDRESS_BATCH_SIZE, ADDRESS_QUEUE_SIZE
from base_processor import BaseProcessor, BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig, WorkUnit
from models.address import Address
from models.dedup_work_item import DedupWorkItem
from pending_database_context import PendingDatabaseContext
from queue_status_display import QueueStatusDisplay


class GeocodingRecordCreator:
    """
    Creates geocoding records for addresses that need geocoding.

    This class is integrated into the address deduplication process to create
    geocoding records for addresses that don't have them yet. It performs
    PO Box detection and creates initial geocoding records with normalized
    addresses for later API processing.
    """

    def __init__(self, db_ops: DatabaseOperations) -> None:
        self.db_ops: DatabaseOperations = db_ops

    def create_geocoding_records_for_addresses(self, addresses: List[Address]) -> Dict[str, Any]:
        """Create geocoding records for addresses that need them.

        Args:
            addresses: List of Address objects that may need geocoding records

        Returns:
            Dictionary containing:
            - 'geocoding_records': List of Geocoding objects to insert
            - 'address_updates': List of address update dictionaries for bulk_update
            - 'progress_count': Number of addresses processed
        """
        geocoding_records: List[Any] = []
        address_updates: List[Dict[str, Any]] = []

        if not addresses:
            log_debug(f"DEBUG: No addresses provided to create_geocoding_records_for_addresses")
            return {
                'geocoding_records': geocoding_records,
                'address_updates': address_updates,
                'progress_count': 0
            }

        log_debug(f"DEBUG: Starting geocoding record creation for {len(addresses)} addresses")

        # DEBUG: Log what addresses we're processing
        for addr in addresses[:5]:  # Log first 5 addresses
            log_debug(f"DEBUG: Processing address {addr.address_id}: geocoding_id={addr.geocoding_id}, canonical='{addr.canonical_address[:50] if addr.canonical_address else None}'")
        if len(addresses) > 5:
            log_debug(f"DEBUG: ... and {len(addresses) - 5} more addresses")

        # Process addresses sequentially
        geocoding_records_created: int = 0
        address_updates_created: int = 0

        for address in addresses:
            # Check if address already has a geocoding record
            if address.geocoding_id:
                log_debug(f"DEBUG: Address {address.address_id} already has geocoding_id {address.geocoding_id}, skipping")
                continue

            # Validate address has required fields
            if not address.canonical_address:
                log_debug(f"DEBUG: Address {address.address_id} missing canonical_address, skipping")
                continue

            # Check for PO Box detection
            if address.is_po_box():
                # Create PO Box update
                log_debug(f"DEBUG: Address {address.address_id} detected as PO Box: {address.po_box}")
                log_debug(f"DEBUG: Creating PO Box update for address {address.address_id}")

                address_updates.append({
                    'address_id': address.address_id,
                    'po_box': address.po_box,
                    'colocator': f"PO:{address.po_box}:{address.zip_code or ''}"
                })
                address_updates_created += 1
            else:
                # Create geocoding record for API processing
                log_debug(f"DEBUG: Creating geocoding record for address {address.address_id}")
                geocoding_obj = address.create_geocoding()
                log_debug(f"DEBUG: Geocoding record created for address {address.address_id}: normalized_address='{geocoding_obj.normalized_address[:50]}'")

                geocoding_records.append(geocoding_obj)
                geocoding_records_created += 1

                # Create address update to link geocoding_id after insertion
                address_updates.append({
                    'address_id': address.address_id,
                    'geocoding_id': geocoding_obj.geocoding_id  # Will be set after bulk insert
                })
                address_updates_created += 1
                log_debug(f"DEBUG: Created address update to link geocoding_id to address {address.address_id}")

        progress_count = geocoding_records_created + address_updates_created

        log_debug(f"DEBUG: Batch processing complete - {geocoding_records_created} geocoding records, {address_updates_created} address updates, total progress: {progress_count}")

        # DEBUG: Log records being created
        for i, geocoding in enumerate(geocoding_records[:3]):  # Log first 3 geocoding records
            log_debug(f"DEBUG: Created geocoding record {i+1}: geocoding_id={geocoding.geocoding_id}, normalized_address='{geocoding.normalized_address[:50]}'")
        if len(geocoding_records) > 3:
            log_debug(f"DEBUG: ... and {len(geocoding_records) - 3} more geocoding records")

        return {
            'geocoding_records': geocoding_records,
            'address_updates': address_updates,
            'progress_count': progress_count
        }

    def get_addresses_needing_geocoding_records(self, limit: Optional[int] = None) -> List[Address]:
        """Get addresses that need geocoding records created.

        Args:
            limit: Maximum number of addresses to return

        Returns:
            List of Address objects that need geocoding records
        """
        # Get addresses that need geocoding (no geocoding_id and not PO Box)
        addresses = self.db_ops.get_addresses_for_geocoding(limit=limit)

        if not global_config.is_quiet():
            log_debug(f"PHASE 1: Retrieved {len(addresses)} addresses from get_addresses_for_geocoding")

        return addresses


class AddressDeduplicationProducer(BaseProducer):
    """Producer for address deduplication - collects deduplication operations in batches.

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000, thread_pool_config: Optional[ThreadPoolConfig] = None) -> None:
        super().__init__(db_ops, batch_size, thread_pool_config)
        self.last_address_id: Optional[str] = None
        self.dedup_groups: int = 0
 
    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.
    """
 
    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000, thread_pool_config: Optional[ThreadPoolConfig] = None):
        super().__init__(db_ops, batch_size, thread_pool_config)
        self.last_address_id: Optional[str] = None
        self.dedup_groups = 0

    def _get_custom_metrics(self) -> Dict[str, Any]:
        """Custom metrics for address deduplication producer."""
        return {'dedup_groups': self.dedup_groups}


    def get_progress_scope(self, bytes: bool = False) -> Dict[str, Any]:
        """Get the estimated total work scope for address deduplication.

        Args:
            bytes: If True, return total in bytes instead of addresses

        Returns:
            Dictionary with 'total' (estimated work scope) and 'unit' ('addrs' or 'bytes')
        """
        if bytes:
            # For addresses, just count them since they're not dramatically different in size
            query = """
                SELECT COUNT(*) as total_addresses
                FROM Addresses
                WHERE master_id IS NULL
                    AND canonical_address IS NOT NULL
                    AND canonical_address != ''
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            return {'total': total, 'unit': 'bytes'}
        else:
            # Count total canonical_addresses from pending_canonicals table (each row is 1 unit of work)
            query = """
                SELECT COUNT(*) as total_canonical_addresses
                FROM pending_canonicals
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            return {'total': total, 'unit': 'addrs'}

    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get a batch of canonical_addresses from pending_canonicals table using key-value paging"""
        # Use min(batch_size, max_files) if max_files is set, otherwise use batch_size
        effective_batch_size = min(self.batch_size, global_config.max_files) if global_config.max_files else self.batch_size

        if last_pk is None:
            query = """
            SELECT canonical_address, root_id
            FROM pending_canonicals
            ORDER BY canonical_address ASC
            LIMIT ?
            """
            params = (effective_batch_size,)
        else:
            query = """
            SELECT canonical_address, root_id
            FROM pending_canonicals
            WHERE canonical_address > ?
            ORDER BY canonical_address ASC
            LIMIT ?
            """
            params = (last_pk, effective_batch_size)

        cursor = self.db_ops.execute_query(query, params)
        rows = cursor.fetchall()

        batch: List[Dict[str, Any]] = []
        max_pk: Optional[str] = None
        for row in rows:
            canonical_address = row[0]
            root_id = row[1]

            # Return canonical_address and root_id from pre-computed table
            dedup_info = {
                "canonical_address": canonical_address,
                "root_id": root_id
            }
            batch.append(dedup_info)

            if max_pk is None or canonical_address > max_pk:
                max_pk = canonical_address

        self.last_address_id = max_pk
        return batch, max_pk

    def _process_work_batch_to_contexts(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        """Process a batch of deduplication work into PendingDatabaseContext objects"""

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

    def _process_work_batch_to_context(self, batch: List[Dict[str, Any]]) -> Optional[PendingDatabaseContext]:
        """Process a batch of canonical_addresses from pending_canonicals into PendingDatabaseContext objects"""

        # Create a single context for the entire batch
        context = PendingDatabaseContext()

        for dedup_info in batch:
            # Get the canonical address and pre-computed root_id
            canonical_address = dedup_info.get("canonical_address", "")
            root_id = dedup_info.get("root_id")

            if canonical_address and root_id:
                # Fetch all addresses with this canonical_address that have master_id IS NULL
                addresses = self.db_ops.select_dataclass(
                    Address,
                    where_clause="canonical_address = ?",
                    params=(canonical_address, )
                )

                if addresses:
                    # Sort by address_id to ensure consistent master selection
                    addresses.sort(key=lambda a: a.address_id)
                    master_address = addresses[0]
                    master_address_id = master_address.address_id

                    # Verify the root_id matches our expectation
                    if str(master_address_id) != str(root_id):
                        log_warning(f"Root ID mismatch for canonical_address '{canonical_address}': expected {root_id}, got {master_address_id}")
                        # Use the actual minimum address_id as master
                        master_address_id = root_id

                    # Create geocoding record for the master address (if not already processed - check colocator)
                    if not master_address.colocator:
                        geocoding = master_address.create_geocoding()
                        context.addObjectToDatabase(geocoding)

                        # Create update to link geocoding_id to master address
                        link_geocoding_op = DatabaseOperation(
                            operation_type=DatabaseOperationType.GENERIC_UPDATE,
                            data={
                                'table': 'Addresses',
                                'updates': [{'geocoding_id': geocoding.geocoding_id, 'master_id': master_address_id}],
                                'id_column': 'address_id'
                            }
                        )
                        context.addOperationToDatabase(link_geocoding_op)

                    # Create deduplication operation for all addresses with this canonical_address
                    # Set master_id for ALL addresses in the group to the master_address_id
                    dedup_work_item = DedupWorkItem(
                        canonical_address=canonical_address,
                        master_address_id=master_address_id,
                        child_address_ids=[a.address_id for a in addresses]  # ALL addresses in the group including master
                    )

                    operation = DatabaseOperation(
                        operation_type=DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH,
                        data=dedup_work_item
                    )
                    context.addOperationToDatabase(operation)

            # Add progress operation (count each canonical_address processed)
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": 1}
            )
            context.addOperationToDatabase(progress_op)

        return context

    def _get_geocoding_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Address], Optional[str]]:
        """Get a batch of addresses needing geocoding records using key-value paging"""
        if self.exit_processing:
            return [], None
        # Use min(batch_size, max_files) if max_files is set, otherwise use batch_size
        effective_batch_size = min(self.batch_size, global_config.max_files) if global_config.max_files else self.batch_size
 
        if last_pk is None:
            query = """
            SELECT * FROM Addresses
            WHERE geocoding_id IS NULL
              AND canonical_address IS NOT NULL
              AND canonical_address != ''
              AND po_box IS NULL
            ORDER BY address_id ASC
            LIMIT ?
            """
            params = (effective_batch_size,)
        else:
            query = """
            SELECT * FROM Addresses
            WHERE geocoding_id IS NULL
              AND canonical_address IS NOT NULL
              AND canonical_address != ''
              AND po_box IS NULL
              AND address_id > ?
            ORDER BY address_id ASC
            LIMIT ?
            """
            params = (last_pk, effective_batch_size)

        addresses = self.db_ops.select_dataclass(Address, query=query, params=params)
 
        max_pk = None
        if addresses:
            max_pk = addresses[-1].address_id
            self.last_address_id = max_pk
 
        return addresses, max_pk

    def _process_geocoding_work_batch_to_context(self, batch: List[Address], context: PendingDatabaseContext) -> None:
        """Process a batch of geocoding work into a PendingDatabaseContext"""
        if not batch:
            log_debug(f"DEBUG: No addresses in geocoding batch")
            return
 
        log_debug(f"DEBUG: Processing geocoding batch with {len(batch)} addresses")
 
        # Use integrated geocoding record creator
        geocoding_creator = GeocodingRecordCreator(self.db_ops)
 
        # Create geocoding records and address updates for this batch
        result = geocoding_creator.create_geocoding_records_for_addresses(batch)
 
        geocoding_records = result['geocoding_records']
        address_updates = result['address_updates']
        progress_count = result['progress_count']
 
        log_debug(f"DEBUG: Created {len(geocoding_records)} geocoding records and {len(address_updates)} address updates from {len(batch)} addresses")
 
        # Add geocoding records to context
        for geocoding_record in geocoding_records:
            if self.producer.exit_processing:
                break
            context.addObjectToDatabase(geocoding_record)
            log_debug(f"DEBUG: Added geocoding record to context")
 
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
            log_debug(f"DEBUG: Added bulk update operation for {len(address_updates)} address updates to context")
 
        # Add progress update operation
        if progress_count > 0:
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": progress_count}
            )
            context.addOperationToDatabase(progress_op)
            log_debug(f"DEBUG: Added progress update operation with count={progress_count} to context")


class AddressDeduplicationConsumer(BaseConsumer):
    """Consumer for address deduplication - executes deduplication operations.

    def __init__(self, db_ops: DatabaseOperations) -> None:
        super().__init__(db_ops)
        self.setup_status_gauges(interval=10.0)
 
    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.
    """
 
    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.setup_status_gauges(interval=10.0)

    def _process_operations_batch(self, operations_by_type: Dict[str, List[DatabaseOperation]]) -> int:
        """Process operations batch for address deduplication consumer"""
        total_updated: int = 0

        # Handle ADDRESS_DEDUPLICATION_BATCH operations
        if DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value]:
                batch_data = operation.data
                master_address_id = batch_data.master_address_id
                child_address_ids = batch_data.child_address_ids
                canonical_address = batch_data.canonical_address

                if master_address_id and child_address_ids:
                    try:
                        updated_count = self.db_ops.execute_address_deduplication_batch(master_address_id, child_address_ids)
                        total_updated += 1

                        log_debug(f"Updated {updated_count} child addresses to point to master {master_address_id} for '{canonical_address[:50]}...'")

                    except Exception as e:
                        log_error(f"Failed to execute deduplication for master {master_address_id}: {e}")
                        raise
                else:
                    log_warning(f"Invalid deduplication operation data: master={master_address_id}, children={len(child_address_ids) if child_address_ids else 0}")

        # Handle geocoding operations (GENERIC_UPDATE)
        geocoding_operations: List[DatabaseOperation] = []
        if DatabaseOperationType.GENERIC_UPDATE.value in operations_by_type:
            geocoding_operations.extend(operations_by_type[DatabaseOperationType.GENERIC_UPDATE.value])

        if geocoding_operations:
            log_debug(f"DEBUG: Processing {len(geocoding_operations)} geocoding operations")
            try:
                geocoding_updated: int = 0
                for operation in geocoding_operations:
                    if operation.operation_type == DatabaseOperationType.GENERIC_UPDATE:
                        log_debug(f"DEBUG: Executing bulk GENERIC_UPDATE operation")

                        # Handle bulk address updates
                        table = operation.data.get('table')
                        updates = operation.data.get('updates', [])
                        id_column = operation.data.get('id_column', 'id')

                        if not table or not updates:
                            log_error(f"CRITICAL: GENERIC_UPDATE operation missing required fields: table={table}, updates_count={len(updates) if updates else 0}")
                            continue

                        # Use bulk_update for address updates
                        updated_rows = self.db_ops.bulk_update(table, updates, id_column=id_column)
                        geocoding_updated += updated_rows
                        log_debug(f"DEBUG: Bulk updated {updated_rows} rows in {table}")

                total_updated += geocoding_updated
                log_debug(f"DEBUG: Executed {len(geocoding_operations)} geocoding operations, updated {geocoding_updated} records")
            except Exception as e:
                log_error(f"Failed to execute geocoding operations: {e}")
                raise

        return total_updated

    def _process_address_deduplication_batch_operations(self, operations_by_type: Dict[str, List[DatabaseOperation]]) -> None:
        """Process address deduplication batch operations - DEPRECATED: Use _process_operations_batch instead"""
        # This method is now redundant since _process_operations_batch handles all operation types
        pass


    def get_progress_scope(self, bytes: bool = False) -> Dict[str, Any]:
        """Get the estimated total work scope for address deduplication.

        Args:
            bytes: If True, return total in bytes instead of addresses

        Returns:
            Dictionary with 'total' (estimated work scope) and 'unit' ('addrs' or 'bytes')
        """
        if bytes:
            # For addresses, just count them since they're not dramatically different in size
            query = """
                SELECT COUNT(*) as total_addresses
                FROM Addresses
                WHERE master_id IS NULL
                    AND canonical_address IS NOT NULL
                    AND canonical_address != ''
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            log_debug(f"DEBUG: get_progress_scope(bytes=True) returning total={total} addresses")
            return {'total': total, 'unit': 'bytes'}
        else:
            # Count total canonical_addresses that have unprocessed addresses (each canonical_address is 1 unit of work)
            query = """
                SELECT COUNT(DISTINCT canonical_address) as total_canonical_addresses
                FROM Addresses
                WHERE master_id IS NULL
                    AND canonical_address IS NOT NULL
                    AND canonical_address != ''
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            log_debug(f"DEBUG: get_progress_scope(bytes=False) returning total={total} canonical addresses")
            return {'total': total, 'unit': 'addrs'}

    def get_geocoding_progress_scope(self) -> Dict[str, Any]:
        """Get the estimated total work scope for geocoding record creation.

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
        log_debug(f"DEBUG: get_geocoding_progress_scope() returning total={total} addresses needing geocoding")
        return {'total': total, 'unit': 'records'}


class AddressDeduplicationProcessor(BaseProcessor):
    """Address deduplication processor using the generalized multi-threaded architecture."""

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.batch_size = 1000

    def setup_pending_canonicals(self) -> None:
        """
        Setup phase: Create and populate the pending_canonicals table.

        This method creates the pending_canonicals table and populates it with
        canonical addresses and their root_ids using the optimized GROUP BY approach.
        """
        log_info("Starting setup phase: Creating pending_canonicals table")
        print("Creating Work Table")
        # Step 1: Drop if exists (safe; no-op if missing)
        self.db_ops.execute_query("DROP TABLE IF EXISTS pending_canonicals;")

        # Step 2: Create with root_id
        create_table_sql = """
        CREATE TABLE pending_canonicals (
            canonical_address VARCHAR PRIMARY KEY,
            root_id UUID NOT NULL
        );
        """
        self.db_ops.execute_query(create_table_sql)

        # Step 3: Insert with MIN root selection (uses GROUP BY for dedup + min; index-accelerated)
        insert_sql = """
        INSERT INTO pending_canonicals (canonical_address, root_id)
        SELECT
            canonical_address,
            MIN(address_id) AS root_id
        FROM Addresses
        WHERE 
          master_id IS NULL
          AND LENGTH(canonical_address) > 0
        GROUP BY canonical_address;
        """
        self.db_ops.execute_query(insert_sql)

        # Step 4: Checkpoint and optimize table for queries
        self.db_ops.execute_query("FORCE CHECKPOINT;")
        self.db_ops.execute_query("VACUUM ANALYZE pending_canonicals;")
        print("Work Table Cmomplete")

        # Get count for logging
        count_result = self.db_ops.execute_query("SELECT COUNT(*) FROM pending_canonicals;")
        count = count_result.fetchone()[0] if count_result else 0

        log_info(f"Setup phase completed: Created pending_canonicals table with {count} canonical address groups")

    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get a batch of address deduplication work items using key-value paging"""
        effective_batch_size = self.batch_size

        if last_pk is None:
            query = """
            SELECT canonical_address, MIN(address_id) as group_pk
            FROM Addresses
            WHERE master_id IS NULL
              AND canonical_address IS NOT NULL
              AND canonical_address != ''
            GROUP BY canonical_address
            HAVING COUNT(*) > 1
            ORDER BY group_pk ASC
            LIMIT ?
            """
            params = (effective_batch_size,)
        else:
            query = """
            SELECT canonical_address, MIN(address_id) as group_pk
            FROM Addresses
            WHERE master_id IS NULL
              AND canonical_address IS NOT NULL
              AND canonical_address != ''
            GROUP BY canonical_address
            HAVING COUNT(*) > 1 AND MIN(address_id) > ?
            ORDER BY group_pk ASC
            LIMIT ?
            """
            params = (last_pk, effective_batch_size)

        cursor = self.db_ops.execute_query(query, params)
        rows = cursor.fetchall()

        batch: List[Dict[str, Any]] = []
        max_pk: Optional[str] = None
        for row in rows:
            canonical_address = row[0]
            group_pk = row[1]

            addresses = self.db_ops.select_dataclass(
                Address,
                where_clause="canonical_address = ? AND master_id IS NULL",
                params=(canonical_address,)
            )

            if len(addresses) > 1:
                addresses.sort(key=lambda a: a.address_id)
                master_address_id = addresses[0].address_id
                child_address_ids = [a.address_id for a in addresses[1:]]
                dedup_info = {
                    "canonical_address": canonical_address,
                    "master_address_id": master_address_id,
                    "child_address_ids": child_address_ids
                }
                batch.append(dedup_info)

                if max_pk is None or group_pk > max_pk:
                    max_pk = group_pk

        return batch, max_pk

    def _feed_thread(self, work_queue: queue.Queue, max_files: Optional[int] = None, num_producers: int = 4):
        """Feeder thread: Fetch batches of deduplication work and enqueue WorkUnits."""
        enqueued = 0
        last_pk = None
        while True:
            batch, new_last_pk = self._get_work_batch(last_pk)
            if not batch:
                break

            for item in batch:
                if self.exit_processing:
                    break
                work_queue.put(WorkUnit.work_item(item))
                enqueued += 1
                if max_files and enqueued >= max_files:
                    break

            if self.exit_processing or (max_files and enqueued >= max_files):
                break

            last_pk = new_last_pk

        # Send sentinels for each producer
        for i in range(num_producers):
            work_queue.put(WorkUnit.sentinel(i))

    def _process_work_item(self, work_item: Dict[str, Any]) -> PendingDatabaseContext:
        """Process a single deduplication work item into a PendingDatabaseContext."""
        context = PendingDatabaseContext()

        canonical_address = work_item.get("canonical_address", "")
        master_address_id = work_item.get("master_address_id")

        if canonical_address and master_address_id:
            # Fetch the master address
            addresses = self.db_ops.select_dataclass(
                Address, where_clause="address_id = ?", params=(master_address_id,)
            )
            if addresses:
                master_address = addresses[0]
                # Create geocoding record
                geocoding = master_address.create_geocoding()
                context.addObjectToDatabase(geocoding)

                # Create update operation to link geocoding_id to master address
                link_update = {
                    'address_id': master_address_id,
                    'master_id': master_address_id,
                    'geocoding_id': geocoding.geocoding_id
                }
                link_op = DatabaseOperation(
                    DatabaseOperationType.GENERIC_UPDATE,
                    {
                        'table_name': 'Addresses',
                        'update_data': link_update,
                        'id_column': 'address_id'
                    }
                )
                context.addOperationToDatabase(link_op)

        # Add deduplication operation
        dedup_op = DatabaseOperation(
            DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH,
            work_item
        )
        context.addOperationToDatabase(dedup_op)

        # Add progress update
        progress_op = DatabaseOperation(
            DatabaseOperationType.PROGRESS_UPDATE,
            {"count": 1}
        )
        context.addOperationToDatabase(progress_op)

        return context

    def get_work_count(self, max_files: Optional[int] = None) -> int:
        """Get the total number of deduplication work items."""
        query = """
        SELECT COUNT(DISTINCT canonical_address) as total_canonical_addresses
        FROM Addresses
        WHERE master_id IS NULL
            AND canonical_address IS NOT NULL
            AND canonical_address != ''
        """
        result = self.db_ops.execute_query(query)
        row = result.fetchone() if result else None
        total = int(row[0]) if row and row[0] is not None else 0
        if max_files:
            total = min(total, max_files)
        return total

    def get_progress_config(self, max_files: Optional[int] = None) -> Tuple[int, str, str]:
        """Get progress bar configuration for address deduplication."""
        total = self.get_work_count(max_files)
        return total, 'groups', 'Address deduplication'

    def deduplicate_addresses(self, progress_bar=None) -> int:
        """Deduplicate addresses using the new pending_canonicals approach."""
        log_info("Starting address deduplication using pending_canonicals approach")

        # Phase 1: Setup - Create and populate pending_canonicals table
        log_info("Phase 1: Setup - Creating pending_canonicals table")
        self.setup_pending_canonicals()

        # Phase 2: Processing - Use producer-consumer pattern with pending_canonicals
        log_info("Phase 2: Processing - Deduplicating addresses using pending_canonicals")
        total_processed = self.process_parallel(global_config.max_files)

        # Phase 3: Cleanup - Drop temporary table
        log_info("Phase 3: Cleanup - Dropping pending_canonicals table")
        try:
            self.db_ops.execute_query("DROP TABLE IF EXISTS pending_canonicals;")
            log_info("Successfully dropped pending_canonicals table")
        except Exception as e:
            log_warning(f"Failed to drop pending_canonicals table: {e}")

        log_info(f"Address deduplication completed: {total_processed} canonical address groups processed")
        return total_processed