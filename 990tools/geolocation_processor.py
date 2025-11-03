#!/usr/bin/env python3
"""
geolocation_processor.py - Simplified geolocation processor for Phase 2 API calls

This module handles geocoding API calls using a simple ThreadPoolExecutor.
Phase 1 (geocoding record creation) is now handled by address deduplication.
Phase 2 (API calls) uses ThreadPoolExecutor with worker count from constants.
"""

import logging
import time
import queue
import threading
from typing import List, Optional, Dict, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import tqdm
except ImportError:
    tqdm = None

try:
    import censusgeocode as cg
except ImportError:
    cg = None

from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models.geocoding import Geocoding
from constants import GEOCODING_API_WORKERS
from logging_utils import log_info, log_error, log_debug, log_warning, get_logger, update_progress, start_progress_reporting
from config import global_config


# Set up logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GeocodingProcessor:
    """
    Producer-consumer geocoding processor for Phase 2 API calls.

    Uses multiple API worker threads and a single database writer thread with queues for communication.
    """

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops
        self.logger = get_logger("geocoding_processor")

        # Queue for results to write
        self.result_queue = queue.Queue(maxsize=1000)  # Results to write

        # Queue for progress updates
        self.progress_queue = queue.Queue(maxsize=1000)  # Progress updates

        # Threading components
        self.producer_thread = None
        self.writer_thread = None
        self.progress_thread = None

        # Control flags
        self.stop_event = threading.Event()


