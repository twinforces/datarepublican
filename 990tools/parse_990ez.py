# parse_990ez.py
import sys
from lxml import etree
from io import BytesIO
import logging
import re
from nameparser import HumanName
from parse_utils import parse_int_field, parse_string_field, parse_schedule, clean_name, MONEY_PATTERN
from xpaths import XPATHS_990EZ, NAMESPACES

logger = None
log_error = None
verbose = False
DEBUG_EINS = set()

ORG_TYPE_SUFFIXES = frozenset([
    "Organization501c3Ind", "Organization501c4Ind", "Organization501c5Ind",
    "Organization501c6Ind", "Organization501c7Ind", "Organization501c8Ind",
    "Organization501c9Ind", "Organization501c10Ind", "Organization501c19Ind",
    "Organization501c12Ind", "Organization501c15Ind", "Organization501c25Ind"
])

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
        logger.error(msg_format.format(*args) if args else msg_format, exc_info=exc_info)
    else:
        logger.info(msg_format.format(*args) if args else msg_format)

if log_error is None:
    log_error = stub_log_error

def parse_org_type_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    elem = parse_string_field(root, XPATHS_990EZ, "org_type", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
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
                    log_error("Invalid 501(c) format in TaxExemptStatus/ExemptStatusCd value {} for EIN {} in {}, defaulting to 501(c)(3)", 
                              elem.text, context.get('filer_ein', 'Unknown'), xml_filename, 
                              ein=context.get('filer_ein', 'Unknown'))
            elif elem.text and "4947(a)(1)" in elem.text:
                org_type = "4947(a)(1)"
            else:
                org_type = "501(c)(3)"  # Default for 990EZ
                log_error("Unexpected TaxExemptStatus/ExemptStatusCd value {} for EIN {} in {}, defaulting to 501(c)(3)", 
                          elem.text, context.get('filer_ein', 'Unknown'), xml_filename, 
                          ein=context.get('filer_ein', 'Unknown'))
        elif elem.tag.endswith("Organization4947a1NotPFInd"):
            org_type = "4947(a)(1)"
        else:
            org_type = "501(c)(3)"  # Default for 990EZ
            log_error("Unexpected org_type tag {} for EIN {} in {}, defaulting to 501(c)(3)", 
                      elem.tag, context.get('filer_ein', 'Unknown'), xml_filename, 
                      ein=context.get('filer_ein', 'Unknown'))
    else:
        log_error("Failed to parse org_type for EIN {} in {}", 
                  context.get('filer_ein', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
        return_data = parse_string_field(root, XPATHS_990EZ, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        all_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("No org_type tags found, defaulting to 501(c)(3). All ReturnData tags: {} in {}", 
                  all_tags, xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
        org_type = "501(c)(3)"  # Default for 990EZ when no org_type tags are found
    if verbose:
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

def parse_grants_to_others_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    debug_eins = {"271414646", "520851555", "471203726", "464284638", "592965108", "486289145", "680005486", "650869895"}

    total += parse_schedule(root, XPATHS_990EZ, "grant_elements_i", "grant_sub_elements_i", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins=debug_eins)

    total += parse_schedule(root, XPATHS_990EZ, "grant_elements_f", "grant_sub_elements_f", "grant_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, debug_eins=debug_eins)

    if total > 5_000_000 or context.get('filer_ein', 'Unknown') in debug_eins:
        log_error("Non-zero grants_to_others ${} for EIN {}, Name {}, TaxYear {}, XML {}", 
                  total, context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), context.get('tax_year', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
    elif total == 0 and context.get('filer_ein', 'Unknown') in debug_eins:
        return_data = parse_string_field(root, XPATHS_990EZ, "return_data", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        child_tags = [child.tag for child in return_data.xpath("*", namespaces=namespaces)] if return_data is not None else []
        log_error("Zero grants_to_others for EIN {}, Name {}, File {}. ReturnData children: {}", 
                  context.get('filer_ein', 'Unknown'), context.get('filer_name', 'Unknown'), xml_filename, child_tags, 
                  ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_travel_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    for xpath in XPATHS_990EZ["schedule_o"]:
        schedule_o = parse_string_field(root, XPATHS_990EZ, "schedule_o", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        if schedule_o is not None:
            desc = parse_string_field(schedule_o, XPATHS_990EZ, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            if desc is not None:
                desc_text = desc.upper()
                if "TRAVEL" in desc_text:
                    match = MONEY_PATTERN.search(desc)
                    if match:
                        amount_str = match.group(1).replace('$', '').replace(',', '')
                    else:
                        # Handle cases where desc is just the amount without $
                        amount_str = desc.replace(',', '')
                    try:
                        amount = int(float(amount_str))
                        total += amount
                        if verbose:
                            log_error("Parsed travel_amt ${} from Schedule O in {}",
                                      amount, xml_filename,
                                      ein=context.get('filer_ein', 'Unknown'))
                    except (ValueError, TypeError):
                        log_error("Invalid travel amount '{}' in {}", desc, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_conferences_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = 0
    for xpath in XPATHS_990EZ["schedule_o"]:
        schedule_o = parse_string_field(root, XPATHS_990EZ, "schedule_o", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        if schedule_o is not None:
            desc = parse_string_field(schedule_o, XPATHS_990EZ, "schedule_o_value", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None)
            if desc is not None:
                desc_text = desc.upper()
                if "CONFERENCE" in desc_text or "MEETING" in desc_text:
                    match = MONEY_PATTERN.search(desc)
                    if match:
                        amount_str = match.group(1).replace('$', '').replace(',', '')
                    else:
                        # Handle cases where desc is just the amount without $
                        amount_str = desc.replace(',', '')
                    try:
                        amount = int(float(amount_str))
                        total += amount
                        if verbose:
                            log_error("Parsed conferences_amt ${} from Schedule O in {}",
                                      amount, xml_filename,
                                      ein=context.get('filer_ein', 'Unknown'))
                    except (ValueError, TypeError):
                        log_error("Invalid conferences amount '{}' in {}", desc, xml_filename, ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_total_assets_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    total = parse_int_field(root, XPATHS_990EZ, "total_assets", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)
    if total > 0 and (verbose or context.get('filer_ein', 'Unknown') in DEBUG_EINS):
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

def parse_organization_address_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    """Parse organization address for Google Street View."""
    address_parts = {}

    # Find business address
    business_address = parse_string_field(root, XPATHS_990EZ, "business_address", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)

    if business_address is not None:
        address_line_1 = parse_string_field(business_address, XPATHS_990EZ, "address_line_1", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="")
        address_line_2 = parse_string_field(business_address, XPATHS_990EZ, "address_line_2", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="")
        city = parse_string_field(business_address, XPATHS_990EZ, "city", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="")
        state = parse_string_field(business_address, XPATHS_990EZ, "state", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="")
        zip_code = parse_string_field(business_address, XPATHS_990EZ, "zip_code", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="")

        # Create canonical address
        address_parts = {
            'address_line_1': address_line_1,
            'address_line_2': address_line_2,
            'city': city,
            'state': state,
            'zip_code': zip_code
        }

        # Create full address string
        full_address = []
        if address_line_1:
            full_address.append(address_line_1)
        if address_line_2:
            full_address.append(address_line_2)
        city_state_zip = []
        if city:
            city_state_zip.append(city)
        if state:
            city_state_zip.append(state)
        if zip_code:
            city_state_zip.append(zip_code)
        if city_state_zip:
            full_address.append(", ".join(city_state_zip))

        canonical_address = " ".join(full_address) if full_address else ""

        return canonical_address, address_parts

    return "", {}

def parse_contractors_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    """Parse contractors and consultants from Schedule L using streaming approach."""
    if verbose:
        log_error("Starting contractor parsing for EIN {} in {}", context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
    contractors = []

    # Define contractor element tags for streaming parsing
    contractor_tags = ["ContractorCompensationGrp", "BusinessRelationshipWithOrganizationGrp", "LoansFromOrganizationGrp", "BusinessTransactionsWithOrganizationGrp"]

    try:
        # Use streaming parser to avoid loading entire XML into memory
        from io import BytesIO
        import lxml.etree as etree

        # Get the XML content from root (assuming root is from a parsed tree)
        xml_content = etree.tostring(root, encoding='unicode')

        context_iter = etree.iterparse(BytesIO(xml_content.encode('utf-8')), events=('end',), tag=contractor_tags, recover=True)

        for event, elem in context_iter:
            contractor_name = ""
            contractor_amount = 0
            contractor_ein = ""

            # Extract contractor name using all name XPaths
            for name_xpath in XPATHS_990EZ["contractor_name"]:
                name_results = name_xpath(elem)
                if name_results:
                    contractor_name = name_results[0].text.strip() if name_results[0].text else ""
                    break

            # Extract contractor amount using all amount XPaths
            for amount_xpath in XPATHS_990EZ["contractor_amount"]:
                amount_results = amount_xpath(elem)
                if amount_results:
                    try:
                        contractor_amount = int(amount_results[0].text.strip())
                        break
                    except (ValueError, AttributeError):
                        continue

            # Extract contractor EIN using all EIN XPaths
            for ein_xpath in XPATHS_990EZ["contractor_ein"]:
                ein_results = ein_xpath(elem)
                if ein_results:
                    contractor_ein = ein_results[0].text.strip() if ein_results[0].text else ""
                    break

            # Try to extract address information for the contractor
            contractor_address = ""
            contractor_zip = ""
            contractor_po_box = ""
            is_foreign_contractor = False

            # Look for address elements in the contractor record
            address_elem = elem.find(".//{http://www.irs.gov/efile}USAddress")
            if address_elem is None:
                address_elem = elem.find(".//USAddress")
            if address_elem is None:
                address_elem = elem.find(".//{http://www.irs.gov/efile}ForeignAddress")
                is_foreign_contractor = True
            if address_elem is None:
                address_elem = elem.find(".//ForeignAddress")
                is_foreign_contractor = True

            if address_elem is not None:
                if is_foreign_contractor:
                    # Handle foreign contractor addresses
                    country_elem = address_elem.find(".//{http://www.irs.gov/efile}CountryCd")
                    if country_elem is None:
                        country_elem = address_elem.find(".//CountryCd")

                    if country_elem is not None and country_elem.text:
                        country_code = country_elem.text.strip()
                        from countryCodes import lookupCC
                        country = lookupCC(country_code) if country_code else None
                        if country:
                            contractor_ein = country["number"]
                            contractor_name = country["name"]
                        else:
                            contractor_ein = "999"
                            contractor_name = f"Foreign Contractor - {country_code or 'Unknown'}"
                else:
                    # Handle US contractor addresses
                    addr_line_1 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine1Txt")
                    if addr_line_1 is None:
                        addr_line_1 = address_elem.find(".//AddressLine1Txt")

                    addr_line_2 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine2Txt")
                    if addr_line_2 is None:
                        addr_line_2 = address_elem.find(".//AddressLine2Txt")

                    city = address_elem.find(".//{http://www.irs.gov/efile}CityNm")
                    if city is None:
                        city = address_elem.find(".//City")

                    state = address_elem.find(".//{http://www.irs.gov/efile}StateAbbreviationCd")
                    if state is None:
                        state = address_elem.find(".//State")

                    zip_code = address_elem.find(".//{http://www.irs.gov/efile}ZIPCd")
                    if zip_code is None:
                        zip_code = address_elem.find(".//ZIPCode")

                    # Build address string
                    address_parts = []
                    if addr_line_1 is not None and addr_line_1.text:
                        address_parts.append(addr_line_1.text.strip())
                    if addr_line_2 is not None and addr_line_2.text:
                        address_parts.append(addr_line_2.text.strip())
                    if city is not None and city.text:
                        if state is not None and state.text:
                            address_parts.append(f"{city.text.strip()}, {state.text.strip()}")
                        else:
                            address_parts.append(city.text.strip())
                    if zip_code is not None and zip_code.text:
                        contractor_zip = zip_code.text.strip()

                    contractor_address = " ".join(address_parts)

                    # Check for PO Box
                    if addr_line_1 is not None and addr_line_1.text and "PO BOX" in addr_line_1.text.upper():
                        contractor_po_box = addr_line_1.text.strip()

            if contractor_name or contractor_amount:
                contractors.append({
                    'name': contractor_name,
                    'amount': contractor_amount or 0,
                    'ein': contractor_ein,
                    'address': contractor_address,
                    'zip_code': contractor_zip,
                    'po_box': contractor_po_box,
                    'filer_ein': context.get('filer_ein', ''),
                    'tax_year': context.get('tax_year', '')
                })

            # Clear the element to free memory
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

    except Exception as e:
        # Fallback to original method if streaming fails
        log_error("Streaming contractor parsing failed, falling back to original method: {}", str(e), ein=context.get('filer_ein', 'Unknown'))

        # Find Schedule L or IRS990EZ
        schedule_l = None
        for xpath in XPATHS_990EZ["contractors_schedule_l"]:
            result = xpath(root)
            if verbose:
                log_error("Trying contractor container XPath: {} - found {} results for EIN {} in {}", xpath.path, len(result) if result else 0, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
            if result:
                schedule_l = result[0] if result else None
                if verbose:
                    log_error("Found contractor container: {} for EIN {} in {}", schedule_l.tag if schedule_l is not None else None, context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))
                break

        if schedule_l is not None:
            # Find contractor elements using all contractor element XPaths
            contractor_elements = []
            for xpath in XPATHS_990EZ["contractor_elements"]:
                result = xpath(schedule_l)
                contractor_elements.extend(result)

            if verbose:
                log_error("Found {} contractor elements for EIN {} in {}", len(contractor_elements), context.get('filer_ein', 'Unknown'), xml_filename, ein=context.get('filer_ein', 'Unknown'))

            for elem in contractor_elements:
                contractor_name = ""
                contractor_amount = 0
                contractor_ein = ""

                # Extract contractor name using all name XPaths
                for name_xpath in XPATHS_990EZ["contractor_name"]:
                    name_results = name_xpath(elem)
                    if name_results:
                        contractor_name = name_results[0].text.strip() if name_results[0].text else ""
                        break

                # Extract contractor amount using all amount XPaths
                for amount_xpath in XPATHS_990EZ["contractor_amount"]:
                    amount_results = amount_xpath(elem)
                    if amount_results:
                        try:
                            contractor_amount = int(amount_results[0].text.strip())
                            break
                        except (ValueError, AttributeError):
                            continue

                # Extract contractor EIN using all EIN XPaths
                for ein_xpath in XPATHS_990EZ["contractor_ein"]:
                    ein_results = ein_xpath(elem)
                    if ein_results:
                        contractor_ein = ein_results[0].text.strip() if ein_results[0].text else ""
                        break

                # Try to extract address information for the contractor
                contractor_address = ""
                contractor_zip = ""
                contractor_po_box = ""
                is_foreign_contractor = False

                # Look for address elements in the contractor record
                address_elem = elem.find(".//{http://www.irs.gov/efile}USAddress")
                if address_elem is None:
                    address_elem = elem.find(".//USAddress")
                if address_elem is None:
                    address_elem = elem.find(".//{http://www.irs.gov/efile}ForeignAddress")
                    is_foreign_contractor = True
                if address_elem is None:
                    address_elem = elem.find(".//ForeignAddress")
                    is_foreign_contractor = True

                if address_elem is not None:
                    if is_foreign_contractor:
                        # Handle foreign contractor addresses
                        country_elem = address_elem.find(".//{http://www.irs.gov/efile}CountryCd")
                        if country_elem is None:
                            country_elem = address_elem.find(".//CountryCd")

                        if country_elem is not None and country_elem.text:
                            country_code = country_elem.text.strip()
                            from countryCodes import lookupCC
                            country = lookupCC(country_code) if country_code else None
                            if country:
                                contractor_ein = country["number"]
                                contractor_name = country["name"]
                            else:
                                contractor_ein = "999"
                                contractor_name = f"Foreign Contractor - {country_code or 'Unknown'}"
                    else:
                        # Handle US contractor addresses
                        addr_line_1 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine1Txt")
                        if addr_line_1 is None:
                            addr_line_1 = address_elem.find(".//AddressLine1Txt")

                        addr_line_2 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine2Txt")
                        if addr_line_2 is None:
                            addr_line_2 = address_elem.find(".//AddressLine2Txt")

                        city = address_elem.find(".//{http://www.irs.gov/efile}CityNm")
                        if city is None:
                            city = address_elem.find(".//City")

                        state = address_elem.find(".//{http://www.irs.gov/efile}StateAbbreviationCd")
                        if state is None:
                            state = address_elem.find(".//State")

                        zip_code = address_elem.find(".//{http://www.irs.gov/efile}ZIPCd")
                        if zip_code is None:
                            zip_code = address_elem.find(".//ZIPCode")

                        # Build address string
                        address_parts = []
                        if addr_line_1 is not None and addr_line_1.text:
                            address_parts.append(addr_line_1.text.strip())
                        if addr_line_2 is not None and addr_line_2.text:
                            address_parts.append(addr_line_2.text.strip())
                        if city is not None and city.text:
                            if state is not None and state.text:
                                address_parts.append(f"{city.text.strip()}, {state.text.strip()}")
                            else:
                                address_parts.append(city.text.strip())
                        if zip_code is not None and zip_code.text:
                            contractor_zip = zip_code.text.strip()

                        contractor_address = " ".join(address_parts)

                        # Check for PO Box
                        if addr_line_1 is not None and addr_line_1.text and "PO BOX" in addr_line_1.text.upper():
                            contractor_po_box = addr_line_1.text.strip()

                if contractor_name or contractor_amount:
                    contractors.append({
                        'name': contractor_name,
                        'amount': contractor_amount or 0,
                        'ein': contractor_ein,
                        'address': contractor_address,
                        'zip_code': contractor_zip,
                        'po_box': contractor_po_box,
                        'filer_ein': context.get('filer_ein', ''),
                        'tax_year': context.get('tax_year', '')
                    })

    return contractors

def parse_political_contributions_990ez(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=None):
    """Parse political contributions from Schedule C using streaming approach."""
    contributions = []

    # Define political contribution element tags for streaming parsing
    political_tags = ["PoliticalCampaignActyGrp", "PoliticalCampaignActivitiesGrp"]

    try:
        # Use streaming parser to avoid loading entire XML into memory
        from io import BytesIO
        import lxml.etree as etree

        # Get the XML content from root (assuming root is from a parsed tree)
        xml_content = etree.tostring(root, encoding='unicode')

        context_iter = etree.iterparse(BytesIO(xml_content.encode('utf-8')), events=('end',), tag=political_tags, recover=True)

        for event, elem in context_iter:
            recipient = parse_string_field(elem, XPATHS_990EZ, "political_recipient", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="")
            amount = parse_int_field(elem, XPATHS_990EZ, "political_amount", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

            # Try to extract address information for the recipient
            recipient_address = ""
            recipient_zip = ""
            recipient_po_box = ""
            is_foreign_recipient = False

            # Look for address elements in the contribution
            address_elem = elem.find(".//{http://www.irs.gov/efile}USAddress")
            if address_elem is None:
                address_elem = elem.find(".//USAddress")
            if address_elem is None:
                address_elem = elem.find(".//{http://www.irs.gov/efile}ForeignAddress")
                is_foreign_recipient = True
            if address_elem is None:
                address_elem = elem.find(".//ForeignAddress")
                is_foreign_recipient = True

            if address_elem is not None:
                if is_foreign_recipient:
                    # Handle foreign recipient addresses
                    country_elem = address_elem.find(".//{http://www.irs.gov/efile}CountryCd")
                    if country_elem is None:
                        country_elem = address_elem.find(".//CountryCd")

                    if country_elem is not None and country_elem.text:
                        country_code = country_elem.text.strip()
                        from countryCodes import lookupCC
                        country = lookupCC(country_code) if country_code else None
                        if country:
                            recipient_ein = country["number"]
                            recipient = country["name"]
                        else:
                            recipient_ein = "999"
                            recipient = f"Foreign Recipient - {country_code or 'Unknown'}"
                else:
                    # Handle US recipient addresses
                    addr_line_1 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine1Txt")
                    if addr_line_1 is None:
                        addr_line_1 = address_elem.find(".//AddressLine1Txt")

                    addr_line_2 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine2Txt")
                    if addr_line_2 is None:
                        addr_line_2 = address_elem.find(".//AddressLine2Txt")

                    city = address_elem.find(".//{http://www.irs.gov/efile}CityNm")
                    if city is None:
                        city = address_elem.find(".//City")

                    state = address_elem.find(".//{http://www.irs.gov/efile}StateAbbreviationCd")
                    if state is None:
                        state = address_elem.find(".//State")

                    zip_code = address_elem.find(".//{http://www.irs.gov/efile}ZIPCd")
                    if zip_code is None:
                        zip_code = address_elem.find(".//ZIPCode")

                    # Build address string
                    address_parts = []
                    if addr_line_1 is not None and addr_line_1.text:
                        address_parts.append(addr_line_1.text.strip())
                    if addr_line_2 is not None and addr_line_2.text:
                        address_parts.append(addr_line_2.text.strip())
                    if city is not None and city.text:
                        if state is not None and state.text:
                            address_parts.append(f"{city.text.strip()}, {state.text.strip()}")
                        else:
                            address_parts.append(city.text.strip())
                    if zip_code is not None and zip_code.text:
                        recipient_zip = zip_code.text.strip()

                    recipient_address = " ".join(address_parts)

                    # Check for PO Box
                    if addr_line_1 is not None and addr_line_1.text and "PO BOX" in addr_line_1.text.upper():
                        recipient_po_box = addr_line_1.text.strip()

            if recipient or amount:
                contributions.append({
                    'recipient': recipient,
                    'amount': amount or 0,
                    'recipient_address': recipient_address,
                    'recipient_zip': recipient_zip,
                    'recipient_po_box': recipient_po_box,
                    'filer_ein': context.get('filer_ein', ''),
                    'tax_year': context.get('tax_year', '')
                })

            # Clear the element to free memory
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

    except Exception as e:
        # Fallback to original method if streaming fails
        log_error("Streaming political contribution parsing failed, falling back to original method: {}", str(e), ein=context.get('filer_ein', 'Unknown'))

        # Find Schedule C
        schedule_c = parse_string_field(root, XPATHS_990EZ, "political_schedule_c", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)

        if schedule_c is not None:
            # Find political contribution elements
            political_elements = parse_string_field(schedule_c, XPATHS_990EZ, "political_contributions", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)

            if political_elements:
                if not isinstance(political_elements, list):
                    political_elements = [political_elements]

                for elem in political_elements:
                    recipient = parse_string_field(elem, XPATHS_990EZ, "political_recipient", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default="")
                    amount = parse_int_field(elem, XPATHS_990EZ, "political_amount", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose)

                    # Try to extract address information for the recipient
                    recipient_address = ""
                    recipient_zip = ""
                    recipient_po_box = ""
                    is_foreign_recipient = False

                    # Look for address elements in the contribution
                    address_elem = elem.find(".//{http://www.irs.gov/efile}USAddress")
                    if address_elem is None:
                        address_elem = elem.find(".//USAddress")
                    if address_elem is None:
                        address_elem = elem.find(".//{http://www.irs.gov/efile}ForeignAddress")
                        is_foreign_recipient = True
                    if address_elem is None:
                        address_elem = elem.find(".//ForeignAddress")
                        is_foreign_recipient = True

                    if address_elem is not None:
                        if is_foreign_recipient:
                            # Handle foreign recipient addresses
                            country_elem = address_elem.find(".//{http://www.irs.gov/efile}CountryCd")
                            if country_elem is None:
                                country_elem = address_elem.find(".//CountryCd")

                            if country_elem is not None and country_elem.text:
                                country_code = country_elem.text.strip()
                                from countryCodes import lookupCC
                                country = lookupCC(country_code) if country_code else None
                                if country:
                                    recipient_ein = country["number"]
                                    recipient = country["name"]
                                else:
                                    recipient_ein = "999"
                                    recipient = f"Foreign Recipient - {country_code or 'Unknown'}"
                        else:
                            # Handle US recipient addresses
                            addr_line_1 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine1Txt")
                            if addr_line_1 is None:
                                addr_line_1 = address_elem.find(".//AddressLine1Txt")

                            addr_line_2 = address_elem.find(".//{http://www.irs.gov/efile}AddressLine2Txt")
                            if addr_line_2 is None:
                                addr_line_2 = address_elem.find(".//AddressLine2Txt")

                            city = address_elem.find(".//{http://www.irs.gov/efile}CityNm")
                            if city is None:
                                city = address_elem.find(".//City")

                            state = address_elem.find(".//{http://www.irs.gov/efile}StateAbbreviationCd")
                            if state is None:
                                state = address_elem.find(".//State")

                            zip_code = address_elem.find(".//{http://www.irs.gov/efile}ZIPCd")
                            if zip_code is None:
                                zip_code = address_elem.find(".//ZIPCode")

                            # Build address string
                            address_parts = []
                            if addr_line_1 is not None and addr_line_1.text:
                                address_parts.append(addr_line_1.text.strip())
                            if addr_line_2 is not None and addr_line_2.text:
                                address_parts.append(addr_line_2.text.strip())
                            if city is not None and city.text:
                                if state is not None and state.text:
                                    address_parts.append(f"{city.text.strip()}, {state.text.strip()}")
                                else:
                                    address_parts.append(city.text.strip())
                            if zip_code is not None and zip_code.text:
                                recipient_zip = zip_code.text.strip()

                            recipient_address = " ".join(address_parts)

                            # Check for PO Box
                            if addr_line_1 is not None and addr_line_1.text and "PO BOX" in addr_line_1.text.upper():
                                recipient_po_box = addr_line_1.text.strip()

                    if recipient or amount:
                        contributions.append({
                            'recipient': recipient,
                            'amount': amount or 0,
                            'recipient_address': recipient_address,
                            'recipient_zip': recipient_zip,
                            'recipient_po_box': recipient_po_box,
                            'filer_ein': context.get('filer_ein', ''),
                            'tax_year': context.get('tax_year', '')
                        })

    return contributions

def parse_990ez(root, xml_filename, xpath_cache, filer_ein, tax_year, form_type, log_error=log_error, xpath_match_stats=None):
    namespaces = {'irs': 'http://www.irs.gov/efile'}
    context = {
        'filer_ein': filer_ein,
        'tax_year': tax_year,
        'form_type': form_type
    }

    if context["filer_ein"] == "680005486" and context["form_type"] != "990EZ":
        context["form_type"] = "990EZ"
        log_error("Forced form_type '990EZ' for EIN {} in {}", context['filer_ein'], xml_filename, ein=context['filer_ein'])
    if context["form_type"] != "990EZ":
        log_error("XML {} is not a Form 990EZ (form_type: {}), skipping", xml_filename, context['form_type'], ein=context['filer_ein'])
        return None, []

    context["filer_name"] = parse_filer_name_990ez(root, "filer_name", namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)

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
        ("org_type", parse_org_type_990ez),
        ("contractors", parse_contractors_990ez),
        ("political_contributions", parse_political_contributions_990ez),
        ("organization_address", parse_organization_address_990ez)
    ]
    data = {}
    officer_entries = []
    contractors = []
    political_contributions = []
    canonical_address = ""
    address_parts = {}

    for field, func in fields:
        if field == "officer_comp":
            total, entries = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
            data[field] = total
            officer_entries.extend(entries)
        elif field == "contractors":
            contractors = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
            data[field] = contractors
        elif field == "political_contributions":
            political_contributions = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
            data[field] = political_contributions
        elif field == "organization_address":
            canonical_address, address_parts = func(root, field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats)
            data[field] = canonical_address
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
    data["comp_ptile"] = "n/y"
    data["travel_ptile"] = "n/y"
    data["conferences_ptile"] = "n/y"
    data["grants_ptile"] = "n/y"
    
    data["foreign_expenses_ptile"] = "n/a"
    data["govt_grants"] = "n/a"
    data["foreign_office"] = "n/a"
    data["foreign_expenses_pct"] = "n/a"
    data["foreign_expenses"] = "n/a"
    data["domestic_misrep_flag"] = False

    row = [
        context["tax_year"], context["filer_ein"], context["filer_name"], data["receipt"], data["govt_grants"],
        data["contributions"], data["org_type"], data["total_exp"], data["prog_exp"], data["travel"],
        data["conferences"], data["officer_comp"], data["comp_pct"], data["comp_ptile"], data["travel_pct"],
        data["travel_ptile"], data["conferences_pct"], data["conferences_ptile"], data["grants_pct"],
        data["grants_ptile"], data["foreign_expenses_pct"], data["foreign_expenses_ptile"], data["grift_ratio"],
        data["total_assets"], context["form_type"], data["denominator"], data["foreign_office"],
        data["foreign_expenses"], data["grants_to_others"], data["domestic_misrep_flag"], xml_filename,
        canonical_address
    ]
    return row, officer_entries, contractors, political_contributions

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

    row, _ = parse_990ez(root, xml_file, xpath_cache={}, filer_ein=filer_ein, tax_year=tax_year, form_type=form_type)
    if row:
        row_str = [str(x).replace('\t', '\\t').replace('\n', '\\n') for x in row]
        print('\t'.join(row_str))

if __name__ == "__main__":
    main()