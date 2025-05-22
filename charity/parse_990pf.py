import sys
from lxml import etree
from io import BytesIO
import logging
from xpaths_990pf import XPATHS_990PF

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def set_logger(new_logger, new_log_error):
    global logger, log_error
    logger = new_logger
    log_error = new_log_error
    
def stub_log_error(msg_format, *args, ein=None, exc_info=False):
    global logger
    if logger is None:
        # If logger isn't set, fall back to basic logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(__name__)
    if exc_info:
        logger.info(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
    else:
        logger.error(msg_format.format(*args) if args else msg_format)

log_error = stub_log_error        
        
def parse_int(value):
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError, AttributeError):
        return 0

def find_element(root, xpaths, namespaces, context):
    for xpath in xpaths:
        try:
            elem = root.xpath(xpath, namespaces=namespaces)
            if elem:
                return elem[0]
        except etree.XPathEvalError as e:
            xml_snippet = etree.tostring(root, encoding='unicode', method='xml')[:2000]
            log_error("XPath error for {}: {}. XML snippet: {}", xpath, e, xml_snippet, ein=context.get('filer_ein', 'Unknown'))
            non_ns_xpath = xpath.replace('irs:', '').replace('{http://www.irs.gov/efile}', '')
            try:
                elem = root.xpath(non_ns_xpath, namespaces=None)
                if elem:
                    return elem[0]
            except etree.XPathEvalError as e:
                log_error("Non-namespaced XPath error for {}: {}. XML snippet: {}", non_ns_xpath, e, xml_snippet)
    return None

