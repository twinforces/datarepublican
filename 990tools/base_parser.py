#!/usr/bin/env python3
"""
base_parser.py - Base parser class for IRS 990 form parsing

This module provides a base class for parsing IRS 990 forms (990, 990EZ, 990PF)
with common functionality and reduced code duplication.
"""

import sys
from lxml import etree  # type: ignore
from io import BytesIO
import logging
from nameparser import HumanName
from parse_utils import parse_int_field, parse_string_field, clean_name, MONEY_PATTERN, parse_float_field
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from typing import Optional, List, Tuple, Dict, Any, Callable
from logging_utils import get_logger, log_info, log_error as proper_log_error, log_debug as proper_log_debug, log_error, log_debug, log_warning, create_stub_log_error, create_stub_log_debug

logger = None
log_error = None
log_debug = None
verbose = False
quiet = False
from constants import DEBUG_EINS, ORG_TYPE_SUFFIXES


class BaseParser:
    """Base class for IRS 990 form parsers"""

    def __init__(self, form_type: str, xpaths_dict: Dict[str, Any], namespaces: Dict[str, str]):
        self.form_type = form_type
        self.XPATHS = xpaths_dict
        self.NAMESPACES = namespaces

    def set_logger(self, new_logger, new_log_error, new_log_debug=None, is_verbose=False, debug_eins=None, is_quiet=False):
        """Set logger functions for the parser"""
        global logger, log_error, log_debug, verbose, quiet, DEBUG_EINS
        logger = new_logger
        log_error = new_log_error
        log_debug = new_log_debug or new_log_error  # fallback to log_error if log_debug not provided
        verbose = is_verbose
        quiet = is_quiet
        DEBUG_EINS = debug_eins if debug_eins is not None else set()

    # Initialize stub functions using factory functions
    stub_log_error = create_stub_log_error(logger)
    stub_log_debug = create_stub_log_debug(logger)

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

                    if (verbose or context.get('filer_ein', 'Unknown') in DEBUG_EINS) and not quiet:
                        log_info(f"Parsed officer {first_name} {last_name} compensation: ${value} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}",
                                 ein=context.get('filer_ein', 'Unknown'))

            if total > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                if not quiet:
                    log_error(f"Suspicious officer_comp ${total} exceeds total_exp ${context.get('total_exp', 0)} in {xml_filename}",
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

    def parse_related_entities(self, root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
        """Parse grants, contractors, and political contributions"""
        from parse_utils import parse_string_field, parse_int_field
        from xpaths import GRANT_XPATHS, GRANT_EIN_XPATHS, GRANT_NAME_XPATHS, GRANT_AMOUNT_XPATHS, GRANT_FOREIGN_ADDRESS_XPATH, GRANT_COUNTRY_XPATH, GRANT_US_ADDRESS_XPATH
        from xpaths import SCHEDULE_C_XPATHS, SCHEDULE_C_AMOUNT_XPATHS, SCHEDULE_C_RECIPIENT_XPATHS, SCHEDULE_C_EIN_XPATHS

        grants = []
        contractors = []
        contributions = []
        addresses = []  # For recipient addresses

        # Parse grants - common logic for 990, 990EZ, 990PF
        if self.form_type in ["990", "990EZ", "990PF"]:
            grant_elements = []
            for xpath in GRANT_XPATHS.get(self.form_type, []):
                result = xpath(root)
                grant_elements.extend(result)

            for grant_elem in grant_elements:
                # Parse grant recipient EIN
                grant_ein = None
                for ein_xpath in GRANT_EIN_XPATHS:
                    try:
                        ein_result = ein_xpath(grant_elem)
                        if ein_result:
                            raw_ein = ein_result[0].text.strip()
                            if raw_ein.isdigit():
                                grant_ein = f"{int(raw_ein):09d}"
                            break
                    except:
                        continue

                # Parse grant recipient name
                grant_name = None
                for name_xpath in GRANT_NAME_XPATHS:
                    try:
                        name_result = name_xpath(grant_elem)
                        if name_result:
                            grant_name = name_result[0].text.strip()
                            break
                    except:
                        continue

                # Parse grant amount
                grant_amount = 0
                for amount_xpath in GRANT_AMOUNT_XPATHS:
                    try:
                        amount_result = amount_xpath(grant_elem)
                        if amount_result:
                            amount_text = amount_result[0].text.strip()
                            try:
                                grant_amount = int(float(amount_text.replace(',', '')))
                                break
                            except (ValueError, AttributeError):
                                continue
                    except:
                        continue

                if grant_amount > 0:
                    # Set colocator based on EIN if available
                    grantee_colocator = f"EIN:{grant_ein}" if grant_ein else None

                    # Create grant record using charity factory method
                    grant = charity.build_grant(
                        grant_ein=grant_ein,
                        grant_amt=grant_amount,
                        grantee_name=grant_name
                    )
                    grants.append(grant)

                    # Parse recipient address for grants (especially for 990PF)
                    if self.form_type == "990PF":
                        try:
                            # Look for RecipientUSAddress in the grant element
                            addr_elem = grant_elem.find(".//irs:RecipientUSAddress", namespaces=NAMESPACES)
                            if addr_elem is not None:
                                addr_line1 = addr_elem.find("irs:AddressLine1Txt", namespaces=NAMESPACES)
                                city = addr_elem.find("irs:CityNm", namespaces=NAMESPACES)
                                state = addr_elem.find("irs:StateAbbreviationCd", namespaces=NAMESPACES)
                                zip_code = addr_elem.find("irs:ZIPCd", namespaces=NAMESPACES)

                                if any([addr_line1, city, state, zip_code]):
                                    recipient_address = Address(
                                        ein=grant_ein or f"grant_{len(addresses)}",
                                        name=grant_name or "Unknown Grant Recipient",
                                        address_line1=addr_line1.text.strip() if addr_line1 is not None else None,
                                        city=city.text.strip() if city is not None else None,
                                        state=state.text.strip() if state is not None else None,
                                        zip_code=zip_code.text.strip() if zip_code is not None else None,
                                        address_type="grant",
                                        owner_id=grant.id if grant else None
                                    )
                                    addresses.append(recipient_address)
                        except Exception as e:
                            if not quiet:
                                log_error(f"Failed to parse grant recipient address: {e}", ein=context.get('filer_ein', 'Unknown'))

        # Parse political contributions - for 990 and 990EZ
        if self.form_type in ["990", "990EZ"]:
            contribution_elements = []
            for xpath in SCHEDULE_C_XPATHS.get(self.form_type, []):
                result = xpath(root)
                contribution_elements.extend(result)

            for contrib_elem in contribution_elements:
                # Parse contribution recipient
                recipient = None
                for recipient_xpath in SCHEDULE_C_RECIPIENT_XPATHS:
                    try:
                        recipient_result = recipient_xpath(contrib_elem)
                        if recipient_result:
                            recipient = recipient_result[0].text.strip()
                            break
                    except:
                        continue

                # Parse contribution amount - try multiple fields
                amount = 0
                for amount_xpath in SCHEDULE_C_AMOUNT_XPATHS:
                    try:
                        amount_result = amount_xpath(contrib_elem)
                        if amount_result:
                            amount_text = amount_result[0].text.strip()
                            try:
                                amount = int(float(amount_text.replace(',', '')))
                                break
                            except (ValueError, AttributeError):
                                continue
                    except:
                        continue

                # Also try PoliticalExpendituresAmt directly
                if amount == 0:
                    try:
                        pol_exp_elem = contrib_elem.find(".//irs:PoliticalExpendituresAmt", namespaces=NAMESPACES)
                        if pol_exp_elem is not None and pol_exp_elem.text:
                            amount = int(float(pol_exp_elem.text.replace(',', '')))
                    except:
                        pass

                if amount > 0:
                    # Create political contribution record using charity factory method
                    contribution = charity.build_political_contribution(
                        recipient=recipient or "Unknown Political Recipient",
                        amount=amount
                    )
                    contributions.append(contribution)

        # Parse contractors - for 990PF forms (ContractorPaidOver50kCnt)
        if self.form_type == "990PF":
            # Parse contractor count from ContractorPaidOver50kCnt
            contractor_count = 0
            try:
                count_elem = root.find(".//irs:ContractorPaidOver50kCnt", namespaces=NAMESPACES)
                if count_elem is not None and count_elem.text:
                    contractor_count = int(count_elem.text.strip())
            except:
                pass

            # If there are contractors, create placeholder records
            # Note: Detailed contractor info is not available in the XML
            for i in range(contractor_count):
                contractor = Contractor(
                    filer_ein=context["filer_ein"],
                    name=f"Contractor {i+1}",
                    amount=50000,  # Minimum threshold amount
                    tax_year=context["tax_year"]
                )
                contractors.append(contractor)

        return grants, contractors, contributions

    def parse_address(self, root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
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
                # Charity must be available to build address - restructure if needed
                return charity.build_address(
                    address_line1=address_line1,
                    address_line2=address_line2,
                    city=city,
                    state=state,
                    zip_code=zip_code
                )
            return None
        except Exception as e:
            if not quiet:
                log_error(f"Failed to parse address for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}: {str(e)}", ein=context.get('filer_ein', 'Unknown'))
            return None

    def calculate_percentage(self, value, denom):
        """Calculate percentage safely"""
        if denom == 0 or value is None or denom is None:
            return 0.0
        return round((value / denom) * 100, 2)

    def get_field_parsers(self) -> List[Tuple[str, Callable]]:
        """Get list of field parsers - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement get_field_parsers")

    def parse_form(self, root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution], Optional[Address]]:
        """Main parsing method"""
        namespaces = {'irs': 'http://www.irs.gov/efile'}
        context = {
            'filer_ein': filer_ein,
            'tax_year': tax_year,
            'form_type': form_type
        }

        if context["form_type"] != self.form_type:
            if not quiet:
                log_error(f"XML {xml_filename} is not a Form {self.form_type} (form_type: {context['form_type']}), skipping",
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
        charity = Charity(
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
            officer = Officer(
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                compensation=entry["amount"],
                tax_year=tax_year
            )
            officers.append(officer)

        # Parse grants, contractors, and political contributions in alphabetical order (Common, ScheduleA-Z)
        grants, contractors, contributions = self.parse_related_entities(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

        # Parse address information
        address = self.parse_address(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

        # Debug logging for address components
        if address and log_debug is not None and not quiet:
            log_debug(f"DEBUG: Address parsed for EIN {address.ein}: line1='{address.address_line1}', line2='{address.address_line2}', city='{address.city}', state='{address.state}', zip='{address.zip_code}', canonical='{address.canonical_address}'",
                      ein=address.ein)
        elif log_debug is not None and not quiet:
            log_debug(f"DEBUG: No address parsed for EIN {context.get('filer_ein', 'Unknown')} in file {xml_filename}", ein=context.get('filer_ein', 'Unknown'))

        if log_debug is not None and not quiet:
            log_debug(f"TRACE: parse_{self.form_type.lower()}() returning Charity, Officers, Grants, Contractors, Contributions, and Address for EIN: '{charity.ein}' in file {xml_filename}",
                      ein=charity.ein)
        return charity, officers, grants, contractors, contributions, address

    def set_form_specific_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Set form-specific fields - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement set_form_specific_fields")


def main():
    """Main function for testing - to be implemented by subclasses"""
    raise NotImplementedError("Subclasses must implement main function")


if __name__ == "__main__":
    main()