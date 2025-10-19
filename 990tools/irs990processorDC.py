#!/usr/bin/env python3
"""
990processorDC.py - @dataclass classes and database utilities for Comprehensive IRS 990 data processing module

This module replaces the collection of separate scripts with a unified,
database-driven processing pipeline for IRS Form 990 data.

Key Features:
- Dataclass-based data models for type safety and clarity
- SQLite database storage with proper relationships
- Geolocation using censusgeocode API
- Comprehensive error handling and logging
- Threaded processing for performance
"""

from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import datetime
import sqlite3
import os

# Valid US state and territory abbreviations
VALID_STATES = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW', 'AA', 'AE', 'AP'}


@dataclass
class ZipFile:
    """Represents a ZIP file containing IRS 990 XML files"""
    zip_id: Optional[int] = None
    filename: str = ""
    file_path: str = ""
    tax_year: int = 0
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    download_date: Optional[datetime] = None
    processed_date: Optional[datetime] = None
    status: str = "downloaded"
    xml_files: List['XMLFile'] = field(default_factory=list)

@dataclass
class XMLFile:
    """Represents an individual XML file within a ZIP"""
    xml_id: Optional[int] = None
    zip_id: int = 0
    filename: str = ""
    internal_path: str = ""
    ein: Optional[str] = None
    tax_year: Optional[int] = None
    form_type: Optional[str] = None
    processed: bool = False
    processing_version: int = 0
    error_message: Optional[str] = None

@dataclass
class Address:
    """Represents a physical address"""
    address_id: Optional[int] = None
    ein: str = ""
    name: str = ""
    canonical_address: str = ""
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    po_box: Optional[str] = None
    address_type: str = "filer"  # 'filer' or 'grantee'
    geocoding_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    colocator: Optional[str] = None  # LL:lat:long, PO:box:zip, FA:country_code

    def __post_init__(self):
        # Initialize private backing field for colocator property
        self._colocator = None

    @property
    def colocator(self):
        if self.po_box and self.zip_code:
            po_box_stripped = self.po_box.strip()
            if po_box_stripped:
                return f"PO:{po_box_stripped}:{self.zip_code}"
        elif self.state and self.state.upper() not in VALID_STATES:
            return f"FA:{self.state}"
        return self._colocator

    @colocator.setter
    def colocator(self, value):
        self._colocator = value

    def insert(self, conn) -> int:
        """Insert this address into the database"""
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Addresses (ein, name, canonical_address, address_line1, address_line2, city, state, zip_code, po_box, address_type, geocoding_id, latitude, longitude, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.ein, self.name, self.canonical_address, self.address_line1, self.address_line2, self.city, self.state, self.zip_code, self.po_box,
              self.address_type, self.geocoding_id, self.latitude, self.longitude, self.colocator))
        self.address_id = cursor.lastrowid
        return self.address_id