def parse_org_type_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["org_type"], namespaces,context)
    if elem is not None:
        if elem.tag.endswith("Organization501c3ExemptPFInd") or elem.tag.endswith("Organization501c3TaxablePFInd"):
            org_type = "501(c)(3)"
        elif any(tag in elem.tag for tag in ["Organization4947a1NotExemptCharitableTrustInd", "Organization4947a1Ind", "Organization4947a1TrtdPFInd"]):
            org_type = "4947(a)(1)"
        else:
            org_type = "Unknown"
            log_error("Unexpected org_type tag {} for EIN {} in {}, defaulting to Unknown",elem.tag, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    else:
        log_error("Failed to parse org_type for EIN {} in {}", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces,context)
        org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None else []
        log_error("Form type: {}, Available org_type tags: {} in {}", context.get('form_type', 'Unknown'), org_tags, xml_filename, ein=context.get('filer_ein', 'Unknown'))
        org_type = "Unknown"
    log_error("Parsed org_type {} for EIN {} in {}", org_type, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return org_type

def parse_officer_comp_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["officer_comp_elements"]:
        officer_elems = root.xpath(xpath, namespaces=namespaces)
        for person in officer_elems:
            comp_elem = find_element(person, XPATHS_990PF["officer_comp_value"], namespaces,context)
            if comp_elem is not None:
                comp = parse_int(comp_elem.text)
                log_error("Raw officer_comp value: {} for EIN {} in {}", comp_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                    log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}", comp, context['total_exp'], xml_filename, ein=context.get('filer_ein', 'Unknown'))
                    continue
                total += comp
    log_error("Parsed officer_comp ${} for EIN {} in {}", total, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_grants_to_others_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    elem = find_element(root, XPATHS_990PF["grants_to_others"], namespaces,context)
    if elem is not None:
        total += parse_int(elem.text)
        log_error("Raw grants_to_others value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        log_error("Parsed grants_to_others ${} from 990PF in {}", total, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    if total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins:
        log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}",
            total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}",
            context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_foreign_expenses_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    for xpath in XPATHS_990PF["foreign_expenses"]:
        schedule_f = find_element(root, [xpath], namespaces,context)
        if schedule_f is not None:
            for sub_xpath in XPATHS_990PF["foreign_exp_sub_elements"]:
                for activity in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                    amount_elem = find_element(activity, XPATHS_990PF["foreign_exp_value"], namespaces)
                    if amount_elem is not None:
                        amount = parse_int(amount_elem.text)
                        log_error("Raw foreign_exp value: {} for EIN {} in {}", amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                        total += amount
                        if context.get('filer_ein', 'Unknown') in debug_eins or (amount > 5_000_000):
                            log_error("Found RegionTotalExpendituresAmt ${} in ScheduleF for EIN {}, File {}", amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    if total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}",
            context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_travel_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["travel"]:
        expense_elem = find_element(root, [xpath], namespaces,context)
        if expense_elem is not None:
            desc_elem = find_element(expense_elem, XPATHS_990PF["expense_desc"], namespaces,context)
            amount_elem = find_element(expense_elem, XPATHS_990PF["expense_value"], namespaces,context)
            if desc_elem is not None and amount_elem is not None:
                amount = parse_int(amount_elem.text)
                log_error("Raw {} value: {} for EIN {} in {}", field, amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                desc_text = desc_elem.text.upper()
                if "TRAVEL" in desc_text:
                    total += amount
                    log_error("Parsed travel_amt ${} from OtherExpensesSchedule in {}", total, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_conferences_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["conferences"]:
        expense_elem = find_element(root, [xpath], namespaces,context)
        if expense_elem is not None:
            desc_elem = find_element(expense_elem, XPATHS_990PF["expense_desc"], namespaces,context)
            amount_elem = find_element(expense_elem, XPATHS_990PF["expense_value"], namespaces,context)
            if desc_elem is not None and amount_elem is not None:
                amount = parse_int(amount_elem.text)
                log_error("Raw {} value: {} for EIN {} in {}", field, amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                desc_text = desc_elem.text.upper()
                if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                    total += amount
                    log_error("Parsed conferences_amt ${} from OtherExpensesSchedule in {}", total, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_receipt_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["receipt"]:
        elem = find_element(root, [xpath], namespaces,context)
        if elem is not None:
            total += parse_int(elem.text)
            log_error("Raw receipt value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    if total == 0:
        log_error("Missing revenue fields in {}. Tried XPaths: {}", xml_filename, XPATHS_990PF['receipt'], ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_govt_grants_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["govt_grants"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_contributions_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["contributions"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_total_exp_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["total_exp"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_prog_exp_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["prog_exp"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_total_assets_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["total_assets"], namespaces,context)
    if elem is not None:
        value = parse_int(elem.text)
        log_error("Raw total_assets value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        log_error("Parsed total_assets ${} for EIN {} in {}", value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return value
    log_error("Missing total_assets for EIN {} in {}. Tried XPaths: {}", context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF['total_assets'], ein=context.get('filer_ein', 'Unknown'))
    return 0

def parse_foreign_office_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["foreign_office"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return False
    return elem.text.strip().upper() == 'X'

def parse_filer_ein_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["filer_ein"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_form_type_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["form_type"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_tax_year_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["tax_year"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_filer_name_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["filer_name"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

# ... (previous code unchanged until parse_990pf)

def parse_990pf(xml_content, xml_filename):
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
    except etree.ParseError as e:
        log_error("Parse error in XML file {}: {}", xml_filename, e)
        return None

    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile'}

    context = {}
    context["filer_ein"] = parse_filer_ein_990pf(root, "filer_ein", namespaces, xml_filename, context)
    log_error("Processing XML: {}", xml_filename, ein=context.get('filer_ein', 'Unknown'))
    log_error("Extracted filer_ein: {} for {}", context['filer_ein'], xml_filename, ein=context.get('filer_ein', 'Unknown'))

    context["form_type"] = parse_form_type_990pf(root, "form_type", namespaces, xml_filename, context)
    if context["form_type"] != "990PF":
        log_error("XML {} is not a Form 990PF (form_type: {}), skipping",xml_filename, context['form_type'], ein=context.get('filer_ein', 'Unknown'))
        return None
    log_error("Extracted form_type: {} for {}", context['form_type'], xml_filename, ein=context.get('filer_ein', 'Unknown'))

    context["tax_year"] = parse_tax_year_990pf(root, "tax_year", namespaces, xml_filename, context)
    log_error("Extracted tax_year: {} for {}", context['tax_year'], xml_filename, ein=context.get('filer_ein', 'Unknown'))

    if context["tax_year"] == "Unknown":
        log_error("Missing TaxYr element in {}, inferring from filename",xml_filename, ein=context.get('filer_ein', 'Unknown'))
        context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else "Unknown"
    else:
        try:
            int(context["tax_year"])
        except ValueError:
            log_error("Invalid tax year {} in {}, inferring from filename",context['tax_year'], xml_filename, ein=context.get('filer_ein', 'Unknown'))
            context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else "Unknown"

    context["filer_name"] = parse_filer_name_990pf(root, "filer_name", namespaces, xml_filename, context)
    log_error("Extracted filer_name: {} for {}", context['filer_name'], xml_filename, ein=context.get('filer_ein', 'Unknown'))

    if context["filer_ein"] == "Unknown":
        log_error("Missing Filer EIN in {}", xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return None

    fields = [
        "receipt", "govt_grants", "contributions", "total_exp", "prog_exp",
        "travel", "conferences", "officer_comp", "grants_to_others", "foreign_expenses",
        "total_assets", "org_type", "foreign_office"
    ]
    data = {field: globals()[f"parse_{field}_990pf"](root, field, namespaces, xml_filename, context) for field in fields}

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

    # Removed direct logger.error call for grift_ratio
    # Let extract_charities.py handle this logging
    # if data["grift_ratio"] > 100 and data["total_exp"] > 0:
    #     log_error("Suspicious grift_ratio {}% for EIN {} in {}", data['grift_ratio'], context['filer_ein'], xml_filename)

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
        print("Error reading XML file {}: {}",xml_file, e, file=sys.stderr)
        sys.exit(1)

    row = parse_990pf(xml_content, xml_file)
    if row:
        row_str = [str(x).replace('\t', ' ').replace('\n', ' ') for x in row]
        print('\t'.join(row_str))

if __name__ == "__main__":
    main()