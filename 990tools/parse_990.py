# parse_990.py
import sys
from lxml import etree
from io import BytesIO
import logging
import re
from parse_utils import parse_schedule, MONEY_PATTERN, parse_float_field
from xpaths import XPATHS_990, NAMESPACES
from base_parser import BaseParser
from constants import TRAVEL_KEYWORDS, CONFERENCE_KEYWORDS
from typing import Optional, List, Tuple

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
            if self.verbose:
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
            elif elem.tag.endswith("Organization4947a1NotPFInd"):
                org_type = "4947(a)(1)"
            else:
                org_type = "Unknown"
                log_error("Unexpected org_type element tag {} for EIN {} in {}",
                           elem.tag, context.get('filer_ein', 'Unknown'), xml_filename,
                           ein=context.get('filer_ein', 'Unknown'))
        else:
            log_error("Failed to parse org_type for EIN {} in {}",
                       context.get('filer_ein', 'Unknown'), xml_filename,
                       ein=context.get('filer_ein', 'Unknown'))
            return_data = parse_string_field(root, self.XPATHS, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None, return_element=True)
            org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None else []
            log_error("Form type: {}, Available org_type tags: {} in {}",
                       context.get('form_type', 'Unknown'), org_tags, xml_filename,
                       ein=context.get('filer_ein', 'Unknown'))
            org_type = "Unknown"
        if self.verbose:
            log_error("Parsed org_type {} for EIN {} in {}",
                       org_type, context.get('filer_ein', 'Unknown'), xml_filename,
                       ein=context.get('filer_ein', 'Unknown'))
        return org_type

    def parse_grants_to_others(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None):
        """Parse grants to others for Form 990"""
        total = parse_schedule(root, self.XPATHS, "grant_elements_f", "grant_sub_elements_f", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, debug_eins={"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"})
        total += parse_schedule(root, self.XPATHS, "grant_elements_i", "grant_sub_elements_i", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, debug_eins={"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"})
        return total

    def parse_foreign_expenses(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None):
        """Parse foreign expenses for Form 990"""
        total = parse_schedule(root, self.XPATHS, "foreign_exp_elements", "foreign_exp_sub_elements", "foreign_exp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, debug_eins={"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"})
        return total

    def parse_travel(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None):
        """Parse travel expenses for Form 990"""
        from parse_utils import parse_string_field
        total = 0
        # Cache the schedule_o elements to avoid repeated XPath evaluations
        if 'schedule_o_elements' not in xpath_cache:
            schedule_o_elements = []
            for xpath in self.XPATHS["schedule_o"]:
                result = xpath(root)
                schedule_o_elements.extend(result)
            xpath_cache['schedule_o_elements'] = schedule_o_elements
        else:
            schedule_o_elements = xpath_cache['schedule_o_elements']

        for elem in schedule_o_elements:
            if "TravelGrp" in elem.tag:
                desc = parse_string_field(elem, self.XPATHS, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None)
                if desc is not None:
                    desc_text = desc.upper()
                    # Check for travel keywords anywhere in the description
                    has_travel_keywords = any(keyword in desc_text for keyword in TRAVEL_KEYWORDS)
                    if has_travel_keywords:
                        # Look for any money pattern in the description
                        match = MONEY_PATTERN.search(desc)
                        if match:
                            amount = int(parse_float_field(match.group(1)))
                            total += amount
                            if self.verbose:
                                log_error("Parsed travel_amt ${} from Schedule O in {}",
                                          amount, xml_filename,
                                          ein=context.get('filer_ein', 'Unknown'))
                        else:
                            # If no money pattern found but keywords present, try to extract any number
                            # Look for patterns like "AMOUNT: $1234" or just "$1234"
                            alt_match = re.search(r'(?:AMOUNT:\s*)?\$?([\d,]+\.?\d*)', desc, re.IGNORECASE)
                            if alt_match:
                                try:
                                    amount = int(parse_float_field(alt_match.group(1)))
                                    total += amount
                                    if self.verbose:
                                        log_error("Parsed travel_amt ${} (alt pattern) from Schedule O in {}",
                                                  amount, xml_filename,
                                                  ein=context.get('filer_ein', 'Unknown'))
                                except (ValueError, IndexError):
                                    log_error("Failed to parse travel amount from '{}' in {}",
                                              desc, xml_filename,
                                              ein=context.get('filer_ein', 'Unknown'))
        return total

    def parse_conferences(self, root, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None):
        """Parse conference expenses for Form 990"""
        from parse_utils import parse_string_field
        total = 0
        # Cache the schedule_o elements to avoid repeated XPath evaluations
        if 'schedule_o_elements' not in xpath_cache:
            schedule_o_elements = []
            for xpath in self.XPATHS["schedule_o"]:
                result = xpath(root)
                schedule_o_elements.extend(result)
            xpath_cache['schedule_o_elements'] = schedule_o_elements
        else:
            schedule_o_elements = xpath_cache['schedule_o_elements']

        for elem in schedule_o_elements:
            if "ConferencesMeetingsGrp" in elem.tag:
                desc = parse_string_field(elem, self.XPATHS, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=self.verbose, default=None)
                if desc is not None:
                    desc_text = desc.upper()
                    # Check for conference/meeting keywords anywhere in the description
                    has_conference_keywords = any(keyword in desc_text for keyword in CONFERENCE_KEYWORDS)
                    if has_conference_keywords:
                        # Look for any money pattern in the description
                        match = MONEY_PATTERN.search(desc)
                        if match:
                            amount = int(parse_float_field(match.group(1)))
                            total += amount
                            if self.verbose:
                                log_error("Parsed conferences_amt ${} from Schedule O in {}",
                                          amount, xml_filename,
                                          ein=context.get('filer_ein', 'Unknown'))
                        else:
                            # If no money pattern found but keywords present, try to extract any number
                            # Look for patterns like "AMOUNT: $1234" or just "$1234"
                            alt_match = re.search(r'(?:AMOUNT:\s*)?\$?([\d,]+\.?\d*)', desc, re.IGNORECASE)
                            if alt_match:
                                try:
                                    amount = int(parse_float_field(alt_match.group(1)))
                                    total += amount
                                    if self.verbose:
                                        log_error("Parsed conferences_amt ${} (alt pattern) from Schedule O in {}",
                                                  amount, xml_filename,
                                                  ein=context.get('filer_ein', 'Unknown'))
                                except (ValueError, IndexError):
                                    log_error("Failed to parse conference amount from '{}' in {}",
                                              desc, xml_filename,
                                              ein=context.get('filer_ein', 'Unknown'))
        return total

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

    def set_form_specific_fields(self, data):
        """Set Form 990 specific fields"""
        data["foreign_expenses_ptile"] = None
        data["domestic_misrep_flag"] = data["grift_ratio"] > 10 and data["foreign_expenses_pct"] < 0.1 * 100 if data["total_exp"] > 0 else False
        return data

# Create parser instance
parser_990 = Parser990()

def parse_990(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=None, xpath_match_stats=None):
    """Parse Form 990 - wrapper function for backward compatibility"""
    return parser_990.parse_form(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)

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

    charity, officers, grants, contractors, contributions, address = parse_990(root, xml_file, xpath_cache={}, filer_ein=filer_ein, tax_year=tax_year, form_type=form_type)
    if charity:
        # For backward compatibility, create a row-like output
        row = [
            charity.tax_year, charity.ein, charity.filer_name, None, None,  # business_name_line1, business_name_line2
            charity.receipt_amt, charity.govt_amt, charity.contrib_amt, charity.org_type,
            charity.total_exp, charity.prog_exp, charity.travel_amt, charity.conferences_amt,
            charity.officer_comp, charity.comp_pct, charity.comp_ptile, charity.travel_pct,
            charity.travel_ptile, charity.conferences_pct, charity.conferences_ptile,
            charity.grants_pct, charity.grants_ptile, charity.foreign_expenses_pct,
            charity.foreign_expenses_ptile, charity.grift_ratio, charity.total_assets,
            charity.form_type, charity.denominator, charity.foreign_office,
            charity.foreign_expenses, charity.grants_to_others, charity.domestic_misrep_flag,
            charity.xml_name
        ]
        row_str = [str(x).replace('\t', ' ').replace('\n', ' ') for x in row]
        print('\t'.join(row_str))

if __name__ == "__main__":
    main()