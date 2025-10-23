# parse_990ez.py
import sys
from lxml import etree
from io import BytesIO
import logging
import re
from nameparser import HumanName
from parse_utils import parse_int_field, parse_string_field, parse_schedule, clean_name, MONEY_PATTERN, parse_float_field
from xpaths_990ez import XPATHS_990EZ, NAMESPACES
from models import Charity as DCCharity, Officer as DCOfficer, Grant as DCGrant, Contractor as DCContractor, PoliticalContribution as DCPoliticalContribution, Address as DCAddress
from typing import Optional, List, Tuple
from logging_utils import log_error, log_debug, log_info, log_warning

logger = None
log_error = None
log_debug = None
verbose = False
quiet = False
from constants import DEBUG_EINS, ORG_TYPE_SUFFIXES, TRAVEL_KEYWORDS, CONFERENCE_KEYWORDS

def set_logger(new_logger, new_log_error, new_log_debug=None, is_verbose=False, debug_eins=None, is_quiet=False):
    global logger, log_error, log_debug, verbose, quiet, DEBUG_EINS
    logger = new_logger
    log_error = new_log_error
    log_debug = new_log_debug or new_log_error  # fallback to log_error if log_debug not provided
    verbose = is_verbose
    quiet = is_quiet
    DEBUG_EINS = debug_eins if debug_eins is not None else set()

