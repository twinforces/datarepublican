#!/usr/bin/env python3
"""
Grant Matching with Backfill Generation

This script implements grant matching using lat/long data to match foreign grants
(those without EINs) to existing charities based on address proximity.

Process:
1. Query grants from the Grants table that have no grant_ein (foreign grants)
2. For each grant without an EIN, extract grantee address information
3. Geocode the grantee address to get lat/long coordinates
4. Find charities within a reasonable distance (~11km / 0.1 degrees)
5. Match based on proximity and name similarity if possible
6. Update the grant record with the matched EIN if found
7. Create a Backfill record with the grant recipient information if no match found

Usage:
    python grant_matching.py [--start-year YEAR] [--end-year YEAR] [--distance DEGREES] [--batch-size SIZE]
"""

import sqlite3
import argparse
import logging
import sys
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import math
import re

# Import existing utilities
from geocoding_db import GeocodingManager, AddressManager
from models import Grant
from pipeline_db import PipelineDatabaseManager
import extract_utils as cu


@dataclass
class GrantMatchCandidate:
    """Represents a potential charity match for a grant"""
    charity_ein: str
    charity_name: str
    distance_km: float
    latitude: float
    longitude: float
    address: str
    name_similarity_score: float = 0.0


class GrantMatcher:
    """Handles grant matching logic using spatial proximity"""

    def __init__(self, db_path: str = None,
                 distance_threshold_degrees: float = 0.1):
        """
        Initialize the grant matcher

        Args:
            db_path: Path to the SQLite database (defaults to /Volumes/Data/final/pipeline_progress.db)
            distance_threshold_degrees: Maximum distance in degrees (~11km at equator)
        """
        if db_path is None:
            db_path = "/Volumes/Data/final/pipeline_progress.db"

        self.db_path = db_path
        """
        Initialize the grant matcher

        Args:
            db_path: Path to the SQLite database
            distance_threshold_degrees: Maximum distance in degrees (~11km at equator)
        """
        self.db_path = db_path
        self.distance_threshold = distance_threshold_degrees
        self.geocoding_manager = GeocodingManager(db_path)
        self.address_manager = AddressManager(db_path)
        self.pipeline_db = PipelineDatabaseManager(db_path)

        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def get_unmatched_grants(self, start_year: int = None, end_year: int = None,
                           limit: int = None) -> List[Dict]:
        """
        Get grants without EINs (foreign grants) that need matching

        Args:
            start_year: Filter grants from this year onwards
            end_year: Filter grants up to this year
            limit: Maximum number of grants to return

        Returns:
            List of grant dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            query = """
                SELECT g.grant_id, g.filer_ein, g.filer_name, g.grant_amt, g.tax_year,
                       g.filer_colocator, g.grantee_colocator, g.created_at,
                       b.name, b.canonical_address, b.po_box, b.zip_code
                FROM Grants g
                LEFT JOIN Backfill b ON g.grant_ein = b.grant_ein
                WHERE (g.grant_ein IS NULL OR g.grant_ein = '' OR g.grant_ein = 'Unknown')
                AND g.grantee_colocator NOT LIKE 'FOREIGN:%'
            """

            params = []
            if start_year is not None:
                query += " AND g.tax_year >= ?"
                params.append(start_year)
            if end_year is not None:
                query += " AND g.tax_year <= ?"
                params.append(end_year)

            query += " ORDER BY g.tax_year DESC, g.grant_amt DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            columns = [desc[0] for desc in cursor.description]

            grants = []
            for row in cursor.fetchall():
                grant_dict = dict(zip(columns, row))
                grants.append(grant_dict)

            self.logger.info(f"Found {len(grants)} unmatched grants")
            return grants

    def extract_grantee_address(self, grant: Dict) -> Optional[Dict[str, str]]:
        """
        Extract grantee address information from grant data

        Args:
            grant: Grant dictionary from database

        Returns:
            Address dictionary or None if no address available
        """
        # Try to get address from backfill data first
        if grant.get('canonical_address') or grant.get('po_box') or grant.get('zip_code'):
            return {
                'name': grant.get('name', ''),
                'canonical_address': grant.get('canonical_address', ''),
                'po_box': grant.get('po_box', ''),
                'zip_code': grant.get('zip_code', '')
            }

        # If no backfill data, try to extract from grantee_colocator
        colocator = grant.get('grantee_colocator', '')
        if colocator and not colocator.startswith('FOREIGN'):
            # Parse colocator format - could be lat,lng or zip:street or full address
            if ',' in colocator and len(colocator.split(',')) >= 3:
                # This looks like a full address (street, city, state zip)
                return {
                    'name': grant.get('name', ''),
                    'canonical_address': colocator,
                    'po_box': None,
                    'zip_code': None  # Will be extracted during geocoding
                }
            elif ',' in colocator and len(colocator.split(',')) == 2:
                # This might be lat,lng coordinates
                try:
                    lat, lon = map(float, colocator.split(','))
                    # We can't extract address from coordinates
                    return None
                except ValueError:
                    # Not coordinates, treat as address
                    return {
                        'name': grant.get('name', ''),
                        'canonical_address': colocator,
                        'po_box': None,
                        'zip_code': None
                    }
            elif ':' in colocator:
                # This might be zip:street format
                parts = colocator.split(':', 1)
                if len(parts) == 2:
                    zip_code, street = parts
                    if zip_code and len(zip_code) == 5 and zip_code.isdigit():
                        return {
                            'name': grant.get('name', ''),
                            'canonical_address': street,
                            'po_box': None,
                            'zip_code': zip_code
                        }

        return None

    def geocode_address(self, address: Dict[str, str]) -> Optional[Tuple[float, float]]:
        """
        Geocode an address to get lat/long coordinates

        Args:
            address: Address dictionary

        Returns:
            Tuple of (latitude, longitude) or None if geocoding failed
        """
        if not address.get('canonical_address') and not address.get('zip_code'):
            return None

        # Create a normalized address string
        address_parts = []
        if address.get('canonical_address'):
            address_parts.append(address['canonical_address'])
        if address.get('zip_code'):
            address_parts.append(address['zip_code'])

        normalized_address = ', '.join(address_parts)
        if not normalized_address:
            return None

        # Check if already geocoded
        import hashlib
        from geocoding import normalize_address
        normalized = normalize_address(normalized_address)
        address_hash = hashlib.sha256(normalized.encode()).hexdigest()

        existing_geocoding = self.geocoding_manager.get_geocoding_by_hash(address_hash)
        if existing_geocoding and existing_geocoding.is_successful:
            return existing_geocoding.latitude, existing_geocoding.longitude

        # Perform geocoding
        try:
            import geocoding
            result = geocoding.geocode_single(normalized_address, use_database_cache=True,
                                            geocoding_manager=self.geocoding_manager)
            if result and len(result) == 2:
                lat, lon = result
                # Mark as successful in database
                geocoding_record = self.geocoding_manager.get_geocoding_by_hash(address_hash)
                if geocoding_record:
                    self.geocoding_manager.mark_geocoding_success(geocoding_record.geocoding_id, lat, lon)
                return lat, lon
            else:
                # Mark as failed
                if existing_geocoding:
                    self.geocoding_manager.mark_geocoding_failed(existing_geocoding.geocoding_id)
        except Exception as e:
            self.logger.warning(f"Geocoding failed for address {normalized_address}: {e}")

        return None

    def find_nearby_charities(self, lat: float, lon: float, max_distance_degrees: float = None) -> List[GrantMatchCandidate]:
        """
        Find charities within the specified distance of the given coordinates

        Args:
            lat: Latitude
            lon: Longitude
            max_distance_degrees: Maximum distance in degrees (defaults to instance threshold)

        Returns:
            List of GrantMatchCandidate objects
        """
        if max_distance_degrees is None:
            max_distance_degrees = self.distance_threshold

        # Get all successful geocodings
        geocodings = self.geocoding_manager.get_successful_geocodings()

        candidates = []
        for geocoding in geocodings:
            if geocoding.latitude is None or geocoding.longitude is None:
                continue

            # Calculate distance in degrees (simple approximation)
            lat_diff = geocoding.latitude - lat
            lon_diff = geocoding.longitude - lon
            # Adjust longitude difference by latitude for better approximation
            adjusted_lon_diff = lon_diff * math.cos(math.radians((lat + geocoding.latitude) / 2))
            distance_degrees = math.sqrt(lat_diff**2 + adjusted_lon_diff**2)

            if distance_degrees <= max_distance_degrees:
                # Convert to kilometers (rough approximation)
                distance_km = distance_degrees * 111  # ~111km per degree

                # Get charity info for this geocoding - we need to find which charity has this address
                # Query addresses that have this geocoding_id
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT ein, name, canonical_address
                        FROM Addresses
                        WHERE geocoding_id = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (geocoding.geocoding_id,))

                    row = cursor.fetchone()
                    if row:
                        charity_ein, charity_name, canonical_address = row

                        candidate = GrantMatchCandidate(
                            charity_ein=charity_ein,
                            charity_name=charity_name,
                            distance_km=distance_km,
                            latitude=geocoding.latitude,
                            longitude=geocoding.longitude,
                            address=canonical_address or geocoding.normalized_address
                        )
                        candidates.append(candidate)

        # Sort by distance
        candidates.sort(key=lambda c: c.distance_km)
        return candidates

    def calculate_name_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate similarity score between two organization names

        Args:
            name1: First organization name
            name2: Second organization name

        Returns:
            Similarity score between 0.0 and 1.0
        """
        if not name1 or not name2:
            return 0.0

        # Normalize names
        name1_norm = re.sub(r'[^a-zA-Z0-9\s]', '', name1).strip().upper()
        name2_norm = re.sub(r'[^a-zA-Z0-9\s]', '', name2).strip().upper()

        # Simple word-based similarity
        words1 = set(name1_norm.split())
        words2 = set(name2_norm.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1.intersection(words2)
        union = words1.union(words2)

        return len(intersection) / len(union) if union else 0.0

    def match_grant_to_charity(self, grant: Dict, candidates: List[GrantMatchCandidate]) -> Optional[GrantMatchCandidate]:
        """
        Select the best charity match for a grant

        Args:
            grant: Grant dictionary
            candidates: List of candidate charities

        Returns:
            Best matching GrantMatchCandidate or None
        """
        if not candidates:
            return None

        # Calculate name similarity for each candidate
        grantee_name = grant.get('name', '')
        for candidate in candidates:
            candidate.name_similarity_score = self.calculate_name_similarity(grantee_name, candidate.charity_name)

        # Sort by combined score: prioritize close distance, then name similarity
        candidates.sort(key=lambda c: (c.distance_km, -c.name_similarity_score))

        # Return the best match if it's within reasonable criteria
        best_match = candidates[0]
        if best_match.distance_km <= (self.distance_threshold * 111):  # Convert to km
            return best_match

        return None


    def create_backfill_record(self, grant: Dict) -> bool:
        """
        Create a backfill record for an unmatched grant

        Args:
            grant: Grant dictionary

        Returns:
            True if backfill record created
        """
        # Check if backfill already exists
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT backfill_id FROM Backfill WHERE grant_ein = ? AND name = ? AND zip_code = ?",
                (grant.get('grant_ein', ''), grant.get('name', ''), grant.get('zip_code', ''))
            )
            if cursor.fetchone():
                return True  # Already exists

            # Extract name from grant data or use a placeholder
            name = grant.get('name') or grant.get('filer_name', 'Unknown Organization')
            if not name or name == 'Unknown Organization':
                # Try to extract from colocator or use a generic name
                colocator = grant.get('grantee_colocator', '')
                if colocator and not colocator.startswith('FOREIGN'):
                    name = f"Organization at {colocator[:50]}..."
                else:
                    name = f"Unmatched Grant Recipient {grant['grant_id']}"

            # Insert new backfill record
            cursor.execute("""
                INSERT INTO Backfill (grant_ein, name, canonical_address, po_box, zip_code, source)
                VALUES (?, ?, ?, ?, ?, 'grant_matching')
            """, (
                grant.get('grant_ein', ''),
                name,
                grant.get('canonical_address', ''),
                grant.get('po_box', ''),
                grant.get('zip_code', '')
            ))
            return cursor.rowcount > 0

    def update_grant_with_ein(self, grant_id: int, matched_ein: str) -> bool:
        """
        Update a grant record with the matched EIN

        Args:
            grant_id: Grant ID to update
            matched_ein: The matched charity EIN

        Returns:
            True if update successful
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE Grants SET grant_ein = ?, updated_at = CURRENT_TIMESTAMP WHERE grant_id = ?",
                (matched_ein, grant_id)
            )
            return cursor.rowcount > 0

    def process_grants_batch(self, grants: List[Dict], batch_size: int = 100) -> Dict[str, int]:
        """
        Process a batch of grants for matching

        Args:
            grants: List of grant dictionaries
            batch_size: Number of grants to process before logging progress

        Returns:
            Statistics dictionary
        """
        stats = {
            'processed': 0,
            'matched': 0,
            'backfilled': 0,
            'no_address': 0,
            'geocoding_failed': 0,
            'no_candidates': 0,
            'errors': 0
        }

        for i, grant in enumerate(grants):
            try:
                stats['processed'] += 1

                if stats['processed'] % batch_size == 0:
                    self.logger.info(f"Processed {stats['processed']}/{len(grants)} grants. "
                                   f"Matched: {stats['matched']}, Backfilled: {stats['backfilled']}")

                # Extract grantee address
                address = self.extract_grantee_address(grant)
                if not address:
                    stats['no_address'] += 1
                    # Still create backfill if we have basic info
                    if grant.get('name') or grant.get('zip_code'):
                        self.create_backfill_record(grant)
                        stats['backfilled'] += 1
                    continue

                # Geocode the address
                coords = self.geocode_address(address)
                if not coords:
                    stats['geocoding_failed'] += 1
                    # Create backfill record
                    self.create_backfill_record(grant)
                    stats['backfilled'] += 1
                    continue

                lat, lon = coords

                # Find nearby charities
                candidates = self.find_nearby_charities(lat, lon)
                if not candidates:
                    stats['no_candidates'] += 1
                    # Create backfill record
                    self.create_backfill_record(grant)
                    stats['backfilled'] += 1
                    continue

                # Match to best candidate
                match = self.match_grant_to_charity(grant, candidates)
                if match:
                    # Update grant with matched EIN
                    success = self.update_grant_with_ein(grant['grant_id'], match.charity_ein)
                    if success:
                        stats['matched'] += 1
                        self.logger.info(f"Matched grant {grant['grant_id']} to {match.charity_ein} "
                                       f"({match.charity_name}) at {match.distance_km:.2f}km")
                    else:
                        self.logger.warning(f"Failed to update grant {grant['grant_id']} with EIN {match.charity_ein}")
                        stats['backfilled'] += 1
                        self.create_backfill_record(grant)
                else:
                    # No good match found, create backfill
                    stats['backfilled'] += 1
                    self.create_backfill_record(grant)

            except Exception as e:
                stats['errors'] += 1
                self.logger.error(f"Error processing grant {grant.get('grant_id', 'unknown')}: {e}")
                # Still try to create backfill record on error
                try:
                    self.create_backfill_record(grant)
                    stats['backfilled'] += 1
                except Exception as backfill_error:
                    self.logger.error(f"Failed to create backfill for grant {grant.get('grant_id', 'unknown')}: {backfill_error}")

        return stats

    def run_matching(self, start_year: int = None, end_year: int = None,
                    limit: int = None, batch_size: int = 100) -> Dict[str, int]:
        """
        Run the complete grant matching process

        Args:
            start_year: Start year for grants to process
            end_year: End year for grants to process
            limit: Maximum number of grants to process
            batch_size: Batch size for processing

        Returns:
            Final statistics
        """
        self.logger.info("Starting grant matching process")
        self.logger.info(f"Distance threshold: {self.distance_threshold} degrees (~{self.distance_threshold * 111:.0f}km)")

        # Get unmatched grants
        grants = self.get_unmatched_grants(start_year, end_year, limit)
        if not grants:
            self.logger.info("No unmatched grants found")
            return {'processed': 0, 'matched': 0, 'backfilled': 0}

        # Process grants in batches
        stats = self.process_grants_batch(grants, batch_size)

        self.logger.info("Grant matching completed:")
        self.logger.info(f"  Processed: {stats['processed']}")
        self.logger.info(f"  Matched: {stats['matched']}")
        self.logger.info(f"  Backfilled: {stats['backfilled']}")
        self.logger.info(f"  No address: {stats['no_address']}")
        self.logger.info(f"  Geocoding failed: {stats['geocoding_failed']}")
        self.logger.info(f"  No candidates: {stats['no_candidates']}")
        if stats['errors'] > 0:
            self.logger.warning(f"  Errors: {stats['errors']}")

        return stats


def main():
    parser = argparse.ArgumentParser(description="Grant Matching with Backfill Generation")
    parser.add_argument("--db-path", default="/Volumes/Data/final/pipeline_progress.db",
                       help="Path to SQLite database")
    parser.add_argument("--start-year", type=int, help="Start year for grants")
    parser.add_argument("--end-year", type=int, help="End year for grants")
    parser.add_argument("--distance", type=float, default=0.1,
                       help="Distance threshold in degrees (~11km)")
    parser.add_argument("--limit", type=int, help="Maximum number of grants to process")
    parser.add_argument("--batch-size", type=int, default=100,
                       help="Batch size for processing")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    # Initialize matcher
    matcher = GrantMatcher(args.db_path, args.distance)

    # Run matching
    stats = matcher.run_matching(args.start_year, args.end_year, args.limit, args.batch_size)

    # Exit with success/failure code
    if stats['processed'] > 0:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()