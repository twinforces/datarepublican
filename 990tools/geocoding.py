import censusgeocode as cg
import time
import hashlib
from typing import List, Dict, Tuple, Optional
import re

# In-memory cache for geocoded results
cache: Dict[str, Tuple[float, float]] = {}

# Database-backed geocoding support
try:
    from geocoding_db import GeocodingManager, batch_geocode_with_cache
    DATABASE_GEOCODING_AVAILABLE = True
except ImportError:
    DATABASE_GEOCODING_AVAILABLE = False
    GeocodingManager = None
    batch_geocode_with_cache = None

def normalize_address(address: str) -> str:
    """
    Normalize an address string for consistent cache keys.
    Converts to lowercase, strips whitespace, and removes extra spaces.
    """
    # Remove extra whitespace and convert to lowercase
    normalized = re.sub(r'\s+', ' ', address.strip().lower())
    return normalized

def parse_address(address: str) -> Dict[str, str]:
    """
    Parse a full address string into components for censusgeocode batch API.
    Assumes format: "street address, city, state, zip"
    """
    # Split by comma and strip
    parts = [part.strip() for part in address.split(',')]

    if len(parts) >= 4:
        # Format: "street, city, state, zip"
        street = parts[0]
        city = parts[1]
        state = parts[2]
        zipcode = parts[3]
    elif len(parts) == 3:
        # Format: "street, city, state zip"
        street = parts[0]
        city = parts[1]
        state_zip = parts[2].split()
        if len(state_zip) >= 2:
            state = state_zip[0]
            zipcode = ' '.join(state_zip[1:])
        else:
            state = parts[2]
            zipcode = ''
    elif len(parts) == 2:
        # Format: "street, city state zip"
        street = parts[0]
        city_state_zip = parts[1].split()
        if len(city_state_zip) >= 3:
            city = ' '.join(city_state_zip[:-2])
            state = city_state_zip[-2]
            zipcode = city_state_zip[-1]
        else:
            city = parts[1]
            state = ''
            zipcode = ''
    else:
        # Fallback: treat as single street field
        street = address
        city = ''
        state = ''
        zipcode = ''

    return {
        'street': street,
        'city': city,
        'state': state,
        'zip': zipcode
    }

def geocode_single(address: str, retries: int = 3, use_database_cache: bool = False,
                  geocoding_manager: Optional[GeocodingManager] = None) -> Tuple[float, float]:
    """
    Geocode a single address using censusgeocode.
    Includes caching and retry logic with exponential backoff.
    Returns lat, lon rounded to 4 decimal places.
    Raises ValueError if geocoding fails after retries.

    Args:
        address: Address string to geocode
        retries: Number of retry attempts
        use_database_cache: Whether to use database caching
        geocoding_manager: GeocodingManager instance for database operations
    """
    norm_addr = normalize_address(address)

    # Check in-memory cache first
    if norm_addr in cache:
        return cache[norm_addr]

    # Check database cache if enabled
    if use_database_cache and DATABASE_GEOCODING_AVAILABLE and geocoding_manager:
        address_hash = hashlib.sha256(norm_addr.encode()).hexdigest()
        cached_result = geocoding_manager.get_geocoding_by_hash(address_hash)
        if cached_result and cached_result.is_successful:
            lat, lon = cached_result.latitude, cached_result.longitude
            cache[norm_addr] = (lat, lon)  # Also cache in memory
            return lat, lon

    # Create database record for tracking if using database cache
    if use_database_cache and DATABASE_GEOCODING_AVAILABLE and geocoding_manager:
        geocoding_manager.create_geocoding_from_address(norm_addr)

    for attempt in range(retries):
        try:
            result = cg.address(address)
            if result and 'coordinates' in result[0]:
                lat = round(result[0]['coordinates']['y'], 4)
                lon = round(result[0]['coordinates']['x'], 4)
                cache[norm_addr] = (lat, lon)

                # Store in database cache if enabled
                if use_database_cache and DATABASE_GEOCODING_AVAILABLE and geocoding_manager:
                    address_hash = hashlib.sha256(norm_addr.encode()).hexdigest()
                    geocoding_record = geocoding_manager.get_geocoding_by_hash(address_hash)
                    if geocoding_record:
                        geocoding_manager.mark_geocoding_success(geocoding_record.geocoding_id, lat, lon)

                return lat, lon
            else:
                raise ValueError(f"No geocoding result for address: {address}")
        except Exception as e:
            if attempt == retries - 1:
                # Mark as failed in database if using database cache
                if use_database_cache and DATABASE_GEOCODING_AVAILABLE and geocoding_manager:
                    address_hash = hashlib.sha256(norm_addr.encode()).hexdigest()
                    geocoding_record = geocoding_manager.get_geocoding_by_hash(address_hash)
                    if geocoding_record:
                        geocoding_manager.mark_geocoding_failed(geocoding_record.geocoding_id)

                raise ValueError(f"Geocoding failed after {retries} attempts: {str(e)}")
            time.sleep(2 ** attempt)  # Exponential backoff

