#!/usr/bin/env python3
"""
geocoding_api_processor.py - Standalone geocoding API processor for Phase 2

This module handles the API calls for geocoding records created in Phase 1.
It processes geocoding records that are pending API calls, makes census API
calls, and updates the records with the results. This is a standalone process
that can run independently of address deduplication.
"""

import logging
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

from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models.geocoding import Geocoding
from logging_utils import log_info, log_error, log_debug, log_warning, get_logger
from config import global_config
from constants import GEOCODING_API_BATCH_SIZE


class GeocodingAPIProcessor:
    """
    Standalone processor for geocoding API calls (Phase 2).

    This processor reads existing geocoding records and makes census API calls
    to update them with latitude/longitude coordinates. It can run independently
    of the address deduplication process.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_API_BATCH_SIZE):
        self.db_ops = db_ops
        self.batch_size = batch_size
        self.logger = get_logger("geocoding_api_processor")

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

    def process_pending_geocoding_records(self, progress_bar=None) -> int:
        """
        Process all pending geocoding records by making API calls.

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

        # Get total pending records for progress bar
        total_pending_query = """
            SELECT COUNT(*) FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status = 'pending'
        """
        result = self.db_ops.execute_query(total_pending_query)
        total_pending = result.fetchone()[0] if result else 0

        # Apply max_files limit if set
        if global_config.max_files:
            total_pending = min(total_pending, global_config.max_files)

        # Create progress bar if none provided and tqdm is available
        created_progress_bar = False
        if progress_bar is None and tqdm is not None and not global_config.is_quiet():
            progress_bar = tqdm.tqdm(
                total=total_pending,
                desc="Geocoding records",
                unit="records",
                bar_format='{desc}: {n}/{total} |{bar}| {percentage:3.0f}% | {elapsed} | {rate_fmt}'
            )
            created_progress_bar = True

        total_processed = 0
        last_geocoding_id = None

        if not global_config.is_quiet():
            log_info(self.logger, f"Starting geocoding API processing with batch_size={self.batch_size}")

        while True:
            # Check if we've reached the global max_files limit
            if global_config.max_files and total_processed >= global_config.max_files:
                if not global_config.is_quiet():
                    log_info(self.logger, f"Reached max_files limit: {global_config.max_files} records processed")
                break

            # Get next batch of geocoding records needing API calls
            batch = self._get_pending_geocoding_records(last_geocoding_id)
            if not batch:
                break

            # Process the batch
            batch_processed = self._process_geocoding_batch(batch)
            total_processed += batch_processed

            # Update progress bar
            if progress_bar:
                progress_bar.update(batch_processed)

            # Update last_geocoding_id for pagination
            if batch:
                last_geocoding_id = max(record['geocoding_id'] for record in batch)

            if not global_config.is_quiet():
                log_debug(self.logger, f"PHASE 2: Processed batch of {len(batch)} records, total processed: {total_processed}")

        if not global_config.is_quiet():
            log_info(self.logger, f"Geocoding API processing completed: {total_processed} records processed")

        # Close progress bar if we created it
        if created_progress_bar and progress_bar:
            progress_bar.close()

        return total_processed

    def _get_pending_geocoding_records(self, last_geocoding_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get a batch of geocoding records that need API calls.

        Args:
            last_geocoding_id: Last geocoding_id from previous batch for pagination

        Returns:
            List of geocoding record dictionaries
        """
        current_batch_size = self._get_dynamic_batch_size()

        where_clause = "geocoding_status IS NULL OR geocoding_status = 'pending'"
        if last_geocoding_id is not None:
            where_clause += " AND geocoding_id > ?"
            params = (last_geocoding_id,)
        else:
            params = None

        effective_batch_size = min(global_config.max_files, current_batch_size) if global_config.max_files else current_batch_size
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
                    'normalized_address': row[1],  # This is the prebuilt JSON dict
                    'attempt_count': row[2] or 0
                }
                for row in rows
            ]

            if not global_config.is_quiet():
                log_debug(self.logger, f"PHASE 2: Retrieved {len(batch_data)} geocoding records from database (last_geocoding_id={last_geocoding_id})")
                log_debug(self.logger, f"PHASE 2: Dynamic batch size was {effective_batch_size} (base: {self.batch_size}, max_files: {global_config.max_files})")

            return batch_data
        return []

    def _process_geocoding_batch(self, batch: List[Dict[str, Any]]) -> int:
        """
        Process a batch of geocoding records with API calls.

        Args:
            batch: List of geocoding record dictionaries

        Returns:
            Number of records processed
        """
        if not batch:
            return 0

        if not global_config.is_quiet():
            log_debug(self.logger, f"PHASE 2: Processing batch of {len(batch)} geocoding records")

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
                log_debug(self.logger, f"PHASE 2: Update dict for geocoding_id {update_dict['geocoding_id']}: {update_dict}")

            updates.append(update_dict)

            # Track successful geocoding results for Address table updates
            if api_result.get('status') == 'success' and update_dict['latitude'] is not None and update_dict['longitude'] is not None:
                # Create colocator string for successful geocoding
                colocator = f"LL:{update_dict['latitude']}:{update_dict['longitude']}"
                address_updates.append({
                    'geocoding_id': update_dict['geocoding_id'],
                    'colocator': colocator
                })

        # Execute bulk update for Geocoding table
        if updates:
            total_updated = self.db_ops.bulk_update('Geocoding', updates, 'geocoding_id')
            if not global_config.is_quiet():
                log_debug(self.logger, f"PHASE 2: Executed {len(updates)} updates, updated {total_updated} records")

        # Update Address table and propagate colocator for successful geocoding
        if address_updates:
            for addr_update in address_updates:
                geocoding_id = addr_update['geocoding_id']
                colocator = addr_update['colocator']

                # Find the address_id that corresponds to this geocoding_id
                # We need to query the Addresses table to find the address_id
                # This is a bit inefficient but necessary since we don't have address_id in the batch
                address_query = """
                    SELECT address_id FROM Addresses WHERE geocoding_id = ?
                """
                result = self.db_ops.execute_query(address_query, (geocoding_id,))
                if result:
                    row = result.fetchone()
                    if row and row[0]:
                        address_id = row[0]
                        # Update the Address table with geocoding_id and colocator, and propagate colocator to owner
                        self.db_ops.update_address_geocoding(address_id, geocoding_id, colocator)
                        if not global_config.is_quiet():
                            log_debug(self.logger, f"PHASE 2: Updated Address {address_id} with geocoding_id {geocoding_id} and colocator {colocator}")

            if not global_config.is_quiet():
                log_debug(self.logger, f"PHASE 2: Updated {len(address_updates)} addresses with geocoding results and propagated colocator")

        return len(batch) if updates else 0

    def _batch_geocode_addresses(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Make a batch geocoding API call for multiple addresses.

        Args:
            batch: List of geocoding record dictionaries

        Returns:
            List of result dictionaries
        """
        start_time = time.time()

        if not cg:
            duration = time.time() - start_time
            self._update_api_performance(len(batch), duration)
            return [{'geocoding_record': record, 'api_result': {'status': 'failed', 'error': 'censusgeocode not available'},
                      'last_attempt': datetime.now().isoformat(), 'attempt_count': record.get('attempt_count', 0) + 1}
                    for record in batch]

        try:
            # Prepare batch addresses for API call
            batch_addresses = []
            record_map = {}  # Map geocoding_id to original record

            for geocoding_record in batch:
                # Use the prebuilt JSON directly from normalized_address field
                census_json = geocoding_record['normalized_address']
                if not global_config.is_quiet():
                    log_debug(self.logger, f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - census_json type: {type(census_json)}, value: {census_json}")

                if isinstance(census_json, dict):
                    # Set the ID to geocoding_id for easy lookup when results come back
                    api_record = census_json.copy()
                    api_record['id'] = str(geocoding_record['geocoding_id'])  # Ensure string ID
                    if not global_config.is_quiet():
                        log_debug(self.logger, f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - dict path: api_record after copy: {api_record}")
                else:
                    # Parse as JSON (all records should now be proper JSON)
                    try:
                        import json
                        api_record = json.loads(census_json)
                        api_record['id'] = str(geocoding_record['geocoding_id'])  # Ensure string ID
                        if not global_config.is_quiet():
                            log_debug(self.logger, f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - JSON parse successful: api_record: {api_record}")
                    except Exception as json_e:
                        if not global_config.is_quiet():
                            log_error(self.logger, f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - JSON parse failed: {json_e}, creating minimal record")
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
                    log_debug(self.logger, f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} (type: {type(geocoding_record['geocoding_id'])}) - added to batch_addresses: {api_record}")
                    log_debug(self.logger, f"DEBUG: record_map keys after addition: {[(k, type(k)) for k in list(record_map.keys())[-3:]]}")

            # Make batch API call
            if not global_config.is_quiet():
                log_debug(self.logger, f"Making batch API call with {len(batch_addresses)} addresses")
                # Log the actual address data being sent to API
                for addr in batch_addresses:
                    log_debug(self.logger, f"API call address: id={addr.get('id')}, street='{addr.get('street')}', city='{addr.get('city')}', state='{addr.get('state')}', zip='{addr.get('zip')}'")
            api_results = cg.addressbatch(batch_addresses)
            if not global_config.is_quiet():
                log_debug(self.logger, f"DEBUG: API call completed, api_results: {api_results}")

            # Track performance
            duration = time.time() - start_time
            self._update_api_performance(len(batch), duration)

            # Process results
            results = []
            if api_results and len(api_results) > 0:
                if not global_config.is_quiet():
                    log_debug(self.logger,f"Census API call params {api_results}")

                for result in api_results:
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
                            log_error(self.logger, f"PHASE 2: CRITICAL ERROR - No geocoding_record found for geocoding_id {geocoding_id} (type: {type(geocoding_id)})")
                            log_error(self.logger, f"PHASE 2: Available geocoding_ids in record_map: {[(k, type(k)) for k in list(record_map.keys())[:5]]}")
                            log_error(self.logger, f"PHASE 2: API result being processed: {result}")
                        continue

                    lat = result.get('lat')
                    lon = result.get('lon')
                    match = result.get('match', False)
                    matchtype = result.get('matchtype')

                    if not global_config.is_quiet():
                        log_debug(self.logger, f"DEBUG: Processing result for geocoding_id {geocoding_id}: lat={lat}, lon={lon}, match={match}, matchtype={matchtype}")

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
                            log_debug(self.logger, f"DEBUG: geocoding_id {geocoding_id} - success result: {api_result}")
                    else:
                        # No match or invalid result
                        api_result = {
                            'status': 'failed',
                            'geocoding_status': 'No_Match'
                        }
                        if not global_config.is_quiet():
                            log_debug(self.logger, f"DEBUG: geocoding_id {geocoding_id} - failed result: {api_result}")

                    results.append({
                        'geocoding_record': geocoding_record,
                        'api_result': api_result,
                        'last_attempt': datetime.now().isoformat(),
                        'attempt_count': geocoding_record.get('attempt_count', 0) + 1
                    })
            else:
                # No results from API
                if not global_config.is_quiet():
                    log_debug(self.logger,f"Census API call NO RESULT")
                    log_debug(self.logger, f"DEBUG: batch_addresses that were sent: {batch_addresses}")
                for geocoding_record in batch:
                    if not global_config.is_quiet():
                        log_debug(self.logger, f"DEBUG: geocoding_id {geocoding_record['geocoding_id']} - no API result, creating failed result")
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
            self._update_api_performance(len(batch), duration)
            return [{'geocoding_record': record, 'api_result': {'status': 'failed', 'error': str(e)},
                      'last_attempt': datetime.now().isoformat(), 'attempt_count': record.get('attempt_count', 0) + 1}
                    for record in batch]

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
            log_debug(self.logger, f"PHASE 2: Dynamic batch sizing - performance: {performance_level}, avg_time_per_record: {avg_time:.2f}s, factor: {factor}, batch_size: {self.batch_size} -> {dynamic_size}")

        return dynamic_size