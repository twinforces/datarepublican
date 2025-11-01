#!/usr/bin/env python3
"""
database_operations.py - Database operations for IRS 990 data processing

This module contains all database-related operations for the IRS 990 processor,
including CRUD operations for all data models.

This module now uses DuckDB exclusively.
"""

import duckdb
import pandas as pd
import inspect
from typing import Optional, List, Tuple, Dict, Any, Type
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
from logging_utils import get_logger, log_error, log_debug, log_info, log_warning
from loggingDuckDB import LoggingDuckDBConnection


class DatabaseOperationType(Enum):
    """Enumeration of database operation types for flexible processing"""
    INSERT_CHARITY = "insert_charity"
    INSERT_OFFICER = "insert_officer"
    INSERT_GRANT = "insert_grant"
    INSERT_CONTRACTOR = "insert_contractor"
    INSERT_POLITICAL_CONTRIBUTION = "insert_political_contribution"
    INSERT_ADDRESS = "insert_address"
    INSERT_ZIP_FILE = "insert_zip_file"
    UPDATE_XML_FILE_SUCCESS = "update_xml_file_success"
    UPDATE_XML_FILE_ERROR = "update_xml_file_error"
    XML_FILE_UPDATE = "xml_file_update"
    UPDATE_XML_EIN = "update_xml_ein"
    OPTIMIZE_DATABASE = "optimize_database"
    GENERIC_UPDATE = "generic_update"
    ADDRESS_DEDUPLICATION_BATCH = "address_deduplication_batch"
    PROGRESS_UPDATE = "progress_update"
    INSERT_GEOCODING = "insert_geocoding"
    UPDATE_GEOCODING = "update_geocoding"


class DatabaseOperation:
    """Represents a single database operation with its data and dependencies"""

    def __init__(self, operation_type: DatabaseOperationType, data: Any, xml_id: Optional[str] = None,
                 dependencies: Optional[List[str]] = None):
        self.operation_type = operation_type
        self.data = data
        self.xml_id = xml_id
        self.dependencies = dependencies or []  # List of operation types this depends on


