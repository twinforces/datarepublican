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
import logging
import argparse
import time
from pathlib import Path
from typing import Optional, Tuple, List

# Import extracted modules
from database_operations import DatabaseOperations, Charity, Officer, Grant, Contractor, PoliticalContribution
from zip_processor import ZipProcessor
from xml_processor import XMLProcessor
from geolocation_processor import GeolocationProcessor
from address_matcher import AddressMatcher
from percentile_calculator import PercentileCalculator
from tsv_exporter import TSVExporter

# Constants
DEFAULT_DB_PATH = "irs990.db"
DEFAULT_ZIPS_DIR = "/Volumes/Data/irs_zips"
DEFAULT_OUT_DIR = "/Volumes/Data/tsvs"
DEFAULT_ANAL_DIR = "/Volumes/Data/atsvs"
DEFAULT_FINAL_DIR = "/Volumes/Data/final"

# Processing version constants
CURRENT_PROCESSING_VERSION = 2  # Increment when processing logic changes (refactored)


class IRS990Processor:
    """Main processor class for IRS 990 data"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, zips_dir: str = DEFAULT_ZIPS_DIR,
                 out_dir: str = DEFAULT_OUT_DIR, anal_dir: str = DEFAULT_ANAL_DIR,
                 final_dir: str = DEFAULT_FINAL_DIR, verbose: bool = False, max_files: Optional[int] = None):
        self.db_path = os.path.join(final_dir, "irs990.db") if db_path == DEFAULT_DB_PATH else db_path
        self.zips_dir = zips_dir
        self.out_dir = out_dir
        self.anal_dir = anal_dir
        self.final_dir = final_dir
        self.verbose = verbose
        self.max_files = max_files

        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG if verbose else logging.WARNING)

        # Initialize components
        self.db_ops = DatabaseOperations(self.db_path)
        self.zip_processor = ZipProcessor(self.db_ops, zips_dir)
        self.xml_processor = XMLProcessor(self.db_ops, CURRENT_PROCESSING_VERSION)
        self.geolocation_processor = GeolocationProcessor(self.db_ops)
        self.address_matcher = AddressMatcher(self.db_ops)
        self.percentile_calculator = PercentileCalculator(self.db_ops)
        self.tsv_exporter = TSVExporter(self.db_ops, final_dir)

        # Initialize database
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with schema"""
        # Check if database is already initialized
        # This would need to be implemented in DatabaseOperations
        # For now, assume it's handled there
        pass

    def log_error(self, msg: str, *args, ein: str = None, exc_info: bool = False):
        """Log error with optional EIN context"""
        if ein:
            self.logger.error(f"EIN {ein}: {msg}", *args, exc_info=exc_info)
        else:
            self.logger.error(msg, *args, exc_info=exc_info)

    def log_info(self, msg: str, *args, ein: str = None):
        """Log info with optional EIN context"""
        if ein:
            self.logger.info(f"EIN {ein}: {msg}", *args)
        else:
            self.logger.info(msg, *args)

    def log_debug(self, msg: str, *args, ein: str = None):
        """Log debug with optional EIN context"""
        if ein:
            self.logger.debug(f"EIN {ein}: {msg}", *args)
        else:
            self.logger.debug(msg, *args)

    # Main processing methods that delegate to modules

    def process_zip_files(self, start_year: int, end_year: int):
        """Process ZIP files and register XML files (steps 2-4)"""
        self.log_info(f"Processing ZIP files from {start_year} to {end_year}")
        return self.zip_processor.process_zip_files(start_year, end_year)

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
            xml_data = [(xf.zip_id, xf.filename, xf.internal_path, xf.ein, xf.tax_year, xf.form_type, xf.processed, xf.processing_version, xf.error_message)
                        for xf in xml_file_objects]
            self.db_cursor.executemany("""
                INSERT OR IGNORE INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year, form_type, processed, processing_version, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return self.xml_processor.process_xml_files(self.max_files)

    def _process_xml_files_parallel(self, xml_files):
        """Process XML files using producer-consumer pattern for threading safety"""
        # Create queues for communication between threads
        xml_queue = Queue(maxsize=QUEUE_SIZE)  # Producer -> Consumer
        result_queue = Queue(maxsize=QUEUE_SIZE)  # Consumer -> Main thread

        # Create database connection for consumer thread
        consumer_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        consumer_cursor = consumer_conn.cursor()
        consumer_conn.execute("PRAGMA foreign_keys = ON")
        self.log_debug("Consumer database connection established")

        # Start consumer thread (single writer to database)
        consumer_thread = threading.Thread(
            target=self._database_consumer,
            args=(xml_queue, result_queue, consumer_conn, consumer_cursor)
        )
        consumer_thread.daemon = True
        consumer_thread.start()
        self.log_debug("Consumer thread started")

        # Start producer threads
        producer_threads = []
        num_producers = min(MAX_WORKERS, len(xml_files) // 1000 + 1)  # Scale with workload
        self.log_info(f"Starting {num_producers} producer threads for {len(xml_files)} XML files")

        # For debugging, start with just 1 producer thread
        if len(xml_files) <= 10:
            num_producers = 1

        self.log_debug("Starting producer threads...")
        for i in range(num_producers):
            thread = threading.Thread(
                target=self._xml_producer,
                args=(xml_files, xml_queue, i, num_producers)
            )
            thread.daemon = True
            producer_threads.append(thread)
            thread.start()
            self.log_debug(f"Started producer thread {i}")

        # Monitor progress
        total_processed = 0
        last_update_time = time.time()
        last_log_time = time.time()
        self.log_debug("Starting progress monitoring loop")
        with tqdm(total=len(xml_files), desc="Processing XML files") as pbar:
            while total_processed < len(xml_files):
                self.log_debug(f"Monitoring loop: total_processed={total_processed}, total_files={len(xml_files)}")

                # Check if consumer is still alive
                if not consumer_thread.is_alive():
                    self.log_error("Database consumer thread died")
                    break

                # Check for results from consumer
                try:
                    self.log_debug("Waiting for result from consumer...")
                    batch_size = result_queue.get(timeout=1.0)
                    self.log_debug(f"Received batch_size: {batch_size}")
                    total_processed += batch_size
                    pbar.update(batch_size)
                    last_update_time = time.time()
                    self.log_debug(f"Progress: {total_processed}/{len(xml_files)} files processed")
                except:
                    # No results yet, continue monitoring
                    self.log_debug("No result available, continuing monitoring")
                    pass

                # Periodic update every 60 seconds regardless of batch completion
                if time.time() - last_update_time >= 60:
                    pbar.update(0)
                    last_update_time = time.time()

                # Log progress every 30 seconds
                if time.time() - last_log_time >= 30:
                    self.log_info(f"Still processing: {total_processed}/{len(xml_files)} files done")
                    last_log_time = time.time()

                # Check if we should pause for manual review - reduce for debugging
                if total_processed >= 100:  # Process 100 files then stop for now
                    self.log_info(f"Processed {total_processed} files, pausing for manual review")
                    break

        # Wait for producers to finish first
        self.log_info("Waiting for producers to finish")
        for thread in producer_threads:
            thread.join(timeout=30.0)  # Increased timeout
            if thread.is_alive():
                self.log_error(f"Producer thread did not finish gracefully")

        # Now signal consumer to stop - send multiple signals to ensure it's received
        self.log_info("Signaling consumer to stop")
        for _ in range(3):  # Send multiple shutdown signals
            xml_queue.put(None)
        consumer_thread.join(timeout=10.0)
        if consumer_thread.is_alive():
            self.log_error("Consumer thread did not finish gracefully")

        # Close consumer connection
        consumer_conn.close()

        self.log_info(f"Parallel processing complete: {total_processed} files processed")

    def _xml_producer(self, xml_files, xml_queue, producer_id, num_producers):
        """Producer thread: parses XML and sends results to consumer"""
        self.log_debug(f"Producer {producer_id} started")

        # Create thread-local database connection for read-only operations
        local_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        local_cursor = local_conn.cursor()

        processed_count = 0
        for i in range(producer_id, len(xml_files), num_producers):
            xml_id, zip_id, filename, internal_path = xml_files[i]
            self.log_debug(f"Producer {producer_id}: processing XML {xml_id} - {filename}")

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
                    self.log_debug(f"Producer {producer_id}: sending result to consumer for {filename}")
                    xml_queue.put(result)
                    processed_count += 1
                    self.log_debug(f"Producer {producer_id}: sent result, processed {processed_count} files so far")
                    if processed_count % 100 == 0:
                        self.log_debug(f"Producer {producer_id}: processed {processed_count} files")
                else:
                    self.log_debug(f"Producer {producer_id}: no result from processing {filename}")
            except Exception as e:
                self.log_error(f"XML processing failed for {filename}: {e}", exc_info=True)
                # Mark as processed even on error
                xml_queue.put(('error', xml_id))

        self.log_debug(f"Producer {producer_id}: completed, processed {processed_count} files")
        # Signal that this producer is done
        xml_queue.put(None)
        local_conn.close()

    def _database_consumer(self, xml_queue, result_queue, conn, cursor):
        """Consumer thread: writes results to database (single-threaded for SQLite safety)"""
        batch_data = []
        total_processed = 0
        self.log_debug("Consumer thread started")

        shutdown_signals_received = 0

        while True:
            try:
                self.log_debug("Consumer waiting for item from xml_queue...")
                item = xml_queue.get(timeout=5.0)  # Shorter timeout for debugging
                self.log_debug(f"Consumer received item: {type(item)}")

                if item is None:  # Shutdown signal
                    shutdown_signals_received += 1
                    self.log_debug(f"Consumer received shutdown signal #{shutdown_signals_received}")
                    if shutdown_signals_received >= 3:  # Need multiple shutdown signals
                        break
                    continue

                if item == ('error', None):
                    continue

                if isinstance(item, tuple) and item[0] == 'error':
                    # Mark XML as processed with error
                    xml_id = item[1]
                    cursor.execute("""
                        UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
                        WHERE xml_id = ?
                    """, (CURRENT_PROCESSING_VERSION, "Processing error", xml_id))
                    conn.commit()
                    continue

                # Add to batch
                batch_data.append(item)
                total_processed += 1

                # Process batch when it gets large enough or for debugging, process immediately
                if len(batch_data) >= BATCH_SIZE or len(batch_data) >= 1:  # Process every item for debugging
                    self.log_debug(f"Consumer: processing batch of {len(batch_data)} items (total: {total_processed})")
                    self._bulk_insert_batch(batch_data, conn, cursor)
                    result_queue.put(len(batch_data))  # Signal progress
                    batch_data = []

            except Exception as e:
                self.log_error(f"Consumer error: {e}", exc_info=True)
                # Don't continue on critical errors - let the thread die
                break

        # Process remaining batch
        if batch_data:
            self.log_debug(f"Consumer: processing final batch of {len(batch_data)} items (total: {total_processed})")
            self._bulk_insert_batch(batch_data, conn, cursor)
            result_queue.put(len(batch_data))

        self.log_debug(f"Consumer: completed, processed {total_processed} total items")

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
                self.log_debug(f"Setting officer.charity_id to {charity_id} for officer {officer.first_name} {officer.last_name}")
                officer.charity_id = charity_id
                officers.append(officer)

            for grant in grant_list:
                grants.append(grant)

            for contractor in contractor_list:
                contractors.append(contractor)

            for contribution in contribution_list:
                contributions.append(contribution)

        self.log_debug(f"Bulk insert batch: {len(charities)} charities (deduplicated), {len(officers)} officers, {len(grants)} grants, {len(contractors)} contractors, {len(contributions)} contributions")

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

            # TRACE: Log EIN values being inserted into database
            self.logger.info(f"TRACE: Preparing to insert {len(charity_data)} charities into Charities table")
            for data in charity_data[:10]:  # Log first 10 for debugging
                ein = data[0]  # ein is first field
                xml_name = data[26]  # xml_name is last field
                self.logger.info(f"TRACE: Inserting charity EIN='{ein}', xml_name='{xml_name}'")
            if len(charity_data) > 10:
                self.logger.info(f"TRACE: ... and {len(charity_data) - 10} more charities to insert")

            try:
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
                self.log_debug(f"Inserted {len(charity_data)} charities")
            except Exception as e:
                self.log_error(f"Failed to insert charities: {e}", exc_info=True)
                conn.rollback()
                return

            # Get the charity IDs for related data - CORRECTED METHOD
            # Instead of using lastrowid calculation, query the database for the IDs
            # of the charities we just inserted
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
                # Map officers to their correct charity_ids based on the temporary charity_id field
                officer_data = []
                for officer in officers:
                    # The officer.charity_id is a 1-based index into the charities batch
                    batch_index = officer.charity_id - 1
                    if 0 <= batch_index < len(charity_ids):
                        actual_charity_id = charity_ids[batch_index]
                        officer_data.append((actual_charity_id, officer.first_name, officer.last_name,
                                           officer.compensation, officer.tax_year))
                        self.log_debug(f"Mapped officer {officer.first_name} {officer.last_name} from batch index {batch_index} to charity_id {actual_charity_id}")
                    else:
                        self.log_error(f"Invalid batch index {batch_index} for officer {officer.first_name} {officer.last_name} (charity_ids length: {len(charity_ids)})")

                try:
                    cursor.executemany("""
                        INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
                        VALUES (?, ?, ?, ?, ?)
                    """, officer_data)
                    self.log_debug(f"Inserted {len(officer_data)} officers")
                except Exception as e:
                    self.log_error(f"Failed to insert officers: {e}", exc_info=True)

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
                    self.log_debug(f"Inserted {len(grant_data)} grants")
                except Exception as e:
                    self.log_error(f"Failed to insert grants: {e}", exc_info=True)

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
                    self.log_debug(f"Inserted {len(contractor_data)} contractors")
                except Exception as e:
                    self.log_error(f"Failed to insert contractors: {e}", exc_info=True)

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
                    self.log_debug(f"Inserted {len(contribution_data)} contributions")
                except Exception as e:
                    self.log_error(f"Failed to insert contributions: {e}", exc_info=True)

        try:
            conn.commit()
            self.log_debug("Batch commit successful")
        except Exception as e:
            self.log_error(f"Failed to commit batch: {e}", exc_info=True)
            conn.rollback()


    def _process_single_xml(self, xml_id: int, zip_path: str, filename: str, internal_path: str):
        """Process a single XML file"""
        try:
            self.log_debug(f"Processing XML {filename} (ID: {xml_id})")

            # Extract XML content from ZIP using Python zipfile (unzip has issues with these ZIP files)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                with zip_ref.open(internal_path) as xml_file:
                    xml_content = xml_file.read()

            self.log_debug(f"Extracted XML content for {filename}, size: {len(xml_content)} bytes")

            # Parse XML
            parser = ET.XMLParser(recover=True)
            tree = ET.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            self.log_debug(f"Extracted metadata for {filename}: form_type={form_type}, tax_year={tax_year}, ein={filer_ein}")

            if not filer_ein or filer_ein == "Unknown":
                self.log_error(f"Skipping XML {filename}: invalid EIN {filer_ein}")
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
                self.log_debug(f"Successfully parsed {filename}: charity={charity.ein}, grants={len(grants)}, officers={len(officers)}")
                self.log_debug(f"Returning tuple: charity={type(charity)}, officers={type(officers)}, grants={type(grants)}, contractors={type(contractors)}, contributions={type(contributions)}")
                return charity, officers, grants, contractors, contributions
            else:
                self.log_error(f"Failed to extract charity data from {filename}")
                return ('error', xml_id)

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
                    self.log_info(f"TRACE: Found raw EIN: '{raw_ein}' using xpath: {xpath.path}")
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        self.log_info(f"TRACE: Formatted EIN: '{formatted_ein}' (valid 9-digit)")
                        return formatted_ein
                    else:
                        self.logger.warning(f"TRACE: Non-digit EIN found: '{raw_ein}', returning 'Unknown'")
                        return "Unknown"
            except Exception as e:
                self.log_debug(f"XPath {xpath.path} failed: {e}")
                continue
        self.logger.warning("TRACE: No EIN found in XML, returning 'Unknown'")
        return "Unknown"

    def _mark_xml_error(self, xml_id: int, error_msg: str):
        """Mark XML file as having an error"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (CURRENT_PROCESSING_VERSION, error_msg, xml_id))
        self.db_conn.commit()

    def _mark_xml_processed(self, xml_id: int):
        """Mark XML file as processed"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?
            WHERE xml_id = ?
        """, (CURRENT_PROCESSING_VERSION, xml_id))
        self.db_conn.commit()

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        xpath_cache: Dict = {}
        """Parse Form 990 data"""
        xpath_cache = {}

        self.logger.info(f"TRACE: _parse_990_data() called with EIN: '{filer_ein}' for file {filename}")

        # Extract charity data using existing parsing functions
        row, officer_entries = parse_990.parse_990(root, filename, xpath_cache, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not row:
            self.logger.warning(f"TRACE: parse_990() returned None for EIN: '{filer_ein}' in file {filename}")
            return None, [], [], [], []
        else:
            self.logger.info(f"TRACE: parse_990() returned row with EIN: '{row[1]}' for file {filename}")

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
        return self.geolocation_processor.geolocate_addresses()

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
        return self.address_matcher.match_grants_by_address()

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
                street=None,
                city=None,
                state=None,
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
        return self.percentile_calculator.calculate_percentiles()

    def _calculate_percentile(self, value: float, sorted_values: List[float]) -> float:
        """Calculate percentile rank for a value in a sorted list"""
        if not sorted_values:
            return 0.0

        # Find position
        for i, v in enumerate(sorted_values):
            if value <= v:
                return (i / len(sorted_values)) * 100.0

        return 100.0  # Value is higher than all others

    def export_final_tsvs(self):
        """Export final TSV files (step 11)"""
        self.log_info("Exporting final TSV files")
        self.tsv_exporter.export_final_tsvs()

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

        # TRACE: Log EIN values loaded from database
        self.logger.info(f"TRACE: Loaded {len(charities)} charities from LatestCharities")
        for row in charities[:10]:  # Log first 10 for debugging
            ein = row[1]  # ein is second column
            xml_name = row[26]  # xml_name is last column
            self.logger.info(f"TRACE: Charity EIN='{ein}', xml_name='{xml_name}'")
        if len(charities) > 10:
            self.logger.info(f"TRACE: ... and {len(charities) - 10} more charities")

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

        # TRACE: Log EIN values loaded from database
        self.logger.info(f"TRACE: Loaded {len(grants)} grants from Grants table")
        for row in grants[:10]:  # Log first 10 for debugging
            filer_ein = row[0]  # filer_ein is first column
            grant_ein = row[2]  # grant_ein is third column
            self.logger.info(f"TRACE: Grant filer_ein='{filer_ein}', grant_ein='{grant_ein}'")
        if len(grants) > 10:
            self.logger.info(f"TRACE: ... and {len(grants) - 10} more grants")

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

        # TRACE: Log EIN values loaded from database
        self.logger.info(f"TRACE: Loaded {len(contractors)} contractors from Contractors table")
        for row in contractors[:10]:  # Log first 10 for debugging
            filer_ein = row[0]  # filer_ein is first column
            contractor_ein = row[3]  # ein is fourth column
            self.logger.info(f"TRACE: Contractor filer_ein='{filer_ein}', contractor_ein='{contractor_ein}'")
        if len(contractors) > 10:
            self.logger.info(f"TRACE: ... and {len(contractors) - 10} more contractors")

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

        # TRACE: Log EIN values loaded from database
        self.logger.info(f"TRACE: Loaded {len(contributions)} political contributions from PoliticalContributions table")
        for row in contributions[:10]:  # Log first 10 for debugging
            filer_ein = row[0]  # filer_ein is first column
            self.logger.info(f"TRACE: Political contribution filer_ein='{filer_ein}'")
        if len(contributions) > 10:
            self.logger.info(f"TRACE: ... and {len(contributions) - 10} more political contributions")

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
    parser.add_argument("--start-year", type=int, default=2017, help="Start year for processing (default: 2017)")
    parser.add_argument("--end-year", type=int, default=2030, help="End year for processing (default: 2030)")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")
    parser.add_argument("--zips-dir", default=DEFAULT_ZIPS_DIR, help="ZIP files directory")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory")
    parser.add_argument("--anal-dir", default=DEFAULT_ANAL_DIR, help="Analysis directory")
    parser.add_argument("--final-dir", default=DEFAULT_FINAL_DIR, help="Final output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--max-files", type=int, default=None, help="Maximum number of XML files to process (default: no limit)")
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
        verbose=args.verbose,
        max_files=args.max_files
    )

    try:
        if args.step in ["all", "zip"]:
            processor.process_zip_files(args.start_year, args.end_year)

        if args.step in ["all", "xml"]:
            processor.process_xml_files()

        # Address processing is part of XML processing

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