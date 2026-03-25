#!/usr/bin/env python3
"""
pending_database_context.py - Context object for collecting database objects during XML parsing

This module provides a PendingDatabaseContext class that collects all objects
to be inserted into the database, eliminating the need for tuple passing
through the parse tree.

ARCHITECTURE OVERVIEW:
- PendingDatabaseContext (PDC): Collects database objects during processing
- Eliminates tuple passing through parse trees
- Handles ownership relationships and operation execution
- Integrates with DatabaseOperations for bulk inserts

KEY FEATURES:
- Type-based object collection (charity, officer, grant, etc.)
- Ownership relationship management
- Direct database execution via save_to_database()
- Operation-based updates (XML file status, geocoding, etc.)
- Context merging for batch processing

INTEGRATION:
- Used by all processors for object accumulation
- Called by BaseConsumer.execute_contexts_batch()
- Works with DatabaseOperations.INSERT_BY_TYPE() for type-grouped inserts
"""

from typing import Dict, List, Any, Optional, Generator
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address, Geocoding, ZipFile, XMLFile, AuthoritativeEin
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_error, log_warning, log_debug
from datetime import datetime
import threading

class PendingDatabaseContext:
    """
    Context object that collects all database objects during XML parsing.

    This is the CORE data collection class in the PDC (PendingDatabaseContext) architecture.
    It accumulates database objects by type during processing, then executes them in the
    correct ownership order when save_to_database() is called.

    OBJECT ORGANIZATION:
    - Maintains type-based collections: charity, officer, grant, contractor, political_contribution, address
    - Handles ownership relationships automatically
    - Supports operation-based updates (XML status, geocoding, etc.)

    EXECUTION MODEL:
    - save_to_database(): Executes all collected objects via INSERT_BY_TYPE
    - Handles operations (XML updates, geocoding, deduplication)
    - Returns all generated IDs for relationship tracking

    THREADING: Safe for multi-threaded producers, single-threaded execution
    PERFORMANCE: Batches objects by type for efficient bulk inserts
    POOL INTEGRATION: Uses db_ops.acquire_write_conn() for tx management
    """
    _updated_counter = 0

    def __init__(self, xml_id: str = None, xml_content: bytes = None):
        """Initialize the context with empty collections"""
        self.xml_id = xml_id
        self.xml_content = xml_content
        self.objects: Dict[str, List[Any]] = {
            'charity': [],
            'officer': [],
            'grant': [],
            'contractor': [],
            'political_contribution': [],
            'address': [],
            'geocoding': [],  # For geocoding records
            'zipfile': [],  # For ZIP file records
            'authoritativeein': [],  # For AuthoritativeEin records
            'xmlfile': []   # For XML file records
        }
        self._updates: List[Dict[str, Any]] = []  # For future UPDATE operations
        self.operations: List[DatabaseOperation] = []  # For generic database operations
        self.error_message: Optional[str] = None  # Error message for failed processing
        self.estimated_updates: int = 0  # Estimated number of database updates this context will perform

    def addObjectToDatabase(self, obj: Any) -> None:
        """
        Add an object to the appropriate collection based on its type.

        Args:
            obj: The database model object to add (Charity, Officer, Grant, etc.)
        """
        obj_type = type(obj).__name__.lower()

        # Add to the appropriate list (no special handling for charity)
        if obj_type in self.objects:
            self.objects[obj_type].append(obj)
            # Increment estimated updates (rough estimate for size-based batching)
            self.estimated_updates += 1
            log_debug(f"Added {obj_type} object to PDC (total {len(self.objects[obj_type])})")
        else:
            raise ValueError(f"Unknown object type: {obj_type}")

    def updateObject(self, obj_type: str, obj_id: str, updates: Dict[str, Any]) -> None:
        """
        Record an UPDATE operation to be performed on an existing database object.

        Args:
            obj_type: The type of object to update (e.g., 'charity', 'officer')
            obj_id: The ID of the object to update
            updates: Dictionary of field updates (column -> value)
        """
        self._updates.append({
            'type': obj_type,
            'id': obj_id,
            'updates': updates
        })

    def addOperationToDatabase(self, operation: DatabaseOperation) -> None:
        """
        Add a generic database operation to be executed.

        Args:
            operation: The DatabaseOperation to add
        """
        self.operations.append(operation)
        # Increment estimated updates (rough estimate for size-based batching)
        self.estimated_updates += 1
        log_debug(f"Added operation {operation.operation_type} to PDC (total {len(self.operations)})")

    def getObjectsByType(self, obj_type: str) -> List[Any]:
        """
        Get all objects of a specific type.

        Args:
            obj_type: The object type to retrieve ('charity', 'officer', etc.)

        Returns:
            List of objects of the specified type
        """
        return self.objects.get(obj_type, [])

    def getCharity(self) -> Optional[Charity]:
        """
        Get the first charity object if available.

        Returns:
            The first Charity object, or None if no charities exist
        """
        charities = self.getObjectsByType('charity')
        return charities[0] if charities else None

    def getAllObjects(self) -> Dict[str, List[Any]]:
        """
        Get all objects organized by type.

        Returns:
            Dictionary mapping object types to lists of objects
        """
        return self.objects.copy()

    def getUpdates(self) -> List[Dict[str, Any]]:
        """
        Get all recorded UPDATE operations.

        Returns:
            List of update operations
        """
        return self._updates.copy()
    
    def getUpdatesCount(self) -> int:
        return len(self._updates)
    
    def getOperationsCount(self)-> int:
        return len(self.operations)

    def isEmpty(self) -> bool:
        """Check if this context contains any objects"""
        return all(not objs for objs in self.objects.values())

    def getObjectCounts(self) -> Dict[str, int]:
        """
        Get counts of objects by type.

        Returns:
            Dictionary mapping object types to counts
        """
        return {obj_type: len(objects) for obj_type, objects in self.objects.items()}

    def getTotalObjectCount(self) -> int:
        """
        Get total count of all objects in this context.

        Returns:
            Total number of objects across all types
        """
        return sum(len(objects) for objects in self.objects.values())

    def save_to_database(self, db_ops: DatabaseOperations, checkpoint: bool = False) -> List[str]:
        """
        Execute all collected objects and operations directly with periodic flushing.
        This is the PRIMARY execution method for PDC. It handles both:
        1. Objects collected via addObjectToDatabase (using INSERT_BY_TYPE for each type)
        2. Operations collected via addOperationToDatabase (XML updates, geocoding, etc.)
        
        EXECUTION ORDER:
        - Objects inserted first (by type, respecting ownership)
        - Operations executed second (updates, geocoding, etc.)
        
        Args:
            db_ops: DatabaseOperations instance for executing operations
        
        Returns:
            List of generated IDs from all inserted objects (for relationship tracking)
        
        THREADING: Must be called from single consumer thread (DuckDB writer constraint)
        PERFORMANCE: Batches by type for efficient bulk inserts
        """
        print(f"###DEBUG### PDC_SAVE: Starting save_to_database with {len(self.operations)} operations")
        if not self.operations and all(not objs for objs in self.objects.values()):
           print("###DEBUG### PDC_SAVE: No operations or objects to save, returning early")
           return []

        ids: List[str] = []
        with db_ops.acquire_write_conn() as conn:

            # <<< WINNING CODE — PERIODIC FLUSHING FOR ADDRESS DEDUP >>>
            FLUSH_EVERY_N_ROWS = 10_000   # More aggressive flushing to prevent out-of-memory

            try:
                # Start explicit transaction
                log_debug("###DEBUG### PDC_SAVE: Beginning transaction")
                conn.execute("BEGIN TRANSACTION")

                # Insert any real objects first (normal for XML parsing, skipped in address dedup)
                for obj_type in ['zipfile', 'xmlfile', 'charity', 'officer', 'grant',
                                    'contractor', 'political_contribution', 'address', 'geocoding']:
                    objects = self.objects.get(obj_type, [])
                    if objects:   # <-- only call if there are actual objects
                        print(f"###DEBUG### PDC_SAVE: Inserting {len(objects)} {obj_type} objects")
                        new_ids = db_ops.INSERT_BY_TYPE(objects, obj_type.capitalize(), commit_batches=False, conn=conn)
                        PendingDatabaseContext._updated_counter += len(new_ids)
                        if new_ids:
                            ids.extend(new_ids)

                # Execute operations with periodic flushing
                print(f"###DEBUG### PDC_SAVE: Executing {len(self.operations)} operations")
                for i, operation in enumerate(self.operations):
                    print(f"###DEBUG### PDC_SAVE: Executing operation {i+1}/{len(self.operations)}: type={operation.operation_type}, data_keys={list(operation.data.keys()) if operation.data else 'None'}")
                    self._execute_operation(db_ops, operation)

                    if operation.operation_type == DatabaseOperationType.GENERIC_UPDATE:
                        # Handle both traditional bulk updates and WHERE clause updates
                        if 'updates' in operation.data:
                            # Traditional bulk update - count the number of records
                            rows_this_op = len(operation.data['updates'])
                            print(f"###DEBUG### PDC_SAVE: GENERIC_UPDATE with {rows_this_op} updates to table {operation.data.get('table')}")
                        elif 'where_clause' in operation.data:
                            # WHERE clause update - check if batched
                            param_sets = operation.data.get('param_sets')
                            if param_sets:
                                # Batched WHERE updates - estimate based on number of operations
                                rows_this_op = len(param_sets) * 5  # Conservative estimate of 5 rows per canonical group
                                print(f"###DEBUG### PDC_SAVE: WHERE_UPDATE batched with {len(param_sets)} param sets")
                            else:
                                # Single WHERE clause update - estimate based on typical canonical group size
                                # Most canonical groups have 2-10 addresses, but some have hundreds
                                # Use conservative estimate of 5 addresses per canonical group
                                rows_this_op = 10
                                print(f"###DEBUG### PDC_SAVE: WHERE_UPDATE single")
                        else:
                            # Unknown format, skip counting
                            rows_this_op = 0
                            print("###DEBUG### PDC_SAVE: GENERIC_UPDATE unknown format")

                        PendingDatabaseContext._updated_counter += rows_this_op

                        if PendingDatabaseContext._updated_counter >= FLUSH_EVERY_N_ROWS:
                            print(f"###DEBUG### PDC_SAVE: Triggering intermediate commit after {PendingDatabaseContext._updated_counter} updates")
                            PendingDatabaseContext._updated_counter = self._intermediate_commit_and_checkpoint(conn, PendingDatabaseContext._updated_counter, db_ops)
                            # Get a fresh connection after recycling
                            conn.execute("BEGIN TRANSACTION")
                            PendingDatabaseContext._updated_counter = 0

                # Final commit
                print("###DEBUG### PDC_SAVE: Executing final commit")
                conn.commit()
                print("###DEBUG### PDC_SAVE: Final commit completed successfully")
                log_info("Final commit completed – address deduplication batch saved")

            except Exception as e:
                print(f"###DEBUG### PDC_SAVE: ERROR during save: {e}")
                try:
                    conn.rollback()
                except:
                    pass
                log_error(f"Failed to save context to database: {e}", exc_info=True)
                raise

            if checkpoint:
            # Final checkpoint with macOS workaround
                try:
                    # Recycle connection first to enable reliable checkpointing on macOS
                    #db_ops.recycle_connection()
                    # Then attempt CHECKPOINT for WAL flushing
                    conn.execute("CHECKPOINT")
                    print("###DEBUG### PDC_SAVE: Final checkpoint completed successfully")
                    log_info("Final checkpoint completed successfully")
                except Exception as e:
                    print(f"###DEBUG### PDC_SAVE: Final checkpoint failed: {e}")
                    log_warning(f"Final checkpoint failed: {e}")
                    # Continue anyway - data is still committed

            print(f"###DEBUG### PDC_SAVE: Completed save_to_database, returned {len(ids)} IDs")
        return ids

    def _execute_operation(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """
        Execute a single database operation.

        Args:
            db_ops: DatabaseOperations instance
            operation: The operation to execute
        """

        op_type = operation.operation_type
        data = operation.data

        if op_type == DatabaseOperationType.XML_FILE_UPDATE:
            self._execute_xml_file_update(db_ops, operation)
        elif op_type == DatabaseOperationType.UPDATE_XML_EIN:
            self._execute_update_xml_ein(db_ops, operation)
        elif op_type == DatabaseOperationType.OPTIMIZE_DATABASE:
            db_ops.optimize_database()
        elif op_type == DatabaseOperationType.GENERIC_UPDATE:
            self._execute_generic_update(db_ops, operation)
        elif op_type == DatabaseOperationType.PROGRESS_UPDATE:
            from logging_utils import update_progress
            update_progress(n=operation.data.get("count", 0))
        elif op_type == DatabaseOperationType.INSERT_GEOCODING:
            self._execute_insert_geocoding(db_ops, operation)
        elif op_type == DatabaseOperationType.UPDATE_GEOCODING:
            self._execute_update_geocoding(db_ops, operation)
        elif op_type == DatabaseOperationType.UPDATE_ADDRESS_GEOCODING:
            self._execute_update_address_geocoding(db_ops, operation)
        elif op_type == DatabaseOperationType.AUTHORITATIVE_EIN_UPDATE:
            name = data['name']
            colocator = data['colocator']
            ein = data['ein']
            db_ops.update_authoritative_ein(name, colocator, ein)
        # Add other operation types as needed

    def _execute_xml_file_update(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """Execute XML file update operation"""
        metadata = operation.data
        xml_id = operation.xml_id
    
        from constants import CURRENT_PROCESSING_VERSION
        db_ops.execute_query("""
            UPDATE XmlFiles SET
                processed = ?,
                processing_version = ?,
                file_size = ?,
                ein = ?,
                tax_year = ?,
                form_type = ?,
                org_type = ?,
                error_message = ?,
                processed_at = ?
            WHERE xml_id = ?
        """, (
            metadata["processed"],
            CURRENT_PROCESSING_VERSION,
            metadata["file_size"],
            metadata["ein"],
            metadata["tax_year"],
            metadata["form_type"],
            metadata.get("org_type"),
            metadata["error_message"],
            datetime.now().isoformat() if metadata["processed"] else None,
            xml_id
        ))

    def _execute_update_xml_ein(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """Execute UPDATE_XML_EIN operation"""
        xml_id = operation.xml_id
        data = operation.data
        ein = data.get("ein") if data else None
        if ein:
            db_ops.execute_query("UPDATE XmlFiles SET ein = ? WHERE xml_id = ?", (ein, xml_id))


    def _execute_generic_update(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> int:
        """Execute generic update operation"""
        data = operation.data
        updated_count = 0

        # Check if this is a WHERE clause update (optimization for address deduplication)
        where_clause = data.get('where_clause')
        if where_clause:
            # WHERE clause update - delegate to database_operations
            db_ops._execute_generic_update_operation(operation, None)
            # For WHERE updates, we can't easily get the row count, so return 0
            # The actual update happened in database_operations
            return 0
        else:
            # Traditional bulk update by ID
            table_name = data.get("table") or data.get("table_name")
            update_data = data.get("updates") or data.get("update_data", [])
            id_column = data.get("key_field") or data.get("id_column", "id")

            if table_name and update_data:
                # Check if update_data is a list (bulk updates) or single dict
                if isinstance(update_data, list):
                    # Bulk update - update_data is already a list of records
                    updated_count = db_ops.bulk_update(table_name, update_data, id_column=id_column)
                else:
                    # Single update - wrap in list for bulk_update
                    key_value = data.get("key_value")
                    if key_value is not None:
                        update_record = {**update_data, id_column: key_value}
                        updated_count = db_ops.bulk_update(table_name, [update_record], id_column=id_column)
                    else:
                        # Fallback for old format
                        updated_count = db_ops.bulk_update(table_name, [update_data], id_column=id_column)

        return updated_count

    def _execute_insert_geocoding(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """Execute geocoding insert operation"""
        data = operation.data
        normalized_address = data.get("normalized_address")
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        status = data.get("status", "pending")

        if normalized_address is not None:
            geocoding_id = db_ops.insert_geocoding_record(normalized_address, latitude, longitude, status, commit=False)

    def _execute_update_geocoding(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """Execute geocoding update operation"""
        data = operation.data
        table_name = data.get("table")
        update_data = data.get("updates", {})
        key_field = data.get("key_field", "geocoding_id")
        key_value = data.get("key_value")

        if table_name and update_data and key_value is not None:
            # Build SET clause from update_data (excluding the key field if present)
            set_parts = []
            params = []
            for field, value in update_data.items():
                if field != key_field:  # Don't include key field in SET clause
                    set_parts.append(f"{field} = ?")
                    params.append(value)

            if set_parts:
                set_clause = ", ".join(set_parts)
                sql = f"UPDATE {table_name} SET {set_clause} WHERE {key_field} = ?"
                params.append(key_value)
                db_ops.execute_query(sql, tuple(params))

    def _execute_update_address_geocoding(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """Execute address geocoding update operation"""
        # The operation.data should now contain a Geocoding object
        geocoding = operation.data
        if geocoding and hasattr(geocoding, 'canonical_address') and hasattr(geocoding, 'geocoding_id'):
            # Create colocator from geocoding results
            colocator = None
            if geocoding.latitude is not None and geocoding.longitude is not None:
                colocator = f"LL:{geocoding.latitude}:{geocoding.longitude}"

            if geocoding.canonical_address:
                try:
                    db_ops.update_address_geocoding_by_canonical(geocoding.canonical_address, geocoding.geocoding_id, colocator)
                except Exception as e:
                    # Log the error but don't fail the entire transaction
                    log_error(f"Failed to update addresses for canonical_address {geocoding.canonical_address}: {e}")
                    # Continue with the transaction - geocoding record update will still succeed
    def _intermediate_commit_and_checkpoint(self, conn, updated_so_far: int, db_ops: DatabaseOperations) -> int:
        """
        Commit the current transaction, recycle connection to enable checkpointing, then checkpoint for WAL flushing.

        On macOS, DuckDB has known issues where CHECKPOINT doesn't work reliably.
        The solution is to recycle the connection first (to work around macOS issues),
        then attempt CHECKPOINT (for performance benefits from WAL flushing).
        """
        try:
            log_debug("Performing intermediate commit and checkpoint...")
            conn.commit()                               # Ends the huge transaction

            # Now attempt CHECKPOINT for WAL flushing and performance benefits
            try:
                # Get the fresh connection after recycling
                with db_ops.acquire_write_conn() as fresh_conn:
                    fresh_conn.execute("CHECKPOINT")
                    log_debug("Intermediate checkpoint succeeded after connection recycling")
            except Exception as checkpoint_e:
                log_warning(f"Checkpoint failed even after recycling: {checkpoint_e}")
                # Continue anyway - the recycling itself provides WAL cleanup

            log_info(f"INTERMEDIATE COMMIT + CHECKPOINT after ~{updated_so_far:,} address updates — memory/WAL reclaimed")
            return 0                                        # reset the counter

        except Exception as e:
            # Log connection state for debugging
            current_thread_id = threading.get_ident()
            log_warning(f"Intermediate commit/checkpoint failed on thread {current_thread_id} (continuing anyway): {e}")

            # Log recent queries from duckdb_logs
            try:
                logs = conn.execute("""
                    SELECT timestamp, message, log_level, type
                    FROM duckdb_logs()
                    WHERE type = 'QueryLog'
                    ORDER BY timestamp DESC
                    LIMIT 10
                """).fetchall()
                log_warning("Recent queries from duckdb_logs:")
                for log_entry in logs:
                    log_warning(str(log_entry))
            except Exception as log_e:
                log_warning(f"Failed to query duckdb_logs: {log_e}")

            try:
                conn.execute("BEGIN TRANSACTION")       # at least try to restart the tx
            except:
                pass
        return 0

    @classmethod
    def merge(cls, contexts: List['PendingDatabaseContext']) -> 'PendingDatabaseContext':
        """
        Merge multiple PendingDatabaseContext objects into a single context.

        This method combines all objects from multiple contexts into one,
        following the architecture's requirement for efficient batch processing.
        Used by parallel producers to consolidate results before consumer execution.

        Args:
            contexts: List of PendingDatabaseContext objects to merge

        Returns:
            Single PendingDatabaseContext containing all objects from input contexts

        THREADING: Safe for merging results from parallel producer threads
        PERFORMANCE: Enables batch processing of multiple XML files at once
        """
        print(f"###DEBUG### PDC_MERGE: Merging {len(contexts)} contexts")
        if not contexts:
            print("###DEBUG### PDC_MERGE: No contexts to merge, returning empty")
            return cls()
        if len(contexts) == 1:
            return contexts[0] # already done!

        # Use the first context as the base
        merged = cls(xml_id=contexts[0].xml_id, xml_content=contexts[0].xml_content)

        # Collect all error messages from contexts that have them
        error_messages = [ctx.error_message for ctx in contexts if ctx.error_message]
        if error_messages:
            merged.error_message = '\n'.join(error_messages)
        else:
            merged.error_message = None

        # Merge all objects from all contexts
        for context in contexts:
            # Merge all objects
            for obj_type, objects in context.objects.items():
                merged.objects[obj_type].extend(objects)

            # Merge updates
            merged._updates.extend(context._updates)

        # Consolidate operations - group GENERIC_UPDATE operations for batching
        from database_operations import DatabaseOperationType
        generic_update_groups = {}  # Key: (table, set_clause, where_clause), Value: list of param sets

        for context in contexts:
            # Sum estimated updates
            merged.estimated_updates += context.estimated_updates

            for operation in context.operations:
                if operation.operation_type == DatabaseOperationType.GENERIC_UPDATE:
                    data = operation.data
                    where_clause = data.get('where_clause')
                    if where_clause:
                        # WHERE clause update - group by signature for batching
                        table = data.get('table')
                        set_clause = data.get('set_clause')
                        params = data.get('params', [])
                        key = (table, set_clause, where_clause)
                        if key not in generic_update_groups:
                            generic_update_groups[key] = []
                        generic_update_groups[key].append(params)
                    else:
                        # Traditional bulk update - add as-is
                        merged.operations.append(operation)
                else:
                    # Non-GENERIC_UPDATE operation - add as-is
                    merged.operations.append(operation)

        # Create consolidated GENERIC_UPDATE operations
        for (table, set_clause, where_clause), param_sets in generic_update_groups.items():
            if len(param_sets) == 1:
                # Single operation - create normal operation
                merged.operations.append(DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': table,
                        'set_clause': set_clause,
                        'where_clause': where_clause,
                        'params': param_sets[0]
                    }
                ))
            else:
                # Multiple operations with same signature - create batched operation
                merged.operations.append(DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': table,
                        'set_clause': set_clause,
                        'where_clause': where_clause,
                        'param_sets': param_sets  # Multiple parameter sets for executemany
                    }
                ))

        print(f"###DEBUG### PDC_MERGE: Merged context has {len(merged.operations)} operations, estimated_updates={merged.estimated_updates}")
        return merged

    def clear(self) -> None:
        """Clear all objects from this context"""
        self.objects = {key: [] for key in self.objects.keys()}
        self._updates = []
        self.operations = []
        self.xml_id = None
        self.xml_content = None
        self.error_message = None
        self.estimated_updates = 0