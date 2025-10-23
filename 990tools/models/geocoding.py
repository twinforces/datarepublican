#!/usr/bin/env python3
"""
models/geocoding.py - Geocoding data model

This module contains the Geocoding dataclass and related business logic.
Geocoding represents cached geocoding results for addresses.
"""

from dataclasses import dataclass, field
from typing import Optional
from uuid7 import generate_uuid_v7


@dataclass
class Geocoding:
    """Represents cached geocoding results for an address"""

    geocoding_id: Optional[str] = field(default=None, init=False)
    address_hash: str = ""
    normalized_address: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geocoding_status: str = "pending"
    last_attempt: Optional[str] = None
    attempt_count: int = 0

    def is_successful(self) -> bool:
        """Check if geocoding was successful"""
        return self.geocoding_status == "success"

    def is_failed(self) -> bool:
        """Check if geocoding failed"""
        return self.geocoding_status == "failed"

    def is_pending(self) -> bool:
        """Check if geocoding is pending"""
        return self.geocoding_status == "pending"

    def increment_attempts(self):
        """Increment the attempt count"""
        self.attempt_count += 1

    @property
    def id(self) -> str:
        """Get the primary key, creating it if necessary"""
        if self.geocoding_id is None:
            self.geocoding_id = generate_uuid_v7()
        return self.geocoding_id

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'geocoding_id': self.geocoding_id,
            'address_hash': self.address_hash,
            'normalized_address': self.normalized_address,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'geocoding_status': self.geocoding_status,
            'last_attempt': self.last_attempt,
            'attempt_count': self.attempt_count
        }