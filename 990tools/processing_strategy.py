#!/usr/bin/env python3
"""
processing_strategy.py - Strategy pattern for IRS 990 processing phases

This module implements the Strategy pattern to organize different processing
phases of the IRS 990 pipeline, making the main processor more maintainable.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Dict, Any
import logging
import time
import threading
from collections import deque
from pathlib import Path
import sqlite3
from io import BytesIO
from lxml import etree as ET
from dataclasses import fields
import zipfile

from database_operations import DatabaseOperations
from irs990processorDC import Charity as DCCharity, Officer as DCOfficer, Grant as DCGrant, Contractor as DCContractor, PoliticalContribution as DCPoliticalContribution
from parse_990 import parse_990
from parse_990ez import parse_990ez
from parse_990pf import parse_990pf
from parse_utils import parse_grants
from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF


class ProcessingStrategy(ABC):
    """Abstract base class for processing strategies"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, log_sql: bool = False):
        self.db_ops = db_ops
        self.logger = logger
        self.log_sql = log_sql

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the processing strategy"""
        pass

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if ein:
            self.logger.info("EIN %s: " + msg, ein, *args)
        else:
            self.logger.info(msg, *args)

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context"""
        if ein:
            self.logger.error("EIN {}: {}".format(ein, msg.format(*args)), exc_info=exc_info)
        else:
            self.logger.error(msg.format(*args), exc_info=exc_info)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if ein:
            self.logger.debug("EIN %s: " + msg, ein, *args)
        else:
            self.logger.debug(msg, *args)


