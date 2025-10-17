#!/usr/bin/env python3
"""
990processorDC.py - @dataclass classes for Comprehensive IRS 990 data processing module

This module replaces the collection of separate scripts with a unified,
database-driven processing pipeline for IRS Form 990 data.

Key Features:
- Dataclass-based data models for type safety and clarity
- SQLite database storage with proper relationships
- Geolocation using censusgeocode API
- Comprehensive error handling and logging
- Threaded processing for performance
"""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class ZipFile:
    """Represents a ZIP file containing IRS 990 XML files"""
    zip_id: Optional[int] = None
    filename: str = ""
    file_path: str = ""
    tax_year: int = 0
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    download_date: Optional[datetime] = None
    processed_date: Optional[datetime] = None
    status: str = "downloaded"
    xml_files: List['XMLFile'] = field(default_factory=list)

@dataclass
class XMLFile:
    """Represents an individual XML file within a ZIP"""
    xml_id: Optional[int] = None
    zip_id: int = 0
    filename: str = ""
    internal_path: str = ""
    ein: Optional[str] = None
    tax_year: Optional[int] = None
    form_type: Optional[str] = None
    processed: bool = False
    processing_version: int = 0
    error_message: Optional[str] = None

@dataclass
class Address:
    """Represents a physical address"""
    address_id: Optional[int] = None
    ein: str = ""
    name: str = ""
    canonical_address: str = ""
    po_box: Optional[str] = None
    zip_code: Optional[str] = None
    address_type: str = "filer"  # 'filer' or 'grantee'
    geocoding_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    colocator: Optional[str] = None  # LL:lat:long, PO:box:zip, FA:country_code

@dataclass
class Charity:
    """Represents a charity organization"""
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
    # Analysis fields
    comp_ptile_value: Optional[float] = None
    travel_ptile_value: Optional[float] = None
    conferences_ptile_value: Optional[float] = None
    grants_ptile_value: Optional[float] = None
    foreign_expenses_ptile_value: Optional[float] = None

@dataclass
class Grant:
    """Represents a grant from a charity"""
    grant_id: Optional[int] = None
    filer_ein: str = ""
    filer_name: str = ""
    grant_ein: Optional[str] = None
    grant_amt: float = 0.0
    tax_year: int = 0
    grantee_name: Optional[str] = None
    grantee_address: Optional[str] = None
    grantee_zip: Optional[str] = None
    grantee_po_box: Optional[str] = None
    filer_colocator: Optional[str] = None
    grantee_colocator: Optional[str] = None

@dataclass
class Officer:
    """Represents an officer compensation record"""
    officer_id: Optional[int] = None
    charity_id: int = 0
    first_name: str = ""
    last_name: str = ""
    compensation: float = 0.0
    tax_year: int = 0

@dataclass
class Contractor:
    """Represents a contractor payment"""
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

@dataclass
class PoliticalContribution:
    """Represents a political contribution"""
    political_id: Optional[int] = None
    filer_ein: str = ""
    recipient: str = ""
    amount: float = 0.0
    recipient_address: Optional[str] = None
    recipient_zip: Optional[str] = None
    recipient_po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None
