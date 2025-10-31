#!/usr/bin/env python3
"""
geolocation_processor.py - Geolocation processor using Producer-Consumer pattern

This module handles geocoding of addresses using the census API with Producer-Consumer pattern,
storing latitude/longitude coordinates for address matching.

Now uses Producer-Consumer pattern for safe batch processing with DuckDB.
"""

import logging
import time
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any

try:
    import censusgeocode as cg
except ImportError:
    cg = None

from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models import Address
from models.geocoding import Geocoding
from constants import VALID_STATES, STATE_NAME_TO_ABBREV, PO_BOX_REGEX, PO_BOX_NUMBER_REGEX, GEOCODING_BATCH_SIZE, GEOCODING_API_BATCH_SIZE, GEOCODING_FAST_WORKERS, GEOCODING_API_WORKERS
from logging_utils import log_info, log_error, log_debug, log_warning, start_progress_reporting, stop_progress_reporting, update_progress, set_progress_description, get_logger
from address_deduplication_processor import AddressDeduplicationProcessor
from base_processor import BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig
from config import global_config

# Set up logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GeolocationProducerGC(BaseProducer):
    """
    Producer for creating Geocoding records - Phase 1: Fast processing

    Creates Geocoding records for addresses that need geocoding.
    Does PO Box detection and creates initial geocoding records.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000):
        super().__init__(db_ops, batch_size)
        self.address_dedup = AddressDeduplicationProcessor(db_ops)

    def _get_work_batch(self, offset: int) -> List[Dict[str, Any]]:
        """Get a batch of address_ids that need geocoding records created"""
        # Get addresses that need geocoding (no geocoding_id and not PO Box)
        addresses = self.db_ops.get_addresses_for_geocoding(limit=self.batch_size, offset=offset)

        # Return just the address_ids as work items - we'll fetch full Address objects in processing
        work_items = []
        for address in addresses:
            work_items.append({'address_id': address.address_id})

        return work_items

    def _process_work_batch(self, batch: List[Dict[str, Any]]) -> List[DatabaseOperation]:
        """Process a batch of address_ids into geocoding record creation operations using ThreadPoolExecutor"""
        operations = []

        # Get full Address objects for this batch
        address_ids = [work_item['address_id'] for work_item in batch]
        addresses = self.db_ops.select_dataclass(
            dataclass_type=Address,
            where_clause="address_id IN ({})".format(','.join('?' for _ in address_ids)),
            params=address_ids
        )

        # Debug logging: Log retrieved address data
        if not global_config.is_quiet():
            log_debug(self.logger, f"PHASE 1: Retrieved {len(addresses)} addresses for geocoding record creation")
            for addr in addresses:
                log_debug(self.logger, f"PHASE 1: Retrieved address {addr.address_id}: line1='{addr.address_line1}', line2='{addr.address_line2}', city='{addr.city}', state='{addr.state}', zip='{addr.zip_code}'")

        # Create a lookup dict for quick access
        address_lookup = {addr.address_id: addr for addr in addresses}

        # Process addresses concurrently using ThreadPoolExecutor
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def process_single_address(work_item):
            """Process a single address for geocoding record creation"""
            address_id = work_item['address_id']
            address = address_lookup.get(address_id)

            if not address:
                if not global_config.is_quiet():
                    log_warning(self.logger, f"PHASE 1: Address {address_id} not found in lookup")
                return None

            # Check for PO Box detection
            if address.is_po_box():
                # Create PO Box operation
                if not global_config.is_quiet():
                    log_debug(self.logger, f"PHASE 1: Address {address_id} detected as PO Box: {address.po_box}")
                operation = DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Addresses',
                        'updates': {
                            'po_box': address.po_box,
                            'colocator': f"PO:{address.po_box}:{address.zip_code or ''}"
                        },
                        'where': {'address_id': address_id}
                    }
                )
                return operation
            else:
                # Use factory method to create geocoding operation
                geocoding_op = address.create_geocoding_operation()
                # Add address_id to the operation data so we can update the address later
                geocoding_op.data['address_id'] = address_id
                if not global_config.is_quiet():
                    log_debug(self.logger, f"PHASE 1: Created geocoding operation for address {address_id}: normalized_address='{geocoding_op.data.get('normalized_address', 'N/A')}'")
                return geocoding_op

        # Use ThreadPoolExecutor for concurrent processing
        with ThreadPoolExecutor(max_workers=GEOCODING_FAST_WORKERS) as executor:
            futures = [executor.submit(process_single_address, work_item) for work_item in batch]

            for future in as_completed(futures):
                operation = future.result()
                if operation:
                    operations.append(operation)

        # Create progress update operation for the entire batch
        if operations:
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": len([op for op in operations if op.operation_type != DatabaseOperationType.PROGRESS_UPDATE])}
            )
            operations.append(progress_op)

        if not global_config.is_quiet():
            geocoding_ops = [op for op in operations if op.operation_type == DatabaseOperationType.INSERT_GEOCODING]
            po_box_ops = [op for op in operations if op.operation_type == DatabaseOperationType.GENERIC_UPDATE]
            log_debug(self.logger, f"PHASE 1: Batch processing complete - {len(geocoding_ops)} geocoding operations, {len(po_box_ops)} PO Box operations")

        return operations

    def _detect_po_box_from_address(self, address) -> Optional[str]:
        """Detect PO Box from Address object using regex"""
        # Use the existing is_po_box method from Address class
        return address.is_po_box()


class GeolocationProducerAPI(BaseProducer):
    """
    Producer for API geocoding - Phase 2: Slow processing

    Reads existing Geocoding records and makes census API calls to update them.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000):
        super().__init__(db_ops, batch_size)

    def _get_work_batch(self, offset: int) -> List[Dict[str, Any]]:
        """Get a batch of geocoding records that need API calls"""
        # Get geocoding records that haven't been processed yet
        query = """
            SELECT geocoding_id, normalized_address, attempt_count
            FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status = 'pending'
            ORDER BY geocoding_id
            LIMIT ? OFFSET ?
        """
        result = self.db_ops.execute_query(query, (self.batch_size, offset))
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
                log_debug(self.logger, f"PHASE 2: Retrieved {len(batch_data)} geocoding records for API calls")
                for record in batch_data:
                    log_debug(self.logger, f"PHASE 2: Retrieved geocoding record {record['geocoding_id']} for API call - attempt_count={record['attempt_count']}")

            return batch_data
        return []


    def _process_work_batch(self, batch: List[Dict[str, Any]]) -> List[DatabaseOperation]:
        """Process a batch of geocoding records by making a single batch API call"""
        operations = []

        if not batch:
            return operations

        # Debug logging: Log geocoding records being prepared for API calls
        if not global_config.is_quiet():
            for record in batch:
                log_debug(self.logger, f"Preparing API call for geocoding_id {record['geocoding_id']}: normalized_address='{record['normalized_address']}'")

        try:
            # Make a single batch API call for all records in this batch
            batch_results = self._batch_geocode_addresses(batch)

            # Create operations for each result
            for result in batch_results:
                geocoding_record = result['geocoding_record']
                api_result = result['api_result']

                operation = DatabaseOperation(
                    operation_type=DatabaseOperationType.UPDATE_GEOCODING,
                    data={
                        'geocoding_id': geocoding_record['geocoding_id'],
                        'api_result': api_result,
                        'last_attempt': result['last_attempt'],
                        'attempt_count': result['attempt_count']
                    }
                )
                operations.append(operation)

        except Exception as e:
            # If batch call fails, fall back to individual calls
            if not global_config.is_quiet():
                log_warning(self.logger, f"Batch geocoding failed, falling back to individual calls: {e}")

            for geocoding_record in batch:
                try:
                    api_result = self._single_geocode_address(geocoding_record)
                    operation = DatabaseOperation(
                        operation_type=DatabaseOperationType.UPDATE_GEOCODING,
                        data={
                            'geocoding_id': geocoding_record['geocoding_id'],
                            'api_result': api_result,
                            'last_attempt': datetime.now().isoformat(),
                            'attempt_count': geocoding_record.get('attempt_count', 0) + 1
                        }
                    )
                    operations.append(operation)
                except Exception as inner_e:
                    operation = DatabaseOperation(
                        operation_type=DatabaseOperationType.UPDATE_GEOCODING,
                        data={
                            'geocoding_id': geocoding_record['geocoding_id'],
                            'api_result': {'status': 'failed', 'error': str(inner_e)},
                            'last_attempt': datetime.now().isoformat(),
                            'attempt_count': geocoding_record.get('attempt_count', 0) + 1
                        }
                    )
                    operations.append(operation)

        # Create progress update operation for the batch
        progress_op = DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={"count": len(batch)}
        )
        operations.append(progress_op)

        return operations

    def _single_geocode_address(self, geocoding_record: Dict[str, Any]) -> Dict[str, Any]:
        """Make geocoding API call for a single address using prebuilt JSON"""
        if not cg:
            return {'status': 'failed', 'error': 'censusgeocode not available'}

        try:
            # Use the prebuilt JSON directly from normalized_address field
            census_json = geocoding_record['normalized_address']
            if isinstance(census_json, dict):
                # Set the ID to geocoding_id for easy lookup when results come back
                api_record = census_json.copy()
                api_record['id'] = geocoding_record['geocoding_id']
            else:
                # Parse as JSON (all records should now be proper JSON)
                try:
                    import json
                    api_record = json.loads(census_json)
                    api_record['id'] = geocoding_record['geocoding_id']
                except Exception as json_e:
                    # If JSON parsing fails, create a minimal record
                    if not global_config.is_quiet():
                        log_error(self.logger, f"JSON parsing failed for geocoding_id {geocoding_record['geocoding_id']}: {json_e}")
                    api_record = {
                        'id': geocoding_record['geocoding_id'],
                        'street': '',
                        'city': '',
                        'state': '',
                        'zip': ''
                    }

            # Make single-item batch API call
            batch_addresses = [api_record]
            if not global_config.is_quiet():
                log_debug(self.logger,f"Census API call params {batch_addresses}")
                # Log the actual address data being sent to API
                log_debug(self.logger, f"API call address: id={api_record.get('id')}, street='{api_record.get('street')}', city='{api_record.get('city')}', state='{api_record.get('state')}', zip='{api_record.get('zip')}'")
            api_results = cg.addressbatch(batch_addresses)

            if api_results and len(api_results) > 0:
                if not global_config.is_quiet():
                    log_debug(self.logger,f"Census API call result {api_results}")
                result = api_results[0]
                lat = result.get('lat')
                lon = result.get('lon')
                geocoding_status = result.get('geocoding_status', 'No_Match')

                if geocoding_status == 'Match' and lat is not None and lon is not None and str(lat).strip() and str(lon).strip():
                    # Success
                    return {
                        'status': 'success',
                        'latitude': round(float(lat), 4),
                        'longitude': round(float(lon), 4),
                        'geocoding_status': geocoding_status
                    }
                else:
                    # No match or invalid result
                    return {
                        'status': 'failed',
                        'geocoding_status': geocoding_status
                    }
            else:
                return {
                    'status': 'failed',
                    'error': 'no result from API'
                }

        except Exception as e:
            # API call failed
            log_error(self.logger,f"Census API call failure {str(e)}")
            return {'status': 'failed', 'error': str(e)}



    def _batch_geocode_addresses(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Make a batch geocoding API call for multiple addresses"""
        if not cg:
            return [{'geocoding_record': record, 'api_result': {'status': 'failed', 'error': 'censusgeocode not available'},
                     'last_attempt': datetime.now().isoformat(), 'attempt_count': record.get('attempt_count', 0) + 1}
                    for record in batch]

        try:
            # Prepare batch addresses for API call
            batch_addresses = []
            record_map = {}  # Map API ID to original record

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
            return [{'geocoding_record': record, 'api_result': {'status': 'failed', 'error': str(e)},
                     'last_attempt': datetime.now().isoformat(), 'attempt_count': record.get('attempt_count', 0) + 1}
                    for record in batch]



class GeolocationConsumer(BaseConsumer):
    """
    Consumer for address geocoding - executes geocoding result operations.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers handle API calls and pass results.
    """

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.address_dedup = AddressDeduplicationProcessor(db_ops)

    def _process_operations_batch(self, operations_by_type) -> int:
        """Process operations batch for geocoding consumer"""
        total_updated = 0

        # Handle INSERT_GEOCODING operations (creating geocoding records)
        if DatabaseOperationType.INSERT_GEOCODING.value in operations_by_type:
            geocoding_objects = []
            address_updates = []  # Track which addresses need geocoding_id updates

            for operation in operations_by_type[DatabaseOperationType.INSERT_GEOCODING.value]:
                data = operation.data
                # Create Geocoding object
                geocoding = Geocoding(
                    normalized_address=data['normalized_address'],
                    latitude=data.get('latitude'),
                    longitude=data.get('longitude'),
                    geocoding_status=data.get('status', 'pending')
                )
                geocoding_objects.append(geocoding)

                # Track the address that needs to be updated with this geocoding_id
                if 'address_id' in data:
                    address_updates.append({
                        'address_id': data['address_id'],
                        'geocoding_id': geocoding.geocoding_id  # Will be set after bulk insert
                    })

            # Bulk insert geocoding records
            if geocoding_objects:
                if not global_config.is_quiet():
                    log_debug(self.logger, f"PHASE 1 CONSUMER: About to bulk insert {len(geocoding_objects)} geocoding records")
                    for geo in geocoding_objects:
                        log_debug(self.logger, f"PHASE 1 CONSUMER: Inserting geocoding record - geocoding_id={geo.geocoding_id}, normalized_address='{geo.normalized_address}', status='{geo.geocoding_status}'")

                geocoding_ids = self.db_ops.bulk_insert(geocoding_objects)
                total_updated += len(geocoding_ids)

                # Now update addresses with their geocoding_ids
                for i, geocoding_id in enumerate(geocoding_ids):
                    if i < len(address_updates):
                        address_update = address_updates[i]
                        address_id = address_update['address_id']

                        if not global_config.is_quiet():
                            log_debug(self.logger, f"PHASE 1 CONSUMER: Updating address {address_id} with geocoding_id {geocoding_id}")

                        # Update the address with the geocoding_id
                        self.db_ops.execute_query("""
                            UPDATE Addresses SET geocoding_id = ? WHERE address_id = ?
                        """, (str(geocoding_id), address_id))

                        total_updated += 1

                if not global_config.is_quiet():
                    log_debug(self.logger, f"PHASE 1 CONSUMER: Bulk insert completed - inserted {len(geocoding_ids)} geocoding records with IDs: {geocoding_ids[:5]}{'...' if len(geocoding_ids) > 5 else ''}")
                    # Commit confirmation logging
                    log_debug(self.logger, f"PHASE 1 CONSUMER: Committing geocoding record insertions and address updates...")
                    self.db_ops.commit()
                    log_debug(self.logger, f"PHASE 1 CONSUMER: Geocoding record insertions and address updates committed successfully")

        # Handle UPDATE_GEOCODING operations (updating geocoding records with API results)
        if DatabaseOperationType.UPDATE_GEOCODING.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.UPDATE_GEOCODING.value]:
                data = operation.data
                geocoding_id = data['geocoding_id']
                api_result = data['api_result']

                if not global_config.is_quiet():
                    log_debug(self.logger, f"PHASE 2 CONSUMER: Processing UPDATE_GEOCODING for geocoding_id {geocoding_id} - status: {api_result['status']}")

                # Update geocoding record with API results
                if api_result['status'] == 'success':
                    if not global_config.is_quiet():
                        log_debug(self.logger, f"PHASE 2 CONSUMER: Updating geocoding_id {geocoding_id} with SUCCESS - lat={api_result['latitude']}, lon={api_result['longitude']}, status={api_result['geocoding_status']}")

                    self.db_ops.execute_query("""
                        UPDATE Geocoding SET
                            latitude = ?, longitude = ?, geocoding_status = ?,
                            last_attempt = ?, attempt_count = ?
                        WHERE geocoding_id = ?
                    """, (
                        api_result['latitude'], api_result['longitude'], api_result['geocoding_status'],
                        data['last_attempt'], data['attempt_count'], geocoding_id
                    ))

                    # Update all addresses that reference this geocoding record
                    colocator = f"LL:{api_result['latitude']}:{api_result['longitude']}"
                    if not global_config.is_quiet():
                        log_debug(self.logger, f"PHASE 2 CONSUMER: Updating addresses with geocoding_id {geocoding_id} to colocator '{colocator}'")
                    address_id, master_id = self._update_addresses_with_geocoding(geocoding_id, colocator)

                    # Create operations to update owner colocator
                    self._create_owner_colocator_operations_from_geocoding(geocoding_id, colocator, address_id, master_id)
                else:
                    # Update with failed status
                    if not global_config.is_quiet():
                        log_debug(self.logger, f"PHASE 2 CONSUMER: Updating geocoding_id {geocoding_id} with FAILED - status={api_result.get('geocoding_status', 'failed')}")

                    self.db_ops.execute_query("""
                        UPDATE Geocoding SET
                            geocoding_status = ?,
                            last_attempt = ?, attempt_count = ?
                        WHERE geocoding_id = ?
                    """, (
                        api_result.get('geocoding_status', 'failed'),
                        data['last_attempt'], data['attempt_count'], geocoding_id
                    ))

                total_updated += 1

            if not global_config.is_quiet():
                log_debug(self.logger, f"PHASE 2 CONSUMER: Processed {len(operations_by_type[DatabaseOperationType.UPDATE_GEOCODING.value])} UPDATE_GEOCODING operations")
                # Commit confirmation logging
                log_debug(self.logger, f"PHASE 2 CONSUMER: Committing geocoding API result updates...")
                self.db_ops.commit()
                log_debug(self.logger, f"PHASE 2 CONSUMER: Geocoding API result updates committed successfully")

        # Handle GENERIC_UPDATE operations (PO Box updates)
        if DatabaseOperationType.GENERIC_UPDATE.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.GENERIC_UPDATE.value]:
                data = operation.data

                if data.get('table') == 'Addresses':
                    # PO Box update
                    updates = data['updates']
                    where = data['where']
                    self.db_ops.execute_query(f"""
                        UPDATE Addresses SET {', '.join(f'{k} = ?' for k in updates.keys())}
                        WHERE {', '.join(f'{k} = ?' for k in where.keys())}
                    """, list(updates.values()) + list(where.values()))

                    # Update owner with colocator if present
                    if 'colocator' in updates and where.get('address_id'):
                        self._update_owner_colocator(where['address_id'], updates['colocator'])

                    total_updated += 1

        return total_updated

    def _update_addresses_with_geocoding(self, geocoding_id: str, colocator: str):
        """Update all addresses that reference this geocoding record"""
        # Get all addresses with this geocoding_id
        addresses = self.db_ops.select_dataclass(
            dataclass_type=Address,
            where_clause="geocoding_id = ?",
            params=(geocoding_id,)
        )

        if not global_config.is_quiet():
            log_debug(self.logger, f"PHASE 2 CONSUMER: Found {len(addresses)} addresses referencing geocoding_id {geocoding_id}")

        # Get the first address to return its IDs for colocation operations
        first_address = addresses[0] if addresses else None

        for address in addresses:
            if not global_config.is_quiet():
                log_debug(self.logger, f"PHASE 2 CONSUMER: Updating address {address.address_id} with colocator '{colocator}'")
            # Update address with colocator only (geocoding_id is already set)
            self.db_ops.update_address_geocoding(address.address_id, None, colocator)

        # Return the address_id and master_id for colocation operations
        if first_address:
            return first_address.address_id, first_address.master_id
        return None, None

    def _create_owner_colocator_operations_from_geocoding(self, geocoding_id: str, colocator: str, address_id: str, master_id: str = None):
        """Create operations to update owner colocator from geocoding success - called from consumer"""
        # Use master_id if it exists (this address is a child), otherwise use address_id (this is the master)
        update_id = master_id if master_id else address_id

        # Update Charities - all charities that have this address as their master or child
        self.db_ops.execute_query("""
            UPDATE Charities SET colocator = ? WHERE charity_id IN (
                SELECT owner_id FROM Addresses
                WHERE (address_id = ? OR master_id = ?) AND address_type = 'charity' AND owner_id IS NOT NULL
            )
        """, (colocator, update_id, update_id))

        # Update Grants - all grants that have this address as their master or child
        self.db_ops.execute_query("""
            UPDATE Grants SET colocator = ? WHERE grant_id IN (
                SELECT owner_id FROM Addresses
                WHERE (address_id = ? OR master_id = ?) AND address_type = 'grant' AND owner_id IS NOT NULL
            )
        """, (colocator, update_id, update_id))

        # Update Contractors - all contractors that have this address as their master or child
        self.db_ops.execute_query("""
            UPDATE Contractors SET colocator = ? WHERE contractor_id IN (
                SELECT owner_id FROM Addresses
                WHERE (address_id = ? OR master_id = ?) AND address_type = 'contractor' AND owner_id IS NOT NULL
            )
        """, (colocator, update_id, update_id))

        # Update PoliticalContributions - all political contributions that have this address as their master or child
        self.db_ops.execute_query("""
            UPDATE PoliticalContributions SET colocator = ? WHERE political_id IN (
                SELECT owner_id FROM Addresses
                WHERE (address_id = ? OR master_id = ?) AND address_type = 'politicalcontribution' AND owner_id IS NOT NULL
            )
        """, (colocator, update_id, update_id))

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not global_config.is_quiet():
            log_info(self.logger, msg, *args, ein=ein)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not global_config.is_quiet():
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, *args, ein=ein, exc_info=exc_info)


def geolocate_addresses(db_ops: DatabaseOperations, progress_bar=None) -> int:
    """
    Geolocate addresses using ThreadPoolManager with Producer-Consumer pattern.

    Args:
        db_ops: Database operations instance
        progress_bar: Optional progress bar to update

    Returns:
        Number of addresses processed
    """
    logger = get_logger("geolocation")

    # Check if censusgeocode is available
    if cg is None:
        if not global_config.is_quiet():
            log_warning(logger, "censusgeocode library not available. Skipping geocoding. "
                            "To enable geocoding, install with: pip install censusgeocode")
        return 0

    try:
        if not global_config.is_quiet():
            log_info(logger, f"Starting geocoding with batch_size={GEOCODING_BATCH_SIZE}, api_batch_size={GEOCODING_API_BATCH_SIZE}, fast_workers={GEOCODING_FAST_WORKERS}, api_workers={GEOCODING_API_WORKERS}")

        # Create GeolocationProcessorThreaded instance to use ThreadPoolManager
        processor = GeolocationProcessorThreaded(db_ops)

        # Use the threaded implementation which uses ThreadPoolManager
        return processor.geolocate_addresses_threaded(progress_bar)

    except Exception as e:
        log_error(logger, f"Address geocoding failed: {e}", exc_info=True)
        return 0




def _estimate_total_geocoding_work(db_ops: DatabaseOperations) -> int:
    """Estimate total geocoding work for progress bar"""
    try:
        # Count distinct canonical addresses that need geocoding records created
        address_query = """
            SELECT COUNT(DISTINCT canonical_address) FROM Addresses
            WHERE master_id IS NULL
                AND (colocator IS NULL OR colocator = '')
                AND canonical_address IS NOT NULL
                AND canonical_address != ''
        """
        address_result = db_ops.execute_query(address_query)
        address_count = address_result.fetchone()[0] if address_result else 0

        # Count geocoding records that need API calls
        geocoding_query = """
            SELECT COUNT(*) FROM Geocoding
            WHERE geocoding_status = 'pending' OR geocoding_status IS NULL
        """
        geocoding_result = db_ops.execute_query(geocoding_query)
        geocoding_count = geocoding_result.fetchone()[0] if geocoding_result else 0

        total_work = address_count + geocoding_count
        return total_work
    except Exception:
        # Fallback to a reasonable estimate
        return 10000



class GeolocationProcessor:
    """
    Main processor for address geocoding using Producer-Consumer pattern.

    This processor coordinates producers and consumers to safely geocode
    addresses in batches, following the same pattern as XMLProcessor.
    """

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops
        self.logger = get_logger("geolocation")

        # Create producer and consumer instances
        self.producer_gc = GeolocationProducerGC(db_ops, GEOCODING_BATCH_SIZE)
        self.producer_api = GeolocationProducerAPI(db_ops, GEOCODING_API_BATCH_SIZE)
        self.consumer = GeolocationConsumer(db_ops)


class GeolocationProcessorThreaded(GeolocationProcessor):
    """
    Threaded version of GeolocationProcessor using generalized ThreadPoolManager.

    Uses split thread pools:
    - Fast pool for geocoding record generation (GEOCODING_FAST_WORKERS)
    - Slow pool for API calls (GEOCODING_API_WORKERS)
    - Single-threaded database writing maintained for safety
    """

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)

        # Configure thread pools for split processing
        fast_pool_config = PoolConfig(
            max_workers=GEOCODING_FAST_WORKERS,
            queue_size=1000,
            batch_size=GEOCODING_BATCH_SIZE
        )
        slow_pool_config = PoolConfig(
            max_workers=GEOCODING_API_WORKERS,
            queue_size=1000,
            batch_size=GEOCODING_API_BATCH_SIZE
        )
        consumer_pool_config = PoolConfig(max_workers=1)  # Single-threaded DB writes

        self.thread_pool_config = ThreadPoolConfig(
            producer_config=fast_pool_config,  # Default for general use
            consumer_config=consumer_pool_config
        )

        # Separate configs for different phases
        self.fast_thread_config = ThreadPoolConfig(
            producer_config=fast_pool_config,
            consumer_config=consumer_pool_config
        )
        self.slow_thread_config = ThreadPoolConfig(
            producer_config=slow_pool_config,
            consumer_config=consumer_pool_config
        )

    def geolocate_addresses(self, progress_bar=None) -> int:
        """
        Geolocate addresses using two-phase Producer-Consumer pattern.

        Phase 1: Create geocoding records for addresses needing geocoding
        Phase 2: Make API calls to update geocoding records

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of addresses processed
        """
        log_info(self.logger, "Starting address geocoding with two-phase Producer-Consumer pattern")

        # Check if censusgeocode is available
        if cg is None:
            if not global_config.is_quiet():
                log_warning(self.logger, "censusgeocode library not available. Skipping geocoding. "
                                "To enable geocoding, install with: pip install censusgeocode")
            return 0

        try:
            # Phase 1: Create geocoding records
            phase1_count = self._run_geocoding_phase(self.producer_gc, "Phase 1: Creating geocoding records")
            if not global_config.is_quiet():
                log_info(self.logger, f"Phase 1 completed: created geocoding records for {phase1_count} addresses")

            # Phase 2: Make API calls to update geocoding records
            phase2_count = self._run_geocoding_phase(self.producer_api, "Phase 2: Making API calls")
            if not global_config.is_quiet():
                log_info(self.logger, f"Phase 2 completed: made API calls for {phase2_count} geocoding records")

            return phase1_count + phase2_count

        except Exception as e:
            log_error(self.logger, f"Address geocoding failed: {e}", exc_info=True)
            return 0

    def geolocate_addresses_threaded(self, progress_bar=None) -> int:
        """
        Geolocate addresses using ThreadPoolManager with split thread pools.

        Phase 1: Fast thread pool for geocoding record generation
        Phase 2: Slow thread pool for API calls
        Database writing remains single-threaded for safety

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of addresses processed
        """
        log_info(self.logger, "Starting threaded address geocoding with split thread pools")

        # Check if censusgeocode is available
        if cg is None:
            if not global_config.is_quiet():
                log_warning(self.logger, "censusgeocode library not available. Skipping geocoding. "
                                "To enable geocoding, install with: pip install censusgeocode")
            return 0

        try:
            # Phase 1: Create geocoding records using fast thread pool
            phase1_count = self._run_geocoding_phase_threaded(
                self.producer_gc,
                "Phase 1: Creating geocoding records (threaded)",
                self.fast_thread_config
            )
            if not global_config.is_quiet():
                log_info(self.logger, f"Phase 1 completed: created geocoding records for {phase1_count} addresses")

            # Phase 2: Make API calls using slow thread pool
            phase2_count = self._run_geocoding_phase_threaded(
                self.producer_api,
                "Phase 2: Making API calls (threaded)",
                self.slow_thread_config
            )
            if not global_config.is_quiet():
                log_info(self.logger, f"Phase 2 completed: made API calls for {phase2_count} geocoding records")

            return phase1_count + phase2_count

        except Exception as e:
            log_error(self.logger, f"Threaded address geocoding failed: {e}", exc_info=True)
            return 0

    def _run_geocoding_phase(self, producer, phase_name: str) -> int:
        """Run a single geocoding phase using producer-consumer pattern"""
        try:
            # Use producer-consumer pattern with threading
            import queue
            import threading

            # Create shared condition for producer-consumer synchronization
            batch_condition = threading.Condition()

            # Create queues for producer-consumer communication
            operation_queue = queue.Queue(maxsize=1000)  # Queue for DatabaseOperation objects

            # Start consumer thread
            consumer_thread = threading.Thread(
                target=_consumer_worker,
                args=(operation_queue, None, batch_condition, self.consumer, self.logger)
            )
            consumer_thread.daemon = True
            consumer_thread.start()

            # Producer: collect operations and send to consumer
            producer_thread = threading.Thread(
                target=_producer_worker,
                args=(operation_queue, batch_condition, producer, self.logger)
            )
            producer_thread.daemon = True
            producer_thread.start()

            # Wait for producer to finish
            producer_thread.join()

            # Signal consumer to finish and wait
            operation_queue.put(None)  # Sentinel value
            consumer_thread.join(timeout=30.0)

            if consumer_thread.is_alive():
                log_error(self.logger, f"Consumer thread did not finish within timeout for {phase_name}")
                return 0

            # Return the total updated count from consumer
            return getattr(consumer_thread, '_total_updated', 0)

        except Exception as e:
            log_error(self.logger, f"Geocoding phase failed ({phase_name}): {e}", exc_info=True)
            return 0

    def _run_geocoding_phase_threaded(self, producer, phase_name: str, thread_config: ThreadPoolConfig) -> int:
        """Run a single geocoding phase using ThreadPoolManager"""
        try:
            # Initialize thread pool manager with appropriate config
            thread_pool_manager = ThreadPoolManager(thread_config, self.logger)

            total_processed = 0

            try:
                # Get all work items for this phase
                all_work_items = []
                offset = 0
                while True:
                    batch = producer._get_work_batch(offset)
                    if not batch:
                        break
                    all_work_items.extend(batch)

                    # Check global limit
                    if global_config.max_files and len(all_work_items) >= global_config.max_files:
                        all_work_items = all_work_items[:global_config.max_files]
                        break

                    offset += producer.batch_size

                if not all_work_items:
                    return 0

                # Start producer pool to process work items
                thread_pool_manager.start_producer_pool(
                    all_work_items,
                    self._threaded_work_processor,
                    producer
                )

                # Start consumer pool (single consumer for DB safety)
                thread_pool_manager.start_consumer_pool(
                    self._threaded_consumer_processor,
                    self.consumer
                )

                # Wait for completion
                thread_pool_manager.wait_for_completion()

                # Collect results
                while not thread_pool_manager.result_queue.empty():
                    try:
                        result = thread_pool_manager.result_queue.get_nowait()
                        if isinstance(result, int):
                            total_processed += result
                        thread_pool_manager.result_queue.task_done()
                    except queue.Empty:
                        break

            finally:
                thread_pool_manager.shutdown()

            if not global_config.is_quiet():
                log_info(self.logger, f"Threaded phase {phase_name} completed: processed {total_processed} items")
            return total_processed

        except Exception as e:
            log_error(self.logger, f"Threaded geocoding phase failed ({phase_name}): {e}", exc_info=True)
            return 0

    def _threaded_work_processor(self, work_items: List[Any], work_queue: queue.Queue,
                                result_queue: queue.Queue, thread_id: int, num_threads: int,
                                producer) -> None:
        """Process work items using ThreadPoolManager"""
        try:
            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                work_item = work_items[i]

                # Process single work item (wrap in list for batch processing)
                batch_operations = producer._process_work_batch([work_item])

                # Put operations in result queue for consumer
                result_queue.put(batch_operations)

        except Exception as e:
            log_error(self.logger, f"Threaded work processor {thread_id} error: {e}", exc_info=True)

    def _threaded_consumer_processor(self, result_queue: queue.Queue, thread_id: int,
                                    consumer) -> None:
        """Process operations using ThreadPoolManager"""
        try:
            batch_operations = []
            batch_size = 50  # Process in reasonable batches

            while True:
                try:
                    operations = result_queue.get(timeout=1.0)
                    if operations is None:  # Sentinel
                        break

                    if isinstance(operations, list):
                        batch_operations.extend(operations)

                        if len(batch_operations) >= batch_size:
                            # Process batch
                            processed = consumer.execute_operations_batch(batch_operations)
                            result_queue.put(processed)  # Put count back for collection
                            batch_operations = []

                    result_queue.task_done()

                except queue.Empty:
                    continue

            # Process remaining operations
            if batch_operations:
                processed = consumer.execute_operations_batch(batch_operations)
                result_queue.put(processed)

        except Exception as e:
            log_error(self.logger, f"Threaded consumer processor {thread_id} error: {e}", exc_info=True)
