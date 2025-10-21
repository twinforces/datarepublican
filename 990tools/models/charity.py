#!/usr/bin/env python3
"""
models/charity.py - Charity data model

This module contains the Charity dataclass and related business logic.
Charities represent the main IRS 990 filers and their financial information.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Charity:
    """Represents a charity organization and its IRS 990 filing data"""

    charity_id: Optional[int] = None
    ein: str = ""
    tax_year: int = 0
    filer_name: str = ""
    receipt_amt: Optional[float] = None
    govt_amt: Optional[float] = None
    contrib_amt: Optional[float] = None
    org_type: str = ""
    total_exp: Optional[float] = None
    prog_exp: Optional[float] = None
    travel_amt: Optional[float] = None
    conferences_amt: Optional[float] = None
    officer_comp: Optional[float] = None
    comp_pct: Optional[float] = None
    comp_ptile: Optional[float] = None
    travel_pct: Optional[float] = None
    travel_ptile: Optional[float] = None
    conferences_pct: Optional[float] = None
    conferences_ptile: Optional[float] = None
    grants_pct: Optional[float] = None
    grants_ptile: Optional[float] = None
    foreign_expenses_pct: Optional[float] = None
    foreign_expenses_ptile: Optional[float] = None
    grift_ratio: Optional[float] = None
    total_assets: Optional[float] = None
    form_type: str = ""
    denominator: Optional[float] = None
    foreign_office: bool = False
    foreign_expenses: Optional[float] = None
    grants_to_others: Optional[float] = None
    domestic_misrep_flag: bool = False
    xml_name: str = ""
    colocator: Optional[str] = None

    def is_501c3(self) -> bool:
        """Check if this is a 501(c)(3) organization"""
        return self.org_type == "501(c)(3)" if self.org_type else False

    def has_foreign_operations(self) -> bool:
        """Check if charity has foreign operations"""
        return self.foreign_office or (self.foreign_expenses and self.foreign_expenses > 0)

    def calculate_grift_ratio(self) -> float:
        """Calculate grift ratio (officer comp + travel + conferences) / total expenses"""
        if not self.total_exp or self.total_exp == 0:
            return 0.0

        grift_expenses = (self.officer_comp or 0) + (self.travel_amt or 0) + (self.conferences_amt or 0)
        return round((grift_expenses / self.total_exp) * 100, 2)

    def is_high_grift_risk(self) -> bool:
        """Check if charity has high grift risk indicators"""
        return self.grift_ratio and self.grift_ratio > 10

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'charity_id': self.charity_id,
            'ein': self.ein,
            'tax_year': self.tax_year,
            'filer_name': self.filer_name,
            'receipt_amt': self.receipt_amt,
            'govt_amt': self.govt_amt,
            'contrib_amt': self.contrib_amt,
            'org_type': self.org_type,
            'total_exp': self.total_exp,
            'prog_exp': self.prog_exp,
            'travel_amt': self.travel_amt,
            'conferences_amt': self.conferences_amt,
            'officer_comp': self.officer_comp,
            'comp_pct': self.comp_pct,
            'comp_ptile': self.comp_ptile,
            'travel_pct': self.travel_pct,
            'travel_ptile': self.travel_ptile,
            'conferences_pct': self.conferences_pct,
            'conferences_ptile': self.conferences_ptile,
            'grants_pct': self.grants_pct,
            'grants_ptile': self.grants_ptile,
            'foreign_expenses_pct': self.foreign_expenses_pct,
            'foreign_expenses_ptile': self.foreign_expenses_ptile,
            'grift_ratio': self.grift_ratio,
            'total_assets': self.total_assets,
            'form_type': self.form_type,
            'denominator': self.denominator,
            'foreign_office': self.foreign_office,
            'foreign_expenses': self.foreign_expenses,
            'grants_to_others': self.grants_to_others,
            'domestic_misrep_flag': self.domestic_misrep_flag,
            'xml_name': self.xml_name,
            'colocator': self.colocator
        }