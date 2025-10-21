#!/usr/bin/env python3
"""
models/officer.py - Officer data model

This module contains the Officer dataclass and related business logic.
Officers represent key personnel of charities with compensation information.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Officer:
    """Represents a charity officer with compensation information"""

    officer_id: Optional[int] = None
    charity_id: int = 0
    first_name: str = ""
    last_name: str = ""
    compensation: float = 0.0
    tax_year: int = 0

    @property
    def full_name(self) -> str:
        """Get full name of the officer"""
        return f"{self.first_name} {self.last_name}".strip()

    def is_highly_compensated(self) -> bool:
        """Check if officer compensation exceeds $200,000"""
        return self.compensation > 200000.0

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'officer_id': self.officer_id,
            'charity_id': self.charity_id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'compensation': self.compensation,
            'tax_year': self.tax_year
        }