class DatabaseConsumer:
    """Database consumer for threaded XML processing"""

    def __init__(self, db_path: str, logger):
        self.db_path = db_path
        self.logger = logger

    def consume_batch(self, xml_queue, result_queue, batch_size: int = 100):
        """Consume XML processing results and insert into database"""
        # Create database connection for consumer thread
        consumer_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        consumer_cursor = consumer_conn.cursor()
        consumer_conn.execute("PRAGMA foreign_keys = ON")
        self.logger.debug("Consumer database connection established")

        batch_data = []
        total_processed = 0
        shutdown_signals_received = 0

        while True:
            try:
                self.logger.debug("Consumer waiting for item from xml_queue...")
                item = xml_queue.get(timeout=5.0)
                self.logger.debug(f"Consumer received item: {type(item)}")

                if item is None:  # Shutdown signal
                    shutdown_signals_received += 1
                    self.logger.debug(f"Consumer received shutdown signal #{shutdown_signals_received}")
                    if shutdown_signals_received >= 3:  # Need multiple shutdown signals
                        break
                    continue

                if item == ('error', None):
                    continue

                if isinstance(item, tuple) and item[0] == 'error':
                    # Mark XML as processed with error
                    xml_id = item[1]
                    consumer_cursor.execute("""
                        UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
                        WHERE xml_id = ?
                    """, (2, "Processing error", xml_id))  # CURRENT_PROCESSING_VERSION
                    consumer_conn.commit()
                    continue

                # Add to batch
                batch_data.append(item)
                total_processed += 1

                # Process batch when it gets large enough
                if len(batch_data) >= batch_size:
                    self.logger.debug(f"Consumer: processing batch of {len(batch_data)} items (total: {total_processed})")
                    self._bulk_insert_batch(batch_data, consumer_conn, consumer_cursor)
                    result_queue.put(len(batch_data))
                    batch_data = []
                    time.sleep(0.01)  # Small delay to prevent busy waiting

            except Exception as e:
                self.logger.error(f"Consumer error: {e}", exc_info=True)
                break

        # Process remaining batch
        if batch_data:
            self.logger.debug(f"Consumer: processing final batch of {len(batch_data)} items (total: {total_processed})")
            self._bulk_insert_batch(batch_data, consumer_conn, consumer_cursor)
            result_queue.put(len(batch_data))

        self.logger.debug(f"Consumer: completed, processed {total_processed} total items")

        # Close consumer connection
        consumer_conn.close()

    def _bulk_insert_batch(self, batch_data, conn=None, cursor=None):
        """Bulk insert a batch of processed XML data"""
        # Import dataclasses here to avoid circular imports
        from irs990processorDC import Charity, Officer, Grant, Contractor, PoliticalContribution

        charities = []
        officers = []
        grants = []
        contractors = []
        contributions = []

        # Deduplicate charities by (ein, tax_year) - keep the one with the latest XML filename
        charity_map = {}
        for charity, officer_list, grant_list, contractor_list, contribution_list in batch_data:
            if charity:
                key = (charity.ein, charity.tax_year)
                # Compare XML filenames to keep the latest one (assuming sequential naming)
                if key not in charity_map or charity.xml_name > charity_map[key][0].xml_name:
                    charity_map[key] = (charity, officer_list, grant_list, contractor_list, contribution_list)

        # Process deduplicated charities
        for charity, officer_list, grant_list, contractor_list, contribution_list in charity_map.values():
            charities.append(charity)
            charity_id = len(charities)  # Temporary ID for batch processing

            for officer in officer_list:
                officer.charity_id = charity_id
                officers.append(officer)

            for grant in grant_list:
                grants.append(grant)

            for contractor in contractor_list:
                contractors.append(contractor)

            for contribution in contribution_list:
                contributions.append(contribution)

        self.logger.debug(f"Bulk insert batch: {len(charities)} charities (deduplicated), {len(officers)} officers, {len(grants)} grants, {len(contractors)} contractors, {len(contributions)} contributions")

        # Bulk insert charities
        if charities:
            charity_data = [(c.ein, c.tax_year, c.filer_name, c.receipt_amt, c.govt_amt,
                            c.contrib_amt, c.org_type, c.total_exp, c.prog_exp, c.travel_amt,
                            c.conferences_amt, c.officer_comp, c.comp_pct, c.comp_ptile,
                            c.travel_pct, c.travel_ptile, c.conferences_pct, c.conferences_ptile,
                            c.grants_pct, c.grants_ptile, c.foreign_expenses_pct,
                            c.foreign_expenses_ptile, c.grift_ratio, c.total_assets,
                            c.form_type, c.denominator, c.foreign_office, c.foreign_expenses,
                            c.grants_to_others, c.domestic_misrep_flag, c.xml_name)
                           for c in charities]

            try:
                cursor.executemany("""
                    INSERT OR IGNORE INTO Charities (ein, tax_year, filer_name, receipt_amt, govt_amt,
                                          contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                          conferences_amt, officer_comp, comp_pct, comp_ptile,
                                          travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                          grants_pct, grants_ptile, foreign_expenses_pct,
                                          foreign_expenses_ptile, grift_ratio, total_assets,
                                          form_type, denominator, foreign_office, foreign_expenses,
                                          grants_to_others, domestic_misrep_flag, xml_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, charity_data)
                self.logger.debug(f"Inserted {len(charity_data)} charities")
            except Exception as e:
                self.logger.error(f"Failed to insert charities: {e}", exc_info=True)
                conn.rollback()
                return

            # Get the charity IDs for related data
            if charities:
                ein_list = [c.ein for c in charities]
                tax_year_list = [c.tax_year for c in charities]
                placeholders = ','.join('?' for _ in ein_list)
                cursor.execute(f"""
                    SELECT charity_id FROM Charities
                    WHERE ein IN ({placeholders}) AND tax_year IN ({placeholders})
                    ORDER BY charity_id
                """, ein_list + tax_year_list)
                charity_ids = [row[0] for row in cursor.fetchall()]
            else:
                charity_ids = []

            # Bulk insert officers
            if officers:
                officer_data = []
                for officer in officers:
                    batch_index = officer.charity_id - 1
                    if 0 <= batch_index < len(charity_ids):
                        actual_charity_id = charity_ids[batch_index]
                        officer_data.append((actual_charity_id, officer.first_name, officer.last_name,
                                           officer.compensation, officer.tax_year))

                try:
                    cursor.executemany("""
                        INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
                        VALUES (?, ?, ?, ?, ?)
                    """, officer_data)
                    self.logger.debug(f"Inserted {len(officer_data)} officers")
                except Exception as e:
                    self.logger.error(f"Failed to insert officers: {e}", exc_info=True)

            # Bulk insert grants
            if grants:
                grant_data = [(g.filer_ein, g.filer_name, g.grant_ein, g.grant_amt, g.tax_year,
                              g.filer_colocator, g.grantee_colocator) for g in grants]
                try:
                    cursor.executemany("""
                        INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                                           filer_colocator, grantee_colocator)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, grant_data)
                    self.logger.debug(f"Inserted {len(grant_data)} grants")
                except Exception as e:
                    self.logger.error(f"Failed to insert grants: {e}", exc_info=True)

            # Bulk insert contractors
            if contractors:
                contractor_data = [(c.filer_ein, c.name, c.amount, c.ein, c.address, c.zip_code,
                                  c.po_box, c.tax_year) for c in contractors]
                try:
                    cursor.executemany("""
                        INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                               po_box, tax_year)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, contractor_data)
                    self.logger.debug(f"Inserted {len(contractor_data)} contractors")
                except Exception as e:
                    self.logger.error(f"Failed to insert contractors: {e}", exc_info=True)

            # Bulk insert political contributions
            if contributions:
                contribution_data = [(c.filer_ein, c.recipient, c.amount, c.recipient_address,
                                    c.recipient_zip, c.recipient_po_box, c.tax_year) for c in contributions]
                try:
                    cursor.executemany("""
                        INSERT INTO PoliticalContributions (filer_ein, recipient, amount,
                                                          recipient_address, recipient_zip,
                                                          recipient_po_box, tax_year)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, contribution_data)
                    self.logger.debug(f"Inserted {len(contribution_data)} contributions")
                except Exception as e:
                    self.logger.error(f"Failed to insert contributions: {e}", exc_info=True)

        try:
            conn.commit()
            self.logger.debug("Batch commit successful")
        except Exception as e:
            self.logger.error(f"Failed to commit batch: {e}", exc_info=True)
            conn.rollback()


