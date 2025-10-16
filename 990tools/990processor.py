#!/usr/bin/env python3
"""
990processor.py - Comprehensive IRS 990 data processing module

This module replaces the collection of separate scripts with a unified,
database-driven processing pipeline for IRS Form 990 data.

Key Features:
- Dataclass-based data models for type safety and clarity
- SQLite database storage with proper relationships
- Geolocation using censusgeocode API
- Comprehensive error handling and logging
- Threaded processing for performance
"""

import os
import sys
import sqlite3
import logging
import argparse
import zipfile
import json
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import hashlib
import requests
from lxml import etree as ET
from io import BytesIO
try:
    import censusgeocode as cg
except ImportError:
    cg = None
from nameparser import HumanName
from collections import defaultdict
import numpy as np
from tqdm import tqdm

# Import existing parsing utilities
from parse_utils import parse_int_field, parse_string_field, parse_schedule, clean_name, MONEY_PATTERN
import parse_utils
from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF, NAMESPACES
from countryCodes import lookupCC

# Import parsing modules
import parse_990
import parse_990ez
import parse_990pf

# Precompile XPaths used in parse_xml_file
FORM_TYPE_XPATHS = [
    ET.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces={'irs': 'http://www.irs.gov/efile'}),
    ET.XPath(".//ReturnHeader/ReturnTypeCd")
]
TAX_YEAR_XPATHS = [
    ET.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces={'irs': 'http://www.irs.gov/efile'}),
    ET.XPath(".//ReturnHeader/TaxYr")
]
FILER_EIN_XPATHS = [
    ET.XPath(".//irs:Filer/irs:EIN", namespaces={'irs': 'http://www.irs.gov/efile'}),
    ET.XPath(".//Filer/EIN")
]


# Constants
DEFAULT_DB_PATH = "irs990.db"
DEFAULT_ZIPS_DIR = "/Volumes/Data/irs_zips"
DEFAULT_OUT_DIR = "/Volumes/Data/tsvs"
DEFAULT_ANAL_DIR = "/Volumes/Data/atsvs"
DEFAULT_FINAL_DIR = "/Volumes/Data/final"

# Threading constants
MAX_WORKERS = 16
BATCH_SIZE = 5000
QUEUE_SIZE = 20000

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
    error_message: Optional[str] = None

@dataclass
class Address:
    """Represents a physical address"""
    address_id: Optional[int] = None
    ein: str = ""
    name: str = ""
    canonical_address: str = ""
    po_box: Optional[str] = None
    zip_code: Optional[str] = None
    address_type: str = "filer"  # 'filer' or 'grantee'
    geocoding_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    colocator: Optional[str] = None  # LL:lat:long, PO:box:zip, FA:country_code

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

@dataclass
class Officer:
    """Represents an officer compensation record"""
    officer_id: Optional[int] = None
    charity_id: int = 0
    first_name: str = ""
    last_name: str = ""
    compensation: float = 0.0
    tax_year: int = 0

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

