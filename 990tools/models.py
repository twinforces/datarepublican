from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict
from datetime import datetime
import re


@dataclass
class Charity:
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
    foreign_office: Optional[str] = None
    foreign_expenses: Optional[float] = None
    grants_to_others: Optional[float] = None
    domestic_misrep_flag: Optional[str] = None
    xml_name: str = ""
    grift: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_high_grift(self) -> bool:
        return self.grift_ratio is not None and self.grift_ratio > 100

    @property
    def has_foreign_activities(self) -> bool:
        return bool(self.foreign_office or self.foreign_expenses)

    @property
    def grift_amount(self) -> Optional[float]:
        """Calculate grift amount if not directly stored"""
        if self.grift is not None:
            return self.grift
        if self.total_exp and self.prog_exp:
            return self.total_exp - self.prog_exp
        return None

    def to_row(self) -> List[Any]:
        """Convert to TSV row format for backward compatibility"""
        return [
            self.tax_year, self.filer_ein, self.filer_name, self.receipt_amt,
            self.govt_amt, self.contrib_amt, self.org_type, self.total_exp,
            self.prog_exp, self.travel_amt, self.conferences_amt, self.officer_comp,
            self.comp_pct, self.comp_ptile, self.travel_pct, self.travel_ptile,
            self.conferences_pct, self.conferences_ptile, self.grants_pct,
            self.grants_ptile, self.foreign_expenses_pct, self.foreign_expenses_ptile,
            self.grift_ratio, self.total_assets, self.form_type, self.denominator,
            self.foreign_office, self.foreign_expenses, self.grants_to_others,
            self.domestic_misrep_flag, self.xml_name, self.canonical_address,
            self.mailing_zip, self.colocator
        ]

    # Smart setters that handle comma-stripped string parsing
    def set_receipt_amt_from_string(self, value: Optional[str]) -> None:
        self.receipt_amt = self._parse_float_from_string(value)

    def set_govt_amt_from_string(self, value: Optional[str]) -> None:
        self.govt_amt = self._parse_float_from_string(value)

    def set_contrib_amt_from_string(self, value: Optional[str]) -> None:
        self.contrib_amt = self._parse_float_from_string(value)

    def set_total_exp_from_string(self, value: Optional[str]) -> None:
        self.total_exp = self._parse_float_from_string(value)

    def set_prog_exp_from_string(self, value: Optional[str]) -> None:
        self.prog_exp = self._parse_float_from_string(value)

    def set_travel_amt_from_string(self, value: Optional[str]) -> None:
        self.travel_amt = self._parse_float_from_string(value)

    def set_conferences_amt_from_string(self, value: Optional[str]) -> None:
        self.conferences_amt = self._parse_float_from_string(value)

    def set_officer_comp_from_string(self, value: Optional[str]) -> None:
        self.officer_comp = self._parse_float_from_string(value)

    def set_total_assets_from_string(self, value: Optional[str]) -> None:
        self.total_assets = self._parse_float_from_string(value)

    def set_foreign_expenses_from_string(self, value: Optional[str]) -> None:
        self.foreign_expenses = self._parse_float_from_string(value)

    def set_grants_to_others_from_string(self, value: Optional[str]) -> None:
        self.grants_to_others = self._parse_float_from_string(value)

    def set_denominator_from_string(self, value: Optional[str]) -> None:
        self.denominator = self._parse_float_from_string(value)

    @staticmethod
    def _parse_float_from_string(value: Optional[str]) -> Optional[float]:
        """Parse a string to float, stripping commas and handling edge cases."""
        if value in ("n/a", "", None, "nan"):
            return None
        try:
            # Strip commas before parsing
            cleaned_value = str(value).replace(',', '')
            return float(cleaned_value)
        except (ValueError, TypeError):
            return None


@dataclass
class Grant:
    grant_id: Optional[int] = None
    filer_ein: str = ""
    filer_name: str = ""
    grant_ein: Optional[str] = None
    grant_amt: float = 0.0
    tax_year: int = 0
    filer_colocator: Optional[str] = None
    grantee_colocator: Optional[str] = None
    created_at: Optional[datetime] = None

    @property
    def is_domestic(self) -> bool:
        return bool(self.grant_ein and len(str(self.grant_ein)) == 9)

    @property
    def is_foreign(self) -> bool:
        return not self.is_domestic


@dataclass
class Address:
    address_id: Optional[int] = None
    ein: str = ""
    name: str = ""
    canonical_address: str = ""
    po_box: Optional[str] = None
    zip_code: Optional[str] = None
    address_type: str = ""  # 'filer' or 'grantee'
    geocoding_id: Optional[int] = None
    created_at: Optional[datetime] = None

    @property
    def has_po_box(self) -> bool:
        return bool(self.po_box)

    @property
    def is_valid_zip(self) -> bool:
        ZIP_REGEX = re.compile(r'^\d{5}(-\d{4})?$')
        return bool(self.zip_code and ZIP_REGEX.match(self.zip_code))

    @property
    def is_filer_address(self) -> bool:
        return self.address_type == 'filer'

    @property
    def is_grantee_address(self) -> bool:
        return self.address_type == 'grantee'


@dataclass
class Contribution:
    contribution_id: Optional[int] = None
    filer_ein: str = ""
    filer_name: str = ""
    recipient_ein: Optional[str] = None
    amount: float = 0.0
    tax_year: int = 0
    created_at: Optional[datetime] = None


@dataclass
class Geocoding:
    geocoding_id: Optional[int] = None
    address_hash: str = ""
    normalized_address: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geocoding_status: str = "pending"  # 'pending', 'success', 'failed'
    last_attempt: Optional[datetime] = None
    attempt_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_successful(self) -> bool:
        return self.geocoding_status == 'success'

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass
class ZipFile:
    zip_id: Optional[int] = None
    filename: str = ""
    file_path: str = ""
    tax_year: int = 0
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    download_date: Optional[datetime] = None
    processed_date: Optional[datetime] = None
    status: str = "downloaded"  # 'downloaded', 'processed', 'error'
    created_at: Optional[datetime] = None

    @property
    def is_processed(self) -> bool:
        return self.status == 'processed'

    @property
    def has_error(self) -> bool:
        return self.status == 'error'


@dataclass
class XmlFile:
    xml_id: Optional[int] = None
    zip_id: int = 0
    filename: str = ""
    internal_path: str = ""
    ein: Optional[str] = None
    tax_year: Optional[int] = None
    form_type: Optional[str] = None
    processed: bool = False
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None

    @property
    def is_processed(self) -> bool:
        return self.processed

    @property
    def has_error(self) -> bool:
        return self.error_message is not None


@dataclass
class PipelineProgress:
    progress_id: Optional[int] = None
    step_name: str = ""
    start_year: int = 0
    end_year: int = 0
    status: str = "pending"  # 'pending', 'running', 'completed', 'failed'
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    records_processed: int = 0
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_completed(self) -> bool:
        return self.status == 'completed'

    @property
    def is_running(self) -> bool:
        return self.status == 'running'

    @property
    def has_error(self) -> bool:
        return self.status == 'failed'

    @property
    def duration(self) -> Optional[float]:
        """Duration in seconds if both start and end times are available"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None