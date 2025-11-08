#!/usr/bin/env python3

from lxml import etree  # type: ignore
from io import BytesIO
from parse_utils import parse_int_field, parse_string_field, clean_name, MONEY_PATTERN, parse_float_field, parse_name_fast
from xpaths_990pf import XPATHS_990PF, NAMESPACES
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from typing import Optional, List, Tuple, Dict, Any, Callable
from logging_utils import log_error, log_debug, log_info, log_warning, create_stub_log_error, create_stub_log_debug
from config import global_config
from base_parser import BaseParser
from constants import DEBUG_EINS, ORG_TYPE_SUFFIXES


class Parser990PF(BaseParser):
    """Parser for IRS Form 990PF"""

    def __init__(self):
        super().__init__("990PF", XPATHS_990PF, NAMESPACES)

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

    def parse_travel(self, root, field, namespaces, xml_filename, context, xpath_cache, form_type, xpath_match_stats=None):
        """Parse travel expenses - 990PF doesn't have TravelGrp"""
        return 0
    def parse_grants_to_others(self, root, field, namespaces, xml_filename, context, xpath_cache, form_type, xpath_match_stats=None):
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
                    grant_amount = int(float((amt_elem.text or "").replace(',', '')))
                except (ValueError, AttributeError):
                    pass

            if grant_amount > 0:
                total += grant_amount

        debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "650869895"}
        if (total > 5_000_000 or (context.getCharity().ein if context.getCharity() else 'Unknown') in debug_eins) and not global_config.is_quiet():
            log_debug("Non-zero grants_to_others $%s for EIN %s, Name %s, TaxYear %s, XML %s",
                       total, context.getCharity().ein if context.getCharity() else 'Unknown', context.getCharity().filer_name if context.getCharity() else 'Unknown', context.getCharity().tax_year if context.getCharity() else 'Unknown', xml_filename)
        elif total == 0 and (context.getCharity().ein if context.getCharity() else 'Unknown') in debug_eins and not global_config.is_quiet():
            return_data = parse_string_field(root, XPATHS_990PF, "return_data", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None, return_element=True)
            child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None and return_data.xpath is not None else []
            log_debug("Zero grants_to_others for EIN %s, Name %s, File %s. ReturnData children: %r",
                               context.getCharity().ein if context.getCharity() else 'Unknown', context.getCharity().filer_name if context.getCharity() else 'Unknown', xml_filename, child_tags)
        return total

    def parse_travel_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        total = 0
        for xpath in XPATHS_990PF["schedule_expenses"]:
            expense_elem = parse_string_field(root, XPATHS_990PF, "schedule_expenses", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None, return_element=True)
            if expense_elem is not None:
                desc = parse_string_field(expense_elem, XPATHS_990PF, "expense_desc", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
                amount = parse_int_field(expense_elem, XPATHS_990PF, "expense_value", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)
                if desc is not None:
                    desc_text = (desc or "").upper()
                    if "TRAVEL" in desc_text:
                        total += amount
                        log_info("Parsed travel_amt ${} from OtherExpensesSchedule in {}",
                                  amount, xml_filename)
        return total

    def parse_conferences_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        total = 0
        for xpath in XPATHS_990PF["schedule_expenses"]:
            expense_elem = parse_string_field(root, XPATHS_990PF, "schedule_expenses", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None, return_element=True)
            if expense_elem is not None:
                desc = parse_string_field(expense_elem, XPATHS_990PF, "expense_desc", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
                amount = parse_int_field(expense_elem, XPATHS_990PF, "expense_value", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)
                if desc is not None:
                    desc_text = (desc or "").upper()
                    if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                        total += amount
                        log_info("Parsed conferences_amt ${} from OtherExpensesSchedule in {}",
                                  amount, xml_filename)
        return total

    def parse_receipt_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_govt_grants_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_contributions_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_total_exp_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_prog_exp_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        return parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_total_assets_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        total = parse_int_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)
        log_info(f"Raw total_assets value: {total} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename}")
        return total

    def parse_filer_name_990pf(root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        return parse_string_field(root, XPATHS_990PF, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default="Unknown")

    def parse_address_990pf(root, xml_filename, context, xpath_cache, charity=None, xpath_match_stats=None):
        """Parse address information from Form 990PF"""
        from parse_utils import parse_string_field
        try:
            namespaces = {'irs': 'http://www.irs.gov/efile'}
            # Parse address components
            address_line1 = parse_string_field(root, XPATHS_990PF, "address_line1", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
            address_line2 = parse_string_field(root, XPATHS_990PF, "address_line2", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
            city = parse_string_field(root, XPATHS_990PF, "city", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
            state = parse_string_field(root, XPATHS_990PF, "state", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
            zip_code = parse_string_field(root, XPATHS_990PF, "zip_code", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)

            # Check if we have at least some address components
            if any([address_line1, address_line2, city, state, zip_code]) and charity is not None:
                # Charity must be available to build address - restructure if needed
                return charity.build_address(
                    address_line1=address_line1 or "",
                    address_line2=address_line2 or "",
                    city=city or "",
                    state=state or "",
                    zip_code=zip_code or ""
                )
            return None
        except Exception as e:
            log_error("Failed to parse address for EIN %s in %s: %s", context.getCharity().ein if context.getCharity() else 'Unknown', xml_filename, str(e))
            return None

    def parse_org_type(self, root, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=None):
        elem = parse_string_field(root, XPATHS_990PF, "org_type", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None, return_element=True)
        if elem is not None:
            log_info("Found org_type element: tag={elem.tag}, text={!r}, attrib={!r} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename}")
            if elem.tag.endswith(tuple(ORG_TYPE_SUFFIXES)):
                if elem.tag.endswith(("Organization501c3ExemptPFInd", "Organization501c3TaxablePFInd")):
                    org_type = "501(c)(3)"
                elif "4947a1" in elem.tag:
                    org_type = "4947(a)(1)"
                else:
                    org_type = "Unknown"
                    log_error(f"Unexpected org_type tag {elem.tag} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename}, defaulting to Unknown")
            elif elem.tag.endswith("Organization4947a1TrtdPFInd"):
                # Handle 4947(a)(1) trust organizations
                org_type = "4947(a)(1)"
                log_info(f"Found org_type tag {elem.tag} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename} (handled as 4947(a)(1))")
            elif elem.tag.endswith("Organization501c3TaxablePFInd"):
                # Handle taxable private foundations
                org_type = "501(c)(3)"
                log_info(f"Found org_type tag {elem.tag} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename} (handled as 501(c)(3) taxable PF)")
            elif elem.tag.endswith("Organization501c3ExemptPFInd"):
                # Handle the case where the tag doesn't end with the expected suffixes but is still valid
                org_type = "501(c)(3)"
                log_info(f"Found org_type tag {elem.tag} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename} (handled as 501(c)(3))")
            else:
                org_type = "Unknown"
                log_error(f"Unexpected org_type tag {elem.tag} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename}, defaulting to Unknown")
        else:
            log_error(f"Failed to parse org_type for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename}")
            return_data = parse_string_field(root, XPATHS_990PF, "return_data", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None, return_element=True)
            org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None and return_data.xpath is not None else []
            log_error(f"Form type: {context.getCharity().form_type if context.getCharity() else 'Unknown'}, Available org_type tags: {org_tags!r} in {xml_filename}")
            org_type = "Unknown"
        log_info(f"Parsed org_type {org_type} for EIN {context.getCharity().ein if context.getCharity() else 'Unknown'} in {xml_filename}")
        return org_type

def parse_990pf(root, xml_filename, xpath_cache, context, xpath_match_stats=None):
    """Parse Form 990PF - now uses context instead of returning tuples"""
    from pending_database_context import PendingDatabaseContext

    if not isinstance(context, PendingDatabaseContext):
        raise ValueError("context must be a PendingDatabaseContext instance")

    # Validate charity exists
    charity = context.getCharity()
    if not charity or not charity.ein or charity.ein == "Unknown":
        raise ValueError(f"Invalid charity in context for Form 990PF parsing in file {xml_filename}")

    parser_990pf.parse_form(root, xml_filename, xpath_cache, context, xpath_match_stats=xpath_match_stats)

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