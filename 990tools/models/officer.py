#!/usr/bin/env python3
"""
models/officer.py - Officer data model

This module contains the Officer dataclass and related business logic.
Officers represent key personnel of charities with compensation information.
"""

from dataclasses import dataclass
from typing import Optional
from .base import BaseModel


@dataclass
class Officer(BaseModel):
    """Represents a charity officer with compensation information"""

    officer_id: Optional[int] = None
    charity_id: int = 0
    master_id: Optional[str] = None
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    compensation: float = 0.0
    tax_year: int = 0
    photo_url: Optional[str] = None
    created_at: Optional[str] = None

    @property
    def display_name(self) -> str:
        """Get display name of the officer (computed from first/last if full_name not set)"""
        if self.full_name:
            return self.full_name
        return f"{self.first_name} {self.last_name}".strip()

    def is_highly_compensated(self) -> bool:
        """Check if officer compensation exceeds $200,000"""
        return self.compensation > 200000.0

    def prep_for_insert(self):
        """Prepare the record for database insertion"""
        super().prep_for_insert()
        pass

    @classmethod
    def create_for_charity(cls, charity, first_name: str, last_name: str, full_name: str, compensation: float, tax_year: int) -> 'Officer':
        """Factory method to create an Officer for a specific charity"""
        return cls(
            charity_id=charity.charity_id,
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            compensation=compensation,
            tax_year=tax_year
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'officer_id': self.officer_id,
            'charity_id': self.charity_id,
            'master_id': self.master_id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': self.full_name,
            'compensation': self.compensation,
            'tax_year': self.tax_year,
            'photo_url': self.photo_url,
            'created_at': self.created_at
        }