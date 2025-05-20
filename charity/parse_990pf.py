import sys
from lxml import etree
from io import BytesIO
import logging
from xpaths_990pf import XPATHS_990PF

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def parse_int(value):
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError, AttributeError):
        return 0

def find_element(root, xpaths, namespaces):
    for xpath in xpaths:
        try:
            elem = root.xpath(xpath, namespaces=namespaces)
            if elem:
                return elem[0]
        except etree.XPathEvalError as e:
            xml_snippet = etree.tostring(root, encoding='unicode', method='xml')[:2000]
            logger.error("XPath error for {}: {}. XML snippet: {}".format(xpath, e, xml_snippet))
            non_ns_xpath = xpath.replace('irs:', '').replace('{http://www.irs.gov/efile}', '')
            try:
                elem = root.xpath(non_ns_xpath, namespaces=None)
                if elem:
                    return elem[0]
            except etree.XPathEvalError as e:
                logger.error("Non-namespaced XPath error for {}: {}. XML snippet: {}".format(non_ns_xpath, e, xml_snippet))
    return None

def parse_org_type_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["org_type"], namespaces)
    if elem is not None:
        if elem.tag.endswith("Organization501c3ExemptPFInd") or elem.tag.endswith("Organization501c3TaxablePFInd"):
            org_type = "501(c)(3)"
        elif any(tag in elem.tag for tag in ["Organization4947a1NotExemptCharitableTrustInd", "Organization4947a1Ind", "Organization4947a1TrtdPFInd"]):
            org_type = "4947(a)(1)"
        else:
            org_type = "Unknown"
            logger.error("Unexpected org_type tag {} for EIN {} in {}, defaulting to Unknown".format(elem.tag, context.get('filer_ein', 'Unknown'), xml_filename))
    else:
        logger.error("Failed to parse org_type for EIN {} in {}".format(context.get('filer_ein', 'Unknown'), xml_filename))
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        org_tags = [child.tag for child in return_data.xpath("*[contains(local-name(), 'Organization')]", namespaces=namespaces)] if return_data is not None else []
        logger.error("Form type: {}, Available org_type tags: {} in {}".format(context.get('form_type', 'Unknown'), org_tags, xml_filename))
        org_type = "Unknown"
    logger.info("Parsed org_type {} for EIN {} in {}".format(org_type, context.get('filer_ein', 'Unknown'), xml_filename))
    return org_type

def parse_officer_comp_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["officer_comp_elements"]:
        officer_elems = root.xpath(xpath, namespaces=namespaces)
        for person in officer_elems:
            comp_elem = find_element(person, XPATHS_990PF["officer_comp_value"], namespaces)
            if comp_elem is not None:
                comp = parse_int(comp_elem.text)
                logger.info("Raw officer_comp value: {} for EIN {} in {}".format(comp_elem.text, context.get('filer_ein', 'Unknown'), xml_filename))
                if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                    logger.error("Suspicious officer_comp ${} exceeds total_exp ${} in {}".format(comp, context['total_exp'], xml_filename))
                    continue
                total += comp
    logger.info("Parsed officer_comp ${} for EIN {} in {}".format(total, context.get('filer_ein', 'Unknown'), xml_filename))
    return total

def parse_grants_to_others_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    elem = find_element(root, XPATHS_990PF["grants_to_others"], namespaces)
    if elem is not None:
        total += parse_int(elem.text)
        logger.info("Raw grants_to_others value: {} for EIN {} in {}".format(elem.text, context.get('filer_ein', 'Unknown'), xml_filename))
        logger.info("Parsed grants_to_others ${} from 990PF in {}".format(total, xml_filename))
    if total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins:
        logger.info("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}".format(
            total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename))
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        logger.info("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}".format(
            context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags))
    return total

def parse_foreign_expenses_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    for xpath in XPATHS_990PF["foreign_expenses"]:
        schedule_f = find_element(root, [xpath], namespaces)
        if schedule_f is not None:
            for sub_xpath in XPATHS_990PF["foreign_exp_sub_elements"]:
                for activity in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                    amount_elem = find_element(activity, XPATHS_990PF["foreign_exp_value"], namespaces)
                    if amount_elem is not None:
                        amount = parse_int(amount_elem.text)
                        logger.info("Raw foreign_exp value: {} for EIN {} in {}".format(amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename))
                        total += amount
                        if context.get('filer_ein', 'Unknown') in debug_eins or (amount > 5_000_000):
                            logger.info("Found RegionTotalExpendituresAmt ${} in ScheduleF for EIN {}, File {}".format(amount, context.get('filer_ein', 'Unknown'), xml_filename))
    if total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        logger.info("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}".format(
            context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags))
    return total

def parse_travel_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["travel"]:
        expense_elem = find_element(root, [xpath], namespaces)
        if expense_elem is not None:
            desc_elem = find_element(expense_elem, XPATHS_990PF["expense_desc"], namespaces)
            amount_elem = find_element(expense_elem, XPATHS_990PF["expense_value"], namespaces)
            if desc_elem is not None and amount_elem is not None:
                amount = parse_int(amount_elem.text)
                logger.info("Raw {} value: {} for EIN {} in {}".format(field, amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename))
                desc_text = desc_elem.text.upper()
                if "TRAVEL" in desc_text:
                    total += amount
                    logger.info("Parsed travel_amt ${} from OtherExpensesSchedule in {}".format(total, xml_filename))
    return total

