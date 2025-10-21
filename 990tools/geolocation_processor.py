#!/usr/bin/env python3
"""
geolocation_processor.py - Address geocoding for IRS 990 data

This module handles geocoding of addresses using the census API,
storing latitude/longitude coordinates for address matching.
"""

import logging
from typing import List, Tuple, Optional
from tqdm import tqdm

try:
    import censusgeocode as cg
except ImportError:
    cg = None
    # logger.warning("censusgeocode library not found. Geocoding will be skipped. "
    #               "To enable geocoding, install with: pip install censusgeocode")

from database_operations import DatabaseOperations
from models import Address
from constants import VALID_STATES, STATE_NAME_TO_ABBREV, PO_BOX_REGEX, PO_BOX_NUMBER_REGEX

# Set up logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GeolocationProcessor:
    """Handles address geocoding operations"""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

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
                    logger.info(f"Fallback PO Box detection for address {address.address_id}: found '{po_box}' in line{line_idx + 1}='{line}'")
                    return po_box
        return None


    def geolocate_addresses(self) -> int:
        """Geolocate addresses using census API (step 7)"""
        logger.info("Starting address geolocation")

        # Check if censusgeocode is available
        if cg is None:
            logger.warning("censusgeocode library not available. Skipping geocoding. "
                           "To enable geocoding, install with: pip install censusgeocode")
            return 0

        # Process in batches of 5000 with progress bar (under 10,000 API limit)
        batch_size = 5000
        total_geolocated = 0

        logger.info(f"Starting batch processing with batch_size={batch_size}")
        with tqdm(desc="Geocoding addresses") as pbar:
            while True:
                # Get next batch of addresses
                logger.info(f"DEBUG geolocate_addresses: Calling get_addresses_for_geocoding with limit={batch_size}")
                batch_addresses = self.db_ops.get_addresses_for_geocoding(limit=batch_size)
                if not batch_addresses:
                    logger.info("No more addresses to process, ending batch processing")
                    break

                logger.info(f"Processing batch with {len(batch_addresses)} addresses")

                # Process all addresses - _geolocate_batch will handle canonical address grouping
                if batch_addresses:
                    logger.info(f"DEBUG: Batch sample addresses: {[addr.canonical_address for addr in batch_addresses[:3]]}")
                    batch_geolocated = self._geolocate_batch(batch_addresses)
                    logger.info(f"Batch geolocated {batch_geolocated} addresses")
                    total_geolocated += batch_geolocated

                pbar.update(len(batch_addresses))

        logger.info(f"Geolocation complete: {total_geolocated} addresses geolocated")
        return total_geolocated

    def _geolocate_batch(self, batch: List[Address]) -> int:
        """Geolocate a batch of addresses"""
        logger.debug(f"Processing batch of {len(batch)} addresses")
        logger.info(f"DEBUG: Batch input addresses: {len(batch)}")
        addresses_to_geocode = []
        address_id_lists = []  # list of lists of address_ids, parallel to addresses_to_geocode
        full_address_to_index = {}  # full_address -> index in addresses_to_geocode

        parsed_count = 0
        skipped_po_box = 0
        failed_parse = 0

        for idx, address in enumerate(batch):
            address_id = address.address_id
            canonical_address = address.canonical_address
            logger.info(f"Processing address_id {address_id}: line1='{address.address_line1}', line2='{address.address_line2}', city='{address.city}', state='{address.state}', zip='{address.zip_code}', po_box='{address.po_box}'")

            # Check for PO Box detection during geocoding
            fallback_po_box = self._detect_po_box_from_address_lines(address)

            if fallback_po_box:
                # Update the record with detected PO Box and set colocator
                logger.info(f"Updating address {address_id} with fallback PO Box detection: po_box='{fallback_po_box}'")
                colocator = f"PO:{fallback_po_box}:{address.zip_code or ''}"
                self.db_ops.update_address_po_box_and_colocator(address_id, fallback_po_box, colocator)
                logger.info(f"Updated address {address_id} with po_box='{fallback_po_box}' and colocator='{colocator}'")
                logger.info(f"Skipping PO box address {address_id} - newly detected")
                skipped_po_box += 1
                continue
            logger.info(f"Canonical address for {address_id}: '{canonical_address}'")

            if canonical_address and canonical_address.strip():
                # Use the smart canonical_address property
                full_address = canonical_address
                logger.info(f"Using canonical address for {address_id}: '{full_address}'")

                # Group addresses by full_address for batch geocoding
                if full_address not in full_address_to_index:
                    full_address_to_index[full_address] = len(addresses_to_geocode)
                    addresses_to_geocode.append({
                        'full_address': full_address,
                        'street': f"{address.address_line1 or ''} {address.address_line2 or ''}".strip(),
                        'city': address.city or '',
                        'state': address.state or '',
                        'zip': address.zip_code or ''
                    })
                    address_id_lists.append([address_id])
                else:
                    addr_idx = full_address_to_index[full_address]
                    address_id_lists[addr_idx].append(address_id)
                parsed_count += 1
            else:
                logger.warning(f"Failed to generate canonical address for {address_id}: components line1='{address.address_line1}', city='{address.city}', state='{address.state}', zip='{address.zip_code}'")
                failed_parse += 1

        logger.info(f"Batch parsing summary: {parsed_count} parsed, {skipped_po_box} PO boxes skipped (from DB), {failed_parse} failed to parse")
        logger.info(f"DEBUG: Total addresses in batch: {len(batch)}, processed: {parsed_count + skipped_po_box + failed_parse}")
        logger.info(f"DEBUG: Addresses that passed PO box check: {parsed_count + failed_parse}")
        logger.info(f"DEBUG: Addresses that failed PO box check: {skipped_po_box}")
        logger.info(f"DEBUG: Addresses to send to API: {len(addresses_to_geocode)}")
        if addresses_to_geocode:
            logger.info(f"DEBUG: Sample addresses to API: {addresses_to_geocode[:2]}")

        if not addresses_to_geocode:
            logger.warning("No addresses to geocode in this batch after parsing - all addresses were PO boxes or failed parsing")
            logger.warning(f"DEBUG: Batch breakdown - PO boxes: {skipped_po_box}, failed parse: {failed_parse}, total batch: {len(batch)}")
            return 0

        # Call census geocoding API
        try:
            # Prepare batch for censusgeocode
            batch_addresses = []
            for i, addr in enumerate(addresses_to_geocode):
                batch_addresses.append({
                    'id': i,
                    'street': addr['street'],
                    'city': addr['city'],
                    'state': addr['state'],
                    'zip': addr['zip']
                })

            logger.info(f"Sending {len(batch_addresses)} addresses to census geocoding API")
            logger.debug(f"API request addresses: {['{}, {}, {} {}'.format(addr['street'], addr['city'], addr['state'], addr['zip']) for addr in batch_addresses]}")

            # Geocode batch
            if cg is None:
                logger.error("censusgeocode library not available, skipping geocoding. "
                            "To install, run: pip install censusgeocode")
                return 0

            logger.info("Calling censusgeocode.addressbatch()...")
            results = cg.addressbatch(batch_addresses)
            logger.info(f"API call completed, received {len(results)} results")
            logger.info(f"DEBUG: First few API results: {results[:3] if results else 'No results'}")
            logger.debug(f"API response received: {results}")
            geolocated_count = 0

            for result in results:
                idx_str = result.get('id')
                logger.debug(f"Processing API result with id: {idx_str}")

                # Convert string id to integer index
                try:
                    idx = int(idx_str) if idx_str is not None else None
                except (ValueError, TypeError):
                    logger.warning(f"Invalid 'id' format in result: {result}")
                    continue

                if idx is None or idx < 0 or idx >= len(addresses_to_geocode):
                    logger.warning(f"Invalid or out-of-range 'id' in result: {result}")
                    continue

                addr_info = addresses_to_geocode[idx]
                full_address = addr_info['full_address']
                logger.debug(f"Processing API result for address: '{full_address}' (id: {idx})")

                address_ids = address_id_lists[idx]
                logger.debug(f"Address '{full_address}' maps to {len(address_ids)} address IDs: {address_ids}")

                lat = result.get('lat')
                lon = result.get('lon')
                logger.debug(f"Raw lat/lon for '{full_address}': lat={lat}, lon={lon}")

                if lat is not None and lon is not None and str(lat).strip() and str(lon).strip():
                    # Success - round to nearest 10 meters (~0.0001 degrees)
                    try:
                        lat = round(float(lat), 4)
                        lon = round(float(lon), 4)
                        colocator = f"LL:{lat}:{lon}"
                        logger.info(f"Successfully geocoded '{full_address}' -> {colocator}")

                        # Insert geocoding record
                        geocoding_id = self.db_ops.insert_geocoding_record(
                            hash(full_address), full_address, lat, lon, 'success'
                        )
                        logger.debug(f"Inserted geocoding record ID {geocoding_id} for '{full_address}'")

                        # Update all addresses with this canonical address
                        for address_id in address_ids:
                            self.db_ops.update_address_geocoding(
                                address_id, geocoding_id, colocator
                            )
                            logger.debug(f"Updated address ID {address_id} with geocoding")
                        geolocated_count += len(address_ids)
                    except (ValueError, TypeError) as e:
                        logger.error(f"Failed to parse lat/lon for '{full_address}': lat={result.get('lat')}, lon={result.get('lon')}, error: {e}")
                        # Failed
                        geocoding_id = self.db_ops.insert_geocoding_record(
                            hash(full_address), full_address, status='parse_error'
                        )
                        for address_id in address_ids:
                            self.db_ops.update_address_geocoding(address_id, geocoding_id)
                else:
                    logger.warning(f"Geocoding failed for '{full_address}': missing or empty lat/lon")
                    # Failed
                    geocoding_id = self.db_ops.insert_geocoding_record(
                        hash(full_address), full_address, status='failed'
                    )
                    logger.debug(f"Inserted failed geocoding record ID {geocoding_id} for '{full_address}'")

                    # Update all addresses with failed geocoding
                    for address_id in address_ids:
                        self.db_ops.update_address_geocoding(address_id, geocoding_id)
                        logger.debug(f"Updated address ID {address_id} with failed geocoding")

            logger.info(f"Geolocated batch of {len(addresses_to_geocode)} unique addresses, {geolocated_count} total addresses updated")
            return geolocated_count

        except Exception as e:
            logger.error(f"Failed to geolocate batch: {e}", exc_info=True)
            return 0