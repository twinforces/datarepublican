import sys
from lxml import etree
from io import BytesIO
import logging
from xpaths_990pf import XPATHS_990PF
from xpath_utils import find_element  # Import find_element from xpath_utils

# Setup logging
logger = None
log_error = None

def set_logger(new_logger, new_log_error):
    global logger, log_error
    logger = new_logger
    log_error = new_log_error
    
def stub_log_error(msg_format, *args, ein=None, exc_info=False):
    global logger
    if logger is None:
        # If logger isn't set, fall back to basic logging
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(__name__)
    if exc_info:
        logger.error(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
    else:
        logger.info(msg_format.format(*args) if args else msg_format)

# Default to stub if log_error isn't set by extract_charities.py
if log_error is None:
    log_error = stub_log_error

def parse_int(value):
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError, AttributeError):
        return 0

def parse_org_type_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["org_type"], namespaces, xpath_cache, "org_type")
    if elem is not None:
        if elem.tag.endswith("Organization501c3ExemptPFInd") or elem.tag.endswith("Organization501c3TaxablePFInd"):
            org_type = "501(c)(3)"
        elif any(tag in elem.tag for tag in ["Organization4947a1NotExemptCharitableTrustInd", "Organization4947a1Ind", "Organization4947a1TrtdPFInd"]):
            org_type = "4947(a)(1)"
        else:
            org_type = "Unknown"
            log_error("Unexpected org_type tag {} for EIN {} in {}, defaulting to Unknown", elem.tag, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    else:
        log_error("Failed to parse org_type for EIN {} in {}", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces, xpath_cache, "return_data")
        org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None else []
        log_error("Form type: {}, Available org_type tags: {} in {}", context.get('form_type', 'Unknown'), org_tags, xml_filename, ein=context.get('filer_ein', 'Unknown'))
        org_type = "Unknown"
    log_error("Parsed org_type {} for EIN {} in {}", org_type, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return org_type

def parse_officer_comp_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    total = 0
    # Extract officer elements once to reduce XPath evaluations
    officer_elems = []
    for xpath in XPATHS_990PF["officer_comp_elements"]:
        elems = root.xpath(xpath, namespaces=namespaces)
        officer_elems.extend(elems)

    for person in officer_elems:
        comp_elem = find_element(person, XPATHS_990PF["officer_comp_value"], namespaces, xpath_cache, "officer_comp_value")
        if comp_elem is not None:
            comp = parse_int(comp_elem.text)
            log_error("Raw officer_comp value: {} for EIN {} in {}", comp_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
            if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}", comp, context['total_exp'], xml_filename, ein=context.get('filer_ein', 'Unknown'))
                continue
            total += comp
    log_error("Parsed officer_comp ${} for EIN {} in {}", total, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_grants_to_others_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    elem = find_element(root, XPATHS_990PF["grants_to_others"], namespaces, xpath_cache, "grants_to_others")
    if elem is not None:
        total += parse_int(elem.text)
        log_error("Raw grants_to_others value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        log_error("Parsed grants_to_others ${} from 990PF in {}", total, xml_filename, ein=context.get('filer_ein', 'Unknown'))

    if total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins:
        log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}", 
                  total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces, xpath_cache, "return_data")
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}", 
                  context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, 
                  ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_foreign_expenses_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    # Extract Schedule F elements once
    schedule_f_elems = []
    for xpath in XPATHS_990PF["foreign_expenses"]:
        schedule_f = find_element(root, [xpath], namespaces, xpath_cache, "foreign_expenses")
        if schedule_f is not None:
            schedule_f_elems.append(schedule_f)

    for schedule_f in schedule_f_elems:
        for sub_xpath in XPATHS_990PF["foreign_exp_sub_elements"]:
            for activity in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                amount_elem = find_element(activity, XPATHS_990PF["foreign_exp_value"], namespaces, xpath_cache, "foreign_exp_value")
                if amount_elem is not None:
                    amount = parse_int(amount_elem.text)
                    log_error("Raw foreign_exp value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                    total += amount
                    if context.get('filer_ein', 'Unknown') in debug_eins or (amount > 5_000_000):
                        log_error("Found RegionTotalExpendituresAmt ${} in ScheduleF for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))

    if total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces, xpath_cache, "return_data")
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}", 
                  context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, 
                  ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_travel_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    total = 0
    # Extract expense elements once
    expense_elems = []
    for xpath in XPATHS_990PF["travel"]:
        expense_elem = find_element(root, [xpath], namespaces, xpath_cache, "travel")
        if expense_elem is not None:
            expense_elems.append(expense_elem)

    for expense_elem in expense_elems:
        desc_elem = find_element(expense_elem, XPATHS_990PF["expense_desc"], namespaces, xpath_cache, "expense_desc")
        amount_elem = find_element(expense_elem, XPATHS_990PF["expense_value"], namespaces, xpath_cache, "expense_value")
        if desc_elem is not None and amount_elem is not None:
            amount = parse_int(amount_elem.text)
            log_error("Raw {} value: {} for EIN {} in {}", field, amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
            desc_text = desc_elem.text.upper()
            if "TRAVEL" in desc_text:
                total += amount
                log_error("Parsed travel_amt ${} from OtherExpensesSchedule in {}", total, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_conferences_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    total = 0
    # Extract expense elements once
    expense_elems = []
    for xpath in XPATHS_990PF["conferences"]:
        expense_elem = find_element(root, [xpath], namespaces, xpath_cache, "conferences")
        if expense_elem is not None:
            expense_elems.append(expense_elem)

    for expense_elem in expense_elems:
        desc_elem = find_element(expense_elem, XPATHS_990PF["expense_desc"], namespaces, xpath_cache, "expense_desc")
        amount_elem = find_element(expense_elem, XPATHS_990PF["expense_value"], namespaces, xpath_cache, "expense_value")
        if desc_elem is not None and amount_elem is not None:
            amount = parse_int(amount_elem.text)
            log_error("Raw {} value: {} for EIN {} in {}", field, amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
            desc_text = desc_elem.text.upper()
            if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                total += amount
                log_error("Parsed conferences_amt ${} from OtherExpensesSchedule in {}", total, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_receipt_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    total = 0
    for xpath in XPATHS_990PF["receipt"]:
        elem = find_element(root, [xpath], namespaces, xpath_cache, "receipt")
        if elem is not None:
            total += parse_int(elem.text)
            log_error("Raw receipt value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    if total == 0:
        log_error("Missing revenue fields in {}. Tried XPaths: {}", xml_filename, XPATHS_990PF['receipt'], ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_govt_grants_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["govt_grants"], namespaces, xpath_cache, "govt_grants")
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_contributions_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["contributions"], namespaces, xpath_cache, "contributions")
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_total_exp_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["total_exp"], namespaces, xpath_cache, "total_exp")
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_prog_exp_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["prog_exp"], namespaces, xpath_cache, "prog_exp")
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_total_assets_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["total_assets"], namespaces, xpath_cache, "total_assets")
    if elem is not None:
        value = parse_int(elem.text)
        log_error("Raw total_assets value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        log_error("Parsed total_assets ${} for EIN {} in {}", value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return value
    log_error("Missing total_assets for EIN {} in {}. Tried XPaths: {}", context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF['total_assets'], ein=context.get('filer_ein', 'Unknown'))
    return 0

def parse_foreign_office_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["foreign_office"], namespaces, xpath_cache, "foreign_office")
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return False
    return elem.text.strip().upper() == 'X'

def parse_filer_name_990pf(root, field, namespaces, xml_filename, context, xpath_cache):
    elem = find_element(root, XPATHS_990PF["filer_name"], namespaces, xpath_cache, "filer_name")
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_990pf(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type):
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    context = {
        'filer_ein': filer_ein,
        'tax_year': tax_year,
        'form_type': form_type
    }

    if context["form_type"] != "990PF":
        log_error("XML {} is not a Form 990PF (form_type: {}), skipping", xml_filename, context['form_type'], ein=context.get('filer_ein', 'Unknown'))
        return None

    context["filer_name"] = parse_filer_name_990pf(root, "filer_name", namespaces, xml_filename, context, xpath_cache)

    fields = [
        "receipt", "govt_grants", "contributions", "total_exp", "prog_exp",
        "travel", "conferences", "officer_comp", "grants_to_others", "foreign_expenses",
        "total_assets", "org_type", "foreign_office"
    ]
    data = {field: globals()[f"parse_{field}_990pf"](root, field, namespaces, xml_filename, context, xpath_cache) for field in fields}

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
    return row

def main():
    if len(sys.argv) != 2:
        print("Usage: python parse_990pf.py <xml_file>", file=sys.stderr)
        sys.exit(1)

    xml_file = sys.argv[1]
    try:
        with open(xml_file, 'rb') as f:
            xml_content = f.read()
    except IOError as e:
        print("Error reading XML file {}: {}", xml_file, e, file=sys.stderr)
        sys.exit(1)

    # Parse the XML once and extract necessary fields
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(BytesIO(xml_content), parser)
    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile'}

    form_type_paths = [
        ".//irs:ReturnHeader/irs:ReturnTypeCd",
        ".//ReturnHeader/ReturnTypeCd"
    ]
    form_type_elem = find_element(root, form_type_paths, namespaces, xpath_cache={})
    form_type = form_type_elem.text if form_type_elem is not None else "Unknown"

    tax_year_paths = [
        ".//irs:ReturnHeader/irs:TaxYr",
        ".//ReturnHeader/TaxYr"
    ]
    tax_year_elem = find_element(root, tax_year_paths, namespaces, xpath_cache={})
    tax_year = tax_year_elem.text if tax_year_elem is not None else "Unknown"
    if tax_year == "Unknown":
        tax_year = xml_file[:4] if xml_file[:4].isdigit() else "Unknown"
    else:
        try:
            int(tax_year)
        except ValueError:
            tax_year = xml_file[:4] if xml_file[:4].isdigit() else "Unknown"

    filer_ein_paths = [
        ".//irs:ReturnHeader/irs:Filer/irs:EIN",
        ".//ReturnHeader/Filer/EIN"
    ]
    filer_ein_elem = find_element(root, filer_ein_paths, namespaces, xpath_cache={})
    filer_ein = filer_ein_elem.text.strip() if filer_ein_elem is not None else "Unknown"

    row = parse_990pf(root, xml_file, xpath_cache={}, filer_ein=filer_ein, tax_year=tax_year, form_type=form_type)
    if row:
        row_str = [str(x).replace('\t', ' ').replace('\n', ' ') for x in row]
        print('\t'.join(row_str))

if __name__ == "__main__":
    main()