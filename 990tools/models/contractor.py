#!/usr/bin/env python3
"""
models/contractor.py - Contractor data model

This module contains the Contractor dataclass and related business logic.
Contractors represent independent contractors paid by charities.
"""

from dataclasses import dataclass
from typing import Optional
from models.address import Address


@dataclass
class Contractor:
    """Represents an independent contractor paid by a charity"""

    contractor_id: Optional[int] = None
    filer_ein: str = ""
    name: str = ""
    amount: float = 0.0
    ein: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None

    def is_highly_compensated(self) -> bool:
        """Check if contractor compensation exceeds $100,000"""
        return self.amount > 100000.0

    def build_address(self, address_line1: Optional[str] = None, address_line2: Optional[str] = None,
                     city: Optional[str] = None, state: Optional[str] = None, zip_code: Optional[str] = None) -> Address:
        """Build an Address dataclass record owned by this contractor"""
        address = Address(
            ein=self.ein,
            name=self.name,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            zip_code=zip_code,
            address_type="contractor",
            owner_id=self.contractor_id  # Use the integer ID for contractors
        )
        return address

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'contractor_id': self.contractor_id,
            'filer_ein': self.filer_ein,
            'name': self.name,
            'amount': self.amount,
            'ein': self.ein,
            'address': self.address,
            'zip_code': self.zip_code,
            'po_box': self.po_box,
            'tax_year': self.tax_year,
            'colocator': self.colocator
        }