class DatabaseManager:
    """Database initialization and management utilities"""

    @staticmethod
    def init_database(db_path: str, schema_path: str = None) -> sqlite3.Connection:
        """Initialize SQLite database with schema"""
        if schema_path is None:
            schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')

        # Check if database is already initialized
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ZipFiles'")
            if not cursor.fetchone():
                print("Database schema not found, initializing...")
                # Read and execute schema.sql
                with open(schema_path, 'r') as f:
                    schema_sql = f.read()
                cursor.executescript(schema_sql)
                conn.commit()
                print("Database schema initialized successfully")
            else:
                print("Database schema already exists")
        except Exception as e:
            print(f"Failed to initialize database: {e}")
            raise

        return conn

@dataclass
class Charity:
    """Represents a charity organization"""
    charity_id: Optional[int] = None
    ein: str = ""
    tax_year: int = 0
    filer_name: str = ""
    receipt_amt: Optional[float] = None
    govt_amt: Optional[float] = None
    contrib_amt: Optional[float] = None
    org_type: str = ""
    total_exp: Optional[float] = None
    prog_exp: Optional[float] = None
    travel_amt: Optional[float] = None
    conferences_amt: Optional[float] = None
    officer_comp: Optional[float] = None
    comp_pct: Optional[float] = None
    comp_ptile: Optional[float] = None
    travel_pct: Optional[float] = None
    travel_ptile: Optional[float] = None
    conferences_pct: Optional[float] = None
    conferences_ptile: Optional[float] = None
    grants_pct: Optional[float] = None
    grants_ptile: Optional[float] = None
    foreign_expenses_pct: Optional[float] = None
    foreign_expenses_ptile: Optional[float] = None
    grift_ratio: Optional[float] = None
    total_assets: Optional[float] = None
    form_type: str = ""
    denominator: Optional[float] = None
    foreign_office: bool = False
    foreign_expenses: Optional[float] = None
    grants_to_others: Optional[float] = None
    domestic_misrep_flag: bool = False
    xml_name: str = ""
    colocator: Optional[str] = None

    # Analysis fields
    comp_ptile_value: Optional[float] = None
    travel_ptile_value: Optional[float] = None
    conferences_ptile_value: Optional[float] = None
    grants_ptile_value: Optional[float] = None
    foreign_expenses_ptile_value: Optional[float] = None

