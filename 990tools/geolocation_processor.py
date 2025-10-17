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

from database_operations import DatabaseOperations
# Address is imported from database_operations

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GeolocationProcessor:
    """Handles address geocoding operations"""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

    def geolocate_addresses(self) -> int:
        """Geolocate addresses using census API (step 7)"""
        logger.info("Starting address geolocation")

        # Get addresses that need geocoding, deduplicated by canonical address
        addresses = self.db_ops.get_addresses_for_geocoding()
        logger.info(f"Found {len(addresses)} addresses to geolocate")

        # Deduplicate by canonical address to avoid redundant API calls
        unique_addresses = {}
        for addr in addresses:
            canonical = addr[1]  # canonical_address
            if canonical not in unique_addresses:
                unique_addresses[canonical] = addr

        deduplicated_addresses = list(unique_addresses.values())
        logger.info(f"After deduplication: {len(deduplicated_addresses)} unique addresses to geolocate")

        # Process in batches of 5000 with progress bar
        batch_size = 5000
        total_geolocated = 0

        with tqdm(total=len(deduplicated_addresses), desc="Geocoding addresses") as pbar:
            for i in range(0, len(deduplicated_addresses), batch_size):
                batch = deduplicated_addresses[i:i + batch_size]
                batch_geolocated = self._geolocate_batch(batch)
                total_geolocated += batch_geolocated
                pbar.update(len(batch))

        logger.info(f"Geolocation complete: {total_geolocated} addresses geolocated")
        return total_geolocated

    def _geolocate_batch(self, batch: List[Tuple]) -> int:
        """Geolocate a batch of addresses"""
        logger.debug(f"Processing batch of {len(batch)} addresses")
        addresses_to_geocode = []
        address_mappings = {}  # canonical_address -> list of address_ids

        for idx, (address_id, canonical_address, po_box, zip_code) in enumerate(batch):
            logger.debug(f"Processing address_id {address_id}: canonical='{canonical_address}', po_box='{po_box}', zip='{zip_code}'")

            # Skip if PO box
            if po_box and po_box.strip():
                colocator = f"PO:{po_box.strip()}:{zip_code or ''}"
                logger.debug(f"Skipping PO box address {address_id}, setting colocator='{colocator}'")
                self.db_ops.update_address_geocoding(address_id, colocator=colocator)
                continue

            # Prepare address for geocoding
            # Parse canonical_address back to components
            # Handle both comma-separated format and single-string format
            address_parts = canonical_address.split(', ')
            logger.debug(f"Address parts for {address_id}: {address_parts}")

            if len(address_parts) >= 3:
                # Standard comma-separated format: "street, city, state zip"
                street = address_parts[0]
                city = address_parts[1]
                state_zip = address_parts[2].split(' ')
                if len(state_zip) >= 2:
                    state = state_zip[0]
                    zip_part = state_zip[1]
                    full_address = f"{street}, {city}, {state} {zip_part}"
                    logger.debug(f"Parsed comma-separated address for {address_id}: '{full_address}'")

                    # Group addresses by canonical address for batch geocoding
                    if full_address not in address_mappings:
                        address_mappings[full_address] = []
                        addresses_to_geocode.append({
                            'full_address': full_address,
                            'street': street,
                            'city': city,
                            'state': state,
                            'zip': zip_part
                        })
                    address_mappings[full_address].append(address_id)
                else:
                    logger.warning(f"Invalid state/zip format for address {address_id}: '{address_parts[2]}' (parts: {state_zip}) - canonical: '{canonical_address}'")
            elif len(address_parts) == 1:
                # Single-string format: try to parse as "street city state zip"
                single_string = canonical_address.strip()
                # Split by spaces and try to identify components
                words = single_string.split()
                if len(words) >= 4:
                    # Assume last word is ZIP, second to last is state, third to last is city, rest is street
                    zip_part = words[-1]
                    state = words[-2]
                    city = words[-3]
                    street = ' '.join(words[:-3])

                    # Check if state looks valid (2 letters, uppercase)
                    valid_states = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW', 'AA', 'AE', 'AP'}

                    if len(state) == 2 and state.isupper() and state in valid_states:
                        # Check if zip looks valid (5 digits)
                        if zip_part.isdigit() and len(zip_part) == 5:
                            full_address = f"{street}, {city}, {state} {zip_part}"
                            logger.debug(f"Parsed single-string address for {address_id}: '{full_address}'")

                            # Group addresses by canonical address for batch geocoding
                            if full_address not in address_mappings:
                                address_mappings[full_address] = []
                                addresses_to_geocode.append({
                                    'full_address': full_address,
                                    'street': street,
                                    'city': city,
                                    'state': state,
                                    'zip': zip_part
                                })
                            address_mappings[full_address].append(address_id)
                        else:
                            logger.warning(f"Invalid ZIP format in single-string address {address_id}: '{zip_part}' - canonical: '{canonical_address}'")
                    else:
                        logger.warning(f"Invalid state format in single-string address {address_id}: '{state}' - canonical: '{canonical_address}'")
                else:
                    logger.warning(f"Single-string address too short for parsing {address_id}: '{canonical_address}' (word count: {len(words)})")
            else:
                logger.warning(f"Invalid canonical address format for address {address_id}: '{canonical_address}' (parts count: {len(address_parts)}, parts: {address_parts})")

        if not addresses_to_geocode:
            logger.debug("No addresses to geocode in this batch")
            return 0

        # Call census geocoding API
        try:
            # Prepare batch for censusgeocode
            batch_addresses = []
            for addr in addresses_to_geocode:
                batch_addresses.append({
                    'address': addr['full_address']
                })

            logger.info(f"Sending {len(batch_addresses)} addresses to census geocoding API")
            logger.debug(f"API request addresses: {[addr['address'] for addr in batch_addresses]}")

            # Geocode batch
            if cg is None:
                logger.error("censusgeocode library not available, skipping geocoding")
                return 0

            results = cg.addressbatch(batch_addresses)
            logger.debug(f"API response received: {results}")
            geolocated_count = 0

            for result in results:
                full_address = result.get('address')
                logger.debug(f"Processing API result for address: '{full_address}'")

                if not full_address or full_address not in address_mappings:
                    logger.warning(f"Address '{full_address}' not found in mappings or empty")
                    continue

                address_ids = address_mappings[full_address]
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
                                address_id, geocoding_id, lat, lon, colocator
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