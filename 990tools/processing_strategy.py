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
import queue
from pathlib import Path
import sqlite3
from io import BytesIO
from lxml import etree as ET
from dataclasses import fields
import zipfile

from database_operations import DatabaseOperations
from models import Charity as DCCharity, Officer as DCOfficer, Grant as DCGrant, Contractor as DCContractor, PoliticalContribution as DCPoliticalContribution, Address as DCAddress
from parse_990 import parse_990
from parse_990ez import parse_990ez
from parse_990pf import parse_990pf
from parse_utils import parse_grants
from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF
from geolocation_processor import GeolocationProcessor
from address_matcher import AddressMatcher
from constants import VALID_STATES
from zip_processor import ZipProcessor
from logging_utils import log_info, log_error, log_debug, log_warning


class ProcessingStrategy(ABC):
    """Abstract base class for processing strategies"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        self.db_ops = db_ops
        self.logger = logger
        self.quiet = quiet

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the processing strategy"""
        pass

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not self.quiet:
            log_info(self.logger, msg, ein, *args)

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, ein, exc_info, *args)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not self.quiet:
            log_debug(self.logger, msg, ein, *args)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, ein, *args)


class ParallelXMLProcessingStrategy(ProcessingStrategy):
    """Strategy for parallel XML file processing using producer-consumer pattern"""

    MAX_WORKERS = 16
    QUEUE_SIZE = 1000
    BATCH_SIZE = 100
    STALL_THRESHOLD = 30

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, workers: int = MAX_WORKERS, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.workers = workers

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
        """Process XML files using producer-consumer pattern with thread-safe queues"""
        xml_files = self._get_xml_files_to_process()
        if max_files:
            xml_files = xml_files[:max_files]
        if not xml_files:
            return 0

        num_producers = min(self.workers, len(xml_files))
        self.log_info(f"Processing {len(xml_files)} files with {num_producers} producers")

        # Use thread-safe queues with backpressure
        xml_queue = queue.Queue(maxsize=self.QUEUE_SIZE)
        progress_queue = queue.Queue(maxsize=10)

        # Start consumer thread (single writer to database)
        consumer_thread = threading.Thread(
            target=self._database_consumer,
            args=(xml_queue, progress_queue, num_producers, self.db_ops.db_conn)
        )
        consumer_thread.daemon = True
        consumer_thread.start()

        # Start producer threads
        producer_threads = []
        for i in range(num_producers):
            t = threading.Thread(target=self._xml_producer, args=(xml_files, xml_queue, i, num_producers))
            t.daemon = True
            producer_threads.append(t)
            t.start()

        # Wait for producers to finish (parse-heavy operations)
        for i, t in enumerate(producer_threads):
            t.join(timeout=60.0)
            if t.is_alive():
                self.log_error(f"Producer {i} timeout")
            else:
                self.log_info(f"Producer {i} done")

        # Drain queue and wait for consumer
        xml_queue.join()
        consumer_thread.join(timeout=30.0)
        if consumer_thread.is_alive():
            self.log_error("Consumer timeout")

        # Tally progress from consumer signals
        total_processed = 0
        try:
            while True:
                batch_size = progress_queue.get_nowait()
                total_processed += abs(batch_size)
                if batch_size < 0:
                    self.log_error(f"Error in batch of {-batch_size}")
        except queue.Empty:
            pass

        self.log_info(f"Complete: {total_processed} files")
        return total_processed

    def _xml_producer(self, xml_files, xml_queue, producer_id, num_producers):
        """Producer thread: parses XML and sends results to consumer"""
        processed = 0
        start = producer_id
        for i in range(start, len(xml_files), num_producers):
            xml_id, path, filename, internal = xml_files[i]
            try:
                result = self._process_single_xml(xml_id, path, filename, internal)
                xml_queue.put(result, block=True)
                processed += 1
                if processed % 50 == 0:
                    self.log_info(f"Producer {producer_id}: {processed} queued")
            except Exception as e:
                self.log_error(f"Producer {producer_id} error on {filename}: {e}", exc_info=True)
                xml_queue.put(('error', xml_id, str(e)), block=True)
        xml_queue.put(None)  # Sentinel
        self.log_info(f"Producer {producer_id} done: {processed} files")

    def _database_consumer(self, xml_queue, progress_queue, num_expected, conn):
        """Consumer thread: writes results to database (single-threaded for DuckDB safety)"""
        batch_data = []
        total = 0
        signals = 0
        while signals < num_expected:
            item = xml_queue.get(block=True)
            if item is None:
                signals += 1
                xml_queue.task_done()
                continue
            if isinstance(item, tuple) and item[0] == 'error':
                xml_id = item[1]
                msg = item[2] if len(item) > 2 else "Error"
                self.db_ops.execute_query(
                    "UPDATE XmlFiles SET processed=TRUE, processing_version=2, error_message=? WHERE xml_id=?",
                    (msg, xml_id)
                )
                self.db_ops.commit()
                xml_queue.task_done()
                continue
            batch_data.append(item)
            total += 1
            xml_queue.task_done()
            if len(batch_data) >= self.BATCH_SIZE:
                try:
                    self._bulk_insert_batch(batch_data, conn)
                    progress_queue.put(len(batch_data))
                except Exception as e:
                    self.log_error(f"Batch error: {e}", exc_info=True)
                    progress_queue.put(-len(batch_data))
                batch_data = []

        # Final batch
        if batch_data:
            try:
                self._bulk_insert_batch(batch_data, conn)
                progress_queue.put(len(batch_data))
            except Exception as e:
                self.log_error(f"Final batch error: {e}", exc_info=True)
                progress_queue.put(-len(batch_data))

        self.log_info(f"Consumer done: {total} items, {signals} signals")

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

        # Check database connection state
        try:
            # Test connection with a simple query
            test_result = conn.execute("SELECT 1").fetchone()
            self.log_info(f"Database connection test: OK (result={test_result})")
        except Exception as e:
            self.log_error(f"Database connection test FAILED: {e}")
            return

        self.log_info(f"Bulk insert batch: STARTING with {len(batch_data)} items from queue")
        self.log_info(f"Bulk insert batch: DEBUG - batch_data type: {type(batch_data)}, length: {len(batch_data)}")
        if batch_data:
            self.log_info(f"Bulk insert batch: DEBUG - first item type: {type(batch_data[0])}, length: {len(batch_data[0]) if hasattr(batch_data[0], '__len__') else 'N/A'}")

        charities = []
        officers = []
        grants = []
        contractors = []
        contributions = []

        # Process charities without deduplication (let database handle uniqueness constraints)
        addresses = []
        for charity, officer_list, grant_list, contractor_list, contribution_list, address in batch_data:
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

                if address:
                    addresses.append(address)

        self.log_info(f"Bulk insert batch: Processing {len(charities)} charities, {len(officers)} officers, {len(grants)} grants, {len(contractors)} contractors, {len(contributions)} contributions, {len(addresses)} addresses")

        # Bulk insert charities using reflection
        if charities:
            self.log_info(f"Bulk insert: Processing {len(charities)} charities")
            # Set colocator to 'notyet' for new charities
            for charity in charities:
                charity.colocator = 'notyet'

            charity_data = []
            for charity in charities:
                sql, values = self._build_insert_from_dataclass(charity, 'Charities', ['charity_id'])
                charity_data.append(values)

            try:
                self.log_info(f"Bulk insert: STARTING charity insert for {len(charity_data)} records")
                self.log_info(f"Bulk insert: DEBUG - charity SQL: {sql}")
                self.log_info(f"Bulk insert: DEBUG - first charity values: {charity_data[0] if charity_data else 'None'}")
                conn.executemany(sql, charity_data)
                self.log_info(f"Bulk insert: FINISHED charity insert for {len(charity_data)} records")
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
                for i, address in enumerate(addresses):
                    # Compute colocator for DCAddress objects if not already set
                    if hasattr(address, 'colocator') and address.colocator is None:
                        if address.po_box and address.zip_code:
                            po_box_stripped = address.po_box.strip()
                            if po_box_stripped:
                                address.colocator = f"PO:{po_box_stripped}:{address.zip_code}"
                        elif address.state and address.state.upper() not in VALID_STATES:
                            address.colocator = f"FA:{address.state}"
                        else:
                            address.colocator = None

                    # Set owner_id for charity addresses (link to charity that owns this address)
                    if hasattr(address, 'address_type') and address.address_type == 'charity':
                        # Find the charity that owns this address by matching index in batch
                        if i < len(charity_ids):
                            address.owner_id = charity_ids[i]

                    sql, values = self._build_insert_from_dataclass(address, 'Addresses', ['address_id'])
                    address_data.append(values)

                try:
                    conn.executemany(sql, address_data)
                    self.log_debug(f"Inserted {len(address_data)} addresses")
                except Exception as e:
                    self.log_error(f"Failed to insert addresses: {e}", exc_info=True)

        try:
            self.log_info("Bulk insert: STARTING batch commit")
            conn.commit()
            self.log_info("Bulk insert: FINISHED batch commit successful")
        except Exception as e:
            self.log_error(f"Failed to commit batch: {e}", exc_info=True)
            try:
                conn.rollback()
                self.log_info("Bulk insert: Rollback completed")
            except Exception as rollback_e:
                self.log_error(f"Failed to rollback: {rollback_e}", exc_info=True)
            raise  # Re-raise to propagate the error


    def _process_single_xml(self, xml_id: int, zip_path: str, filename: str, internal_path: str):
        """Process a single XML file"""
        try:
            self.log_debug(f"Processing XML {filename} (ID: {xml_id})")

            # Read XML content directly from ZIP file using cached connection
            xml_content = self._extract_xml_from_zip(zip_path, internal_path)

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
                self.log_info(f"Unsupported form type {form_type} in {filename}")
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
    def _extract_xml_from_zip(self, zip_path: str, internal_path: str) -> bytes:
        """Extract XML content from ZIP file using cached connection"""
        zip_key = str(zip_path)

        with ZipProcessor._zip_cache_lock:
            if zip_key not in ZipProcessor._zip_cache:
                # Open ZIP file and cache the connection
                ZipProcessor._zip_cache[zip_key] = zipfile.ZipFile(zip_path, 'r')
                self.log_debug(f"Opened and cached ZIP connection for {zip_path}")

            zip_ref = ZipProcessor._zip_cache[zip_key]

        # Extract XML content from cached connection
        with zip_ref.open(internal_path) as f:
            return f.read()

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
    """Strategy for batch geocoding addresses - DEPRECATED: Use GeolocationProcessor instead"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.log_warning("GeocodingBatchStrategy is deprecated. Use GeolocationProcessor instead.")

    def execute(self, batch: List[DCAddress]) -> int:
        """Geolocate a batch of addresses - DEPRECATED"""
        self.log_warning("GeocodingBatchStrategy.execute() is deprecated. Use GeolocationProcessor.geolocate_addresses() instead.")
        processor = GeolocationProcessor(self.db_ops)
        return processor._geolocate_batch(batch)


class AddressMatchingStrategy(ProcessingStrategy):
    """Strategy for matching grants by address/colocator - DEPRECATED: Use AddressMatcher instead"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.log_warning("AddressMatchingStrategy is deprecated. Use AddressMatcher instead.")

    def execute(self) -> int:
        """Match grants with unknown EINs by address/colocator - DEPRECATED"""
        self.log_warning("AddressMatchingStrategy.execute() is deprecated. Use AddressMatcher.match_grants_by_address() instead.")
        matcher = AddressMatcher(self.db_ops)
        return matcher.match_grants_by_address()


