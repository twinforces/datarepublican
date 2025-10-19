# parse_990.py
import sys
from lxml import etree
from io import BytesIO
import logging
import re
from nameparser import HumanName
from parse_utils import parse_int_field, parse_string_field, parse_schedule, clean_name, MONEY_PATTERN, parse_float_field
from xpaths import XPATHS_990, NAMESPACES
from irs990processorDC import Charity as DCCharity, Officer as DCOfficer, Grant as DCGrant, Contractor as DCContractor, PoliticalContribution as DCPoliticalContribution, Address as DCAddress
from typing import Optional, List, Tuple

logger = None
log_error = None
log_debug = None
verbose = False
DEBUG_EINS = set()

ORG_TYPE_SUFFIXES = frozenset([
    "Organization501c3Ind", "Organization501cInd", "Organization4947a1NotPFInd"
])

def set_logger(new_logger, new_log_error, new_log_debug=None, is_verbose=False, debug_eins=None):
    global logger, log_error, log_debug, verbose, DEBUG_EINS
    logger = new_logger
    log_error = new_log_error
    log_debug = new_log_debug or new_log_error  # fallback to log_error if log_debug not provided
    verbose = is_verbose
    DEBUG_EINS = debug_eins if debug_eins is not None else set()

def stub_log_error(msg_format, *args, ein=None, exc_info=False):
    global logger
    if logger is None:
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(__name__)
    if exc_info:
        logger.info(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
    else:
        logger.error(msg_format.format(*args) if args else msg_format)

if log_error is None:
    log_error = stub_log_error

def stub_log_debug(msg_format, *args, ein=None, exc_info=False):
    global logger
    if logger is None:
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(__name__)
    if exc_info:
        logger.debug(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
    else:
        logger.debug(msg_format.format(*args) if args else msg_format)

if log_debug is None:
    log_debug = stub_log_debug

def parse_org_type_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    elem = parse_string_field(root, XPATHS_990, "org_type", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
    if elem is not None:
        if verbose:
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
        return_data = parse_string_field(root, XPATHS_990, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None else []
        log_error("Form type: {}, Available org_type tags: {} in {}", 
                  context.get('form_type', 'Unknown'), org_tags, xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
        org_type = "Unknown"
    if verbose:
        log_error("Parsed org_type {} for EIN {} in {}", 
                  org_type, context.get('filer_ein', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
    return org_type

def parse_officer_comp_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    form_type = context.get('form_type', 'Unknown')
    total = 0
    officer_entries = []
    
    elements = []
    for xpath in XPATHS_990["officer_comp_elements"]:
        result = xpath(root)
        elements.extend(result)
    
    for elem in elements:
        name_elem = parse_string_field(elem, XPATHS_990, "officer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        value_elem = parse_string_field(elem, XPATHS_990, "officer_comp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        
        if name_elem and value_elem:
            cleaned_name = clean_name(name_elem)
            name = HumanName(cleaned_name)
            first_name = name.first or "Unknown"
            last_name = name.last or "Unknown"
            value = parse_int_field(elem, XPATHS_990, "officer_comp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
            
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

def parse_grants_to_others_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = parse_schedule(root, XPATHS_990, "grant_elements_f", "grant_sub_elements_f", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins={"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"})
    total += parse_schedule(root, XPATHS_990, "grant_elements_i", "grant_sub_elements_i", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins={"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"})
    return total

def parse_foreign_expenses_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = parse_schedule(root, XPATHS_990, "foreign_exp_elements", "foreign_exp_sub_elements", "foreign_exp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins={"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"})
    return total

def parse_receipt_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_govt_grants_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_contributions_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_total_exp_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_prog_exp_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_travel_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    # Cache the schedule_o elements to avoid repeated XPath evaluations
    if 'schedule_o_elements' not in xpath_cache:
        schedule_o_elements = []
        for xpath in XPATHS_990["schedule_o"]:
            result = xpath(root)
            schedule_o_elements.extend(result)
        xpath_cache['schedule_o_elements'] = schedule_o_elements
    else:
        schedule_o_elements = xpath_cache['schedule_o_elements']

    for elem in schedule_o_elements:
        if "TravelGrp" in elem.tag:
            desc = parse_string_field(elem, XPATHS_990, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            if desc is not None:
                desc_text = desc.upper()
                if "TRAVEL" in desc_text:
                    match = MONEY_PATTERN.search(desc)
                    if match:
                        amount = int(parse_float_field(match.group(1)))
                        total += amount
                        if verbose:
                            log_error("Parsed travel_amt ${} from Schedule O in {}",
                                      amount, xml_filename,
                                      ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_conferences_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    # Cache the schedule_o elements to avoid repeated XPath evaluations
    if 'schedule_o_elements' not in xpath_cache:
        schedule_o_elements = []
        for xpath in XPATHS_990["schedule_o"]:
            result = xpath(root)
            schedule_o_elements.extend(result)
        xpath_cache['schedule_o_elements'] = schedule_o_elements
    else:
        schedule_o_elements = xpath_cache['schedule_o_elements']

    for elem in schedule_o_elements:
        if "ConferencesMeetingsGrp" in elem.tag:
            desc = parse_string_field(elem, XPATHS_990, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            if desc is not None:
                desc_text = desc.upper()
                if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                    match = MONEY_PATTERN.search(desc)
                    if match:
                        amount = int(parse_float_field(match.group(1)))
                        total += amount
                        if verbose:
                            log_error("Parsed conferences_amt ${} from Schedule O in {}",
                                      amount, xml_filename,
                                      ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_total_assets_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_foreign_office_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    elem = parse_string_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
    return elem.strip().upper() == 'X' if elem is not None else False

def parse_filer_name_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_string_field(root, XPATHS_990, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="Unknown")

def parse_address_990(root, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    """Parse address information from Form 990"""
    try:
        namespaces = {'irs': 'http://www.irs.gov/efile'}
        # Parse address components
        address_line1 = parse_string_field(root, XPATHS_990, "address_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        address_line2 = parse_string_field(root, XPATHS_990, "address_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        city = parse_string_field(root, XPATHS_990, "city", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        state = parse_string_field(root, XPATHS_990, "state", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        zip_code = parse_string_field(root, XPATHS_990, "zip_code", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

        # Split ZIP code into zip_code (first 5) and zip4 (last 4)
        zip5 = None
        zip4 = None
        if zip_code:
            stripped = zip_code.strip()
            if len(stripped) >= 5:
                zip5 = stripped[:5]
                if len(stripped) >= 9:
                    zip4 = stripped[5:9]

        # Build canonical address
        address_parts = [part for part in [address_line1, address_line2, city, state, zip5] if part]
        canonical_address = ", ".join(address_parts) if address_parts else None

        if canonical_address:
            return DCAddress(
                ein=context["filer_ein"],
                name=context["filer_name"] or "Unknown",
                canonical_address=canonical_address,
                zip_code=zip5,
                zip4=zip4,
                address_type="filer"
            )
        return None
    except Exception as e:
        log_error("Failed to parse address for EIN {} in {}: {}", context.get('filer_ein', 'Unknown'), xml_filename, str(e), ein=context.get('filer_ein', 'Unknown'))
        return None

def parse_990(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None) -> Tuple[Optional[DCCharity], List[DCOfficer], List[DCGrant], List[DCContractor], List[DCPoliticalContribution], Optional[DCAddress]]:
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    context = {
        'filer_ein': filer_ein,
        'tax_year': tax_year,
        'form_type': form_type
    }
    log_debug("TRACE: parse_990() started with context: EIN='%s', tax_year=%s, form_type=%s, file=%s",
              context['filer_ein'], context['tax_year'], context['form_type'], xml_filename,
              ein=context['filer_ein'])

    if context["form_type"] != "990":
        log_error("TRACE: XML {} is not a Form 990 (form_type: {}), skipping for EIN {}",
                  xml_filename, context['form_type'], context['filer_ein'],
                  ein=context['filer_ein'])
        return None, [], [], [], [], None

    context["filer_name"] = parse_filer_name_990(root, "filer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
    context["business_name_line1"] = parse_string_field(root, XPATHS_990, "business_name_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
    context["business_name_line2"] = parse_string_field(root, XPATHS_990, "business_name_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
    log_debug("TRACE: After filer_name parsing, context EIN: '%s' in file %s", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))

    fields = [
        ("receipt", parse_receipt_990),
        ("govt_grants", parse_govt_grants_990),
        ("contributions", parse_contributions_990),
        ("total_exp", parse_total_exp_990),
        ("prog_exp", parse_prog_exp_990),
        ("travel", parse_travel_990),
        ("conferences", parse_conferences_990),
        ("officer_comp", parse_officer_comp_990),
        ("grants_to_others", parse_grants_to_others_990),
        ("foreign_expenses", parse_foreign_expenses_990),
        ("total_assets", parse_total_assets_990),
        ("org_type", parse_org_type_990),
        ("foreign_office", parse_foreign_office_990)
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
    data["foreign_expenses_pct"] = calculate_percentage(data["foreign_expenses"], data["total_exp"])
    data["grift_ratio"] = calculate_percentage(data["officer_comp"] + data["travel"] + data["conferences"], data["total_exp"])

    data["denominator"] = data["total_assets"] + data["receipt"]
    data["comp_ptile"] = None
    data["travel_ptile"] = None
    data["conferences_ptile"] = None
    data["grants_ptile"] = None
    data["foreign_expenses_ptile"] = None
    data["domestic_misrep_flag"] = data["grift_ratio"] > 10 and data["foreign_expenses_pct"] < 0.1 * 100 if data["total_exp"] > 0 else False

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
    address = parse_address_990(root, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

    # Debug logging for address components
    if address:
        log_debug("DEBUG: Address parsed for EIN %s: line1='%s', line2='%s', city='%s', state='%s', zip='%s', canonical='%s'",
                  address.ein, address.address_line1, address.address_line2, address.city, address.state, address.zip_code, address.canonical_address,
                  ein=address.ein)
    else:
        log_debug("DEBUG: No address parsed for EIN %s in file %s", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))

    log_debug("TRACE: parse_990() returning Charity, Officers, Grants, Contractors, Contributions, and Address for EIN: '%s' in file %s", charity.ein, xml_filename, ein=charity.ein)
    return charity, officers, [], [], [], address

def main():
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
            log_error("TRACE: Found raw EIN: '%s' using xpath: %s in file %s", raw_ein, xpath.path, xml_file, ein=raw_ein)
            if raw_ein.isdigit():
                filer_ein = f"{int(raw_ein):09d}"
                log_error("TRACE: Formatted EIN: '%s' (valid 9-digit) in file %s", filer_ein, xml_file, ein=filer_ein)
            else:
                log_error("TRACE: Non-digit EIN found: '%s' in file %s, setting to 'Unknown'", raw_ein, xml_file, ein=raw_ein)
                filer_ein = "Unknown"
            break
    if filer_ein is None:
        log_error("TRACE: No EIN found in XML file %s, setting to 'Unknown'", xml_file, ein="Unknown")
        filer_ein = "Unknown"

    log_error("TRACE: Final EIN before parse_990() call: '%s' in file %s", filer_ein, xml_file, ein=filer_ein)

    log_error("TRACE: Calling parse_990() with EIN: '%s' for file %s", filer_ein, xml_file, ein=filer_ein)
    charity, officers = parse_990(root, xml_file, xpath_cache={}, filer_ein=filer_ein, tax_year=tax_year, form_type=form_type)
    if charity:
        log_error("TRACE: parse_990() returned Charity with EIN: '%s' for file %s", charity.ein, xml_file, ein=charity.ein)
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
    else:
        log_debug("TRACE: parse_990() returned None for EIN: '%s' in file %s", filer_ein, xml_file, ein=filer_ein)

if __name__ == "__main__":
    main()