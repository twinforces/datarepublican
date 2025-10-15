"""
Database-backed geocoding system for IRS 990 processing pipeline.
Provides AddressManager and GeocodingManager classes for persistent storage
of addresses and geocoding results in SQLite database.
"""

import sqlite3
import hashlib
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import asdict
from models import Address, Geocoding


class DatabaseManager:
    """Base class for database operations with connection management."""

    def __init__(self, db_path: str = "/Volumes/Data/final/pipeline_progress.db"):
        """Initialize database connection."""
        self.db_path = db_path
        self._ensure_tables_exist()

    def _ensure_tables_exist(self):
        """Ensure required tables exist in the database."""
        with sqlite3.connect(self.db_path) as conn:
            # Create Addresses table if it doesn't exist
            conn.execute('''
                CREATE TABLE IF NOT EXISTS Addresses (
                    address_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ein TEXT NOT NULL,
                    name TEXT NOT NULL,
                    canonical_address TEXT,
                    po_box TEXT,
                    zip_code TEXT,
                    address_type TEXT NOT NULL CHECK(address_type IN ('filer', 'grantee')),
                    geocoding_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (geocoding_id) REFERENCES Geocoding(geocoding_id) ON DELETE SET NULL,
                    UNIQUE(ein, canonical_address)
                )
            ''')

            # Create Geocoding table if it doesn't exist
            conn.execute('''
                CREATE TABLE IF NOT EXISTS Geocoding (
                    geocoding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address_hash TEXT NOT NULL UNIQUE,
                    normalized_address TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    geocoding_status TEXT DEFAULT 'pending' CHECK(
                        geocoding_status IN ('pending', 'success', 'failed')
                    ),
                    last_attempt DATETIME,
                    attempt_count INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create indexes for performance
            conn.execute('CREATE INDEX IF NOT EXISTS idx_addresses_ein ON Addresses(ein)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_addresses_zip_code ON Addresses(zip_code)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_addresses_type ON Addresses(address_type)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_addresses_geocoding ON Addresses(geocoding_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_geocoding_address_hash ON Geocoding(address_hash)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_geocoding_status ON Geocoding(geocoding_status)')

            # Create triggers for updated_at timestamps
            conn.execute('''
                CREATE TRIGGER IF NOT EXISTS update_geocoding_timestamp
                AFTER UPDATE ON Geocoding
                BEGIN
                    UPDATE Geocoding SET updated_at = CURRENT_TIMESTAMP
                    WHERE geocoding_id = NEW.geocoding_id;
                END
            ''')

            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        return sqlite3.connect(self.db_path)


class AddressManager(DatabaseManager):
    """Manages database operations for addresses."""

    def _row_to_address(self, row) -> Address:
        """Convert database row to Address object."""
        return Address(
            address_id=row[0],
            ein=row[1],
            name=row[2],
            canonical_address=row[3],
            po_box=row[4],
            zip_code=row[5],
            address_type=row[6],
            geocoding_id=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else None,
        )

    def insert_address(self, address: Address) -> int:
        """Insert a new address into the database. Returns the address_id."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                INSERT INTO Addresses
                (ein, name, canonical_address, po_box, zip_code, address_type, geocoding_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ein, canonical_address) DO UPDATE SET
                    geocoding_id = excluded.geocoding_id
                RETURNING address_id
            ''', (
                address.ein,
                address.name,
                address.canonical_address,
                address.po_box,
                address.zip_code,
                address.address_type,
                address.geocoding_id
            ))

            result = cursor.fetchone()
            return result[0] if result else 0

    def get_address_by_id(self, address_id: int) -> Optional[Address]:
        """Get an address by its ID."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT address_id, ein, name, canonical_address, po_box, zip_code,
                       address_type, geocoding_id, created_at
                FROM Addresses
                WHERE address_id = ?
            ''', (address_id,))

            row = cursor.fetchone()
            return self._row_to_address(row) if row else None

    def get_addresses_by_ein(self, ein: str) -> List[Address]:
        """Get all addresses for a given EIN."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT address_id, ein, name, canonical_address, po_box, zip_code,
                       address_type, geocoding_id, created_at
                FROM Addresses
                WHERE ein = ?
                ORDER BY created_at
            ''', (ein,))

            return [self._row_to_address(row) for row in cursor.fetchall()]

    def update_address(self, address: Address) -> bool:
        """Update an existing address."""
        if not address.address_id:
            return False

        with self._get_connection() as conn:
            conn.execute('''
                UPDATE Addresses
                SET ein = ?, name = ?, canonical_address = ?, po_box = ?,
                    zip_code = ?, address_type = ?, geocoding_id = ?
                WHERE address_id = ?
            ''', (
                address.ein,
                address.name,
                address.canonical_address,
                address.po_box,
                address.zip_code,
                address.address_type,
                address.geocoding_id,
                address.address_id
            ))

            return conn.total_changes > 0

    def delete_address(self, address_id: int) -> bool:
        """Delete an address by ID."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM Addresses WHERE address_id = ?', (address_id,))
            return conn.total_changes > 0

    def get_addresses_without_geocoding(self, limit: int = 1000) -> List[Address]:
        """Get addresses that don't have geocoding results yet."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT address_id, ein, name, canonical_address, po_box, zip_code,
                       address_type, geocoding_id, created_at
                FROM Addresses
                WHERE geocoding_id IS NULL
                ORDER BY created_at
                LIMIT ?
            ''', (limit,))

            return [self._row_to_address(row) for row in cursor.fetchall()]


