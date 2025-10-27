#!/usr/bin/env python3
"""
models/charity.py - Charity data model

This module contains the Charity dataclass and related business logic.
Charities represent the main IRS 990 filers and their financial information.
"""

from dataclasses import dataclass, field
from typing import Optional
from uuid7 import generate_uuid_v7
from .base import BaseModel
from .address import Address
from .grant import Grant
from .contractor import Contractor
from .political_contribution import PoliticalContribution


@dataclass
class Charity(BaseModel):
    """Represents a charity organization and its IRS 990 filing data"""

    charity_id: Optional[str] = field(default=None, init=False)
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
    comp_ptile_value: Optional[float] = None
    conferences_ptile_value: Optional[float] = None
    created_at: Optional[str] = None
    foreign_expenses_ptile_value: Optional[float] = None
    grants_ptile_value: Optional[float] = None
    grift: Optional[float] = None
    travel_ptile_value: Optional[float] = None
    updated_at: Optional[str] = None

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

    def build_address(self, address_line1: Optional[str] = None, address_line2: Optional[str] = None,
                     city: Optional[str] = None, state: Optional[str] = None, zip_code: Optional[str] = None,
                     zip4: Optional[str] = None) -> Address:
        """Build an Address dataclass record owned by this charity"""
        address = Address(
            ein=self.ein,
            name=self.filer_name or "Unknown",
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            zip_code=zip_code,
            zip4=zip4,
            address_type="charity",
            owner_id=self.id  # This ensures owner_id is always set since self.id creates the primary key if needed
        )
        return address

    def build_grant(self, grant_ein: Optional[str] = None, grant_amt: float = 0.0,
                    grantee_name: Optional[str] = None) -> Grant:
        """Build a Grant dataclass record owned by this charity"""
        grant = Grant(
            filer_ein=self.ein,
            filer_name=self.filer_name or "Unknown",
            grant_ein=grant_ein,
            grant_amt=grant_amt,
            tax_year=self.tax_year,
            grantee_name=grantee_name
        )
        return grant

    def build_contractor(self, name: str = "", amount: float = 0.0, ein: Optional[str] = None) -> Contractor:
        """Build a Contractor dataclass record owned by this charity"""
        contractor = Contractor(
            filer_ein=self.ein,
            name=name,
            amount=amount,
            ein=ein,
            tax_year=self.tax_year
        )
        return contractor

    def build_political_contribution(self, recipient: str = "", amount: float = 0.0) -> PoliticalContribution:
        """Build a PoliticalContribution dataclass record owned by this charity"""
        contribution = PoliticalContribution(
            filer_ein=self.ein,
            recipient=recipient,
            amount=amount,
            tax_year=self.tax_year
        )
        return contribution

    def prep_for_insert(self):
        """Prepare the record for database insertion"""
        #print(f"#### Charity prep_for_insert: xml_name={self.xml_name} (type: {type(self.xml_name)})")
        super().prep_for_insert()
        pass

    @property
    def id(self) -> str:
        """Get the primary key, creating it if necessary"""
        if self.charity_id is None:
            self.charity_id = generate_uuid_v7()
        return self.charity_id

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
            'comp_ptile_value': self.comp_ptile_value,
            'travel_pct': self.travel_pct,
            'travel_ptile': self.travel_ptile,
            'travel_ptile_value': self.travel_ptile_value,
            'conferences_pct': self.conferences_pct,
            'conferences_ptile': self.conferences_ptile,
            'conferences_ptile_value': self.conferences_ptile_value,
            'grants_pct': self.grants_pct,
            'grants_ptile': self.grants_ptile,
            'grants_ptile_value': self.grants_ptile_value,
            'foreign_expenses_pct': self.foreign_expenses_pct,
            'foreign_expenses_ptile': self.foreign_expenses_ptile,
            'foreign_expenses_ptile_value': self.foreign_expenses_ptile_value,
            'grift_ratio': self.grift_ratio,
            'total_assets': self.total_assets,
            'form_type': self.form_type,
            'denominator': self.denominator,
            'foreign_office': self.foreign_office,
            'foreign_expenses': self.foreign_expenses,
            'grants_to_others': self.grants_to_others,
            'domestic_misrep_flag': self.domestic_misrep_flag,
            'xml_name': self.xml_name,
            'colocator': self.colocator,
            'grift': self.grift,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }