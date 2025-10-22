#!/usr/bin/env python3
"""
geolocation_processor.py - Multithreaded address geocoding for IRS 990 data

This module handles geocoding of addresses using the census API with multithreading,
storing latitude/longitude coordinates for address matching.
"""

import logging
import threading
import queue
import time
from typing import List, Tuple, Optional, Dict, Any
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import censusgeocode as cg
except ImportError:
    cg = None

from database_operations import DatabaseOperations
from models import Address
from constants import VALID_STATES, STATE_NAME_TO_ABBREV, PO_BOX_REGEX, PO_BOX_NUMBER_REGEX
from logging_utils import log_info, log_error, log_debug, log_warning

# Set up logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GeolocationProcessor:
    """Handles multithreaded address geocoding operations"""

    # Threading configuration
    MAX_API_WORKERS = 4  # Up to 4 concurrent API calls
    BATCH_SIZE = 5000    # Addresses per batch (under census API limit)
    QUEUE_SIZE = 1000    # Size of work/result queues

    def __init__(self, db_ops: DatabaseOperations, quiet: bool = False):
        self.db_ops = db_ops
        self.quiet = quiet

    def _normalize_state(self, state: str) -> Optional[str]:
        """Normalize state to uppercase abbreviation, handling full names and case issues"""
        if not state:
            return None

        # Uppercase the input
        state_upper = state.upper().strip()

        # If it's already a valid abbreviation, return it
        if state_upper in self.VALID_STATES:
            return state_upper

        # If it's a full state name, map to abbreviation
        if state_upper in self.STATE_NAME_TO_ABBREV:
            return self.STATE_NAME_TO_ABBREV[state_upper]

        # Not recognized
        return None

    def _detect_po_box_from_address_lines(self, address: Address) -> Optional[str]:
        """Detect PO Box from address lines using regex"""
        address_lines_to_check = []
        if address.address_line1:
            address_lines_to_check.append(address.address_line1)
        if address.address_line2:
            address_lines_to_check.append(address.address_line2)

        for line_idx, line in enumerate(address_lines_to_check):
            match = PO_BOX_REGEX.search(line)
            if match:
                po_box_str = match.group(1)
                number_match = PO_BOX_NUMBER_REGEX.match(po_box_str)
                if number_match:
                    po_box = number_match.group(0)
                    if not self.quiet:
                        log_info(logger, f"Fallback PO Box detection for address {address.address_id}: found '{po_box}' in line{line_idx + 1}='{line}'")
                    return po_box
        return None


    def geolocate_addresses(self) -> int:
        """Geolocate addresses using multithreaded census API calls"""
        if not self.quiet:
            log_info(logger, "Starting multithreaded address geolocation")

        # Check if censusgeocode is available
        if cg is None:
            if not self.quiet:
                log_warning(logger, "censusgeocode library not available. Skipping geocoding. "
                                "To enable geocoding, install with: pip install censusgeocode")
            return 0

        # Create work and result queues
        work_queue = queue.Queue(maxsize=self.QUEUE_SIZE)
        result_queue = queue.Queue(maxsize=self.QUEUE_SIZE)

        # Start consumer thread
        consumer_thread = threading.Thread(
            target=self._geocoding_consumer,
            args=(result_queue,),
            daemon=True
        )
        consumer_thread.start()
        if not self.quiet:
            log_info(logger, "Started consumer thread")

        # Start API worker threads
        api_threads = []
        for i in range(self.MAX_API_WORKERS):
            thread = threading.Thread(
                target=self._geocoding_worker,
                args=(work_queue, result_queue, i),
                daemon=True
            )
            thread.start()
            api_threads.append(thread)
        if not self.quiet:
            log_info(logger, f"Started {self.MAX_API_WORKERS} API worker threads")

        total_geolocated = 0
        batch_count = 0

        logger.info("Starting batch processing loop")
        with tqdm(desc="Geocoding addresses", unit="addr") as pbar:
            while True:
                batch_count += 1
                if not self.quiet:
                    log_info(logger, f"Batch {batch_count}: Getting addresses for geocoding")

                # Get next batch of addresses
                batch_addresses = self.db_ops.get_addresses_for_geocoding(limit=self.BATCH_SIZE)
                if not batch_addresses:
                    if not self.quiet:
                        log_info(logger, "No more addresses to process, breaking batch loop")
                    break

                if not self.quiet:
                    log_info(logger, f"Batch {batch_count}: Processing {len(batch_addresses)} addresses")

                # Process batch with multithreading
                batch_geolocated = self._geolocate_batch_multithreaded(
                    batch_addresses, work_queue, result_queue, batch_count
                )
                total_geolocated += batch_geolocated
                if not self.quiet:
                    log_info(logger, f"Batch {batch_count}: Geolocation complete, {batch_geolocated} addresses processed")

                pbar.update(batch_geolocated)  # Update by actual geolocated count, not batch size

        if not self.quiet:
            log_info(logger, f"All batches processed, total geolocated: {total_geolocated}")

        # Signal workers to stop
        if not self.quiet:
            log_info(logger, "Signaling workers to stop")
        for _ in range(self.MAX_API_WORKERS):
            work_queue.put(None)

        # Wait for all threads to complete
        if not self.quiet:
            log_info(logger, "Waiting for threads to complete")
        for i, thread in enumerate(api_threads):
            if not self.quiet:
                log_info(logger, f"Waiting for API worker {i}")
            thread.join(timeout=5.0)
            if thread.is_alive():
                if not self.quiet:
                    log_warning(logger, f"API worker {i} did not stop cleanly")

        if not self.quiet:
            log_info(logger, "Waiting for consumer thread")
        consumer_thread.join(timeout=5.0)
        if consumer_thread.is_alive():
            if not self.quiet:
                log_warning(logger, "Consumer thread did not stop cleanly")

        if not self.quiet:
            log_info(logger, f"Geolocation complete: {total_geolocated} addresses geolocated")
        return total_geolocated

    def _geolocate_batch_multithreaded(self, batch: List[Address], work_queue: queue.Queue, result_queue: queue.Queue, batch_num: int) -> int:
        """Geolocate a batch of addresses using multithreading"""
        if not self.quiet:
            log_info(logger, f"Batch {batch_num}: Processing {len(batch)} addresses with multithreading")

        # Parse addresses and prepare work items
        work_items = self._prepare_geocoding_work(batch)
        if not self.quiet:
            log_info(logger, f"Batch {batch_num}: Prepared {len(work_items)} work items")
        if not work_items:
            if not self.quiet:
                log_info(logger, f"Batch {batch_num}: No work items to process")
            return 0

        # Submit work to queue
        if not self.quiet:
            log_info(logger, f"Batch {batch_num}: Submitting {len(work_items)} work items to queue")
        for work_item in work_items:
            work_queue.put(work_item)
        if not self.quiet:
            log_info(logger, f"Batch {batch_num}: All work items submitted")

        # Wait for all results - results are lists of (address_id, geocoding_id) tuples
        total_expected_results = len(work_items)  # One result list per work item
        completed_results = 0
        geolocated_count = 0

        if not self.quiet:
            log_info(logger, f"Batch {batch_num}: Waiting for {total_expected_results} work item results")
        while completed_results < total_expected_results:
            if not self.quiet:
                log_debug(logger, f"Batch {batch_num}: Waiting for result {completed_results + 1}/{total_expected_results}")
            try:
                result = result_queue.get(timeout=30.0)  # 30 second timeout
                if result is None:  # Shutdown signal
                    if not self.quiet:
                        log_info(logger, f"Batch {batch_num}: Received shutdown signal")
                    break

                # Result is a list of (address_id, geocoding_id) tuples from one work item
                if isinstance(result, list):
                    geolocated_count += len(result)
                    completed_results += 1
                    if not self.quiet:
                        log_debug(logger, f"Batch {batch_num}: Received result {completed_results}/{total_expected_results}, addresses in result: {len(result)}, total geolocated: {geolocated_count}")
                else:
                    if not self.quiet:
                        log_warning(logger, f"Batch {batch_num}: Unexpected result type: {type(result)}, value: {result}")
                    completed_results += 1

            except queue.Empty:
                if not self.quiet:
                    log_warning(logger, f"Batch {batch_num}: Timeout waiting for geocoding results ({completed_results}/{total_expected_results} completed)")
                break

        if not self.quiet:
            log_info(logger, f"Batch {batch_num}: Multithreaded processing complete: {geolocated_count} addresses geolocated from {completed_results} work items")
        return geolocated_count

    def _prepare_geocoding_work(self, batch: List[Address]) -> List[Dict[str, Any]]:
        """Prepare geocoding work items from address batch"""
        if not self.quiet:
            log_info(logger, f"Preparing geocoding work for {len(batch)} addresses")
        work_items = []
        processed_count = 0
        skipped_po_box = 0
        failed_parse = 0

        for address in batch:
            if not self.quiet:
                log_debug(logger, f"Processing address {address.address_id}: {address.canonical_address}")

            # Check for PO Box detection during geocoding
            fallback_po_box = self._detect_po_box_from_address_lines(address)

            if fallback_po_box:
                # Update the record with detected PO Box and set colocator
                colocator = f"PO:{fallback_po_box}:{address.zip_code or ''}"
                if not self.quiet:
                    log_info(logger, f"Skipping PO Box address {address.address_id}, setting colocator: {colocator}")
                self.db_ops.update_address_po_box_and_colocator(address.address_id, fallback_po_box, colocator)
                skipped_po_box += 1
                continue

            if address.canonical_address and address.canonical_address.strip():
                work_item = {
                    'address_ids': [address.address_id],
                    'street': f"{address.address_line1 or ''} {address.address_line2 or ''}".strip(),
                    'city': address.city or '',
                    'state': address.state or '',
                    'zip': address.zip_code or '',
                    'canonical_address': address.canonical_address
                }
                work_items.append(work_item)
                processed_count += 1
                if not self.quiet:
                    log_debug(logger, f"Created work item for address {address.address_id}")
            else:
                if not self.quiet:
                    log_warning(logger, f"Failed to create work item for address {address.address_id}: no canonical address")
                failed_parse += 1

        if not self.quiet:
            log_info(logger, f"Work preparation complete: {processed_count} work items created, {skipped_po_box} PO boxes skipped, {failed_parse} failed to parse")
        return work_items

    def _geocoding_worker(self, work_queue: queue.Queue, result_queue: queue.Queue, worker_id: int):
        """Worker thread that makes API calls"""
        if not self.quiet:
            log_info(logger, f"Geocoding worker {worker_id} started")

        while True:
            try:
                if not self.quiet:
                    log_debug(logger, f"Worker {worker_id}: Waiting for work item")
                work_item = work_queue.get(timeout=1.0)
                if work_item is None:  # Shutdown signal
                    if not self.quiet:
                        log_info(logger, f"Worker {worker_id}: Received shutdown signal")
                    break

                if not self.quiet:
                    log_debug(logger, f"Worker {worker_id}: Processing work item for {len(work_item['address_ids'])} addresses")

                # Process the geocoding work
                results = self._process_geocoding_work(work_item)
                if not self.quiet:
                    log_debug(logger, f"Worker {worker_id}: Completed work item, {len(results)} address results")

                # Send the entire batch of results to consumer for bulk processing
                result_queue.put(results)

                if not self.quiet:
                    log_debug(logger, f"Worker {worker_id}: Batch results sent to queue")

            except queue.Empty:
                if not self.quiet:
                    log_debug(logger, f"Worker {worker_id}: Queue empty, continuing")
                continue
            except Exception as e:
                if not self.quiet:
                    log_error(logger, f"Worker {worker_id} error: {e}", exc_info=True)
                # Send empty result on error
                result_queue.put([])

        if not self.quiet:
            log_info(logger, f"Geocoding worker {worker_id} stopped")

    def _process_geocoding_work(self, work_item: Dict[str, Any]) -> List[Tuple[str, str]]:
        """Process a single geocoding work item and return list of (address_id, geocoding_id) tuples"""
        try:
            if not self.quiet:
                log_debug(logger, f"Processing geocoding work for {len(work_item['address_ids'])} addresses: {work_item['canonical_address']}")

            # Prepare batch for censusgeocode (single item batch)
            batch_addresses = [{
                'id': 0,
                'street': work_item['street'],
                'city': work_item['city'],
                'state': work_item['state'],
                'zip': work_item['zip']
            }]

            if not self.quiet:
                log_debug(logger, f"Making API call for: {work_item['street']}, {work_item['city']}, {work_item['state']} {work_item['zip']}")

            # Make API call
            results = cg.addressbatch(batch_addresses)
            if not self.quiet:
                log_debug(logger, f"API call returned {len(results) if results else 0} results")

            if not results:
                if not self.quiet:
                    log_warning(logger, f"API returned no results for: {work_item['canonical_address']}")
                # Failed geocoding - create geocoding record and return results
                geocoding_id = self.db_ops.insert_geocoding_record(
                    hash(work_item['canonical_address']), work_item['canonical_address'], status='failed'
                )
                return [(address_id, geocoding_id) for address_id in work_item['address_ids']]

            result = results[0]
            lat = result.get('lat')
            lon = result.get('lon')
            if not self.quiet:
                log_debug(logger, f"API result: lat={lat}, lon={lon}")

            if lat is not None and lon is not None and str(lat).strip() and str(lon).strip():
                # Success
                lat = round(float(lat), 4)
                lon = round(float(lon), 4)
                colocator = f"LL:{lat}:{lon}"
                if not self.quiet:
                    log_info(logger, f"Successfully geocoded '{work_item['canonical_address']}' -> {colocator}")

                # Insert geocoding record
                geocoding_id = self.db_ops.insert_geocoding_record(
                    hash(work_item['canonical_address']), work_item['canonical_address'], lat, lon, 'success'
                )

                return [(address_id, geocoding_id) for address_id in work_item['address_ids']]
            else:
                if not self.quiet:
                    log_warning(logger, f"API returned invalid lat/lon for: {work_item['canonical_address']}")
                # Failed - create geocoding record and return results
                geocoding_id = self.db_ops.insert_geocoding_record(
                    hash(work_item['canonical_address']), work_item['canonical_address'], status='failed'
                )
                return [(address_id, geocoding_id) for address_id in work_item['address_ids']]

        except Exception as e:
            if not self.quiet:
                log_error(logger, f"Failed to process geocoding work for {work_item['canonical_address']}: {e}", exc_info=True)
            # On error, return empty results
            return []

    def _geocoding_consumer(self, result_queue: queue.Queue):
        """Consumer thread that processes results and updates database"""
        if not self.quiet:
            log_info(logger, "Geocoding consumer started")
        total_geolocated = 0
        results_processed = 0

        while True:
            try:
                if not self.quiet:
                    log_debug(logger, "Consumer: Waiting for result")
                result = result_queue.get(timeout=1.0)
                if result is None:  # Shutdown signal
                    if not self.quiet:
                        log_info(logger, "Consumer: Received shutdown signal")
                    break

                # Result is a list of (address_id, geocoding_id) tuples from one work item
                if isinstance(result, list):
                    if not self.quiet:
                        log_debug(logger, f"Consumer: Processing batch of {len(result)} results")
                    self._bulk_update_geocoding(result)
                    total_geolocated += len(result)
                else:
                    if not self.quiet:
                        log_warning(logger, f"Consumer: Unexpected result type: {type(result)}")

                results_processed += 1
                if not self.quiet:
                    log_debug(logger, f"Consumer: Processed result {results_processed}, total geolocated: {total_geolocated}")

            except queue.Empty:
                if not self.quiet:
                    log_debug(logger, "Consumer: Queue empty, continuing")
                continue

        if not self.quiet:
            log_info(logger, f"Geocoding consumer stopped. Processed {results_processed} results, total geolocated: {total_geolocated}")

    def _bulk_update_geocoding(self, updates: List[Tuple[int, str]]):
        """Bulk update geocoding results in database"""
        if not updates:
            return

        if not self.quiet:
            log_debug(logger, f"Bulk updating {len(updates)} geocoding results")

        # Group updates by geocoding_id for efficiency
        geocoding_groups = {}
        for address_id, geocoding_id in updates:
            if geocoding_id not in geocoding_groups:
                geocoding_groups[geocoding_id] = []
            geocoding_groups[geocoding_id].append(address_id)

        # Update each group
        for geocoding_id, address_ids in geocoding_groups.items():
            # Get colocator from geocoding record
            geocoding_result = self.db_ops.execute_query(
                "SELECT latitude, longitude FROM Geocoding WHERE geocoding_id = ?",
                (geocoding_id,)
            ).fetchone()

            colocator = None
            if geocoding_result and geocoding_result[0] and geocoding_result[1]:
                lat, lon = geocoding_result[0], geocoding_result[1]
                colocator = f"LL:{lat}:{lon}"

            # Bulk update addresses
            for address_id in address_ids:
                self.db_ops.update_address_geocoding(address_id, geocoding_id, colocator)

    # Keep the old single-threaded method for compatibility
    def _geolocate_batch(self, batch: List[Address]) -> int:
        """Legacy single-threaded geocoding method"""
        work_items = self._prepare_geocoding_work(batch)
        if not work_items:
            return 0

        geolocated_count = 0
        for work_item in work_items:
            count = self._process_geocoding_work(work_item)
            geolocated_count += count

        return geolocated_count