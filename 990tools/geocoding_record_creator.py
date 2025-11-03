#!/usr/bin/env python3
"""
geocoding_record_creator.py - Geocoding record creation for Phase 1

This module handles the creation of geocoding records during address deduplication.
It integrates with the address deduplication process to create geocoding records
for addresses that need geocoding, separating the fast database operations from
the slow API calls.
"""

import logging
from typing import List, Dict, Any, Optional
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models import Address
from models.geocoding import Geocoding
from logging_utils import log_info, log_error, log_debug, log_warning, get_logger
from config import global_config


class GeocodingRecordCreator:
    """
    Creates geocoding records for addresses that need geocoding.

    This class is integrated into the address deduplication process to create
    geocoding records for addresses that don't have them yet. It performs
    PO Box detection and creates initial geocoding records with normalized
    addresses for later API processing.
    """

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops
        self.logger = get_logger("geocoding_record_creator")

    def create_geocoding_records_for_addresses(self, addresses: List[Address]) -> Dict[str, Any]:
        """
        Create geocoding records for addresses that need them.

        Args:
            addresses: List of Address objects that may need geocoding records

        Returns:
            Dictionary containing:
            - 'geocoding_records': List of Geocoding objects to insert
            - 'address_updates': List of address update dictionaries for bulk_update
            - 'progress_count': Number of addresses processed
        """
        geocoding_records = []
        address_updates = []

        if not addresses:
            log_debug(self.logger, f"DEBUG: No addresses provided to create_geocoding_records_for_addresses")
            return {
                'geocoding_records': geocoding_records,
                'address_updates': address_updates,
                'progress_count': 0
            }

        log_debug(self.logger, f"DEBUG: Starting geocoding record creation for {len(addresses)} addresses")

        # DEBUG: Log what addresses we're processing
        for addr in addresses[:5]:  # Log first 5 addresses
            log_debug(self.logger, f"DEBUG: Processing address {addr.address_id}: geocoding_id={addr.geocoding_id}, canonical='{addr.canonical_address[:50] if addr.canonical_address else None}'")
        if len(addresses) > 5:
            log_debug(self.logger, f"DEBUG: ... and {len(addresses) - 5} more addresses")

        # Process addresses sequentially
        geocoding_records_created = 0
        address_updates_created = 0

        for address in addresses:
            # Check if address already has a geocoding record
            if address.geocoding_id:
                log_debug(self.logger, f"DEBUG: Address {address.address_id} already has geocoding_id {address.geocoding_id}, skipping")
                continue

            # Validate address has required fields
            if not address.canonical_address:
                log_debug(self.logger, f"DEBUG: Address {address.address_id} missing canonical_address, skipping")
                continue

            # Check for PO Box detection
            if address.is_po_box():
                # Create PO Box update
                log_debug(self.logger, f"DEBUG: Address {address.address_id} detected as PO Box: {address.po_box}")
                log_debug(self.logger, f"DEBUG: Creating PO Box update for address {address.address_id}")

                address_updates.append({
                    'address_id': address.address_id,
                    'po_box': address.po_box,
                    'colocator': f"PO:{address.po_box}:{address.zip_code or ''}"
                })
                address_updates_created += 1
            else:
                # Create geocoding record for API processing
                log_debug(self.logger, f"DEBUG: Creating geocoding record for address {address.address_id}")
                geocoding_op = address.create_geocoding_operation()

                # Validate the geocoding operation was created properly
                if not geocoding_op or not geocoding_op.data:
                    log_debug(self.logger, f"DEBUG: Failed to create geocoding operation for address {address.address_id}")
                    continue

                # Validate operation data integrity
                if 'geocoding' not in geocoding_op.data or 'address_id' not in geocoding_op.data:
                    log_debug(self.logger, f"DEBUG: Geocoding operation missing required keys for address {address.address_id}")
                    continue

                geocoding_obj = geocoding_op.data['geocoding']
                log_debug(self.logger, f"DEBUG: Geocoding record created for address {address.address_id}: normalized_address='{geocoding_obj.normalized_address[:50]}'")

                geocoding_records.append(geocoding_obj)
                geocoding_records_created += 1

                # Create address update to link geocoding_id after insertion
                address_updates.append({
                    'address_id': address.address_id,
                    'geocoding_id': geocoding_obj.geocoding_id  # Will be set after bulk insert
                })
                address_updates_created += 1
                log_debug(self.logger, f"DEBUG: Created address update to link geocoding_id to address {address.address_id}")

        progress_count = geocoding_records_created + address_updates_created

        log_debug(self.logger, f"DEBUG: Batch processing complete - {geocoding_records_created} geocoding records, {address_updates_created} address updates, total progress: {progress_count}")

        # DEBUG: Log records being created
        for i, geocoding in enumerate(geocoding_records[:3]):  # Log first 3 geocoding records
            log_debug(self.logger, f"DEBUG: Created geocoding record {i+1}: geocoding_id={geocoding.geocoding_id}, normalized_address='{geocoding.normalized_address[:50]}'")
        if len(geocoding_records) > 3:
            log_debug(self.logger, f"DEBUG: ... and {len(geocoding_records) - 3} more geocoding records")

        return {
            'geocoding_records': geocoding_records,
            'address_updates': address_updates,
            'progress_count': progress_count
        }

    def get_addresses_needing_geocoding_records(self, limit: Optional[int] = None) -> List[Address]:
        """
        Get addresses that need geocoding records created.

        Args:
            limit: Maximum number of addresses to return

        Returns:
            List of Address objects that need geocoding records
        """
        # Get addresses that need geocoding (no geocoding_id and not PO Box)
        addresses = self.db_ops.get_addresses_for_geocoding(limit=limit)

        if not global_config.is_quiet():
            log_debug(self.logger, f"PHASE 1: Retrieved {len(addresses)} addresses from get_addresses_for_geocoding")

        return addresses