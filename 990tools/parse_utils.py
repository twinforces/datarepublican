# parse_utils.py
import re
from lxml import etree
from nameparser import HumanName
from io import BytesIO
import logging
from xpaths import NAMESPACES, XPATHS_990EZ, XPATHS_990, XPATHS_990PF, GRANT_XPATHS, GRANT_EIN_XPATHS, GRANT_NAME_XPATHS, GRANT_AMOUNT_XPATHS, GRANT_FOREIGN_ADDRESS_XPATH, GRANT_COUNTRY_XPATH, GRANT_US_ADDRESS_XPATH
from xpaths import SCHEDULE_C_XPATHS, SCHEDULE_C_AMOUNT_XPATHS, SCHEDULE_C_RECIPIENT_XPATHS, SCHEDULE_C_EIN_XPATHS
from xpath_utils import find_element
from extract_utils import canonicalize_address
MONEY_PATTERN = re.compile(r'\$([\d,]+(?:\.\d{2})?)')
ORG_TYPE_PATTERN = re.compile(r'501\(c\)\((\d+)\)')

def parse_int_field(root, xpaths_dict, field, namespaces, xml_filename, context, xpath_cache, log_error, xpath_match_stats, verbose=False):
    elem = find_element(root, xpaths_dict[field], namespaces, xpath_cache=xpath_cache, field=field, form_type=context.get('form_type'), log_error=log_error, xpath_match_stats=xpath_match_stats)
    if elem is not None:
        try:
            value = int(elem.text.strip())
            if verbose:
                log_error("Parsed int field {}: {} for EIN {} in {}", field, value, context.get('filer_ein'), xml_filename)
            return value
        except (ValueError, AttributeError):
            log_error("Invalid int value for field {}: {} in {}", field, elem.text, xml_filename)
    return 0

def parse_string_field(root, xpaths_dict, field, namespaces, xml_filename, context, xpath_cache, log_error, xpath_match_stats, verbose=False, default=None, return_element=False):
    elem = find_element(root, xpaths_dict[field], namespaces, xpath_cache=xpath_cache, field=field, form_type=context.get('form_type'), log_error=log_error, xpath_match_stats=xpath_match_stats)
    if elem is not None:
        value = elem.text.strip() if elem.text else default
        if verbose:
            log_error("Parsed string field {}: {} for EIN {} in {}", field, value, context.get('filer_ein'), xml_filename)
        return elem if return_element else value
    return default

def parse_schedule(root, xpaths_dict, elements_key, sub_elements_key, value_key, namespaces, xml_filename, context, xpath_cache, log_error, xpath_match_stats, verbose=False, debug_eins=None):
    total = 0
    elements = []
    for xpath in xpaths_dict[elements_key]:
        result = xpath(root)
        elements.extend(result)
    for elem in elements:
        sub_elements = []
        for sub_xpath in xpaths_dict[sub_elements_key]:
            sub_result = sub_xpath(elem)
            sub_elements.extend(sub_result)
        for sub_elem in sub_elements:
            value_elem = find_element(sub_elem, xpaths_dict[value_key], namespaces, xpath_cache=xpath_cache, field=value_key, form_type=context.get('form_type'), log_error=log_error, xpath_match_stats=xpath_match_stats)
            if value_elem is not None:
                try:
                    amount = int(value_elem.text.strip())
                    total += amount
                    if verbose:
                        log_error("Parsed schedule amount ${} for field in {}", amount, xml_filename)
                except (ValueError, AttributeError):
                    log_error("Invalid schedule value: {} in {}", value_elem.text, xml_filename)
    return total

def clean_name(name):
    return re.sub(r'[^a-zA-Z0-9\s]', '', name).strip().upper()