class ParallelXMLProcessingStrategy(ProcessingStrategy):
    """Strategy for parallel XML file processing using producer-consumer pattern"""

    MAX_WORKERS = 16
    QUEUE_SIZE = 1000
    BATCH_SIZE = 100

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, log_sql: bool = False):
        super().__init__(db_ops, logger, log_sql)

    # Lock-free queue implementation for single-file processing

    def _get_xml_files_to_process(self) -> List[Tuple]:
        """Get list of XML files to process from database"""
        result = self.db_ops.execute_query("""
            SELECT xf.xml_id, zf.file_path, xf.filename, xf.internal_path
            FROM XmlFiles xf
            JOIN ZipFiles zf ON xf.zip_id = zf.zip_id
            WHERE xf.processed = FALSE
            ORDER BY xf.xml_id
        """)
        return result.fetchall()

    def execute(self, max_files: Optional[int] = None) -> int:
        """Process XML files using producer-consumer pattern"""
        # Get XML files from database
        xml_files = self._get_xml_files_to_process()
        if max_files:
            xml_files = xml_files[:max_files]

        # Create deques for communication between threads (lock-free)
        xml_queue = deque(maxlen=self.QUEUE_SIZE)  # Producer -> Consumer
        result_queue = deque(maxlen=self.QUEUE_SIZE)  # Consumer -> Main thread

        # Use shared database connection for consumer thread
        consumer_conn = self.db_ops.db_conn
        self.log_debug("Consumer database connection established")

        # Start consumer thread (single writer to database)
        consumer_thread = threading.Thread(
            target=self._database_consumer,
            args=(xml_queue, result_queue, consumer_conn)
        )
        consumer_thread.daemon = True
        consumer_thread.start()
        self.log_debug("Consumer thread started")

        # Start producer threads
        producer_threads = []
        num_producers = min(self.MAX_WORKERS, len(xml_files) // 1000 + 1)  # Scale with workload
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

        try:
            from tqdm import tqdm
            with tqdm(total=len(xml_files), desc="Processing XML files") as pbar:
                while total_processed < len(xml_files):
                    # Check if consumer is still alive
                    if not consumer_thread.is_alive():
                        self.log_error("Database consumer thread died")
                        break

                    # Check for results from consumer (lock-free polling)
                    if result_queue:
                        batch_size = result_queue.popleft()
                        total_processed += batch_size
                        pbar.update(batch_size)
                        last_update_time = time.time()

                    # Periodic update every 60 seconds
                    if time.time() - last_update_time >= 60:
                        pbar.update(0)
                        last_update_time = time.time()

                    # Log progress every 30 seconds
                    if time.time() - last_log_time >= 30:
                        self.log_info(f"Still processing: {total_processed}/{len(xml_files)} files done")
                        last_log_time = time.time()

                    # Check if we should pause for manual review
                    if max_files and total_processed >= max_files:  # Process max_files then stop for now
                        self.log_info(f"Processed {total_processed} files, pausing for manual review")
                        break
        except ImportError:
            # tqdm not available, use simple monitoring
            while total_processed < len(xml_files):
                if not consumer_thread.is_alive():
                    self.log_error("Database consumer thread died")
                    break

                # Poll result_queue for progress updates (lock-free)
                if result_queue:
                    batch_size = result_queue.popleft()
                    total_processed += batch_size

                if time.time() - last_log_time >= 30:
                    self.log_info(f"Still processing: {total_processed}/{len(xml_files)} files done")
                    last_log_time = time.time()

                if max_files and total_processed >= max_files:
                    self.log_info(f"Processed {total_processed} files, pausing for manual review")
                    break

        # Wait for producers to finish
        self.log_info("Waiting for producers to finish")
        for thread in producer_threads:
            thread.join(timeout=30.0)
            if thread.is_alive():
                self.log_error(f"Producer thread did not finish gracefully")

        # Signal consumer to stop
        self.log_info("Signaling consumer to stop")
        for _ in range(3):  # Send multiple shutdown signals
            xml_queue.append(None)
        consumer_thread.join(timeout=10.0)
        if consumer_thread.is_alive():
            self.log_error("Consumer thread did not finish gracefully")


        self.log_info(f"Parallel processing complete: {total_processed} files processed")
        return total_processed

    def _xml_producer(self, xml_files, xml_queue, producer_id, num_producers):
        """Producer thread: parses XML and sends results to consumer"""
        self.log_debug(f"Producer {producer_id} started")

        processed_count = 0
        for i in range(producer_id, len(xml_files), num_producers):
            xml_id, file_path, filename, internal_path = xml_files[i]
            self.log_debug(f"Producer {producer_id}: processing XML {xml_id} - {filename}")

            try:
                # Parse XML (CPU-intensive, thread-safe)
                result = self._process_single_xml(xml_id, file_path, filename, internal_path)
                if result:
                    # Send result to consumer
                    self.log_debug(f"Producer {producer_id}: sending result to consumer for {filename}")
                    xml_queue.append(result)
                    processed_count += 1
                    if processed_count % 100 == 0:
                        self.log_debug(f"Producer {producer_id}: processed {processed_count} files")
                else:
                    self.log_debug(f"Producer {producer_id}: no result from processing {filename}")
            except Exception as e:
                self.log_error(f"XML processing failed for {filename}: {e}", exc_info=True)
                # Mark as processed even on error
                xml_queue.append(('error', xml_id))

        self.log_debug(f"Producer {producer_id}: completed, processed {processed_count} files")
        # Signal that this producer is done
        xml_queue.append(None)

    def _database_consumer(self, xml_queue, result_queue, conn):
        """Consumer thread: writes results to database (single-threaded for DuckDB safety)"""
        batch_data = []
        total_processed = 0
        self.log_debug("Consumer thread started")

        shutdown_signals_received = 0

        while True:
            try:
                # Poll the deque for items (lock-free)
                if xml_queue:
                    item = xml_queue.popleft()
                else:
                    # No items available, small delay to prevent busy waiting
                    time.sleep(0.01)
                    continue

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
                    self.db_ops.execute_query("""
                        UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
                        WHERE xml_id = ?
                    """, (2, "Processing error", xml_id))  # CURRENT_PROCESSING_VERSION = 2
                    self.db_ops.commit()
                    continue

                # Add to batch
                batch_data.append(item)
                total_processed += 1

                # Process batch when it gets large enough
                if len(batch_data) >= self.BATCH_SIZE or len(batch_data) >= 1:  # Process every item for debugging
                    self.log_debug(f"Consumer: processing batch of {len(batch_data)} items (total: {total_processed})")
                    self._bulk_insert_batch(batch_data, conn)
                    result_queue.append(len(batch_data))  # Signal progress
                    batch_data = []
                else:
                    # Small delay to prevent busy waiting
                    time.sleep(0.01)

            except Exception as e:
                self.log_error(f"Consumer error: {e}", exc_info=True)
                break

        # Process remaining batch
        if batch_data:
            self.log_debug(f"Consumer: processing final batch of {len(batch_data)} items (total: {total_processed})")
            self._bulk_insert_batch(batch_data, conn)
            result_queue.append(len(batch_data))

        self.log_debug(f"Consumer: completed, processed {total_processed} total items")

    def _build_insert_from_dataclass(self, obj, table_name: str, exclude_fields: List[str] = None) -> Tuple[str, Tuple]:
        """Build INSERT statement and values tuple from dataclass using reflection"""
        if exclude_fields is None:
            exclude_fields = []

        # Get all fields from the dataclass, excluding specified ones
        field_names = [f.name for f in fields(obj) if f.name not in exclude_fields]

        # Build column list and placeholders
        columns = ', '.join(field_names)
        placeholders = ', '.join('?' for _ in field_names)

        # Build values tuple
        values = tuple(getattr(obj, field_name) for field_name in field_names)

        # Debug logging: show fields and values being inserted
        self.log_debug(f"INSERT {table_name}: fields={field_names}, values={values}")

        # Use INSERT OR IGNORE for reference/master tables to preserve first inserted record,
        # INSERT OR REPLACE for detail/transaction tables to handle legitimate duplicates
        if table_name in ('Charities', 'Addresses', 'XmlFiles', 'ZipFiles'):
            sql = f"INSERT OR IGNORE INTO {table_name} ({columns}) VALUES ({placeholders})"
        else:
            sql = f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})"

        return sql, values

    def _bulk_insert_batch(self, batch_data, conn=None):
        """Bulk insert a batch of processed XML data"""
        if conn is None:
            conn = self.db_ops.db_conn

        charities = []
        officers = []
        grants = []
        contractors = []
        contributions = []

        # Deduplicate charities by (ein, tax_year) - keep the one with the latest XML filename
        charity_map = {}
        for charity, officer_list, grant_list, contractor_list, contribution_list, address in batch_data:
            if charity:
                key = (charity.ein, charity.tax_year)
                # Compare XML filenames to keep the latest one (assuming sequential naming)
                if key not in charity_map or charity.xml_name > charity_map[key][0].xml_name:
                    charity_map[key] = (charity, officer_list, grant_list, contractor_list, contribution_list, address)

        # Process deduplicated charities
        addresses = []
        for charity, officer_list, grant_list, contractor_list, contribution_list, address in charity_map.values():
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

            if address:
                addresses.append(address)

        self.log_debug(f"Bulk insert batch: {len(charities)} charities (deduplicated), {len(officers)} officers, {len(grants)} grants, {len(contractors)} contractors, {len(contributions)} contributions, {len(addresses)} addresses")

        # Bulk insert charities using reflection
        if charities:
            # Set colocator to 'notyet' for new charities
            for charity in charities:
                charity.colocator = 'notyet'

            charity_data = []
            for charity in charities:
                sql, values = self._build_insert_from_dataclass(charity, 'Charities', ['charity_id'])
                charity_data.append(values)

            try:
                conn.executemany(sql, charity_data)
                self.log_debug(f"Inserted {len(charity_data)} charities")
            except Exception as e:
                self.log_error(f"Failed to insert charities: {e}", exc_info=True)
                conn.rollback()
                return

            # Get the charity IDs for related data
            if charities:
                ein_list = [c.ein for c in charities]
                tax_year_list = [c.tax_year for c in charities]
                placeholders = ','.join('?' for _ in ein_list)
                conn.execute(f"""
                    SELECT charity_id FROM Charities
                    WHERE ein IN ({placeholders}) AND tax_year IN ({placeholders})
                    ORDER BY charity_id
                """, ein_list + tax_year_list)
                charity_ids = [row[0] for row in conn.fetchall()]
            else:
                charity_ids = []

            # Bulk insert officers using reflection
            if officers:
                officer_data = []
                for officer in officers:
                    batch_index = officer.charity_id - 1
                    if 0 <= batch_index < len(charity_ids):
                        officer.charity_id = charity_ids[batch_index]
                        sql, values = self._build_insert_from_dataclass(officer, 'Officers', ['officer_id'])
                        officer_data.append(values)

                try:
                    conn.executemany(sql, officer_data)
                    self.log_debug(f"Inserted {len(officer_data)} officers")
                except Exception as e:
                    self.log_error(f"Failed to insert officers: {e}", exc_info=True)

            # Bulk insert grants using reflection
            if grants:
                grant_data = []
                for grant in grants:
                    sql, values = self._build_insert_from_dataclass(grant, 'Grants', ['grant_id'])
                    grant_data.append(values)

                try:
                    conn.executemany(sql, grant_data)
                    self.log_debug(f"Inserted {len(grant_data)} grants")
                except Exception as e:
                    self.log_error(f"Failed to insert grants: {e}", exc_info=True)

            # Bulk insert contractors using reflection
            if contractors:
                contractor_data = []
                for contractor in contractors:
                    sql, values = self._build_insert_from_dataclass(contractor, 'Contractors', ['contractor_id'])
                    contractor_data.append(values)

                try:
                    conn.executemany(sql, contractor_data)
                    self.log_debug(f"Inserted {len(contractor_data)} contractors")
                except Exception as e:
                    self.log_error(f"Failed to insert contractors: {e}", exc_info=True)

            # Bulk insert political contributions using reflection
            if contributions:
                contribution_data = []
                for contribution in contributions:
                    sql, values = self._build_insert_from_dataclass(contribution, 'PoliticalContributions', ['political_id'])
                    contribution_data.append(values)

                try:
                    conn.executemany(sql, contribution_data)
                    self.log_debug(f"Inserted {len(contribution_data)} contributions")
                except Exception as e:
                    self.log_error(f"Failed to insert contributions: {e}", exc_info=True)

            # Bulk insert addresses using reflection
            if addresses:
                address_data = []
                for address in addresses:
                    # Compute colocator for DCAddress objects if not already set
                    if hasattr(address, 'colocator') and address.colocator is None:
                        if address.po_box and address.zip_code:
                            po_box_stripped = address.po_box.strip()
                            if po_box_stripped:
                                address.colocator = f"PO:{po_box_stripped}:{address.zip_code}"
                        elif address.state and address.state.upper() not in DatabaseOperations.VALID_STATES:
                            address.colocator = f"FA:{address.state}"
                        else:
                            address.colocator = None

                    sql, values = self._build_insert_from_dataclass(address, 'Addresses', ['address_id'])
                    address_data.append(values)

                try:
                    conn.executemany(sql, address_data)
                    self.log_debug(f"Inserted {len(address_data)} addresses")
                except Exception as e:
                    self.log_error(f"Failed to insert addresses: {e}", exc_info=True)

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

            # Read XML content directly from ZIP file
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                with zip_ref.open(internal_path) as f:
                    xml_content = f.read()

            self.log_debug(f"Retrieved XML content for {filename}, size: {len(xml_content)} bytes")

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
                charity, officers, grants, contractors, contributions, address = self._parse_990_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990EZ":
                charity, officers, grants, contractors, contributions, address = self._parse_990ez_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990PF":
                charity, officers, grants, contractors, contributions, address = self._parse_990pf_data(root, filename, filer_ein, tax_year, form_type)
            else:
                self.log_error(f"Unsupported form type {form_type} in {filename}")
                return ('error', xml_id)

            if charity:
                self.log_debug(f"Successfully parsed {filename}: charity={charity.ein}, grants={len(grants)}, officers={len(officers)}, address={address is not None}")
                return charity, officers, grants, contractors, contributions, address
            else:
                self.log_error(f"Failed to extract charity data from {filename}")
                return ('error', xml_id)

        except Exception as e:
            self.log_error(f"Failed to process XML {filename}: {e}", exc_info=True)
            return ('error', xml_id)

    def _extract_form_type(self, root) -> str:
        """Extract form type from XML"""
        for xpath in XPATHS_990["form_type"] + XPATHS_990EZ["form_type"] + XPATHS_990PF["form_type"]:
            try:
                result = xpath(root)
                if result:
                    return result[0].text
            except:
                continue
        return "Unknown"

    def _extract_tax_year(self, root) -> int:
        """Extract tax year from XML"""
        for xpath in XPATHS_990["tax_year"] + XPATHS_990EZ["tax_year"] + XPATHS_990PF["tax_year"]:
            try:
                result = xpath(root)
                if result:
                    year_str = result[0].text
                    if year_str and year_str.isdigit():
                        return int(year_str)
            except:
                continue
        return 0

    def _extract_filer_ein(self, root) -> str:
        """Extract filer EIN from XML"""
        for xpath in XPATHS_990["filer_ein"] + XPATHS_990EZ["filer_ein"] + XPATHS_990PF["filer_ein"]:
            try:
                result = xpath(root)
                if result:
                    raw_ein = result[0].text.strip()
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        return formatted_ein
                    else:
                        return "Unknown"
            except:
                continue
        return "Unknown"

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str):
        """Parse Form 990 data"""
        charity, officers, grants, contractors, contributions, address = parse_990(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not charity:
            return None, [], [], [], [], None

        return charity, officers, grants, contractors, contributions, address

    def _parse_990ez_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str):
        """Parse Form 990EZ data"""
        charity, officers, grants, contractors, contributions, address = parse_990ez(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not charity:
            return None, [], [], [], [], None

        return charity, officers, grants, contractors, contributions, address

    def _parse_990pf_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str):
        """Parse Form 990PF data"""
        charity, officers, grants, contractors, contributions, address = parse_990pf(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        if not charity:
            return None, [], [], [], [], None

        return charity, officers, grants, contractors, contributions, address

    def _extract_grants_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCGrant]:
        """Extract grants from Form 990"""
        grants = []
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990")
        for grant_data in grants_data:
            grant = DCGrant(
                filer_ein=filer_ein,
                filer_name="",
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCGrant]:
        """Extract grants from Form 990EZ"""
        grants = []
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990EZ")
        for grant_data in grants_data:
            grant = DCGrant(
                filer_ein=filer_ein,
                filer_name="",
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCGrant]:
        """Extract grants from Form 990PF"""
        grants = []
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990PF")
        for grant_data in grants_data:
            grant = DCGrant(
                filer_ein=filer_ein,
                filer_name="",
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_contractors_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCContractor]:
        """Extract contractors from Form 990"""
        return []

    def _extract_contractors_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCContractor]:
        """Extract contractors from Form 990EZ"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_contractors_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCContractor]:
        """Extract contractors from Form 990PF"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCPoliticalContribution]:
        """Extract political contributions from Form 990"""
        return []

    def _extract_political_contributions_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCPoliticalContribution]:
        """Extract political contributions from Form 990EZ"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[DCPoliticalContribution]:
        """Extract political contributions from Form 990PF"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)