class IRS990Processor:
    """Main processor class for IRS 990 data"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, zips_dir: str = DEFAULT_ZIPS_DIR,
                 out_dir: str = DEFAULT_OUT_DIR, anal_dir: str = DEFAULT_ANAL_DIR,
                 final_dir: str = DEFAULT_FINAL_DIR, verbose: bool = False):
        self.db_path = os.path.join(final_dir, "irs990.db") if db_path == DEFAULT_DB_PATH else db_path
        self.zips_dir = zips_dir
        self.out_dir = out_dir
        self.anal_dir = anal_dir
        self.final_dir = final_dir
        self.verbose = verbose

        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG if verbose else logging.WARNING)

        # Database connection
        self.db_conn: sqlite3.Connection
        self.db_cursor: sqlite3.Cursor

        # Threading
        self.executor = None
        self.queues: Dict[str, Queue] = {}

        # Initialize database
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with schema"""
        self.db_conn = sqlite3.connect(self.db_path)
        self.db_cursor = self.db_conn.cursor()
        # Ensure database is properly initialized
        assert self.db_conn is not None
        assert self.db_cursor is not None

        # Enable foreign keys
        self.db_cursor.execute("PRAGMA foreign_keys = ON")

        # Check if database is already initialized
        self.db_cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='Charities'
        """)
        if self.db_cursor.fetchone():
            self.log_info("Database already initialized, skipping schema creation")
            return

        # Read and execute schema
        schema_path = Path(__file__).parent / "schema.sql"
        with open(schema_path, 'r') as f:
            schema_sql = f.read()

        self.db_cursor.executescript(schema_sql)
        self.db_conn.commit()
        self.log_info("Database schema initialized")

    def __del__(self):
        """Cleanup database connection"""
        if self.db_conn:
            self.db_conn.close()

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context"""
        if ein:
            self.logger.error(f"EIN {ein}: {msg}", *args, exc_info=exc_info)
        else:
            self.logger.error(msg, *args, exc_info=exc_info)

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if ein:
            self.logger.info(f"EIN {ein}: {msg}", *args)
        else:
            self.logger.info(msg, *args)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if ein:
            self.logger.debug(f"EIN {ein}: {msg}", *args)
        else:
            self.logger.debug(msg, *args)

    # Database operations
    def insert_zip_file(self, zip_file: ZipFile) -> int:
        """Insert ZipFile into database, handling duplicates"""
        # Check if ZIP file already exists
        self.db_cursor.execute("SELECT zip_id FROM ZipFiles WHERE filename = ?", (zip_file.filename,))
        existing = self.db_cursor.fetchone()
        if existing:
            zip_file.zip_id = existing[0]
            return existing[0]

        self.db_cursor.execute("""
            INSERT INTO ZipFiles (filename, file_path, tax_year, file_size, checksum,
                                download_date, processed_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (zip_file.filename, zip_file.file_path, zip_file.tax_year,
              zip_file.file_size, zip_file.checksum, zip_file.download_date.isoformat() if zip_file.download_date else None,
              zip_file.processed_date.isoformat() if zip_file.processed_date else None, zip_file.status))
        zip_file.zip_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return zip_file.zip_id

    def insert_xml_file(self, xml_file: XMLFile) -> int:
        """Insert XMLFile into database, handling duplicates"""
        # Check if XML file already exists
        self.db_cursor.execute("SELECT xml_id FROM XmlFiles WHERE zip_id = ? AND filename = ?",
                              (xml_file.zip_id, xml_file.filename))
        existing = self.db_cursor.fetchone()
        if existing:
            xml_file.xml_id = existing[0]
            return existing[0]

        self.db_cursor.execute("""
            INSERT INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year,
                                form_type, processed, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (xml_file.zip_id, xml_file.filename, xml_file.internal_path,
              xml_file.ein, xml_file.tax_year, xml_file.form_type,
              xml_file.processed, xml_file.error_message))
        xml_file.xml_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return xml_file.xml_id

    def insert_address(self, address: Address) -> int:
        """Insert Address into database, avoiding duplicates"""
        # Check for existing address
        self.db_cursor.execute("""
            SELECT address_id FROM Addresses
            WHERE ein = ? AND canonical_address = ?
        """, (address.ein, address.canonical_address))

        existing = self.db_cursor.fetchone()
        if existing:
            address.address_id = existing[0]
            return existing[0]

        self.db_cursor.execute("""
            INSERT INTO Addresses (ein, name, canonical_address, po_box, zip_code,
                                 address_type, geocoding_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (address.ein, address.name, address.canonical_address, address.po_box,
              address.zip_code, address.address_type, address.geocoding_id))
        address.address_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return address.address_id

    def insert_charity(self, charity: Charity) -> int:
        """Insert Charity into database"""
        self.db_cursor.execute("""
            INSERT INTO Charities (ein, tax_year, filer_name, receipt_amt, govt_amt,
                                 contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                 conferences_amt, officer_comp, comp_pct, comp_ptile,
                                 travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                 grants_pct, grants_ptile, foreign_expenses_pct,
                                 foreign_expenses_ptile, grift_ratio, total_assets,
                                 form_type, denominator, foreign_office, foreign_expenses,
                                 grants_to_others, domestic_misrep_flag, xml_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (charity.ein, charity.tax_year, charity.filer_name, charity.receipt_amt,
              charity.govt_amt, charity.contrib_amt, charity.org_type, charity.total_exp,
              charity.prog_exp, charity.travel_amt, charity.conferences_amt,
              charity.officer_comp, charity.comp_pct, charity.comp_ptile,
              charity.travel_pct, charity.travel_ptile, charity.conferences_pct,
              charity.conferences_ptile, charity.grants_pct, charity.grants_ptile,
              charity.foreign_expenses_pct, charity.foreign_expenses_ptile,
              charity.grift_ratio, charity.total_assets, charity.form_type,
              charity.denominator, charity.foreign_office, charity.foreign_expenses,
              charity.grants_to_others, charity.domestic_misrep_flag, charity.xml_name))
        charity.charity_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return charity.charity_id

    def insert_grant(self, grant: Grant) -> int:
        """Insert Grant into database"""
        self.db_cursor.execute("""
            INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                              filer_colocator, grantee_colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (grant.filer_ein, grant.filer_name, grant.grant_ein, grant.grant_amt,
              grant.tax_year, grant.filer_colocator, grant.grantee_colocator))
        grant.grant_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return grant.grant_id

    def insert_officer(self, officer: Officer) -> int:
        """Insert Officer into database"""
        self.db_cursor.execute("""
            INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
            VALUES (?, ?, ?, ?, ?)
        """, (officer.charity_id, officer.first_name, officer.last_name,
              officer.compensation, officer.tax_year))
        officer.officer_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return officer.officer_id

    def insert_contractor(self, contractor: Contractor) -> int:
        """Insert Contractor into database"""
        self.db_cursor.execute("""
            INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                   po_box, tax_year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (contractor.filer_ein, contractor.name, contractor.amount, contractor.ein,
              contractor.address, contractor.zip_code, contractor.po_box, contractor.tax_year))
        contractor.contractor_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return contractor.contractor_id

    def insert_political_contribution(self, contribution: PoliticalContribution) -> int:
        """Insert PoliticalContribution into database"""
        self.db_cursor.execute("""
            INSERT INTO PoliticalContributions (filer_ein, recipient, amount,
                                             recipient_address, recipient_zip,
                                             recipient_po_box, tax_year)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (contribution.filer_ein, contribution.recipient, contribution.amount,
              contribution.recipient_address, contribution.recipient_zip,
              contribution.recipient_po_box, contribution.tax_year))
        contribution.political_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return contribution.political_id

    def process_zip_files(self, start_year: int, end_year: int):
        """Process ZIP files and register XML files (steps 2-4)"""
        self.log_info(f"Processing ZIP files from {start_year} to {end_year}")

        # Step 2: Read the directory with the zip files as specified in the args
        zip_files = []
        for year in range(start_year, end_year + 1):
            year_str = f"{year}"
            zip_pattern = f"{year}*.zip"
            for zip_path in Path(self.zips_dir).glob(zip_pattern):
                zip_files.append(zip_path)

        self.log_info(f"Found {len(zip_files)} ZIP files to process")

        # Step 3: Pull the list of zip files available from the IRS site, see if there are any new ones to be downloaded
        # For now, we'll work with existing files - download logic can be added later

        # Step 4: Use command line tools to get a listing of each zip file, and register the zip as ZipFile in the database,
        # and the contents as XMLFile
        with tqdm(total=len(zip_files), desc="Processing ZIP files") as pbar:
            for zip_path in zip_files:
                try:
                    self._process_single_zip(zip_path)
                    pbar.update(1)
                except Exception as e:
                    self.log_error(f"Failed to process ZIP {zip_path}: {e}", exc_info=True)
                    pbar.update(1)

    def _process_single_zip(self, zip_path: Path):
        """Process a single ZIP file"""
        zip_filename = zip_path.name
        zip_year = int(zip_filename[:4]) if zip_filename[:4].isdigit() else 0

        # Create ZipFile object
        zip_file = ZipFile(
            filename=zip_filename,
            file_path=str(zip_path),
            tax_year=zip_year,
            file_size=zip_path.stat().st_size if zip_path.exists() else None
        )

        # Use filename + size as simple integrity check (no checksum needed)
        if zip_path.exists():
            zip_file.checksum = f"{zip_path.name}:{zip_path.stat().st_size}"

        # Insert ZIP file into database
        zip_id = self.insert_zip_file(zip_file)
        self.log_info(f"Registered ZIP file: {zip_filename} (ID: {zip_id})")

        # Extract XML file listing using Python zipfile (unzip has issues with these ZIP files)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            xml_files = [f for f in zip_ref.namelist() if f.endswith('.xml')]
        self.log_info(f"Found {len(xml_files)} XML files using Python zipfile")

        # Batch insert all XML files for this ZIP
        xml_file_objects = []
        for xml_filename in xml_files:
            xml_file = XMLFile(
                zip_id=zip_id,
                filename=xml_filename,
                internal_path=xml_filename
            )
            xml_file_objects.append(xml_file)

        # Bulk insert XML files
        if xml_file_objects:
            xml_data = [(xf.zip_id, xf.filename, xf.internal_path, xf.ein, xf.tax_year, xf.form_type, xf.processed, xf.error_message)
                       for xf in xml_file_objects]
            self.db_cursor.executemany("""
                INSERT OR IGNORE INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year, form_type, processed, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, xml_data)
            self.db_conn.commit()
            self.log_info(f"Bulk inserted {len(xml_file_objects)} XML files for ZIP {zip_filename}")

        self.log_info(f"Registered {len(xml_files)} XML files from {zip_filename}")

        # Update ZIP status
        self.db_cursor.execute("""
            UPDATE ZipFiles SET status = 'processed', processed_date = ?
            WHERE zip_id = ?
        """, (datetime.now().isoformat(), zip_id))
        self.db_conn.commit()

    def process_xml_files(self):
        """Parse XML files and extract data to dataclasses (step 5)"""
        self.log_info("Processing XML files and extracting data")

        # Get unprocessed XML files
        self.db_cursor.execute("""
            SELECT xml_id, zip_id, filename, internal_path
            FROM XmlFiles
            WHERE processed = FALSE
            ORDER BY zip_id, filename
        """)

        xml_files = self.db_cursor.fetchall()
        self.log_info(f"Found {len(xml_files)} unprocessed XML files")

        # Use producer-consumer pattern for parallel processing
        # Producers: XML parsing threads
        # Consumer: Single database writer thread
        self._process_xml_files_parallel(xml_files)

    def _process_xml_files_parallel(self, xml_files):
        """Process XML files using producer-consumer pattern for threading safety"""
        # Create queues for communication between threads
        xml_queue = Queue(maxsize=QUEUE_SIZE)  # Producer -> Consumer
        result_queue = Queue(maxsize=QUEUE_SIZE)  # Consumer -> Main thread

        # Create database connection for consumer thread
        consumer_conn = sqlite3.connect(self.db_path)
        consumer_cursor = consumer_conn.cursor()
        consumer_conn.execute("PRAGMA foreign_keys = ON")

        # Start consumer thread (single writer to database)
        consumer_thread = threading.Thread(
            target=self._database_consumer,
            args=(result_queue, consumer_conn, consumer_cursor)
        )
        consumer_thread.daemon = True
        consumer_thread.start()

        # Start producer threads
        producer_threads = []
        num_producers = min(MAX_WORKERS, len(xml_files) // 1000 + 1)  # Scale with workload

        for i in range(num_producers):
            thread = threading.Thread(
                target=self._xml_producer,
                args=(xml_files, xml_queue, i, num_producers)
            )
            thread.daemon = True
            producer_threads.append(thread)
            thread.start()

        # Monitor progress
        total_processed = 0
        with tqdm(total=len(xml_files), desc="Processing XML files") as pbar:
            while total_processed < len(xml_files):
                # Check if consumer is still alive
                if not consumer_thread.is_alive():
                    self.log_error("Database consumer thread died")
                    break

                # Check for results from consumer
                try:
                    batch_size = result_queue.get(timeout=1.0)
                    total_processed += batch_size
                    pbar.update(batch_size)
                except:
                    # No results yet, continue monitoring
                    continue

                # Check if we should pause for manual review
                if total_processed >= 10000:  # Process 10k files then stop for now
                    self.log_info(f"Processed {total_processed} files, pausing for manual review")
                    break

        # Signal producers to stop
        for _ in producer_threads:
            xml_queue.put(None)

        # Wait for producers to finish
        for thread in producer_threads:
            thread.join(timeout=5.0)

        # Signal consumer to stop and wait
        result_queue.put(None)
        consumer_thread.join(timeout=10.0)

        # Close consumer connection
        consumer_conn.close()

        self.log_info(f"Parallel processing complete: {total_processed} files processed")

    def _xml_producer(self, xml_files, xml_queue, producer_id, num_producers):
        """Producer thread: parses XML and sends results to consumer"""
        # Create thread-local database connection for read-only operations
        local_conn = sqlite3.connect(self.db_path)
        local_cursor = local_conn.cursor()

        for i in range(producer_id, len(xml_files), num_producers):
            xml_id, zip_id, filename, internal_path = xml_files[i]

            # Get ZIP file path using thread-local connection
            local_cursor.execute("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (zip_id,))
            zip_path_result = local_cursor.fetchone()
            if not zip_path_result:
                self.log_error(f"No ZIP file found for xml_id {xml_id}")
                continue

            zip_path = zip_path_result[0]

            try:
                # Parse XML (CPU-intensive, thread-safe)
                result = self._process_single_xml(xml_id, zip_path, filename, internal_path)
                if result:
                    # Send result to consumer
                    xml_queue.put(result)
            except Exception as e:
                self.log_error(f"XML processing failed for {filename}: {e}", exc_info=True)
                # Mark as processed even on error
                xml_queue.put(('error', xml_id))

        local_conn.close()

    def _database_consumer(self, result_queue, conn, cursor):
        """Consumer thread: writes results to database (single-threaded for SQLite safety)"""
        batch_data = []

        while True:
            try:
                item = result_queue.get(timeout=1.0)

                if item is None:  # Shutdown signal
                    break

                if item == ('error', None):
                    continue

                if isinstance(item, tuple) and item[0] == 'error':
                    # Mark XML as processed with error
                    xml_id = item[1]
                    cursor.execute("""
                        UPDATE XmlFiles SET processed = TRUE, error_message = ?
                        WHERE xml_id = ?
                    """, ("Processing error", xml_id))
                    conn.commit()
                    continue

                # Add to batch
                batch_data.append(item)

                # Process batch when it gets large enough
                if len(batch_data) >= BATCH_SIZE:
                    self._bulk_insert_batch(batch_data, conn, cursor)
                    result_queue.put(len(batch_data))  # Signal progress
                    batch_data = []

            except:
                continue

        # Process remaining batch
        if batch_data:
            self._bulk_insert_batch(batch_data, conn, cursor)
            result_queue.put(len(batch_data))

    def _bulk_insert_batch(self, batch_data, conn=None, cursor=None):
        """Bulk insert a batch of processed XML data"""
        if conn is None:
            conn = self.db_conn
        if cursor is None:
            cursor = self.db_cursor

        charities = []
        officers = []
        grants = []
        contractors = []
        contributions = []

        for charity, officer_list, grant_list, contractor_list, contribution_list in batch_data:
            if charity:
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
            cursor.executemany("""
                INSERT INTO Charities (ein, tax_year, filer_name, receipt_amt, govt_amt,
                                    contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                    conferences_amt, officer_comp, comp_pct, comp_ptile,
                                    travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                    grants_pct, grants_ptile, foreign_expenses_pct,
                                    foreign_expenses_ptile, grift_ratio, total_assets,
                                    form_type, denominator, foreign_office, foreign_expenses,
                                    grants_to_others, domestic_misrep_flag, xml_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, charity_data)

            # Get the charity IDs for related data
            last_id = cursor.lastrowid or 0
            charity_ids = [last_id - len(charities) + i + 1 for i in range(len(charities))]

            # Bulk insert officers
            if officers:
                officer_data = [(charity_ids[o.charity_id - 1], o.first_name, o.last_name,
                               o.compensation, o.tax_year) for o in officers]
                cursor.executemany("""
                    INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
                    VALUES (?, ?, ?, ?, ?)
                """, officer_data)

            # Bulk insert grants
            if grants:
                grant_data = [(g.filer_ein, g.filer_name, g.grant_ein, g.grant_amt, g.tax_year,
                             g.filer_colocator, g.grantee_colocator) for g in grants]
                cursor.executemany("""
                    INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                                      filer_colocator, grantee_colocator)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, grant_data)

            # Bulk insert contractors
            if contractors:
                contractor_data = [(c.filer_ein, c.name, c.amount, c.ein, c.address, c.zip_code,
                                  c.po_box, c.tax_year) for c in contractors]
                cursor.executemany("""
                    INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                           po_box, tax_year)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, contractor_data)

            # Bulk insert political contributions
            if contributions:
                contribution_data = [(c.filer_ein, c.recipient, c.amount, c.recipient_address,
                                    c.recipient_zip, c.recipient_po_box, c.tax_year) for c in contributions]
                cursor.executemany("""
                    INSERT INTO PoliticalContributions (filer_ein, recipient, amount,
                                                     recipient_address, recipient_zip,
                                                     recipient_po_box, tax_year)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, contribution_data)

        conn.commit()

    def _bulk_insert_batch(self, batch_data):
        """Bulk insert a batch of processed XML data"""
        charities = []
        officers = []
        grants = []
        contractors = []
        contributions = []

        for charity, officer_list, grant_list, contractor_list, contribution_list in batch_data:
            if charity:
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
            self.db_cursor.executemany("""
                INSERT INTO Charities (ein, tax_year, filer_name, receipt_amt, govt_amt,
                                    contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                    conferences_amt, officer_comp, comp_pct, comp_ptile,
                                    travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                    grants_pct, grants_ptile, foreign_expenses_pct,
                                    foreign_expenses_ptile, grift_ratio, total_assets,
                                    form_type, denominator, foreign_office, foreign_expenses,
                                    grants_to_others, domestic_misrep_flag, xml_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, charity_data)

            # Get the charity IDs for related data
            last_id = self.db_cursor.lastrowid or 0
            charity_ids = [last_id - len(charities) + i + 1 for i in range(len(charities))]

            # Bulk insert officers
            if officers:
                officer_data = [(charity_ids[o.charity_id - 1], o.first_name, o.last_name,
                               o.compensation, o.tax_year) for o in officers]
                self.db_cursor.executemany("""
                    INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
                    VALUES (?, ?, ?, ?, ?)
                """, officer_data)

            # Bulk insert grants
            if grants:
                grant_data = [(g.filer_ein, g.filer_name, g.grant_ein, g.grant_amt, g.tax_year,
                             g.filer_colocator, g.grantee_colocator) for g in grants]
                self.db_cursor.executemany("""
                    INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                                      filer_colocator, grantee_colocator)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, grant_data)

            # Bulk insert contractors
            if contractors:
                contractor_data = [(c.filer_ein, c.name, c.amount, c.ein, c.address, c.zip_code,
                                  c.po_box, c.tax_year) for c in contractors]
                self.db_cursor.executemany("""
                    INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                           po_box, tax_year)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, contractor_data)

            # Bulk insert political contributions
            if contributions:
                contribution_data = [(c.filer_ein, c.recipient, c.amount, c.recipient_address,
                                    c.recipient_zip, c.recipient_po_box, c.tax_year) for c in contributions]
                self.db_cursor.executemany("""
                    INSERT INTO PoliticalContributions (filer_ein, recipient, amount,
                                                     recipient_address, recipient_zip,
                                                     recipient_po_box, tax_year)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, contribution_data)

        self.db_conn.commit()

    def _process_single_xml(self, xml_id: int, zip_path: str, filename: str, internal_path: str):
        """Process a single XML file"""
        try:
            # Extract XML content from ZIP using Python zipfile (unzip has issues with these ZIP files)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                with zip_ref.open(internal_path) as xml_file:
                    xml_content = xml_file.read()

            # Parse XML
            parser = ET.XMLParser(recover=True)
            tree = ET.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            if not filer_ein or filer_ein == "Unknown":
                self.log_error(f"Skipping XML {filename}: invalid EIN")
                return ('error', xml_id)

            # Extract data based on form type
            if form_type == "990":
                charity, officers, grants, contractors, contributions = self._parse_990_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990EZ":
                charity, officers, grants, contractors, contributions = self._parse_990ez_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990PF":
                charity, officers, grants, contractors, contributions = self._parse_990pf_data(root, filename, filer_ein, tax_year, form_type)
            else:
                self.log_error(f"Unsupported form type {form_type} in {filename}")
                return ('error', xml_id)

            if charity:
                return charity, officers, grants, contractors, contributions

        except Exception as e:
            self.log_error(f"Failed to process XML {filename}: {e}", exc_info=True)
            return ('error', xml_id)

        return None

    def _extract_form_type(self, root) -> str:
        """Extract form type from XML"""
        for xpath in FORM_TYPE_XPATHS:
            try:
                result = xpath(root)
                if result:
                    return result[0].text
            except:
                continue
        return "Unknown"

    def _extract_tax_year(self, root) -> int:
        """Extract tax year from XML"""
        for xpath in TAX_YEAR_XPATHS:
            try:
                result = xpath(root)
                if result:
                    year_str = result[0].text
                    if year_str and year_str.isdigit():
                        return int(year_str)
            except:
                continue
        return 0  # Default fallback

    def _extract_filer_ein(self, root) -> str:
        """Extract filer EIN from XML"""
        for xpath in FILER_EIN_XPATHS:
            try:
                result = xpath(root)
                if result:
                    raw_ein = result[0].text.strip()
                    return f"{int(raw_ein):09d}" if raw_ein.isdigit() else "Unknown"
            except:
                continue
        return "Unknown"

    def _mark_xml_error(self, xml_id: int, error_msg: str):
        """Mark XML file as having an error"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE, error_message = ?
            WHERE xml_id = ?
        """, (error_msg, xml_id))
        self.db_conn.commit()

    def _mark_xml_processed(self, xml_id: int):
        """Mark XML file as processed"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE
            WHERE xml_id = ?
        """, (xml_id,))
        self.db_conn.commit()

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        xpath_cache: Dict = {}
        """Parse Form 990 data"""
        xpath_cache = {}

        # Extract charity data using existing parsing functions
        row, officer_entries = parse_990.parse_990(root, filename, xpath_cache, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not row:
            return None, [], [], [], []

        # Convert row to Charity dataclass
        charity = Charity(
            ein=row[1],  # filer_ein
            tax_year=row[0],  # tax_year
            filer_name=row[2],  # filer_name
            receipt_amt=row[3],  # receipt
            govt_amt=row[4],  # govt_grants
            contrib_amt=row[5],  # contributions
            org_type=row[6],  # org_type
            total_exp=row[7],  # total_exp
            prog_exp=row[8],  # prog_exp
            travel_amt=row[9],  # travel
            conferences_amt=row[10],  # conferences
            officer_comp=row[11],  # officer_comp
            comp_pct=row[12],  # comp_pct
            comp_ptile=row[13],  # comp_ptile
            travel_pct=row[14],  # travel_pct
            travel_ptile=row[15],  # travel_ptile
            conferences_pct=row[16],  # conferences_pct
            conferences_ptile=row[17],  # conferences_ptile
            grants_pct=row[18],  # grants_pct
            grants_ptile=row[19],  # grants_ptile
            foreign_expenses_pct=row[20],  # foreign_expenses_pct
            foreign_expenses_ptile=row[21],  # foreign_expenses_ptile
            grift_ratio=row[22],  # grift_ratio
            total_assets=row[23],  # total_assets
            form_type=row[24],  # form_type
            denominator=row[25],  # denominator
            foreign_office=row[26],  # foreign_office
            foreign_expenses=row[27],  # foreign_expenses
            grants_to_others=row[28],  # grants_to_others
            domestic_misrep_flag=row[29],  # domestic_misrep_flag
            xml_name=row[30]  # xml_name
        )

        # Convert officer entries
        officers = []
        for entry in officer_entries:
            officer = Officer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Extract grants, contractors, and political contributions
        grants = self._extract_grants_990(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _extract_grants_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990"""
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_utils.parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990EZ"""
        # Similar to 990 but using EZ-specific parsing
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_utils.parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990EZ")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990PF"""
        # Similar to 990 but using PF-specific parsing
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_utils.parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990PF")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_contractors_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990"""
        contractors = []
        # TODO: Implement contractor extraction from Schedule L or other sections
        return contractors

    def _extract_contractors_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990EZ"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_contractors_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990PF"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990"""
        contributions = []
        # TODO: Implement political contribution extraction
        return contributions

    def _extract_political_contributions_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990EZ"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990PF"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _parse_990ez_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990EZ data"""
        xpath_cache: Dict = {}

        # Extract charity data using existing parsing functions
        row, officer_entries = parse_990ez.parse_990ez(root, filename, xpath_cache, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not row:
            return None, [], [], [], []

        # Convert row to Charity dataclass (similar to 990)
        charity = Charity(
            ein=row[1], tax_year=row[0], filer_name=row[2], receipt_amt=row[3],
            govt_amt=row[4], contrib_amt=row[5], org_type=row[6], total_exp=row[7],
            prog_exp=row[8], travel_amt=row[9], conferences_amt=row[10],
            officer_comp=row[11], comp_pct=row[12], comp_ptile=row[13],
            travel_pct=row[14], travel_ptile=row[15], conferences_pct=row[16],
            conferences_ptile=row[17], grants_pct=row[18], grants_ptile=row[19],
            foreign_expenses_pct=row[20], foreign_expenses_ptile=row[21],
            grift_ratio=row[22], total_assets=row[23], form_type=row[24],
            denominator=row[25], foreign_office=row[26], foreign_expenses=row[27],
            grants_to_others=row[28], domestic_misrep_flag=row[29], xml_name=row[30]
        )

        # Convert officer entries
        officers = []
        for entry in officer_entries:
            officer = Officer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Extract grants, contractors, and political contributions
        grants: List[Grant] = self._extract_grants_990ez(root, filename, filer_ein, tax_year)
        contractors: List[Contractor] = self._extract_contractors_990ez(root, filename, filer_ein, tax_year)
        contributions: List[PoliticalContribution] = self._extract_political_contributions_990ez(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _parse_990pf_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990PF data"""
        xpath_cache: Dict = {}

        # Extract charity data using existing parsing functions
        row, officer_entries = parse_990pf.parse_990pf(root, filename, xpath_cache, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not row:
            return None, [], [], [], []

        # Convert row to Charity dataclass (similar to 990)
        charity = Charity(
            ein=row[1], tax_year=row[0], filer_name=row[2], receipt_amt=row[3],
            govt_amt=row[4], contrib_amt=row[5], org_type=row[6], total_exp=row[7],
            prog_exp=row[8], travel_amt=row[9], conferences_amt=row[10],
            officer_comp=row[11], comp_pct=row[12], comp_ptile=row[13],
            travel_pct=row[14], travel_ptile=row[15], conferences_pct=row[16],
            conferences_ptile=row[17], grants_pct=row[18], grants_ptile=row[19],
            foreign_expenses_pct=row[20], foreign_expenses_ptile=row[21],
            grift_ratio=row[22], total_assets=row[23], form_type=row[24],
            denominator=row[25], foreign_office=row[26], foreign_expenses=row[27],
            grants_to_others=row[28], domestic_misrep_flag=row[29], xml_name=row[30]
        )

        # Convert officer entries
        officers = []
        for entry in officer_entries:
            officer = Officer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Extract grants, contractors, and political contributions
        grants = self._extract_grants_990pf(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990pf(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990pf(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def geolocate_addresses(self):
        """Geolocate addresses using census API (step 7)"""
        self.log_info("Starting address geolocation")

        # Get addresses that need geocoding
        self.db_cursor.execute("""
            SELECT address_id, canonical_address, po_box, zip_code
            FROM Addresses
            WHERE geocoding_id IS NULL
            AND (po_box IS NULL OR po_box = '')  -- Skip PO boxes
            ORDER BY address_id
        """)

        addresses = self.db_cursor.fetchall()
        self.log_info(f"Found {len(addresses)} addresses to geolocate")

        # Process in batches of 5000 with progress bar
        batch_size = 5000
        with tqdm(total=len(addresses), desc="Geocoding addresses") as pbar:
            for i in range(0, len(addresses), batch_size):
                batch = addresses[i:i + batch_size]
                self._geolocate_batch(batch)
                pbar.update(len(batch))

    def _geolocate_batch(self, batch: List[Tuple]):
        """Geolocate a batch of addresses"""
        addresses_to_geocode = []

        for address_id, canonical_address, po_box, zip_code in batch:
            # Skip if PO box
            if po_box and po_box.strip():
                colocator = f"PO:{po_box.strip()}:{zip_code or ''}"
                self.db_cursor.execute("""
                    UPDATE Addresses SET colocator = ? WHERE address_id = ?
                """, (colocator, address_id))
                continue

            # Prepare address for geocoding
            # Parse canonical_address back to components
            # This assumes canonical_address is in a standard format
            address_parts = canonical_address.split(', ')
            if len(address_parts) >= 3:
                street = address_parts[0]
                city = address_parts[1]
                state_zip = address_parts[2].split(' ')
                if len(state_zip) >= 2:
                    state = state_zip[0]
                    zip_part = state_zip[1]
                    addresses_to_geocode.append({
                        'address_id': address_id,
                        'street': street,
                        'city': city,
                        'state': state,
                        'zip': zip_part
                    })

        if not addresses_to_geocode:
            return

        # Call census geocoding API
        try:
            # Prepare batch for censusgeocode
            batch_addresses = []
            for addr in addresses_to_geocode:
                batch_addresses.append({
                    'address': f"{addr['street']}, {addr['city']}, {addr['state']} {addr['zip']}"
                })

            # Geocode batch
            if cg is None:
                self.log_error("censusgeocode not available, skipping geocoding")
                return
            results = cg.addressbatch(batch_addresses)

            for i, result in enumerate(results):
                addr_data = addresses_to_geocode[i]
                address_id = addr_data['address_id']

                if 'lat' in result and 'lon' in result and result['lat'] and result['lon']:
                    # Success - round to nearest 10 meters (~0.0001 degrees)
                    lat = round(float(result['lat']), 4)
                    lon = round(float(result['lon']), 4)
                    colocator = f"LL:{lat}:{lon}"

                    # Insert geocoding record
                    self.db_cursor.execute("""
                        INSERT INTO Geocoding (address_hash, normalized_address, latitude, longitude, geocoding_status)
                        VALUES (?, ?, ?, ?, 'success')
                    """, (hash(result['address']), result['address'], lat, lon))

                    geocoding_id = self.db_cursor.lastrowid

                    # Update address
                    self.db_cursor.execute("""
                        UPDATE Addresses SET geocoding_id = ?, latitude = ?, longitude = ?, colocator = ?
                        WHERE address_id = ?
                    """, (geocoding_id, lat, lon, colocator, address_id))
                else:
                    # Failed
                    self.db_cursor.execute("""
                        INSERT INTO Geocoding (address_hash, normalized_address, geocoding_status)
                        VALUES (?, ?, 'failed')
                    """, (hash(result.get('address', '')), result.get('address', '')))

                    geocoding_id = self.db_cursor.lastrowid

                    # Update address with failed geocoding
                    self.db_cursor.execute("""
                        UPDATE Addresses SET geocoding_id = ? WHERE address_id = ?
                    """, (geocoding_id, address_id))

            self.db_conn.commit()
            self.log_info(f"Geolocated batch of {len(addresses_to_geocode)} addresses")

        except Exception as e:
            self.log_error(f"Failed to geolocate batch: {e}", exc_info=True)

    def match_grants_by_address(self):
        """Match grants with unknown EINs by address or colocator (step 9)"""
        self.log_info("Matching grants with unknown EINs by address/colocator")

        # Get grants with unknown EINs
        self.db_cursor.execute("""
            SELECT grant_id, filer_ein, grant_amt, tax_year
            FROM Grants
            WHERE grant_ein IS NULL OR grant_ein = ''
        """)

        grants = self.db_cursor.fetchall()
        self.log_info(f"Found {len(grants)} grants with unknown EINs to match")

        with tqdm(total=len(grants), desc="Matching grants") as pbar:
            for grant_id, filer_ein, grant_amt, tax_year in grants:
                # For now, just create stub charities for unmatched grants
                # TODO: Implement proper address matching
                stub_ein = self._create_stub_charity(f"Unknown Grantee {grant_id}", "", "", "", tax_year)
                if stub_ein:
                    self.db_cursor.execute("""
                        UPDATE Grants SET grant_ein = ? WHERE grant_id = ?
                    """, (stub_ein, grant_id))
                    self.log_info(f"Created stub charity {stub_ein} for grant {grant_id}")

                pbar.update(1)

        self.db_conn.commit()

    def _find_charity_by_address(self, name: str, address: str, zip_code: str, po_box: str, tax_year: int) -> Optional[str]:
        """Find charity EIN by address/colocator matching"""
        # First try exact address match
        self.db_cursor.execute("""
            SELECT DISTINCT c.ein
            FROM Charities c
            JOIN Addresses a ON c.ein = a.ein
            WHERE c.tax_year = ?
            AND LOWER(TRIM(a.canonical_address)) = LOWER(TRIM(?))
        """, (tax_year, address or ""))

        result = self.db_cursor.fetchone()
        if result:
            return result[0]

        # Try name + ZIP match
        if name and zip_code:
            self.db_cursor.execute("""
                SELECT DISTINCT c.ein
                FROM Charities c
                JOIN Addresses a ON c.ein = a.ein
                WHERE c.tax_year = ?
                AND LOWER(TRIM(c.filer_name)) = LOWER(TRIM(?))
                AND a.zip_code = ?
            """, (tax_year, name, zip_code))

            result = self.db_cursor.fetchone()
            if result:
                return result[0]

        # Try colocator match if we have address components
        if address and zip_code:
            # Find charities with similar addresses
            self.db_cursor.execute("""
                SELECT DISTINCT c.ein, c.colocator
                FROM Charities c
                JOIN Addresses a ON c.ein = a.ein
                WHERE c.tax_year = ?
                AND a.zip_code = ?
                AND c.colocator LIKE 'LL:%'
            """, (tax_year, zip_code))

            candidates = self.db_cursor.fetchall()
            for ein, colocator in candidates:
                if colocator and colocator.startswith('LL:'):
                    # Could implement more sophisticated matching here
                    # For now, return first candidate
                    return ein

        return None

    def _create_stub_charity(self, name: str, address: str, zip_code: str, po_box: str, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants"""
        # Generate a pseudo-EIN for stub records
        stub_ein = f"STUB{hash(name + (address or '') + str(tax_year)) % 1000000000:09d}"

        # Check if stub already exists
        self.db_cursor.execute("SELECT 1 FROM Charities WHERE ein = ?", (stub_ein,))
        if self.db_cursor.fetchone():
            return stub_ein

        # Create stub charity
        charity = Charity(
            ein=stub_ein,
            tax_year=tax_year,
            filer_name=name or "Unknown",
            xml_name=f"stub_{stub_ein}_{tax_year}"
        )

        # Insert stub charity
        charity_id = self.insert_charity(charity)

        # Create address record if we have address info
        if address or zip_code:
            addr = Address(
                ein=stub_ein,
                name=name or "Unknown",
                canonical_address=address or "",
                zip_code=zip_code,
                po_box=po_box,
                address_type="grantee"
            )
            self.insert_address(addr)

        return stub_ein

    def calculate_percentiles(self):
        """Calculate percentile rankings by org type and tax year (step 10)"""
        self.log_info("Calculating percentile rankings")

        # Get all charities grouped by org_type and tax_year
        self.db_cursor.execute("""
            SELECT org_type, tax_year, ein, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct
            FROM Charities
            WHERE denominator > 0
            ORDER BY org_type, tax_year
        """)

        charities = self.db_cursor.fetchall()

        # Group by org_type and tax_year
        groups = defaultdict(lambda: defaultdict(list))
        for org_type, tax_year, ein, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct in charities:
            key = (org_type, tax_year)
            groups[org_type][tax_year].append({
                'ein': ein,
                'comp_pct': comp_pct,
                'travel_pct': travel_pct,
                'conferences_pct': conferences_pct,
                'grants_pct': grants_pct,
                'foreign_expenses_pct': foreign_expenses_pct
            })

        total_groups = sum(len(years) for years in groups.values())
        processed_groups = 0

        # Calculate percentiles for each group
        with tqdm(total=total_groups, desc="Calculating percentiles") as pbar:
            for org_type, years in groups.items():
                for tax_year, org_charities in years.items():
                    if len(org_charities) < 2:
                        processed_groups += 1
                        pbar.update(1)
                        continue  # Need at least 2 for meaningful percentiles

                    # Extract values for each metric
                    comp_values = [c['comp_pct'] for c in org_charities if c['comp_pct'] is not None]
                    travel_values = [c['travel_pct'] for c in org_charities if c['travel_pct'] is not None]
                    conferences_values = [c['conferences_pct'] for c in org_charities if c['conferences_pct'] is not None]
                    grants_values = [c['grants_pct'] for c in org_charities if c['grants_pct'] is not None]
                    foreign_values = [c['foreign_expenses_pct'] for c in org_charities if c['foreign_expenses_pct'] is not None]

                    # Calculate percentiles for each charity
                    for charity in org_charities:
                        ein = charity['ein']

                        # Compensation percentile
                        if charity['comp_pct'] is not None and comp_values:
                            comp_values_sorted = sorted(comp_values)
                            comp_ptile = self._calculate_percentile(charity['comp_pct'], comp_values_sorted)
                        else:
                            comp_ptile = None

                        # Travel percentile
                        if charity['travel_pct'] is not None and travel_values:
                            travel_values_sorted = sorted(travel_values)
                            travel_ptile = self._calculate_percentile(charity['travel_pct'], travel_values_sorted)
                        else:
                            travel_ptile = None

                        # Conferences percentile
                        if charity['conferences_pct'] is not None and conferences_values:
                            conferences_values_sorted = sorted(conferences_values)
                            conferences_ptile = self._calculate_percentile(charity['conferences_pct'], conferences_values_sorted)
                        else:
                            conferences_ptile = None

                        # Grants percentile
                        if charity['grants_pct'] is not None and grants_values:
                            grants_values_sorted = sorted(grants_values)
                            grants_ptile = self._calculate_percentile(charity['grants_pct'], grants_values_sorted)
                        else:
                            grants_ptile = None

                        # Foreign expenses percentile
                        if charity['foreign_expenses_pct'] is not None and foreign_values:
                            foreign_values_sorted = sorted(foreign_values)
                            foreign_ptile = self._calculate_percentile(charity['foreign_expenses_pct'], foreign_values_sorted)
                        else:
                            foreign_ptile = None

                        # Update database
                        self.db_cursor.execute("""
                            UPDATE Charities SET
                                comp_ptile_value = ?,
                                travel_ptile_value = ?,
                                conferences_ptile_value = ?,
                                grants_ptile_value = ?,
                                foreign_expenses_ptile_value = ?
                            WHERE ein = ? AND tax_year = ?
                        """, (comp_ptile, travel_ptile, conferences_ptile, grants_ptile, foreign_ptile, ein, tax_year))

                    processed_groups += 1
                    pbar.update(1)
                    self.log_info(f"Calculated percentiles for {org_type} {tax_year}: {len(org_charities)} charities")

        self.db_conn.commit()

    def _calculate_percentile(self, value: float, sorted_values: List[float]) -> float:
        """Calculate percentile rank for a value in a sorted list"""
        if not sorted_values:
            return 0.0

        # Find position
        for i, v in enumerate(sorted_values):
            if value <= v:
                return (i / len(sorted_values)) * 100.0

        return 100.0  # Value is higher than all others

    def select_latest_charities(self):
        """Select the most recent filing for each charity (pre-step 11)"""
        self.log_info("Selecting latest charity filings")

        # Create a view/table for latest charities
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
        self.log_info("Created LatestCharities table")

    def export_final_tsvs(self):
        """Export final TSV files (step 11)"""
        self.log_info("Exporting final TSV files")

        # Ensure latest charities are selected
        self.select_latest_charities()

        # Export charities
        self._export_charities_tsv()

        # Export grants
        self._export_grants_tsv()

        # Export contractors
        self._export_contractors_tsv()

        # Export political contributions
        self._export_political_contributions_tsv()

    def _export_charities_tsv(self):
        """Export charities to TSV"""
        self.db_cursor.execute("""
            SELECT
                tax_year, ein, filer_name, receipt_amt, govt_amt, contrib_amt,
                org_type, total_exp, prog_exp, travel_amt, conferences_amt,
                officer_comp, comp_pct, comp_ptile_value, travel_pct, travel_ptile_value,
                conferences_pct, conferences_ptile_value, grants_pct, grants_ptile_value,
                foreign_expenses_pct, foreign_expenses_ptile_value, grift_ratio,
                total_assets, form_type, denominator, foreign_office, foreign_expenses,
                grants_to_others, domestic_misrep_flag, xml_name
            FROM LatestCharities
            ORDER BY ein
        """)

        charities = self.db_cursor.fetchall()

        output_path = Path(self.final_dir) / "charities_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt",
                "org_type", "total_exp", "prog_exp", "travel_amt", "conferences_amt",
                "officer_comp", "comp_pct", "comp_ptile", "travel_pct", "travel_ptile",
                "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
                "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio",
                "total_assets", "form_type", "denominator", "foreign_office", "foreign_expenses",
                "grants_to_others", "domestic_misrep_flag", "xml_name"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in charities:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        self.log_info(f"Exported {len(charities)} charities to {output_path}")

    def _export_grants_tsv(self):
        """Export grants to TSV"""
        self.db_cursor.execute("""
            SELECT
                g.filer_ein, g.filer_name, g.grant_ein, g.grant_amt, g.tax_year,
                g.filer_colocator, g.grantee_colocator
            FROM Grants g
            JOIN LatestCharities lc ON g.filer_ein = lc.ein
            ORDER BY g.filer_ein, g.tax_year
        """)

        grants = self.db_cursor.fetchall()

        output_path = Path(self.final_dir) / "grants_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "filer_ein", "filer_name", "grant_ein", "grant_amt", "tax_year",
                "filer_colocator", "grantee_colocator"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in grants:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        self.log_info(f"Exported {len(grants)} grants to {output_path}")

    def _export_contractors_tsv(self):
        """Export contractors to TSV"""
        self.db_cursor.execute("""
            SELECT
                c.filer_ein, c.name, c.amount, c.ein, c.address, c.zip_code,
                c.po_box, c.tax_year, c.colocator
            FROM Contractors c
            JOIN LatestCharities lc ON c.filer_ein = lc.ein
            ORDER BY c.filer_ein, c.tax_year
        """)

        contractors = self.db_cursor.fetchall()

        output_path = Path(self.final_dir) / "contractors_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "filer_ein", "name", "amount", "ein", "address", "zip_code",
                "po_box", "tax_year", "colocator"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in contractors:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        self.log_info(f"Exported {len(contractors)} contractors to {output_path}")

    def _export_political_contributions_tsv(self):
        """Export political contributions to TSV"""
        self.db_cursor.execute("""
            SELECT
                pc.filer_ein, pc.recipient, pc.amount, pc.recipient_address,
                pc.recipient_zip, pc.recipient_po_box, pc.tax_year, pc.colocator
            FROM PoliticalContributions pc
            JOIN LatestCharities lc ON pc.filer_ein = lc.ein
            ORDER BY pc.filer_ein, pc.tax_year
        """)

        contributions = self.db_cursor.fetchall()

        output_path = Path(self.final_dir) / "political_contributions_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "filer_ein", "recipient", "amount", "recipient_address",
                "recipient_zip", "recipient_po_box", "tax_year", "colocator"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in contributions:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        self.log_info(f"Exported {len(contributions)} political contributions to {output_path}")

def main():
    """Command-line interface"""
    parser = argparse.ArgumentParser(description="IRS 990 Data Processor")
    parser.add_argument("start_year", type=int, help="Start year for processing")
    parser.add_argument("end_year", type=int, help="End year for processing")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")
    parser.add_argument("--zips-dir", default=DEFAULT_ZIPS_DIR, help="ZIP files directory")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory")
    parser.add_argument("--anal-dir", default=DEFAULT_ANAL_DIR, help="Analysis directory")
    parser.add_argument("--final-dir", default=DEFAULT_FINAL_DIR, help="Final output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--step", choices=["all", "zip", "xml", "address", "geolocate",
                                         "match", "percentiles", "export"],
                       default="all", help="Processing step to run")

    args = parser.parse_args()

    processor = IRS990Processor(
        db_path=args.db_path,
        zips_dir=args.zips_dir,
        out_dir=args.out_dir,
        anal_dir=args.anal_dir,
        final_dir=args.final_dir,
        verbose=args.verbose
    )

    try:
        if args.step in ["all", "zip"]:
            processor.process_zip_files(args.start_year, args.end_year)

        if args.step in ["all", "xml"]:
            processor.process_xml_files()

        if args.step in ["all", "address"]:
            pass  # Address processing is part of XML processing

        if args.step in ["all", "geolocate"]:
            processor.geolocate_addresses()

        if args.step in ["all", "match"]:
            processor.match_grants_by_address()

        if args.step in ["all", "percentiles"]:
            processor.calculate_percentiles()

        if args.step in ["all", "export"]:
            processor.export_final_tsvs()

    except Exception as e:
        processor.logger.error(f"Processing failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()