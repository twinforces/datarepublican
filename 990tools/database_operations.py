#!/usr/bin/env python3
"""
database_operations.py - Database operations for IRS 990 data processing

This module contains all database-related operations for the IRS 990 processor,
including CRUD operations for all data models.

This module now uses DuckDB exclusively.
"""

import duckdb
from typing import Optional, List, Tuple, Dict, Any, Type
from dataclasses import fields
from datetime import datetime
from pathlib import Path
import uuid
import time
import random
import sys
import os

# Add current directory to path for imports
sys.path.append(os.path.dirname(__file__))

# Import all dataclasses from irs990processorDC
from irs990processorDC import Address, ZipFile, XMLFile, Charity, Grant, Officer, Contractor, PoliticalContribution


class DatabaseOperations:
    """Handles all DuckDB operations for IRS 990 data processing"""

    # Valid US state and territory abbreviations
    VALID_STATES = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW', 'AA', 'AE', 'AP'}

    @staticmethod
    def generate_uuid_v7() -> str:
        """Generate a UUID v7 (time-ordered) instead of random v4"""
        # Get current timestamp in milliseconds since Unix epoch
        timestamp_ms = int(time.time() * 1000)

        # UUID v7 format: timestamp (48 bits) + version (4 bits) + rand_a (12 bits) + variant (2 bits) + rand_b (62 bits)
        # timestamp: 48 bits (milliseconds since 1970-01-01)
        # version: 4 bits (set to 7)
        # rand_a: 12 bits (random)
        # variant: 2 bits (set to 2 for RFC 4122)
        # rand_b: 62 bits (random)

        # Extract timestamp components
        timestamp_high = (timestamp_ms >> 16) & 0xFFFFFFFFFFFF  # 48 bits
        timestamp_low = timestamp_ms & 0xFFFF  # 16 bits

        # Generate random parts
        rand_a = random.randint(0, 0xFFF)  # 12 bits
        rand_b = random.randint(0, 0x3FFFFFFFFFFFFFFF)  # 62 bits

        # Construct UUID v7
        # Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        # timestamp_high (32 bits) | version (4 bits) | timestamp_low (16 bits) | rand_a (12 bits) | variant (2 bits) | rand_b (62 bits)

        part1 = timestamp_high >> 16  # First 32 bits of timestamp
        part2 = ((timestamp_high & 0xFFFF) << 16) | (7 << 12) | timestamp_low  # timestamp_low(16) + version(4) + timestamp_high_low(12)
        part3 = (rand_a << 2) | 0x2  # rand_a(12) + variant(2)
        part4 = rand_b  # rand_b(62 bits, but we'll take 32)

        # Actually construct properly
        uuid_int = (timestamp_high << 80) | (7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b

        # Convert to hex and format as UUID string
        uuid_hex = f"{uuid_int:032x}"
        return f"{uuid_hex[:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-{uuid_hex[16:20]}-{uuid_hex[20:32]}"

    def __init__(self, db_path: str, log_sql: bool = False, read_only: bool = False, memory_limit: str = "4GB", threads: int = None):
        """
        Initialize DuckDB connection

        Args:
            db_path: Path to DuckDB database file
            log_sql: Whether to log SQL queries
            read_only: Whether to open database in read-only mode
            memory_limit: Memory limit for DuckDB (default: 4GB)
            threads: Number of threads for DuckDB (default: auto)
        """
        self.db_path = db_path
        self.log_sql = log_sql
        self.read_only = read_only
        self.memory_limit = memory_limit
        self.threads = threads
        self.db_conn: duckdb.DuckDBPyConnection
        self._init_connection()

    def _init_connection(self):
        """Initialize DuckDB connection and schema"""
        # Connect to DuckDB with performance optimizations
        config = {
            'memory_limit': self.memory_limit
        }
        if self.threads and self.threads != 'auto':
            config['threads'] = self.threads
        if self.read_only:
            config['read_only'] = True

        self.db_conn = duckdb.connect(self.db_path, config=config)

        # Set additional performance settings
        self.db_conn.execute("SET enable_progress_bar = false")  # Disable progress bars for better performance
        self.db_conn.execute("SET enable_object_cache = true")   # Enable object cache
        self.db_conn.execute("SET max_temp_directory_size = '10GB'")  # Increase temp directory size

        # Enable query logging if requested
        if self.log_sql:
            # DuckDB doesn't have a direct trace callback like sqlite3,
            # but we can implement custom logging
            pass

        # Initialize schema if needed
        self._init_schema()

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

    def execute_query(self, query: str, params: tuple = None) -> duckdb.DuckDBPyRelation:
        """Execute a query and return results"""
        if self.log_sql:
            print(f"Executing: {query}")
            if params:
                print(f"Parameters: {params}")

        if params:
            return self.db_conn.execute(query, params)
        else:
            return self.db_conn.execute(query)

    def select_dataclass(self, dataclass_type: Type, where_clause: str = "", params: tuple = None,
                        order_by: str = "", limit: int = None, offset: int = None) -> List[Any]:
        """
        Generic method to select records and convert them to dataclass instances using reflection.

        Args:
            dataclass_type: The dataclass type to instantiate (e.g., Charity, Address)
            where_clause: Optional WHERE clause (without the WHERE keyword)
            params: Parameters for the WHERE clause
            order_by: Optional ORDER BY clause (without the ORDER BY keyword)
            limit: Optional LIMIT clause

        Returns:
            List of dataclass instances
        """
        print(f"DEBUG select_dataclass: Called with limit={limit}, offset={offset}")
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
            print(f"DEBUG select_dataclass: Added LIMIT {limit} to query")

        if offset:
            query += f" OFFSET {offset}"
            print(f"DEBUG select_dataclass: Added OFFSET {offset} to query")

        # Execute query
        result = self.execute_query(query, params)
        rows = result.fetchall()

        # Convert rows to dataclass instances
        instances = []
        for row in rows:
            # Create dict from row data
            row_dict = dict(zip(field_names, row))
            # Instantiate dataclass with only the fields that were selected
            instance = dataclass_type(**row_dict)
            instances.append(instance)

        return instances

    def _filter_existing_columns(self, table_name: str, field_names: List[str]) -> List[str]:
        """Filter field names to only include columns that exist in the table"""
        try:
            # Get column names from the table
            result = self.execute_query(f"DESCRIBE {table_name}")
            table_columns = [row[0] for row in result.fetchall()]

            # Filter field names to only include existing columns
            existing_fields = [field for field in field_names if field in table_columns]
            return existing_fields
        except Exception:
            # If DESCRIBE fails, return all fields (fallback)
            return field_names

    def _get_table_name(self, dataclass_type: Type) -> str:
        """Get table name from dataclass type"""
        class_name = dataclass_type.__name__
        # Special cases based on actual table names in schema
        table_name_map = {
            'XMLFile': 'XmlFiles',
            'ZipFile': 'ZipFiles',
            'PoliticalContribution': 'PoliticalContributions'
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

    def __del__(self):
        """Cleanup database connection on object destruction"""
        self.close()

    # ZipFile operations
    def insert_zip_file(self, zip_file: ZipFile) -> str:
        """Insert ZipFile into database, handling duplicates. Returns UUID."""
        zip_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT OR IGNORE INTO ZipFiles (zip_id, filename, file_path, tax_year, file_size, checksum,
                                  download_date, processed_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (zip_id, zip_file.filename, zip_file.file_path, zip_file.tax_year,
              zip_file.file_size, zip_file.checksum, zip_file.download_date.isoformat() if zip_file.download_date else None,
              zip_file.processed_date.isoformat() if zip_file.processed_date else None, zip_file.status))
        zip_file.zip_id = zip_id
        self.commit()
        return zip_id

    def update_zip_status(self, zip_id: str, status: str):
        """Update ZIP file processing status"""
        self.execute_query("""
            UPDATE ZipFiles SET status = ?, processed_date = ?
            WHERE zip_id = ?
        """, (status, datetime.now().isoformat(), zip_id))
        self.commit()

    # XMLFile operations
    def insert_xml_file(self, xml_file: XMLFile) -> str:
        """Insert XMLFile into database, handling duplicates. Returns UUID."""
        xml_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT OR IGNORE INTO XmlFiles (xml_id, zip_id, filename, internal_path, ein, tax_year,
                                  form_type, processed, processing_version, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (xml_id, xml_file.zip_id, xml_file.filename, xml_file.internal_path,
              xml_file.ein, xml_file.tax_year, xml_file.form_type,
              xml_file.processed, xml_file.processing_version, xml_file.error_message))
        xml_file.xml_id = xml_id
        self.commit()
        return xml_id

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
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (processing_version, "success", xml_id))
        self.commit()

    def mark_xml_error(self, xml_id: str, processing_version: int, error_msg: str):
        """Mark XML file as having an error"""
        self.execute_query("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (processing_version, error_msg, xml_id))
        self.commit()

    def update_xml_ein(self, xml_id: str, ein: str):
        """Update XML file with EIN after parsing"""
        self.execute_query("UPDATE XmlFiles SET ein = ? WHERE xml_id = ?", (ein, xml_id))
        self.commit()

    # Address operations
    def insert_address(self, address: Address) -> str:
        """Insert Address into database, avoiding duplicates. Returns UUID."""
        # Use the colocator that was set by __post_init__ instead of recalculating
        colocator = address.colocator

        # Ensure canonical_address is built from components if not provided
        canonical_address = address.canonical_address

        # Check for existing address before insertion
        existing_check = self.execute_query("""
            SELECT address_id FROM Addresses
            WHERE ein = ? AND canonical_address = ?
        """, (address.ein, canonical_address)).fetchone()

        if existing_check:
            print(f"DEBUG insert_address: DUPLICATE FOUND - Address for EIN {address.ein} with canonical_address '{canonical_address}' already exists (address_id: {existing_check[0]}). Skipping insertion.")
            return existing_check[0]  # Return existing address_id

        # Debug logging
        if hasattr(address, 'ein') and address.ein:
            print(f"DEBUG: Inserting NEW address for EIN {address.ein}: line1='{address.address_line1}', line2='{address.address_line2}', city='{address.city}', state='{address.state}', zip='{address.zip_code}', po_box='{address.po_box}', canonical='{canonical_address}', colocator='{colocator}'")
            # Log the final colocator value being inserted
            print(f"DEBUG insert_address: Final colocator value being inserted: '{colocator}' (from address.colocator)")

        address_id = self.generate_uuid_v7()
        sql = """
            INSERT OR IGNORE INTO Addresses (address_id, ein, name, address_line1, address_line2, city, state, zip_code, po_box,
                                    canonical_address, address_type, geocoding_id, latitude, longitude, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        values = (address_id, address.ein, address.name, getattr(address, 'address_line1', None), getattr(address, 'address_line2', None),
                  getattr(address, 'city', None), getattr(address, 'state', None),
                  address.zip_code, address.po_box, canonical_address,
                  address.address_type, address.geocoding_id, address.latitude, address.longitude, colocator)

        # Log the SQL statement and values
        print(f"DEBUG insert_address: SQL: {sql.strip()}")
        print(f"DEBUG insert_address: Values: {values}")

        result = self.execute_query(sql, values)

        # Check if the insert actually happened (DuckDB INSERT OR IGNORE returns affected rows)
        # For DuckDB, we need to check the result differently
        try:
            # Try to get the row count affected
            affected_rows = result.fetchall()  # This might not work for INSERT
            print(f"DEBUG insert_address: Query executed, result: {affected_rows}")
        except:
            print(f"DEBUG insert_address: Query executed (unable to get affected rows)")

        # Verify the address was actually inserted
        verify_check = self.execute_query("""
            SELECT address_id FROM Addresses
            WHERE address_id = ?
        """, (address_id,)).fetchone()

        if verify_check:
            print(f"DEBUG insert_address: SUCCESS - Address inserted with address_id: {address_id}")
            address.address_id = address_id
            self.commit()
            return address_id
        else:
            print(f"DEBUG insert_address: FAILURE - Address was not inserted despite no duplicate found. Checking for constraint violations...")
            # Check for any constraint issues by trying a simple select
            constraint_check = self.execute_query("""
                SELECT COUNT(*) FROM Addresses
                WHERE ein = ? AND canonical_address = ?
            """, (address.ein, canonical_address)).fetchone()
            print(f"DEBUG insert_address: After failed insert, found {constraint_check[0]} existing addresses with same ein+canonical_address")
            return None

    def get_addresses_for_geocoding(self, limit: int = None, offset: int = 0) -> List[Address]:
        """Get addresses that need geocoding, with optional batching support"""
        print(f"DEBUG get_addresses_for_geocoding: Called with limit={limit}, offset={offset}")
        # DEBUG: First get overall statistics
        total_count = self.execute_query("SELECT COUNT(*) FROM Addresses").fetchone()[0]
        null_geocoding_count = self.execute_query("SELECT COUNT(*) FROM Addresses WHERE geocoding_id IS NULL").fetchone()[0]
        po_box_condition_count = self.execute_query("SELECT COUNT(*) FROM Addresses WHERE geocoding_id IS NULL AND (po_box IS NULL OR po_box = '')").fetchone()[0]

        print(f"DEBUG get_addresses_for_geocoding: Total addresses: {total_count}")
        print(f"DEBUG get_addresses_for_geocoding: Addresses with NULL geocoding_id: {null_geocoding_count}")
        print(f"DEBUG get_addresses_for_geocoding: Addresses meeting geocoding criteria (no PO box): {po_box_condition_count}")
        print(f"DEBUG get_addresses_for_geocoding: Addresses excluded by PO box condition: {null_geocoding_count - po_box_condition_count}")

        # DEBUG: Check why addresses are excluded
        if null_geocoding_count > 0 and po_box_condition_count == 0:
            excluded_by_po_box = self.execute_query("""
                SELECT COUNT(*) FROM Addresses
                WHERE geocoding_id IS NULL AND po_box IS NOT NULL AND po_box != ''
            """).fetchone()[0]
            print(f"DEBUG get_addresses_for_geocoding: Excluded by PO box condition: {excluded_by_po_box}")

            # Sample of excluded addresses
            excluded_sample = self.execute_query("""
                SELECT address_id, address_line1, address_line2, city, state, zip_code, po_box, geocoding_id
                FROM Addresses
                WHERE geocoding_id IS NULL
                LIMIT 5
            """).fetchall()
            print(f"DEBUG get_addresses_for_geocoding: Sample excluded addresses: {excluded_sample}")

        where_clause = "geocoding_id IS NULL AND (po_box IS NULL OR po_box = '')"
        print(f"DEBUG get_addresses_for_geocoding: Calling select_dataclass with limit={limit}, offset={offset}")
        addresses = self.select_dataclass(Address, where_clause=where_clause, order_by="address_id", limit=limit, offset=offset)

        print(f"DEBUG get_addresses_for_geocoding: Final query returned {len(addresses)} addresses (limit={limit}, offset={offset})")
        if addresses:
            print(f"DEBUG get_addresses_for_geocoding: Sample addresses: {addresses[:3]}")
        return addresses

    def update_address_geocoding(self, address_id: str, geocoding_id: Optional[str] = None, colocator: str = None):
        """Update address with geocoding information"""
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
        self.commit()

        # Debug logging
        if geocoding_id is not None or colocator is not None:
            print(f"DEBUG update_address_geocoding: Updated address {address_id} with geocoding_id={geocoding_id}, colocator={colocator}")

    def update_address_po_box_and_colocator(self, address_id: str, po_box: str, colocator: str):
        """Update address with PO Box and colocator information"""
        self.execute_query("""
            UPDATE Addresses SET po_box = ?, colocator = ?
            WHERE address_id = ?
        """, (po_box, colocator, address_id))
        self.commit()
        print(f"DEBUG update_address_po_box_and_colocator: Updated address {address_id} with po_box='{po_box}', colocator='{colocator}'")

    # Charity operations
    def insert_charity(self, charity: Charity) -> str:
        """Insert Charity into database, handling duplicates by EIN and tax_year. Returns UUID."""
        charity_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT OR IGNORE INTO Charities (charity_id, ein, tax_year, filer_name, receipt_amt, govt_amt,
                                    contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                    conferences_amt, officer_comp, comp_pct, comp_ptile,
                                    travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                    grants_pct, grants_ptile, foreign_expenses_pct,
                                    foreign_expenses_ptile, grift_ratio, total_assets, form_type,
                                    denominator, foreign_office, foreign_expenses, grants_to_others,
                                    domestic_misrep_flag, xml_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (charity_id, charity.ein, charity.tax_year, charity.filer_name, charity.receipt_amt, charity.govt_amt,
              charity.contrib_amt, charity.org_type, charity.total_exp, charity.prog_exp,
              charity.travel_amt, charity.conferences_amt, charity.officer_comp, charity.comp_pct,
              charity.comp_ptile, charity.travel_pct, charity.travel_ptile, charity.conferences_pct,
              charity.conferences_ptile, charity.grants_pct, charity.grants_ptile,
              charity.foreign_expenses_pct, charity.foreign_expenses_ptile, charity.grift_ratio,
              charity.total_assets, charity.form_type, charity.denominator, charity.foreign_office,
              charity.foreign_expenses, charity.grants_to_others, charity.domestic_misrep_flag,
              charity.xml_name))
        charity.charity_id = charity_id
        self.commit()
        return charity_id

    def update_charity_percentiles(self, ein: str, tax_year: int, comp_ptile: float = None,
                                    travel_ptile: float = None, conferences_ptile: float = None,
                                    grants_ptile: float = None, foreign_ptile: float = None):
        """Update charity percentile rankings"""
        self.execute_query("""
            UPDATE Charities SET
                comp_ptile_value = ?,
                travel_ptile_value = ?,
                conferences_ptile_value = ?,
                grants_ptile_value = ?,
                foreign_expenses_ptile_value = ?
            WHERE ein = ? AND tax_year = ?
        """, (comp_ptile, travel_ptile, conferences_ptile, grants_ptile, foreign_ptile, ein, tax_year))
        self.commit()

    def get_charities_for_percentiles(self) -> List[Tuple]:
        """Get charities for percentile calculation"""
        # This method returns specific fields as tuples, not Charity instances
        # So we keep the original implementation
        result = self.execute_query("""
            SELECT org_type, tax_year, ein, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct
            FROM Charities
            WHERE denominator > 0
            ORDER BY org_type, tax_year
        """)
        return result.fetchall()

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
        """Insert Grant into database. Returns UUID."""
        grant_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT OR REPLACE INTO Grants (grant_id, filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                                filer_colocator, grantee_colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (grant_id, grant.filer_ein, grant.filer_name, grant.grant_ein, grant.grant_amt,
              grant.tax_year, grant.filer_colocator, grant.grantee_colocator))
        grant.grant_id = grant_id
        self.commit()
        return grant_id

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
        """Insert Officer into database. Returns UUID."""
        # Debug: Validate charity_id before insertion
        if officer.charity_id is None or officer.charity_id <= 0:
            raise ValueError(f"Invalid charity_id {officer.charity_id} for officer {officer.first_name} {officer.last_name}")

        # Debug: Check if charity_id exists in Charities table
        result = self.execute_query("SELECT 1 FROM Charities WHERE charity_id = ?", (officer.charity_id,))
        if not result.fetchone():
            raise ValueError(f"charity_id {officer.charity_id} does not exist in Charities table for officer {officer.first_name} {officer.last_name}")

        officer_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT OR REPLACE INTO Officers (officer_id, charity_id, first_name, last_name, compensation, tax_year)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (officer_id, officer.charity_id, officer.first_name, officer.last_name,
              officer.compensation, officer.tax_year))
        officer.officer_id = officer_id
        self.commit()
        return officer_id

    # Contractor operations
    def insert_contractor(self, contractor: Contractor) -> str:
        """Insert Contractor into database. Returns UUID."""
        contractor_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT OR REPLACE INTO Contractors (contractor_id, filer_ein, name, amount, ein, address, zip_code,
                                      po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (contractor_id, contractor.filer_ein, contractor.name, contractor.amount, contractor.ein,
              contractor.address, contractor.zip_code, contractor.po_box, contractor.tax_year, contractor.colocator))
        contractor.contractor_id = contractor_id
        self.commit()
        return contractor_id

    # Political Contribution operations
    def insert_political_contribution(self, contribution: PoliticalContribution) -> str:
        """Insert PoliticalContribution into database. Returns UUID."""
        political_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT OR REPLACE INTO PoliticalContributions (political_id, filer_ein, recipient, amount,
                                                recipient_address, recipient_zip,
                                                recipient_po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (political_id, contribution.filer_ein, contribution.recipient, contribution.amount,
              contribution.recipient_address, contribution.recipient_zip,
              contribution.recipient_po_box, contribution.tax_year, contribution.colocator))
        contribution.political_id = political_id
        self.commit()
        return political_id

    # Geocoding operations
    def insert_geocoding_record(self, address_hash: str, normalized_address: str,
                                latitude: float = None, longitude: float = None,
                                status: str = 'success') -> str:
        """Insert geocoding record. Returns UUID."""
        geocoding_id = self.generate_uuid_v7()
        self.execute_query("""
            INSERT INTO Geocoding (geocoding_id, address_hash, normalized_address, latitude, longitude, geocoding_status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (geocoding_id, address_hash, normalized_address, latitude, longitude, status))
        self.commit()
        return geocoding_id

    # Bulk operations
    def bulk_insert_xml_files(self, xml_files: List[XMLFile]):
        """Bulk insert XML files using DuckDB's efficient bulk insert"""
        xml_data = [(self.generate_uuid_v7(), xml.zip_id, xml.filename, xml.internal_path, xml.ein, xml.tax_year,
                     xml.form_type, xml.processed, xml.processing_version, xml.error_message)
                    for xml in xml_files]

        # Use DuckDB's VALUES clause for bulk insert
        placeholders = ', '.join(['(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'] * len(xml_data))
        flat_data = [item for sublist in xml_data for item in sublist]

        query = f"""
            INSERT INTO XmlFiles (xml_id, zip_id, filename, internal_path, ein, tax_year,
                                 form_type, processed, processing_version, error_message)
            VALUES {placeholders}
        """

        self.execute_query(query, tuple(flat_data))
        self.commit()

    def get_bulk_operations(self):
        """Get bulk operations handler"""
        # DuckDB handles bulk operations internally
        return self

    # Export operations
    def get_export_operations(self, final_dir: str):
        """Get export operations handler"""
        # DuckDB handles export operations through its own methods
        return self

    # Analytics methods for DuckDB
    def get_charity_summary_stats(self, tax_year: int = None) -> Dict[str, Any]:
        """Get summary statistics for charities"""
        where_clause = f"WHERE tax_year = {tax_year}" if tax_year else ""

        query = f"""
            SELECT
                COUNT(*) as total_charities,
                AVG(total_exp) as avg_expenses,
                SUM(total_exp) as total_expenses,
                AVG(officer_comp) as avg_officer_comp,
                COUNT(CASE WHEN foreign_office = 'Y' THEN 1 END) as foreign_offices
            FROM Charities
            {where_clause}
        """

        result = self.execute_query(query).fetchone()
        if result:
            # DuckDB returns tuples, not dict-like objects
            return {
                'total_charities': result[0],
                'avg_expenses': result[1],
                'total_expenses': result[2],
                'avg_officer_comp': result[3],
                'foreign_offices': result[4]
            }
        return {}

    def get_top_grant_recipients(self, limit: int = 10) -> List[Tuple]:
        """Get top grant recipients by total amount"""
        # This method performs aggregation, so we keep the original implementation
        result = self.execute_query("""
            SELECT grant_ein, SUM(grant_amt) as total_grants, COUNT(*) as grant_count
            FROM Grants
            WHERE grant_ein IS NOT NULL AND grant_ein != ''
            GROUP BY grant_ein
            ORDER BY total_grants DESC
            LIMIT ?
        """, (limit,))
        return result.fetchall()

    def get_geocoding_stats(self) -> Dict[str, int]:
        """Get geocoding completion statistics"""
        result = self.execute_query("""
            SELECT
                COUNT(*) as total_addresses,
                COUNT(CASE WHEN geocoding_id IS NOT NULL THEN 1 END) as geocoded,
                COUNT(CASE WHEN geocoding_id IS NULL THEN 1 END) as pending
            FROM Addresses
        """).fetchone()
        if result:
            return {
                'total_addresses': result[0],
                'geocoded': result[1],
                'pending': result[2]
            }
        return {}

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get database performance statistics"""
        stats = {}

        # Get table sizes
        table_sizes = self.execute_query("""
            SELECT table_name,
                   estimated_size as size_bytes,
                   ROUND(estimated_size / 1024.0 / 1024.0, 2) as size_mb
            FROM duckdb_tables()
            WHERE table_name IN ('Charities', 'Grants', 'Addresses', 'Officers', 'Geocoding')
        """).fetchall()

        stats['table_sizes'] = {row[0]: {'bytes': row[1], 'mb': row[2]} for row in table_sizes}

        # Get query performance info
        try:
            # This would require PRAGMA statements or system tables
            # For now, return basic stats
            stats['memory_usage'] = self.memory_limit
            stats['threads'] = self.threads or 'auto'
        except:
            pass

        return stats

    def optimize_database(self):
        """Run database optimization commands"""
        # Analyze tables for better query planning
        self.execute_query("ANALYZE Charities")
        self.execute_query("ANALYZE Grants")
        self.execute_query("ANALYZE Addresses")
        self.execute_query("ANALYZE Officers")
        self.execute_query("ANALYZE Geocoding")

        # Checkpoint to ensure data is written
        self.db_conn.checkpoint()
        self.commit()