class GeocodingBatchStrategy(ProcessingStrategy):
    """Strategy for batch geocoding addresses"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, log_sql: bool = False):
        super().__init__(db_ops, logger, log_sql)

    def execute(self, batch: List[Tuple]) -> None:
        """Geolocate a batch of addresses"""
        addresses_to_geocode = []

        for address_id, canonical_address, po_box, zip_code in batch:
            # Skip if PO box
            if po_box and po_box.strip():
                colocator = f"PO:{po_box.strip()}:{zip_code or ''}"
                self.db_ops.execute_query("""
                    UPDATE Addresses SET colocator = ? WHERE address_id = ?
                """, (colocator, address_id))
                continue

            # Prepare address for geocoding
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
            import censusgeocode as cg

            # Prepare batch for censusgeocode
            batch_addresses = []
            for addr in addresses_to_geocode:
                batch_addresses.append({
                    'address': f"{addr['street']}, {addr['city']}, {addr['state']} {addr['zip']}"
                })

            # Geocode batch
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
                    self.db_ops.execute_query("""
                        INSERT INTO Geocoding (address_hash, normalized_address, latitude, longitude, geocoding_status)
                        VALUES (?, ?, ?, ?, 'success')
                    """, (hash(result['address']), result['address'], lat, lon))

                    geocoding_id = self.db_ops.execute_query("SELECT last_insert_rowid()").fetchone()[0]

                    # Update address
                    self.db_ops.execute_query("""
                        UPDATE Addresses SET geocoding_id = ?, latitude = ?, longitude = ?, colocator = ?
                        WHERE address_id = ?
                    """, (geocoding_id, lat, lon, colocator, address_id))
                else:
                    # Failed
                    self.db_ops.execute_query("""
                        INSERT INTO Geocoding (address_hash, normalized_address, geocoding_status)
                        VALUES (?, ?, 'failed')
                    """, (hash(result.get('address', '')), result.get('address', '')))

                    geocoding_id = self.db_ops.execute_query("SELECT last_insert_rowid()").fetchone()[0]

                    # Update address with failed geocoding
                    self.db_ops.execute_query("""
                        UPDATE Addresses SET geocoding_id = ? WHERE address_id = ?
                    """, (geocoding_id, address_id))

            self.db_ops.commit()
            self.log_info(f"Geolocated batch of {len(addresses_to_geocode)} addresses")

        except Exception as e:
            self.log_error(f"Failed to geolocate batch: {e}", exc_info=True)


class AddressMatchingStrategy(ProcessingStrategy):
    """Strategy for matching grants by address/colocator"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, log_sql: bool = False):
        super().__init__(db_ops, logger, log_sql)

    def execute(self) -> int:
        """Match grants with unknown EINs by address/colocator"""
        # Get grants without EINs
        result = self.db_ops.execute_query("""
            SELECT grant_id, filer_ein, grant_amt, tax_year
            FROM Grants
            WHERE grant_ein IS NULL OR grant_ein = ''
        """)
        grants = result.fetchall()

        matched_count = 0
        for grant_id, filer_ein, grant_amt, tax_year in grants:
            # Try to find matching charity by address
            matched_ein = self._find_charity_by_grant_info(grant_id, filer_ein, grant_amt, tax_year)
            if matched_ein:
                self.db_ops.execute_query("""
                    UPDATE Grants SET grant_ein = ? WHERE grant_id = ?
                """, (matched_ein, grant_id))
                matched_count += 1

        self.db_ops.commit()
        self.log_info(f"Matched {matched_count} grants by address/colocator")
        return matched_count

    def _find_charity_by_grant_info(self, grant_id: int, filer_ein: str, grant_amt: float, tax_year: int) -> Optional[str]:
        """Find charity EIN by grant information"""
        # Get grant details
        result = self.db_ops.execute_query("""
            SELECT filer_name, grantee_name, grantee_address, grantee_zip, grantee_po_box
            FROM Grants
            WHERE grant_id = ?
        """, (grant_id,))
        grant_info = result.fetchone()

        if not grant_info:
            return None

        filer_name, grantee_name, grantee_address, grantee_zip, grantee_po_box = grant_info

        # Try exact address match
        result = self.db_ops.execute_query("""
            SELECT DISTINCT c.ein
            FROM Charities c
            JOIN Addresses a ON c.ein = a.ein
            WHERE c.tax_year = ?
            AND LOWER(TRIM(a.canonical_address)) = LOWER(TRIM(?))
        """, (tax_year, grantee_address or ""))

        row = result.fetchone()
        if row:
            return row[0]

        # Try name + ZIP match
        if grantee_name and grantee_zip:
            result = self.db_ops.execute_query("""
                SELECT DISTINCT c.ein
                FROM Charities c
                JOIN Addresses a ON c.ein = a.ein
                WHERE c.tax_year = ?
                AND LOWER(TRIM(c.filer_name)) = LOWER(TRIM(?))
                AND a.zip_code = ?
            """, (tax_year, grantee_name, grantee_zip))

            row = result.fetchone()
            if row:
                return row[0]

        # Try colocator match
        if grantee_address and grantee_zip:
            result = self.db_ops.execute_query("""
                SELECT DISTINCT c.ein, c.colocator
                FROM Charities c
                JOIN Addresses a ON c.ein = a.ein
                WHERE c.tax_year = ?
                AND a.zip_code = ?
                AND c.colocator LIKE 'LL:%'
            """, (tax_year, grantee_zip))

            candidates = result.fetchall()
            for ein, colocator in candidates:
                if colocator and colocator.startswith('LL:'):
                    return ein

        return None