def parse_grants(xml_content, xml_filename, filer_ein, filer_name, tax_year, known_eins, form_type, backfill_entries=None, seen_backfill_keys=None):
    grants = []
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        grant_xpaths = GRANT_XPATHS.get(form_type, [])
        for xpath in grant_xpaths:
            elements = xpath(root)
            for elem in elements:
                # Try to find EIN using all available XPaths
                grant_ein = "Unknown"
                for ein_xpath in GRANT_EIN_XPATHS:
                    ein_elem = elem.xpath(ein_xpath.path, namespaces=NAMESPACES)
                    if ein_elem:
                        grant_ein = ein_elem[0].text.strip()
                        break
    
                # Try to find name using all available XPaths
                grantee_name = "Unknown"
                for name_xpath in GRANT_NAME_XPATHS:
                    name_elem = elem.xpath(name_xpath.path, namespaces=NAMESPACES)
                    if name_elem:
                        grantee_name = name_elem[0].text.strip()
                        break
    
                # Try to find amount using all available XPaths
                grant_amt = 0
                for amount_xpath in GRANT_AMOUNT_XPATHS:
                    amount_elem = elem.xpath(amount_xpath.path, namespaces=NAMESPACES)
                    if amount_elem and amount_elem[0].text:
                        try:
                            grant_amt = int(float(amount_elem[0].text.strip()))
                            break
                        except (ValueError, TypeError):
                            continue
    
                is_foreign = elem.xpath(GRANT_FOREIGN_ADDRESS_XPATH.path, namespaces=NAMESPACES)
                if is_foreign:
                    country_elem = elem.xpath(GRANT_COUNTRY_XPATH.path, namespaces=NAMESPACES)
                    country_code = country_elem[0].text.strip() if country_elem else None
                    from countryCodes import lookupCC
                    country = lookupCC(country_code) if country_code else None
                    if country:
                        grant_ein = country["number"]
                        grantee_name = country["name"]
                    else:
                        grant_ein = "999"
                        grantee_name = "Foreign_" + (country_code or "Unknown")
    
                if grant_ein == "Unknown" and not is_foreign:
                    continue
                if grant_amt > 0:
                    grants.append({
                        'filer_ein': filer_ein,
                        'filer_name': filer_name,
                        'grant_ein': grant_ein,
                        'grant_amt': grant_amt,
                        'tax_year': tax_year
                    })
                    # Only add backfill entries if the parameters are provided
                    if backfill_entries is not None and seen_backfill_keys is not None:
                        if grant_ein not in known_eins and grant_ein.isdigit() and grant_ein != "999" and not is_foreign:
                            is_valid, reason = validate_ein(grant_ein)
                            if is_valid:
                                address_components = elem.xpath(GRANT_US_ADDRESS_XPATH.path, namespaces=NAMESPACES)
                                canonical_address, po_box, zip_code, _ = canonicalize_address([comp for comp in address_components if comp.text], None)
                                if canonical_address or po_box or zip_code:
                                    backfill_key = (grant_ein, grantee_name, zip_code)
                                    if backfill_key not in seen_backfill_keys:
                                        seen_backfill_keys.add(backfill_key)
                                        backfill_entries.append({
                                            'grant_ein': grant_ein,
                                            'name': grantee_name,
                                            'canonical_address': canonical_address,
                                            'po_box': po_box,
                                            'zip_code': zip_code
                                        })
    except Exception as e:
        logging.error(f"Error parsing grants from {xml_filename}: {e}")
    return grants

def parse_contributions(xml_content, xml_filename, filer_ein, filer_name, tax_year, form_type):
    contributions = []
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        schedule_c_xpaths = SCHEDULE_C_XPATHS.get(form_type, [])
        for xpath in schedule_c_xpaths:
            elements = xpath(root)
            for elem in elements:
                # Try to find amount using all available XPaths
                amount = 0
                for amount_xpath in SCHEDULE_C_AMOUNT_XPATHS:
                    amount_elem = elem.xpath(amount_xpath.path, namespaces=NAMESPACES)
                    if amount_elem and amount_elem[0].text:
                        try:
                            amount = int(float(amount_elem[0].text.strip()))
                            break
                        except (ValueError, TypeError):
                            continue

                # Try to find recipient name using all available XPaths
                recipient_name = "Unknown"
                for recipient_xpath in SCHEDULE_C_RECIPIENT_XPATHS:
                    recipient_elem = elem.xpath(recipient_xpath.path, namespaces=NAMESPACES)
                    if recipient_elem:
                        recipient_name = recipient_elem[0].text.strip()
                        break

                # Try to find recipient EIN using all available XPaths
                recipient_ein = "Unknown"
                for ein_xpath in SCHEDULE_C_EIN_XPATHS:
                    ein_elem = elem.xpath(ein_xpath.path, namespaces=NAMESPACES)
                    if ein_elem:
                        recipient_ein = ein_elem[0].text.strip()
                        break

                # Check for foreign address
                is_foreign = elem.xpath(GRANT_FOREIGN_ADDRESS_XPATH.path, namespaces=NAMESPACES)
                if is_foreign:
                    country_elem = elem.xpath(GRANT_COUNTRY_XPATH.path, namespaces=NAMESPACES)
                    country_code = country_elem[0].text.strip() if country_elem else None
                    from countryCodes import lookupCC
                    country = lookupCC(country_code) if country_code else None
                    if country:
                        recipient_ein = country["number"]
                        recipient_name = country["name"]
                    else:
                        recipient_ein = "999"
                        recipient_name = "Foreign_" + (country_code or "Unknown")

                if recipient_ein == "Unknown" and not is_foreign:
                    continue
                if amount > 0:
                    contributions.append({
                        'filer_ein': filer_ein,
                        'filer_name': filer_name,
                        'recipient_ein': recipient_ein,
                        'amount': amount,
                        'tax_year': tax_year
                    })
    except Exception as e:
        logging.error(f"Error parsing contributions from {xml_filename}: {e}")
    return contributions

def validate_ein(ein):
    if not ein or not re.match(r'^\d{9}$', ein) or ein == "000000000":
        return False, "Invalid EIN format"
    prefix = ein[:2]
    valid_prefixes = {'01', '02', '03', '04', '05', '06', '11', '13', '14', '16', '20', '21', '22', '23', '24', '25', '26', '27',
                      '30', '31', '32', '33', '34', '35', '36', '37', '38', '39', '40', '41', '42', '43', '44', '45', '46', '47', '48', '49',
                      '50', '51', '52', '53', '54', '55', '56', '57', '58', '59', '60', '61', '62', '63', '64', '65', '66', '67', '68', '69',
                      '71', '72', '73', '74', '75', '76', '77', '78', '79', '80', '81', '82', '83', '84', '85', '86', '87', '88', '90', '91',
                      '92', '93', '94', '95', '98'}
    if prefix not in valid_prefixes:
        return False, f"Invalid EIN prefix {prefix}"
    return True, ""