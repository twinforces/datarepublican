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
from parse_utils import parse_int_field, parse_string_field, clean_name, MONEY_PATTERN, parse_float_field, parse_name_fast, split_zip_code
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
                first_name, last_name = parse_name_fast(cleaned_name)
                value = parse_int_field(elem, self.XPATHS, "officer_comp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

                if value > 0:
                    officer_entries.append({
                        "first_name": first_name,
                        "last_name": last_name,
                        "full_name": name_elem,  # Store original name for photo lookup
                        "amount": value,
                        "ein": context.get('filer_ein', 'Unknown'),
                        "charity_name": context.get('filer_name', 'Unknown'),
                        "tax_year": context.get('tax_year', 'Unknown')
                    })
                    total += value

                    if (verbose or context.get('filer_ein', 'Unknown') in DEBUG_EINS) and not quiet and logger is not None:
                        log_info(logger, f"Parsed officer {first_name} {last_name} compensation: ${value} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}",
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
        """Parse travel expenses from TravelGrp/TotalAmt"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_conferences(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse conference expenses using XPath union for better performance"""
        # Single XPath union to get all conference expense elements at once
        conferences_union_xpath = etree.XPath("""
            .//irs:IRS990/irs:ConferencesMeetingsGrp/irs:TotalAmt |
            .//irs:IRS990EZ/irs:ConferencesMeetingsGrp/irs:TotalAmt |
            .//ConferencesMeetingsGrp/TotalAmt |
            .//irs:ConferencesMeetings/irs:TotalAmt |
            .//ConferencesMeetings/TotalAmt
        """, namespaces=namespaces)

        # Get all conference elements in one query
        conference_elements = conferences_union_xpath(root)

        # Return the first valid element's text as integer
        for elem in conference_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        try:
            return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
        except KeyError:
            # Field not available for this form type
            return 0

    def parse_receipt(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse receipt amount using XPath union for better performance"""
        # Single XPath union to get all receipt elements at once
        receipt_union_xpath = etree.XPath("""
            .//irs:TotalRevenueAmt |
            .//irs:IRS990EZ/irs:TotalRevenueAmt |
            .//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:TotalRevenueRevAndExpnssAmt |
            .//irs:AnalysisOfRevenueAndExpenses/irs:TotalRevenueRevAndExpnssAmt |
            .//TotalRevenueAmt
        """, namespaces=namespaces)

        # Get all receipt elements in one query
        receipt_elements = receipt_union_xpath(root)

        # Return the first valid element's text as integer
        for elem in receipt_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)


    def parse_govt_grants(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse government grants"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_contributions(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse contributions"""
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_total_exp(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse total expenses using XPath union for better performance"""
        # Single XPath union to get all total expenses elements at once
        total_exp_union_xpath = etree.XPath("""
            .//irs:IRS990/irs:TotalFunctionalExpensesGrp/irs:TotalAmt |
            .//irs:IRS990/irs:TotalExpensesAmt |
            .//irs:IRS990EZ/irs:TotalExpensesAmt |
            .//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt |
            .//irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt |
            .//TotalFunctionalExpensesGrp/TotalAmt |
            .//TotalExpensesAmt
        """, namespaces=namespaces)

        # Get all total expenses elements in one query
        exp_elements = total_exp_union_xpath(root)

        # Return the first valid element's text as integer
        for elem in exp_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_prog_exp(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse program expenses using XPath union for better performance"""
        # Single XPath union to get all program expenses elements at once
        prog_exp_union_xpath = etree.XPath("""
            .//irs:IRS990/irs:TotalProgramServiceExpensesAmt |
            .//irs:IRS990EZ/irs:TotalProgramServiceExpensesAmt |
            .//irs:ProgramServiceExpensesAmt |
            .//TotalProgramServiceExpensesAmt
        """, namespaces=namespaces)

        # Get all program expenses elements in one query
        prog_elements = prog_exp_union_xpath(root)

        # Return the first valid element's text as integer
        for elem in prog_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_total_assets(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse total assets using XPath union for better performance"""
        # Single XPath union to get all total assets elements at once
        total_assets_union_xpath = etree.XPath("""
            .//irs:IRS990/irs:TotalAssetsEOYAmt |
            .//irs:IRS990EZ/irs:TotalAssetsEOYAmt |
            .//irs:IRS990PF/irs:Form990PFBalanceSheetsGrp/irs:TotalAssetsEOYAmt |
            .//irs:Form990PFBalanceSheetsGrp/irs:TotalAssetsEOYAmt |
            .//TotalAssetsEOYAmt
        """, namespaces=namespaces)

        # Get all total assets elements in one query
        assets_elements = total_assets_union_xpath(root)

        # Return the first valid element's text as integer
        for elem in assets_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        return parse_int_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    def parse_foreign_office(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse foreign office indicator using XPath union for better performance"""
        # Single XPath union to get all foreign office elements at once
        foreign_office_union_xpath = etree.XPath("""
            .//irs:IRS990/irs:ForeignOfficeInd |
            .//irs:IRS990EZ/irs:ForeignOfficeInd |
            .//irs:IRS990EZ/irs:ForeignOfficeCountryCd |
            .//ForeignOfficeInd |
            .//ForeignOfficeCountryCd
        """, namespaces=namespaces)

        # Get all foreign office elements in one query
        office_elements = foreign_office_union_xpath(root)

        # Return the first valid element's text
        for elem in office_elements:
            if elem.text and elem.text.strip():
                return elem.text.strip().upper() == 'X'

        # Fallback to original method if union fails
        try:
            elem = parse_string_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            return elem.strip().upper() == 'X' if elem is not None else False
        except Exception as e:
            # 990PF forms don't have a 'foreign_office' field, so catch the exception and set to False
            if not quiet and log_debug is not None and logger is not None:
                log_debug(logger, f"DEBUG: foreign_office field not found for {self.form_type} form, setting to False: {str(e)}", ein=context.get('filer_ein', 'Unknown'))
            return False

    def parse_filer_name(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse filer name"""
        return parse_string_field(root, self.XPATHS, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="Unknown")

    def parse_schedule_i(self, root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
        """Parse Schedule I (Grants to Organizations) - optimized XPath union for better performance"""
        # Single XPath union to get all grant elements at once
        grant_union_xpath = etree.XPath("""
            .//irs:ReturnData/irs:IRS990/irs:RecipientTable/irs:RecipientBusinessName/irs:BusinessNameLine1Txt |
            .//irs:IRS990/irs:RecipientTable/irs:RecipientBusinessName/irs:BusinessNameLine1Txt |
            .//irs:ReturnData/irs:IRS990EZ/irs:RecipientTable/irs:RecipientBusinessName/irs:BusinessNameLine1Txt |
            .//irs:IRS990EZ/irs:RecipientTable/irs:RecipientBusinessName/irs:BusinessNameLine1Txt |
            .//irs:ReturnData/irs:IRS990PF/irs:GrantsAndContributionsPaidDuringYearGrp/irs:RecipientName/irs:BusinessName/irs:BusinessNameLine1Txt |
            .//irs:IRS990PF/irs:GrantsAndContributionsPaidDuringYearGrp/irs:RecipientName/irs:BusinessName/irs:BusinessNameLine1Txt |
            .//irs:RecipientTable/irs:RecipientBusinessName/irs:BusinessNameLine1Txt |
            .//irs:GrantsAndContributionsPaidDuringYearGrp/irs:RecipientName/irs:BusinessName/irs:BusinessNameLine1Txt
        """, namespaces={'irs': 'http://www.irs.gov/efile'})

        # Get all grant elements in one query
        grant_elements = grant_union_xpath(root)

        # Process each grant element
        for grant_elem in grant_elements:
            # XPath unions for grant data within each grant element
            name_union_xpath = etree.XPath("""
                irs:RecipientBusinessName/irs:BusinessNameLine1Txt |
                irs:RecipientBusinessName/irs:BusinessNameLine1 |
                irs:BusinessName/irs:BusinessNameLine1Txt |
                irs:BusinessName/irs:BusinessNameLine1 |
                RecipientBusinessName/BusinessNameLine1Txt |
                RecipientBusinessName/BusinessNameLine1 |
                BusinessName/BusinessNameLine1Txt |
                BusinessName/BusinessNameLine1
            """, namespaces={'irs': 'http://www.irs.gov/efile'})

            amount_union_xpath = etree.XPath("""
                irs:AmountOfCashGrant |
                irs:CashGrantAmt |
                AmountOfCashGrant |
                CashGrantAmt
            """, namespaces={'irs': 'http://www.irs.gov/efile'})

            name_elements = name_union_xpath(grant_elem)
            amount_elements = amount_union_xpath(grant_elem)

            grant_name = None
            grant_amount = None

            # Get first valid name
            for name_elem in name_elements:
                if name_elem.text and name_elem.text.strip():
                    grant_name = name_elem.text.strip()
                    break

            # Get first valid amount
            for amt_elem in amount_elements:
                if amt_elem.text and amt_elem.text.strip():
                    try:
                        grant_amount = int(amt_elem.text.strip())
                        break
                    except ValueError:
                        continue

            # Create grant if we have valid data
            if grant_name and grant_amount is not None:
                from models import Grant
                grant = Grant(
                    recipient_name=grant_name,
                    amount=grant_amount,
                    tax_year=charity.tax_year,
                    charity_id=charity.id
                )
                context.addObjectToDatabase(grant)

        # Fallback to original method if no grants found via union
        if not grant_elements:
            from parse_schedule_i import parse_grants
            grants_data = parse_grants(root, xml_filename, charity.ein, charity.filer_name, charity.tax_year, set(), self.form_type, context=context)
    def parse_schedule_c(self, root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
        """Parse Schedule C (Political Contributions) - optimized to skip if no Schedule C exists"""
        # Quick check: if no Schedule C element exists, skip entirely
        schedule_c_check = etree.XPath(".//irs:ScheduleC", namespaces={'irs': 'http://www.irs.gov/efile'})
        if not schedule_c_check(root):
            return  # No Schedule C, nothing to parse

        # Only parse if Schedule C actually exists
        from parse_schedule_c import parse_contributions
        contributions_data = parse_contributions(root, xml_filename, charity.ein, charity.filer_name, charity.tax_year, self.form_type, context=context)
        # parse_contributions now adds contributions directly to context
    def parse_schedule_l(self, root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
        """Parse Schedule L (Contractors) - optimized XPath union for better performance"""
        # Single XPath union to get all contractor elements at once
        contractor_union_xpath = etree.XPath("""
            .//irs:ReturnData/irs:IRS990/irs:ContractorCompensationGrp |
            .//irs:IRS990/irs:ContractorCompensationGrp |
            .//irs:ReturnData/irs:IRS990EZ/irs:ContractorCompensationGrp |
            .//irs:IRS990EZ/irs:ContractorCompensationGrp |
            .//irs:ReturnData/irs:IRS990PF/irs:CompensationOfHghstPdCntrctGrp |
            .//irs:IRS990PF/irs:CompensationOfHghstPdCntrctGrp |
            .//irs:ContractorCompensationGrp |
            .//irs:CompensationOfHghstPdCntrctGrp
        """, namespaces={'irs': 'http://www.irs.gov/efile'})

        # Get all contractor elements in one query
        contractor_elements = contractor_union_xpath(root)

        # Process each contractor element
        for contractor_elem in contractor_elements:
            # Extract contractor data using XPath unions for efficiency
            name_union_xpath = etree.XPath("""
                irs:ContractorName/irs:BusinessName/irs:BusinessNameLine1Txt |
                irs:ContractorName/irs:BusinessNameLine1Txt |
                irs:BusinessName/irs:BusinessNameLine1Txt |
                irs:BusinessName/irs:BusinessNameLine1 |
                ContractorName/BusinessName/BusinessNameLine1Txt |
                ContractorName/BusinessNameLine1Txt |
                BusinessName/BusinessNameLine1Txt |
                BusinessName/BusinessNameLine1
            """, namespaces={'irs': 'http://www.irs.gov/efile'})

            comp_union_xpath = etree.XPath("""
                irs:ContractorCompensationAmt |
                irs:CompensationAmt |
                ContractorCompensationAmt |
                CompensationAmt
            """, namespaces={'irs': 'http://www.irs.gov/efile'})

            name_elements = name_union_xpath(contractor_elem)
            comp_elements = comp_union_xpath(contractor_elem)

            contractor_name = None
            contractor_comp = None

            # Get first valid name
            for name_elem in name_elements:
                if name_elem.text and name_elem.text.strip():
                    contractor_name = name_elem.text.strip()
                    break

            # Get first valid compensation
            for comp_elem in comp_elements:
                if comp_elem.text and comp_elem.text.strip():
                    try:
                        contractor_comp = int(comp_elem.text.strip())
                        break
                    except ValueError:
                        continue

            # Create contractor if we have valid data
            if contractor_name and contractor_comp is not None:
                from models import Contractor
                contractor = Contractor(
                    name=contractor_name,
                    amount=contractor_comp,
                    tax_year=charity.tax_year,
                    charity_id=charity.id
                )
                context.addObjectToDatabase(contractor)

        # Fallback to original method if no contractors found via union
        if not contractor_elements:
            from parse_contractors import parse_contractors
            contractors_data = parse_contractors(root, xml_filename, charity.ein, charity.filer_name, charity.tax_year, self.form_type, context=context)
    def parse_address(self, root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
        """Parse address information using XPath union for better performance"""
        try:
            namespaces = {'irs': 'http://www.irs.gov/efile'}

            # Single XPath union to get all address components at once
            address_union_xpath = etree.XPath("""
                .//irs:Filer/irs:USAddress/irs:AddressLine1Txt |
                .//irs:Filer/irs:USAddress/irs:AddressLine2Txt |
                .//irs:Filer/irs:USAddress/irs:CityNm |
                .//irs:Filer/irs:USAddress/irs:StateAbbreviationCd |
                .//irs:Filer/irs:USAddress/irs:ZIPCd |
                .//Filer/USAddress/AddressLine1Txt |
                .//Filer/USAddress/AddressLine2Txt |
                .//Filer/USAddress/CityNm |
                .//Filer/USAddress/StateAbbreviationCd |
                .//Filer/USAddress/ZIPCd
            """, namespaces=namespaces)

            # Get all address elements in one query
            address_elements = address_union_xpath(root)

            # Extract values by tag name
            address_line1 = None
            address_line2 = None
            city = None
            state = None
            zip_code_raw = None

            for elem in address_elements:
                tag_name = elem.tag.split('}')[-1]  # Remove namespace prefix
                if tag_name in ('AddressLine1Txt', 'AddressLine1'):
                    address_line1 = elem.text.strip() if elem.text else None
                elif tag_name in ('AddressLine2Txt', 'AddressLine2'):
                    address_line2 = elem.text.strip() if elem.text else None
                elif tag_name == 'CityNm':
                    city = elem.text.strip() if elem.text else None
                elif tag_name == 'StateAbbreviationCd':
                    state = elem.text.strip() if elem.text else None
                elif tag_name == 'ZIPCd':
                    zip_code_raw = elem.text.strip() if elem.text else None

            # Split ZIP code into zip_code and zip4
            zip_code, zip4 = split_zip_code(zip_code_raw)

            # Check if we have at least some address components
            if any([address_line1, address_line2, city, state, zip_code]):
                # Charity must be available to build address - restructure if needed
                return charity.build_address(
                    address_line1=address_line1,
                    address_line2=address_line2,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    zip4=zip4
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

    def parse_form(self, root, xml_filename, xpath_cache, context, log_error=log_error, xpath_match_stats=None):
        """Main parsing method - now uses context instead of returning tuples"""
        from pending_database_context import PendingDatabaseContext

        if not isinstance(context, PendingDatabaseContext):
            raise ValueError("context must be a PendingDatabaseContext instance")

        namespaces = {'irs': 'http://www.irs.gov/efile'}

        # Validate EIN before proceeding
        charity = context.getCharity()
        if not charity or not charity.ein or charity.ein == "Unknown":
            raise ValueError(f"Invalid EIN '{charity.ein if charity else 'None'}' for file {xml_filename}")

        if charity.form_type != self.form_type:
            if not quiet:
                log_error(f"XML {xml_filename} is not a Form {self.form_type} (form_type: {charity.form_type}), skipping",
                          ein=charity.ein)
            return

        # Re-raise exceptions after logging for better error handling
        try:
            # Parse filer name components
            business_name_line1 = parse_string_field(root, self.XPATHS, "business_name_line1", namespaces, xml_filename, {}, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            business_name_line2 = parse_string_field(root, self.XPATHS, "business_name_line2", namespaces, xml_filename, {}, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

            # Combine business name lines
            filer_name = f"{business_name_line1 or ''} {business_name_line2 or ''}".strip() or "Unknown"
            charity.filer_name = filer_name

            # Get field parsers from subclass
            fields = self.get_field_parsers()
            data = {}
            officer_entries = []
            for field, func in fields:
                if field == "officer_comp":
                    total, entries = func(root, field, namespaces, xml_filename, {}, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
                    data[field] = total
                    officer_entries.extend(entries)
                else:
                    data[field] = func(root, field, namespaces, xml_filename, {}, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

            # Calculate percentages
            data["comp_pct"] = self.calculate_percentage(data["officer_comp"], data["total_exp"])
            data["travel_pct"] = self.calculate_percentage(data["travel"], data["total_exp"])
            data["conferences_pct"] = self.calculate_percentage(data.get("conferences", 0), data["total_exp"])
            data["grants_pct"] = self.calculate_percentage(data["grants_to_others"], data["total_exp"])
            data["foreign_expenses_pct"] = self.calculate_percentage(data.get("foreign_expenses", 0), data["total_exp"])
            data["grift_ratio"] = self.calculate_percentage(data["officer_comp"] + data["travel"] + data.get("conferences", 0), data["total_exp"])

            # Set common fields
            data["denominator"] = data["total_assets"] + data["receipt"]
            data["comp_ptile"] = None
            data["travel_ptile"] = None
            data["conferences_ptile"] = None
            data["grants_ptile"] = None

            # Set form-specific fields (to be overridden by subclasses)
            data = self.set_form_specific_fields(data)

            # Ensure all required fields are present with defaults
            required_fields = [
                "foreign_office", "foreign_expenses", "domestic_misrep_flag",
                "govt_grants", "contributions", "foreign_expenses_pct", "foreign_expenses_ptile"
            ]
            for field in required_fields:
                if field not in data:
                    if field in ["foreign_office", "domestic_misrep_flag"]:
                        data[field] = False
                    elif field in ["foreign_expenses_pct", "foreign_expenses_ptile"]:
                        data[field] = None
                    else:
                        data[field] = None

            # Update charity with parsed data
            charity.receipt_amt = data["receipt"]
            charity.govt_amt = data["govt_grants"]
            charity.contrib_amt = data["contributions"]
            charity.org_type = data["org_type"]
            charity.total_exp = data["total_exp"]
            charity.prog_exp = data["prog_exp"]
            charity.travel_amt = data["travel"]
            charity.conferences_amt = data.get("conferences", 0)
            charity.officer_comp = data["officer_comp"]
            charity.comp_pct = data["comp_pct"]
            charity.comp_ptile = data["comp_ptile"]
            charity.travel_pct = data["travel_pct"]
            charity.travel_ptile = data["travel_ptile"]
            charity.conferences_pct = data["conferences_pct"]
            charity.conferences_ptile = data["conferences_ptile"]
            charity.grants_pct = data["grants_pct"]
            charity.grants_ptile = data["grants_ptile"]
            charity.foreign_expenses_pct = data["foreign_expenses_pct"]
            charity.foreign_expenses_ptile = data["foreign_expenses_ptile"]
            charity.grift_ratio = data["grift_ratio"]
            charity.total_assets = data["total_assets"]
            charity.denominator = data["denominator"]
            charity.foreign_office = data.get("foreign_office", False)
            charity.foreign_expenses = data.get("foreign_expenses", None)
            charity.grants_to_others = data.get("grants_to_others", 0)
            charity.domestic_misrep_flag = data.get("domestic_misrep_flag", False)
            charity.xml_name = xml_filename

            # Create and add officers to context
            for entry in officer_entries:
                officer = Officer(
                    first_name=entry["first_name"],
                    last_name=entry["last_name"],
                    full_name=entry["full_name"],
                    compensation=entry["amount"],
                    tax_year=charity.tax_year,
                    charity_id=charity.id
                )
                context.addObjectToDatabase(officer)

            # Parse Schedule L (Contractors) - part of main form
            self.parse_schedule_l(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

            # Parse related entities (grants, political contributions)
            self.parse_related_entities(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

            # Parse address information and add to context
            address = self.parse_address(root, xml_filename, {}, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)
            if address:
                context.addObjectToDatabase(address)

            # Debug logging for address components
            if address and log_debug is not None and not quiet:
                log_debug(f"DEBUG: Address parsed for EIN {address.ein}: line1='{address.address_line1}', line2='{address.address_line2}', city='{address.city}', state='{address.state}', zip='{address.zip_code}', canonical='{address.canonical_address}'",
                          ein=address.ein)
            elif log_debug is not None and not quiet:
                log_debug(f"DEBUG: No address parsed for EIN {charity.ein} in file {xml_filename}", ein=charity.ein)

            if log_debug is not None and not quiet:
                log_debug(f"TRACE: parse_{self.form_type.lower()}() completed parsing for EIN: '{charity.ein}' in file {xml_filename}",
                          ein=charity.ein)

        except Exception as e:
            if log_error:
                log_error(f"Error parsing form {self.form_type} for EIN {charity.ein if charity else 'Unknown'} in {xml_filename}: {str(e)}",
                         ein=charity.ein if charity else 'Unknown')
            raise  # Re-raise the exception after logging


    def set_form_specific_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Set form-specific fields - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement set_form_specific_fields")

    @classmethod
    def create_parser(cls, form_type: str):
        """Factory method to create appropriate parser based on form type"""
        if form_type == "990":
            from parse_990 import Parser990
            return Parser990()
        elif form_type == "990EZ":
            from parse_990ez import Parser990EZ
            return Parser990EZ()
        elif form_type == "990PF":
            from parse_990pf import Parser990PF
            return Parser990PF()
        elif form_type == "990T":
            from parse_990t import Parser990T
            return Parser990T()
        else:
            return None

    def parse_related_entities(self, root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
        """Parse grants and political contributions using optimized XPath unions"""
        # Parse Schedule I (Grants) - already optimized with XPath unions
        self.parse_schedule_i(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

        # Parse Schedule C (Political Contributions) - already optimized to skip if no Schedule C exists
        self.parse_schedule_c(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)


def main():
    """Main function for testing - to be implemented by subclasses"""
    raise NotImplementedError("Subclasses must implement main function")


if __name__ == "__main__":
    main()