class GeocodingManager(DatabaseManager):
    """Manages database operations for geocoding results."""

    def _row_to_geocoding(self, row) -> Geocoding:
        """Convert database row to Geocoding object."""
        return Geocoding(
            geocoding_id=row[0],
            address_hash=row[1],
            normalized_address=row[2],
            latitude=row[3],
            longitude=row[4],
            geocoding_status=row[5],
            last_attempt=datetime.fromisoformat(row[6]) if row[6] else None,
            attempt_count=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else None,
            updated_at=datetime.fromisoformat(row[9]) if row[9] else None,
        )

    def insert_geocoding(self, geocoding: Geocoding) -> int:
        """Insert a new geocoding result. Returns the geocoding_id."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                INSERT INTO Geocoding
                (address_hash, normalized_address, latitude, longitude, geocoding_status,
                 last_attempt, attempt_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address_hash) DO UPDATE SET
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    geocoding_status = excluded.geocoding_status,
                    last_attempt = excluded.last_attempt,
                    attempt_count = excluded.attempt_count
                RETURNING geocoding_id
            ''', (
                geocoding.address_hash,
                geocoding.normalized_address,
                geocoding.latitude,
                geocoding.longitude,
                geocoding.geocoding_status,
                geocoding.last_attempt.isoformat() if geocoding.last_attempt else None,
                geocoding.attempt_count
            ))

            result = cursor.fetchone()
            return result[0] if result else 0

    def get_geocoding_by_hash(self, address_hash: str) -> Optional[Geocoding]:
        """Get geocoding result by address hash."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT geocoding_id, address_hash, normalized_address, latitude, longitude,
                       geocoding_status, last_attempt, attempt_count, created_at, updated_at
                FROM Geocoding
                WHERE address_hash = ?
            ''', (address_hash,))

            row = cursor.fetchone()
            return self._row_to_geocoding(row) if row else None

    def get_geocoding_by_id(self, geocoding_id: int) -> Optional[Geocoding]:
        """Get geocoding result by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT geocoding_id, address_hash, normalized_address, latitude, longitude,
                       geocoding_status, last_attempt, attempt_count, created_at, updated_at
                FROM Geocoding
                WHERE geocoding_id = ?
            ''', (geocoding_id,))

            row = cursor.fetchone()
            return self._row_to_geocoding(row) if row else None

    def update_geocoding(self, geocoding: Geocoding) -> bool:
        """Update an existing geocoding result."""
        if not geocoding.geocoding_id:
            return False

        with self._get_connection() as conn:
            conn.execute('''
                UPDATE Geocoding
                SET address_hash = ?, normalized_address = ?, latitude = ?, longitude = ?,
                    geocoding_status = ?, last_attempt = ?, attempt_count = ?
                WHERE geocoding_id = ?
            ''', (
                geocoding.address_hash,
                geocoding.normalized_address,
                geocoding.latitude,
                geocoding.longitude,
                geocoding.geocoding_status,
                geocoding.last_attempt.isoformat() if geocoding.last_attempt else None,
                geocoding.attempt_count,
                geocoding.geocoding_id
            ))

            return conn.total_changes > 0

    def get_pending_geocodings(self, limit: int = 100) -> List[Geocoding]:
        """Get geocoding records that are still pending."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT geocoding_id, address_hash, normalized_address, latitude, longitude,
                       geocoding_status, last_attempt, attempt_count, created_at, updated_at
                FROM Geocoding
                WHERE geocoding_status = 'pending'
                ORDER BY created_at
                LIMIT ?
            ''', (limit,))

            return [self._row_to_geocoding(row) for row in cursor.fetchall()]

    def get_successful_geocodings(self) -> List[Geocoding]:
        """Get all successful geocoding results."""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT geocoding_id, address_hash, normalized_address, latitude, longitude,
                       geocoding_status, last_attempt, attempt_count, created_at, updated_at
                FROM Geocoding
                WHERE geocoding_status = 'success'
                ORDER BY created_at
            ''')

            return [self._row_to_geocoding(row) for row in cursor.fetchall()]

    def create_geocoding_from_address(self, normalized_address: str) -> Geocoding:
        """Create a new geocoding record for an address."""
        address_hash = hashlib.sha256(normalized_address.encode()).hexdigest()

        geocoding = Geocoding(
            address_hash=address_hash,
            normalized_address=normalized_address,
            geocoding_status='pending',
            attempt_count=0
        )

        geocoding_id = self.insert_geocoding(geocoding)
        geocoding.geocoding_id = geocoding_id
        return geocoding

    def mark_geocoding_success(self, geocoding_id: int, lat: float, lon: float) -> bool:
        """Mark a geocoding as successful with coordinates."""
        with self._get_connection() as conn:
            now = datetime.now().isoformat()
            conn.execute('''
                UPDATE Geocoding
                SET latitude = ?, longitude = ?, geocoding_status = 'success',
                    last_attempt = ?, attempt_count = attempt_count + 1
                WHERE geocoding_id = ?
            ''', (lat, lon, now, geocoding_id))

            return conn.total_changes > 0

    def mark_geocoding_failed(self, geocoding_id: int) -> bool:
        """Mark a geocoding as failed."""
        with self._get_connection() as conn:
            now = datetime.now().isoformat()
            conn.execute('''
                UPDATE Geocoding
                SET geocoding_status = 'failed', last_attempt = ?,
                    attempt_count = attempt_count + 1
                WHERE geocoding_id = ?
            ''', (now, geocoding_id))

            return conn.total_changes > 0


def batch_geocode_with_cache(addresses: List[str], geocoding_manager: GeocodingManager,
                           address_manager: Optional[AddressManager] = None) -> List[Dict[str, Any]]:
    """
    Batch geocode addresses using database caching.

    Args:
        addresses: List of address strings to geocode
        geocoding_manager: GeocodingManager instance for database operations
        address_manager: Optional AddressManager for storing address records

    Returns:
        List of dicts with 'address', 'lat', 'lon', 'geocoding_id' keys
    """
    import geocoding  # Import here to avoid circular imports

    results = []

    # Check cache for each address
    uncached_addresses = []
    uncached_indices = []

    for i, addr in enumerate(addresses):
        normalized = geocoding.normalize_address(addr)
        address_hash = hashlib.sha256(normalized.encode()).hexdigest()

        cached_result = geocoding_manager.get_geocoding_by_hash(address_hash)
        if cached_result and cached_result.is_successful:
            results.append({
                'address': addr,
                'lat': cached_result.latitude,
                'lon': cached_result.longitude,
                'geocoding_id': cached_result.geocoding_id
            })
        else:
            # Create pending geocoding record if it doesn't exist
            if not cached_result:
                geocoding_manager.create_geocoding_from_address(normalized)
                cached_result = geocoding_manager.get_geocoding_by_hash(address_hash)

            uncached_addresses.append(addr)
            uncached_indices.append(i)
            results.append({
                'address': addr,
                'lat': None,
                'lon': None,
                'geocoding_id': cached_result.geocoding_id if cached_result else None
            })

    # Geocode uncached addresses
    if uncached_addresses:
        try:
            geocoded_results = geocoding.geocode_batch(uncached_addresses)

            for j, result in enumerate(geocoded_results):
                original_index = uncached_indices[j]
                geocoding_id = results[original_index]['geocoding_id']

                if result['lat'] is not None and result['lon'] is not None and geocoding_id:
                    geocoding_manager.mark_geocoding_success(geocoding_id, result['lat'], result['lon'])
                    results[original_index]['lat'] = result['lat']
                    results[original_index]['lon'] = result['lon']
                elif geocoding_id:
                    geocoding_manager.mark_geocoding_failed(geocoding_id)

        except Exception as e:
            print(f"Batch geocoding failed: {e}")
            # Mark all uncached as failed
            for j in range(len(uncached_addresses)):
                original_index = uncached_indices[j]
                geocoding_id = results[original_index]['geocoding_id']
                if geocoding_id:
                    geocoding_manager.mark_geocoding_failed(geocoding_id)

    return results


def get_geocoded_address(ein: str, address_type: str = 'filer') -> Optional[Dict[str, Any]]:
    """
    Get geocoded address information for an EIN.

    Args:
        ein: Employer Identification Number
        address_type: 'filer' or 'grantee'

    Returns:
        Dict with address and geocoding info, or None if not found
    """
    address_manager = AddressManager()
    geocoding_manager = GeocodingManager()

    addresses = address_manager.get_addresses_by_ein(ein)
    filer_addresses = [addr for addr in addresses if addr.address_type == address_type]

    if not filer_addresses:
        return None

    # Get the most recent address
    address = max(filer_addresses, key=lambda x: x.created_at or datetime.min)

    if not address.geocoding_id:
        return {
            'address': address,
            'geocoding': None
        }

    geocoding = geocoding_manager.get_geocoding_by_id(address.geocoding_id)
    return {
        'address': address,
        'geocoding': geocoding
    }


def get_all_geocoded_addresses_for_ein(ein: str) -> List[Dict[str, Any]]:
    """
    Get all geocoded addresses for an EIN.

    Args:
        ein: Employer Identification Number

    Returns:
        List of dicts with address and geocoding info
    """
    address_manager = AddressManager()
    geocoding_manager = GeocodingManager()

    addresses = address_manager.get_addresses_by_ein(ein)
    results = []

    for address in addresses:
        geocoding = None
        if address.geocoding_id:
            geocoding = geocoding_manager.get_geocoding_by_id(address.geocoding_id)

        results.append({
            'address': address,
            'geocoding': geocoding
        })

    return results


def get_addresses_by_coordinates(lat: float, lon: float, radius_km: float = 10) -> List[Dict[str, Any]]:
    """
    Find addresses within a radius of given coordinates.

    Args:
        lat: Latitude
        lon: Longitude
        radius_km: Search radius in kilometers

    Returns:
        List of addresses with geocoding info within the radius
    """
    geocoding_manager = GeocodingManager()

    # Get all successful geocodings
    geocodings = geocoding_manager.get_successful_geocodings()

    # Filter by distance (simple Euclidean approximation)
    # Note: For production, consider using a spatial index or proper geospatial queries
    results = []
    for geocoding in geocodings:
        if geocoding.latitude is not None and geocoding.longitude is not None:
            # Rough distance calculation (degrees to km approximation)
            lat_diff = geocoding.latitude - lat
            lon_diff = geocoding.longitude - lon
            distance_km = ((lat_diff * 111)**2 + (lon_diff * 111 * abs(lat))**2)**0.5

            if distance_km <= radius_km:
                results.append({
                    'geocoding': geocoding,
                    'distance_km': distance_km
                })

    return results


def process_database_geocoding_batch(addresses: List[Address], geocoding_manager: GeocodingManager,
                                   address_manager: AddressManager) -> Dict[str, int]:
    """
    Process a batch of addresses for geocoding, storing results in database.

    Args:
        addresses: List of Address objects to process
        geocoding_manager: GeocodingManager instance
        address_manager: AddressManager instance

    Returns:
        Dict with processing statistics
    """
    import geocoding

    stats = {'processed': 0, 'geocoded': 0, 'po_box': 0, 'failed': 0}

    for address in addresses:
        stats['processed'] += 1

        # Check if already has geocoding
        if address.geocoding_id:
            continue

        # Handle PO Box addresses
        if address.has_po_box and address.zip_code:
            # Create placeholder geocoding for PO Box
            normalized_address = f"PO:{address.po_box}:{address.zip_code}"
            address_hash = hashlib.sha256(normalized_address.encode()).hexdigest()

            # Check if this PO Box geocoding already exists
            existing_geocoding = geocoding_manager.get_geocoding_by_hash(address_hash)
            if not existing_geocoding:
                geocoding_record = Geocoding(
                    address_hash=address_hash,
                    normalized_address=normalized_address,
                    geocoding_status='success',  # PO Box is considered successfully "geocoded"
                    attempt_count=0
                )
                geocoding_id = geocoding_manager.insert_geocoding(geocoding_record)
            else:
                geocoding_id = existing_geocoding.geocoding_id

            # Update address with geocoding_id
            address.geocoding_id = geocoding_id
            address_manager.update_address(address)
            stats['po_box'] += 1

        # Handle regular addresses
        elif address.canonical_address and address.zip_code:
            try:
                # Geocode the address
                result = geocoding.geocode_single(address.canonical_address, use_database_cache=True,
                                                geocoding_manager=geocoding_manager)

                if result and len(result) == 2:
                    lat, lon = result
                    # Get the geocoding record that was created/updated
                    address_hash = hashlib.sha256(geocoding.normalize_address(address.canonical_address).encode()).hexdigest()
                    geocoding_record = geocoding_manager.get_geocoding_by_hash(address_hash)

                    if geocoding_record:
                        # Update address with geocoding_id
                        address.geocoding_id = geocoding_record.geocoding_id
                        address_manager.update_address(address)
                        stats['geocoded'] += 1
                    else:
                        stats['failed'] += 1
                else:
                    stats['failed'] += 1

            except Exception as e:
                print(f"Geocoding failed for address {address.canonical_address}: {e}")
                stats['failed'] += 1
        else:
            stats['failed'] += 1

    return stats


def process_database_geocoding_threaded(db_path: str = "/Volumes/Data/final/pipeline_progress.db",
                                       num_threads: int = 4, batch_size: int = 5000) -> Dict[str, int]:
    """
    Process all ungeocoded addresses from database using multi-threading.

    Args:
        db_path: Path to the database file
        num_threads: Number of threads to use (default 4)
        batch_size: Number of addresses to process per batch (default 5000)

    Returns:
        Dict with overall processing statistics
    """
    import threading
    import queue
    import time

    overall_stats = {'processed': 0, 'geocoded': 0, 'po_box': 0, 'failed': 0, 'batches': 0}

    # Create managers (not thread-safe, so create per thread)
    def worker_thread(thread_id: int, address_queue: queue.Queue, stats_queue: queue.Queue):
        """Worker thread function."""
        address_manager = AddressManager(db_path)
        geocoding_manager = GeocodingManager(db_path)

        while True:
            try:
                batch = address_queue.get(timeout=1)
                if batch is None:  # Sentinel value to stop thread
                    break

                print(f"Thread {thread_id}: Processing batch of {len(batch)} addresses")
                batch_stats = process_database_geocoding_batch(batch, geocoding_manager, address_manager)
                stats_queue.put(batch_stats)
                address_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                print(f"Thread {thread_id}: Error processing batch: {e}")
                stats_queue.put({'processed': 0, 'geocoded': 0, 'po_box': 0, 'failed': 0})
                continue

    # Get all ungeocoded addresses
    temp_manager = AddressManager(db_path)
    all_ungeocoded = temp_manager.get_addresses_without_geocoding(limit=None)  # Get all

    if not all_ungeocoded:
        print("No ungeocoded addresses found.")
        return overall_stats

    print(f"Found {len(all_ungeocoded)} ungeocoded addresses to process")

    # Create batches
    batches = []
    for i in range(0, len(all_ungeocoded), batch_size):
        batches.append(all_ungeocoded[i:i + batch_size])

    overall_stats['batches'] = len(batches)

    # Create queues
    address_queue = queue.Queue()
    stats_queue = queue.Queue()

    # Start worker threads
    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker_thread, args=(i+1, address_queue, stats_queue))
        t.daemon = True
        t.start()
        threads.append(t)

    # Add batches to queue
    for batch in batches:
        address_queue.put(batch)

    # Add sentinel values to stop threads
    for _ in range(num_threads):
        address_queue.put(None)

    # Collect results
    completed_batches = 0
    while completed_batches < len(batches):
        try:
            batch_stats = stats_queue.get(timeout=5)
            overall_stats['processed'] += batch_stats['processed']
            overall_stats['geocoded'] += batch_stats['geocoded']
            overall_stats['po_box'] += batch_stats['po_box']
            overall_stats['failed'] += batch_stats['failed']
            completed_batches += 1

            print(f"Completed {completed_batches}/{len(batches)} batches. "
                  f"Total processed: {overall_stats['processed']}, "
                  f"Geocoded: {overall_stats['geocoded']}, "
                  f"PO Box: {overall_stats['po_box']}, "
                  f"Failed: {overall_stats['failed']}")

        except queue.Empty:
            # Check if all threads are still alive
            alive_threads = sum(1 for t in threads if t.is_alive())
            if alive_threads == 0:
                break
            continue

    # Wait for all threads to finish
    for t in threads:
        t.join(timeout=10)

    print("Geocoding process completed.")
    print(f"Final stats: {overall_stats}")

    return overall_stats