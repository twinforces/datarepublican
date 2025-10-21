#!/usr/bin/env python3
"""
models/grant.py - Grant data model

This module contains the Grant dataclass and related business logic.
Grants represent charitable contributions made by filers to other organizations.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Grant:
    """Represents a charitable grant from one organization to another"""

    grant_id: Optional[int] = None
    filer_ein: str = ""
    filer_name: str = ""
    grant_ein: Optional[str] = None
    grant_amt: float = 0.0
    tax_year: int = 0
    filer_colocator: Optional[str] = None
    grantee_colocator: Optional[str] = None

    def is_large_grant(self) -> bool:
        """Check if grant amount exceeds $100,000"""
        return self.grant_amt > 100000

    def is_foreign_grant(self) -> bool:
        """Check if grant is to a foreign recipient"""
        return self.grant_ein.startswith('99') or self.grant_ein == '999'

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'grant_id': self.grant_id,
            'filer_ein': self.filer_ein,
            'filer_name': self.filer_name,
            'grant_ein': self.grant_ein,
            'grant_amt': self.grant_amt,
            'tax_year': self.tax_year,
            'filer_colocator': self.filer_colocator,
            'grantee_colocator': self.grantee_colocator
        }