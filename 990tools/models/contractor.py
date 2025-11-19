#!/usr/bin/env python3
"""
models/contractor.py - Contractor data model

This module contains the Contractor dataclass and related business logic.
Contractors represent independent contractors paid by charities.
"""

from dataclasses import dataclass
from typing import Optional
from .base import BaseModel
from .address import Address


@dataclass
class Contractor(BaseModel):
    """Represents an independent contractor paid by a charity"""

    contractor_id: Optional[int] = None
    charity_id: Optional[str] = None
    filer_ein: str = ""
    name: str = ""
    amount: float = 0.0
    ein: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None
    created_at: Optional[str] = None

    def is_highly_compensated(self) -> bool:
        """Check if contractor compensation exceeds $100,000"""
        return self.amount > 100000.0

    def build_address(self, address_line1: Optional[str] = None, address_line2: Optional[str] = None,
                     city: Optional[str] = None, state: Optional[str] = None, zip_code: Optional[str] = None,
                     zip4: Optional[str] = None) -> Address:
        """Build an Address dataclass record owned by this contractor"""
        address = Address(
            ein=self.ein or "",
            name=self.name,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            zip_code=zip_code,
            zip4=zip4,
            address_type="contractor",
            owner_id=str(self.contractor_id) if self.contractor_id is not None else None  # Convert to string
        )
        address.prep_for_insert()
        if address.colocator: self.colocator = address.colocator
        return address

    def prep_for_insert(self):
        """Prepare the record for database insertion"""
        super().prep_for_insert()
        pass

    @classmethod
    def create_for_charity(cls, charity, name: str, amount: float, ein: Optional[str], tax_year: int) -> 'Contractor':
        """Factory method to create a Contractor for a specific charity"""
        return cls(
            filer_ein=charity.ein,
            name=name,
            amount=amount,
            ein=ein,
            tax_year=tax_year
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'contractor_id': self.contractor_id,
            'charity_id': self.charity_id,
            'filer_ein': self.filer_ein,
            'name': self.name,
            'amount': self.amount,
            'ein': self.ein,
            'tax_year': self.tax_year,
            'colocator': self.colocator,
            'created_at': self.created_at
        }