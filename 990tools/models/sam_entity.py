#!/usr/bin/env python3
"""SAM.gov public entity (UEI). No EIN/TIN in the public extract."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import List, Optional

from .address import Address
from .base import BaseModel

US_COUNTRY = frozenset({"USA", "US", "UNITED STATES", ""})


def _clean_text(value: Optional[str], *, title: bool = False) -> str:
    text = html.unescape(value or "").replace("\xa0", " ").strip()
    if title and text:
        return text.title()
    return text


def _combine_zip(zip_code: Optional[str], zip4: Optional[str]) -> str:
    z5 = _clean_text(zip_code).replace(" ", "")
    z4 = _clean_text(zip4).replace(" ", "")
    if z5 and z4 and z4 not in z5:
        return f"{z5}-{z4}"
    return z5 or z4


@dataclass
class SamEntity(BaseModel):
    id: Optional[str] = None
    uei: str = ""
    cage: Optional[str] = None
    extract_code: Optional[str] = None
    purpose: Optional[str] = None
    registration_date: Optional[str] = None
    expiration_date: Optional[str] = None
    last_update_date: Optional[str] = None
    activation_date: Optional[str] = None
    legal_name: Optional[str] = None
    dba_name: Optional[str] = None
    phy_line1: Optional[str] = None
    phy_line2: Optional[str] = None
    phy_city: Optional[str] = None
    phy_state: Optional[str] = None
    phy_zip: Optional[str] = None
    phy_zip4: Optional[str] = None
    phy_country: Optional[str] = None
    mail_line1: Optional[str] = None
    mail_line2: Optional[str] = None
    mail_city: Optional[str] = None
    mail_state: Optional[str] = None
    mail_zip: Optional[str] = None
    mail_zip4: Optional[str] = None
    mail_country: Optional[str] = None
    entity_url: Optional[str] = None
    business_types: Optional[str] = None
    primary_naics: Optional[str] = None
    created_at: Optional[str] = None

    @property
    def display_name(self) -> str:
        legal = _clean_text(self.legal_name)
        if legal:
            return legal
        dba = _clean_text(self.dba_name)
        if dba:
            return dba
        return self.uei or "SAM entity"

    def build_addresses(self) -> List[Address]:
        if self.id is None:
            self.id = self.generate_id()
        out: List[Address] = []
        phy = self._one_address(
            line1=self.phy_line1,
            line2=self.phy_line2,
            city=self.phy_city,
            state=self.phy_state,
            zip_code=self.phy_zip,
            zip4=self.phy_zip4,
            country=self.phy_country,
            address_type="sam_entity_phy",
        )
        if phy is not None:
            out.append(phy)
        mail = self._one_address(
            line1=self.mail_line1,
            line2=self.mail_line2,
            city=self.mail_city,
            state=self.mail_state,
            zip_code=self.mail_zip,
            zip4=self.mail_zip4,
            country=self.mail_country,
            address_type="sam_entity_mail",
        )
        if mail is not None:
            out.append(mail)
        return out

    def _one_address(
        self,
        *,
        line1: Optional[str],
        line2: Optional[str],
        city: Optional[str],
        state: Optional[str],
        zip_code: Optional[str],
        zip4: Optional[str],
        country: Optional[str],
        address_type: str,
    ) -> Optional[Address]:
        street1 = _clean_text(line1, title=True)
        street2 = _clean_text(line2, title=True)
        city_n = _clean_text(city, title=True)
        state_n = _clean_text(state).upper()
        zip_n = _combine_zip(zip_code, zip4)
        country_n = _clean_text(country).upper()
        if not any((street1, street2, city_n, state_n, zip_n)):
            return None
        address = Address(
            ein="",
            name=self.display_name,
            address_line1=street1,
            address_line2=street2,
            city=city_n,
            state=state_n,
            zip_code=zip_n,
            address_type=address_type,
            owner_id=self.id,
        )
        address.prep_for_insert()
        if country_n and country_n not in US_COUNTRY:
            # After canonicalize so the street stays on canonical_address.
            address.colocator = f"FA:{country_n}"
            return address
        if not (address.canonical_address or "").strip():
            return None
        return address
