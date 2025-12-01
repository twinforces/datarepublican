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
import re
import os
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
from constants import GEOCODING_BATCH_SIZE, GEOCODING_API_BATCH_SIZE, GEOCODING_MAX_UPDATES_PER_BATCH
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
        self.geocoding_patterns = self._load_geocoding_patterns()
        if cg is None and not global_config.is_quiet():
            log_warning("censusgeocode library not available. Skipping geocoding API calls. "
                        "To enable geocoding, install with: pip install censusgeocode")

    def setup_address_counts(self):
        """Setup step: Populate address_count column in Geocoding table if not already set."""
        log_info("Setup: Checking if address_count column needs to be populated")

        # Check if address_count is already populated by counting distinct values
        result = self.db_ops.execute_query("SELECT COUNT(DISTINCT address_count) FROM Geocoding")
        distinct_count = result.fetchone()[0] if result else 0

        if distinct_count > 1:
            log_info("address_count column already populated (found multiple distinct values)")
            return

        log_info("address_count column not populated, populating now...")

        # Use explicit thread-local connection (safe even if called from any thread)
        conn = self.db_ops._get_thread_local_connection()

        try:
            # Explicit transaction for the update
            conn.execute("BEGIN TRANSACTION")

            # Update address_count for all geocoding records
            conn.execute("""
                UPDATE Geocoding
                SET address_count = (
                    SELECT COUNT(*)
                    FROM Addresses
                    WHERE Addresses.geocoding_id = Geocoding.geocoding_id
                )
            """)

            # Commit the transaction
            conn.commit()

            log_info("address_count column populated successfully")

            # Get count for logging
            count_result = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding WHERE address_count > 0")
            populated_count = count_result.fetchone()[0] if count_result else 0

            log_info(f"Setup completed: {populated_count} geocoding records now have address_count > 0")

        except Exception as e:
            try:
                conn.rollback()
            except:
                pass
            log_error(f"Failed to populate address_count: {e}", exc_info=True)
            raise

    def _load_geocoding_patterns(self) -> List[Dict[str, Any]]:
        """Load geocoding patterns from JSON file."""
        patterns_file = os.path.join(os.path.dirname(__file__), 'geocoding_patterns.json')
        try:
            with open(patterns_file, 'r') as f:
                data = json.load(f)
            patterns = data.get('patterns', [])
            # Sort by priority (lower number = higher priority)
            patterns.sort(key=lambda x: x.get('priority', 999))
            log_info(f"Loaded {len(patterns)} geocoding patterns from {patterns_file}")
            return patterns
        except Exception as e:
            log_warning(f"Could not load geocoding patterns: {e}")
            return []

    def _check_geocoding_patterns(self, canonical_address: str, zip_code: str = "") -> Optional[Dict[str, Any]]:
        """Check if address matches any geocoding patterns."""
        for pattern in self.geocoding_patterns:
            # Handle nested patterns (like major_institutions)
            if 'patterns' in pattern:
                for sub_pattern in pattern['patterns']:
                    regex = sub_pattern.get('regex', '')
                    if re.search(regex, canonical_address, re.IGNORECASE):
                        return {
                            'colocator': sub_pattern.get('colocator', ''),
                            'status': sub_pattern.get('status', 'owners'),
                            'action': sub_pattern.get('action', 'match')
                        }
            else:
                regex = pattern.get('regex', '')
                if re.search(regex, canonical_address, re.IGNORECASE):
                    colocator = pattern.get('colocator', '')
                    # Replace placeholders in colocator
                    if '{zip}' in colocator and zip_code:
                        colocator = colocator.replace('{zip}', zip_code)
                    if '{city}' in colocator:
                        # Extract city from address
                        city_match = re.search(r', ([^,]+), ([A-Z]{2}),', canonical_address)
                        if city_match:
                            city = city_match.group(1).strip()
                            colocator = colocator.replace('{city}', city)
                    if '{state}' in colocator:
                        state_match = re.search(r', ([A-Z]{2}),', canonical_address)
                        if state_match:
                            state = state_match.group(1).strip()
                            colocator = colocator.replace('{state}', state)
                    if '{entity}' in colocator:
                        # For C/O patterns, extract entity name
                        entity_match = re.match(r'c/o\s+(.+?),', canonical_address, re.IGNORECASE)
                        if entity_match:
                            entity = entity_match.group(1).strip()
                            colocator = colocator.replace('{entity}', entity)
                    if '{location}' in colocator:
                        # For malls, extract a simplified location name
                        location_match = re.search(r'(.+?)\s+(?:mall|center|plaza)', canonical_address, re.IGNORECASE)
                        if location_match:
                            location = location_match.group(1).strip()
                            colocator = colocator.replace('{location}', location)

                    return {
                        'colocator': colocator,
                        'status': pattern.get('status', 'owners'),
                        'action': pattern.get('action', 'match')
                    }
        return None

    def _process_owners_work_item(self, work_item: Dict[str, Any]) -> PendingDatabaseContext:
        """Process a geocoding work item with 'owners' status - update ownership without API call."""
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()

        geocoding_id = work_item['geocoding_id']
        attempt_count = int(work_item['attempt_count'])

        # Get the colocator that should already be set on addresses
        colocator_result = self.db_ops.execute_query(
            "SELECT colocator FROM Addresses WHERE geocoding_id = ? LIMIT 1",
            (geocoding_id,)
        )
        colocator_row = colocator_result.fetchone()
        colocator = colocator_row[0] if colocator_row else None

        if colocator:
            # Update geocoding status to 'Match' since ownership is handled
            update_dict = {
                'geocoding_id': geocoding_id,
                'last_attempt': now,
                'attempt_count': attempt_count + 1,
                'latitude': None,
                'longitude': None,
                'geocoding_status': 'Match'
            }

            geo_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [update_dict],
                    'id_column': 'geocoding_id'
                }
            )
            context.addOperationToDatabase(geo_op)

            # Update owner records with colocator
            owner_result = self.db_ops.execute_query(
                "SELECT address_type, owner_id FROM Addresses WHERE geocoding_id = ?",
                (geocoding_id,)
            )

            owner_updates = []
            for row in owner_result.fetchall():
                if row[1]:  # owner_id not null
                    addr_type = row[0]
                    owner_id = row[1]
                    if addr_type == 'charity':
                        owner_updates.append({
                            'charity_id': str(owner_id),
                            'colocator': colocator
                        })
                    elif addr_type == 'grant':
                        owner_updates.append({
                            'grant_id': str(owner_id),
                            'colocator': colocator
                        })
                    elif addr_type == 'contractor':
                        owner_updates.append({
                            'contractor_id': str(owner_id),
                            'colocator': colocator
                        })
                    elif addr_type == 'politicalcontribution':
                        owner_updates.append({
                            'political_id': str(owner_id),
                            'colocator': colocator
                        })

            if owner_updates:
                # Group by table for efficiency
                table_updates = {}
                for update in owner_updates:
                    table_name = None
                    id_field = None
                    if 'charity_id' in update:
                        table_name = 'Charities'
                        id_field = 'charity_id'
                    elif 'grant_id' in update:
                        table_name = 'Grants'
                        id_field = 'grant_id'
                    elif 'contractor_id' in update:
                        table_name = 'Contractors'
                        id_field = 'contractor_id'
                    elif 'political_id' in update:
                        table_name = 'PoliticalContributions'
                        id_field = 'political_id'

                    if table_name:
                        if table_name not in table_updates:
                            table_updates[table_name] = {'updates': [], 'id_column': id_field}
                        table_updates[table_name]['updates'].append(update)

                # Create operations for each table
                for table_name, data in table_updates.items():
                    owner_op = DatabaseOperation(
                        operation_type=DatabaseOperationType.GENERIC_UPDATE,
                        data={
                            'table': table_name,
                            'updates': data['updates'],
                            'id_column': data['id_column']
                        }
                    )
                    context.addOperationToDatabase(owner_op)

        # Add progress update
        addresses_processed = work_item.get('address_count', 0)
        progress_op = DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': addresses_processed}
        )
        context.addOperationToDatabase(progress_op)

        return context

    def _get_custom_metrics(self) -> Dict[str, Any]:
        try:
            pending_result = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')"
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
        """Feeder thread: Fetch geocoding records and enqueue them in batches."""
        last_pk = None
        total_fed = 0
        total_batches = 0

        if not global_config.is_quiet():
            log_info("Starting feeder thread for geocoding records")

        while not self.exit_processing:
            try:
                batch, last_pk = self._get_work_batch(last_pk)
                if not batch:
                    break

                # Enqueue work items in batches
                for i in range(0, len(batch), self.batch_size):
                    if max_files and total_fed >= max_files:
                        break

                    work_batch = batch[i:i + self.batch_size]
                    if max_files:
                        remaining = max_files - total_fed
                        work_batch = work_batch[:remaining]

                    if not work_batch:
                        break

                    work_queue.put(WorkUnit.batch(work_batch))
                    batch_size = len(work_batch)
                    total_fed += batch_size
                    total_batches += 1

                    if not global_config.is_quiet() and total_batches % 10 == 0:
                        log_debug(f"Feeder enqueued {total_batches} batches ({total_fed} geocoding records)")

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
            log_info(f"Feeder completed: enqueued {total_batches} batches ({total_fed} records) and {num_producers} sentinels")

    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get a batch of pending geocoding records by geocoding_id > last_pk"""
        where_clause = "geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')"
        params = ()
        if last_pk is not None:
            where_clause += " AND geocoding_id > ?"
            params = (last_pk,)

        effective_batch_size = self.batch_size
        if global_config.max_files:
            effective_batch_size = min(global_config.max_files, effective_batch_size)

        # Optimized query: select only necessary fields with aliases
        select_fields = "geocoding_id as geocoding_id, normalized_address as normalized_address, attempt_count as attempt_count, canonical_address as canonical_address, address_count as address_count, geocoding_status as geocoding_status"
        query = f"SELECT {select_fields} FROM Geocoding WHERE {where_clause} ORDER BY geocoding_id LIMIT ?"
        params_list = list(params) + [effective_batch_size]
        result = self.db_ops.execute_query(query, tuple(params_list))
        rows = result.fetchall()

        batch_data = []
        max_pk = None
        for row in rows:
            work_item = {
                'geocoding_id': row[0],
                'normalized_address': row[1],
                'canonical_address': row[3],
                'attempt_count': row[2],
                'address_count': row[4] if row[4] is not None else 0,
                'geocoding_status': row[5]
            }
            batch_data.append(work_item)

            if max_pk is None or row[0] > max_pk:
                max_pk = row[0]

        log_info(f"_get_work_batch: Retrieved {len(batch_data)} geocoding records (last_pk={last_pk}, effective_batch_size={effective_batch_size})")

        return batch_data, max_pk

    def _process_work_item(self, work_item: Dict[str, Any]) -> PendingDatabaseContext:
        """Process a single geocoding work item: check patterns first, then make API call if needed."""
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()

        geocoding_id = work_item['geocoding_id']
        normalized_address = work_item['normalized_address']
        canonical_address = work_item['canonical_address']
        attempt_count = int(work_item['attempt_count'])

        # Handle owners status records - update ownership without API call
        if work_item['geocoding_status'] == 'owners':
            return self._process_owners_work_item(work_item)

        # Prevent infinite retries for stuck records
        if attempt_count > 10:
            log_warning(f"Geocoding record {geocoding_id} has {attempt_count} attempts, marking as No_Match")
            update_dict = {
                'geocoding_id': geocoding_id,
                'last_attempt': datetime.now().isoformat(),
                'attempt_count': attempt_count + 1,
                'latitude': None,
                'longitude': None,
                'geocoding_status': 'No_Match'
            }
            geo_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [update_dict],
                    'id_column': 'geocoding_id'
                }
            )
            context.addOperationToDatabase(geo_op)
            # Add progress update
            addresses_processed = work_item.get('address_count', 0)
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': addresses_processed}
            )
            context.addOperationToDatabase(progress_op)
            return context

        # Check geocoding patterns first
        zip_code = ""
        if isinstance(normalized_address, str):
            try:
                addr_data = json.loads(normalized_address)
                zip_code = addr_data.get('zip', '')
            except:
                pass

        pattern_match = self._check_geocoding_patterns(canonical_address, zip_code)
        if pattern_match:
            log_debug(f"Pattern match for geocoding_id {geocoding_id}: {pattern_match}")

            if pattern_match['action'] == 'strip':
                # Strip codes and re-queue for geocoding
                regex_pattern = pattern_match.get('regex', '')
                cleaned_address = re.sub(regex_pattern, '', canonical_address, flags=re.IGNORECASE)
                cleaned_address = re.sub(r'\s+', ' ', cleaned_address).strip()

                update_dict = {
                    'geocoding_id': geocoding_id,
                    'canonical_address': cleaned_address,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'geocoding_status': 'pending'
                }
            else:
                # Direct match with colocator
                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': pattern_match['status']
                }

            geo_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [update_dict],
                    'id_column': 'geocoding_id'
                }
            )
            context.addOperationToDatabase(geo_op)

            # Add colocator update for addresses if we have one
            if pattern_match.get('colocator'):
                colocator_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Addresses',
                        'set_clause': 'colocator = ?',
                        'where_clause': 'geocoding_id = ?',
                        'params': (pattern_match['colocator'], geocoding_id)
                    }
                )
                context.addOperationToDatabase(colocator_op)

                # If status is 'owners', also update owner records
                if pattern_match['status'] == 'owners':
                    # Query for owner information
                    owner_result = self.db_ops.execute_query(
                        "SELECT address_type, owner_id FROM Addresses WHERE geocoding_id = ?",
                        (geocoding_id,)
                    )
                    for row in owner_result.fetchall():
                        if row[1]:  # owner_id not null
                            addr_type = row[0]
                            owner_id = row[1]
                            if addr_type == 'charity':
                                owner_op = DatabaseOperation(
                                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                                    data={
                                        'table': 'Charities',
                                        'updates': [{'charity_id': str(owner_id), 'colocator': pattern_match['colocator']}],
                                        'id_column': 'charity_id'
                                    }
                                )
                                context.addOperationToDatabase(owner_op)
                            # Add similar logic for other owner types as needed

            # Add progress update
            addresses_processed = work_item.get('address_count', 0)
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': addresses_processed}
            )
            context.addOperationToDatabase(progress_op)

            return context

        # No pattern match, proceed with normal API geocoding
 
        if cg is None:
            # Set failed for this geocoding record
            update_dict = {
                'geocoding_id': geocoding_id,
                'last_attempt': now,
                'attempt_count': attempt_count + 1,
                'latitude': None,
                'longitude': None,
                'geocoding_status': 'failed'
            }
            geo_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [update_dict],
                    'id_column': 'geocoding_id'
                }
            )
            context.addOperationToDatabase(geo_op)
            return context
 
        try:
            # Prepare API record from normalized_address
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
                'geocoding_id': geocoding_id,
                'last_attempt': now,
                'attempt_count': attempt_count + 1,
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
                    rounded_lat = round(float(lat), 4)
                    rounded_lon = round(float(lon), 4)
                    update_dict['latitude'] = rounded_lat
                    update_dict['longitude'] = rounded_lon
                    update_dict['geocoding_status'] = geocoding_status
 
                    colocator = f"LL:{rounded_lat}:{rounded_lon}"
 
                    # Set colocator on all addresses with this geocoding_id
                    colocator_data = {
                        'table': 'Addresses',
                        'set_clause': 'colocator = ?',
                        'where_clause': 'geocoding_id = ?',
                        'params': (colocator, geocoding_id)
                    }
                    colocator_op = DatabaseOperation(
                        operation_type=DatabaseOperationType.GENERIC_UPDATE,
                        data=colocator_data
                    )
                    context.addOperationToDatabase(colocator_op)
                    if not global_config.is_quiet():
                        log_debug(f"Prepared Address colocator update for geocoding_id: {geocoding_id}")
 
                    # Owner updates - query using geocoding_id
                    owner_updates = {}
                    try:
                        owner_result = self.db_ops.execute_query(
                            "SELECT address_type, owner_id FROM Addresses WHERE geocoding_id = ?",
                            (geocoding_id,)
                        )
                        for row in owner_result.fetchall():
                            if row[1]:  # owner_id not null
                                addr_type = row[0]
                                owner_id = row[1]
                                if addr_type not in owner_updates:
                                    owner_updates[addr_type] = set()
                                owner_updates[addr_type].add(owner_id)
                    except Exception as e:
                        log_warning(f"Could not query owners for geocoding_id {geocoding_id}: {e}")
 
                    # Type map
                    type_map = {
                        'charity': {'table': 'Charities', 'id_field': 'charity_id'},
                        'grant': {'table': 'Grants', 'id_field': 'grant_id'},
                        'contractor': {'table': 'Contractors', 'id_field': 'contractor_id'},
                        'politicalcontribution': {'table': 'PoliticalContributions', 'id_field': 'political_id'},
                    }
                    for addr_type, oids in owner_updates.items():
                        if addr_type in type_map:
                            map_info = type_map[addr_type]
                            table = map_info['table']
                            id_field = map_info['id_field']
                            updates = [{id_field: str(oid), 'colocator': colocator} for oid in sorted(oids)]
                            owner_data = {
                                'table': table,
                                'updates': updates,
                                'id_column': id_field
                            }
                            owner_op = DatabaseOperation(
                                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                                data=owner_data
                            )
                            context.addOperationToDatabase(owner_op)
                            if not global_config.is_quiet():
                                log_debug(f"Prepared owner colocator update for {addr_type}: {len(oids)} ids")
                else:
                    update_dict['geocoding_status'] = 'No_Match'
            else:
                update_dict['geocoding_status'] = 'failed'
 
            # Add UPDATE operation for Geocoding
            geo_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [update_dict],
                    'id_column': 'geocoding_id'
                }
            )
            context.addOperationToDatabase(geo_op)

            # Set estimated updates for size-based batching decisions
            context.estimated_updates = work_item.get('address_count', 0) * 2

            if not global_config.is_quiet():
                status = update_dict.get('geocoding_status', 'unknown')
                log_debug(f"Processed geocoding_id {geocoding_id}: status={status}")

        except Exception as e:
            log_error(f"API call failed for geocoding_id {geocoding_id}: {e}")
            # Set failed for this geocoding record
            update_dict = {
                'geocoding_id': geocoding_id,
                'last_attempt': now,
                'attempt_count': attempt_count + 1,
                'latitude': None,
                'longitude': None,
                'geocoding_status': 'failed'
            }
            geo_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [update_dict],
                    'id_column': 'geocoding_id'
                }
            )
            context.addOperationToDatabase(geo_op)

        # Add progress update for the addresses processed in this work item
        addresses_processed = work_item.get('address_count', 0)
        progress_op = DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': addresses_processed}
        )
        context.addOperationToDatabase(progress_op)

        return context

    def _producer_worker(self, producer_id: int, num_producers: int):
        """Producer worker thread: Processes work items from work_queue to result_queue."""
        log_info(f"PRODUCER THREAD {producer_id} STARTED")
        processed_count = 0
        while True:
            # DEADLOCK PREVENTION: Check for exit processing before database operations
            if BaseProcessor.exit_processing:
                log_info("Feeder thread exiting due to exit_processing flag")
                break
            item = self.work_queue.get()

            if item.is_sentinel_for(producer_id):
                self.work_queue.task_done()
                # Send sentinel to result queue
                self.result_queue.put(WorkUnit.sentinel(producer_id))
                log_info(f"Producer {producer_id} processed {processed_count} items, sending sentinel to consumer")
                break
            elif item.is_sentinel():
                # Sentinel for another producer, put back
                self.work_queue.put(item)
                # Do not call task_done() for items that are put back
                time.sleep(0.1)  # Avoid busy waiting
                continue

            if item.type == 'work':
                pdc = self._process_work_item(item.data)
                self.result_queue.put(WorkUnit.result(pdc))
                processed_count += 1
            elif item.type == 'batch':
                pdc = self._process_batch(item.data)
                self.result_queue.put(WorkUnit.result(pdc))
                processed_count += len(item.data)  # Count individual geocoding records

            self.work_queue.task_done()

            # Check for exit processing
            if BaseProcessor.exit_processing:
                break

        log_info(f"PRODUCER THREAD {producer_id} COMPLETED")

    def _process_batch(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        """Process a batch of geocoding work items: check patterns first, then make batched API call."""
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()

        # Separate items by type: owners, pattern matches and API calls
        owners_items = []
        pattern_matches = []
        api_batch = []

        for work_item in batch:
            if work_item['geocoding_status'] == 'owners':
                owners_items.append(work_item)
            else:
                canonical_address = work_item['canonical_address']
                zip_code = ""
                normalized_address = work_item['normalized_address']

                if isinstance(normalized_address, str):
                    try:
                        addr_data = json.loads(normalized_address)
                        zip_code = addr_data.get('zip', '')
                    except:
                        pass

                pattern_match = self._check_geocoding_patterns(canonical_address, zip_code)
                if pattern_match:
                    log_debug(f"Batch pattern match for geocoding_id {work_item['geocoding_id']}: {pattern_match}")
                    pattern_matches.append((work_item, pattern_match))
                else:
                    api_batch.append(work_item)

        # Process owners items
        for work_item in owners_items:
            owner_context = self._process_owners_work_item(work_item)
            context.operations.extend(owner_context.operations)
            context.estimated_updates += owner_context.estimated_updates

        # Process pattern matches
        for work_item, pattern_match in pattern_matches:
            geocoding_id = work_item['geocoding_id']
            attempt_count = int(work_item['attempt_count'])

            if pattern_match['action'] == 'strip':
                # Strip codes and re-queue for geocoding
                regex_pattern = pattern_match.get('regex', '')
                cleaned_address = re.sub(regex_pattern, '', work_item['canonical_address'], flags=re.IGNORECASE)
                cleaned_address = re.sub(r'\s+', ' ', cleaned_address).strip()

                update_dict = {
                    'geocoding_id': geocoding_id,
                    'canonical_address': cleaned_address,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'geocoding_status': 'pending'
                }
            else:
                # Direct match with colocator
                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': pattern_match['status']
                }

            geo_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [update_dict],
                    'id_column': 'geocoding_id'
                }
            )
            context.addOperationToDatabase(geo_op)

            # Add colocator update for addresses if we have one
            if pattern_match.get('colocator'):
                colocator_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Addresses',
                        'set_clause': 'colocator = ?',
                        'where_clause': 'geocoding_id = ?',
                        'params': (pattern_match['colocator'], geocoding_id)
                    }
                )
                context.addOperationToDatabase(colocator_op)

        # Process remaining items with API calls
        if not api_batch:
            # Add progress update for pattern-matched items
            addresses_processed = sum(work_item.get('address_count', 0) for work_item, _ in pattern_matches)
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': addresses_processed}
            )
            context.addOperationToDatabase(progress_op)
            return context

        # Continue with normal API batch processing for remaining items
        if cg is None:
            # Set failed for all remaining geocoding records in batch
            for work_item in api_batch:
                geocoding_id = work_item['geocoding_id']
                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': int(work_item['attempt_count']) + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': 'failed'
                }
                geo_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Geocoding',
                        'updates': [update_dict],
                        'id_column': 'geocoding_id'
                    }
                )
                context.addOperationToDatabase(geo_op)
            return context

        # Prepare API records with ids
        api_records = []
        work_item_map = {}
        for work_item in api_batch:
            geocoding_id = work_item['geocoding_id']
            normalized_address = work_item['normalized_address']
            canonical_address = work_item['canonical_address']

            if isinstance(normalized_address, str):
                try:
                    api_record = json.loads(normalized_address)
                except json.JSONDecodeError:
                    api_record = {'street': '', 'city': '', 'state': '', 'zip': ''}
            else:
                api_record = normalized_address.copy() if isinstance(normalized_address, dict) else {}

            # Remove the 'id' field that we use for JSON storage - censusgeocode doesn't expect it
            api_record.pop('id', None)
            # Add our geocoding_id as 'id' for matching results
            api_record['id'] = str(geocoding_id)

            api_records.append(api_record)
            work_item_map[str(geocoding_id)] = work_item

        try:
            # Make batched API call
            api_results = cg.addressbatch(api_records)

            # Process results
            for result in api_results:
                geocoding_id_str = result.get('id')
                if not geocoding_id_str:
                    continue
                work_item = work_item_map.get(geocoding_id_str)
                if not work_item:
                    continue

                geocoding_id = work_item['geocoding_id']
                attempt_count = int(work_item['attempt_count'])

                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': attempt_count + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': None
                }

                lat = result.get('lat')
                lon = result.get('lon')
                match = result.get('match', False)
                matchtype = result.get('matchtype', '')

                if match and lat is not None and lon is not None and str(lat).strip() and str(lon).strip():
                    geocoding_status = 'Match'
                    if matchtype and matchtype != 'Exact':
                        geocoding_status = f'Match:{matchtype}'
                    rounded_lat = round(float(lat), 4)
                    rounded_lon = round(float(lon), 4)
                    update_dict['latitude'] = rounded_lat
                    update_dict['longitude'] = rounded_lon
                    update_dict['geocoding_status'] = geocoding_status

                    colocator = f"LL:{rounded_lat}:{rounded_lon}"

                    # Set colocator on all addresses with this geocoding_id
                    colocator_data = {
                        'table': 'Addresses',
                        'set_clause': 'colocator = ?',
                        'where_clause': 'geocoding_id = ?',
                        'params': (colocator, geocoding_id)
                    }
                    colocator_op = DatabaseOperation(
                        operation_type=DatabaseOperationType.GENERIC_UPDATE,
                        data=colocator_data
                    )
                    context.addOperationToDatabase(colocator_op)
                    if not global_config.is_quiet():
                        log_debug(f"Prepared Address colocator update for geocoding_id: {geocoding_id}")

                    # Owner updates - query using geocoding_id
                    owner_updates = {}
                    try:
                        owner_result = self.db_ops.execute_query(
                            "SELECT address_type, owner_id FROM Addresses WHERE geocoding_id = ?",
                            (geocoding_id,)
                        )
                        for row in owner_result.fetchall():
                            if row[1]:  # owner_id not null
                                addr_type = row[0]
                                owner_id = row[1]
                                if addr_type not in owner_updates:
                                    owner_updates[addr_type] = set()
                                owner_updates[addr_type].add(owner_id)
                    except Exception as e:
                        log_warning(f"Could not query owners for geocoding_id {geocoding_id}: {e}")

                    # Type map
                    type_map = {
                        'charity': {'table': 'Charities', 'id_field': 'charity_id'},
                        'grant': {'table': 'Grants', 'id_field': 'grant_id'},
                        'contractor': {'table': 'Contractors', 'id_field': 'contractor_id'},
                        'politicalcontribution': {'table': 'PoliticalContributions', 'id_field': 'political_id'},
                    }
                    for addr_type, oids in owner_updates.items():
                        if addr_type in type_map:
                            map_info = type_map[addr_type]
                            table = map_info['table']
                            id_field = map_info['id_field']
                            updates = [{id_field: str(oid), 'colocator': colocator} for oid in sorted(oids)]
                            owner_data = {
                                'table': table,
                                'updates': updates,
                                'id_column': id_field
                            }
                            owner_op = DatabaseOperation(
                                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                                data=owner_data
                            )
                            context.addOperationToDatabase(owner_op)
                            if not global_config.is_quiet():
                                log_debug(f"Prepared owner colocator update for {addr_type}: {len(oids)} ids")
                else:
                    update_dict['geocoding_status'] = 'No_Match'

                # Add UPDATE operation for Geocoding
                geo_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Geocoding',
                        'updates': [update_dict],
                        'id_column': 'geocoding_id'
                    }
                )
                context.addOperationToDatabase(geo_op)

                if not global_config.is_quiet():
                    status = update_dict.get('geocoding_status', 'unknown')
                    log_debug(f"Processed geocoding_id {geocoding_id}: status={status}")

        except Exception as e:
            log_error(f"Batched API call failed: {e}")
            # Set failed for all in batch
            for work_item in batch:
                geocoding_id = work_item['geocoding_id']
                update_dict = {
                    'geocoding_id': geocoding_id,
                    'last_attempt': now,
                    'attempt_count': int(work_item['attempt_count']) + 1,
                    'latitude': None,
                    'longitude': None,
                    'geocoding_status': 'failed'
                }
                geo_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Geocoding',
                        'updates': [update_dict],
                        'id_column': 'geocoding_id'
                    }
                )
                context.addOperationToDatabase(geo_op)

        # Add progress update for all addresses processed in this batch (both pattern matches and API calls)
        addresses_processed = sum(work_item.get('address_count', 0) for work_item in batch)
        progress_op = DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': addresses_processed}
        )
        context.addOperationToDatabase(progress_op)

        return context

    def get_work_count(self, max_files: Optional[int] = None) -> int:
        """Get the total number of addresses to geocode."""
        if max_files:
            query = """
                SELECT SUM(address_count) FROM (
                    SELECT address_count FROM Geocoding
                    WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
                    ORDER BY geocoding_id
                    LIMIT ?
                )
            """
            result = self.db_ops.execute_query(query, (max_files,))
        else:
            query = """
                SELECT SUM(address_count) FROM Geocoding
                WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
            """
            result = self.db_ops.execute_query(query)
        row = result.fetchone() if result else None
        total_addresses = row[0] if row and row[0] is not None else 0
        return total_addresses

    def get_progress_config(self, max_files: Optional[int] = None) -> Tuple[int, str, str]:
        """Get progress bar configuration for geocoding."""
        total = self.get_work_count(max_files)
        return total, "addresses", "Geocoding addresses"

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

        # Setup step: Ensure address_count column is populated
        self.setup_address_counts()

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