@dataclass
class Grant:
    """Represents a grant from a charity"""
    grant_id: Optional[int] = None
    filer_ein: str = ""
    filer_name: str = ""
    grant_ein: Optional[str] = None
    grant_amt: float = 0.0
    tax_year: int = 0
    grantee_name: Optional[str] = None
    grantee_address: Optional[str] = None
    grantee_zip: Optional[str] = None
    grantee_po_box: Optional[str] = None
    filer_colocator: Optional[str] = None
    grantee_colocator: Optional[str] = None

    def insert(self, conn) -> int:
        """Insert this grant into the database"""
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                               filer_colocator, grantee_colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (self.filer_ein, self.filer_name, self.grant_ein, self.grant_amt, self.tax_year,
              self.filer_colocator, self.grantee_colocator))
        self.grant_id = cursor.lastrowid
        return self.grant_id

@dataclass
class Officer:
    """Represents an officer compensation record"""
    officer_id: Optional[int] = None
    charity_id: int = 0
    first_name: str = ""
    last_name: str = ""
    compensation: float = 0.0
    tax_year: int = 0

    def insert(self, conn) -> int:
        """Insert this officer into the database"""
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
            VALUES (?, ?, ?, ?, ?)
        """, (self.charity_id, self.first_name, self.last_name, self.compensation, self.tax_year))
        self.officer_id = cursor.lastrowid
        return self.officer_id

@dataclass
class Contractor:
    """Represents a contractor payment"""
    contractor_id: Optional[int] = None
    filer_ein: str = ""
    name: str = ""
    amount: float = 0.0
    ein: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None

    def insert(self, conn) -> int:
        """Insert this contractor into the database"""
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                   po_box, tax_year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.filer_ein, self.name, self.amount, self.ein, self.address, self.zip_code,
              self.po_box, self.tax_year))
        self.contractor_id = cursor.lastrowid
        return self.contractor_id

@dataclass
class PoliticalContribution:
    """Represents a political contribution"""
    political_id: Optional[int] = None
    filer_ein: str = ""
    recipient: str = ""
    amount: float = 0.0
    recipient_address: Optional[str] = None
    recipient_zip: Optional[str] = None
    recipient_po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None
