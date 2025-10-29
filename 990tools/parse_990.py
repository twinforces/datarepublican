# parse_990.py
import sys
from lxml import etree  # type: ignore
from io import BytesIO
import logging
import re
from parse_utils import MONEY_PATTERN, parse_float_field, parse_string_field
from xpaths import XPATHS_990, NAMESPACES
from base_parser import BaseParser
from constants import TRAVEL_KEYWORDS, CONFERENCE_KEYWORDS
from typing import Optional, List, Tuple
from logging_utils import log_error, log_debug, log_info, log_warning
from config import global_config
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address

class Parser990(BaseParser):
    """Parser for IRS Form 990"""

    def __init__(self):
        super().__init__("990", XPATHS_990, NAMESPACES)
        self.verbose = False  # Add verbose attribute

    def parse_org_type(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None):
        """Parse organization type for Form 990"""
        from parse_utils import parse_string_field
        elem = parse_string_field(root, self.XPATHS, "org_type", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None, return_element=True)
        if elem is not None:
            if self.verbose and not global_config.is_quiet() and log_error is not None:
                log_error("Found org_type element: tag={}, text={}, attrib={} for EIN {} in {}",
                           elem.tag, elem.text, elem.attrib, context.get('filer_ein', 'Unknown'), xml_filename,
                           ein=context.get('filer_ein', 'Unknown'))
            if elem.tag.endswith("Organization501cInd"):
                type_num = elem.get("organization501cTypeTxt")
                if type_num and type_num.isdigit() and 1 <= int(type_num) <= 29:
                    org_type = f"501(c)({type_num})"
                elif elem.text and "X" in elem.text.upper():
                    org_type = "501(c)(3)"
                else:
                    org_type = "501(c)(3)"
            elif elem.tag.endswith("Organization501c3Ind"):
                org_type = "501(c)(3)"
            elif elem.tag.endswith("Organization4947a1NotPFInd") or elem.tag.endswith("Organization4947a1TrtdPFInd"):
                org_type = "4947(a)(1)"
            else:
                org_type = "Unknown"
                if not global_config.is_quiet() and log_error is not None:
                    log_error("Unexpected org_type element tag {} for EIN {} in {}",
                               elem.tag, context.get('filer_ein', 'Unknown'), xml_filename,
                               ein=context.get('filer_ein', 'Unknown'))
        else:
            if not global_config.is_quiet() and log_error is not None:
                log_error("Failed to parse org_type for EIN {} in {}",
                           context.get('filer_ein', 'Unknown'), xml_filename,
                           ein=context.get('filer_ein', 'Unknown'))
            return_data = parse_string_field(root, self.XPATHS, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None, return_element=True)
            org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None and return_data.xpath is not None else []
            if not global_config.is_quiet() and log_error is not None:
                log_error("Form type: {}, Available org_type tags: {} in {}",
                           context.get('form_type', 'Unknown'), org_tags, xml_filename,
                           ein=context.get('filer_ein', 'Unknown'))
            org_type = "Unknown"
        if self.verbose and not global_config.is_quiet() and log_error is not None:
            log_error("Parsed org_type {} for EIN {} in {}",
                       org_type, context.get('filer_ein', 'Unknown'), xml_filename,
                       ein=context.get('filer_ein', 'Unknown'))
        return org_type

    def parse_grants_to_others(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None):
        """Parse grants to others for Form 990"""
        # Use the schedule parsing methods which add directly to context
        charity = context.getCharity() if hasattr(context, 'getCharity') else None
        if charity:
            self.parse_schedule_i(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)
        # For foreign grants, we need to parse Schedule F as well
        # But for now, just return 0 since the actual grant objects are added to context
        return 0

    def parse_foreign_expenses(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None):
        """Parse foreign expenses for Form 990"""
        # For now, just return 0 since foreign expenses parsing is complex
        # and the actual foreign expense objects would be added to context
        return 0


    def get_field_parsers(self):
        """Get field parsers for Form 990"""
        return [
            ("receipt", self.parse_receipt),
            ("govt_grants", self.parse_govt_grants),
            ("contributions", self.parse_contributions),
            ("total_exp", self.parse_total_exp),
            ("prog_exp", self.parse_prog_exp),
            ("travel", self.parse_travel),
            ("conferences", self.parse_conferences),
            ("officer_comp", self.parse_officer_comp),
            ("grants_to_others", self.parse_grants_to_others),
            ("foreign_expenses", self.parse_foreign_expenses),
            ("total_assets", self.parse_total_assets),
            ("org_type", self.parse_org_type),
            ("foreign_office", self.parse_foreign_office)
        ]

    def parse_related_entities(self, root, xml_filename, context, xpath_cache, charity=None, log_error=None, xpath_match_stats=None):
        """Parse grants, contractors, and political contributions for Form 990"""
        # Parse Schedule I (Grants)
        self.parse_schedule_i(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

        # Parse Schedule C (Political Contributions)
        self.parse_schedule_c(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

        # Parse Schedule L (Contractors)
        self.parse_schedule_l(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)





    def set_form_specific_fields(self, data):
        """Set Form 990 specific fields"""
        data["foreign_expenses_ptile"] = None
        data["domestic_misrep_flag"] = data["grift_ratio"] > 10 and data["foreign_expenses_pct"] < 0.1 * 100 if data["total_exp"] > 0 else False
        return data

# Create parser instance
parser_990 = Parser990()

def parse_990(root, xml_filename, xpath_cache, context, log_error=None, xpath_match_stats=None):
    """Parse Form 990 - now uses context instead of returning tuples"""
    from pending_database_context import PendingDatabaseContext

    if not isinstance(context, PendingDatabaseContext):
        raise ValueError("context must be a PendingDatabaseContext instance")

    # Validate charity exists
    charity = context.getCharity()
    if not charity or not charity.ein or charity.ein == "Unknown":
        raise ValueError(f"Invalid charity in context for Form 990 parsing in file {xml_filename}")

    parser_990.parse_form(root, xml_filename, xpath_cache, context)

def main():
    """Main function for testing Form 990 parser"""
    if len(sys.argv) != 2:
        print("Usage: python parse_990.py <xml_file>", file=sys.stderr)
        sys.exit(1)

    xml_file = sys.argv[1]
    try:
        with open(xml_file, 'rb') as f:
            xml_content = f.read()
    except IOError as e:
        print("Error reading XML file {}: {}", xml_file, e, file=sys.stderr)
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
            raw_ein = result[0].text.strip()
            if raw_ein.isdigit():
                filer_ein = f"{int(raw_ein):09d}"
            else:
                filer_ein = "Unknown"
            break
    if filer_ein is None:
        filer_ein = "Unknown"

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
    parse_990(root, xml_file, {}, context)

    # Print object counts
    print(context.getObjectCounts())

if __name__ == "__main__":
    main()