class GeolocationProcessorThreaded(GeocodingProcessor):
    """
    Threaded geolocation processor that combines Phase 1 (record creation) and Phase 2 (API calls).

    This class inherits from GeocodingProcessor to provide the same interface as the old GeolocationProcessorThreaded
    but uses the new producer-consumer architecture internally.
    """

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.logger = get_logger("geolocation_processor_threaded")

    def geolocate_addresses_threaded(self, progress_bar=None) -> int:
        """
        Run both Phase 1 (record creation) and Phase 2 (API calls) in parallel.

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of geocoding records processed
        """
        # For now, just run Phase 2 (API calls) since Phase 1 is handled by address deduplication
        return self.process_geocoding_records(progress_bar)

    def _get_total_pending_records(self) -> int:
        """
        Get the total number of pending geocoding records for progress bar initialization.

        Returns:
            Total number of pending geocoding records
        """
        query = """
            SELECT COUNT(*) FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status = 'pending'
        """
        result = self.db_ops.execute_query(query)
        if result:
            total_pending = result.fetchone()[0]
            # Apply max_files limit if set
            if global_config.max_files:
                total_pending = min(total_pending, global_config.max_files)
            return total_pending
        return 0

    def process_geocoding_records(self, progress_bar=None) -> int:
        """
        Process all pending geocoding records using ThreadPoolExecutor for API calls.

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of geocoding records processed
        """
        if cg is None:
            if not global_config.is_quiet():
                log_warning(self.logger, "censusgeocode library not available. Skipping geocoding API calls. "
                                "To enable geocoding, install with: pip install censusgeocode")
            return 0

        # Create progress bar if none provided and tqdm is available
        created_progress_bar = False
        if progress_bar is None and tqdm is not None and not global_config.is_quiet():
            total_records = self._get_total_pending_records()
            progress_bar = tqdm.tqdm(
                total=total_records,
                desc="Geocoding records",
                unit="records",
                bar_format='{desc}: {n}/{total} |{bar}| {percentage:3.0f}% | {elapsed} | {rate_fmt}'
            )
            created_progress_bar = True

        # Start progress reporting using the base processor pattern
        total_records = self._get_total_pending_records()
        start_progress_reporting(total=total_records, desc="Geocoding records", unit="records")

        if not global_config.is_quiet():
            log_info(self.logger, f"Starting geocoding API processing with {GEOCODING_API_WORKERS} API workers and 1 database writer")

        # Log initial thread count
        initial_threads = threading.active_count()
        log_debug(self.logger, f"Initial active threads: {initial_threads}")

        total_processed = 0

        try:
            # Create ThreadPoolExecutor for API calls
            with ThreadPoolExecutor(max_workers=GEOCODING_API_WORKERS) as executor:
                log_info(self.logger, f"ThreadPoolExecutor created with {GEOCODING_API_WORKERS} max workers for API calls")
                log_debug(self.logger, f"ThreadPoolExecutor created with {GEOCODING_API_WORKERS} max workers")

                # Start producer thread
                self.producer_thread = threading.Thread(target=self._producer_worker, args=(executor,), daemon=True, name="GeocodingProducer")
                self.producer_thread.start()
                log_info(self.logger, f"Producer thread started: {self.producer_thread.name} (daemon={self.producer_thread.daemon})")
                log_debug(self.logger, f"Producer thread started: {self.producer_thread.name} (daemon={self.producer_thread.daemon})")

                # Start database writer thread
                self.writer_thread = threading.Thread(target=self._database_writer, args=(progress_bar, created_progress_bar), daemon=True, name="GeocodingWriter")
                self.writer_thread.start()
                log_info(self.logger, f"Writer thread started: {self.writer_thread.name} (daemon={self.writer_thread.daemon})")
                log_debug(self.logger, f"Writer thread started: {self.writer_thread.name} (daemon={self.writer_thread.daemon})")

                # Start progress consumer thread
                self.progress_thread = threading.Thread(target=self._progress_consumer, daemon=True, name="GeocodingProgress")
                self.progress_thread.start()
                log_info(self.logger, f"Progress thread started: {self.progress_thread.name} (daemon={self.progress_thread.daemon})")
                log_debug(self.logger, f"Progress thread started: {self.progress_thread.name} (daemon={self.progress_thread.daemon})")

                # Log thread counts after starting threads
                after_start_threads = threading.active_count()
                log_debug(self.logger, f"Active threads after starting producer/writer/progress: {after_start_threads}")

                # Enumerate all threads for debugging
                all_threads = threading.enumerate()
                thread_names = [t.name for t in all_threads]
                log_debug(self.logger, f"All active threads: {thread_names}")

                # Wait for producer to finish
                log_debug(self.logger, "Waiting for producer thread to finish...")
                self.producer_thread.join()
                log_debug(self.logger, "Producer thread finished")

                # Shutdown executor and wait for all tasks to complete
                log_debug(self.logger, "Shutting down ThreadPoolExecutor...")
                executor.shutdown(wait=True)
                log_debug(self.logger, "ThreadPoolExecutor shutdown complete")

                # Signal writer and progress threads to stop
                self.stop_event.set()
                log_debug(self.logger, "Stop event set for writer and progress threads")

                # Wait for writer to finish
                log_debug(self.logger, "Waiting for writer thread to finish...")
                self.writer_thread.join()
                log_debug(self.logger, "Writer thread finished")

                # Wait for progress thread to finish
                log_debug(self.logger, "Waiting for progress thread to finish...")
                self.progress_thread.join()
                log_debug(self.logger, "Progress thread finished")

                # Get total processed from result queue (sentinel value)
                while not self.result_queue.empty():
                    item = self.result_queue.get_nowait()
                    if isinstance(item, dict) and 'total_processed' in item:
                        total_processed = item['total_processed']
                        break

        except Exception as e:
            log_error(self.logger, f"Geocoding processing failed: {e}", exc_info=True)
        finally:
            # Cleanup
            self._cleanup()

            # Close progress bar if we created it
            if created_progress_bar and progress_bar:
                progress_bar.close()

        if not global_config.is_quiet():
            log_info(self.logger, f"Geocoding API processing completed: {total_processed} records processed")

        return total_processed

    def _producer_worker(self, executor):
        """Producer thread: fetches geocoding records and submits them to ThreadPoolExecutor."""
        log_debug(self.logger, f"Producer thread {threading.current_thread().name} started")
        last_geocoding_id = None
        total_submitted = 0

        while not self.stop_event.is_set():
            try:
                # Get next batch of geocoding records
                batch = self._get_pending_geocoding_records(last_geocoding_id)
                if not batch:
                    log_debug(self.logger, "Producer: No more records to process")
                    break  # No more records

                log_debug(self.logger, f"Producer: Retrieved batch of {len(batch)} records")

                # Submit each record to the executor
                for record in batch:
                    if not self.stop_event.is_set():
                        try:
                            executor.submit(self._process_single_geocoding_record, record)
                            total_submitted += 1
                            log_debug(self.logger, f"Submitted geocoding record {record['geocoding_id']} to executor (total submitted: {total_submitted})")
                        except RuntimeError:
                            # Executor is shutting down, stop submitting
                            log_debug(self.logger, "Producer: Executor shutting down, stopping submission")
                            break

                # Update last_geocoding_id for pagination
                if batch:
                    last_geocoding_id = max(record['geocoding_id'] for record in batch)

            except Exception as e:
                log_error(self.logger, f"Producer error: {e}", exc_info=True)
                break

        log_debug(self.logger, f"Producer thread {threading.current_thread().name} finished. Total records submitted: {total_submitted}")

    def _get_pending_geocoding_records(self, last_geocoding_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get a batch of geocoding records that need API calls.

        Args:
            last_geocoding_id: Last geocoding_id from previous batch for pagination

        Returns:
            List of geocoding record dictionaries
        """
        batch_size = 100  # Smaller batch size for queue processing

        where_clause = "geocoding_status IS NULL OR geocoding_status = 'pending'"
        if last_geocoding_id is not None:
            where_clause += " AND geocoding_id > ?"
            params = (last_geocoding_id,)
        else:
            params = None

        effective_batch_size = min(global_config.max_files, batch_size) if global_config.max_files else batch_size
        query = f"""
            SELECT geocoding_id, normalized_address, attempt_count
            FROM Geocoding
            WHERE {where_clause}
            ORDER BY geocoding_id
            LIMIT ?
        """
        if params:
            params = params + (effective_batch_size,)
        else:
            params = (effective_batch_size,)

        result = self.db_ops.execute_query(query, params)
        if result:
            rows = result.fetchall()
            batch_data = [
                {
                    'geocoding_id': row[0],
                    'normalized_address': row[1],
                    'attempt_count': row[2] or 0
                }
                for row in rows
            ]

            if not global_config.is_quiet():
                log_debug(self.logger, f"Retrieved {len(batch_data)} geocoding records from database (last_geocoding_id={last_geocoding_id})")

            return batch_data
        return []


    def _process_single_geocoding_record(self, record: Dict[str, Any]) -> None:
        """
        Process a single geocoding record with API call and put result in queue.

        Args:
            record: Geocoding record dictionary
        """
        try:
            # Make single API call
            api_result = self._single_geocode_address(record)
        except Exception as e:
            api_result = {'status': 'failed', 'error': str(e)}

        # Prepare result for database update
        update_dict = {
            'geocoding_id': record['geocoding_id'],
            'last_attempt': datetime.now().isoformat(),
            'attempt_count': record.get('attempt_count', 0) + 1,
            'latitude': None,
            'longitude': None,
            'geocoding_status': None
        }

        # Map api_result fields
        if 'latitude' in api_result:
            update_dict['latitude'] = api_result['latitude']
        if 'longitude' in api_result:
            update_dict['longitude'] = api_result['longitude']
        if 'geocoding_status' in api_result:
            update_dict['geocoding_status'] = api_result['geocoding_status']

        # Put result in result queue
        self.result_queue.put(update_dict)

        # For successful geocoding, also update Address table and propagate colocator
        if api_result.get('status') == 'success' and update_dict['latitude'] is not None and update_dict['longitude'] is not None:
            # Create colocator string for successful geocoding
            colocator = f"LL:{update_dict['latitude']}:{update_dict['longitude']}"

            # Find the address_id that corresponds to this geocoding_id
            address_query = """
                SELECT address_id FROM Addresses WHERE geocoding_id = ?
            """
            result = self.db_ops.execute_query(address_query, (record['geocoding_id'],))
            if result:
                row = result.fetchone()
                if row and row[0]:
                    address_id = row[0]
                    # Update the Address table with geocoding_id and colocator, and propagate colocator to owner
                    self.db_ops.update_address_geocoding(address_id, record['geocoding_id'], colocator)
                    if not global_config.is_quiet():
                        log_debug(self.logger, f"PHASE 2: Updated Address {address_id} with geocoding_id {record['geocoding_id']} and colocator {colocator}")

    def _single_geocode_address(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make a single geocoding API call for one address.

        Args:
            record: Geocoding record dictionary

        Returns:
            API result dictionary
        """
        if not cg:
            return {'status': 'failed', 'error': 'censusgeocode not available'}

        try:
            census_json = record['normalized_address']

            if isinstance(census_json, dict):
                api_record = census_json.copy()
            else:
                try:
                    import json
                    api_record = json.loads(census_json)
                except Exception as json_e:
                    if not global_config.is_quiet():
                        log_error(self.logger, f"JSON parsing failed for geocoding_id {record['geocoding_id']}: {json_e}")
                    api_record = {
                        'street': '',
                        'city': '',
                        'state': '',
                        'zip': ''
                    }

            # Add diagnostic logging
            if not global_config.is_quiet():
                log_info(self.logger, f"Starting API call for geocoding_id {record['geocoding_id']} with address: {api_record}")
                log_debug(self.logger, f"Starting API call for geocoding_id {record['geocoding_id']} with address: {api_record}")

            # Make single API call (using address() instead of addressbatch()) with timeout
            # Increased timeout from 30s to 300s for better reliability with large datasets
            api_result = cg.address(**api_record, timeout=300)

            if not global_config.is_quiet():
                log_info(self.logger, f"API call completed for geocoding_id {record['geocoding_id']}")
                log_debug(self.logger, f"API call completed for geocoding_id {record['geocoding_id']}")

            if api_result and len(api_result) > 0:
                result = api_result[0]  # Take first result
                lat = result.get('coordinates', {}).get('y') if result.get('coordinates') else result.get('lat')
                lon = result.get('coordinates', {}).get('x') if result.get('coordinates') else result.get('lon')
                match = result.get('matched', False)

                if match and lat is not None and lon is not None and str(lat).strip() and str(lon).strip():
                    geocoding_status = 'Match'
                    matchtype = result.get('matchtype')
                    if matchtype and matchtype != 'Exact':
                        geocoding_status = f'Match:{matchtype}'

                    return {
                        'status': 'success',
                        'latitude': round(float(lat), 4),
                        'longitude': round(float(lon), 4),
                        'geocoding_status': geocoding_status
                    }
                else:
                    return {'status': 'failed', 'geocoding_status': 'No_Match'}
            else:
                return {'status': 'failed', 'error': 'no result from API'}

        except Exception as e:
            return {'status': 'failed', 'error': str(e)}

    def _database_writer(self, progress_bar=None, created_progress_bar=False):
        """Database writer thread: batches results and writes to database."""
        log_debug(self.logger, f"Database writer thread {threading.current_thread().name} started")
        batch_size = 100
        batch = []
        total_processed = 0

        while not self.stop_event.is_set() or not self.result_queue.empty():
            try:
                # Get result from queue
                result = self.result_queue.get(timeout=1)
                batch.append(result)
                self.result_queue.task_done()

                # Process batch when full or if stopping
                if len(batch) >= batch_size or (self.stop_event.is_set() and not self.result_queue.empty()):
                    if batch:
                        updated = self.db_ops.bulk_update('Geocoding', batch, 'geocoding_id')
                        total_processed += len(batch)

                        # Queue progress update instead of updating progress bar directly
                        progress_op = DatabaseOperation(
                            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                            data={'count': len(batch)}
                        )
                        self.progress_queue.put(progress_op)

                        if not global_config.is_quiet():
                            log_info(self.logger, f"Executed {len(batch)} updates, updated {updated} records")
                            log_debug(self.logger, f"Executed {len(batch)} updates, updated {updated} records")

                        batch = []

            except queue.Empty:
                # Process remaining batch if stopping
                if self.stop_event.is_set() and batch:
                    if batch:
                        updated = self.db_ops.bulk_update('Geocoding', batch, 'geocoding_id')
                        total_processed += len(batch)

                        # Queue final progress update
                        progress_op = DatabaseOperation(
                            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                            data={'count': len(batch)}
                        )
                        self.progress_queue.put(progress_op)

                        if not global_config.is_quiet():
                            log_info(self.logger, f"Final batch: executed {len(batch)} updates, updated {updated} records")
                            log_debug(self.logger, f"Final batch: executed {len(batch)} updates, updated {updated} records")

                        batch = []
                continue
            except Exception as e:
                log_error(self.logger, f"Database writer error: {e}", exc_info=True)

        # Send total processed count via result queue as sentinel
        self.result_queue.put({'total_processed': total_processed})
        log_debug(self.logger, f"Database writer thread {threading.current_thread().name} finished. Total records processed: {total_processed}")

    def _progress_consumer(self):
        """Progress consumer thread: processes PROGRESS_UPDATE operations."""
        log_debug(self.logger, f"Progress consumer thread {threading.current_thread().name} started")

        while not self.stop_event.is_set() or not self.progress_queue.empty():
            try:
                # Get progress operation from queue
                operation = self.progress_queue.get(timeout=1)

                # Process PROGRESS_UPDATE operation
                if operation.operation_type == DatabaseOperationType.PROGRESS_UPDATE:
                    progress_count = operation.data.get("count", 0)
                    update_progress(n=progress_count)

                self.progress_queue.task_done()

            except queue.Empty:
                # Check if we should stop
                if self.stop_event.is_set():
                    break
                continue
            except Exception as e:
                log_error(self.logger, f"Progress consumer error: {e}", exc_info=True)

        log_debug(self.logger, f"Progress consumer thread {threading.current_thread().name} finished")

    def _cleanup(self):
        """Clean up threads and queues."""
        self.stop_event.set()

        # Clear result queue
        while not self.result_queue.empty():
            try:
                self.result_queue.get_nowait()
                self.result_queue.task_done()
            except queue.Empty:
                break

        # Clear progress queue
        while not self.progress_queue.empty():
            try:
                self.progress_queue.get_nowait()
                self.progress_queue.task_done()
            except queue.Empty:
                break



def geolocate_addresses(db_ops: DatabaseOperations, progress_bar=None) -> int:
    """
    Geolocate addresses using the simplified approach.

    Phase 1 (geocoding record creation) is now integrated into address deduplication.
    Phase 2 (API calls) uses a simple GeocodingProcessor with ThreadPoolExecutor.

    This function now only runs Phase 2 for backward compatibility.

    Args:
        db_ops: Database operations instance
        progress_bar: Optional progress bar to update

    Returns:
        Number of geocoding records processed
    """
    from geocoding_api_processor import GeocodingAPIProcessor

    logger = get_logger("geolocation")

    # Check if censusgeocode is available
    if cg is None:
        if not global_config.is_quiet():
            log_warning(logger, "censusgeocode library not available. Skipping geocoding. "
                              "To enable geocoding, install with: pip install censusgeocode")
        return 0

    try:
        if not global_config.is_quiet():
            log_info(logger, "Starting geocoding API processing (Phase 2) - geocoding records should be created during address deduplication (Phase 1)")

        # Create and run the API processor for Phase 2
        api_processor = GeocodingAPIProcessor(db_ops)
        return api_processor.process_pending_geocoding_records(progress_bar)

    except Exception as e:
        log_error(logger, f"Address geocoding failed: {e}", exc_info=True)
        return 0