def stub_log_error(msg_format, *args, ein=None, exc_info=False):
    global logger
    if logger is None:
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(__name__)
    if exc_info:
        logger.error(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
    else:
        logger.info(msg_format.format(*args) if args else msg_format)

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

if log_error is None:
    log_error = stub_log_error

if log_debug is None:
    log_debug = stub_log_debug

def parse_org_type_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    elem = parse_string_field(root, XPATHS_990EZ, "org_type", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
    if elem is not None:
        if verbose and not quiet:
            log_error(f"Found org_type element: tag={elem.tag}, text={elem.text}, attrib={elem.attrib} for EIN {context.get('filer_ein', 'Unknown')} in {xml_filename}",
                      ein=context.get('filer_ein', 'Unknown'))
        if elem.tag.endswith("Organization501cInd"):
            type_num = elem.get("organization501cTypeTxt")
            if type_num and type_num.isdigit() and 1 <= int(type_num) <= 29:
                org_type = f"501(c)({type_num})"
            elif elem.text and "X" in elem.text.upper():
                org_type = "501(c)(3)"
            else:
                org_type = "501(c)(3)"
        elif elem.tag.endswith(tuple(ORG_TYPE_SUFFIXES)):
            for suffix in ORG_TYPE_SUFFIXES:
                if elem.tag.endswith(suffix):
                    type_num = suffix.replace("Organization501c", "").replace("Ind", "")
                    if type_num == "3":
                        org_type = "501(c)(3)"
                    elif type_num.isdigit() and 1 <= int(type_num) <= 29:
                        org_type = f"501(c)({type_num})"
                    else:
                        org_type = "501(c)(3)"
                        if not quiet:
                            log_error("Unexpected suffix {} for EIN {} in {}, defaulting to 501(c)(3)",
                                       suffix, context.get('filer_ein', 'Unknown'), xml_filename,
                                       ein=context.get('filer_ein', 'Unknown'))
                    break
        elif elem.tag.endswith("TaxExemptStatus") or elem.tag.endswith("ExemptStatusCd"):
            if elem.text and "501(c)" in elem.text:
                match = re.search(r'501\(c\)\((\d+)\)', elem.text)
                if match and 1 <= int(match.group(1)) <= 29:
                    org_type = f"501(c)({match.group(1)})"
                else:
                    org_type = "501(c)(3)"
                    if not quiet:
                        log_error("Invalid 501(c) format in TaxExemptStatus/ExemptStatusCd value {} for EIN {} in {}, defaulting to 501(c)(3)",
                                   elem.text, context.get('filer_ein', 'Unknown'), xml_filename,
                                   ein=context.get('filer_ein', 'Unknown'))
            elif elem.text and "4947(a)(1)" in elem.text:
                org_type = "4947(a)(1)"
            else:
                org_type = "501(c)(3)"  # Default for 990EZ
                if not quiet:
                    log_error("Unexpected TaxExemptStatus/ExemptStatusCd value {} for EIN {} in {}, defaulting to 501(c)(3)",
                               elem.text, context.get('filer_ein', 'Unknown'), xml_filename,
                               ein=context.get('filer_ein', 'Unknown'))
        elif elem.tag.endswith("Organization4947a1NotPFInd") or elem.tag.endswith("Organization4947a1TrtdPFInd"):
            org_type = "4947(a)(1)"
        elif elem.tag.endswith("Organization501c3Ind"):
            org_type = "501(c)(3)"
        else:
            org_type = "501(c)(3)"  # Default for 990EZ
            # Only log error if verbose or debug mode
            if (verbose or context.get('filer_ein', 'Unknown') in DEBUG_EINS) and not quiet:
                log_error("Unexpected org_type tag {} for EIN {} in {}, defaulting to 501(c)(3)",
                           elem.tag, context.get('filer_ein', 'Unknown'), xml_filename,
                           ein=context.get('filer_ein', 'Unknown'))
    else:
        if not quiet:
            log_error("Failed to parse org_type for EIN {} in {}",
                       context.get('filer_ein', 'Unknown'), xml_filename,
                       ein=context.get('filer_ein', 'Unknown'))
        return_data = parse_string_field(root, XPATHS_990EZ, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        all_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        if not quiet:
            log_error("No org_type tags found, defaulting to 501(c)(3). All ReturnData tags: {} in {}",
                       all_tags, xml_filename,
                       ein=context.get('filer_ein', 'Unknown'))
        org_type = "501(c)(3)"  # Default for 990EZ when no org_type tags are found
    if verbose and not quiet:
        log_error("Parsed org_type {} for EIN {} in {}",
                   org_type, context.get('filer_ein', 'Unknown'), xml_filename,
                   ein=context.get('filer_ein', 'Unknown'))
    return org_type

def parse_officer_comp_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    form_type = context.get('form_type', 'Unknown')
    total = 0
    officer_entries = []
    
    elements = []
    for xpath in XPATHS_990EZ["officer_comp_elements"]:
        result = xpath(root)
        elements.extend(result)
    
    for elem in elements:
        name_elem = parse_string_field(elem, XPATHS_990EZ, "officer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        value_elem = parse_string_field(elem, XPATHS_990EZ, "officer_comp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        
        if name_elem and value_elem:
            cleaned_name = clean_name(name_elem)
            name = HumanName(cleaned_name)
            first_name = name.first or "Unknown"
            last_name = name.last or "Unknown"
            value = parse_int_field(elem, XPATHS_990EZ, "officer_comp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
            
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
                log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}",
                           total, context.get('total_exp', 0), xml_filename,
                           ein=context.get('filer_ein', 'Unknown'))
            total = 0
            officer_entries = []

    return total, officer_entries

def parse_grants_to_others_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    total += parse_schedule(root, XPATHS_990EZ, "grant_elements_i", "grant_sub_elements_i", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins=debug_eins)

    total += parse_schedule(root, XPATHS_990EZ, "grant_elements_f", "grant_sub_elements_f", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins=debug_eins)

    if total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins:
        if not quiet:
            log_warning(f"Non-zero grants_to_others ${total} for EIN {context.get('filer_ein', 'Unknown')}, Name {context.get('filer_name', 'Unknown')}, TaxYear {context.get('tax_year', 'Unknown')}, XML {xml_filename}",
                        ein=context.get('filer_ein', 'Unknown'))
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = parse_string_field(root, XPATHS_990EZ, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        if not quiet:
            log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}",
                       context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags,
                       ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_travel_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    # Cache the schedule_o elements to avoid repeated XPath evaluations
    if 'schedule_o_elements' not in xpath_cache:
        schedule_o_elements = []
        for xpath in XPATHS_990EZ["schedule_o"]:
            result = xpath(root)
            schedule_o_elements.extend(result)
        xpath_cache['schedule_o_elements'] = schedule_o_elements
    else:
        schedule_o_elements = xpath_cache['schedule_o_elements']

    for elem in schedule_o_elements:
        desc = parse_string_field(elem, XPATHS_990EZ, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        if desc is not None:
            desc_text = desc.upper()
            # Check for travel keywords anywhere in the description
            has_travel_keywords = any(keyword in desc_text for keyword in TRAVEL_KEYWORDS)
            if has_travel_keywords:
                # Look for any money pattern in the description
                match = MONEY_PATTERN.search(desc)
                if match:
                    try:
                        amount = int(parse_float_field(match.group(1)))
                        total += amount
                        if verbose and not quiet:
                            log_error("Parsed travel_amt ${} from Schedule O in {}",
                                       amount, xml_filename,
                                       ein=context.get('filer_ein', 'Unknown'))
                    except IndexError:
                        # Skip logging travel parsing failures - handled downstream
                        pass
                else:
                    # If no money pattern found but keywords present, try to extract any number
                    # Look for patterns like "AMOUNT: $1234" or just "$1234"
                    alt_match = re.search(r'(?:AMOUNT:\s*)?\$?([\d,]+\.?\d*)', desc, re.IGNORECASE)
                    if alt_match:
                        try:
                            amount = int(parse_float_field(alt_match.group(1)))
                            total += amount
                            if verbose and not quiet:
                                log_error("Parsed travel_amt ${} (alt pattern) from Schedule O in {}",
                                           amount, xml_filename,
                                           ein=context.get('filer_ein', 'Unknown'))
                        except (ValueError, IndexError):
                            # Skip logging travel parsing failures - handled downstream
                            pass
    return total

def parse_conferences_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    # Cache the schedule_o elements to avoid repeated XPath evaluations
    if 'schedule_o_elements' not in xpath_cache:
        schedule_o_elements = []
        for xpath in XPATHS_990EZ["schedule_o"]:
            result = xpath(root)
            schedule_o_elements.extend(result)
        xpath_cache['schedule_o_elements'] = schedule_o_elements
    else:
        schedule_o_elements = xpath_cache['schedule_o_elements']

    for elem in schedule_o_elements:
        desc = parse_string_field(elem, XPATHS_990EZ, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        if desc is not None:
            desc_text = desc.upper()
            # Check for conference/meeting keywords anywhere in the description
            has_conference_keywords = any(keyword in desc_text for keyword in CONFERENCE_KEYWORDS)
            if has_conference_keywords:
                # Look for any money pattern in the description
                match = MONEY_PATTERN.search(desc)
                if match:
                    try:
                        amount = int(parse_float_field(match.group(1)))
                        total += amount
                        if verbose and not quiet:
                            log_error("Parsed conferences_amt ${} from Schedule O in {}",
                                       amount, xml_filename,
                                       ein=context.get('filer_ein', 'Unknown'))
                    except IndexError:
                        # Skip logging conference parsing failures - handled downstream
                        pass
                else:
                    # If no money pattern found but keywords present, try to extract any number
                    # Look for patterns like "AMOUNT: $1234" or just "$1234"
                    alt_match = re.search(r'(?:AMOUNT:\s*)?\$?([\d,]+\.?\d*)', desc, re.IGNORECASE)
                    if alt_match:
                        try:
                            amount = int(parse_float_field(alt_match.group(1)))
                            total += amount
                            if verbose and not quiet:
                                log_error("Parsed conferences_amt ${} (alt pattern) from Schedule O in {}",
                                           amount, xml_filename,
                                           ein=context.get('filer_ein', 'Unknown'))
                        except (ValueError, IndexError):
                            # Skip logging conference parsing failures - handled downstream
                            pass
    return total

def parse_total_assets_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = parse_int_field(root, XPATHS_990EZ, "total_assets", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
    if total > 0 and (verbose or context.get('filer_ein', 'Unknown') in DEBUG_EINS) and not quiet:
        log_error("Raw total_assets value: {} for EIN {} in {}",
                   total, context.get('filer_ein', 'Unknown'), xml_filename,
                   ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_receipt_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990EZ, "receipt", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_govt_grants_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990EZ, "govt_grants", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_contributions_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990EZ, "contributions", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_total_exp_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990EZ, "total_exp", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_prog_exp_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_int_field(root, XPATHS_990EZ, "prog_exp", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

def parse_filer_name_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    return parse_string_field(root, XPATHS_990EZ, "filer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="Unknown")

def parse_address_990ez(root, xml_filename, context, xpath_cache, charity=None, log_error=log_error, xpath_match_stats=None):
    """Parse address information from Form 990EZ"""
    try:
        namespaces = {'irs': 'http://www.irs.gov/efile'}
        # Parse address components
        address_line1 = parse_string_field(root, XPATHS_990EZ, "address_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        address_line2 = parse_string_field(root, XPATHS_990EZ, "address_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        city = parse_string_field(root, XPATHS_990EZ, "city", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        state = parse_string_field(root, XPATHS_990EZ, "state", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
        zip_code = parse_string_field(root, XPATHS_990EZ, "zip_code", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

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
            log_error("Failed to parse address for EIN {} in {}: {}", context.get('filer_ein', 'Unknown'), xml_filename, str(e), ein=context.get('filer_ein', 'Unknown'))
        return None

def parse_990ez(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None):
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    context = {
        'filer_ein': filer_ein,
        'tax_year': tax_year,
        'form_type': form_type
    }

    if context["filer_ein"] == "680005486" and context["form_type"] != "990EZ":
        context["form_type"] = "990EZ"
        if not quiet:
            log_error("Forced form_type '990EZ' for EIN {} in {}", context['filer_ein'], xml_filename, ein=context['filer_ein'])
    if context["form_type"] != "990EZ":
        if not quiet:
            log_error("XML {} is not a Form 990EZ (form_type: {}), skipping", xml_filename, context['form_type'], ein=context['filer_ein'])
        return None, [], [], [], [], None

    context["filer_name"] = parse_filer_name_990ez(root, "filer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
    context["business_name_line1"] = parse_string_field(root, XPATHS_990EZ, "business_name_line1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
    context["business_name_line2"] = parse_string_field(root, XPATHS_990EZ, "business_name_line2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)

    fields = [
        ("receipt", parse_receipt_990ez),
        ("contributions", parse_contributions_990ez),
        ("total_exp", parse_total_exp_990ez),
        ("prog_exp", parse_prog_exp_990ez),
        ("officer_comp", parse_officer_comp_990ez),
        ("grants_to_others", parse_grants_to_others_990ez),
        ("total_assets", parse_total_assets_990ez),
        ("travel", parse_travel_990ez),
        ("conferences", parse_conferences_990ez),
        ("org_type", parse_org_type_990ez)
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
    data["govt_grants"] = None
    data["grift_ratio"] = None
    data["foreign_office"] = None
    data["foreign_expenses_pct"] = None
    data["foreign_expenses"] = None
    data["domestic_misrep_flag"] = False

    # Create Charity dataclass
    charity = DCCharity(
        ein=context["filer_ein"],
        tax_year=context["tax_year"],
        filer_name=context["filer_name"] or "Unknown",
        receipt_amt=data["receipt"],
        govt_amt=data["govt_grants"],  # Will be None for 990EZ
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
        foreign_expenses_pct=data["foreign_expenses_pct"],  # Will be "n/a"
        foreign_expenses_ptile=data["foreign_expenses_ptile"],  # Will be "n/a"
        grift_ratio=data["grift_ratio"],
        total_assets=data["total_assets"],
        form_type=context["form_type"],
        denominator=data["denominator"],
        foreign_office=data["foreign_office"],  # Will be "n/a"
        foreign_expenses=data["foreign_expenses"],  # Will be "n/a"
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
    address = parse_address_990ez(root, xml_filename, context, xpath_cache, charity=charity, log_error=log_error, xpath_match_stats=xpath_match_stats)

    # Debug logging for address components
    if address and not quiet:
        log_debug("DEBUG: Address parsed for EIN %s: line1='%s', line2='%s', city='%s', state='%s', zip='%s', po_box='%s', canonical='%s'",
                  address.ein, address.address_line1, address.address_line2, address.city, address.state, address.zip_code, address.po_box, address.canonical_address,
                  ein=address.ein)
    elif not quiet:
        log_debug("DEBUG: No address parsed for EIN %s in file %s", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))

    # Parse grants, contractors, and political contributions
    grants, contractors, contributions = parse_related_entities_990ez(root, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

    if not quiet:
        log_debug("TRACE: parse_990ez() returning Charity, Officers, Grants, Contractors, Contributions, and Address for EIN: '%s' in file %s", charity.ein, xml_filename, ein=charity.ein)
    return charity, officers, grants, contractors, contributions, address

def parse_related_entities_990ez(root, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    """Parse grants, contractors, and political contributions for Form 990EZ"""
    from xpaths import GRANT_XPATHS, GRANT_EIN_XPATHS, GRANT_NAME_XPATHS, GRANT_AMOUNT_XPATHS
    from xpaths import SCHEDULE_C_XPATHS, SCHEDULE_C_AMOUNT_XPATHS, SCHEDULE_C_RECIPIENT_XPATHS, SCHEDULE_C_EIN_XPATHS

    grants = []
    contractors = []
    contributions = []

    # Parse grants from Schedule I and F
    grant_elements = []
    for xpath in GRANT_XPATHS["990EZ"]:
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
            # Create grant record
            grant = DCGrant(
                filer_ein=context["filer_ein"],
                filer_name=context["filer_name"],
                grant_ein=grant_ein,
                grant_amt=grant_amount,
                tax_year=context["tax_year"],
                grantee_name=grant_name
            )
            grants.append(grant)

    # Parse political contributions from Schedule C
    contribution_elements = []
    for xpath in SCHEDULE_C_XPATHS["990EZ"]:
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

        # Parse contribution amount
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

        if amount > 0 and recipient:
            # Create political contribution record
            contribution = DCPoliticalContribution(
                filer_ein=context["filer_ein"],
                recipient=recipient,
                amount=amount,
                tax_year=context["tax_year"]
            )
            contributions.append(contribution)

    return grants, contractors, contributions

def main():
    if len(sys.argv) != 2:
        print("Usage: python parse_990ez.py <xml_file>", file=sys.stderr)
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
            filer_ein = result[0].text.strip()
            break
    filer_ein = filer_ein if filer_ein is not None else "Unknown"

    charity, officers, grants, contractors, contributions, address = parse_990ez(root, xml_file, xpath_cache={}, filer_ein=filer_ein, tax_year=tax_year, form_type=form_type)
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
        row_str = [str(x).replace('\t', '\\t').replace('\n', '\\n') for x in row]
        print('\t'.join(row_str))

if __name__ == "__main__":
    main()