class StubCharityCreationStrategy(ProcessingStrategy):
    """Strategy for creating stub charities for unmatched grants - DEPRECATED: Use AddressMatcher instead"""

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger, quiet: bool = False):
        super().__init__(db_ops, logger, quiet)
        self.log_warning("StubCharityCreationStrategy is deprecated. Use AddressMatcher instead.")

    def execute(self, name: str, address: str, zip_code: str, po_box: str, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants - DEPRECATED"""
        self.log_warning("StubCharityCreationStrategy.execute() is deprecated. Use AddressMatcher._create_stub_charity_for_grant() instead.")
        # Generate a pseudo-EIN for stub records
        stub_ein = f"STUB{hash(name + (address or '') + str(tax_year)) % 1000000000:09d}"

        # Check if stub already exists
        result = self.db_ops.execute_query("SELECT 1 FROM Charities WHERE ein = ?", (stub_ein,))
        if result.fetchone():
            return stub_ein

        # Create stub charity
        from models import Charity as DBCharity
        charity = DBCharity(
            ein=stub_ein,
            tax_year=tax_year,
            filer_name=name or "Unknown",
            xml_name=f"stub_{stub_ein}_{tax_year}"
        )
        charity_id = self.db_ops.insert_charity(charity)

        # Create address record if we have address info
        if address or zip_code:
            from models import Address as DBAddress
            addr = DBAddress(
                ein=stub_ein,
                name=name or "Unknown",
                zip_code=zip_code,
                po_box=po_box,
                canonical_address=address or "",
                address_type="grantee"
            )
            self.db_ops.insert_address(addr)

        return stub_ein