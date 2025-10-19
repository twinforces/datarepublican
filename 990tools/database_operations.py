#!/usr/bin/env python3
"""
database_operations.py - Database operations for IRS 990 data processing

This module contains all database-related operations for the IRS 990 processor,
including CRUD operations for all data models.
"""

import sqlite3
from typing import Optional, List, Tuple
from datetime import datetime
from pathlib import Path

import sys
import os
sys.path.append(os.path.dirname(__file__))
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

# Import all dataclasses from irs990processorDC
from irs990processorDC import Address, ZipFile, XMLFile, Charity, Grant, Officer, Contractor, PoliticalContribution


class DatabaseOperations:
    """Handles all database operations for IRS 990 data processing"""

    # Valid US state and territory abbreviations
    VALID_STATES = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW', 'AA', 'AE', 'AP'}

    def __init__(self, db_path: str, log_sql: bool = False):
        self.db_path = db_path
        self.log_sql = log_sql
        self.db_conn: sqlite3.Connection
        self.db_cursor: sqlite3.Cursor
        self._init_connection()

    def _init_connection(self):
        """Initialize database connection and schema"""
        self.db_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db_cursor = self.db_conn.cursor()
        # Enable foreign keys
        self.db_cursor.execute("PRAGMA foreign_keys = ON")
        # Enable SQL logging if requested
        if self.log_sql:
            self.db_conn.set_trace_callback(print)
        # Initialize schema if needed
        self._init_schema()

    def _init_schema(self):
        """Initialize database schema if not already present"""
        # Check if schema is already initialized
        self.db_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ZipFiles'")
        if self.db_cursor.fetchone():
            return  # Schema already exists

        # Read and execute schema.sql
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        try:
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            self.db_cursor.executescript(schema_sql)
            self.db_conn.commit()
        except Exception as e:
            print(f"Failed to initialize database schema: {e}")
            raise

    def __del__(self):
        """Cleanup database connection"""
        if hasattr(self, 'db_conn') and self.db_conn:
            self.db_conn.close()

    # ZipFile operations
    def insert_zip_file(self, zip_file: ZipFile) -> int:
        """Insert ZipFile into database, handling duplicates"""
        self.db_cursor.execute("""
            INSERT OR IGNORE INTO ZipFiles (filename, file_path, tax_year, file_size, checksum,
                                download_date, processed_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (zip_file.filename, zip_file.file_path, zip_file.tax_year,
              zip_file.file_size, zip_file.checksum, zip_file.download_date.isoformat() if zip_file.download_date else None,
              zip_file.processed_date.isoformat() if zip_file.processed_date else None, zip_file.status))
        zip_file.zip_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return zip_file.zip_id

    def update_zip_status(self, zip_id: int, status: str):
        """Update ZIP file processing status"""
        self.db_cursor.execute("""
            UPDATE ZipFiles SET status = ?, processed_date = ?
            WHERE zip_id = ?
        """, (status, datetime.now().isoformat(), zip_id))
        self.db_conn.commit()

    # XMLFile operations
    def insert_xml_file(self, xml_file: XMLFile) -> int:
        """Insert XMLFile into database, handling duplicates"""
        self.db_cursor.execute("""
            INSERT OR IGNORE INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year,
                                form_type, processed, processing_version, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (xml_file.zip_id, xml_file.filename, xml_file.internal_path,
              xml_file.ein, xml_file.tax_year, xml_file.form_type,
              xml_file.processed, xml_file.processing_version, xml_file.error_message))
        xml_file.xml_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return xml_file.xml_id

    def get_unprocessed_xml_files(self, processing_version: int, max_files: Optional[int] = None) -> List[Tuple]:
        """Get unprocessed XML files"""
        query = """
            SELECT xml_id, zip_id, filename, internal_path
            FROM XmlFiles
            WHERE processed = FALSE OR processing_version < ?
            ORDER BY zip_id, filename
        """
        params = (processing_version,)

        if max_files:
            query += " LIMIT ?"
            params = (processing_version, max_files)

        self.db_cursor.execute(query, params)
        return self.db_cursor.fetchall()

    def mark_xml_processed(self, xml_id: int, processing_version: int):
        """Mark XML file as processed"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?
            WHERE xml_id = ?
        """, (processing_version, xml_id))
        self.db_conn.commit()

    def mark_xml_error(self, xml_id: int, processing_version: int, error_msg: str):
        """Mark XML file as having an error"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (processing_version, error_msg, xml_id))
        self.db_conn.commit()

    def update_xml_ein(self, xml_id: int, ein: str):
        """Update XML file with EIN after parsing"""
        self.db_cursor.execute("UPDATE XmlFiles SET ein = ? WHERE xml_id = ?", (ein, xml_id))
        self.db_conn.commit()

    # Address operations
    def insert_address(self, address: Address) -> int:
        """Insert Address into database, avoiding duplicates"""
        # Use the colocator that was set by __post_init__ instead of recalculating
        colocator = address.colocator

        # Debug logging
        if hasattr(address, 'ein') and address.ein:
            print(f"DEBUG: Inserting address for EIN {address.ein}: line1='{address.address_line1}', line2='{address.address_line2}', city='{address.city}', state='{address.state}', zip='{address.zip_code}', po_box='{address.po_box}', canonical='{address.canonical_address}', colocator='{colocator}'")
            # Log the final colocator value being inserted
            print(f"DEBUG insert_address: Final colocator value being inserted: '{colocator}' (from address.colocator)")

        sql = """
            INSERT OR IGNORE INTO Addresses (ein, name, address_line1, address_line2, city, state, zip_code, po_box,
                                  canonical_address, address_type, geocoding_id, latitude, longitude, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        values = (address.ein, address.name, getattr(address, 'address_line1', None), getattr(address, 'address_line2', None),
                  getattr(address, 'city', None), getattr(address, 'state', None),
                  address.zip_code, address.po_box, address.canonical_address,
                  address.address_type, address.geocoding_id, address.latitude, address.longitude, colocator)

        # Log the SQL statement and values
        print(f"DEBUG insert_address: SQL: {sql.strip()}")
        print(f"DEBUG insert_address: Values: {values}")

        self.db_cursor.execute(sql, values)
        address.address_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return address.address_id

    def get_addresses_for_geocoding(self) -> List[Tuple]:
        """Get addresses that need geocoding"""
        self.db_cursor.execute("""
            SELECT address_id, canonical_address, po_box, zip_code
            FROM Addresses
            WHERE geocoding_id IS NULL
            AND (po_box IS NULL OR po_box = '')  -- Skip PO boxes
            ORDER BY address_id
        """)
        return self.db_cursor.fetchall()

    def update_address_geocoding(self, address_id: int, geocoding_id: int = None, colocator: str = None):
        """Update address with geocoding information"""
        self.db_cursor.execute("""
            UPDATE Addresses SET geocoding_id = ?, colocator = ?
            WHERE address_id = ?
        """, (geocoding_id, colocator, address_id))
        self.db_conn.commit()

    # Charity operations
    def insert_charity(self, charity: Charity) -> int:
        """Insert Charity into database, handling duplicates by EIN and tax_year"""
        self.db_cursor.execute("""
            INSERT OR IGNORE INTO Charities (ein, tax_year, filer_name, receipt_amt, govt_amt,
                                  contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                  conferences_amt, officer_comp, comp_pct, comp_ptile,
                                  travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                  grants_pct, grants_ptile, foreign_expenses_pct,
                                  foreign_expenses_ptile, grift_ratio, total_assets, form_type,
                                  denominator, foreign_office, foreign_expenses, grants_to_others,
                                  domestic_misrep_flag, xml_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (charity.ein, charity.tax_year, charity.filer_name, charity.receipt_amt, charity.govt_amt,
              charity.contrib_amt, charity.org_type, charity.total_exp, charity.prog_exp,
              charity.travel_amt, charity.conferences_amt, charity.officer_comp, charity.comp_pct,
              charity.comp_ptile, charity.travel_pct, charity.travel_ptile, charity.conferences_pct,
              charity.conferences_ptile, charity.grants_pct, charity.grants_ptile,
              charity.foreign_expenses_pct, charity.foreign_expenses_ptile, charity.grift_ratio,
              charity.total_assets, charity.form_type, charity.denominator, charity.foreign_office,
              charity.foreign_expenses, charity.grants_to_others, charity.domestic_misrep_flag,
              charity.xml_name))
        charity.charity_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return charity.charity_id

    def update_charity_percentiles(self, ein: str, tax_year: int, comp_ptile: float = None,
                                  travel_ptile: float = None, conferences_ptile: float = None,
                                  grants_ptile: float = None, foreign_ptile: float = None):
        """Update charity percentile rankings"""
        self.db_cursor.execute("""
            UPDATE Charities SET
                comp_ptile_value = ?,
                travel_ptile_value = ?,
                conferences_ptile_value = ?,
                grants_ptile_value = ?,
                foreign_expenses_ptile_value = ?
            WHERE ein = ? AND tax_year = ?
        """, (comp_ptile, travel_ptile, conferences_ptile, grants_ptile, foreign_ptile, ein, tax_year))
        self.db_conn.commit()

    def get_charities_for_percentiles(self) -> List[Tuple]:
        """Get charities for percentile calculation"""
        self.db_cursor.execute("""
            SELECT org_type, tax_year, ein, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct
            FROM Charities
            WHERE denominator > 0
            ORDER BY org_type, tax_year
        """)
        return self.db_cursor.fetchall()

    def create_latest_charities_table(self):
        """Create LatestCharities table with most recent filings"""
        self.db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS LatestCharities AS
            SELECT c.*
            FROM Charities c
            INNER JOIN (
                SELECT ein, MAX(tax_year) as max_year
                FROM Charities
                GROUP BY ein
            ) latest ON c.ein = latest.ein AND c.tax_year = latest.max_year
        """)
        self.db_conn.commit()

    # Grant operations
    def insert_grant(self, grant: Grant) -> int:
        """Insert Grant into database"""
        self.db_cursor.execute("""
            INSERT OR REPLACE INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                              filer_colocator, grantee_colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (grant.filer_ein, grant.filer_name, grant.grant_ein, grant.grant_amt,
              grant.tax_year, grant.filer_colocator, grant.grantee_colocator))
        grant.grant_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return grant.grant_id

    def update_grant_ein(self, grant_id: int, grant_ein: str):
        """Update grant with matched EIN"""
        self.db_cursor.execute("""
            UPDATE Grants SET grant_ein = ? WHERE grant_id = ?
        """, (grant_ein, grant_id))
        self.db_conn.commit()

    def get_grants_without_ein(self) -> List[Tuple]:
        """Get grants with unknown EINs for matching"""
        self.db_cursor.execute("""
            SELECT grant_id, filer_ein, grant_amt, tax_year
            FROM Grants
            WHERE grant_ein IS NULL OR grant_ein = ''
        """)
        return self.db_cursor.fetchall()

    # Officer operations
    def insert_officer(self, officer: Officer) -> int:
        """Insert Officer into database"""
        # Debug: Validate charity_id before insertion
        if officer.charity_id is None or officer.charity_id <= 0:
            raise ValueError(f"Invalid charity_id {officer.charity_id} for officer {officer.first_name} {officer.last_name}")

        # Debug: Check if charity_id exists in Charities table
        self.db_cursor.execute("SELECT 1 FROM Charities WHERE charity_id = ?", (officer.charity_id,))
        if not self.db_cursor.fetchone():
            raise ValueError(f"charity_id {officer.charity_id} does not exist in Charities table for officer {officer.first_name} {officer.last_name}")

        self.db_cursor.execute("""
            INSERT OR REPLACE INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
            VALUES (?, ?, ?, ?, ?)
        """, (officer.charity_id, officer.first_name, officer.last_name,
              officer.compensation, officer.tax_year))
        officer.officer_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return officer.officer_id

    # Contractor operations
    def insert_contractor(self, contractor: Contractor) -> int:
        """Insert Contractor into database"""
        self.db_cursor.execute("""
            INSERT OR REPLACE INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                    po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (contractor.filer_ein, contractor.name, contractor.amount, contractor.ein,
              contractor.address, contractor.zip_code, contractor.po_box, contractor.tax_year, contractor.colocator))
        contractor.contractor_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return contractor.contractor_id

    # Political Contribution operations
    def insert_political_contribution(self, contribution: PoliticalContribution) -> int:
        """Insert PoliticalContribution into database"""
        self.db_cursor.execute("""
            INSERT OR REPLACE INTO PoliticalContributions (filer_ein, recipient, amount,
                                              recipient_address, recipient_zip,
                                              recipient_po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (contribution.filer_ein, contribution.recipient, contribution.amount,
              contribution.recipient_address, contribution.recipient_zip,
              contribution.recipient_po_box, contribution.tax_year, contribution.colocator))
        contribution.political_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return contribution.political_id

    # Geocoding operations
    def insert_geocoding_record(self, address_hash: int, normalized_address: str,
                               latitude: float = None, longitude: float = None,
                               status: str = 'success') -> int:
        """Insert geocoding record"""
        self.db_cursor.execute("""
            INSERT INTO Geocoding (address_hash, normalized_address, latitude, longitude, geocoding_status)
            VALUES (?, ?, ?, ?, ?)
        """, (address_hash, normalized_address, latitude, longitude, status))
        geocoding_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return geocoding_id
    # Import bulk operations
    try:
        from bulk_operations import BulkOperations
    except ImportError:
        BulkOperations = None
    else:
        # Make it available as a class attribute
        DatabaseOperations.BulkOperations = BulkOperations

    def bulk_insert_xml_files(self, xml_files: List[XMLFile]):
        """Bulk insert XML files"""
        xml_data = [(xml.zip_id, xml.filename, xml.internal_path, xml.ein, xml.tax_year,
                    xml.form_type, xml.processed, xml.processing_version, xml.error_message)
                   for xml in xml_files]

        self.db_cursor.executemany("""
            INSERT INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year,
                                form_type, processed, processing_version, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, xml_data)
        self.db_conn.commit()

    def get_bulk_operations(self):
        """Get bulk operations handler"""
        if DatabaseOperations.BulkOperations is None:
            raise ImportError("BulkOperations module not available")
        return DatabaseOperations.BulkOperations(self)

    # Import export operations
    try:
        from export_processor import TSVExporter
    except ImportError:
        TSVExporter = None
    else:
        # Make it available as a class attribute
        DatabaseOperations.TSVExporter = TSVExporter

    def get_export_operations(self, final_dir: str):
        """Get export operations handler"""
        if DatabaseOperations.TSVExporter is None:
            raise ImportError("TSVExporter module not available")
        return DatabaseOperations.TSVExporter(self, final_dir)