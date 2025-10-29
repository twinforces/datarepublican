#!/usr/bin/env python3
"""
officer_deduplication_processor.py - Officer deduplication for IRS 990 data processing

This module handles deduplication of officers across different charities and tax years,
creating master-child relationships similar to address deduplication.
"""

import logging
from typing import List, Tuple, Dict, Set
from database_operations import DatabaseOperations
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning


class OfficerDeduplicationProcessor:
    """Handles officer deduplication and master-child relationship creation"""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops
        self.logger = get_logger("officer_deduplication")

    def deduplicate_officers(self) -> int:
        """Deduplicate officers and create master-child relationships"""
        log_info(self.logger, "Starting officer deduplication")

        # Step 1: Find duplicate officers by name
        duplicates = self._find_duplicate_officers()
        if not duplicates:
            log_info(self.logger, "No duplicate officers found")
            return 0

        log_info(self.logger, f"Found {len(duplicates)} groups of duplicate officers")

        # Step 2: Create master records and update child records
        updated_count = 0
        for name_key, officer_ids in duplicates.items():
            master_id = self._create_master_officer(officer_ids)
            if master_id:
                updated_count += self._update_child_officers(officer_ids, master_id)

        log_info(self.logger, f"Officer deduplication complete. Updated {updated_count} officer records.")
        return updated_count

    def _find_duplicate_officers(self) -> Dict[str, List[str]]:
        """Find officers with the same name across different charities/years"""
        # First, we need to get charity names for officers
        query = """
            SELECT
                o.officer_id,
                LOWER(TRIM(o.first_name)) || '|' || LOWER(TRIM(o.last_name)) || '|' || LOWER(TRIM(c.filer_name)) as name_key
            FROM Officers o
            JOIN Charities c ON o.charity_id = c.charity_id
            WHERE o.master_id IS NULL
        """

        result = self.db_ops.execute_query(query).fetchall()

        # Group by name_key
        name_groups = {}
        for officer_id, name_key in result:
            if name_key not in name_groups:
                name_groups[name_key] = []
            name_groups[name_key].append(str(officer_id))

        # Only keep groups with multiple officers
        duplicates = {k: v for k, v in name_groups.items() if len(v) > 1}

        return duplicates

    def _create_master_officer(self, officer_ids: List[str]) -> str:
        """Create a master officer record from the first officer in the group"""
        # Use the first officer as the master
        master_officer_id = officer_ids[0]

        # Generate a new master_id (UUID)
        import uuid
        master_id = str(uuid.uuid4())

        # Update the master officer record
        update_query = """
            UPDATE Officers
            SET master_id = ?
            WHERE officer_id = ?
        """
        self.db_ops.execute_query(update_query, (master_id, master_officer_id))

        log_debug(self.logger, f"Created master officer {master_id} from officer {master_officer_id}")
        return master_id

    def _update_child_officers(self, officer_ids: List[str], master_id: str) -> int:
        """Update child officers to point to the master"""
        # Skip the first officer (already set as master)
        child_ids = officer_ids[1:]

        if not child_ids:
            return 0

        # Update all child officers
        placeholders = ','.join('?' for _ in child_ids)
        update_query = f"""
            UPDATE Officers
            SET master_id = ?
            WHERE officer_id IN ({placeholders})
        """

        self.db_ops.execute_query(update_query, tuple([master_id] + child_ids))
        self.db_ops.commit()

        log_debug(self.logger, f"Updated {len(child_ids)} child officers to point to master {master_id}")
        return len(child_ids)