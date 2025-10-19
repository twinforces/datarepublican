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
# Address is imported from database_operations

# Set up logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GeolocationProcessor:
    """Handles address geocoding operations"""

    # Valid US state and territory abbreviations
    VALID_STATES = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW', 'AA', 'AE', 'AP'}

    # State full name to abbreviation mapping
    STATE_NAME_TO_ABBREV = {
        'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR', 'CALIFORNIA': 'CA',
        'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE', 'FLORIDA': 'FL', 'GEORGIA': 'GA',
        'HAWAII': 'HI', 'IDAHO': 'ID', 'ILLINOIS': 'IL', 'INDIANA': 'IN', 'IOWA': 'IA',
        'KANSAS': 'KS', 'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME', 'MARYLAND': 'MD',
        'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN', 'MISSISSIPPI': 'MS', 'MISSOURI': 'MO',
        'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV', 'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ',
        'NEW MEXICO': 'NM', 'NEW YORK': 'NY', 'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH',
        'OKLAHOMA': 'OK', 'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC',
        'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT', 'VERMONT': 'VT',
        'VIRGINIA': 'VA', 'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV', 'WISCONSIN': 'WI', 'WYOMING': 'WY',
        'DISTRICT OF COLUMBIA': 'DC', 'PUERTO RICO': 'PR', 'VIRGIN ISLANDS': 'VI', 'GUAM': 'GU',
        'AMERICAN SAMOA': 'AS', 'NORTHERN MARIANA ISLANDS': 'MP', 'FEDERATED STATES OF MICRONESIA': 'FM',
        'MARSHALL ISLANDS': 'MH', 'PALAU': 'PW', 'ARMED FORCES AMERICAS': 'AA', 'ARMED FORCES EUROPE': 'AE',
        'ARMED FORCES PACIFIC': 'AP'
    }

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

    def _parse_address_components(self, canonical_address: str, db_zip: str) -> Optional[dict]:
        """Parse address components from canonical address string with robust handling"""
        if not canonical_address:
            return None

        # The canonical address is built from parsed components, so we need to parse it back
        # But we should use the individual components that were already parsed during XML processing
        # For now, since we don't have access to the original parsed components here,
        # we'll improve the parsing logic to handle the canonical format better

        # Clean up the canonical address - remove extra spaces and normalize
        canonical_address = ' '.join(canonical_address.split())

        # Check if it's a PO Box address
        if canonical_address.upper().startswith('PO BOX'):
            # For PO Box addresses, we can't geocode them, so return None
            return None

        # Split by spaces and work backwards
        words = canonical_address.split()
        if len(words) < 4:
            logger.debug(f"Address too short to parse: '{canonical_address}'")
            return None

        # Last word should be ZIP
        zip_part = words[-1]
        if not (zip_part.isdigit() and len(zip_part) == 5):
            zip_part = db_zip or ''
            if not zip_part:
                logger.debug(f"No valid ZIP found in '{canonical_address}'")
                return None

        # Second to last word should be state
        state = words[-2].upper()
        if state not in self.VALID_STATES and state not in self.STATE_NAME_TO_ABBREV:
            # Try to normalize state name
            normalized_state = self._normalize_state(state)
            if not normalized_state:
                logger.debug(f"Invalid state '{state}' in address: '{canonical_address}'")
                return None
            state = normalized_state

        # Everything before state and zip is city and street
        # We need to split this into street and city
        # Look for common street suffixes to identify where street ends and city begins
        street_suffixes = {'STREET', 'ST', 'AVENUE', 'AVE', 'ROAD', 'RD', 'DRIVE', 'DR', 'LANE', 'LN',
                          'BOULEVARD', 'BLVD', 'PLACE', 'PL', 'COURT', 'CT', 'CIRCLE', 'CIR',
                          'PARKWAY', 'PKWY', 'HIGHWAY', 'HWY', 'SQUARE', 'SQ', 'TERRACE', 'TER'}

        remaining_words = words[:-2]  # Everything except state and zip
        street_parts = []
        city_parts = []

        # Work backwards from the end to find where city starts
        found_city_start = False
        for i in range(len(remaining_words) - 1, -1, -1):
            word = remaining_words[i].upper()
            if word in street_suffixes or word.replace('.', '') in street_suffixes:
                # This looks like a street suffix, so everything before it + suffix is street
                street_parts = remaining_words[:i+1]
                city_parts = remaining_words[i+1:]
                found_city_start = True
                break

        if not found_city_start:
            # Fallback: assume last 1-2 words are city, rest is street
            if len(remaining_words) >= 3:
                street_parts = remaining_words[:-1]  # Leave more for street
                city_parts = remaining_words[-1:]   # Just last word as city
            elif len(remaining_words) == 2:
                street_parts = remaining_words[:1]
                city_parts = remaining_words[1:]
            else:
                # Can't parse
                logger.debug(f"Unable to split street/city in: '{canonical_address}'")
                return None

        street = ' '.join(street_parts)
        city = ' '.join(city_parts)

        # Validate we have reasonable components
        if not street or not city:
            logger.debug(f"Missing street or city in parsed address: street='{street}', city='{city}'")
            return None

        return {
            'street': street.strip(),
            'city': city.strip(),
            'state': state.strip(),
            'zip': zip_part
        }

    def geolocate_addresses(self) -> int:
        """Geolocate addresses using census API (step 7)"""
        logger.info("Starting address geolocation")

        # Check if censusgeocode is available
        if cg is None:
            logger.warning("censusgeocode library not available. Skipping geocoding. "
                          "To enable geocoding, install with: pip install censusgeocode")
            return 0

        # Get addresses that need geocoding, deduplicated by canonical address
        addresses = self.db_ops.get_addresses_for_geocoding()
        logger.info(f"Found {len(addresses)} addresses to geolocate")

        # DEBUG: Log sample addresses to verify data
        if addresses:
            logger.info(f"Sample address data: {addresses[:3]}")
        else:
            logger.warning("No addresses found for geocoding - check if XML processing completed successfully")
            return 0

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
        address_id_lists = []  # list of lists of address_ids, parallel to addresses_to_geocode
        full_address_to_index = {}  # full_address -> index in addresses_to_geocode

        for idx, (address_id, canonical_address, po_box, zip_code) in enumerate(batch):
            logger.debug(f"Processing address_id {address_id}: canonical='{canonical_address}', po_box='{po_box}', zip='{zip_code}'")

            # Skip if PO box - colocator already set during insertion
            if po_box and po_box.strip():
                logger.debug(f"Skipping PO box address {address_id} - colocator already set")
                continue

            # Prepare address for geocoding
            # Parse canonical_address back to components with improved logic
            parsed_components = self._parse_address_components(canonical_address, zip_code)
            logger.debug(f"Parsed components for {address_id}: {parsed_components}")

            if parsed_components:
                street = parsed_components['street']
                city = parsed_components['city']
                state = parsed_components['state']
                zip_part = parsed_components['zip']

                # Normalize state
                normalized_state = self._normalize_state(state)
                if normalized_state:
                    full_address = f"{street}, {city}, {normalized_state} {zip_part}"
                    logger.debug(f"Parsed address for {address_id}: '{full_address}'")

                    # Group addresses by full_address for batch geocoding
                    if full_address not in full_address_to_index:
                        full_address_to_index[full_address] = len(addresses_to_geocode)
                        addresses_to_geocode.append({
                            'full_address': full_address,
                            'street': street,
                            'city': city,
                            'state': normalized_state,
                            'zip': zip_part
                        })
                        address_id_lists.append([address_id])
                    else:
                        addr_idx = full_address_to_index[full_address]
                        address_id_lists[addr_idx].append(address_id)
                else:
                    logger.warning(f"Invalid state '{state}' for address {address_id} - canonical: '{canonical_address}'")
            else:
                logger.warning(f"Failed to parse address for {address_id}: '{canonical_address}'")

        if not addresses_to_geocode:
            logger.debug("No addresses to geocode in this batch")
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

            results = cg.addressbatch(batch_addresses)
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