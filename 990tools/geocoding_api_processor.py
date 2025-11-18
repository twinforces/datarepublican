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
from models.geocoding_work_item import GeocodingWorkItem
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config
from constants import GEOCODING_BATCH_SIZE, GEOCODING_API_BATCH_SIZE
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
        """Feeder thread: Fetch individual geocoding records and enqueue them."""
        last_pk = None
        total_fed = 0

        if not global_config.is_quiet():
            log_info("Starting feeder thread for geocoding records")

        while not self.exit_processing:
            try:
                batch, last_pk = self._get_work_batch(last_pk)
                if not batch:
                    break

                # Enqueue each individual work item
                for work_item in batch:
                    # Check if we've already reached max_files before enqueuing this item
                    if max_files and total_fed >= max_files:
                        break

                    work_queue.put(WorkUnit.work_item(work_item))
                    total_fed += 1

                    if not global_config.is_quiet() and total_fed % 100 == 0:
                        log_debug(f"Feeder enqueued {total_fed} geocoding records")

                    # Stop if we've reached the max_files limit
                    if max_files and total_fed >= max_files:
                        break

            except Exception as e:
                log_error(f"Error in feeder thread: {e}")
                break

            # Stop if we've reached the max_files limit
            if max_files and total_fed >= max_files:
                break

        # Enqueue sentinels for each producer
        for i in range(num_producers):
            work_queue.put(WorkUnit.sentinel(i))

        if not global_config.is_quiet():
            log_info(f"Feeder completed: enqueued {total_fed} records and {num_producers} sentinels")

    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[GeocodingWorkItem], Optional[str]]:
        """Get a batch of pending geocoding records by geocoding_id > last_pk"""
        where_clause = "geocoding_status IS NULL OR geocoding_status = 'pending'"
        params = ()
        if last_pk is not None:
            where_clause += " AND geocoding_id > ?"
            params = (last_pk,)

        effective_batch_size = self.batch_size
        if global_config.max_files:
            effective_batch_size = min(global_config.max_files, effective_batch_size)

        # Use select_dataclass to get full Geocoding objects
        geocoding_records = self.db_ops.select_dataclass(
            Geocoding,
            where_clause=where_clause,
            params=params,
            order_by="geocoding_id",
            limit=effective_batch_size
        )

        if not geocoding_records:
            return [], None

        # Create work items with full Geocoding objects
        batch_data = []
        max_pk = None

        for geocoding in geocoding_records:
            batch_data.append(GeocodingWorkItem(
                geocoding_id=geocoding.geocoding_id,
                normalized_address=geocoding.normalized_address,
                address_id=None,  # Not needed in new architecture
                attempt_count=geocoding.attempt_count,
                related_address_ids=[],  # Will be populated by consumer
                geocoding_obj=geocoding  # Pass the full object
            ))

            if max_pk is None or geocoding.geocoding_id > max_pk:
                max_pk = geocoding.geocoding_id

        log_info(f"_get_work_batch: Retrieved {len(batch_data)} pending geocoding records (last_pk={last_pk}, effective_batch_size={effective_batch_size})")

        return batch_data, max_pk

    def _process_work_item(self, work_item) -> PendingDatabaseContext:
        """Process a single geocoding work item: make API call and prepare PDC with updates."""
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()

        geocoding = work_item.geocoding_obj
        if not geocoding:
            return context

        if cg is None:
            # Set failed for this geocoding record
            update_dict = {
                'geocoding_id': geocoding.geocoding_id,
                'last_attempt': now,
                'attempt_count': geocoding.attempt_count + 1,
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
                    'key_value': geocoding.geocoding_id
                }
            )
            context.addOperationToDatabase(geo_op)
            return context

        try:
            # Prepare API record from normalized_address
            normalized_address = geocoding.normalized_address
            if isinstance(normalized_address, str):
                try:
                    api_record = json.loads(normalized_address)
                except json.JSONDecodeError:
                    api_record = {'street': '', 'city': '', 'state': '', 'zip': ''}
            else:
                api_record = normalized_address.copy() if isinstance(normalized_address, dict) else {}

            # Remove the 'id' field that we use for JSON storage - censusgeocode doesn't expect it
            api_record.pop('id', None)

            # Make single API call
            api_results = cg.addressbatch([api_record])

            # Process the result
            update_dict = {
                'geocoding_id': geocoding.geocoding_id,
                'last_attempt': now,
                'attempt_count': geocoding.attempt_count + 1,
                'latitude': None,
                'longitude': None,
                'geocoding_status': None
            }

            # Handle response format (list with single result)
            if isinstance(api_results, list) and len(api_results) > 0:
                result = api_results[0]
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

                    # Update the geocoding object with results for the address update operation
                    geocoding.latitude = update_dict['latitude']
                    geocoding.longitude = update_dict['longitude']
                    geocoding.geocoding_status = update_dict['geocoding_status']

                    # Update all addresses with this canonical_address
                    addr_op = DatabaseOperation(
                        operation_type=DatabaseOperationType.UPDATE_ADDRESS_GEOCODING,
                        data=geocoding  # Pass the updated Geocoding object
                    )
                    context.addOperationToDatabase(addr_op)
                    if not global_config.is_quiet():
                        log_debug(f"Prepared Address geocoding update for canonical_address: {geocoding.canonical_address}")
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
                    'key_value': geocoding.geocoding_id
                }
            )
            context.addOperationToDatabase(geo_op)

            if not global_config.is_quiet():
                status = update_dict.get('geocoding_status', 'unknown')
                log_debug(f"Processed geocoding_id {geocoding.geocoding_id}: status={status}")

        except Exception as e:
            log_error(f"API call failed for geocoding_id {geocoding.geocoding_id}: {e}")
            # Set failed for this geocoding record
            update_dict = {
                'geocoding_id': geocoding.geocoding_id,
                'last_attempt': now,
                'attempt_count': geocoding.attempt_count + 1,
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
                    'key_value': geocoding.geocoding_id
                }
            )
            context.addOperationToDatabase(geo_op)

        return context

    def get_work_count(self, max_files: Optional[int] = None) -> int:
        """Get the total number of geocoding work items (pending records)."""
        query = """
            SELECT COUNT(*) FROM Geocoding g
            LEFT JOIN Addresses a ON g.geocoding_id = a.geocoding_id
            WHERE (g.geocoding_status IS NULL OR g.geocoding_status = 'pending')
            AND (a.colocator IS NULL OR (a.colocator NOT LIKE 'PO:%' AND a.colocator NOT LIKE 'FA:%'))
        """
        result = self.db_ops.execute_query(query)
        full_count = result.fetchone()[0] if result else 0
        return min(full_count, max_files) if max_files else full_count

    def get_progress_config(self, max_files: Optional[int] = None) -> Tuple[int, str, str]:
        """Get progress bar configuration for geocoding."""
        total = self.get_work_count(max_files)
        if max_files:
            total = min(total, max_files)
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
