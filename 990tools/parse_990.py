import sys
from lxml import etree
from io import BytesIO
import logging
import re
from nameparser import HumanName
from xpaths_990 import XPATHS_990
from parse_utils import parse_int_field, parse_string_field, parse_total, parse_schedule, clean_name, MONEY_PATTERN

# Setup logging
logger = None
log_error = None
verbose = False
DEBUG_EINS = set()

# XPaths are precompiled in xpaths_990.py
NAMESPACES = {'irs': 'http://www.irs.gov/efile'}

def set_logger(new_logger, new_log_error, is_verbose=False, debug_eins=None):
    global logger, log_error, verbose, DEBUG_EINS
    logger = new_logger
    log_error = new_log_error
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
    """
    Parse officer compensation and names, returning total and individual entries.
    
    Returns:
        Tuple of (total compensation, list of officer entries)
    """
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
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    schedule_f_total = parse_schedule(root, XPATHS_990, "grant_elements_f", "grant_sub_elements_f", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins=debug_eins)
    total += schedule_f_total

    schedule_i_total = parse_schedule(root, XPATHS_990, "grant_elements_i", "grant_sub_elements_i", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins=debug_eins)
    total += schedule_i_total

    if total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins:
        log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}", 
                  total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = parse_string_field(root, XPATHS_990, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}", 
                  context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, 
                  ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_foreign_expenses_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = parse_schedule(root, XPATHS_990, "foreign_exp_elements", "foreign_exp_sub_elements", "foreign_exp_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins={"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"})
    
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}
    if total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = parse_string_field(root, XPATHS_990, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}", 
                  context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, 
                  ein=context.get('filer_ein', 'Unknown'))
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
    schedule_field = "schedule_o"
    travel_xpaths = [xpath for xpath in XPATHS_990["schedule_o"] if "TravelGrp" in xpath.path]
    for xpath in travel_xpaths:
        schedule_o = parse_string_field(root, {schedule_field: [xpath]}, schedule_field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        if schedule_o is not None:
            desc = parse_string_field(schedule_o, XPATHS_990, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            if desc is not None:
                desc_text = desc.upper()
                if "TRAVEL" in desc_text:
                    match = MONEY_PATTERN.search( desc)
                    if match:
                        amount = int(float(match.group(1).replace('$', '')))
                        total += amount
                        if verbose:
                            log_error("Parsed travel_amt ${} from Schedule O in {}", 
                                      amount, xml_filename, 
                                      ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_conferences_990(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    schedule_field = "schedule_o"
    conferences_xpaths = [xpath for xpath in XPATHS_990["schedule_o"] if "ConferencesMeetingsGrp" in xpath.path]
    for xpath in conferences_xpaths:
        schedule_o = parse_string_field(root, {schedule_field: [xpath]}, schedule_field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        if schedule_o is not None:
            desc = parse_string_field(schedule_o, XPATHS_990, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            if desc is not None:
                desc_text = desc.upper()
                if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                    match = MONEY_PATTERN.search( desc)
                    if match:
                        amount = int(float(match.group(1).replace('$', '')))
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

def parse_990(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None):
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    context = {
        'filer_ein': filer_ein,
        'tax_year': tax_year,
        'form_type': form_type
    }

    if context["form_type"] != "990":
        log_error("XML {} is not a Form 990 (form_type: {}), skipping", 
                  xml_filename, context['form_type'], 
                  ein=context['filer_ein'])
        return None, []

    context["filer_name"] = parse_filer_name_990(root, "filer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

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
    data["comp_ptile"] = "n/y"
    data["travel_ptile"] = "n/y"
    data["conferences_ptile"] = "n/y"
    data["grants_ptile"] = "n/y"
    data["foreign_expenses_ptile"] = "n/y"
    data["domestic_misrep_flag"] = data["grift_ratio"] > 10 and data["foreign_expenses_pct"] < 0.1 * 100 if data["total_exp"] > 0 else False

    row = [
        context["tax_year"], context["filer_ein"], context["filer_name"], data["receipt"], data["govt_grants"],
        data["contributions"], data["org_type"], data["total_exp"], data["prog_exp"], data["travel"],
        data["conferences"], data["officer_comp"], data["comp_pct"], data["comp_ptile"], data["travel_pct"],
        data["travel_ptile"], data["conferences_pct"], data["conferences_ptile"], data["grants_pct"],
        data["grants_ptile"], data["foreign_expenses_pct"], data["foreign_expenses_ptile"], data["grift_ratio"],
        data["total_assets"], context["form_type"], data["denominator"], data["foreign_office"],
        data["foreign_expenses"], data["grants_to_others"], data["domestic_misrep_flag"], xml_filename
    ]
    return row, officer_entries

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
            filer_ein = result[0].text.strip()
            break
    filer_ein = filer_ein if filer_ein is not None else "Unknown"

    row, _ = parse_990(root, xml_file, xpath_cache={}, filer_ein=filer_ein, tax_year=tax_year, form_type=form_type)
    if row:
        row_str = [str(x).replace('\t', ' ').replace('\n', ' ') for x in row]
        print('\t'.join(row_str))

if __name__ == "__main__":
    main()