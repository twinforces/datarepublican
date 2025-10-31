# parse_990pf.py
import sys
from lxml import etree  # type: ignore
from io import BytesIO
import logging
from nameparser import HumanName
from parse_utils import parse_int_field, parse_string_field, clean_name, MONEY_PATTERN, parse_float_field, parse_name_fast
from xpaths import XPATHS_990PF, NAMESPACES
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from typing import Optional, List, Tuple, Dict, Any
from logging_utils import get_logger, log_error as proper_log_error, log_debug as proper_log_debug, log_error, log_debug, log_info, log_warning, create_stub_log_error, create_stub_log_debug
from config import global_config
from base_parser import BaseParser

logger = None
log_error = None
log_debug = None
log_info = None
verbose = False
quiet = global_config.is_quiet()
from constants import DEBUG_EINS, ORG_TYPE_SUFFIXES

class Parser990PF(BaseParser):
    """Parser for IRS Form 990PF"""

    def __init__(self):
        super().__init__("990PF", XPATHS_990PF, NAMESPACES)
        self.verbose = False  # Add verbose attribute

    def get_field_parsers(self):
        """Get field parsers for Form 990PF"""
        return [
            ("receipt", self.parse_receipt),
            ("total_exp", self.parse_total_exp),
            ("prog_exp", self.parse_prog_exp),
            ("officer_comp", self.parse_officer_comp),
            ("grants_to_others", self.parse_grants_to_others),
            ("total_assets", self.parse_total_assets),
            ("travel", self.parse_travel),
            ("conferences", self.parse_conferences),
            ("org_type", self.parse_org_type),
            ("foreign_office", self.parse_foreign_office)
        ]

    def set_form_specific_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Set form-specific fields for 990PF"""
        # PF forms don't have govt_grants or contributions
        data["govt_grants"] = None
        data["contributions"] = None
        data["foreign_expenses"] = None
        data["foreign_expenses_pct"] = None
        data["foreign_expenses_ptile"] = None
        data["foreign_office"] = False
        data["domestic_misrep_flag"] = False
        return data

    def parse_travel(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse travel expenses - 990PF doesn't have TravelGrp"""
        return 0
    def parse_grants_to_others(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        """Parse grants to others for Form 990PF"""
        total = 0
        # Parse grants from Supplementary Information
        grant_elements = []
        for xpath in XPATHS_990PF["grant_elements"]:
            result = xpath(root)
            grant_elements.extend(result)

        for entry in grant_elements:
            # Parse grant amount
            amt_elem = entry.find("irs:Amt", namespaces=NAMESPACES)
            grant_amount = 0
            if amt_elem is not None and amt_elem.text:
                try:
                    grant_amount = int(float(amt_elem.text.replace(',', '')))
                except (ValueError, AttributeError):
                    pass

            if grant_amount > 0:
                total += grant_amount

        debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "650869895"}
        if (total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins) and not quiet and log_debug is not None and logger is not None:
            log_debug(logger, "Non-zero grants_to_others $%s for EIN %s, Name %s, TaxYear %s, XML %s",
                      total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename)
        elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins and not quiet and log_debug is not None and logger is not None:
            return_data = parse_string_field(root, XPATHS_990PF, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None, return_element=True)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None and return_data.xpath is not None else []
            log_debug(logger, "Zero grants_to_others for EIN %s, Name %s, File %s. ReturnData children: %s",
                      context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags)
        return total

    def parse_org_type(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
        elem = parse_string_field(root, XPATHS_990PF, "org_type", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None, return_element=True)
        if elem is not None:
            if self.verbose and not quiet:
                log_error(f"Found org_type element: tag={elem.tag}, text={elem.text}, attrib={elem.attrib} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}",
                           ein=context.get('filer_ein', 'Unknown'))
            if elem.tag.endswith(tuple(ORG_TYPE_SUFFIXES)):
                if elem.tag.endswith(("Organization501c3ExemptPFInd", "Organization501c3TaxablePFInd")):
                    org_type = "501(c)(3)"
                elif "4947a1" in elem.tag:
                    org_type = "4947(a)(1)"
                else:
                    org_type = "Unknown"
                    log_error(f"Unexpected org_type tag {elem.tag} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}, defaulting to Unknown",
                               ein=context.get('filer_ein', 'Unknown'))
            elif elem.tag.endswith("Organization4947a1TrtdPFInd"):
                # Handle 4947(a)(1) trust organizations
                org_type = "4947(a)(1)"
                if self.verbose and not quiet:
                    log_error(f"Found org_type tag {elem.tag} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename} (handled as 4947(a)(1))",
                               ein=context.get('filer_ein', 'Unknown'))
            elif elem.tag.endswith("Organization501c3TaxablePFInd"):
                # Handle taxable private foundations
                org_type = "501(c)(3)"
                if self.verbose and not quiet:
                    log_error(f"Found org_type tag {elem.tag} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename} (handled as 501(c)(3) taxable PF)",
                               ein=context.get('filer_ein', 'Unknown'))
            elif elem.tag.endswith("Organization501c3ExemptPFInd"):
                # Handle the case where the tag doesn't end with the expected suffixes but is still valid
                org_type = "501(c)(3)"
                if self.verbose and not quiet:
                    log_error(f"Found org_type tag {elem.tag} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename} (handled as 501(c)(3))",
                               ein=context.get('filer_ein', 'Unknown'))
            else:
                org_type = "Unknown"
                if not quiet:
                    if not quiet:
                        log_error(f"Unexpected org_type tag {elem.tag} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}, defaulting to Unknown",
                                   ein=context.get('filer_ein', 'Unknown'))
        else:
            if not quiet:
                log_error(f"Failed to parse org_type for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}",
                           ein=context.get('filer_ein', 'Unknown'))
            return_data = parse_string_field(root, XPATHS_990PF, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None, return_element=True)
            org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None and return_data.xpath is not None else []
            if not quiet:
                log_error(f"Form type: {context.get('form_type', 'Unknown')}, Available org_type tags: {org_tags} in {xml_filename}",
                           ein=context.get('filer_ein', 'Unknown'))
            org_type = "Unknown"
        if self.verbose and not quiet:
            log_error(f"Parsed org_type {org_type} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}",
                       ein=context.get('filer_ein', 'Unknown'))
        return org_type

def parse_officer_comp_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    form_type = context.get('form_type', 'Unknown')
    total = 0
    officer_entries = []

    elements = []
    for xpath in XPATHS_990PF["officer_comp_elements"]:
        result = xpath(root)
        elements.extend(result)

    for elem in elements:
        # Parse officer name from PersonNm element
        name_elem = parse_string_field(elem, XPATHS_990PF, "officer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        # Parse compensation amount
        value = parse_int_field(elem, XPATHS_990PF, "officer_comp", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

        if name_elem and value > 0:
            cleaned_name = clean_name(name_elem)
            first_name, last_name = parse_name_fast(cleaned_name)

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

            if (verbose or context.get('filer_ein', 'Unknown') in DEBUG_EINS) and not quiet and log_info is not None and logger is not None:
                log_info(logger, "Parsed officer %s %s compensation: $%s for EIN %s in %s",
                         first_name, last_name, value, context.get('filer_ein', 'Unknown'), xml_filename)

    if total > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
        if not quiet:
            log_error(f"Suspicious officer_comp ${total} exceeds total_exp ${context.get('total_exp', 0)} in {xml_filename}",
                      ein=context.get('filer_ein', 'Unknown'))
        total = 0
        officer_entries = []

    return total, officer_entries

def parse_grants_to_others_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
    
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "650869895"}
    if (total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins) and not quiet and log_debug is not None and logger is not None:
        log_debug(logger, "Non-zero grants_to_others $%s for EIN %s, Name %s, TaxYear %s, XML %s",
                  total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename)
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins and not quiet and log_debug is not None and logger is not None:
        return_data = parse_string_field(root, XPATHS_990PF, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None and return_data.xpath is not None else []
        log_debug(logger, "Zero grants_to_others for EIN %s, Name %s, File %s. ReturnData children: %s",
                  context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags)
    return total

def parse_travel_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    for xpath in XPATHS_990PF["schedule_expenses"]:
        expense_elem = parse_string_field(root, XPATHS_990PF, "schedule_expenses", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        if expense_elem is not None:
            desc = parse_string_field(expense_elem, XPATHS_990PF, "expense_desc", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            amount = parse_int_field(expense_elem, XPATHS_990PF, "expense_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
            if desc is not None:
                desc_text = desc.upper()
                if "TRAVEL" in desc_text:
                    total += amount
                    if verbose and not quiet:
                        log_error(f"Parsed travel_amt ${total} from OtherExpensesSchedule in {xml_filename}",
                                   ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_conferences_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    for xpath in XPATHS_990PF["schedule_expenses"]:
        expense_elem = parse_string_field(root, XPATHS_990PF, "schedule_expenses", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        if expense_elem is not None:
            desc = parse_string_field(expense_elem, XPATHS_990PF, "expense_desc", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            amount = parse_int_field(expense_elem, XPATHS_990PF, "expense_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
            if desc is not None:
                desc_text = desc.upper()
                if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                    total += amount
                    if verbose and not quiet:
                        log_error(f"Parsed conferences_amt ${total} from OtherExpensesSchedule in {xml_filename}",
                                   ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_receipt_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_govt_grants_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_contributions_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_total_exp_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_prog_exp_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_total_assets_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
    if total > 0 and verbose and not quiet:
        log_error(f"Raw total_assets value: {total} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}",
                   ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_filer_name_990pf(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_string_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="Unknown")

def parse_address_990pf(root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
    """Parse address information from Form 990PF"""
    try:
        namespaces = {'irs': 'http://www.irs.gov/efile'}
        # Parse address components
        address_line1 = parse_string_field(root, XPATHS_990PF, "address_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        address_line2 = parse_string_field(root, XPATHS_990PF, "address_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        city = parse_string_field(root, XPATHS_990PF, "city", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        state = parse_string_field(root, XPATHS_990PF, "state", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        zip_code = parse_string_field(root, XPATHS_990PF, "zip_code", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

        # Check if we have at least some address components
        if any([address_line1, address_line2, city, state, zip_code]) and charity is not None:
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
        if not quiet and log_error is not None:
            log_error("Failed to parse address for EIN %s in %s: %s", context.get('filer_ein', 'Unknown'), xml_filename, str(e), ein=context.get('filer_ein', 'Unknown'))
        return None

def parse_990pf(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None, context=None):
    """Parse Form 990PF - now uses context instead of returning tuples"""
    from pending_database_context import PendingDatabaseContext

    if not isinstance(context, PendingDatabaseContext):
        raise ValueError("context must be a PendingDatabaseContext instance")

    # Validate EIN before parsing
    if not filer_ein or filer_ein == "Unknown":
        raise ValueError(f"Invalid EIN '{filer_ein}' for Form 990PF parsing in file {xml_filename}")

    # Parse and add to context
    parse_990pf_legacy(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats, context=context)

def parse_990pf_legacy(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None, context=None) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution], Optional[Address], List[Address]]:
    """Parse Form 990PF and add objects to context"""
    from pending_database_context import PendingDatabaseContext
    if context is not None and not isinstance(context, PendingDatabaseContext):
        raise ValueError("context parameter must be a PendingDatabaseContext instance")
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    context = {
        'filer_ein': filer_ein,
        'tax_year': tax_year,
        'form_type': form_type
    }

    # Validate EIN before proceeding
    if not context["filer_ein"] or context["filer_ein"] == "Unknown":
        raise ValueError(f"Invalid EIN '{context['filer_ein']}' for Form 990PF parsing in file {xml_filename}")

    if context["form_type"] != "990PF":
        if not global_config.is_quiet():
            log_error(f"XML {xml_filename} is not a Form 990PF (form_type: {context['form_type']}), skipping",
                         ein=context['filer_ein'])
        return None, [], [], [], [], None, []

    context["filer_name"] = parse_filer_name_990pf(root, "filer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
    context["business_name_line1"] = parse_string_field(root, XPATHS_990PF, "business_name_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
    context["business_name_line2"] = parse_string_field(root, XPATHS_990PF, "business_name_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

    fields = [
        ("receipt", parse_receipt_990pf),
        ("total_exp", parse_total_exp_990pf),
        ("prog_exp", parse_prog_exp_990pf),
        ("officer_comp", parse_officer_comp_990pf),
        ("grants_to_others", parse_grants_to_others_990pf),
        ("total_assets", parse_total_assets_990pf),
        ("travel", parse_travel_990pf),
        ("conferences", parse_conferences_990pf),
        ("org_type", Parser990PF().parse_org_type),
        ("govt_grants", lambda root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None: None),  # PF forms don't have government grants
        ("contributions", lambda root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None: None),  # PF forms don't have contributions
        ("foreign_expenses", lambda root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None: None),  # Will be implemented later if needed
        ("foreign_office", lambda root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None: False),  # PF forms typically don't have foreign offices
    ]
    data = {}
    officer_entries = []
    for field, func in fields:
        if field == "officer_comp":
            total, entries = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
            data[field] = total
            officer_entries.extend(entries)
        else:
            data[field] = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

    def calculate_percentage(value, denom):
        if denom == 0 or value is None or denom is None:
            return 0.0
        return round((value / denom) * 100, 2)

    data["comp_pct"] = calculate_percentage(data["officer_comp"], data["total_exp"])
    data["travel_pct"] = calculate_percentage(data["travel"], data["total_exp"])
    data["conferences_pct"] = calculate_percentage(data["conferences"], data["total_exp"])
    data["grants_pct"] = calculate_percentage(data["grants_to_others"], data["total_exp"])
    data["grift_ratio"] = calculate_percentage(data["officer_comp"] + data["travel"] + data["conferences"], data["total_exp"])

    data["denominator"] = data["total_assets"] + data["receipt"]
    data["comp_ptile"] = None
    data["travel_ptile"] = None
    data["conferences_ptile"] = None
    data["grants_ptile"] = None
    data["foreign_expenses_ptile"] = None
    data["foreign_expenses_pct"] = None
    data["foreign_expenses"] = None
    data["foreign_office"] = data["foreign_office"]  # Already set above
    data["grift_ratio"] = calculate_percentage(data["officer_comp"] + data["travel"] + data["conferences"], data["total_exp"])
    data["domestic_misrep_flag"] = False  # PF forms don't have foreign expenses to compare

    data["govt_grants"] = None
    data["contributions"] = None
    data["domestic_misrep_flag"] = False

    # Create Charity dataclass
    charity = Charity(
        ein=context["filer_ein"],
        tax_year=context["tax_year"],
        filer_name=context["filer_name"] or "Unknown",
        receipt_amt=data["receipt"],
        govt_amt=data["govt_grants"],  # Will be "n/a"
        contrib_amt=data["contributions"],  # Will be "n/a"
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
        foreign_expenses_pct=data["foreign_expenses_pct"],  # Will be "n/a"
        foreign_expenses_ptile=data["foreign_expenses_ptile"],  # Will be "n/a"
        grift_ratio=data["grift_ratio"],
        total_assets=data["total_assets"],
        form_type=context["form_type"],
        denominator=data["denominator"],
        foreign_office=False,  # Will be False
        foreign_expenses=data["foreign_expenses"],  # Will be "n/a"
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
            full_name=entry["full_name"],
            compensation=entry["amount"],
            tax_year=tax_year
        )
        officers.append(officer)

    # Parse address information
    address = parse_address_990pf(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

    # Parse additional balance sheet data
    total_assets_boy = parse_int_field(root, XPATHS_990PF, "total_assets_boy", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
    total_liabilities = parse_int_field(root, XPATHS_990PF, "total_liabilities", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
    net_assets = parse_int_field(root, XPATHS_990PF, "net_assets", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

    # Store additional data in charity object if needed
    if charity:
        charity.total_assets_boy = total_assets_boy
        charity.total_liabilities = total_liabilities
        charity.net_assets = net_assets

    # Debug logging for address components
    if address and not global_config.is_quiet() and log_debug is not None and logger is not None:
        log_debug(logger, "DEBUG: Address parsed for EIN %s: line1='%s', line2='%s', city='%s', state='%s', zip='%s', canonical='%s'",
                  address.ein, address.address_line1, address.address_line2, address.city, address.state, address.zip_code, address.canonical_address)
    elif not global_config.is_quiet() and log_debug is not None and logger is not None:
        log_debug(logger, "DEBUG: No address parsed for EIN %s in file %s", context.get('filer_ein', 'Unknown'), xml_filename)

    # Parse grants, contractors, and political contributions and add to context
    if context is not None and not isinstance(context, dict):
        parse_related_entities_990pf(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)
        return  # Context-based parsing doesn't return values

    # Legacy return for backward compatibility
    grants, contractors, contributions, grant_addresses = parse_related_entities_990pf(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

    if not global_config.is_quiet() and log_debug is not None and logger is not None:
        log_debug(logger, "TRACE: parse_990pf() returning Charity, Officers, Grants, Contractors, Contributions, Address, and Grant Addresses for EIN: '%s' in file %s", charity.ein, xml_filename)
    return charity, officers, grants, contractors, contributions, address, grant_addresses

def parse_related_entities_990pf(root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
    """Parse grants, contractors, and political contributions for Form 990PF"""
    from xpaths import NAMESPACES

    grants = []
    contractors = []
    contributions = []
    addresses = []  # For grant recipient addresses

    # Parse grants from Supplementary Information
    grant_elements = []
    for xpath in XPATHS_990PF["grant_elements"]:
        result = xpath(root)
        grant_elements.extend(result)

    for entry in grant_elements:
        # Parse grant recipient name - combine BusinessNameLine1Txt and BusinessNameLine2Txt if present
        name_line1_elem = entry.find(".//irs:RecipientBusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES)
        name_line2_elem = entry.find(".//irs:RecipientBusinessName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES)

        grant_name_parts = []
        if name_line1_elem is not None and name_line1_elem.text:
            grant_name_parts.append(name_line1_elem.text.strip())
        if name_line2_elem is not None and name_line2_elem.text:
            grant_name_parts.append(name_line2_elem.text.strip())
        grant_name = " ".join(grant_name_parts) if grant_name_parts else None

        # Parse grant amount
        amt_elem = entry.find("irs:Amt", namespaces=NAMESPACES)
        grant_amount = 0
        if amt_elem is not None and amt_elem.text:
            try:
                grant_amount = int(float(amt_elem.text.replace(',', '')))
            except (ValueError, AttributeError):
                pass

        if grant_amount > 0:
            # Create grant record
            grant = Grant(
                filer_ein=context["filer_ein"],
                filer_name=context["filer_name"],
                grant_ein=None,  # PF forms don't typically have recipient EINs
                grant_amt=grant_amount,
                tax_year=context["tax_year"]
            )
            if grant_name:
                grant.grantee_name = grant_name
            grants.append(grant)

            # Parse grant recipient address for matching (PF forms don't have EINs)
            grant_address = None
            if grant_name:
                # Try to extract address from grant entry
                addr_line1_elem = entry.find(".//irs:RecipientUSAddress/irs:AddressLine1Txt", namespaces=NAMESPACES)
                city_elem = entry.find(".//irs:RecipientUSAddress/irs:CityNm", namespaces=NAMESPACES)
                state_elem = entry.find(".//irs:RecipientUSAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES)
                zip_elem = entry.find(".//irs:RecipientUSAddress/irs:ZIPCd", namespaces=NAMESPACES)

                addr_line1 = addr_line1_elem.text.strip() if addr_line1_elem is not None and addr_line1_elem.text else None
                city = city_elem.text.strip() if city_elem is not None and city_elem.text else None
                state = state_elem.text.strip() if state_elem is not None and state_elem.text else None
                zip_code = zip_elem.text.strip() if zip_elem is not None and zip_elem.text else None

                if any([addr_line1, city, state, zip_code]):
                    grant_address = grant.build_address(
                        address_line1=addr_line1,
                        address_line2=None,
                        city=city,
                        state=state,
                        zip_code=zip_code
                    )
                    if grant_address:
                        grant_address.address_type = 'grant'
                        grant_address.grantee_name = grant_name
                        addresses.append(grant_address)

    # Parse contractors from Schedule L if present
    contractor_elements = []
    for xpath in XPATHS_990PF["contractor_elements"]:
        result = xpath(root)
        contractor_elements.extend(result)

    for elem in contractor_elements:
        # Extract contractor name
        name_xpaths = [
            etree.XPath("irs:ContractorName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
            etree.XPath("irs:ContractorName/irs:BusinessNameLine1", namespaces=NAMESPACES),
            etree.XPath("irs:PersonNm", namespaces=NAMESPACES),
            etree.XPath("irs:Name", namespaces=NAMESPACES),
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
                'filer_ein': context["filer_ein"],
                'filer_name': context["filer_name"],
                'name': contractor_name,
                'amount': amount,
                'ein': contractor_ein,
                'tax_year': context["tax_year"]
            }
            contractors.append(contractor_data)

    return grants, contractors, contributions, addresses

def main():
    if len(sys.argv) != 2:
        print("Usage: python parse_990pf.py <xml_file>", file=sys.stderr)
        sys.exit(1)

    xml_file = sys.argv[1]
    try:
        with open(xml_file, 'rb') as f:
            xml_content = f.read()
    except IOError as e:
        print(f"Error reading XML file {xml_file}: {e}", file=sys.stderr)
        sys.exit(1)

    parser = etree.XMLParser(recover=True)
    tree = etree.parse(BytesIO(xml_content), parser)
    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile'}

    form_type_paths = [
        etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces=namespaces),
        etree.XPath(".//ReturnHeader/ReturnTypeCd")
    ]
    form_type = None
    for xpath in form_type_paths:
        result = xpath(root)
        if result:
            form_type = result[0].text
            break
    form_type = form_type if form_type is not None else "Unknown"

    tax_year_paths = [
        etree.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces=namespaces),
        etree.XPath(".//ReturnHeader/TaxYr")
    ]
    tax_year = None
    for xpath in tax_year_paths:
        result = xpath(root)
        if result:
            tax_year = result[0].text
            break
    tax_year = tax_year if tax_year is not None else "Unknown"
    if tax_year == "Unknown":
        tax_year = xml_file[:4] if xml_file[:4].isdigit() else "Unknown"
    else:
        try:
            int(tax_year)
        except ValueError:
            tax_year = xml_file[:4] if xml_file[:4].isdigit() else "Unknown"

    filer_ein_paths = [
        etree.XPath(".//irs:ReturnHeader/irs:Filer/irs:EIN", namespaces=namespaces),
        etree.XPath(".//ReturnHeader/Filer/EIN")
    ]
    filer_ein = None
    for xpath in filer_ein_paths:
        result = xpath(root)
        if result:
            filer_ein = result[0].text.strip()
            break
    filer_ein = filer_ein if filer_ein is not None else "Unknown"

    # Create context and charity
    from pending_database_context import PendingDatabaseContext
    context = PendingDatabaseContext()
    charity = Charity(
        ein=filer_ein,
        tax_year=tax_year,
        form_type=form_type,
        xml_name=xml_file
    )
    context.addObjectToDatabase(charity)

    # Parse using context
    parse_990pf(root, xml_file, {}, context)

    # Print object counts
    print(context.getObjectCounts())

if __name__ == "__main__":
    main()