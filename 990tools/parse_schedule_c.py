#!/usr/bin/env python3
"""
parse_schedule_c.py - Parse Schedule C (Political Campaign and Lobbying Activities) from IRS 990 forms

This module handles parsing of Schedule C (Political Campaign and Lobbying Activities)
from IRS Form 990, 990EZ, and 990PF.
"""

import re
from lxml import etree  # type: ignore
from io import BytesIO
import logging
from xpaths import NAMESPACES, SCHEDULE_C_XPATHS, SCHEDULE_C_AMOUNT_XPATHS, SCHEDULE_C_RECIPIENT_XPATHS, SCHEDULE_C_EIN_XPATHS
from constants import MONEY_PATTERN, FLOAT_PATTERN
from typing import Optional, List, Dict, Any
from models.political_contribution import PoliticalContribution

def parse_contributions(xml_content, xml_filename: str, filer_ein: str, filer_name: str, tax_year: int, form_type: str, context=None) -> List[Dict[str, Any]]:
    contributions = []
    try:
        parser = etree.XMLParser(recover=True)
        if isinstance(xml_content, bytes):
            tree = etree.parse(BytesIO(xml_content), parser)
        else:
            tree = etree.parse(xml_content, parser)
        root = tree.getroot()
        schedule_c_xpaths = SCHEDULE_C_XPATHS.get(form_type, [])
        for xpath in schedule_c_xpaths:
            elements = xpath(root)
            for elem in elements:
                amount_elem = elem.xpath(SCHEDULE_C_AMOUNT_XPATHS[0].path, namespaces=NAMESPACES)
                recipient_elem = elem.xpath(SCHEDULE_C_RECIPIENT_XPATHS[0].path, namespaces=NAMESPACES)
                ein_elem = elem.xpath(SCHEDULE_C_EIN_XPATHS[0].path, namespaces=NAMESPACES)
                recipient_name = recipient_elem[0].text.strip() if recipient_elem else "Unknown"
                recipient_ein = ein_elem[0].text.strip() if ein_elem else "Unknown"
                if recipient_ein == "Unknown":
                    continue
                if amount_elem and amount_elem[0].text:
                    try:
                        amount = int(parse_float_field(amount_elem[0].text.strip()))
                        if amount > 0:
                            contribution_data = {
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'recipient_ein': recipient_ein,
                                'amount': amount,
                                'tax_year': tax_year
                            }
                            contributions.append(contribution_data)

                            # If context is provided, also add to context
                            if context is not None:
                                contribution_obj = PoliticalContribution(
                                    filer_ein=filer_ein,
                                    recipient=recipient_ein,  # Using recipient_ein as recipient name for now
                                    amount=amount,
                                    tax_year=tax_year
                                )
                                context.addObjectToDatabase(contribution_obj)
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        logging.error(f"Error parsing contributions from {xml_filename}: {e}")
    return contributions

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