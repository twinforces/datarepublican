#!/usr/bin/env python3
"""
models/backfill.py - Backfill data model

This module contains the Backfill dataclass and related business logic.
Backfill represents additional grantee data for unknown EINs.
"""

from dataclasses import dataclass, field
from typing import Optional
from uuid7 import generate_uuid_v7


@dataclass
class Backfill:
    """Represents backfill data for unknown grantee EINs"""

    backfill_id: Optional[str] = field(default=None, init=False)
    grant_ein: str = ""
    name: str = ""
    canonical_address: Optional[str] = None
    po_box: Optional[str] = None
    zip_code: Optional[str] = None
    source: str = "xml"

    def has_address_info(self) -> bool:
        """Check if backfill has address information"""
        return self.canonical_address is not None or self.zip_code is not None

    def is_po_box(self) -> bool:
        """Check if this is a PO Box address"""
        return self.po_box is not None

    def prep_for_insert(self):
        """Prepare the record for database insertion"""
        super().prep_for_insert()
        pass

    @property
    def id(self) -> str:
        """Get the primary key, creating it if necessary"""
        if self.backfill_id is None:
            self.backfill_id = generate_uuid_v7()
        return self.backfill_id

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'backfill_id': self.backfill_id,
            'grant_ein': self.grant_ein,
            'name': self.name,
            'canonical_address': self.canonical_address,
            'po_box': self.po_box,
            'zip_code': self.zip_code,
            'source': self.source
        }