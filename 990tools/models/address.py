#!/usr/bin/env python3
"""
models/address.py - Address data model

This module contains the Address dataclass and related business logic.
Addresses represent physical locations associated with charities and other entities.

Colocator Format:
  LL:<lat>:<long>     - Latitude and longitude of the address, starts empty until geocoded
  EIN:<EIN>           - Used for grants that provide an EIN so we don't need to geolocate an Address
  PO:<po_box>:<zip5> - Used for addresses that are PO Boxes. No need to geolocate, it would be the post office anyways
  FA:<countrycode>    - Used for foreign addresses

This allows downstream database joins by location.
"""

from dataclasses import dataclass, field
from typing import Optional
from constants import VALID_STATES, PO_BOX_REGEX


@dataclass
class Address:
    """Represents a physical address with geocoding information"""

    address_id: Optional[int] = None
    ein: str = ""
    name: str = ""
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None  # First 5 digits
    zip4: Optional[str] = None  # Last 4 digits
    po_box: Optional[str] = None
    address_type: str = "filer"  # filer, grant_recipient, etc.
    owner_id: Optional[str] = None  # Loose relationship to owning entity (EIN or UUID)
    geocoding_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    colocator: Optional[str] = None

    @property
    def canonical_address(self) -> Optional[str]:
        """Generate canonical address string for geocoding"""
        if not self.address_line1 or not self.city or not self.state:
            return None

        parts = [self.address_line1]
        if self.address_line2:
            parts.append(self.address_line2)
        parts.extend([self.city, self.state])
        if self.zip_code:
            parts.append(self.zip_code[:5])  # First 5 digits only
            if self.zip4:
                parts[-1] += f"-{self.zip4}"

        return ", ".join(parts)

    @property
    def po_box_number(self) -> Optional[str]:
        """Extract PO Box number from address_line1"""
        if not self.address_line1:
            return None

        match = PO_BOX_REGEX.search(self.address_line1.upper())
        if match:
            return match.group(1)
        return None

    @po_box_number.setter
    def po_box_number(self, value: Optional[str]):
        """Set PO Box number (for backward compatibility)"""
        self.po_box = value

    def is_valid_state(self) -> bool:
        """Check if state is valid"""
        return self.state in VALID_STATES if self.state else False

    def is_po_box(self) -> bool:
        """Check if this is a PO Box address"""
        return self.po_box_number is not None or (self.address_line1 and "PO BOX" in self.address_line1.upper())

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'address_id': self.address_id,
            'ein': self.ein,
            'name': self.name,
            'address_line1': self.address_line1,
            'address_line2': self.address_line2,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'zip4': self.zip4,
            'po_box': self.po_box,
            'address_type': self.address_type,
            'owner_id': self.owner_id,
            'geocoding_id': self.geocoding_id,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'colocator': self.colocator
        }