def geocode_batch(addresses: List[str], batch_size: int = 10000, retries: int = 3,
                 use_database_cache: bool = False, geocoding_manager: Optional[GeocodingManager] = None) -> List[Dict[str, Optional[float]]]:
    """
    Batch geocode a list of addresses using censusgeocode.
    Supports up to 10,000 addresses per batch (API limit).
    Includes caching and error handling.
    Returns list of dicts with 'address', 'lat', 'lon' (None if failed).

    Args:
        addresses: List of address strings to geocode
        batch_size: Maximum batch size for API calls
        retries: Number of retry attempts
        use_database_cache: Whether to use database caching
        geocoding_manager: GeocodingManager instance for database operations
    """
    if len(addresses) > 10000:
        raise ValueError("Batch size cannot exceed 10,000 addresses")

    # Use database-backed batch geocoding if available and requested
    if use_database_cache and DATABASE_GEOCODING_AVAILABLE and geocoding_manager and batch_geocode_with_cache:
        return batch_geocode_with_cache(addresses, geocoding_manager)

    # Original in-memory cache implementation
    results = []
    # Check cache first
    uncached_addresses = []
    uncached_indices = []
    for i, addr in enumerate(addresses):
        norm_addr = normalize_address(addr)
        if norm_addr in cache:
            lat, lon = cache[norm_addr]
            results.append({'address': addr, 'lat': lat, 'lon': lon})
        else:
            try:
                parsed_addr = parse_address(addr)
                uncached_addresses.append(parsed_addr)
                uncached_indices.append(i)
                results.append({'address': addr, 'lat': None, 'lon': None})  # Placeholder
            except Exception as e:
                print(f"Error parsing address '{addr}': {str(e)}")
                results.append({'address': addr, 'lat': None, 'lon': None})

    if not uncached_addresses:
        return results

    # Process uncached in batches
    for i in range(0, len(uncached_addresses), batch_size):
        batch = uncached_addresses[i:i + batch_size]
        for attempt in range(retries):
            try:
                geocoded = cg.addressbatch(batch)
                for j, result in enumerate(geocoded):
                    original_index = uncached_indices[i + j]
                    if result and 'lat' in result and 'lon' in result and result['lat'] is not None and result['lon'] is not None:
                        lat = round(result['lat'], 4)
                        lon = round(result['lon'], 4)
                        norm_addr = normalize_address(addresses[original_index])
                        cache[norm_addr] = (lat, lon)
                        results[original_index]['lat'] = lat
                        results[original_index]['lon'] = lon
                    # If no result, leave as None
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == retries - 1:
                    # Log error but continue (results will have None for failed)
                    print(f"Batch geocoding failed after {retries} attempts: {str(e)}")
                else:
                    time.sleep(2 ** attempt)

    return results

# Convenience function for single address
def geocode(address: str, use_database_cache: bool = False,
           geocoding_manager: Optional[GeocodingManager] = None) -> Tuple[float, float]:
    """
    Convenience function to geocode a single address.

    Args:
        address: Address string to geocode
        use_database_cache: Whether to use database caching
        geocoding_manager: GeocodingManager instance for database operations
    """
    return geocode_single(address, use_database_cache=use_database_cache,
                         geocoding_manager=geocoding_manager)


def geocode_with_database(address: str, geocoding_manager: Optional[GeocodingManager] = None) -> Tuple[float, float]:
    """
    Convenience function to geocode a single address using database caching.
    Creates a GeocodingManager instance if none provided.

    Args:
        address: Address string to geocode
        geocoding_manager: Optional GeocodingManager instance
    """
    if geocoding_manager is None and DATABASE_GEOCODING_AVAILABLE:
        geocoding_manager = GeocodingManager()

    return geocode_single(address, use_database_cache=True, geocoding_manager=geocoding_manager)


def batch_geocode_with_database(addresses: List[str], geocoding_manager: Optional[GeocodingManager] = None) -> List[Dict[str, Optional[float]]]:
    """
    Convenience function to batch geocode addresses using database caching.
    Creates a GeocodingManager instance if none provided.

    Args:
        addresses: List of address strings to geocode
        geocoding_manager: Optional GeocodingManager instance
    """
    if geocoding_manager is None and DATABASE_GEOCODING_AVAILABLE:
        geocoding_manager = GeocodingManager()

    return geocode_batch(addresses, use_database_cache=True, geocoding_manager=geocoding_manager)