class DatabaseOperations:
    """Handles all DuckDB operations for IRS 990 data processing"""

    # SQL logging is now read directly from global_config

    # Thread-local storage for database connections
    _local = threading.local()

    # Static cache for zip_id -> file_path mapping - preloaded at startup
    _zip_path_cache: Dict[str, str] = {}
    _zip_cache_lock = threading.RLock()  # Reentrant lock for thread safety

    @staticmethod
    def generate_uuid_v7() -> str:
        """Generate a UUID v7 (time-ordered) - now delegates to uuid7 module"""
        from uuid7 import generate_uuid_v7
        return generate_uuid_v7()

    def __init__(self, db_path: str, read_only: bool = False, memory_limit: str = "4GB", threads: Optional[int] = None, dbUI: bool = False, query_timeout: int = 300):
        """
        Initialize DuckDB connection

        Args:
            db_path: Path to DuckDB database file
            read_only: Whether to open database in read-only mode
            memory_limit: Memory limit for DuckDB (default: 4GB)
            threads: Number of threads for DuckDB (default: auto)
            query_timeout: Query timeout in seconds (default: 300)
        """
        self.db_path = db_path
        # SQL logging is now read directly from global_config
        self.read_only = read_only
        self.memory_limit = memory_limit
        self.threads = threads
        self.dbUI = dbUI
        self.query_timeout = query_timeout
        self.logger = get_logger(__name__)
        self._init_connection()
        self._preload_zip_file_cache()

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
            self.db_conn.execute("SET checkpoint_threshold = '1000MB'")  # Increase checkpoint threshold for better performance
        except Exception:
            pass  # Ignore if not supported
        self.db_conn.execute("SET preserve_insertion_order = false")  # Allow reordering for better performance

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
        """Preload all zip_id -> file_path mappings into the static cache at startup"""
        with DatabaseOperations._zip_cache_lock:
            if DatabaseOperations._zip_path_cache:
                # Already preloaded
                return

            try:
                self.logger.info("Preloading zip path cache...")
                # Query all ZipFile records from database
                zip_files = self.select_dataclass(ZipFile, order_by="zip_id")
                self.logger.info(f"Loaded {len(zip_files)} ZipFile objects from database")

                # Populate the cache with zip_id -> file_path mappings
                for zip_file in zip_files:
                    DatabaseOperations._zip_path_cache[zip_file.zip_id] = zip_file.file_path

                self.logger.info(f"Zip path cache preloaded with {len(DatabaseOperations._zip_path_cache)} entries")

            except Exception as e:
                self.logger.error(f"Failed to preload zip path cache: {e}")
                # Continue without cache - operations will fall back to database queries

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
        """Execute a query and return results with timeout protection and enhanced error handling"""
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
                    self.logger.info(f"SQL from {filename}:{line_number} in {function_name}: {query}")
                    if params:
                        self.logger.info(f"Parameters: {params}")

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
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except duckdb.ConnectionException as e:
            # Handle connection-related errors
            error_msg = f"Database connection error: {str(e)}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            # Handle any other unexpected errors
            error_msg = f"Unexpected database error: {str(e)}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _get_thread_local_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create a thread-local database connection"""
        if not hasattr(DatabaseOperations._local, 'db_conn'):
            # Create a new connection for this thread
            if global_config.log_sql:
                DatabaseOperations._local.db_conn = LoggingDuckDBConnection(self.db_path, config=self._connection_config)
            else:
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
                DatabaseOperations._local.db_conn.execute("SET checkpoint_threshold = '1000MB'")
            except Exception:
                pass
            DatabaseOperations._local.db_conn.execute("SET preserve_insertion_order = false")
            # Note: DuckDB doesn't have a built-in query_timeout setting in this version
            # The timeout protection will be handled through enhanced error handling in execute_query
            pass

        return DatabaseOperations._local.db_conn  # type: ignore

    def select_dataclass(self, dataclass_type: Type, where_clause: str = "", params: Optional[Tuple] = None,
                        order_by: str = "", limit: Optional[int] = None, offset: Optional[int] = None,
                        df_threshold: int = 10000) -> List[Any]:
        """
        Generic method to select records and convert them to dataclass instances using reflection.

        Args:
            dataclass_type: The dataclass type to instantiate (e.g., Charity, Address)
            where_clause: Optional WHERE clause (without the WHERE keyword)
            params: Parameters for the WHERE clause
            order_by: Optional ORDER BY clause (without the ORDER BY keyword)
            limit: Optional LIMIT clause
            offset: Optional OFFSET clause
            df_threshold: Threshold for using DataFrame vs fetchall (default: 10000)

        Returns:
            List of dataclass instances
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
        if df_threshold is not None:
            # Execute count query to check result size
            count_query = f"SELECT COUNT(*) FROM {table_name}"
            if where_clause:
                count_query += f" WHERE {where_clause}"
            count_result = self.execute_query(count_query, params)
            if count_result:
                row_count = count_result.fetchone()
                if row_count and len(row_count) > 0:
                    row_count = row_count[0]
                    use_dataframe = row_count > df_threshold

        # Execute main query
        result = self.execute_query(query, params)

        if use_dataframe:
            # Use DataFrame for large result sets
            df = result.df()
            row_dicts = df.to_dict('records')
        else:
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

    @lru_cache(maxsize=None)
    def _get_table_columns(self, table_name: str, conn=None) -> List[str]:
        """Get column names for a table, cached for performance"""
        if conn is None:
            conn = self.db_conn
        try:
            result = conn.execute(f"DESCRIBE {table_name}")
            return [row[0] for row in result.fetchall()]
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

    @lru_cache(maxsize=None)
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
        """Insert ZipFile into database using generic bulk_insert method"""
        ids = self.bulk_insert([zip_file])
        zip_id = ids[0] if ids else ""
        # Update cache if insertion successful
        if zip_id:
            with DatabaseOperations._zip_cache_lock:
                DatabaseOperations._zip_path_cache[zip_id] = zip_file.file_path
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
        """Get ZipFile from database (no longer cached)"""
        # Fallback to database query
        zip_files = self.select_dataclass(ZipFile, where_clause="zip_id = ?", params=(zip_id,))
        if zip_files:
            return zip_files[0]
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
        """Insert XMLFile into database using generic bulk_insert method"""
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

    # Address operations
    def insert_address(self, address: Address) -> str:
        """Insert Address into database using generic bulk_insert method"""
        ids = self.bulk_insert([address])
        return ids[0] if ids else ""

    def get_addresses_for_geocoding(self, limit: Optional[int] = None, offset: int = 0) -> List[Address]:
        """Get addresses that need geocoding, with optional batching support"""
        where_clause = "geocoding_id IS NULL AND (po_box IS NULL OR po_box = '') and colocator IS NULL"
        return self.select_dataclass(Address, where_clause=where_clause, order_by="address_id", limit=limit, offset=offset)

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

    # Charity operations
    def insert_charity(self, charity: Charity) -> str:
        """Insert Charity into database using generic bulk_insert method"""
        ids = self.bulk_insert([charity])
        return ids[0] if ids else ""

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
            SELECT filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                   filer_colocator, grantee_colocator
            FROM Grants
            ORDER BY filer_ein, grant_ein
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

    # Grant operations
    def insert_grant(self, grant: Grant) -> str:
        """Insert Grant into database using generic bulk_insert method"""
        ids = self.bulk_insert([grant])
        return ids[0] if ids else ""

    def update_grant_ein(self, grant_id: str, grant_ein: str):
        """Update grant with matched EIN"""
        self.execute_query("""
            UPDATE Grants SET grant_ein = ? WHERE grant_id = ?
        """, (grant_ein, grant_id))
        self.commit()

    def get_grants_without_ein(self) -> List[Grant]:
        """Get grants with unknown EINs for matching"""
        return self.select_dataclass(Grant, where_clause="grant_ein IS NULL OR grant_ein = ''")

    # Officer operations
    def insert_officer(self, officer: Officer) -> str:
        """Insert Officer into database using generic bulk_insert method"""
        ids = self.bulk_insert([officer])
        return ids[0] if ids else ""

    # Contractor operations
    def insert_contractor(self, contractor: Contractor) -> str:
        """Insert Contractor into database using generic bulk_insert method"""
        ids = self.bulk_insert([contractor])
        return ids[0] if ids else ""

    # Political Contribution operations
    def insert_political_contribution(self, contribution: PoliticalContribution) -> str:
        """Insert PoliticalContribution into database using generic bulk_insert method"""
        ids = self.bulk_insert([contribution])
        return ids[0] if ids else ""

    # Geocoding operations
    def insert_geocoding_record(self, normalized_address: str,
                                latitude: Optional[float] = None, longitude: Optional[float] = None,
                                status: str = 'pending') -> str:
        """Insert geocoding record. Returns UUID."""
        geocoding_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT INTO Geocoding (geocoding_id, normalized_address, latitude, longitude, geocoding_status)
            VALUES (?, ?, ?, ?, ?)
        """, (geocoding_id, normalized_address, latitude, longitude, status))
        self.commit()
        return geocoding_id

    # Bulk operations
    def bulk_insert(self, objects: List[BaseModel], batch_size: int = 50000, use_pandas: bool = False, conn: Optional[duckdb.DuckDBPyConnection] = None) -> List[str]:
        """
        Bulk insert with executemany for high-throughput; optional Pandas fallback.
        Now uses database-generated UUIDs with RETURNING clause for better performance.

        Args:
            objects: Non-empty same-type BaseModels (prepped).
            batch_size: Rows per executemany call (10k-50k; smaller = less mem).
            use_pandas: Toggle vectorized DF (slower on str-heavy; for testing).
            conn: Optional for txns.

        Returns:
            List of str IDs (from database RETURNING clause).

        Raises:
            ValueError: Invalid inputs.
            RuntimeError: Param/build fail.
        """
        if not objects:
            return []
        obj_type = type(objects[0])
        if len({type(obj) for obj in objects}) > 1:
            raise ValueError("Uniform types only.")

        # Validation moved to pending_database_context.py

        conn = conn or self.db_conn  # type: ignore
        table_name = self._get_table_name(obj_type)
        model_fields = set(obj_type.get_db_field_names())
        table_cols = self._get_table_columns(table_name, conn)
        if not table_cols:
            raise ValueError(f"No table columns found for {table_name}")
        missing = set(table_cols) - model_fields
        if missing:
            self.logger.warning(f"{table_name}: NULL-filling {len(missing)} DB cols")
        id_field = next((col for col in table_cols if col.endswith('_id') or col == 'id'), None)
        if not id_field:
            raise ValueError(f"No ID in {table_name}.")

        # Prep all (upfront; chunk if >100k in future) - generate IDs client-side for object relationships
        prep_start = time.perf_counter()
        for obj in objects:
            obj.prep_for_insert()
            obj.set_id_if_needed()  # Generate IDs client-side for object tree relationships
        self.logger.debug(f"Prepped {len(objects)} objs in {time.perf_counter() - prep_start:.2f}s")

        # Use all table columns including ID field since we generate them client-side
        insert_cols = table_cols

        if use_pandas:
            # Fallback: Your current DF path (for A/B)
            data_dict = {col: [getattr(obj, col) if col in model_fields else None for obj in objects] for col in insert_cols}
            df = pd.DataFrame(data_dict)
            all_ids = []
            for i in range(0, len(objects), batch_size):
                batch_df = df.iloc[i:i + batch_size]
                view_name = f'temp_{uuid.uuid4().hex[:8]}'
                conn.register(view_name, batch_df)  # type: ignore
                col_list = ', '.join(insert_cols)
                insert_sql = f"INSERT INTO {table_name} ({col_list}) SELECT {col_list} FROM {view_name} RETURNING {id_field}"
                result = conn.execute(insert_sql)  # type: ignore
                batch_ids = [row[0] for row in result.fetchall()]
                all_ids.extend(batch_ids)
                conn.unregister(view_name)  # type: ignore
            return all_ids
        else:
            # executemany core: Build param lists (excluding ID field)
            build_start = time.perf_counter()
            param_rows = []
            for obj in objects:
                row = tuple(getattr(obj, col) if col in model_fields else None for col in insert_cols)
                param_rows.append(row)
            build_time = time.perf_counter() - build_start
            self.logger.debug(f"Built {len(objects)} param rows in {build_time:.2f}s")

            # Batched executemany for maximum performance - IDs already generated client-side
            insert_start = time.perf_counter()
            for i in range(0, len(objects), batch_size):
                batch_params = param_rows[i:i + batch_size]

                # Use executemany with single row inserts - no RETURNING needed since IDs are client-generated
                col_list = ', '.join(insert_cols)
                placeholders = ', '.join(['?' for _ in insert_cols])
                insert_sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"
                if global_config.log_sql:
                    self.logger.info(f"bulk SQL {insert_sql}")

                # SQL logging handled by execute_query method

                batch_start = time.perf_counter()
                conn.executemany(insert_sql, batch_params)  # type: ignore
                batch_elapsed = time.perf_counter() - batch_start
                rate = len(batch_params) / batch_elapsed if batch_elapsed > 0 else 0
                self.logger.debug(f"Batch {i//batch_size + 1} ({len(batch_params)} rows): {batch_elapsed:.2f}s ({rate:.0f} rows/s)")
            insert_time = time.perf_counter() - insert_start
            self.logger.info(f"executemany inserted {len(objects)} rows in {insert_time:.2f}s ({len(objects)/insert_time:.0f} rows/s)")

            # Log bulk insert completion with counts
            log_info(self.logger, f"Bulk insert completed: {len(objects)} {obj_type.__name__} records inserted")

            # Return the client-generated IDs
            return [str(getattr(obj, id_field)) for obj in objects]

    def bulk_update(self, table_name: str, updates: List[Dict[str, Any]], id_column: str = 'id', conn=None) -> int:
        """Generic bulk update method for database operations"""
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
            # Execute bulk update using executemany for proper batching
            conn.executemany(sql, params)  # type: ignore
            # Do not commit here - let caller handle transaction
            return len(updates)
        except Exception as e:
            # Do not rollback here - let caller handle transaction
            raise RuntimeError(f"Bulk update failed for table {table_name}: {str(e)}") from e

    def get_bulk_operations(self):
        """Get bulk operations handler"""
        # DuckDB handles bulk operations internally
        return self

    def get_xml_files_to_process(self, processing_version: int, max_files: Optional[int] = None) -> List[XMLFile]:
        """Get unprocessed XML files"""
        where_clause = "processed = FALSE OR processing_version < ?"
        params = (processing_version,)
        order_by = "zip_id, filename"
        limit = max_files

        return self.select_dataclass(XMLFile, where_clause=where_clause, params=params, order_by=order_by, limit=limit)

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

    def optimize_database(self):
        """Run database optimization commands"""
        # Analyze tables for better query planning
        for table in ["Charities","Grants","Addresses","Officers","Geocoding","Backfill","XmlFiles","Contributions","Contractors","PoliticalContributions"]:
            self.execute_query(f"VACUUM ANALYZE {table}")

        # Checkpoint to ensure data is written
        self.db_conn.checkpoint()
        self.commit()

    # Logging methods removed - use global logging functions directly

    def _execute_optimize_operation(self, operation, conn):
        """Execute database optimization operation"""
        if operation.operation_type != DatabaseOperationType.OPTIMIZE_DATABASE:
            return

        log_info(self.logger, "Starting database optimization...")
        try:
            # Call the optimize_database method from DatabaseOperations
            self.optimize_database()
            log_info(self.logger, "Database optimization completed successfully")
        except Exception as e:
            log_error(self.logger, f"Database optimization failed: {e}", exc_info=True)
            raise

    # All processing methods removed - context-based approach handles this now

    def _process_xml_file_update_operations(self, operations_by_type, conn, processed_xml_ids):
        """Process XML file update operations using metadata"""
        if DatabaseOperationType.XML_FILE_UPDATE.value not in operations_by_type:
            return

        xml_updates = operations_by_type[DatabaseOperationType.XML_FILE_UPDATE.value]

        for operation in xml_updates:
            xml_id = operation.xml_id
            metadata = operation.data

            try:
                self._update_xml_file_with_metadata(xml_id, metadata, conn)
                processed_xml_ids.add(xml_id)
            except Exception as e:
                log_error(self.logger, f"Failed to update XML file {xml_id} with metadata: {e}")
                # Do not raise here - continue processing other XML files

    def _process_charity_operations(self, operations_by_type, conn):
        """Process charity insert operations and return charity_id mapping"""
        charity_id_map = {}  # xml_id -> charity_id

        if DatabaseOperationType.INSERT_CHARITY.value not in operations_by_type:
            return charity_id_map

        charities = [op.data for op in operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]]

        if not charities:
            return charity_id_map

        log_info(self.logger, f"Bulk insert: Processing {len(charities)} charities")

        # Set colocator to 'notyet' for new charities
        for charity in charities:
            charity.colocator = 'notyet'

        try:
            # Use database_operations bulk_insert method which calls prep_for_insert
            ids = self.bulk_insert(charities, conn=conn)
            log_info(self.logger, f"Bulk insert: Inserted {len(charities)} charities")

            # Map xml_id to charity_id using the operations list and returned IDs
            charity_ops = operations_by_type[DatabaseOperationType.INSERT_CHARITY.value]
            for i, charity_id in enumerate(ids):
                if i < len(charity_ops):
                    xml_id = charity_ops[i].xml_id
                    charity_id_map[xml_id] = charity_id

        except Exception as e:
            log_error(self.logger, f"Failed to insert charities: {e}", exc_info=True)
            raise

        return charity_id_map


    def _process_address_operations(self, operations_by_type, conn, charity_id_map):
        """Process address insert operations"""

        if DatabaseOperationType.INSERT_ADDRESS.value not in operations_by_type:
            return

        addresses = []
        for operation in operations_by_type[DatabaseOperationType.INSERT_ADDRESS.value]:
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
                log_debug(self.logger, f"Inserted {len(addresses)} addresses")
            except Exception as e:
                log_error(self.logger, f"Failed to insert addresses: {e}", exc_info=True)

    def _update_xml_file_with_metadata(self, xml_id, metadata, conn):
        """Update XmlFiles table with metadata from processing"""
        try:
            if metadata["error_message"]:
                # Error case
                conn.execute(
                    "UPDATE XmlFiles SET processed=?, processing_version=?, error_message=?, file_size=?, processed_at=? WHERE xml_id=?",
                    (metadata["processed"], CURRENT_PROCESSING_VERSION, metadata["error_message"], metadata["file_size"], datetime.now().isoformat() if metadata["processed"] else None, xml_id)
                )
            else:
                # Success case
                conn.execute(
                    "UPDATE XmlFiles SET processed=?, processing_version=?, form_type=?, ein=?, tax_year=?, org_type=?, file_size=?, processed_at=? WHERE xml_id=?",
                    (metadata["processed"], CURRENT_PROCESSING_VERSION, metadata["form_type"], metadata["ein"], metadata["tax_year"], metadata.get("org_type"), metadata["file_size"], datetime.now().isoformat() if metadata["processed"] else None, xml_id)
                )
        except Exception as e:
            log_error(self.logger, f"Failed to update XML file {xml_id} with metadata: {e}")

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

    def get_address_deduplication_batch(self, last_address_id: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get canonical addresses that have duplicates needing deduplication with pagination.

        Uses two-phase query approach to avoid full table scan:
        1. First phase: Identify qualifying canonical addresses (have duplicates with NULL master_id) with pagination
        2. Second phase: Fetch details only for those qualifying addresses

        Returns canonical addresses where there are multiple addresses with the same canonical_address,
        and at least one of them has master_id IS NULL (not yet deduplicated).

        Args:
            last_address_id: Last processed address_id for pagination (address_id > last_address_id)
            limit: Maximum number of canonical addresses to return

        Returns a list of dicts with:
        - canonical_address: the canonical address string
        - master_address_id: the min(address_id) to use as master
        - child_address_ids: list of address_ids that should point to the master (excluding the master itself)

        Returns:
            List of deduplication operations, each containing master and children info
        """
        import time
        start_time = time.time()

        batch_limit = limit or 1000  # Default limit if not specified

        # Phase 1: Identify qualifying canonical addresses that have duplicates and NULL master_ids
        # Use subquery with max(address_id) for pagination and LIMIT
        if last_address_id is None:
            # First batch - no pagination filter needed
            phase1_query = """
                SELECT canonical_address, max_id
                FROM (
                    SELECT canonical_address, MAX(address_id) as max_id
                    FROM Addresses
                    WHERE canonical_address IS NOT NULL
                        AND canonical_address != ''
                    GROUP BY canonical_address
                    HAVING COUNT(*) > 1
                        AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
                ) sub
                ORDER BY max_id
                LIMIT ?
            """
            params = (batch_limit,)
        else:
            # Subsequent batches - filter by last_address_id
            phase1_query = """
                SELECT canonical_address, max_id
                FROM (
                    SELECT canonical_address, MAX(address_id) as max_id
                    FROM Addresses
                    WHERE canonical_address IS NOT NULL
                        AND canonical_address != ''
                    GROUP BY canonical_address
                    HAVING COUNT(*) > 1
                        AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
                ) sub
                WHERE max_id > ?
                ORDER BY max_id
                LIMIT ?
            """
            params = (last_address_id, batch_limit)

        phase1_start = time.time()
        qualifying_result = self.execute_query(phase1_query, params)
        qualifying_rows = qualifying_result.fetchall() if qualifying_result else []
        phase1_time = time.time() - phase1_start

        self.logger.debug(f"Phase 1: Found {len(qualifying_rows)} qualifying canonical addresses in {phase1_time:.2f}s")

        if not qualifying_rows:
            return []

        # Extract canonical addresses for phase 2
        canonical_addresses = [row[0] for row in qualifying_rows if row[0] is not None]

        # Phase 2: Fetch details only for qualifying canonical addresses
        # Use parameterized query with IN clause for the qualifying addresses
        placeholders = ','.join('?' for _ in canonical_addresses)
        details_query = f"""
            SELECT canonical_address, address_id, master_id
            FROM Addresses
            WHERE canonical_address IN ({placeholders})
            ORDER BY canonical_address, address_id
        """

        phase2_start = time.time()
        details_result = self.execute_query(details_query, tuple(canonical_addresses))
        details_rows = details_result.fetchall() if details_result else []
        phase2_time = time.time() - phase2_start

        self.logger.debug(f"Phase 2: Fetched {len(details_rows)} detail rows in {phase2_time:.2f}s")
        total_time = time.time() - start_time
        self.logger.debug(f"Total batch processing time: {total_time:.2f}s")

        # Group by canonical_address and compute master/child relationships
        from collections import defaultdict
        address_groups = defaultdict(list)

        for row in details_rows:
            canonical_address = str(row[0]) if row[0] is not None else ''
            address_id = str(row[1]) if row[1] is not None else ''
            master_id = row[2]  # Can be None

            address_groups[canonical_address].append({
                'address_id': address_id,
                'master_id': master_id
            })

        # Build batch operations
        batch_operations = []
        for canonical_address in canonical_addresses:
            if canonical_address not in address_groups:
                continue

            addresses = address_groups[canonical_address]

            # Find addresses with NULL master_id (candidates for children)
            null_master_addresses = [addr for addr in addresses if addr['master_id'] is None]

            if len(null_master_addresses) <= 1:
                continue  # Need more than one NULL master_id to deduplicate

            # Use the minimum address_id as master among NULL master_id addresses
            null_master_ids = [addr['address_id'] for addr in null_master_addresses]
            master_address_id = min(null_master_ids)

            # Children are the other NULL master_id addresses
            child_address_ids = [addr_id for addr_id in null_master_ids if addr_id != master_address_id]

            if child_address_ids:  # Only include if there are children to update
                batch_operations.append({
                    'canonical_address': canonical_address,
                    'master_address_id': master_address_id,
                    'child_address_ids': child_address_ids
                })

        return batch_operations

    def execute_address_deduplication_batch(self, master_address_id: str, child_address_ids: List[str]) -> int:
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