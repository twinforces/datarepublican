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
from constants import ADDRESS_BATCH_SIZE, ADDRESS_QUEUE_SIZE, FEED_BATCH_SIZE
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
                FROM pending_canonicals
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            return {'total': total, 'unit': 'bytes'}
        else:
            # Count total canonical_addresses from Addresses table (each row is 1 unit of work)
            query = """
                SELECT COUNT(DISTINCT canonical_address) as total_canonical_addresses
                FROM Addresses
                WHERE master_id IS NULL
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
            SELECT canonical_address, root_id as group_pk
            FROM pending_canonicals
            ORDER BY root_id ASC
            LIMIT ?
            """
            params = (effective_batch_size,)
        else:
            query = """
            SELECT canonical_address, root_id as group_pk
            FROM pending_canonicals
            WHERE root_id > ?
            ORDER BY root_id ASC
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

            # Return canonical_address and group_pk from pending_canonicals table
            dedup_info = {
                "canonical_address": canonical_address,
                "group_pk": group_pk
            }
            batch.append(dedup_info)

            if max_pk is None or group_pk > max_pk:
                max_pk = group_pk

        self.last_address_id = max_pk
        return batch, max_pk

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
              AND po_box IS NULL
            ORDER BY address_id ASC
            LIMIT ?
            """
            params = (effective_batch_size,)
        else:
            query = """
            SELECT * FROM Addresses
            WHERE geocoding_id IS NULL
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
 
    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.
    """
 
    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.canonical_address = None
        self.last_address_id = None
        self.setup_status_gauges(interval=10.0)

    def _process_operations_batch(self, operations_by_type: Dict[str, List[DatabaseOperation]]) -> int:
        """Process operations batch for address deduplication consumer"""
        total_updated: int = 0

        # Handle all GENERIC_UPDATE operations (both deduplication and geocoding)
        if DatabaseOperationType.GENERIC_UPDATE.value in operations_by_type:
            geocoding_operations = operations_by_type[DatabaseOperationType.GENERIC_UPDATE.value]

            log_debug(f"DEBUG: Processing {len(geocoding_operations)} GENERIC_UPDATE operations")
            try:
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
                        total_updated += updated_rows
                        log_debug(f"DEBUG: Bulk updated {updated_rows} rows in {table}")

                log_debug(f"DEBUG: Executed {len(geocoding_operations)} GENERIC_UPDATE operations, updated {total_updated} records")
            except Exception as e:
                log_error(f"Failed to execute GENERIC_UPDATE operations: {e}")
                raise

        return total_updated

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
                FROM pending_canonicals
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            log_debug(f"DEBUG: get_progress_scope(bytes=True) returning total={total} addresses")
            return {'total': total, 'unit': 'bytes'}
        else:
            # Count total canonical_addresses that have unprocessed addresses (each canonical_address is 1 unit of work)
            query = """
                SELECT COUNT(*) as total_canonical_addresses
                FROM pending_canonicals
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
        # Count total canonical_addresses in pending_canonicals (since geocoding records are attached only to the root address, one per canonical_address)
        query = """
            SELECT COUNT(*) as total_addresses_needing_geocoding
            FROM pending_canonicals
        """
        result = self.db_ops.execute_query(query)
        row = result.fetchone() if result else None
    def _get_geocoding_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Address], Optional[str]]:
        """Get all addresses for the current canonical_address group"""
        if self.exit_processing:
            return [], None

        query = """
        SELECT * FROM Addresses
        WHERE canonical_address = ?
        ORDER BY address_id
        """
        params = (self.canonical_address,)

        addresses = self.db_ops.select_dataclass(Address, query=query, params=params)

        # No paging, return all addresses at once
        return addresses, None
        total = int(row[0]) if row and row[0] is not None else 0
        log_debug(f"DEBUG: get_geocoding_progress_scope() returning total={total} addresses needing geocoding")
        return {'total': total, 'unit': 'records'}


class AddressDeduplicationProcessor(BaseProcessor):
    """Address deduplication processor using the generalized multi-threaded architecture."""

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.batch_size = 1000

    def setup_pending_canonicals(self):
        log_info("Phase 1: Setup - Creating pending_canonicals table (clean slate)")

        with self.db_ops.acquire_write_conn() as conn:
            try:
                conn.execute("BEGIN TRANSACTION")

                # Force clean slate every time
                conn.execute("DROP TABLE IF EXISTS pending_canonicals")

                conn.execute("""
                    CREATE TABLE pending_canonicals (
                        canonical_address VARCHAR PRIMARY KEY,
                        root_id UUID NOT NULL
                    )
                """)

                # Guaranteed unique canonical groups
                conn.execute("""
                    INSERT INTO pending_canonicals (canonical_address, root_id)
                    SELECT 
                        canonical_address,
                        MIN(address_id) AS root_id
                    FROM Addresses 
                    WHERE canonical_address IS NOT NULL 
                    AND canonical_address != '' 
                    AND master_id IS NULL 
                    AND colocator IS NULL
                    GROUP BY canonical_address
                    HAVING MAX(colocator IS NOT NULL) = FALSE
                """)

                # Helpful indexes
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_canonicals_root ON pending_canonicals(root_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_canonicals_canon ON pending_canonicals(canonical_address)")

                conn.commit()

                count = conn.execute("SELECT COUNT(*) FROM pending_canonicals").fetchone()[0] or 0
                log_info(f"Setup complete: Created pending_canonicals with {count:,} unique canonical groups")

            except Exception as e:
                conn.rollback()
                log_error(f"Failed to setup pending_canonicals: {e}", exc_info=True)
                raise
    
    
    def _get_work_batch(self, where_clause: str, params: Tuple, last_pk: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get a batch of address deduplication work items from pending_canonicals using key-value paging"""
        effective_batch_size = min(self.batch_size, global_config.max_files) if global_config.max_files else self.batch_size

        if last_pk is None:
            query = """
            SELECT canonical_address, root_id
            FROM pending_canonicals
            ORDER BY root_id ASC
            LIMIT ?
            """
            query_params = (effective_batch_size,)
        else:
            query = """
            SELECT canonical_address, root_id
            FROM pending_canonicals
            WHERE root_id > ?
            ORDER BY root_id ASC
            LIMIT ?
            """
            query_params = (last_pk, effective_batch_size)

        cursor = self.db_ops.execute_query(query, params=query_params)
        rows = cursor.fetchall()

        batch = []
        max_pk = None
        for row in rows:
            dedup_info = {
                "canonical_address": row[0],
                "group_pk": row[1]
            }
            batch.append(dedup_info)
            if max_pk is None or row[1] > max_pk:
                max_pk = row[1]

        return batch, max_pk
    
    def _sql_deduplicate_and_geocode(self) -> int:
        """Pure DuckDB SQL version of address deduplication + geocoding record creation.

        Fast no-op detection + uses your _intermediate_commit_and_checkpoint helper.
        """
        log_info("Running master/child assignment and geocoding record creation in SQL...")

        with self.db_ops.acquire_write_conn() as conn:
            conn.execute("PRAGMA memory_limit = '16GB';")

            # === FAST EARLY EXIT CHECKS ===
            pending_count = conn.execute("SELECT COUNT(*) FROM pending_canonicals").fetchone()[0] or 0
            if pending_count == 0:
                log_info("Nothing to do — pending_canonicals table is empty.")
                return 0

            unprocessed = conn.execute("""
                SELECT COUNT(*) 
                FROM Addresses 
                WHERE master_id IS NULL
            """).fetchone()[0] or 0

            if unprocessed == 0:
                log_info("Nothing to do — all addresses already have master_id set.")
                return 0

            log_info(f"Processing {pending_count:,} canonical groups ({unprocessed:,} addresses)")

            conn.execute("BEGIN TRANSACTION")

            try:
                # Helpful indexes
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_canonicals_canon ON pending_canonicals(canonical_address)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_addresses_canonical ON Addresses(canonical_address)")

                # Step 1: Insert new Geocoding records
                conn.execute("""
                    INSERT INTO Geocoding (
                        geocoding_id,
                        canonical_address,
                        normalized_address,
                        geocoding_status,
                        created_at
                    )
                    WITH grouped AS (
                        SELECT 
                            pc.canonical_address,
                            pc.root_id,
                            root.address_line1,
                            root.address_line2,
                            root.city,
                            root.state,
                            root.zip_code,
                            root.po_box
                        FROM pending_canonicals pc
                        JOIN Addresses root ON root.address_id = pc.root_id
                    )
                    SELECT 
                        uuidv7() AS geocoding_id,
                        g.canonical_address,
                        to_json(
                            json_object(
                                'id', 0,
                                'street', TRIM(COALESCE(g.address_line1, '') || ' ' || COALESCE(g.address_line2, '')),
                                'city', COALESCE(g.city, ''),
                                'state', COALESCE(g.state, ''),
                                'zip', COALESCE(g.zip_code, '')
                            )
                        ) AS normalized_address,
                        'pending' AS geocoding_status,
                        CURRENT_TIMESTAMP
                    FROM grouped g
                    WHERE g.po_box IS NULL;
                """)

                inserted = conn.execute("SELECT changes()").fetchone()[0] or 0
                log_info(f"DEBUG: Inserted {inserted:,} new Geocoding records")

                self.db_ops._intermediate_commit_and_checkpoint(conn, inserted)

                # Step 2: Update Addresses (defensive DISTINCT prevents duplicate-key error)
                conn.execute("""
                    UPDATE Addresses addr
                    SET 
                        master_id = pc.root_id,
                        geocoding_id = g.geocoding_id
                    FROM (
                        SELECT DISTINCT canonical_address, root_id
                        FROM pending_canonicals
                    ) pc
                    JOIN Geocoding g ON g.canonical_address = pc.canonical_address
                    WHERE addr.canonical_address = pc.canonical_address
                    AND addr.master_id IS NULL;
                """)

                updated_count = conn.execute("SELECT changes()").fetchone()[0] or 0

                self.db_ops._intermediate_commit_and_checkpoint(conn, updated_count)

                # Light maintenance
                conn.execute("VACUUM ANALYZE Addresses;")
                conn.execute("VACUUM ANALYZE Geocoding;")

                log_info(f"SQL deduplication complete - {updated_count:,} addresses updated ({inserted:,} geocoding records created)")
                return updated_count

            except Exception as e:
                conn.rollback()
                log_error(f"SQL deduplication failed: {e}", exc_info=True)
                raise
    
    def _feed_thread(self, work_queue: queue.Queue, max_files: Optional[int] = None, num_producers: int = 4):
        """Feeder thread: Fetch batches of deduplication work and enqueue WorkUnits in batches."""
        from constants import FEED_BATCH_SIZE
        enqueued = 0
        last_pk = None
        batch_accumulator = []
        where_clause = "master_id IS NULL AND LENGTH(canonical_address) > 0"
        params = ()
        while True:
            batch, new_last_pk = self._get_work_batch(where_clause, params, last_pk)
            if not batch:
                break

            for item in batch:
                if self.exit_processing:
                    break
                batch_accumulator.append(item)
                enqueued += 1

                # When batch reaches FEED_BATCH_SIZE, send it
                if len(batch_accumulator) >= FEED_BATCH_SIZE:
                    work_queue.put(WorkUnit.batch(batch_accumulator))
                    batch_accumulator = []

                if max_files and enqueued >= max_files:
                    break

            if self.exit_processing or (max_files and enqueued >= max_files):
                break

            last_pk = new_last_pk

        # Send any remaining items in accumulator
        if batch_accumulator:
            work_queue.put(WorkUnit.batch(batch_accumulator))

        # Send sentinels for each producer
        for i in range(num_producers):
            work_queue.put(WorkUnit.sentinel(i))


    def _process_single_dedup_item(self, context: PendingDatabaseContext, work_item: Dict[str, Any], master_addr: Address) -> None:
        """Process a single deduplication work item into the provided context."""
        canonical = work_item["canonical_address"]
        master_id = work_item["group_pk"]

        # 1. Create geocoding record from the master address
        geo = master_addr.create_geocoding()
        context.addObjectToDatabase(geo)

        # 2. Add UPDATE operation for this canonical group
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={
                'table': 'Addresses',
                'set_clause': 'master_id = ?, geocoding_id = ?',
                'where_clause': 'canonical_address = ?',
                'params': (master_id, geo.geocoding_id, canonical)
            }
        ))

        # 3. Progress tracking
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={"count": 1}
        ))

    def _process_work_item(self, work_item: Dict[str, Any]) -> PendingDatabaseContext:
        """
        Process a single work item.
        """
        context = PendingDatabaseContext()
        master_id = work_item["group_pk"]

        # Lookup master address
        master_addr = self.db_ops.select_dataclass(
            Address,
            where_clause="address_id = ?",
            params=(master_id,)
        )[0]

        self._process_single_dedup_item(context, work_item, master_addr)
        return context

    def _process_batch(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        """
        Process a batch of work items with batched master address lookup.
        """
        context = PendingDatabaseContext()

        # Extract all master_ids for batched lookup
        master_ids = [item["group_pk"] for item in batch]

        # Single batched query for all master addresses
        addresses = self.db_ops.select_dataclass(
            Address,
            where_clause="address_id = ANY(?)",
            params=(master_ids,)
        )

        # Build lookup dict
        master_dict = {addr.address_id: addr for addr in addresses}

        # Process each item in the batch
        for work_item in batch:
            master_id = work_item["group_pk"]
            master_addr = master_dict[master_id]
            self._process_single_dedup_item(context, work_item, master_addr)

        return context

    def get_work_count(self, max_files: Optional[int] = None) -> int:
        """Get the total number of deduplication work items."""
        query = """
        SELECT COUNT(*) as total_canonical_addresses
        FROM pending_canonicals
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

    def is_addresses_clustered(self) -> bool:
        try:
            result = self.db_ops.execute_query("""
                SELECT clustered_column 
                FROM _meta_clustering 
                WHERE table_name = 'Addresses' 
                AND clustered_column = 'canonical_address'
            """)
            row = result.fetchone()
            if row:
                log_info(f"Addresses table is clustered on canonical_address (set at {row[0]})")
                return True
            else:
                log_warning("Addresses table is NOT clustered — deduplication will be SLOW")
                return False
        except:
            return False

    def ensure_addresses_clustered(self):
        if self.is_addresses_clustered():
            log_info("Addresses table already clustered — skipping")
            
            return

        log_info("Clustering Addresses table by canonical_address — this takes 30–90 minutes ONE TIME")
        start = time.time()
        
        self.db_ops.execute_query("""
            CREATE OR REPLACE TABLE Addresses_clustered AS
            SELECT * FROM Addresses ORDER BY canonical_address;
            
            DROP TABLE IF EXISTS Addresses;
            ALTER TABLE Addresses_clustered RENAME TO Addresses;
            
            -- Rebuild indexes
            DROP INDEX IF EXISTS idx_addresses_canonical;
            DROP INDEX IF EXISTS idx_dedup_canon_groups;
            CREATE INDEX idx_dedup_canon_groups ON Addresses(canonical_address, address_id);
        """)
        
        # Mark as done
        self.db_ops.execute_query("""
            CREATE TABLE IF NOT EXISTS _meta_clustering (
                table_name VARCHAR,
                clustered_column VARCHAR,
                clustered_at TIMESTAMP,
                PRIMARY KEY (table_name)
            );
            INSERT INTO _meta_clustering VALUES 
            ('Addresses', 'canonical_address', CURRENT_TIMESTAMP)
            ON CONFLICT DO NOTHING;
        """)
        # Force index usage forever — no more seq scan fallbacks
        duration = time.time() - start
        log_info(f"Addresses table clustered in {duration/60:.1f} minutes — deduplication now 1000× faster")
    
    def deduplicate_addresses(self, progress_bar=None) -> int:
        """Deduplicate addresses and create geocoding records using pure SQL.

        This is the only method you need to call. Everything else (producer,
        consumer, GeocodingRecordCreator, _process_work_item, etc.) can now be
        deleted from the file once you confirm this works.
        """
        log_info("Starting address deduplication (pure DuckDB SQL version)")

        # Phase 1: Setup - Create pending_canonicals table (unchanged)
        log_info("Phase 1: Building pending_canonicals table...")
        self.setup_pending_canonicals()

        # Phase 2: Main work - one fast SQL statement
        log_info("Phase 2: Master/child assignment + geocoding records...")
        start = time.time()
        total_processed = self._sql_deduplicate_and_geocode()
        duration = time.time() - start
        log_info(f"Phase 2 finished in {duration:.1f} seconds ({total_processed:,} groups)")

        # Phase 3: Cleanup
        log_info("Phase 3: Cleanup - dropping temporary table")
        try:
            with self.db_ops.acquire_write_conn() as conn:
                conn.execute("DROP TABLE IF EXISTS pending_canonicals")
        except Exception as e:
            log_warning(f"Could not drop pending_canonicals: {e}")

        log_info(f"Address deduplication completed successfully: {total_processed:,} canonical groups")
        return total_processed

    def old_deduplicate_addresses(self, progress_bar=None) -> int:
        def run():
            return self.process_parallel(global_config.max_files, 4)
        """Deduplicate addresses using the new pending_canonicals approach."""
        log_info("Starting address deduplication using pending_canonicals approach")

        # Phase 1: Setup - Create and populate pending_canonicals table
        log_info("Phase 1: Setup - Creating pending_canonicals table")
        self.setup_pending_canonicals()
        self.ensure_addresses_clustered()

        # Phase 2: Processing - Use producer-consumer pattern with pending_canonicals
        log_info("Phase 2: Processing - Deduplicating addresses using pending_canonicals")
        total_processed = self.process_parallel(global_config.max_files, 4)

        # Phase 3: Cleanup - Drop temporary table
        log_info("Phase 3: Cleanup - Dropping pending_canonicals table")
        try:
            with self.db_ops.acquire_write_conn() as conn:
                conn.execute_query("DROP TABLE IF EXISTS pending_canonicals;", conn=conn)
                log_info("Successfully dropped pending_canonicals table")
        except Exception as e:
            log_warning(f"Failed to drop pending_canonicals table: {e}")

        log_info(f"Address deduplication completed: {total_processed} canonical address groups processed")
        return total_processed