#!/usr/bin/env python3
"""
models/political_contribution.py - Political contribution data model

This module contains the PoliticalContribution dataclass and related business logic.
Political contributions represent payments made to political organizations.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PoliticalContribution:
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

    def is_large_contribution(self) -> bool:
        """Check if contribution exceeds $10,000"""
        return self.amount > 10000.0

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
            'colocator': self.colocator
        }