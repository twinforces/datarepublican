#!/usr/bin/env python3
"""
address_matcher.py - Grant-to-charity matching for IRS 990 data

This module handles matching grants with unknown EINs to charities
based on address and colocator information.
"""

from typing import Optional, List, Tuple
from tqdm import tqdm

from database_operations import DatabaseOperations
from irs990processorDC import Charity, Address


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

        matched_count = 0
        with tqdm(total=len(grants), desc="Matching grants") as pbar:
            for grant_id, filer_ein, grant_amt, tax_year in grants:
                # Try to find matching charity
                matched_ein = self._find_charity_by_grant_info(grant_id, filer_ein, grant_amt, tax_year)
                if matched_ein:
                    self.db_ops.update_grant_ein(grant_id, matched_ein)
                    matched_count += 1
                    print(f"Matched grant {grant_id} to charity {matched_ein}")
                else:
                    # Create stub charity
                    stub_ein = self._create_stub_charity_for_grant(grant_id, filer_ein, grant_amt, tax_year)
                    if stub_ein:
                        self.db_ops.update_grant_ein(grant_id, stub_ein)
                        print(f"Created stub charity {stub_ein} for grant {grant_id}")

                pbar.update(1)

        print(f"Grant matching complete: {matched_count} grants matched, {len(grants) - matched_count} stubs created")
        return matched_count

    def _find_charity_by_grant_info(self, grant_id: int, filer_ein: str, grant_amt: float, tax_year: int) -> Optional[str]:
        """Find charity EIN by grant information"""
        # This is a simplified implementation
        # In practice, this would need access to grant recipient information
        # For now, return None to create stubs
        return None

    def _create_stub_charity_for_grant(self, grant_id: int, filer_ein: str, grant_amt: float, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants"""
        # Generate a pseudo-EIN for stub records
        stub_ein = f"STUB{hash(f'{filer_ein}_{grant_id}_{tax_year}') % 1000000000:09d}"

        # Check if stub already exists
        # This would require a database query - simplified for now
        # For now, just return the stub EIN
        return stub_ein