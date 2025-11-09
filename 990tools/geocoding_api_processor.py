#!/usr/bin/env python3
"""
geocoding_api_processor.py - Geocoding API processor using producer-consumer pattern

This module handles the API calls for geocoding records created in Phase 1.
It processes geocoding records that are pending API calls, makes census API
calls, and updates the records with the results. Uses producer-consumer pattern
for safe batch processing with DuckDB.
"""

import time
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime

try:
    import censusgeocode as cg
except ImportError:
    cg = None

try:
    import tqdm
except ImportError:
    tqdm = None

from base_processor import BaseProcessor, BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models.geocoding import Geocoding
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config
from constants import GEOCODING_API_BATCH_SIZE
from queue_status_display import QueueStatusDisplay


class GeocodingAPIProducer(BaseProducer):
    """Producer for geocoding API operations"""

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_API_BATCH_SIZE):
        super().__init__(db_ops, batch_size=batch_size)

        # API performance tracking for dynamic batch sizing
        self._api_performance_history = []
        self._performance_window = 10  # Track last 10 API call batches
        self._slow_threshold = 2.0  # Consider slow if average > 2 seconds per batch
        self._fast_threshold = 0.5  # Consider fast if average < 0.5 seconds per batch

        # Batch size adjustment factors
        self._batch_factors = {
            'fast': 1.2,     # Increase batch size when API is fast
            'normal': 1.0,   # Normal batch size
            'slow': 0.6,     # Reduce batch size when API is slow
            'very_slow': 0.3 # Significantly reduce batch size when API is very slow
        }

    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get a batch of geocoding records needing API calls using key-value paging on UUID7 str pks"""
        current_batch_size = self._get_dynamic_batch_size()

        where_clause = "geocoding_status IS NULL OR geocoding_status = 'pending'"
        params = None
        if last_pk is not None:
            where_clause += " AND geocoding_id > ?"
            params = (last_pk,)
 
        effective_batch_size = min(global_config.max_files, current_batch_size) if global_config.max_files else current_batch_size
        query = f"""
            SELECT geocoding_id, normalized_address, attempt_count
            FROM Geocoding
            WHERE {where_clause}
            ORDER BY geocoding_id
            LIMIT ?
        """
        if params is None:
            params = (effective_batch_size,)
        else:
            params = params + (effective_batch_size,)
 
        result = self.db_ops.execute_query(query, params)
        if result:
            rows = result.fetchall()
            batch_data = [
                {
                    'geocoding_id': row[0],
                    'normalized_address': row[1],  # This is the prebuilt JSON dict
                    'attempt_count': row[2] or 0
                }
                for row in rows
            ]
 
            max_pk = max(row[0] for row in rows) if rows else None
 
            if not global_config.is_quiet():
                log_debug(f"PHASE 2: Retrieved {len(batch_data)} geocoding records from database (last_pk={last_pk})")
                log_debug(f"PHASE 2: Dynamic batch size was {effective_batch_size} (base: {self.batch_size}, max_files: {global_config.max_files})")
 
            return batch_data, max_pk
        return [], None

    def _process_work_batch_to_context(self, batch: List[Dict[str, Any]]) -> Optional['PendingDatabaseContext']:
        """Process a batch of geocoding records into PendingDatabaseContext object"""
        from pending_database_context import PendingDatabaseContext

        if not batch:
            return None

        # Create context for geocoding batch
        context = PendingDatabaseContext()

        # Create operation to process geocoding batch
        operation = DatabaseOperation(
            operation_type=DatabaseOperationType.UPDATE_GEOCODING,
            data={
                "batch": batch,
                "operation": "process_geocoding_api_batch"
            }
        )
        context.addOperationToDatabase(operation)

        return context

    def _get_dynamic_batch_size(self) -> int:
        """Calculate dynamic batch size based on API performance"""
        performance_level = self._get_api_performance_level()
        factor = self._batch_factors[performance_level]

        dynamic_size = int(self.batch_size * factor)
        # Cap at 10,000 to comply with census API batch limit
        dynamic_size = max(5, min(10000, dynamic_size))

        if not global_config.is_quiet():
            avg_time = 0.0
            if self._api_performance_history:
                recent = self._api_performance_history[-3:]
                avg_time = sum(b['avg_time_per_record'] for b in recent) / len(recent)
            log_debug(f"PHASE 2: Dynamic batch sizing - performance: {performance_level}, avg_time_per_record: {avg_time:.2f}s, factor: {factor}, batch_size: {self.batch_size} -> {dynamic_size}")

        return dynamic_size

    def _get_api_performance_level(self) -> str:
        """Determine API performance level based on recent history"""
        if not self._api_performance_history:
            return 'normal'

        # Calculate average time per record over recent batches
        recent_batches = self._api_performance_history[-5:]  # Last 5 batches
        if not recent_batches:
            return 'normal'

        avg_time_per_record = sum(b['avg_time_per_record'] for b in recent_batches) / len(recent_batches)

        if avg_time_per_record < self._fast_threshold:
            return 'fast'
        elif avg_time_per_record > self._slow_threshold:
            return 'very_slow'
        elif avg_time_per_record > (self._slow_threshold * 0.7):  # 70% of slow threshold
            return 'slow'
        else:
            return 'normal'

    def _update_api_performance(self, batch_size: int, duration: float):
        """Update API performance tracking"""
        self._api_performance_history.append({
            'batch_size': batch_size,
            'duration': duration,
            'avg_time_per_record': duration / batch_size
        })

        # Keep only recent history
        if len(self._api_performance_history) > self._performance_window:
            self._api_performance_history = self._api_performance_history[-self._performance_window:]


class GeocodingAPIConsumer(BaseConsumer):
    """Consumer for geocoding API operations"""

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)

    def _process_operations_batch(self, operations_by_type):
        """Process geocoding API operations"""
        # Handle geocoding operations
        if DatabaseOperationType.UPDATE_GEOCODING.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.UPDATE_GEOCODING.value]:
                self._execute_geocoding_operation(operation)


class GeocodingAPIProcessor(BaseProcessor):
    """
    Processor for geocoding API calls using producer-consumer pattern.

    This processor reads existing geocoding records and makes census API calls
    to update them with latitude/longitude coordinates. Uses producer-consumer
    pattern for safe batch processing with DuckDB.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_API_BATCH_SIZE):
        super().__init__(db_ops)
        self.db_ops = db_ops
        self.batch_size = batch_size

        # Initialize producer and consumer
        self.producer = GeocodingAPIProducer(db_ops, batch_size)
        self.consumer = GeocodingAPIConsumer(db_ops)

        # Initialize thread pool manager
        thread_config = ThreadPoolConfig(
            producer_config=PoolConfig(max_workers=4, queue_size=1000),  # Multiple producers for parallel processing
            consumer_config=PoolConfig(max_workers=1, queue_size=1000)   # Single consumer for DB safety
        )
        self.thread_pool_manager = ThreadPoolManager(thread_config,self)
        self.setup_status_gauges(interval=10.0, queues=[self.thread_pool_manager.result_queue])

        # Initialize QueueStatusDisplay for visual monitoring
        self.queue_status_display = QueueStatusDisplay(self.thread_pool_manager.result_queue, update_interval=30.0)

    def _get_custom_metrics(self) -> Dict[str, Any]:
        try:
            pending_result = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status IS NULL OR geocoding_status = 'pending'"
            )
            pending = pending_result.fetchone()[0] if pending_result else 0

            success_result = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status LIKE 'Match%'"
            )
            success = success_result.fetchone()[0] if success_result else 0

            return {
                'outstanding_geocode_requests': pending,
                'geocoded_addresses': success,
                **super()._get_custom_metrics()
            }
        except Exception as e:
            log_error(f"Error getting custom metrics: {e}")
            return super()._get_custom_metrics()


    def process_pending_geocoding_records(self, progress_bar=None) -> int:
        """
        Process all pending geocoding records by making API calls using producer-consumer pattern.

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of geocoding records processed
        """
        if cg is None:
            if not global_config.is_quiet():
                log_warning("censusgeocode library not available. Skipping geocoding API calls. "
                                "To enable geocoding, install with: pip install censusgeocode")
            return 0

        if not global_config.is_quiet():
            log_info(f"Starting geocoding API processing")

        # Start QueueStatusDisplay for visual monitoring
        self.queue_status_display.start()

        try:
            # Collect all batches using paging
            batches = []
            last_pk = None
            while not self.exit_processing:
                batch, last_pk = self.producer._get_work_batch(last_pk)
                if not batch:
                    break
                batches.append(batch)

                total_items = sum(len(b) for b in batches)
                if global_config.max_files and total_items >= global_config.max_files:
                    excess = total_items - global_config.max_files
                    if excess > 0:
                        batches[-1] = batches[-1][:-excess]
                    break

            if not batches:
                if not global_config.is_quiet():
                    log_info("No geocoding records to process")
                return 0

            total_items = sum(len(b) for b in batches)

            if progress_bar is None and tqdm is not None:
                progress_bar = tqdm.tqdm(total=total_items, desc="Geocoding API calls", unit="addr")

            # Use thread pool for parallel processing
            num_producers = self.thread_pool_manager.config.producer_config.max_workers
            self.thread_pool_manager.start_producer_pool(
                batches, self._producer_wrapper, progress_bar=progress_bar
            )
            self.thread_pool_manager.start_consumer_pool(
                self._consumer_wrapper, num_producers=num_producers, progress_bar=progress_bar
            )

            self.thread_pool_manager.wait_for_completion()

            if progress_bar is not None:
                progress_bar.close()

            if not global_config.is_quiet():
                log_info(f"Geocoding API processing completed: {total_items} records processed")

            # Stop QueueStatusDisplay
            self.queue_status_display.stop()

            return total_items

        except Exception as e:
            log_error(f"Geocoding API processing failed: {e}", exc_info=True)
            # Stop QueueStatusDisplay on error
            self.queue_status_display.stop()
            return 0

    def _producer_wrapper(self, batches: List[List[Dict[str, Any]]], work_queue, result_queue, thread_id: int, num_threads: int):
        """Wrapper for producer thread execution"""
        try:
            log_debug(f"Geocoding Producer thread {thread_id} starting")

            # Distribute batches among threads
            for i in range(thread_id, len(batches), num_threads):
                if self.exit_processing:
                    break
                batch = batches[i]

                # Create operation to process geocoding batch
                operation = DatabaseOperation(
                    operation_type=DatabaseOperationType.UPDATE_GEOCODING,
                    data={
                        "batch": batch,
                        "operation": "process_geocoding_api_batch"
                    }
                )

                # Put operation in result queue for consumer
                result_queue.put(operation)

                log_debug(f"Geocoding Producer {thread_id}: queued operation for batch of {len(batch)} records")

        except Exception as e:
            log_error(f"Geocoding Producer thread {thread_id} error: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)

    def _consumer_wrapper(self, result_queue, thread_id: int, num_producers: int, progress_bar=None):
        """Wrapper for consumer thread execution"""
        try:
            log_debug(f"Geocoding Consumer thread {thread_id} starting")
            sentinels_received = 0

            while True:
                try:
                    operation = result_queue.get(timeout=1.0)
                    if self.exit_processing:
                        break
                    if operation is None:  # Sentinel
                        sentinels_received += 1
                        if sentinels_received >= num_producers:
                            break
                        continue

                    # Execute the operation
                    if isinstance(operation, DatabaseOperation):
                        self.consumer._execute_geocoding_operation(operation)

                        # Update progress for the batch
                        if progress_bar and operation.data.get("batch"):
                            batch_size = len(operation.data["batch"])
                            progress_bar.update(batch_size)

                    result_queue.task_done()

                except queue.Empty:
                    if self.exit_processing:
                        break
                    continue
                except Exception as inner_e:
                    log_error(f"Error in consumer loop: {inner_e}")
                    continue

        except Exception as e:
            log_error(f"Geocoding Consumer thread {thread_id} error: {e}", exc_info=True)

    def _execute_geocoding_operation(self, operation):
        """Execute a geocoding operation"""
        data = operation.data
        batch = data.get("batch", [])
        operation_type = data.get("operation")

        if operation_type == "process_geocoding_api_batch":
            # Process the geocoding batch
            self._process_geocoding_batch(batch)

    def _process_geocoding_batch(self, batch: List[Dict[str, Any]]) -> int:
        """
        Process a batch of geocoding records with API calls, using PDC for DB writes.
 
        Args:
            batch: List of geocoding record dictionaries
 
        Returns:
            Number of records processed
        """
        if not batch:
            return 0
 
        if not global_config.is_quiet():
            log_debug(f"PHASE 2: Processing batch of {len(batch)} geocoding records")
 
        # Process the batch sequentially using batch API calls
        try:
            batch_results = self._batch_geocode_addresses(batch)
        except Exception as e:
            # If batch call fails, return failed results for all records in batch
            batch_results = [{'geocoding_record': record, 'api_result': {'status': 'failed', 'error': str(e)},
                            'last_attempt': datetime.now().isoformat(), 'attempt_count': record.get('attempt_count', 0) + 1}
                            for record in batch]
 
        # Process results into database updates
        updates = []
        address_updates = []  # Track successful geocoding for Address table updates
        for result in batch_results:
            api_result = result['api_result']
            update_dict = {
                'geocoding_id': result['geocoding_record']['geocoding_id'],
                'last_attempt': result['last_attempt'],
                'attempt_count': result['attempt_count'],
                'latitude': None,  # Initialize all possible columns to None
                'longitude': None,
                'geocoding_status': None
            }
 
            # Map api_result fields to actual database columns
            if 'latitude' in api_result:
                update_dict['latitude'] = api_result['latitude']
            if 'longitude' in api_result:
                update_dict['longitude'] = api_result['longitude']
            if 'geocoding_status' in api_result:
                update_dict['geocoding_status'] = api_result['geocoding_status']
 
            # Add diagnostic logging
            if not global_config.is_quiet():
                log_debug(f"PHASE 2: Update dict for geocoding_id {update_dict['geocoding_id']}: {update_dict}")
 
            updates.append(update_dict)
 
            # Track successful geocoding results for Address table updates
            if api_result.get('status') == 'success' and update_dict['latitude'] is not None and update_dict['longitude'] is not None:
                # Create colocator string for successful geocoding
                colocator = f"LL:{update_dict['latitude']}:{update_dict['longitude']}"
                address_updates.append({
                    'geocoding_id': update_dict['geocoding_id'],
                    'colocator': colocator
                })
 
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()
 
        # Add updates to Geocoding table as UPDATE operations
        for update_dict in updates:
            operation = DatabaseOperation(
                operation_type=DatabaseOperationType.UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': update_dict,
                    'key_field': 'geocoding_id',
                    'key_value': update_dict['geocoding_id']
                }
            )
            context.addOperationToDatabase(operation)
 
        # For address updates, since update_address_geocoding is custom, create UPDATE for Address
        # Assuming update_address_geocoding does UPDATE Address SET geocoding_id=?, colocator=? WHERE address_id=?
        # But since we have address_id from query, we need to include it.
        # To preserve logic, we'll query here and add UPDATE operations.
        for addr_update in address_updates:
            geocoding_id = addr_update['geocoding_id']
            colocator = addr_update['colocator']
 
            # Find the address_id
            address_query = """
                SELECT address_id FROM Addresses WHERE geocoding_id = ?
            """
            result = self.db_ops.execute_query(address_query, (geocoding_id,))
            if result:
                row = result.fetchone()
                if row and row[0]:
                    address_id = row[0]
                    # Create UPDATE for Address
                    addr_update_dict = {
                        'address_id': address_id,
                        'geocoding_id': geocoding_id,
                        'colocator': colocator
                    }
                    operation = DatabaseOperation(
                        operation_type=DatabaseOperationType.UPDATE,
                        data={
                            'table': 'Addresses',
                            'updates': {'geocoding_id': geocoding_id, 'colocator': colocator},
                            'key_field': 'address_id',
                            'key_value': address_id
                        }
                    )
                    context.addOperationToDatabase(operation)
 
                    # Note: Propagation to owner would need additional logic; assuming it's handled in db_ops or separate
                    if not global_config.is_quiet():
                        log_debug(f"PHASE 2: Prepared Address update for {address_id} with geocoding_id {geocoding_id} and colocator {colocator}")
 
        # Update PDC size gauge before save
        self.update_pdc_size_gauge(context)
 
        # Execute via PDC
        ids = context.save_to_database(self.db_ops)
 
        if not global_config.is_quiet():
            log_debug(f"PHASE 2: Executed PDC with {len(updates)} Geocoding updates and {len(address_updates)} Address updates")
 
        return len(batch) if ids else 0

    def _batch_geocode_addresses(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Make a batch geocoding API call for multiple addresses, with exit_processing check.
 
        Args:
            batch: List of geocoding record dictionaries
 
        Returns:
            List of result dictionaries
        """
        if self.exit_processing:
            return []
 
        start_time = time.time()
 
        if not cg:
            duration = time.time() - start_time
            self.producer._update_api_performance(len(batch), duration)
            return [{'geocoding_record': record, 'api_result': {'status': 'failed', 'error': 'censusgeocode not available'},
                      'last_attempt': datetime.now().isoformat(), 'attempt_count': record.get('attempt_count', 0) + 1}
                    for record in batch]
 
        try:
            # Prepare batch addresses for API call
            batch_addresses = []
            record_map = {}  # Map geocoding_id to original record
 
            for geocoding_record in batch:
                if self.exit_processing:
                    break
                # Use the prebuilt JSON directly from normalized_address field
                census_json = geocoding_record['normalized_address']
                if not global_config.is_quiet():
                    log_debug(f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - census_json type: {type(census_json)}, value: {census_json}")
 
                if isinstance(census_json, dict):
                    # Set the ID to geocoding_id for easy lookup when results come back
                    api_record = census_json.copy()
                    api_record['id'] = str(geocoding_record['geocoding_id'])  # Ensure string ID
                    if not global_config.is_quiet():
                        log_debug(f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - dict path: api_record after copy: {api_record}")
                else:
                    # Parse as JSON (all records should now be proper JSON)
                    try:
                        import json
                        api_record = json.loads(census_json)
                        api_record['id'] = str(geocoding_record['geocoding_id'])  # Ensure string ID
                        if not global_config.is_quiet():
                            log_debug(f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - JSON parse successful: api_record: {api_record}")
                    except Exception as json_e:
                        if not global_config.is_quiet():
                            log_error(f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - JSON parse failed: {json_e}, creating minimal record")
                        # If JSON parsing fails, create a minimal record
                        api_record = {
                            'id': str(geocoding_record['geocoding_id']),  # Ensure string ID
                            'street': '',
                            'city': '',
                            'state': '',
                            'zip': ''
                        }
 
                batch_addresses.append(api_record)
                record_map[geocoding_record['geocoding_id']] = geocoding_record
                if not global_config.is_quiet():
                    log_debug(f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} (type: {type(geocoding_record['geocoding_id'])}) - added to batch_addresses: {api_record}")
                    log_debug(f"DEBUG: record_map keys after addition: {[(k, type(k)) for k in list(record_map.keys())[-3:]]}")
 
            if self.exit_processing:
                return []
 
            # Make batch API call
            if not global_config.is_quiet():
                log_debug(f"Making batch API call with {len(batch_addresses)} addresses")
                # Log the actual address data being sent to API
                for addr in batch_addresses:
                    log_debug(f"API call address: id={addr.get('id')}, street='{addr.get('street')}', city='{addr.get('city')}', state='{addr.get('state')}', zip='{addr.get('zip')}'")
            api_results = cg.addressbatch(batch_addresses)
            if not global_config.is_quiet():
                log_debug(f"DEBUG: API call completed, api_results: {api_results}")
 
            # Track performance
            duration = time.time() - start_time
            self.producer._update_api_performance(len(batch), duration)
 
            # Process results
            results = []
            if api_results and len(api_results) > 0:
                if not global_config.is_quiet():
                    log_debug(f"Census API call params {api_results}")
 
                for result in api_results:
                    if self.exit_processing:
                        break
                    geocoding_id = result.get('id')
 
                    # Try multiple lookup strategies to handle type mismatches
                    geocoding_record = record_map.get(geocoding_id)
                    if not geocoding_record:
                        # Try with str() conversion
                        geocoding_record = record_map.get(str(geocoding_id))
                    if not geocoding_record:
                        # Try with UUID conversion if it's a string
                        try:
                            from uuid import UUID
                            if isinstance(geocoding_id, str):
                                geocoding_record = record_map.get(UUID(geocoding_id))
                        except (ValueError, TypeError):
                            pass
 
                    if not geocoding_record:
                        if not global_config.is_quiet():
                            log_error(f"PHASE 2: CRITICAL ERROR - No geocoding_record found for geocoding_id {geocoding_id} (type: {type(geocoding_id)})")
                            log_error(f"PHASE 2: Available geocoding_ids in record_map: {[(k, type(k)) for k in list(record_map.keys())[:5]]}")
                            log_error(f"PHASE 2: API result being processed: {result}")
                        continue
 
                    lat = result.get('lat')
                    lon = result.get('lon')
                    match = result.get('match', False)
                    matchtype = result.get('matchtype')
 
                    if not global_config.is_quiet():
                        log_debug(f"DEBUG: Processing result for geocoding_id {geocoding_id}: lat={lat}, lon={lon}, match={match}, matchtype={matchtype}")
 
                    # The census API uses 'match' boolean and 'matchtype' string
                    # We need to determine success based on match and presence of coordinates
                    if match and lat is not None and lon is not None and str(lat).strip() and str(lon).strip():
                        # Success - include match type in status if not Exact
                        geocoding_status = 'Match'
                        if matchtype and matchtype != 'Exact':
                            geocoding_status = f'Match:{matchtype}'
 
                        api_result = {
                            'status': 'success',
                            'latitude': round(float(lat), 4),
                            'longitude': round(float(lon), 4),
                            'geocoding_status': geocoding_status
                        }
                        if not global_config.is_quiet():
                            log_debug(f"DEBUG: geocoding_id {geocoding_id} - success result: {api_result}")
                    else:
                        # No match or invalid result
                        api_result = {
                            'status': 'failed',
                            'geocoding_status': 'No_Match'
                        }
                        if not global_config.is_quiet():
                            log_debug(f"DEBUG: geocoding_id {geocoding_id} - failed result: {api_result}")
 
                    results.append({
                        'geocoding_record': geocoding_record,
                        'api_result': api_result,
                        'last_attempt': datetime.now().isoformat(),
                        'attempt_count': geocoding_record.get('attempt_count', 0) + 1
                    })
            else:
                # No results from API
                if not global_config.is_quiet():
                    log_debug(f"Census API call NO RESULT")
                    log_debug(f"DEBUG: batch_addresses that were sent: {batch_addresses}")
                for geocoding_record in batch:
                    if not global_config.is_quiet():
                        log_debug(f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - no API result, creating failed result")
                    results.append({
                        'geocoding_record': geocoding_record,
                        'api_result': {'status': 'failed', 'error': 'no result from API'},
                        'last_attempt': datetime.now().isoformat(),
                        'attempt_count': geocoding_record.get('attempt_count', 0) + 1
                    })
 
            return results
 
        except Exception as e:
            # Batch API call failed
            duration = time.time() - start_time
            self.producer._update_api_performance(len(batch), duration)
            return [{'geocoding_record': record, 'api_result': {'status': 'failed', 'error': str(e)},
                      'last_attempt': datetime.now().isoformat(), 'attempt_count': record.get('attempt_count', 0) + 1}
                    for record in batch]
