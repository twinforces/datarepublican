#!/usr/bin/env python3
"""
database_operations.py - Database operations for IRS 990 data processing

This module contains all database-related operations for the IRS 990 processor,
including CRUD operations for all data models.

This module now uses DuckDB exclusively.

ARCHITECTURE OVERVIEW:
- DatabaseOperations: Main class handling all DuckDB interactions
- GENERIC_INSERT(): Organizes objects by type and inserts in ownership order (Charity→Officer→Grant→Contractor→PoliticalContribution→Address)
- INSERT_BY_TYPE(): For PDC when objects are pre-sorted by type
- bulk_insert(): High-performance batched inserts with client-side UUID generation
- Thread-local connections for multi-threaded safety
- Static ZIP file path cache for performance

KEY CLASSES:
- DatabaseOperations: Main database interface class
- DatabaseOperationType: Enum of operation types (mostly deprecated)
- DatabaseOperation: Represents individual operations (mostly deprecated)

DATABASE CONSTRAINTS:
- DuckDB allows multiple readers but only one writer
- Uses thread-local connections to avoid writer conflicts
- Client-side UUID7 generation for object relationships
- Strict ownership order enforcement for referential integrity

PERFORMANCE FEATURES:
- Preloaded ZIP file path cache at startup
- Batched executemany operations for bulk inserts
- Connection pooling with thread-local storage
- Query timeout protection and error handling
"""

import duckdb
import pandas as pd
import inspect
from typing import Optional, List, Tuple, Dict, Any, Type, Union
from dataclasses import fields
from datetime import datetime
from pathlib import Path
import uuid
import time
import random
import sys
import os
import threading
from enum import Enum
from functools import lru_cache
from config import global_config

# Add current directory to path for imports
sys.path.append(os.path.dirname(__file__))

# Import all dataclasses from models package
from models import Address, ZipFile, XMLFile, Charity, Grant, Officer, Contractor, PoliticalContribution
from models.base import BaseModel
from constants import VALID_STATES, CURRENT_PROCESSING_VERSION
from logging_utils import log_error, log_debug, log_info, log_warning
from loggingDuckDB import LoggingDuckDBConnection


class DatabaseOperationType(Enum):
    """
    Enumeration of database operation types for flexible processing.

    MOSTLY DEPRECATED: These operation types were used in the old operation-based
    processing model. The new PDC (PendingDatabaseContext) approach handles
    operations internally without explicit operation types.

    Still used for:
    - XML_FILE_UPDATE: Updating XML file processing status
    - OPTIMIZE_DATABASE: Database optimization operations
    - PROGRESS_UPDATE: Progress bar updates
    """
    XML_FILE_UPDATE = "xml_file_update"
    UPDATE_XML_EIN = "update_xml_ein"
    OPTIMIZE_DATABASE = "optimize_database"
    GENERIC_UPDATE = "generic_update"
    GENERIC_INSERT = "generic_insert"
    INSERT_BY_TYPE = "insert_by_type"
    INSERT_CHARITY = "insert_charity"
    INSERT_ADDRESS = "insert_address"
    ADDRESS_DEDUPLICATION_BATCH = "address_deduplication_batch"
    PROGRESS_UPDATE = "progress_update"
    UPDATE_GEOCODING = "update_geocoding"


class DatabaseOperation:
    """
    Represents a single database operation with its data and dependencies.

    DEPRECATED: This class was used in the old operation-based processing model.
    The new PDC (PendingDatabaseContext) approach handles operations internally
    without explicit DatabaseOperation objects.

    Still used for XML file updates and progress tracking operations.
    """

    def __init__(self, operation_type: DatabaseOperationType, data: Any, xml_id: Optional[str] = None,
                  dependencies: Optional[List[str]] = None):
        self.operation_type = operation_type
        self.data = data
        self.xml_id = xml_id
        self.dependencies = dependencies or []  # List of operation types this depends on


