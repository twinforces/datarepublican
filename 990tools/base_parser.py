#!/usr/bin/env python3
"""
base_parser.py - Base parser class for IRS 990 form parsing

This module provides a base class for parsing IRS 990 forms (990, 990EZ, 990PF)
with common functionality and reduced code duplication.
"""

import sys
from lxml import etree
from io import BytesIO
import logging
from nameparser import HumanName
from parse_utils import parse_int_field, parse_string_field, clean_name, MONEY_PATTERN, parse_float_field
from models import Charity as DCCharity, Officer as DCOfficer, Grant as DCGrant, Contractor as DCContractor, PoliticalContribution as DCPoliticalContribution, Address as DCAddress
from typing import Optional, List, Tuple, Dict, Any, Callable
from logging_utils import get_logger, log_info, log_error, log_debug

logger = None
log_error = None
log_debug = None
verbose = False
from constants import DEBUG_EINS, ORG_TYPE_SUFFIXES


class BaseParser:
    """Base class for IRS 990 form parsers"""

    def __init__(self, form_type: str, xpaths_dict: Dict[str, Any], namespaces: Dict[str, str]):
        self.form_type = form_type
        self.XPATHS = xpaths_dict
        self.NAMESPACES = namespaces

    def set_logger(self, new_logger, new_log_error, new_log_debug=None, is_verbose=False, debug_eins=None):
        """Set logger functions for the parser"""
        global logger, log_error, log_debug, verbose, DEBUG_EINS
        logger = new_logger
        log_error = new_log_error
        log_debug = new_log_debug or new_log_error  # fallback to log_error if log_debug not provided
        verbose = is_verbose
        DEBUG_EINS = debug_eins if debug_eins is not None else set()

    def stub_log_error(self, msg_format, *args, ein=None, exc_info=False):
        """Stub log_error function"""
        global logger
        if logger is None:
            import logging
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
            logger = logging.getLogger(__name__)
        if exc_info:
            logger.info(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
        else:
            logger.error(msg_format.format(*args) if args else msg_format)

    def stub_log_debug(self, msg_format, *args, ein=None, exc_info=False):
        """Stub log_debug function"""
        global logger
        if logger is None:
            import logging
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
            logger = logging.getLogger(__name__)
        if exc_info:
            logger.debug(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
        else:
            logger.debug(msg_format.format(*args) if args else msg_format)

    # Initialize stub functions if not set
    if log_error is None:
        log_error = stub_log_error

    if log_debug is None:
        log_debug = stub_log_debug

    def parse_org_type(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse organization type - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement parse_org_type")

    def parse_officer_comp(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse officer compensation - common implementation"""
        form_type = context.get('form_type', 'Unknown')
        total = 0
        officer_entries = []

        elements = []
        for xpath in self.XPATHS["officer_comp_elements"]:
            result = xpath(root)
            elements.extend(result)

        for elem in elements:
            name_elem = parse_string_field(elem, self.XPATHS, "officer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            value_elem = parse_string_field(elem, self.XPATHS, "officer_comp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

            if name_elem and value_elem:
                cleaned_name = clean_name(name_elem)
                name = HumanName(cleaned_name)
                first_name = name.first or "Unknown"
                last_name = name.last or "Unknown"
                value = parse_int_field(elem, self.XPATHS, "officer_comp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

                if value > 0:
                    officer_entries.append({
                        "first_name": first_name,
                        "last_name": last_name,
                        "amount": value,
                        "ein": context.get('filer_ein', 'Unknown'),
                        "charity_name": context.get('filer_name', 'Unknown'),
                        "tax_year": context.get('tax_year', 'Unknown')
                    })
                    total += value

                    if verbose or context.get('filer_ein', 'Unknown') in DEBUG_EINS:
                        log_error("Parsed officer {} {} compensation: ${} for EIN {} in {}",
                                  first_name, last_name, value, context.get('filer_ein', 'Unknown'), xml_filename,
                                  ein=context.get('filer_ein', 'Unknown'))

            if total > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}",
                          total, context.get('total_exp', 0), xml_filename,
                          ein=context.get('filer_ein', 'Unknown'))
                total = 0
                officer_entries = []

        return total, officer_entries

    def parse_grants_to_others(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse grants to others - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement parse_grants_to_others")

    def parse_travel(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse travel expenses - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement parse_travel")

    def parse_conferences(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse conference expenses - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement parse_conferences")

    def parse_receipt(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse receipt amount"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_govt_grants(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse government grants"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_contributions(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse contributions"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_total_exp(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse total expenses"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_prog_exp(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse program expenses"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_total_assets(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse total assets"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_foreign_office(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse foreign office indicator"""
        elem = parse_string_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        return elem.strip().upper() == 'X' if elem is not None else False

    def parse_filer_name(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse filer name"""
        return parse_string_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="Unknown")

    def parse_address(self, root, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse address information"""
        try:
            namespaces = {'irs': 'http://www.irs.gov/efile'}
            # Parse address components
            address_line1 = parse_string_field(root, self.XPATHS, "address_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            address_line2 = parse_string_field(root, self.XPATHS, "address_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            city = parse_string_field(root, self.XPATHS, "city", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            state = parse_string_field(root, self.XPATHS, "state", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            zip_code = parse_string_field(root, self.XPATHS, "zip_code", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

            # Check if we have at least some address components
            if any([address_line1, address_line2, city, state, zip_code]):
                return DCAddress(
                    ein=context["filer_ein"],
                    name=context["filer_name"] or "Unknown",
                    address_line1=address_line1,
                    address_line2=address_line2,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    address_type="filer"
                )
            return None
        except Exception as e:
            log_error("Failed to parse address for EIN {} in {}: {}", context.get('filer_ein', 'Unknown'), xml_filename, str(e), ein=context.get('filer_ein', 'Unknown'), exc_info=True)
            return None

    def calculate_percentage(self, value, denom):
        """Calculate percentage safely"""
        if denom == 0 or value is None or denom is None:
            return 0.0
        return round((value / denom) * 100, 2)

    def get_field_parsers(self) -> List[Tuple[str, Callable]]:
        """Get list of field parsers - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement get_field_parsers")

    def parse_form(self, root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None) -> Tuple[Optional[DCCharity], List[DCOfficer], List[DCGrant], List[DCContractor], List[DCPoliticalContribution], Optional[DCAddress]]:
        """Main parsing method"""
        namespaces = {'irs': 'http://www.irs.gov/efile'}
        context = {
            'filer_ein': filer_ein,
            'tax_year': tax_year,
            'form_type': form_type
        }

        if context["form_type"] != self.form_type:
            log_error("XML {} is not a Form {} (form_type: {}), skipping",
                      xml_filename, self.form_type, context['form_type'],
                      ein=context['filer_ein'])
            return None, [], [], [], [], None

        context["filer_name"] = self.parse_filer_name(root, "filer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
        context["business_name_line1"] = parse_string_field(root, self.XPATHS, "business_name_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        context["business_name_line2"] = parse_string_field(root, self.XPATHS, "business_name_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

        # Get field parsers from subclass
        fields = self.get_field_parsers()
        data = {}
        officer_entries = []
        for field, func in fields:
            if field == "officer_comp":
                total, entries = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
                data[field] = total
                officer_entries.extend(entries)
            else:
                data[field] = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

        # Calculate percentages
        data["comp_pct"] = self.calculate_percentage(data["officer_comp"], data["total_exp"])
        data["travel_pct"] = self.calculate_percentage(data["travel"], data["total_exp"])
        data["conferences_pct"] = self.calculate_percentage(data["conferences"], data["total_exp"])
        data["grants_pct"] = self.calculate_percentage(data["grants_to_others"], data["total_exp"])
        data["foreign_expenses_pct"] = self.calculate_percentage(data["foreign_expenses"], data["total_exp"])
        data["grift_ratio"] = self.calculate_percentage(data["officer_comp"] + data["travel"] + data["conferences"], data["total_exp"])

        # Set common fields
        data["denominator"] = data["total_assets"] + data["receipt"]
        data["comp_ptile"] = None
        data["travel_ptile"] = None
        data["conferences_ptile"] = None
        data["grants_ptile"] = None

        # Set form-specific fields (to be overridden by subclasses)
        data = self.set_form_specific_fields(data)

        # Create Charity dataclass
        charity = DCCharity(
            ein=context["filer_ein"],
            tax_year=context["tax_year"],
            filer_name=context["filer_name"] or "Unknown",
            receipt_amt=data["receipt"],
            govt_amt=data["govt_grants"],
            contrib_amt=data["contributions"],
            org_type=data["org_type"],
            total_exp=data["total_exp"],
            prog_exp=data["prog_exp"],
            travel_amt=data["travel"],
            conferences_amt=data["conferences"],
            officer_comp=data["officer_comp"],
            comp_pct=data["comp_pct"],
            comp_ptile=data["comp_ptile"],
            travel_pct=data["travel_pct"],
            travel_ptile=data["travel_ptile"],
            conferences_pct=data["conferences_pct"],
            conferences_ptile=data["conferences_ptile"],
            grants_pct=data["grants_pct"],
            grants_ptile=data["grants_ptile"],
            foreign_expenses_pct=data["foreign_expenses_pct"],
            foreign_expenses_ptile=data["foreign_expenses_ptile"],
            grift_ratio=data["grift_ratio"],
            total_assets=data["total_assets"],
            form_type=context["form_type"],
            denominator=data["denominator"],
            foreign_office=data["foreign_office"],
            foreign_expenses=data["foreign_expenses"],
            grants_to_others=data["grants_to_others"],
            domestic_misrep_flag=data["domestic_misrep_flag"],
            xml_name=xml_filename
        )

        # Convert officer entries to Officer dataclasses
        officers = []
        for entry in officer_entries:
            officer = DCOfficer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Parse address information
        address = self.parse_address(root, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

        # Debug logging for address components
        if address and log_debug is not None:
            log_debug("DEBUG: Address parsed for EIN %s: line1='%s', line2='%s', city='%s', state='%s', zip='%s', canonical='%s'",
                      address.ein, address.address_line1, address.address_line2, address.city, address.state, address.zip_code, address.canonical_address,
                      ein=address.ein)
        elif log_debug is not None:
            log_debug("DEBUG: No address parsed for EIN %s in file %s", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))

        if log_debug is not None:
            log_debug("TRACE: parse_{}() returning Charity, Officers, Grants, Contractors, Contributions, and Address for EIN: '%s' in file %s",
                      self.form_type.lower(), charity.ein, xml_filename, ein=charity.ein)
        return charity, officers, [], [], [], address

    def set_form_specific_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Set form-specific fields - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement set_form_specific_fields")


def main():
    """Main function for testing - to be implemented by subclasses"""
    raise NotImplementedError("Subclasses must implement main function")


if __name__ == "__main__":
    main()