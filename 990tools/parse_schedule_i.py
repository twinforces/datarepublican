#!/usr/bin/env python3
"""
parse_schedule_i.py - Parse Schedule I (Grants) from IRS 990 forms

This module handles parsing of Schedule I (Grants to Organizations or Individuals in the United States)
from IRS Form 990, 990EZ, and 990PF.
"""

import re
from lxml import etree  # type: ignore
from io import BytesIO
import logging
from xpaths import NAMESPACES, GRANT_XPATHS, GRANT_EIN_XPATHS, GRANT_NAME_XPATHS, GRANT_AMOUNT_XPATHS, GRANT_FOREIGN_ADDRESS_XPATH, GRANT_COUNTRY_XPATH, GRANT_US_ADDRESS_XPATH
from xpath_utils import find_element
from constants import MONEY_PATTERN, FLOAT_PATTERN
from typing import Optional, List, Dict, Any
from models.address import Address
from models.grant import Grant

def parse_grants(xml_content, xml_filename: str, filer_ein: str, filer_name: str, tax_year: int, known_eins, form_type: str, backfill_entries=None, seen_backfill_keys=None, log_error=None, context=None) -> List[Dict[str, Any]]:
    grants = []
    try:
        parser = etree.XMLParser(recover=True)
        if isinstance(xml_content, bytes):
            tree = etree.parse(BytesIO(xml_content), parser)
        else:
            tree = etree.parse(xml_content, parser)
        root = tree.getroot()
        grant_xpaths = GRANT_XPATHS.get(form_type, [])
        for xpath in grant_xpaths:
            elements = xpath(root)
            for elem in elements:
                try:
                    # Try all EIN xpaths
                    ein_elem = None
                    for ein_xpath in GRANT_EIN_XPATHS:
                        ein_elem = elem.xpath(ein_xpath.path, namespaces=NAMESPACES)
                        if ein_elem:
                            break

                    # Try all name xpaths
                    name_elem = None
                    for name_xpath in GRANT_NAME_XPATHS:
                        name_elem = elem.xpath(name_xpath.path, namespaces=NAMESPACES)
                        if name_elem:
                            break

                    # Try all amount xpaths
                    amount_elem = None
                    for amt_xpath in GRANT_AMOUNT_XPATHS:
                        amount_elem = elem.xpath(amt_xpath.path, namespaces=NAMESPACES)
                        if amount_elem:
                            break

                    grantee_name = (name_elem[0].text.strip() if name_elem and name_elem[0].text else "Unknown")
                    grant_ein = ein_elem[0].text.strip() if ein_elem else "Unknown"

                    if grant_ein == "Unknown":
                        continue

                    if amount_elem and amount_elem[0].text:
                        try:
                            grant_amt = int(parse_float_field(amount_elem[0].text.strip()))
                            if grant_amt > 0:
                                grant_data = {
                                    'filer_ein': filer_ein,
                                    'filer_name': filer_name,
                                    'grant_ein': grant_ein,
                                    'grant_amt': grant_amt,
                                    'tax_year': tax_year
                                }
                                grants.append(grant_data)

                                # If context is provided, also add to context
                                if context is not None:
                                    grant_obj = Grant(**grant_data)
                                    context.addObjectToDatabase(grant_obj)
                        except (ValueError, TypeError):
                            pass
                except Exception as e:
                    continue
    except Exception as e:
        logging.error(f"Error parsing grants from {xml_filename}: {e}")
    return grants

def parse_float_field(text):
    """Parse a float from text, handling commas, dollar signs, and other formatting"""
    if not text:
        return 0.0

    # Remove commas and dollar signs first
    cleaned = str(text).replace(',', '').replace('$', '').strip()

    # Use regex to find the first valid number pattern
    match = FLOAT_PATTERN.search(cleaned)
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass

    return 0.0