class StubCharityCreationStrategy(ProcessingStrategy):
    """Strategy for creating stub charities for unmatched grants"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, log_sql: bool = False):
        super().__init__(db_ops, logger, log_sql)

    def execute(self, name: str, address: str, zip_code: str, po_box: str, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants"""
        # Generate a pseudo-EIN for stub records
        stub_ein = f"STUB{hash(name + (address or '') + str(tax_year)) % 1000000000:09d}"

        # Check if stub already exists
        result = self.db_ops.execute_query("SELECT 1 FROM Charities WHERE ein = ?", (stub_ein,))
        if result.fetchone():
            return stub_ein

        # Create stub charity
        from irs990processorDC import Charity as DBCharity
        charity = DBCharity(
            ein=stub_ein,
            tax_year=tax_year,
            filer_name=name or "Unknown",
            xml_name=f"stub_{stub_ein}_{tax_year}"
        )
        charity_id = self.db_ops.insert_charity(charity)

        # Create address record if we have address info
        if address or zip_code:
            from irs990processorDC import Address as DBAddress
            addr = DBAddress(
                ein=stub_ein,
                name=name or "Unknown",
                canonical_address=address or "",
                zip_code=zip_code,
                po_box=po_box,
                address_type="grantee"
            )
            self.db_ops.insert_address(addr)

        return stub_ein