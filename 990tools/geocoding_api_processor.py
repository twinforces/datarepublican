#!/usr/bin/env python3
"""
geocoding_api_processor.py - Geocoding API processor using BaseProcessor architecture

This module handles the API calls for geocoding records. It processes geocoding
records that are pending API calls, makes census API calls, and updates the
records with the results. Uses the generalized BaseProcessor multi-threaded
architecture for safe batch processing with DuckDB.
"""

import time
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

try:
    import censusgeocode as cg
except ImportError:
    cg = None

try:
    import tqdm
except ImportError:
    tqdm = None

from base_processor import BaseProcessor, WorkUnit
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models.geocoding import Geocoding
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config
from constants import GEOCODING_BATCH_SIZE
from pending_database_context import PendingDatabaseContext


class GeocodingAPIProcessor(BaseProcessor):
    """
    Processor for geocoding API calls using the generalized BaseProcessor architecture.

    This processor reads existing geocoding records and makes census API calls
    to update them with latitude/longitude coordinates. Uses the multi-threaded
    feeder-producer-consumer pattern for safe batch processing with DuckDB.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_BATCH_SIZE):
        super().__init__(db_ops)
        self.batch_size = batch_size
        if cg is None and not global_config.is_quiet():
            log_warning("censusgeocode library not available. Skipping geocoding API calls. "
                        "To enable geocoding, install with: pip install censusgeocode")

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

    def _feed_thread(self, work_queue, max_files: Optional[int] = None, num_producers: int = 4) -> None:
        """Feeder thread: Fetch batches of geocoding records and enqueue batch work items (up to 1000 records per API call)."""
        last_pk = None
        total_fed = 0
        if not global_config.is_quiet():
            log_info("Starting feeder thread for geocoding records (batching up to 1000 per API call)")

        while not self.exit_processing:
            batch, last_pk = self._get_work_batch(last_pk)
            if not batch:
                break

            if max_files and total_fed >= max_files:
                # Truncate batch if needed
                remaining = max_files - total_fed
                batch = batch[:remaining]
                if not batch:
                    break

            work_queue.put(WorkUnit.work_item(batch))
            total_fed += len(batch)
            if not global_config.is_quiet() and total_fed % 1000 == 0:
                log_debug(f"Feeder enqueued {total_fed} geocoding records in batches")

            if max_files and total_fed >= max_files:
                break

        # Enqueue sentinels for each producer
        for i in range(num_producers):
            work_queue.put(WorkUnit.sentinel(i))

        if not global_config.is_quiet():
            log_info(f"Feeder completed: enqueued {total_fed} records in batches and {num_producers} sentinels")

    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get a batch of geocoding records needing API calls, including address_id via JOIN."""
        where_clause = "g.geocoding_status IS NULL OR g.geocoding_status = 'pending'"
        params = ()
        if last_pk is not None:
            where_clause += " AND g.geocoding_id > ?"
            params = (last_pk,)
 
        effective_batch_size = 1000  # Batch size for Census API (up to 10000 supported, 1000 safe for rate limits)
        if global_config.max_files:
            effective_batch_size = min(global_config.max_files, effective_batch_size)
 
        query = f"""
            SELECT g.geocoding_id, g.normalized_address, g.attempt_count, a.address_id
            FROM Geocoding g
            LEFT JOIN Addresses a ON g.geocoding_id = a.geocoding_id
            WHERE {where_clause}
            ORDER BY g.geocoding_id
            LIMIT ?
        """
        params += (effective_batch_size,)
 
        result = self.db_ops.execute_query(query, params)
        if result:
            rows = result.fetchall()
            batch_data = [
                {
                    'geocoding_id': row[0],
                    'normalized_address': row[1],
                    'attempt_count': row[2] or 0,
                    'address_id': row[3]
                }
                for row in rows
            ]
 
            max_pk = max(row[0] for row in rows) if rows else None
 
            if not global_config.is_quiet() and batch_data:
                log_debug(f"Feeder retrieved {len(batch_data)} geocoding records (last_pk={last_pk})")
 
            return batch_data, max_pk
        return [], None

    def _process_work_item(self, work_item) -> PendingDatabaseContext:
        """Process a batch of geocoding work items: make batch API call and prepare PDC with updates."""
        batch = work_item  # batch is list of record dicts
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()

        if not batch:
            return context

        if cg is None:
            # Set failed for all in batch
            for record in batch:
                geocoding_id = record['geocoding_id']
                attempt_count = record['attempt_count'] or 0
                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': 'failed'
                }
                geo_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.UPDATE_GEOCODING,
                    data={
                        'table': 'Geocoding',
                        'updates': update_dict,
                        'key_field': 'geocoding_id',
                        'key_value': geocoding_id
                    }
                )
                context.addOperationToDatabase(geo_op)
            return context

        try:
            # Prepare batch_records for API: list of address dicts
            batch_records = []
            for record in batch:
                normalized_address = record['normalized_address']
                if isinstance(normalized_address, str):
                    try:
                        api_record = json.loads(normalized_address)
                    except json.JSONDecodeError:
                        api_record = {'street': '', 'city': '', 'state': '', 'zip': ''}
                else:
                    api_record = normalized_address.copy() if isinstance(normalized_address, dict) else {}
                batch_records.append(api_record)

            # Make batch API call
            api_results = cg.address(batch_records)

            # Process each result (match by index)
            for i, record in enumerate(batch):
                geocoding_id = record['geocoding_id']
                attempt_count = record['attempt_count'] or 0
                address_id = record['address_id']
                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': None
                }

                if i < len(api_results) and api_results[i]:
                    result = api_results[i]
                    lat = result.get('lat')
                    lon = result.get('lon')
                    match = result.get('match', False)
                    matchtype = result.get('matchtype', '')

                    if match and lat is not None and lon is not None and str(lat).strip() and str(lon).strip():
                        geocoding_status = 'Match'
                        if matchtype and matchtype != 'Exact':
                            geocoding_status = f'Match:{matchtype}'
                        update_dict['latitude'] = round(float(lat), 4)
                        update_dict['longitude'] = round(float(lon), 4)
                        update_dict['geocoding_status'] = geocoding_status

                        # Update Address if successful and address_id exists
                        if address_id:
                            colocator = f"LL:{update_dict['latitude']}:{update_dict['longitude']}"
                            addr_op = DatabaseOperation(
                                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                                data={
                                    'table': 'Addresses',
                                    'updates': {'geocoding_id': geocoding_id, 'colocator': colocator},
                                    'key_field': 'address_id',
                                    'key_value': address_id
                                }
                            )
                            context.addOperationToDatabase(addr_op)
                            if not global_config.is_quiet():
                                log_debug(f"Prepared Address update for {address_id} with colocator {colocator}")
                    else:
                        update_dict['geocoding_status'] = 'No_Match'
                else:
                    update_dict['geocoding_status'] = 'failed'

                # Add UPDATE operation for Geocoding
                geo_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.UPDATE_GEOCODING,
                    data={
                        'table': 'Geocoding',
                        'updates': update_dict,
                        'key_field': 'geocoding_id',
                        'key_value': geocoding_id
                    }
                )
                context.addOperationToDatabase(geo_op)

                if not global_config.is_quiet():
                    status = update_dict.get('geocoding_status', 'unknown')
                    log_debug(f"Processed geocoding_id {geocoding_id}: status={status}")

        except Exception as e:
            log_error(f"Batch API call failed for batch starting {batch[0]['geocoding_id'] if batch else 'empty'}: {e}")
            # Set failed for all in batch
            for record in batch:
                geocoding_id = record['geocoding_id']
                attempt_count = record['attempt_count'] or 0
                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': 'failed'
                }
                geo_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.UPDATE_GEOCODING,
                    data={
                        'table': 'Geocoding',
                        'updates': update_dict,
                        'key_field': 'geocoding_id',
                        'key_value': geocoding_id
                    }
                )
                context.addOperationToDatabase(geo_op)

        return context

    def get_work_count(self, max_files: Optional[int] = None) -> int:
        """Get the total number of geocoding work items (pending records)."""
        query = """
            SELECT COUNT(*) FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status = 'pending'
        """
        result = self.db_ops.execute_query(query)
        full_count = result.fetchone()[0] if result else 0
        return min(full_count, max_files) if max_files else full_count

    def get_progress_config(self, max_files: Optional[int] = None) -> Tuple[int, str, str]:
        """Get progress bar configuration for geocoding."""
        total = self.get_work_count(max_files)
        return total, "addr", "Geocoding API calls"

    def process_pending_geocoding_records(self, max_files: Optional[int] = None, progress_bar=None) -> int:
        """
        Process all pending geocoding records using the BaseProcessor architecture.

        Args:
            max_files: Maximum number of records to process
            progress_bar: Optional progress bar to update

        Returns:
            Number of geocoding records processed
        """
        if cg is None:
            if not global_config.is_quiet():
                log_warning("censusgeocode library not available. Skipping geocoding API calls.")
            return 0

        if not global_config.is_quiet():
            log_info("Starting geocoding API processing")

        try:
            processed = self.process_parallel(max_files=max_files)
            if not global_config.is_quiet():
                log_info(f"Geocoding API processing completed: {processed} records processed")
            return processed
        except Exception as e:
            log_error(f"Geocoding API processing failed: {e}", exc_info=True)
            return 0
