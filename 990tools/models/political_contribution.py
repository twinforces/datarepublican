#!/usr/bin/env python3
"""
models/political_contribution.py - Political contribution data model

This module contains the PoliticalContribution dataclass and related business logic.
Political contributions represent payments made to political organizations.
"""

from dataclasses import dataclass
from typing import Optional
from .base import BaseModel
from .address import Address


@dataclass
class PoliticalContribution(BaseModel):
    """Represents a political contribution made by a charity"""

    political_id: Optional[int] = None
    filer_ein: str = ""
    recipient: str = ""
    amount: float = 0.0
    recipient_address: Optional[str] = None
    recipient_zip: Optional[str] = None
    recipient_po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None
    created_at: Optional[str] = None

    def is_large_contribution(self) -> bool:
        """Check if contribution exceeds $10,000"""
        return self.amount > 10000.0

    def build_address(self, address_line1: Optional[str] = None, address_line2: Optional[str] = None,
                     city: Optional[str] = None, state: Optional[str] = None, zip_code: Optional[str] = None,
                     zip4: Optional[str] = None) -> Address:
        """Build an Address dataclass record owned by this political contribution"""
        address = Address(
            ein="",  # Political contributions may not have EINs
            name=self.recipient,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            zip_code=zip_code,
            zip4=zip4,
            address_type="politicalcontribution",
            owner_id=str(self.political_id) if self.political_id is not None else None  # Convert to string
        )
        return address

    def prep_for_insert(self):
        """Prepare the record for database insertion"""
        super().prep_for_insert()
        pass

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'political_id': self.political_id,
            'filer_ein': self.filer_ein,
            'recipient': self.recipient,
            'amount': self.amount,
            'recipient_address': self.recipient_address,
            'recipient_zip': self.recipient_zip,
            'recipient_po_box': self.recipient_po_box,
            'tax_year': self.tax_year,
            'colocator': self.colocator,
            'created_at': self.created_at
        }