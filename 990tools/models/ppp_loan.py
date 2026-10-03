#!/usr/bin/env python3
"""SBA Paycheck Protection Program loan (FOIA). No EIN in the public file."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

from .address import Address
from .base import BaseModel


def _clean_text(value: Optional[str], *, title: bool = False) -> str:
    text = html.unescape(value or "").replace("\xa0", " ").strip()
    if title and text:
        return text.title()
    return text


@dataclass
class PppLoan(BaseModel):
    id: Optional[str] = None
    loan_number: str = ""
    date_approved: Optional[str] = None
    processing_method: Optional[str] = None
    borrower_name: Optional[str] = None
    borrower_address: Optional[str] = None
    borrower_city: Optional[str] = None
    borrower_state: Optional[str] = None
    borrower_zip: Optional[str] = None
    loan_status: Optional[str] = None
    current_approval_amount: Optional[float] = None
    jobs_reported: Optional[int] = None
    naics_code: Optional[str] = None
    business_type: Optional[str] = None
    nonprofit: Optional[bool] = None
    forgiveness_amount: Optional[float] = None
    forgiveness_date: Optional[str] = None
    originating_lender: Optional[str] = None
    servicing_lender_name: Optional[str] = None
    rural_urban: Optional[str] = None
    created_at: Optional[str] = None

    def build_address(self) -> Optional[Address]:
        street = _clean_text(self.borrower_address, title=True)
        city = _clean_text(self.borrower_city, title=True)
        state = _clean_text(self.borrower_state).upper()
        zip_code = _clean_text(self.borrower_zip).replace(" ", "")
        if not any((street, city, state, zip_code)):
            return None
        if self.id is None:
            self.id = self.generate_id()
        address = Address(
            ein="",
            name=_clean_text(self.borrower_name) or self.loan_number,
            address_line1=street,
            city=city,
            state=state,
            zip_code=zip_code,
            address_type="ppp_borrower",
            owner_id=self.id,
        )
        address.prep_for_insert()
        if not (address.canonical_address or "").strip():
            return None
        return address