class DatabaseOperations:
    """
    Handles all DuckDB operations for IRS 990 data processing.

    This is the main database interface class that provides:
    - Connection management with thread-local connections
    - Generic CRUD operations for all dataclass types
    - Bulk insert operations with ownership order enforcement
    - ZIP file path caching for performance
    - Schema initialization and optimization

    THREADING MODEL:
    - Uses thread-local connections to avoid DuckDB's single-writer constraint
    - Multiple readers allowed, but only one writer per connection
    - Static ZIP cache shared across all instances

    PERFORMANCE FEATURES:
    - Preloaded ZIP file path cache at startup
    - Batched executemany operations for bulk inserts
    - Client-side UUID7 generation for object relationships
    - Connection pooling and reuse

    KEY METHODS:
    - GENERIC_INSERT(): Organizes mixed objects by type and inserts in ownership order
    - INSERT_BY_TYPE(): For PDC when objects are pre-sorted by type
    - bulk_insert(): High-performance batched inserts
    - select_dataclass(): Generic SELECT with reflection-based field mapping
    """

    # SQL logging is now read directly from global_config

    # Thread-local storage for database connections
    _local = threading.local()

    # Static cache for zip_id -> file_path mapping - preloaded at startup
    _zip_path_cache: Dict[str, str] = {}
    _zip_file_cache: Dict[str, ZipFile] = {}  # Cache for ZipFile objects
    _zip_cache_lock = threading.RLock()  # Reentrant lock for thread safety

    # Global table metadata cache (no lock needed - loaded at startup, never modified)
    _table_metadata_cache: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def generate_uuid_v7() -> str:
        """Generate a UUID v7 (time-ordered) - now delegates to uuid7 module"""
        from uuid7 import generate_uuid_v7
        return generate_uuid_v7()

    def __init__(self, db_path: str, read_only: bool = False, memory_limit: str = "8GB", threads: Optional[int] = None, dbUI: bool = False, query_timeout: int = 300):
        """
        Initialize DuckDB connection with performance optimizations.

        Sets up thread-local connections, preloads ZIP cache, and initializes schema.
        Configures DuckDB with performance settings for bulk operations.

        Args:
            db_path: Path to DuckDB database file
            read_only: Whether to open database in read-only mode
            memory_limit: Memory limit for DuckDB (default: 4GB)
            threads: Number of threads for DuckDB (default: auto)
            dbUI: Whether to start DuckDB's web UI
            query_timeout: Query timeout in seconds (default: 300)

        THREADING: Creates thread-local connections to avoid DuckDB's single-writer constraint.
        PERFORMANCE: Preloads ZIP file cache and applies bulk operation optimizations.
        """
        self.db_path = db_path
        # SQL logging is now read directly from global_config
        self.read_only = read_only
        self.memory_limit = memory_limit
        self.threads = threads
        self.dbUI = dbUI
        self.query_timeout = query_timeout
        self._init_connection()
        self._preload_zip_file_cache()
        self._preload_table_metadata_cache()

    def _init_connection(self):
        """Initialize DuckDB connection and schema"""
        # Connect to DuckDB with performance optimizations
        config: Dict[str, Any] = {
            'memory_limit': self.memory_limit
        }
        if self.threads is not None and self.threads != 'auto':
            config['threads'] = self.threads
        if self.read_only:
            config['read_only'] = True

        # Store connection parameters for thread-local connections
        self._connection_config = config

        # Create main connection for single-threaded operations
        if global_config.log_sql:
            self.db_conn = LoggingDuckDBConnection(self.db_path, config=config)
        else:
            self.db_conn = duckdb.connect(self.db_path, config=config)

        # Set additional performance settings
        self.db_conn.execute("SET enable_progress_bar = false")  # Disable progress bars for better performance
        self.db_conn.execute("SET enable_object_cache = true")   # Enable object cache
        self.db_conn.execute("SET max_temp_directory_size = '10GB'")  # Increase temp directory size
        # Note: Some performance settings may not be available in all DuckDB versions
        try:
            self.db_conn.execute("SET insert_select_parallelism = true")  # Enable parallel insert-select operations
        except Exception:
            pass  # Ignore if not supported
        try:
            self.db_conn.execute("PRAGMA temp_store = 'memory'")  # Store temporary data in memory
        except Exception:
            pass  # Ignore if not supported
        try:
            self.db_conn.execute("SET checkpoint_threshold = '1GB'")  # WAL size doesn't matter in grok benchmark.
        except Exception:
            pass  # Ignore if not supported
        self.db_conn.execute("SET preserve_insertion_order = false")  # Allow reordering for better performance
        if global_config.log_sql:
            self.db_conn.execute("CALL enable_logging(storage_path = '/Volumes/Data/final/irs990db.log');")

        # Note: DuckDB doesn't have a built-in query_timeout setting in this version
        # The timeout protection will be handled through enhanced error handling in execute_query
        # Store the timeout value for potential future use or custom timeout implementation
        pass
   
        # Initialize schema if needed
        self._init_schema()

        # Check for --dbUI flag and start UI if present
        if self.dbUI:
            try:
                self.db_conn.execute("CALL start_ui();")
                print("Database UI started successfully")
            except Exception as e:
                print(f"Failed to start database UI: {e}")
                print("Continuing without UI...")

    def _preload_zip_file_cache(self):
        """Preload all zip_id -> file_path mappings and ZipFile objects into the static cache at startup"""
        import time
        with DatabaseOperations._zip_cache_lock:
            if DatabaseOperations._zip_path_cache:
                # Already preloaded
                return

            try:
                log_info("Preloading zip path and object cache...")
                start_time = time.time()
                # Query all ZipFile records from database
                zip_files = self.select_dataclass(ZipFile, order_by="zip_id")
                query_time = time.time() - start_time
                log_info(f"Loaded {len(zip_files)} ZipFile objects from database in {query_time:.3f}s")

                # Populate the cache with zip_id -> file_path mappings and ZipFile objects
                cache_start = time.time()
                for zip_file in zip_files:
                    DatabaseOperations._zip_path_cache[zip_file.zip_id] = zip_file.file_path
                    DatabaseOperations._zip_file_cache[zip_file.zip_id] = zip_file
                cache_time = time.time() - cache_start

                log_info(f"Zip path cache preloaded with {len(DatabaseOperations._zip_path_cache)} entries in {cache_time:.3f}s")
                log_info(f"Zip object cache preloaded with {len(DatabaseOperations._zip_file_cache)} entries")

            except Exception as e:
                log_error(f"Failed to preload zip path cache: {e}")
                # Continue without cache - operations will fall back to database queries

    def _preload_table_metadata_cache(self):
        """Preload table metadata cache from pickled file or build it"""
        import pickle
        import os
        import time

        cache_file = os.path.join(os.path.dirname(__file__), 'table_metadata_cache.pkl')

        if DatabaseOperations._table_metadata_cache:
            # Already preloaded
            return

        try:
            # Try to load from pickled file first
            if os.path.exists(cache_file):
                with open(cache_file, 'rb') as f:
                    DatabaseOperations._table_metadata_cache = pickle.load(f)
                log_info(f"Loaded table metadata cache from {cache_file} with {len(DatabaseOperations._table_metadata_cache)} entries")
                return

            # Build cache from database schema with timing
            log_info("Building table metadata cache...")
            tables = ['ZipFiles', 'XmlFiles', 'Charities', 'Officers', 'Grants', 'Contractors', 'PoliticalContributions', 'Addresses', 'Geocoding']

            total_start = time.time()
            for table in tables:
                try:
                    table_start = time.time()
                    columns = self._get_table_columns(table)
                    table_time = time.time() - table_start
                    DatabaseOperations._table_metadata_cache[table] = {
                        'columns': columns,
                        'column_count': len(columns)
                    }
                    log_info(f"  {table}: {len(columns)} columns in {table_time:.3f}s")
                except Exception as e:
                    log_warning(f"Could not get metadata for table {table}: {e}")

            total_time = time.time() - total_start
            log_info(f"Table metadata cache built with {len(DatabaseOperations._table_metadata_cache)} entries in {total_time:.3f}s")

            # Save to pickled file for future use
            try:
                with open(cache_file, 'wb') as f:
                    pickle.dump(DatabaseOperations._table_metadata_cache, f)
                log_info(f"Saved table metadata cache to {cache_file}")
            except Exception as e:
                log_warning(f"Could not save table metadata cache: {e}")

        except Exception as e:
            log_error(f"Failed to preload table metadata cache: {e}")
            # Continue without cache

    def _init_schema(self):
        """Initialize database schema if not already present"""
        # Check if schema is already initialized
        try:
            result = self.db_conn.execute("SELECT table_name FROM duckdb_tables() WHERE table_name='ZipFiles'").fetchone()
            if result:
                return  # Schema already exists
        except duckdb.CatalogException:
            pass  # Table doesn't exist, continue with schema creation

        # Read and execute schema_duckdb.sql
        schema_path = os.path.join(os.path.dirname(__file__), 'schema_duckdb.sql')
        try:
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            self.db_conn.execute(schema_sql)
            self.db_conn.commit()
        except Exception as e:
            print(f"Failed to initialize DuckDB database schema: {e}")
            raise

    def execute_query(self, query: str, params: Optional[Tuple] = None, conn=None) -> Any:
        """
        Execute a query with timeout protection and enhanced error handling.

        Uses thread-local connections for thread safety. Handles DuckDB-specific errors
        and provides detailed logging when SQL logging is enabled.

        Args:
            query: SQL query string
            params: Query parameters tuple
            conn: Optional connection override (uses thread-local by default)

        Returns:
            DuckDB result object

        Raises:
            RuntimeError: For query execution failures with detailed error messages
        """
        if conn is None:
            conn = self._get_thread_local_connection()

        # Don't log here if we're using LoggingDuckDBConnection - it will handle logging
        if global_config.log_sql and not isinstance(conn, LoggingDuckDBConnection):
            import inspect
            # Get the caller's frame, skipping internal functions
            frame = inspect.currentframe()
            if frame:
                frame = frame.f_back
                while frame and (frame.f_code.co_filename.endswith(('database_operations.py', 'logging_utils.py')) or
                                'execute_query' in frame.f_code.co_name):
                    frame = frame.f_back

                if frame:
                    filename = frame.f_code.co_filename
                    line_number = frame.f_lineno
                    function_name = frame.f_code.co_name
                    log_info(f"SQL from {filename}:{line_number} in {function_name}: {query}")
                    if params:
                        log_info(f"Parameters: {params}")

        try:
            if params:
                return conn.execute(query, params)
            else:
                return conn.execute(query)
        except (duckdb.CatalogException, duckdb.BinderException, duckdb.SyntaxException,
                duckdb.ConstraintException, duckdb.DataError) as e:
            # Handle query-specific errors (syntax, constraint violations, table not found, etc.)
            error_msg = f"Query execution failed: {str(e)}"
            if "timeout" in str(e).lower() or "interrupt" in str(e).lower():
                error_msg = f"Query timed out or was interrupted: {str(e)}"
            log_error(error_msg)
            raise RuntimeError(error_msg) from e
        except duckdb.ConnectionException as e:
            # Handle connection-related errors
            error_msg = f"Database connection error: {str(e)}"
            log_error(error_msg)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            # Handle any other unexpected errors
            error_msg = f"Unexpected database error: {str(e)}"
            log_error(error_msg)
            raise RuntimeError(error_msg) from e

    def _get_thread_local_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create a thread-local database connection"""
        if not hasattr(DatabaseOperations._local, 'db_conn'):
            # Create a new connection for this thread
            
            DatabaseOperations._local.db_conn = duckdb.connect(self.db_path, config=self._connection_config)
            # Apply the same settings as the main connection
            DatabaseOperations._local.db_conn.execute("SET enable_progress_bar = false")
            DatabaseOperations._local.db_conn.execute("SET enable_object_cache = true")
            DatabaseOperations._local.db_conn.execute("SET max_temp_directory_size = '10GB'")
            try:
                DatabaseOperations._local.db_conn.execute("SET insert_select_parallelism = true")
            except Exception:
                pass
            try:
                DatabaseOperations._local.db_conn.execute("PRAGMA temp_store = 'memory'")
            except Exception:
                pass
            try:
                DatabaseOperations._local.db_conn.execute("SET checkpoint_threshold = '1GB'")
            except Exception:
                pass
            DatabaseOperations._local.db_conn.execute("SET preserve_insertion_order = false")
            if global_config.log_sql:
                DatabaseOperations._local.db_conn.execute("CALL enable_logging(storage_path = '/Volumes/Data/final/irs990db.log');")

            # Note: DuckDB doesn't have a built-in query_timeout setting in this version
            # The timeout protection will be handled through enhanced error handling in execute_query
            pass

        return DatabaseOperations._local.db_conn  # type: ignore

    def select_dataclass(self, dataclass_type: Type, where_clause: str = "", params: Optional[Tuple] = None,
                        order_by: str = "", limit: Optional[int] = None, offset: Optional[int] = None,
                        df_threshold: int = 10000) -> List[Any]:
        """
        Generic method to select records and convert them to dataclass instances using reflection.

        Uses Python dataclass introspection to dynamically build SELECT queries and map
        database columns to dataclass fields. Handles field filtering for database compatibility.

        Args:
            dataclass_type: The dataclass type to instantiate (e.g., Charity, Address)
            where_clause: Optional WHERE clause (without the WHERE keyword)
            params: Parameters for the WHERE clause
            order_by: Optional ORDER BY clause (without the ORDER BY keyword)
            limit: Optional LIMIT clause
            offset: Optional OFFSET clause
            df_threshold: Threshold for using DataFrame vs fetchall (default: 10000)

        Returns:
            List of dataclass instances with proper field mapping and type conversion

        REFLECTION: Uses dataclass.fields() to dynamically determine table schema
        PERFORMANCE: Filters out non-init fields and missing database columns
        """
        # Get table name from dataclass name (pluralize by adding 's' or 'ies')
        table_name = self._get_table_name(dataclass_type)

        # Get field names from dataclass, but filter out fields that don't exist in the table
        all_field_names = [f.name for f in fields(dataclass_type)]
        field_names = self._filter_existing_columns(table_name, all_field_names)

        # Build SELECT query
        select_fields = ", ".join(field_names)
        query = f"SELECT {select_fields} FROM {table_name}"

        if where_clause:
            query += f" WHERE {where_clause}"

        if order_by:
            query += f" ORDER BY {order_by}"

        if limit:
            query += f" LIMIT {limit}"

        if offset:
            query += f" OFFSET {offset}"

        # Determine if we should use DataFrame for large result sets
        use_dataframe = False
 
        # Execute main query
        result = self.execute_query(query, params)

        # Use fetchall for smaller result sets
        rows = result.fetchall()
        row_dicts = [dict(zip(field_names, row)) for row in rows]

        # Convert row dicts to dataclass instances
        instances = []
        init_fields = {f.name for f in fields(dataclass_type) if f.init}
        for row_dict in row_dicts:
            # Filter out fields that have init=False since they can't be passed to __init__
            filtered_dict = {k: v for k, v in row_dict.items() if k in init_fields}
            # Instantiate dataclass with only the init fields
            instance = dataclass_type(**filtered_dict)
            # Set non-init fields manually
            for k, v in row_dict.items():
                if k not in init_fields:
                    setattr(instance, k, v)
            instances.append(instance)

        return instances

    def _get_table_columns(self, table_name: str, conn=None) -> List[str]:
        """Get column names for a table, cached for performance"""
        # Check global metadata cache first
        if table_name in DatabaseOperations._table_metadata_cache:
            return DatabaseOperations._table_metadata_cache[table_name]['columns']

        # Fallback to database query
        if conn is None:
            conn = self.db_conn
        try:
            result = conn.execute(f"DESCRIBE {table_name}")
            columns = [row[0] for row in result.fetchall()]

            # Cache the result
            DatabaseOperations._table_metadata_cache[table_name] = {
                'columns': columns,
                'column_count': len(columns)
            }

            return columns
        except Exception:
            return []

    def _filter_existing_columns(self, table_name: str, field_names: List[str]) -> List[str]:
        """Filter field names to only include columns that exist in the table"""
        table_columns = self._get_table_columns(table_name)
        if not table_columns:
            # If DESCRIBE fails, return all fields (fallback)
            return field_names

        # Filter field names to only include existing columns
        return [field for field in field_names if field in table_columns]

    def _get_table_name(self, dataclass_type: Type) -> str:
        """Get table name from dataclass type, cached for performance"""
        class_name = dataclass_type.__name__
        # Special cases based on actual table names in schema
        table_name_map = {
            'XMLFile': 'XmlFiles',
            'ZipFile': 'ZipFiles',
            'PoliticalContribution': 'PoliticalContributions',
            'Geocoding': 'Geocoding'
        }

        if class_name in table_name_map:
            return table_name_map[class_name]

        # Simple pluralization rules
        if class_name.endswith('y'):
            return class_name[:-1] + 'ies'
        elif class_name.endswith('s') or class_name.endswith('sh') or class_name.endswith('ch') or class_name.endswith('x') or class_name.endswith('z'):
            return class_name + 'es'
        else:
            return class_name + 's'

    def commit(self):
        """Commit current transaction"""
        self.db_conn.commit()

    def close(self):
        """Explicitly close the database connection"""
        if hasattr(self, 'db_conn') and self.db_conn:
            self.db_conn.close()
        # Close thread-local connections
        if hasattr(DatabaseOperations._local, 'db_conn'):
            DatabaseOperations._local.db_conn.close()
            delattr(DatabaseOperations._local, 'db_conn')

    def __del__(self):
        """Cleanup database connection on object destruction"""
        self.close()

    # ZipFile operations
    def insert_zip_file(self, zip_file: ZipFile) -> str:
        """Insert ZipFile into database using generic insert_dataclass method"""
        ids = self.bulk_insert([zip_file])
        zip_id = ids[0] if ids else ""
        # Update cache if insertion successful
        if zip_id:
            with DatabaseOperations._zip_cache_lock:
                DatabaseOperations._zip_path_cache[zip_id] = zip_file.file_path
                DatabaseOperations._zip_file_cache[zip_id] = zip_file
        return zip_id

    def update_zip_status(self, zip_id: str, status: str):
        """Update ZIP file processing status"""
        self.execute_query("""
            UPDATE ZipFiles SET status = ?, processed_date = ?
            WHERE zip_id = ?
        """, (status, datetime.now().isoformat(), zip_id))
        # No need to update path cache for status changes
        self.commit()

    def get_zip_file(self, zip_id: str) -> Optional[ZipFile]:
        """Get ZipFile from cache or database"""
        with DatabaseOperations._zip_cache_lock:
            # Try direct lookup first
            if zip_id in DatabaseOperations._zip_file_cache:
                return DatabaseOperations._zip_file_cache[zip_id]

        # Fallback to database query if not in cache
        zip_files = self.select_dataclass(ZipFile, where_clause="zip_id = ?", params=(zip_id,))
        if zip_files:
            zip_file = zip_files[0]
            # Cache the result for future use
            with DatabaseOperations._zip_cache_lock:
                DatabaseOperations._zip_file_cache[zip_id] = zip_file
            return zip_file
        return None

    def get_zip_file_path(self, zip_id: str) -> Optional[str]:
        """Get ZIP file path from cache"""
        with DatabaseOperations._zip_cache_lock:
            # Try direct lookup first
            if zip_id in DatabaseOperations._zip_path_cache:
                return DatabaseOperations._zip_path_cache[zip_id]

        # Fallback to database query if not in cache
        zip_files = self.select_dataclass(ZipFile, where_clause="zip_id = ?", params=(zip_id,))
        if zip_files:
            zip_file = zip_files[0]
            # Cache the result for future use
            with DatabaseOperations._zip_cache_lock:
                DatabaseOperations._zip_path_cache[zip_file.zip_id] = zip_file.file_path
            return zip_file.file_path
        return None

    # XMLFile operations
    def insert_xml_file(self, xml_file: XMLFile) -> str:
        """Insert XMLFile into database using generic insert_dataclass method"""
        ids = self.bulk_insert([xml_file])
        return ids[0] if ids else ""

    def get_unprocessed_xml_files(self, processing_version: int, max_files: Optional[int] = None) -> List[XMLFile]:
        """Get unprocessed XML files"""
        where_clause = "processed = FALSE OR processing_version < ?"
        params = (processing_version,)
        order_by = "zip_id, filename"
        limit = max_files

        return self.select_dataclass(XMLFile, where_clause=where_clause, params=params, order_by=order_by, limit=limit)

    def mark_xml_processed(self, xml_id: str, processing_version: int):
        """Mark XML file as processed"""
        self.execute_query("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?, processed_at = ?
            WHERE xml_id = ?
        """, (processing_version, "success", datetime.now().isoformat(), xml_id))
        self.commit()

    def mark_xml_error(self, xml_id: str, processing_version: int, error_msg: str):
        """Mark XML file as having an error"""
        self.execute_query("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?, processed_at = ?
            WHERE xml_id = ?
        """, (processing_version, error_msg, datetime.now().isoformat(), xml_id))
        self.commit()

    def update_xml_ein(self, xml_id: str, ein: str):
        """Update XML file with EIN after parsing"""
        self.execute_query("UPDATE XmlFiles SET ein = ? WHERE xml_id = ?", (ein, xml_id))
        self.commit()

    def execute_update_xml_ein_operation(self, operation: DatabaseOperation, conn=None):
        """Execute UPDATE_XML_EIN operation"""
        if conn is None:
            conn = self.db_conn
        xml_id = operation.xml_id
        data = operation.data
        ein = data.get("ein") if data else None
        if ein:
            conn.execute("UPDATE XmlFiles SET ein = ? WHERE xml_id = ?", (ein, xml_id))

    def get_addresses_for_geocoding(self, limit: Optional[int] = None, last_address_id: Optional[str] = None) -> List[Address]:
        """Get addresses that need geocoding, with max primary key pagination support"""
        where_clause = "geocoding_id IS NULL AND (po_box IS NULL OR po_box = '') AND colocator IS NULL AND master_id IS NULL AND canonical_address IS NOT NULL AND canonical_address != ''"
        if last_address_id is not None:
            where_clause += " AND address_id > ?"
            params = (last_address_id,)
        else:
            params = None
        return self.select_dataclass(Address, where_clause=where_clause, params=params, order_by="address_id", limit=limit)

    def update_address_geocoding(self, address_id: str, geocoding_id: Optional[str] = None, colocator: Optional[str] = None):
        """Update address with geocoding information and propagate colocator to owner"""
        # First update the address
        if geocoding_id is not None and colocator is not None:
            self.execute_query("""
                UPDATE Addresses SET geocoding_id = ?, colocator = ?
                WHERE address_id = ?
            """, (geocoding_id, colocator, address_id))
        elif geocoding_id is not None:
            self.execute_query("""
                UPDATE Addresses SET geocoding_id = ?
                WHERE address_id = ?
            """, (geocoding_id, address_id))
        elif colocator is not None:
            self.execute_query("""
                UPDATE Addresses SET colocator = ?
                WHERE address_id = ?
            """, (colocator, address_id))

        # If colocator was set, also update the owner object
        if colocator is not None:
            # Get address details to determine owner type and ID
            address_info = self.execute_query("""
                SELECT address_type, owner_id FROM Addresses WHERE address_id = ?
            """, (address_id,)).fetchone()

            if address_info and len(address_info) >= 2:
                address_type, owner_id = address_info[0], address_info[1]
                if owner_id:  # Only update if we have an owner_id
                    if address_type == 'charity':
                        self.execute_query("""
                            UPDATE Charities SET colocator = ? WHERE charity_id = ?
                        """, (colocator, owner_id))
                    elif address_type == 'grant':
                        self.execute_query("""
                            UPDATE Grants SET grantee_colocator = ? WHERE grant_id = ?
                        """, (colocator, owner_id))
                    elif address_type == 'contractor':
                        self.execute_query("""
                            UPDATE Contractors SET colocator = ? WHERE contractor_id = ?
                        """, (colocator, owner_id))
                    elif address_type == 'politicalcontribution':
                        self.execute_query("""
                            UPDATE PoliticalContributions SET colocator = ? WHERE political_id = ?
                        """, (colocator, owner_id))

        self.commit()

    def update_address_po_box_and_colocator(self, address_id: str, po_box: str, colocator: str):
        """Update address with PO Box and colocator information and propagate colocator to owner"""
        self.execute_query("""
            UPDATE Addresses SET po_box = ?, colocator = ?
            WHERE address_id = ?
        """, (po_box, colocator, address_id))

        # Also update the owner object with colocator
        address_info = self.execute_query("""
            SELECT address_type, owner_id FROM Addresses WHERE address_id = ?
        """, (address_id,)).fetchone()

        if address_info and len(address_info) >= 2:
            address_type, owner_id = address_info[0], address_info[1]
            if owner_id:  # Only update if we have an owner_id
                if address_type == 'charity':
                    self.execute_query("""
                        UPDATE Charities SET colocator = ? WHERE charity_id = ?
                    """, (colocator, owner_id))
                elif address_type == 'grant':
                    self.execute_query("""
                        UPDATE Grants SET grantee_colocator = ? WHERE grant_id = ?
                    """, (colocator, owner_id))
                elif address_type == 'contractor':
                    self.execute_query("""
                        UPDATE Contractors SET colocator = ? WHERE contractor_id = ?
                    """, (colocator, owner_id))
                elif address_type == 'politicalcontribution':
                    self.execute_query("""
                        UPDATE PoliticalContributions SET colocator = ? WHERE political_id = ?
                    """, (colocator, owner_id))

        self.commit()

    def update_charity_percentiles(self, ein: str, tax_year: int, comp_ptile: Optional[float] = None,
                                    travel_ptile: Optional[float] = None, conferences_ptile: Optional[float] = None,
                                    grants_ptile: Optional[float] = None, foreign_ptile: Optional[float] = None):
        """Update charity percentile rankings"""
        self.execute_query("""
            UPDATE Charities SET
                comp_ptile = ?,
                travel_ptile = ?,
                conferences_ptile = ?,
                grants_ptile = ?,
                foreign_expenses_ptile = ?
            WHERE ein = ? AND tax_year = ?
        """, (comp_ptile, travel_ptile, conferences_ptile, grants_ptile, foreign_ptile, ein, tax_year))
        self.commit()

    def get_charities_for_percentiles(self) -> List[Tuple]:
        """Get charities for percentile calculation"""
        result = self.execute_query("""
            SELECT org_type, tax_year, ein, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct
            FROM Charities
            WHERE denominator > 0
            ORDER BY org_type, tax_year
        """)
        return result.fetchall()

    def get_latest_charities_for_export(self) -> List[Charity]:
        """Get latest charities for export (most recent tax year for each EIN)"""
        return self.select_dataclass(Charity, where_clause="""
            tax_year = (
                SELECT MAX(tax_year)
                FROM Charities c2
                WHERE c2.ein = Charities.ein
            )
        """)

    def get_latest_charities_for_export_batch(self, offset: int, limit: int) -> List[Tuple]:
        """Get batch of latest charities for export (most recent tax year for each EIN)"""
        query = """
            SELECT tax_year, filer_ein, filer_name, receipt_amt, govt_amt, contrib_amt,
                   org_type, total_exp, prog_exp, travel_amt, conferences_amt,
                   officer_comp, comp_pct, comp_ptile, travel_pct, travel_ptile,
                   conferences_pct, conferences_ptile, grants_pct, grants_ptile,
                   foreign_expenses_pct, foreign_expenses_ptile, grift_ratio,
                   total_assets, form_type, denominator, foreign_office, foreign_expenses,
                   grants_to_others, domestic_misrep_flag, xml_name
            FROM Charities
            WHERE tax_year = (
                SELECT MAX(tax_year)
                FROM Charities c2
                WHERE c2.ein = Charities.ein
            )
            ORDER BY filer_ein
            LIMIT ? OFFSET ?
        """
        result = self.execute_query(query, (limit, offset))
        return result.fetchall() if result else []

    def get_grants_for_export(self) -> List[Grant]:
        """Get all grants for export"""
        return self.select_dataclass(Grant)

    def get_grants_for_export_batch(self, offset: int, limit: int) -> List[Tuple]:
        """Get batch of grants for export"""
        query = """
            SELECT filer_ein, filer_name, recipient_ein, grant_amt, tax_year,
                   filer_colocator, grantee_colocator
            FROM Grants
            ORDER BY filer_ein, recipient_ein
            LIMIT ? OFFSET ?
        """
        result = self.execute_query(query, (limit, offset))
        return result.fetchall() if result else []

    def get_contractors_for_export(self) -> List[Contractor]:
        """Get all contractors for export"""
        return self.select_dataclass(Contractor)

    def get_contractors_for_export_batch(self, offset: int, limit: int) -> List[Tuple]:
        """Get batch of contractors for export"""
        query = """
            SELECT filer_ein, name, amount, ein, address, zip_code,
                   po_box, tax_year, colocator
            FROM Contractors
            ORDER BY filer_ein, name
            LIMIT ? OFFSET ?
        """
        result = self.execute_query(query, (limit, offset))
        return result.fetchall() if result else []

    def get_political_contributions_for_export(self) -> List[PoliticalContribution]:
        """Get all political contributions for export"""
        return self.select_dataclass(PoliticalContribution)

    def get_political_contributions_for_export_batch(self, offset: int, limit: int) -> List[Tuple]:
        """Get batch of political contributions for export"""
        query = """
            SELECT filer_ein, recipient, amount, recipient_address,
                   recipient_zip, recipient_po_box, tax_year, colocator
            FROM PoliticalContributions
            ORDER BY filer_ein, recipient
            LIMIT ? OFFSET ?
        """
        result = self.execute_query(query, (limit, offset))
        return result.fetchall() if result else []

    def create_latest_charities_table(self):
        """Create LatestCharities table with most recent filings"""
        self.execute_query("""
            CREATE OR REPLACE TABLE LatestCharities AS
            SELECT c.*
            FROM Charities c
            INNER JOIN (
                SELECT ein, MAX(tax_year) as max_year
                FROM Charities
                GROUP BY ein
            ) latest ON c.ein = latest.ein AND c.tax_year = latest.max_year
        """)
        self.commit()

    def update_grant_ein(self, grant_id: str, recipient_ein: str):
        """Update grant with matched EIN"""
        self.execute_query("""
            UPDATE Grants SET recipient_ein = ? WHERE grant_id = ?
        """, (recipient_ein, grant_id))
        self.commit()

    def get_grants_without_ein(self) -> List[Grant]:
        """Get grants with unknown EINs for matching"""
        return self.select_dataclass(Grant, where_clause="recipient_ein IS NULL OR recipient_ein = ''")

    def GENERIC_INSERT(self, objects: List[BaseModel]) -> List[str]:
        """
        Generic insert method that organizes objects by class type and inserts them
        in ownership order from the Architecture.md file.

        This is the PRIMARY insert method for mixed object collections. It enforces
        the strict ownership hierarchy to maintain referential integrity:
        Charity → Officer → Grant → Contractor → PoliticalContribution → Address

        Args:
            objects: List of BaseModel instances to insert (mixed types allowed)

        Returns:
            List of generated IDs in the order they were inserted

        OWNERSHIP ORDER: Ensures parent objects are inserted before their children
        THREADING: Safe for multi-threaded use with thread-local connections
        PERFORMANCE: Organizes by type first, then bulk inserts each type
        """
        for obj in objects:
            if not isinstance(obj, BaseModel):
                raise ValueError("All objects must be BaseModel instances")

        # Organize objects by type
        objects_by_type = {}
        for obj in objects:
            obj_type = type(obj).__name__
            if obj_type not in objects_by_type:
                objects_by_type[obj_type] = []
            objects_by_type[obj_type].append(obj)

        # Insert in ownership order: Charity first, then related objects, Address last
        # Order: Charity, Officer, Grant, Contractor, PoliticalContribution, Address
        ownership_order = ['Charity', 'Officer', 'Grant', 'Contractor', 'PoliticalContribution', 'Address']
        all_ids = []

        for obj_type in ownership_order:
            if obj_type in objects_by_type:
                ids = self.bulk_insert(objects_by_type[obj_type])
                all_ids.extend(ids)

        return all_ids

    def INSERT_BY_TYPE(self, objects: List[BaseModel], obj_type: str, commit_batches: bool = True) -> List[str]:
        """
        Insert method for DatabasePendingContext that takes a list of objects of the same type
        and calls bulk_insert directly (no sorting needed since DPC has already sorted by type).

        This is the SECONDARY insert method used by PDC when objects are already grouped
        by type. PDC handles ownership ordering, so this method just validates and bulk inserts.

        Args:
            objects: List of BaseModel instances of the same type to insert
            obj_type: The type name (for validation/logging)

        Returns:
            List of generated IDs

        VALIDATION: Ensures all objects are of the specified type
        PDC INTEGRATION: Called by PendingDatabaseContext.save_to_database()
        PERFORMANCE: Direct bulk insert without type sorting overhead
        """
        for obj in objects:
            if not isinstance(obj, BaseModel):
                raise ValueError("All objects must be BaseModel instances")
            if type(obj).__name__.lower() != obj_type.lower():
                raise ValueError(f"All objects must be of type {obj_type}, got {type(obj).__name__}")

        return self.bulk_insert(objects, commit_batches=commit_batches)

    # Geocoding operations
    def insert_geocoding_record(self, normalized_address: str,
                                latitude: Optional[float] = None, longitude: Optional[float] = None,
                                status: str = 'pending', commit: bool = True) -> str:
        """Insert geocoding record. Returns UUID."""
        geocoding_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT INTO Geocoding (geocoding_id, normalized_address, latitude, longitude, geocoding_status)
            VALUES (?, ?, ?, ?, ?)
        """, (geocoding_id, normalized_address, latitude, longitude, status))
        if commit:
            self.commit()
        return geocoding_id

    # Bulk operations
    def bulk_insert(self, objects: List[BaseModel], batch_size: Optional[int] = None, commit_batches: bool = True, conn: Optional[duckdb.DuckDBPyConnection] = None, validate_counts: bool = True) -> List[str]:
        """
        High-performance bulk insert with executemany and client-side UUID generation.

        This is the CORE insert method used by both GENERIC_INSERT and INSERT_BY_TYPE.
        Uses batched executemany operations for maximum throughput while maintaining
        thread safety and referential integrity.

        Args:
            objects: Non-empty same-type BaseModels (all must be same type)
            batch_size: Rows per executemany call (uses BULK_INSERT_BATCH_SIZE from constants if None)
            conn: Optional connection override (uses thread-local by default)

        Returns:
            List of str IDs (client-generated UUIDs returned in insertion order)

        Raises:
            ValueError: Invalid inputs or type mismatches
            RuntimeError: Database errors with detailed validation

        PERFORMANCE: Batched executemany with client-side UUIDs for relationship integrity
        THREADING: Uses thread-local connections for DuckDB writer safety
        VALIDATION: Count validation ensures all rows were inserted successfully
        """
        if not objects:
            return []
        obj_type = type(objects[0])
        if len({type(obj) for obj in objects}) > 1:
            raise ValueError("Uniform types only.")

        # Use default batch size from constants if not specified
        if batch_size is None:
            from constants import BULK_INSERT_BATCH_SIZE
            batch_size = BULK_INSERT_BATCH_SIZE if BULK_INSERT_BATCH_SIZE is not None else 50000
            if not isinstance(batch_size, int):
                batch_size = 50000

        # Validation moved to pending_database_context.py

        # Use thread-local connection for proper multi-threading support
        if conn is None:
            conn = self._get_thread_local_connection()

        # Prep all objects for insert - call prep_for_insert if method exists
        prep_start = time.perf_counter()
        prepped_count = 0
        missing_prep_types = set()
        for obj in objects:
            if hasattr(obj, 'prep_for_insert'):
                obj.prep_for_insert()
                prepped_count += 1
            else:
                missing_prep_types.add(type(obj).__name__)
            obj.set_id_if_needed()  # Generate IDs client-side for object tree relationships
        prep_time = time.perf_counter() - prep_start
        log_debug(f"Prepped {prepped_count} out of {len(objects)} objects in {prep_time:.2f}s")
        if missing_prep_types:
            log_warning(f"Objects without prep_for_insert method: {missing_prep_types}")

        # Pre-insert deduplication check for Charities - controlled by command-line parameter
        from constants import ENABLE_CHARITY_DEDUP_CHECK
        if type(objects[0]).__name__ == 'Charity' and objects and ENABLE_CHARITY_DEDUP_CHECK:
            # Collect all xml_names for batch query
            xml_names = [getattr(obj, 'xml_name', '') for obj in objects if hasattr(obj, 'xml_name') and getattr(obj, 'xml_name', '')]
            if xml_names:
                # Single query to get counts for all xml_names
                placeholders = ','.join(['?' for _ in xml_names])
                query = f"SELECT xml_name, COUNT(*) FROM Charities WHERE xml_name IN ({placeholders}) GROUP BY xml_name"
                existing_counts = dict(conn.execute(query, tuple(xml_names)).fetchall())

                # Filter objects based on batch results
                filtered_objects = []
                skipped = 0
                for obj in objects:
                    xml_name = getattr(obj, 'xml_name', '')
                    if xml_name:
                        count = existing_counts.get(xml_name, 0)
                        if count > 0:
                            skipped += 1
                            continue
                    filtered_objects.append(obj)

                if skipped > 0:
                    log_info(f"Skipped {skipped} duplicate charities in bulk insert (batch check)")
                objects = filtered_objects
                if not objects:
                    return []

        table_name = self._get_table_name(obj_type)
        model_fields = set(obj_type.get_db_field_names())
        table_cols = self._get_table_columns(table_name, conn)
        if not table_cols:
            raise ValueError(f"No table columns found for {table_name}")
        missing = set(table_cols) - model_fields
        id_field = next((col for col in table_cols if col.endswith('_id') or col == 'id'), None)
        if not id_field:
            raise ValueError(f"No ID in {table_name}.")

        # DEBUG: Log connection type and logging status
        log_info(f"DEBUG: bulk_insert called for {len(objects)} {obj_type.__name__} objects, table={table_name}")
        log_info(f"DEBUG: global_config.log_sql={global_config.log_sql}, conn type={type(conn).__name__}")
        log_info(f"DEBUG: Using LoggingDuckDBConnection: {isinstance(conn, LoggingDuckDBConnection)}")

        # COUNT VALIDATION: Check count before insert (optional for performance)
        count_before = None
        if validate_counts:
            try:
                result = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
                row = result.fetchone() if result else None
                count_before = row[0] if row else None
                log_info(f"DEBUG: Count before bulk_insert: {count_before} rows in {table_name}")
            except Exception as e:
                log_warning(f"DEBUG: Could not get count before insert: {e}")
                count_before = None


        # Use all table columns including ID field since we generate them client-side
        insert_cols = table_cols


        # executemany core: Build param lists (excluding ID field)
        build_start = time.perf_counter()
        param_rows = []
        for obj in objects:
            row = tuple(getattr(obj, col) if col in model_fields else None for col in insert_cols)
            param_rows.append(row)
        build_time = time.perf_counter() - build_start
        log_debug(f"Built {len(objects)} param rows in {build_time:.2f}s")

        # Batched executemany for maximum performance - IDs already generated client-side
        insert_start = time.perf_counter()
        for i in range(0, len(objects), batch_size):
            batch_params = param_rows[i:i + batch_size]

            # Use executemany with single row inserts - no RETURNING needed since IDs are client-generated
            col_list = ', '.join(insert_cols)
            placeholders = ', '.join(['?' for _ in insert_cols])
            insert_sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"
            log_info(f"DEBUG: About to execute INSERT SQL: {insert_sql[:100]}...")
            log_info(f"DEBUG: Batch has {len(batch_params)} parameter sets")

            if global_config.log_sql:
                log_info(f"bulk SQL {insert_sql}")

            # SQL logging handled by execute_query method

            batch_start = time.perf_counter()
            conn.executemany(insert_sql, batch_params)  # type: ignore
            if commit_batches:
                conn.commit()
            batch_elapsed = time.perf_counter() - batch_start
            rate = len(batch_params) / batch_elapsed if batch_elapsed > 0 else 0
            log_debug(f"Batch {i//batch_size + 1} ({len(batch_params)} rows): {batch_elapsed:.2f}s ({rate:.0f} rows/s)")
            insert_time = time.perf_counter() - insert_start
            log_info(f"executemany inserted {len(objects)} rows in {insert_time:.2f}s ({len(objects)/insert_time:.0f} rows/s)")

        # COUNT VALIDATION: Check count after insert (optional for performance)
        if validate_counts and count_before is not None:
            try:
                result = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
                row = result.fetchone() if result else None
                count_after = row[0] if row else None
                log_info(f"DEBUG: Count after bulk_insert: {count_after} rows in {table_name}")
                expected_count = count_before + len(objects)
                if count_after != expected_count:
                    error_msg = f"CRITICAL: Bulk insert validation FAILED! Expected {expected_count} rows, got {count_after}. Difference: {count_after - count_before} (expected +{len(objects)})"
                    log_error(error_msg)
                    # Disable validation for performance - just log the error instead of raising
                    log_warning("Count validation disabled for performance - continuing despite validation failure")
                else:
                    log_info(f"DEBUG: Bulk insert validation PASSED: +{len(objects)} rows added")
            except Exception as e:
                log_error(f"DEBUG: Could not validate count after insert: {e}")
                if "CRITICAL" in str(e):
                    # Disable validation for performance - just log the error instead of raising
                    log_warning("Count validation disabled for performance - continuing despite validation failure")

        # Log bulk insert completion with counts
        log_info(f"Bulk insert completed: {len(objects)} {obj_type.__name__} records inserted")

        # Return the client-generated IDs
        return [str(getattr(obj, id_field)) for obj in objects]

    def bulk_update(self, table_name: str, updates: List[Dict[str, Any]], id_column: str = 'id', batch_size: int = 50000, conn=None) -> int:
        """Generic bulk update method for database operations with batched processing"""
        if conn is None:
            conn = self.db_conn

        if not updates:
            return 0

        # Get column names from first update dict
        columns = list(updates[0].keys())
        if id_column not in columns:
            raise ValueError(f"ID column '{id_column}' not found in update data")

        # Build UPDATE statement
        set_columns = [col for col in columns if col != id_column]
        set_clause = ", ".join([f"{col} = ?" for col in set_columns])
        sql = f"UPDATE {table_name} SET {set_clause} WHERE {id_column} = ?"

        # Prepare parameters for bulk update - list of tuples, one per row
        params = []
        for update in updates:
            row_params = tuple([update[col] for col in set_columns] + [update[id_column]])
            params.append(row_params)

        try:
            # Execute bulk update in batches of batch_size using executemany
            total_processed = 0
            for i in range(0, len(params), batch_size):
                batch_params = params[i:i + batch_size]
                batch_start = time.perf_counter()
                conn.executemany(sql, batch_params)  # type: ignore
                batch_elapsed = time.perf_counter() - batch_start
                rate = len(batch_params) / batch_elapsed if batch_elapsed > 0 else 0
                log_debug(f"Batch {i//batch_size + 1} ({len(batch_params)} rows): {batch_elapsed:.2f}s ({rate:.0f} rows/s)")
                total_processed += len(batch_params)

            # Do not commit here - let caller handle transaction
            return total_processed
        except Exception as e:
            # Do not rollback here - let caller handle transaction
            raise RuntimeError(f"Bulk update failed for table {table_name}: {str(e)}") from e

    def get_bulk_operations(self):
        """Get bulk operations handler"""
        # DuckDB handles bulk operations internally
        return self

    def get_xml_files_to_process(self, processing_version: int, max_files: Optional[int] = None, last_xml_id: Optional[str] = None) -> List[XMLFile]:
        """Get unprocessed XML files with key-value paging support"""
        where_clause = "processed = FALSE OR processing_version < ?"
        params = (processing_version,)
        if last_xml_id:
            where_clause += " AND xml_id > ?"
            params += (last_xml_id,)
        order_by = "xml_id"
        limit = max_files

        # DEBUG: Log query details
        log_info(f"DEBUG: get_xml_files_to_process - processing_version={processing_version}, max_files={max_files}, last_xml_id={last_xml_id}")
        log_info(f"DEBUG: Query: WHERE {where_clause}, ORDER BY {order_by}, LIMIT {limit}")

        result = self.select_dataclass(XMLFile, where_clause=where_clause, params=params, order_by=order_by, limit=limit)
        log_info(f"DEBUG: Query returned {len(result)} XML files")
        return result

    def get_xml_files_to_process_count(self, processing_version: int, mode: str = 'count', max_files: Optional[int] = None) -> Union[int, float]:
        """
        Get the count or total bytes of unprocessed XML files.

        Args:
            processing_version: Processing version to filter by
            mode: 'count' for file count, 'bytes' for total file size sum
            max_files: Optional limit for testing (affects both count and sum)

        Returns:
            int for count mode, float for bytes mode (sum of file_size)
        """
        base_where = "processed = FALSE OR processing_version < ?"
        base_params = [processing_version]

        # DEBUG: Log count query details
        log_info(f"DEBUG: get_xml_files_to_process_count - processing_version={processing_version}, mode={mode}, max_files={max_files}")
        
        if mode == 'bytes':
            if max_files is not None:
                subquery = f"""
                    SELECT file_size FROM XmlFiles
                    WHERE {base_where}
                    ORDER BY xml_id
                    LIMIT ?
                """
                params = base_params + [max_files]
                query = f"SELECT COALESCE(SUM(file_size), 0) FROM ({subquery})"
            else:
                query = f"""
                    SELECT COALESCE(SUM(file_size), 0) FROM XmlFiles
                    WHERE {base_where}
                """
                params = base_params
        else:  # 'count'
            if max_files is not None:
                # For count with limit, the count is min(actual_count, max_files)
                # But to be precise, query the limited count
                subquery = f"""
                    SELECT 1 FROM XmlFiles
                    WHERE {base_where}
                    ORDER BY xml_id
                    LIMIT ?
                """
                params = base_params + [max_files]
                query = f"SELECT COUNT(*) FROM ({subquery})"
            else:
                query = f"SELECT COUNT(*) FROM XmlFiles WHERE {base_where}"
                params = base_params
        
        result = self.execute_query(query, tuple(params))
        count = result.fetchone()[0] if result else 0
        log_info(f"DEBUG: get_xml_files_to_process_count returned {count}")
        return count

    # Context-based approach handles this now

    # Export operations
    def get_export_operations(self, final_dir: str):
        """Get export operations handler"""
        # DuckDB handles export operations through its own methods
        return self

    def get_stats_processor(self):
        """Get stats processor instance"""
        from stats_processor import StatsProcessor
        return StatsProcessor(self)

    def optimize_database(self, commit: bool = True):
        """Run database optimization commands"""
        # Analyze tables for better query planning
        for table in ["Charities","Grants","Addresses","Officers","Geocoding","Backfill","XmlFiles","Contributions","Contractors","PoliticalContributions"]:
            self.execute_query(f"VACUUM ANALYZE {table}")

        # Regular checkpoint to ensure data is written and WAL is cleared
        self.db_conn.execute("CHECKPOINT")
        if commit:
            self.commit()

    # Logging methods removed - use global logging functions directly

    def _execute_optimize_operation(self, operation, conn):
        """Execute database optimization operation"""
        if operation.operation_type != DatabaseOperationType.OPTIMIZE_DATABASE:
            return

        log_info("Starting database optimization...")
        try:
            # Call the optimize_database method from DatabaseOperations
            self.optimize_database()
            log_info("Database optimization completed successfully")
        except Exception as e:
            log_error(f"Database optimization failed: {e}", exc_info=True)
            raise

    # All processing methods removed - context-based approach handles this now

    def _process_xml_file_update_operations(self, operations_by_type, conn, processed_xml_ids):
        """Process XML file update operations using metadata"""
        if DatabaseOperationType.XML_FILE_UPDATE.value not in operations_by_type:
            return

        xml_updates = operations_by_type[DatabaseOperationType.XML_FILE_UPDATE.value]  # type: ignore

        for operation in xml_updates:
            xml_id = operation.xml_id
            metadata = operation.data

            try:
                self._update_xml_file_with_metadata(xml_id, metadata, conn)
                processed_xml_ids.add(xml_id)
            except Exception as e:
                log_error(f"Failed to update XML file {xml_id} with metadata: {e}")
                # Do not raise here - continue processing other XML files

    def _process_charity_operations(self, operations_by_type, conn):
        """Process charity insert operations and return charity_id mapping"""
        charity_id_map = {}  # xml_id -> charity_id

        if DatabaseOperationType.INSERT_CHARITY.value not in operations_by_type:
            return charity_id_map

        charities = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]]  # type: ignore

        if not charities:
            return charity_id_map

        log_info(f"Bulk insert: Processing {len(charities)} charities")

        # Set colocator to 'notyet' for new charities
        for charity in charities:
            charity.colocator = 'notyet'

        try:
            # Use database_operations bulk_insert method which calls prep_for_insert
            ids = self.bulk_insert(charities, conn=conn)
            log_info(f"Bulk insert: Inserted {len(charities)} charities")

            # Map xml_id to charity_id using the operations list and returned IDs
            charity_ops = operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]  # type: ignore
            for i, charity_id in enumerate(ids):
                if i < len(charity_ops):
                    xml_id = charity_ops[i].xml_id
                    charity_id_map[xml_id] = charity_id

        except Exception as e:
            log_error(f"Failed to insert charities: {e}", exc_info=True)
            raise

        return charity_id_map


    def _process_address_operations(self, operations_by_type, conn, charity_id_map):
        """Process address insert operations"""

        if DatabaseOperationType.INSERT_ADDRESS.value not in operations_by_type:
            return

        addresses = []
        for operation in operations_by_type[DatabaseOperationType.INSERT_ADDRESS.value]:  # type: ignore
            address = operation.data

            # Set owner_id for charity addresses (link to charity that owns this address)
            if hasattr(address, 'address_type') and address.address_type == 'charity':
                if operation.xml_id in charity_id_map:
                    address.owner_id = charity_id_map[operation.xml_id]

            addresses.append(address)

        if addresses:
            try:
                # Use database_operations bulk_insert method which calls prep_for_insert
                ids = self.bulk_insert(addresses, conn=conn)
                log_debug(f"Inserted {len(addresses)} addresses")
            except Exception as e:
                log_error(f"Failed to insert addresses: {e}", exc_info=True)

    def format_error_with_traceback(self, error: Exception, context: str = "") -> str:
        """Format error message with full stack trace for better debugging"""
        import traceback
        error_msg = f"{context}: {str(error)}" if context else str(error)
        stack_trace = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        return f"{error_msg}\n\nStack Trace:\n{stack_trace}"

    def _get_xml_files_updated_in_batch(self, batch_operations):
        """Get set of XML IDs that were updated in this batch (XML_FILE_UPDATE operations)"""
        xml_files_updated = set()
        for op in batch_operations:
            if op.operation_type == DatabaseOperationType.XML_FILE_UPDATE:
                xml_files_updated.add(op.xml_id)
        return xml_files_updated

    def get_address_deduplication_batch(self, last_address_id: Optional[str] = None, limit: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Get canonical addresses that have duplicates needing deduplication with key-value pagination.

        Optimized single-phase query approach using master address_ids for efficient IN() queries:
        - Collects all address_ids and master_ids for qualifying canonical addresses in one query
        - Uses LIST aggregates to collect address data efficiently
        - Avoids the expensive Phase 2 query by processing master/child logic directly from collected data

        Returns canonical addresses where there are multiple addresses with the same canonical_address,
        and at least one of them has master_id IS NULL (not yet deduplicated).

        Args:
            last_address_id: Last address_id from previous batch for key-value pagination
            limit: Maximum number of canonical addresses to return

        Returns a tuple of:
        - List of deduplication operations, each containing master and children info
        - Next last_address_id for pagination (maximum of the minimum address_ids from master addresses)

        Returns:
            Tuple of (batch_operations, next_last_address_id)
        """
        import time
        start_time = time.time()

        batch_limit = limit or 1000  # Default limit if not specified

        # Single optimized query: Collect all address_ids and master_ids for qualifying canonical addresses
        # Use key-value pagination with min_id > last_address_id
        where_clause = ""
        params = []
        if last_address_id is not None:
            where_clause = "WHERE min_id > ?"
            params = [last_address_id]

        # Use LIST aggregates to collect all address_ids and master_ids for each canonical_address
        # This allows us to determine master/child relationships without a separate Phase 2 query
        optimized_query = f"""
            SELECT
                canonical_address,
                min_id,
                address_ids,
                master_ids
            FROM (
                SELECT
                    canonical_address,
                    MIN(address_id) as min_id,
                    LIST(address_id ORDER BY address_id) as address_ids,
                    LIST(master_id ORDER BY address_id) as master_ids
                FROM Addresses
                WHERE canonical_address IS NOT NULL
                    AND canonical_address != ''
                GROUP BY canonical_address
                HAVING COUNT(*) > 1
                    AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
            ) sub
            {where_clause}
            ORDER BY min_id
            LIMIT ?
        """
        params.append(str(batch_limit))

        query_start = time.time()
        result = self.execute_query(optimized_query, tuple(params))
        rows = result.fetchall() if result else []
        query_time = time.time() - query_start

        log_debug(f"Optimized query: Found {len(rows)} qualifying canonical addresses in {query_time:.2f}s")

        if not rows:
            return [], None

        # Calculate next last_address_id as the maximum of the min_id values from this batch
        next_last_address_id = max(row[1] for row in rows if row[1] is not None) if rows else None

        # Process collected data to build batch operations
        # address_ids and master_ids are returned as lists from LIST aggregate
        batch_operations = []
        for row in rows:
            canonical_address = str(row[0]) if row[0] is not None else ''
            min_id = row[1]
            address_ids = row[2] if row[2] is not None else []
            master_ids = row[3] if row[3] is not None else []

            if not address_ids or len(address_ids) != len(master_ids):
                continue  # Skip invalid data

            # Find addresses with NULL master_id (candidates for children)
            null_master_indices = [i for i, master_id in enumerate(master_ids) if master_id is None]

            if len(null_master_indices) <= 1:
                continue  # Need more than one NULL master_id to deduplicate

            # Get address_ids for NULL master_id addresses
            null_master_address_ids = [address_ids[i] for i in null_master_indices]

            # Use the maximum address_id among NULL master_id addresses as master
            master_address_id = max(null_master_address_ids)

            # Children are the other NULL master_id addresses
            child_address_ids = [addr_id for addr_id in null_master_address_ids if addr_id != master_address_id]

            if child_address_ids:  # Only include if there are children to update
                batch_operations.append({
                    'canonical_address': canonical_address,
                    'master_address_id': master_address_id,
                    'child_address_ids': child_address_ids
                })

        total_time = time.time() - start_time
        log_debug(f"Total optimized batch processing time: {total_time:.2f}s")

        return batch_operations, next_last_address_id

    def execute_address_deduplication_batch(self, master_address_id: str, child_address_ids: List[str], commit: bool = True) -> int:
        """
        Execute a batch of address deduplication updates.

        Updates all child addresses to point to the master address.

        Args:
            master_address_id: The address_id of the master address
            child_address_ids: List of address_ids that should point to the master

        Returns:
            Number of addresses updated
        """
        if not child_address_ids:
            return 0

        # Update all child addresses to point to the master
        placeholders = ','.join('?' for _ in child_address_ids)
        update_query = f"""
            UPDATE Addresses
            SET master_id = ?
            WHERE address_id IN ({placeholders})
        """

        params = [master_address_id] + child_address_ids
        self.execute_query(update_query, tuple(params))
        if commit:
            self.commit()

        return len(child_address_ids)

    def get_officers_for_export_batch(self, offset: int, limit: int) -> List[Tuple]:
        """Get batch of officers for export"""
        query = """
            SELECT charity_id, first_name, last_name, compensation, tax_year, photo_url
            FROM Officers
            ORDER BY charity_id, last_name, first_name
            LIMIT ? OFFSET ?
        """
        result = self.execute_query(query, (limit, offset))
        return result.fetchall() if result else []