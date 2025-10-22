#!/usr/bin/env python3
"""
address_deduplication_processor.py - Address deduplication processor

This module handles deduplication of addresses in the database by creating
master-child relationships based on canonical_address matching.
"""

import logging
from typing import Optional, List, Dict, Any
from database_operations import DatabaseOperations
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning


class AddressDeduplicationProcessor:
    """Processor for deduplicating addresses and creating master-child relationships"""

    def __init__(self, db_ops: DatabaseOperations, quiet: bool = False):
        self.db_ops = db_ops
        self.quiet = quiet
        self.logger = get_logger("address_dedup")

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, ein, exc_info, *args)

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not self.quiet:
            log_info(self.logger, msg, ein, *args)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not self.quiet:
            log_debug(self.logger, msg, ein, *args)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, ein, *args)

    def deduplicate_addresses(self) -> int:
        """
        Deduplicate addresses by creating master-child relationships.

        Returns the number of addresses processed.
        """
        self.log_info("Starting address deduplication")

        try:
            # Get all addresses grouped by canonical_address
            addresses_by_canonical = self._get_addresses_by_canonical()

            total_processed = 0
            total_masters_created = 0

            for canonical_address, addresses in addresses_by_canonical.items():
                if len(addresses) <= 1:
                    # No duplicates for this canonical address
                    continue

                # Create master address and update children
                master_id = self._create_master_address(addresses[0])
                if master_id:
                    total_masters_created += 1
                    # Update all addresses (including the master) to point to the master
                    child_count = self._update_child_addresses(master_id, addresses)
                    total_processed += child_count + 1  # +1 for master

                    self.log_debug(f"Created master {master_id} for {len(addresses)} addresses: {canonical_address[:50]}...")

            self.log_info(f"Address deduplication complete: {total_masters_created} masters created, {total_processed} addresses processed")
            return total_processed

        except Exception as e:
            self.log_error(f"Address deduplication failed: {e}", exc_info=True)
            return 0

    def _get_addresses_by_canonical(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all addresses grouped by canonical_address"""
        query = """
            SELECT address_id, ein, canonical_address, address_type, name
            FROM Addresses
            WHERE canonical_address IS NOT NULL AND canonical_address != ''
            ORDER BY canonical_address, address_id
        """

        result = self.db_ops.execute_query(query)
        addresses = result.fetchall()

        addresses_by_canonical = {}
        for row in addresses:
            canonical = getattr(row, 'canonical_address', None)
            if canonical not in addresses_by_canonical:
                addresses_by_canonical[canonical] = []
            addresses_by_canonical[canonical].append({
                'address_id': str(getattr(row, 'address_id', '')),
                'ein': getattr(row, 'ein', ''),
                'canonical_address': getattr(row, 'canonical_address', ''),
                'address_type': getattr(row, 'address_type', ''),
                'name': getattr(row, 'name', '')
            })

        return addresses_by_canonical

    def _create_master_address(self, address_data: Dict[str, Any]) -> Optional[str]:
        """Create a master address record (first address becomes master)"""
        try:
            # Use the first address as the master
            master_id = str(address_data['address_id'])

            # Update the master address to have master_id = NULL (indicating it's a master)
            self.db_ops.execute_query("""
                UPDATE Addresses
                SET master_id = NULL
                WHERE address_id = ?
            """, (master_id,))

            self.db_ops.commit()
            return master_id

        except Exception as e:
            self.log_error(f"Failed to create master address: {e}")
            return None

    def _update_child_addresses(self, master_id: str, addresses: List[Dict[str, Any]]) -> int:
        """Update child addresses to point to the master"""
        try:
            # Get all address_ids except the master
            child_ids = [str(addr['address_id']) for addr in addresses if str(addr['address_id']) != master_id]

            if not child_ids:
                return 0

            # Update all child addresses to point to the master
            placeholders = ','.join('?' for _ in child_ids)
            self.db_ops.execute_query(f"""
                UPDATE Addresses
                SET master_id = ?
                WHERE address_id IN ({placeholders})
            """, tuple([master_id] + child_ids))

            self.db_ops.commit()
            return len(child_ids)

        except Exception as e:
            self.log_error(f"Failed to update child addresses: {e}")
            return 0

    def get_master_addresses_for_geocoding(self) -> List[Dict[str, Any]]:
        """
        Get all master addresses that need geocoding (master_id IS NULL and no geocoding data)

        Returns list of address records for geocoding.
        """
        query = """
            SELECT address_id, canonical_address, city, state, zip_code, latitude, longitude, colocator
            FROM Addresses
            WHERE master_id IS NULL
            AND canonical_address IS NOT NULL
            AND canonical_address != ''
            AND (latitude IS NULL OR longitude IS NULL)
        """

        result = self.db_ops.execute_query(query)
        return [dict(row) for row in result.fetchall()]

    def propagate_geocoding_results(self, master_address_id: str) -> int:
        """
        Propagate geocoding results from master address to all its children.

        Args:
            master_address_id: The address_id of the master address

        Returns:
            Number of child addresses updated
        """
        try:
            # Get geocoding data from master address
            master_query = """
                SELECT latitude, longitude, colocator
                FROM Addresses
                WHERE address_id = ? AND master_id IS NULL
            """
            result = self.db_ops.execute_query(master_query, (master_address_id,))
            master_data = result.fetchone()

            if not master_data:
                self.log_warning(f"No master address found with ID: {master_address_id}")
                return 0

            latitude = getattr(master_data, 'latitude', None)
            longitude = getattr(master_data, 'longitude', None)
            colocator = getattr(master_data, 'colocator', None)

            if latitude is None or longitude is None:
                self.log_debug(f"Master address {master_address_id} has no geocoding data to propagate")
                return 0

            # Update all child addresses
            update_query = """
                UPDATE Addresses
                SET latitude = ?, longitude = ?, colocator = ?
                WHERE master_id = ?
            """
            self.db_ops.execute_query(update_query, (latitude, longitude, colocator, master_address_id))

            # Also update related records (Charities, Grants, etc.) that reference these addresses
            updated_count = self._propagate_to_related_records(master_address_id, latitude, longitude, colocator or "")

            self.db_ops.commit()

            result = self.db_ops.execute_query("SELECT COUNT(*) as count FROM Addresses WHERE master_id = ?", (master_address_id,))
            child_count = getattr(result.fetchone(), 'count', 0) if result.fetchone() else 0

            self.log_debug(f"Propagated geocoding results from master {master_address_id} to {child_count} children and {updated_count} related records")
            return child_count

        except Exception as e:
            self.log_error(f"Failed to propagate geocoding results for master {master_address_id}: {e}")
            return 0

    def _propagate_to_related_records(self, master_address_id: str, latitude: float, longitude: float, colocator: str) -> int:
        """Propagate geocoding results to related records (Charities, Grants, etc.)"""
        total_updated = 0

        try:
            # Update Charities that reference addresses with this master_id
            charity_query = """
                UPDATE Charities
                SET colocator = ?
                WHERE colocator IS NULL
                AND ein IN (
                    SELECT ein FROM Addresses WHERE master_id = ? OR address_id = ?
                )
            """
            result = self.db_ops.execute_query(charity_query, (colocator, master_address_id, master_address_id))
            # Update Grants that reference addresses with this master_id
            grant_query = """
                UPDATE Grants
                SET colocator = ?
                WHERE colocator IS NULL
                AND (filer_ein, tax_year) IN (
                    SELECT ein, tax_year FROM Addresses WHERE master_id = ? OR address_id = ?
                )
            """
            result = self.db_ops.execute_query(grant_query, (colocator, master_address_id, master_address_id))
            total_updated += getattr(result, 'rowcount', 0)

            # Update Contractors that reference addresses with this master_id
            contractor_query = """
                UPDATE Contractors
                SET colocator = ?
                WHERE colocator IS NULL
                AND filer_ein IN (
                    SELECT ein FROM Addresses WHERE master_id = ? OR address_id = ?
                )
            """
            result = self.db_ops.execute_query(contractor_query, (colocator, master_address_id, master_address_id))
            total_updated += getattr(result, 'rowcount', 0)

            # Update PoliticalContributions that reference addresses with this master_id
            political_query = """
                UPDATE PoliticalContributions
                SET colocator = ?
                WHERE colocator IS NULL
                AND filer_ein IN (
                    SELECT ein FROM Addresses WHERE master_id = ? OR address_id = ?
                )
            """
            result = self.db_ops.execute_query(political_query, (colocator, master_address_id, master_address_id))
            total_updated += getattr(result, 'rowcount', 0)

        except Exception as e:
            self.log_error(f"Failed to propagate geocoding to related records: {e}")

        return total_updated