def parse_conferences_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["conferences"]:
        expense_elem = find_element(root, [xpath], namespaces)
        if expense_elem is not None:
            desc_elem = find_element(expense_elem, XPATHS_990PF["expense_desc"], namespaces)
            amount_elem = find_element(expense_elem, XPATHS_990PF["expense_value"], namespaces)
            if desc_elem is not None and amount_elem is not None:
                amount = parse_int(amount_elem.text)
                logger.info("Raw {} value: {} for EIN {} in {}".format(field, amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename))
                desc_text = desc_elem.text.upper()
                if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                    total += amount
                    logger.info("Parsed conferences_amt ${} from OtherExpensesSchedule in {}".format(total, xml_filename))
    return total

def parse_receipt_990pf(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990PF["receipt"]:
        elem = find_element(root, [xpath], namespaces)
        if elem is not None:
            total += parse_int(elem.text)
            logger.info("Raw receipt value: {} for EIN {} in {}".format(elem.text, context.get('filer_ein', 'Unknown'), xml_filename))
    if total == 0:
        logger.info("Missing revenue fields in {}. Tried XPaths: {}".format(xml_filename, XPATHS_990PF['receipt']))
    return total

def parse_govt_grants_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["govt_grants"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return 0
    value = parse_int(elem.text)
    logger.info("Parsed {} ${} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

def parse_contributions_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["contributions"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return 0
    value = parse_int(elem.text)
    logger.info("Parsed {} ${} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

def parse_total_exp_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["total_exp"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return 0
    value = parse_int(elem.text)
    logger.info("Parsed {} ${} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

def parse_prog_exp_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["prog_exp"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return 0
    value = parse_int(elem.text)
    logger.info("Parsed {} ${} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

def parse_total_assets_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["total_assets"], namespaces)
    if elem is not None:
        value = parse_int(elem.text)
        logger.info("Raw total_assets value: {} for EIN {} in {}".format(elem.text, context.get('filer_ein', 'Unknown'), xml_filename))
        logger.info("Parsed total_assets ${} for EIN {} in {}".format(value, context.get('filer_ein', 'Unknown'), xml_filename))
        return value
    logger.info("Missing total_assets for EIN {} in {}. Tried XPaths: {}".format(context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF['total_assets']))
    return 0

def parse_foreign_office_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["foreign_office"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return False
    return elem.text.strip().upper() == 'X'

def parse_filer_ein_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["filer_ein"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return "Unknown"
    value = elem.text.strip()
    logger.info("Parsed {} {} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

def parse_form_type_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["form_type"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return "Unknown"
    value = elem.text.strip()
    logger.info("Parsed {} {} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

def parse_tax_year_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["tax_year"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return "Unknown"
    value = elem.text.strip()
    logger.info("Parsed {} {} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

def parse_filer_name_990pf(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990PF["filer_name"], namespaces)
    if elem is None:
        logger.info("Missing {} for EIN {} in {}. Tried XPaths: {}".format(field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990PF[field]))
        return "Unknown"
    value = elem.text.strip()
    logger.info("Parsed {} {} for EIN {} in {}".format(field, value, context.get('filer_ein', 'Unknown'), xml_filename))
    return value

# ... (previous code unchanged until parse_990pf)

def parse_990pf(xml_content, xml_filename):
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
    except etree.ParseError as e:
        logger.error("Parse error in XML file {}: {}".format(xml_filename, e))
        return None

    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    logger.info("Processing XML: {}".format(xml_filename))

    context = {}
    context["filer_ein"] = parse_filer_ein_990pf(root, "filer_ein", namespaces, xml_filename, context)
    logger.info("Extracted filer_ein: {} for {}".format(context['filer_ein'], xml_filename))

    context["form_type"] = parse_form_type_990pf(root, "form_type", namespaces, xml_filename, context)
    if context["form_type"] != "990PF":
        logger.error("XML {} is not a Form 990PF (form_type: {}), skipping".format(xml_filename, context['form_type']))
        return None
    logger.info("Extracted form_type: {} for {}".format(context['form_type'], xml_filename))

    context["tax_year"] = parse_tax_year_990pf(root, "tax_year", namespaces, xml_filename, context)
    logger.info("Extracted tax_year: {} for {}".format(context['tax_year'], xml_filename))

    if context["tax_year"] == "Unknown":
        logger.info("Missing TaxYr element in {}, inferring from filename".format(xml_filename))
        context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else "Unknown"
    else:
        try:
            int(context["tax_year"])
        except ValueError:
            logger.info("Invalid tax year {} in {}, inferring from filename".format(context['tax_year'], xml_filename))
            context["tax_year"] = xml_filename[:4] if xml_filename[:4].isdigit() else "Unknown"

    context["filer_name"] = parse_filer_name_990pf(root, "filer_name", namespaces, xml_filename, context)
    logger.info("Extracted filer_name: {} for {}".format(context['filer_name'], xml_filename))

    if context["filer_ein"] == "Unknown":
        logger.error("Missing Filer EIN in {}".format(xml_filename))
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
    #     logger.error("Suspicious grift_ratio {}% for EIN {} in {}".format(data['grift_ratio'], context['filer_ein'], xml_filename))

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
        print("Error reading XML file {}: {}".format(xml_file, e), file=sys.stderr)
        sys.exit(1)

    row = parse_990pf(xml_content, xml_file)
    if row:
        row_str = [str(x).replace('\t', ' ').replace('\n', ' ') for x in row]
        print('\t'.join(row_str))

if __name__ == "__main__":
    main()