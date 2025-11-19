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

from typing import Dict, List, Any, Optional
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
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
    """

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
            'xmlfile': []   # For XML file records
        }
        self._updates: List[Dict[str, Any]] = []  # For future UPDATE operations
        self.operations: List[DatabaseOperation] = []  # For generic database operations
        self.error_message: Optional[str] = None  # Error message for failed processing

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

    def save_to_database(self, db_ops: DatabaseOperations) -> List[str]:
        """
        Execute all collected objects and operations directly.
        
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
        from logging_utils import log_info, log_error
        all_ids = []
        conn = db_ops.db_conn
        try:
            log_info("Starting transaction for batch")
            #conn.execute("BEGIN TRANSACTION") just causes problem
    
            # Execute objects by type (inserts) - use INSERT_BY_TYPE for each type
            for obj_type, obj_list in self.objects.items():
                if obj_list:
                    ids = db_ops.INSERT_BY_TYPE(obj_list, obj_type, commit_batches=True)
                    all_ids.extend(ids)
            conn.commit()
   
            log_info("Inserts completed")

            # Separate DB operations from non-DB operations (like PROGRESS_UPDATE)
            db_operations = []
            non_db_operations = []
            for operation in self.operations:
                if operation.operation_type == DatabaseOperationType.PROGRESS_UPDATE:
                    non_db_operations.append(operation)
                else:
                    db_operations.append(operation)

            # Execute DB operations before commit
            for operation in db_operations:
                self._execute_operation(db_ops, operation)

            log_info("All DB operations completed")
    
            # Execute non-DB operations after commit (e.g., progress updates)
            for operation in non_db_operations:
                self._execute_operation(db_ops, operation)

        except Exception as e:
            conn.execute("ROLLBACK")
            log_error(f"Transaction rolled back due to error: {str(e)}")
            raise
    
        return all_ids

    def _execute_operation(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """
        Execute a single database operation.

        Args:
            db_ops: DatabaseOperations instance
            operation: The operation to execute
        """
        from database_operations import DatabaseOperationType

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
        elif op_type == DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH:
            self._execute_address_deduplication_batch(db_ops, operation)
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

    def _execute_generic_update(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """Execute generic update operation"""
        data = operation.data
        table_name = data.get("table") or data.get("table_name")
        update_data = data.get("updates") or data.get("update_data", {})
        id_column = data.get("key_field") or data.get("id_column", "id")
        key_value = data.get("key_value")

        if table_name and update_data:
            # If we have a key_value, add it to the update data
            if key_value is not None:
                update_record = {**update_data, id_column: key_value}
                db_ops.bulk_update(table_name, [update_record], id_column=id_column)
            else:
                # Fallback for old format
                db_ops.bulk_update(table_name, [update_data], id_column=id_column)

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
                    from logging_utils import log_error
                    log_error(f"Failed to update addresses for canonical_address {geocoding.canonical_address}: {e}")
                    # Continue with the transaction - geocoding record update will still succeed

    def _execute_address_deduplication_batch(self, db_ops: DatabaseOperations, operation: DatabaseOperation) -> None:
        """Execute address deduplication batch operation"""
        data = operation.data
        master_address_id = data.get("master_address_id")
        child_address_ids = data.get("child_address_ids", [])

        if master_address_id and child_address_ids:
            # Use the same connection as the PDC transaction
            conn = db_ops.db_conn
            db_ops.execute_address_deduplication_batch(master_address_id, child_address_ids, commit=False, conn=conn)

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
        if not contexts:
            return cls()

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

            # Merge operations
            merged.operations.extend(context.operations)

            # Merge updates
            merged._updates.extend(context._updates)

        return merged

    def clear(self) -> None:
        """Clear all objects from this context"""
        self.objects = {key: [] for key in self.objects.keys()}
        self._updates = []
        self.operations = []
        self.xml_id = None
        self.xml_content = None
        self.error_message = None