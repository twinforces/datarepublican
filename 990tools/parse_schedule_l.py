#!/usr/bin/env python3
"""
parse_schedule_l.py - Parse Schedule L (Transactions with Interested Persons) from IRS 990 forms

This module handles parsing of Schedule L (Transactions with Interested Persons)
from IRS Form 990, 990EZ, and 990PF.
"""

import re
from lxml import etree  # type: ignore
from io import BytesIO
import logging
from xpaths import NAMESPACES, SCHEDULE_L_XPATHS
from constants import MONEY_PATTERN, FLOAT_PATTERN
from typing import Optional, List, Dict, Any
from models.contractor import Contractor

def parse_contractors(xml_content, xml_filename: str, filer_ein: str, filer_name: str, tax_year: int, form_type: str, context=None) -> List[Dict[str, Any]]:
    """Parse contractors from Schedule L"""
    contractors = []
    try:
        parser = etree.XMLParser(recover=True)
        if isinstance(xml_content, bytes):
            tree = etree.parse(BytesIO(xml_content), parser)
        else:
            tree = etree.parse(xml_content, parser)
        root = tree.getroot()

        # Use the form-specific XPath patterns from xpaths.py
        contractor_xpaths = SCHEDULE_L_XPATHS.get(form_type, [])

        for xpath in contractor_xpaths:
            elements = xpath(root)
            for elem in elements:
                # Extract contractor name
                name_xpaths = [
                    etree.XPath("irs:ContractorName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
                    etree.XPath("irs:ContractorName/irs:BusinessNameLine1", namespaces=NAMESPACES),
                    etree.XPath("irs:PersonNm", namespaces=NAMESPACES),
                    etree.XPath("irs:Name", namespaces=NAMESPACES),
                    etree.XPath("ContractorName/BusinessNameLine1Txt"),
                    etree.XPath("ContractorName/BusinessNameLine1"),
                    etree.XPath("PersonNm"),
                    etree.XPath("Name"),
                ]

                contractor_name = "Unknown"
                for name_xpath in name_xpaths:
                    try:
                        name_elem = name_xpath(elem)
                        if name_elem and name_elem[0].text:
                            contractor_name = name_elem[0].text.strip()
                            break
                    except:
                        continue

                # Extract compensation amount
                amount_xpaths = [
                    etree.XPath("irs:CompensationAmt", namespaces=NAMESPACES),
                    etree.XPath("irs:ServicesAmt", namespaces=NAMESPACES),
                    etree.XPath("irs:TotalAmt", namespaces=NAMESPACES),
                    etree.XPath("CompensationAmt"),
                    etree.XPath("ServicesAmt"),
                    etree.XPath("TotalAmt"),
                ]

                amount = 0.0
                for amt_xpath in amount_xpaths:
                    try:
                        amt_elem = amt_xpath(elem)
                        if amt_elem and amt_elem[0].text:
                            amount = parse_float_field(amt_elem[0].text.strip())
                            if amount > 0:
                                break
                    except:
                        continue

                # Extract EIN if available
                ein_xpaths = [
                    etree.XPath("irs:EIN", namespaces=NAMESPACES),
                    etree.XPath("irs:ContractorEIN", namespaces=NAMESPACES),
                    etree.XPath("EIN"),
                    etree.XPath("ContractorEIN"),
                ]

                contractor_ein = None
                for ein_xpath in ein_xpaths:
                    try:
                        ein_elem = ein_xpath(elem)
                        if ein_elem and ein_elem[0].text:
                            raw_ein = ein_elem[0].text.strip()
                            if raw_ein.isdigit():
                                contractor_ein = f"{int(raw_ein):09d}"
                                break
                    except:
                        continue

                if amount > 0:
                    contractor_data = {
                        'filer_ein': filer_ein,
                        'filer_name': filer_name,
                        'name': contractor_name,
                        'amount': amount,
                        'ein': contractor_ein,
                        'tax_year': tax_year
                    }
                    contractors.append(contractor_data)

                    # If context is provided, also add to context
                    if context is not None:
                        contractor_obj = Contractor(**contractor_data)
                        context.addObjectToDatabase(contractor_obj)

    except Exception as e:
        logging.error(f"Error parsing contractors from {xml_filename}: {e}")
    return contractors

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