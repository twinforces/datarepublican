import sys
from lxml import etree
from io import BytesIO
import logging
import re
from xpaths_990ez import XPATHS_990EZ

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

def find_element(root, xpaths, namespaces,context):
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
                log_error("Non-namespaced XPath error for {}: {}. XML snippet: {}", non_ns_xpath, e, xml_snippet, ein=context.get('filer_ein', 'Unknown'))
    return None

def parse_org_type_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["org_type"], namespaces,context)
    if elem is not None:
        log_error("Found org_type element: tag={}, text={}, attrib={} for EIN {} in {}",
            elem.tag, elem.text, elem.attrib, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        if elem.tag.endswith("Organization501cInd"):
            type_num = elem.get("organization501cTypeTxt")
            if type_num and type_num.isdigit() and 1 <= int(type_num) <= 29:
                org_type = f"501(c)({type_num})"
            elif elem.text and "X" in elem.text.upper():
                org_type = "501(c)(3)"
            else:
                org_type = "501(c)(3)"  # Default for 990EZ
        elif any(elem.tag.endswith(suffix) for suffix in [
            "Organization501c3Ind", "Organization501c4Ind", "Organization501c5Ind",
            "Organization501c6Ind", "Organization501c7Ind", "Organization501c8Ind",
            "Organization501c9Ind", "Organization501c10Ind", "Organization501c19Ind",
            "Organization501c12Ind", "Organization501c15Ind", "Organization501c25Ind"
        ]):
            # Extract the number directly from the tag suffix
            for suffix in ["Organization501c3Ind", "Organization501c4Ind", "Organization501c5Ind",
                          "Organization501c6Ind", "Organization501c7Ind", "Organization501c8Ind",
                          "Organization501c9Ind", "Organization501c10Ind", "Organization501c19Ind",
                          "Organization501c12Ind", "Organization501c15Ind", "Organization501c25Ind"]:
                if elem.tag.endswith(suffix):
                    type_num = suffix.replace("Organization501c", "").replace("Ind", "")
                    if type_num == "3":  # Special case for 501c3
                        org_type = "501(c)(3)"
                    elif type_num.isdigit() and 1 <= int(type_num) <= 29:
                        org_type = f"501(c)({type_num})"
                    else:
                        org_type = "501(c)(3)"
                        log_error("Unexpected suffix {} for EIN {} in {}, defaulting to 501(c)(3)",
                            suffix, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                    break
        elif elem.tag.endswith("TaxExemptStatus") or elem.tag.endswith("ExemptStatusCd"):
            if elem.text and "501(c)" in elem.text:
                match = re.search(r'501\(c\)\((\d+)\)', elem.text)
                if match and 1 <= int(match.group(1)) <= 29:
                    org_type = f"501(c)({match.group(1)})"
                else:
                    org_type = "501(c)(3)"
                    log_error("Invalid 501(c) format in TaxExemptStatus/ExemptStatusCd value {} for EIN {} in {}, defaulting to 501(c)(3)",
                        elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
            elif elem.text and "4947(a)(1)" in elem.text:
                org_type = "4947(a)(1)"
            else:
                org_type = "501(c)(3)"  # Default for 990EZ
                log_error("Unexpected TaxExemptStatus/ExemptStatusCd value {} for EIN {} in {}, defaulting to 501(c)(3)",
                    elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        elif elem.tag.endswith("Organization4947a1NotPFInd"):
            org_type = "4947(a)(1)"
        else:
            org_type = "501(c)(3)"  # Default for 990EZ
            log_error("Unexpected org_type tag {} for EIN {} in {}, defaulting to 501(c)(3)",
                elem.tag, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    else:
        log_error("Failed to parse org_type for EIN {} in {}", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces,context)
        all_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("No org_type tags found, defaulting to 501(c)(3). All ReturnData tags: {} in {}", all_tags, xml_filename, ein=context.get('filer_ein', 'Unknown'))
        org_type = "501(c)(3)"  # Default for 990EZ when no org_type tags are found
    log_error("Parsed org_type {} for EIN {} in {}", org_type, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return org_type

def parse_officer_comp_990ez(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990EZ["officer_comp_elements"]:
        officer_elems = root.xpath(xpath, namespaces=namespaces)
        if officer_elems:
            for person in officer_elems:
                comp_elem = find_element(person, XPATHS_990EZ["officer_comp_value"], namespaces,context)
                if comp_elem is not None:
                    comp = parse_int(comp_elem.text)
                    log_error("Raw officer_comp value: {} for EIN {} in {}",
                        comp_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                    if comp > context.get("total_exp", 0) and context.get("total_exp", 0) > 0:
                        log_error("Suspicious officer_comp ${} exceeds total_exp ${} in {}",
                            comp, context['total_exp'], xml_filename, ein=context.get('filer_ein', 'Unknown'))
                        continue
                    total += comp
        else:
            log_error("No officer elements found for EIN {} in {}. Tried XPaths: {}",
                context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ['officer_comp_elements'], ein=context.get('filer_ein', 'Unknown'))
    log_error("Parsed officer_comp ${} for EIN {} in {}", total, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_grants_to_others_990ez(root, field, namespaces, xml_filename, context):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    for xpath in XPATHS_990EZ["grant_elements_o"]:
        schedule_o = find_element(root, [xpath], namespaces,context)
        if schedule_o is not None:
            desc = find_element(schedule_o, XPATHS_990EZ["schedule_o_value"], namespaces,context)
            if desc is not None and "DISBURSEMENT" in desc.text.upper():
                match = re.search(r'\$(\d+\.\d{2}|\d+)', desc.text)
                if match:
                    amount = int(float(match.group(1).replace('$', '')))
                    total += amount
                    log_error("Parsed grants_to_others ${} from Schedule O DISBURSEMENT in {}", amount, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    for xpath in XPATHS_990EZ["grant_elements_i"]:
        schedule_i = find_element(root, [xpath], namespaces,context)
        if schedule_i is not None:
            for sub_xpath in XPATHS_990EZ["grant_sub_elements_i"]:
                for grant in schedule_i.xpath(sub_xpath, namespaces=namespaces):
                    amount_elem = find_element(grant, XPATHS_990EZ["grant_value"], namespaces,context)
                    if amount_elem is not None:
                        amount = parse_int(amount_elem.text)
                        log_error("Raw grant value: {} for EIN {} in {}",
                            amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                        total += amount
                        if context.get('filer_ein', 'Unknown') in debug_eins:
                            log_error("{} Grant: ${} in ScheduleI for EIN {}, File {}",
                                'CHAI' if context.get('filer_ein', 'Unknown') == '271414646' else 'Amnesty', amount, context.get('filer_ein', 'Unknown'), xml_filename,ein=context.get('filer_ein', 'Unknown'))
                        elif amount > 5_000_000:
                            log_error("Found CashGrantAmt ${} in ScheduleI for EIN {}, File {}",
                                amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    for xpath in XPATHS_990EZ["grant_elements_f"]:
        schedule_f = find_element(root, [xpath], namespaces,context)
        if schedule_f is not None:
            for sub_xpath in XPATHS_990EZ["grant_sub_elements_f"]:
                for grant in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                    amount_elem = find_element(grant, XPATHS_990EZ["grant_value"], namespaces,context)
                    if amount_elem is not None:
                        amount = parse_int(amount_elem.text)
                        log_error("Raw grant value: {} for EIN {} in {}",
                            amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                        total += amount
                        if context.get('filer_ein', 'Unknown') in debug_eins:
                            log_error("{} Grant: ${} in ScheduleF for EIN {}, File {}",
                                'CHAI' if context.get('filer_ein', 'Unknown') == '271414646' else 'Amnesty', amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                        elif amount > 5_000_000:
                            log_error("Found CashGrantAmt ${} in ScheduleF for EIN {}, File {}",
                                amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    if total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins:
        log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}",
            total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces,context)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}",
            context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_foreign_expenses_990ez(root, field, namespaces, xml_filename, context):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    for xpath in XPATHS_990EZ["foreign_exp_elements"]:
        schedule_f = find_element(root, [xpath], namespaces,context)
        if schedule_f is not None:
            for sub_xpath in XPATHS_990EZ["foreign_exp_sub_elements"]:
                for activity in schedule_f.xpath(sub_xpath, namespaces=namespaces):
                    amount_elem = find_element(activity, XPATHS_990EZ["foreign_exp_value"], namespaces,context)
                    if amount_elem is not None:
                        amount = parse_int(amount_elem.text)
                        log_error("Raw foreign_exp value: {} for EIN {} in {}",
                            amount_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                        total += amount
                        if context.get('filer_ein', 'Unknown') in debug_eins or (amount > 5_000_000):
                            log_error("Found RegionTotalExpendituresAmt ${} in ScheduleF for EIN {}, File {}",
                                amount, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    if total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = find_element(root, [".//irs:ReturnData", ".//ReturnData"], namespaces,context)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero foreign_expenses for EIN {}, Name {}, File {}. ReturnData children: {}",
            context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_travel_990ez(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990EZ["travel"]:
        schedule_o = find_element(root, [xpath], namespaces,context)
        if schedule_o is not None:
            desc = find_element(schedule_o, XPATHS_990EZ["schedule_o_value"], namespaces,context)
            if desc is not None:
                desc_text = desc.text.upper()
                if "TRAVEL" in desc_text:
                    match = re.search(r'\$(\d+\.\d{2}|\d+)', desc.text)
                    if match:
                        amount = int(float(match.group(1).replace('$', '')))
                        total += amount
                        log_error("Parsed travel_amt ${} from Schedule O in {}", amount, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_conferences_990ez(root, field, namespaces, xml_filename, context):
    total = 0
    for xpath in XPATHS_990EZ["conferences"]:
        schedule_o = find_element(root, [xpath], namespaces,context)
        if schedule_o is not None:
            desc = find_element(schedule_o, XPATHS_990EZ["schedule_o_value"], namespaces,context)
            if desc is not None:
                desc_text = desc.text.upper()
                if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                    match = re.search(r'\$(\d+\.\d{2}|\d+)', desc.text)
                    if match:
                        amount = int(float(match.group(1).replace('$', '')))
                        total += amount
                        log_error("Parsed conferences_amt ${} from Schedule O in {}", amount, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_total_assets_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["total_assets"], namespaces,context)
    if elem is not None:
        value = parse_int(elem.text)
        log_error("Raw total_assets value: {} for EIN {} in {}", elem.text, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        log_error("Parsed total_assets ${} for EIN {} in {}", value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return value
    log_error("Missing total_assets for EIN {} in {}. Tried XPaths: {}", context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ['total_assets'], ein=context.get('filer_ein', 'Unknown'))
    return 0

def parse_receipt_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["receipt"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_govt_grants_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["govt_grants"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_contributions_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["contributions"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_total_exp_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["total_exp"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_prog_exp_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["prog_exp"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    log_error("Parsed {} ${} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_foreign_office_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["foreign_office"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return False
    return elem.text.strip().upper() == 'X'

def parse_filer_ein_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["filer_ein"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_form_type_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["form_type"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_tax_year_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["tax_year"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_filer_name_990ez(root, field, namespaces, xml_filename, context):
    elem = find_element(root, XPATHS_990EZ["filer_name"], namespaces,context)
    if elem is None:
        log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", field, context.get('filer_ein', 'Unknown'), xml_filename, XPATHS_990EZ[field], ein=context.get('filer_ein', 'Unknown'))
        return "Unknown"
    value = elem.text.strip()
    log_error("Parsed {} {} for EIN {} in {}", field, value, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return value

# ... (previous code unchanged until parse_990ez)

def parse_990ez(xml_content, xml_filename):
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
    except etree.ParseError as e:
        log_error("Parse error in XML file {}: {}", xml_filename, e)
        return None

    root = tree.getroot()
    namespaces = {'irs': 'http://www.irs.gov/efile'}

    context = {}
    context["filer_ein"] = parse_filer_ein_990ez(root, "filer_ein", namespaces, xml_filename, context)
    log_error("Processing XML: {}", xml_filename, ein=context.get('filer_ein', 'Unknown'))
    log_error("Extracted filer_ein: {} for {}", context['filer_ein'], xml_filename, ein=context.get('filer_ein', 'Unknown'))

    context["form_type"] = parse_form_type_990ez(root, "form_type", namespaces, xml_filename, context)
    if context["filer_ein"] == "680005486" and context["form_type"] != "990EZ":
        context["form_type"] = "990EZ"
        log_error("Forced form_type '990EZ' for EIN {} in {}", context['filer_ein'], xml_filename, ein=context.get('filer_ein', 'Unknown'))
    if context["form_type"] != "990EZ":
        log_error("XML {} is not a Form 990EZ (form_type: {}), skipping",xml_filename, context['form_type'], ein=context.get('filer_ein', 'Unknown'))
        return None
    log_error("Extracted form_type: {} for {}", context['form_type'], xml_filename, ein=context.get('filer_ein', 'Unknown'))

    context["tax_year"] = parse_tax_year_990ez(root, "tax_year", namespaces, xml_filename, context)
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

    context["filer_name"] = parse_filer_name_990ez(root, "filer_name", namespaces, xml_filename, context)
    log_error("Extracted filer_name: {} for {}", context['filer_name'], xml_filename, ein=context.get('filer_ein', 'Unknown'))

    if context["filer_ein"] == "Unknown":
        log_error("Missing Filer EIN in {}", xml_filename, ein=context.get('filer_ein', 'Unknown'))
        return None

    fields = [
        "receipt", "govt_grants", "contributions", "total_exp", "prog_exp",
        "travel", "conferences", "officer_comp", "grants_to_others", "foreign_expenses",
        "total_assets", "org_type", "foreign_office"
    ]
    data = {field: globals()[f"parse_{field}_990ez"](root, field, namespaces, xml_filename, context) for field in fields}

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
        print("Usage: python parse_990ez.py <xml_file>", file=sys.stderr)
        sys.exit(1)

    xml_file = sys.argv[1]
    try:
        with open(xml_file, 'rb') as f:
            xml_content = f.read()
    except IOError as e:
        print("Error reading XML file {}: {}",xml_file, e, file=sys.stderr, ein=context.get('filer_ein', 'Unknown'))
        sys.exit(1)

    row = parse_990ez(xml_content, xml_file)
    if row:
        print('\t'.join(str(x) for x in row))

if __name__ == "__main__":
    main()