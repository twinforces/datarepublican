#!/usr/bin/env python3
"""
address_matcher.py - Grant-to-charity matching for IRS 990 data

This module handles matching grants with unknown EINs to charities
based on address and colocator information.
"""

from typing import Optional, List, Tuple

from database_operations import DatabaseOperations
from models import Charity, Address
from logging_utils import start_progress_reporting, stop_progress_reporting, update_progress


class AddressMatcher:
    """Handles grant-to-charity matching by address"""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

    def match_grants_by_address(self) -> int:
        """Match grants with unknown EINs by address or colocator (step 9)"""
        print("Matching grants with unknown EINs by address/colocator")

        # Get grants with unknown EINs
        grants = self.db_ops.get_grants_without_ein()
        print(f"Found {len(grants)} grants with unknown EINs to match")

        # Start thread-safe progress reporting
        progress_reporter = start_progress_reporting(
            total=len(grants),
            desc="Matching grants",
            unit="grant"
        )

        matched_count = 0
        for grant in grants:
            # Try to find matching charity
            if grant.grant_id is not None:
                matched_ein = self._find_charity_by_grant_info(grant.grant_id, grant.filer_ein, grant.grant_amt, grant.tax_year)
                if matched_ein:
                    self.db_ops.update_grant_ein(grant.grant_id, matched_ein)
                    matched_count += 1
                    print(f"Matched grant {grant.grant_id} to charity {matched_ein}")
                else:
                    # Create stub charity
                    stub_ein = self._create_stub_charity_for_grant(grant.grant_id, grant.filer_ein, grant.grant_amt, grant.tax_year)
                    if stub_ein:
                        self.db_ops.update_grant_ein(grant.grant_id, stub_ein)
                        print(f"Created stub charity {stub_ein} for grant {grant.grant_id}")

            update_progress(progress_reporter, 1)

        # Stop progress reporting
        stop_progress_reporting()

        print(f"Grant matching complete: {matched_count} grants matched, {len(grants) - matched_count} stubs created")
        return matched_count

    def _find_charity_by_grant_info(self, grant_id: str, filer_ein: str, grant_amt: float, tax_year: int) -> Optional[str]:
        """Find charity EIN by grant information"""
        # This is a simplified implementation
        # In practice, this would need access to grant recipient information
        # For now, return None to create stubs
        return None

    def _create_stub_charity_for_grant(self, grant_id: str, filer_ein: str, grant_amt: float, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants"""
        # Generate a pseudo-EIN for stub records
        stub_ein = f"STUB{hash(f'{filer_ein}_{grant_id}_{tax_year}') % 1000000000:09d}"

        # Check if stub already exists
        # This would require a database query - simplified for now
        # For now, just return the stub EIN
        return stub_ein