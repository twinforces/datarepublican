# parse_utils.py
import re
from lxml import etree  # type: ignore
from nameparser import HumanName
from io import BytesIO
import logging
from xpaths import NAMESPACES, XPATHS_990, XPATHS_990PF, GRANT_XPATHS, GRANT_EIN_XPATHS, GRANT_NAME_XPATHS, GRANT_AMOUNT_XPATHS, GRANT_FOREIGN_ADDRESS_XPATH, GRANT_COUNTRY_XPATH, GRANT_US_ADDRESS_XPATH
from xpaths_990ez import XPATHS_990EZ
from xpaths import SCHEDULE_C_XPATHS, SCHEDULE_C_AMOUNT_XPATHS, SCHEDULE_C_RECIPIENT_XPATHS, SCHEDULE_C_EIN_XPATHS
from xpath_utils import find_element
from constants import MONEY_PATTERN, FLOAT_PATTERN
from typing import Optional, List, Dict, Any
from models.address import Address
from models.political_contribution import PoliticalContribution

def parse_int_field(root, xpaths_dict, field, namespaces, xml_filename, context, xpath_cache, log_error, xpath_match_stats, verbose=False):
    elem = find_element(root, xpaths_dict[field], namespaces, xpath_cache=xpath_cache, field=field, form_type=context.get('form_type'), log_error=log_error, xpath_match_stats=xpath_match_stats)
    if elem is not None:
        text = elem.text.strip() if elem.text else ""
        if text.upper() == "RESTRICTED":
            # Skip RESTRICTED values silently
            return 0
        try:
            value = int(text)
            if verbose:
                log_error(f"Parsed int field {field}: {value} for EIN {context.get('filer_ein')} in {xml_filename}")
            return value
        except (ValueError, AttributeError):
            if log_error:
                log_error("Invalid int value for field %s: %s in %s", field, text, xml_filename)
    return 0

def parse_string_field(root, xpaths_dict, field, namespaces, xml_filename, context, xpath_cache, log_error, xpath_match_stats, verbose=False, default=None, return_element=False):
    elem = find_element(root, xpaths_dict[field], namespaces, xpath_cache=xpath_cache, field=field, form_type=context.get('form_type'), log_error=log_error, xpath_match_stats=xpath_match_stats)
    if elem is not None:
        value = elem.text.strip() if elem.text else default
        if verbose:
            log_error(f"Parsed string field {field}: {value} for EIN {context.get('filer_ein')} in {xml_filename}")
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
                    amount = int(parse_float_field(value_elem.text.strip()))
                    total += amount
                    if verbose:
                        log_error(f"Parsed schedule amount ${amount} for field in {xml_filename}")
                except (ValueError, AttributeError):
                    log_error(f"Invalid schedule value: {value_elem.text} in {xml_filename}")
    return total

def clean_name(name):
    return re.sub(r'[^a-zA-Z0-9\s]', '', name).strip().upper()

# Precompiled regex patterns for fast name parsing
CLEAN_NAME_PATTERN = re.compile(r'[^a-zA-Z0-9\s]')
SPLIT_NAME_PATTERN = re.compile(r'\s+')

def parse_name_fast(name):
    """
    Fast name parsing with fallback to HumanName for complex names.
    Handles most IRS officer names (simple "First Last" format) with regex,
    falls back to HumanName for complex names (>2 parts).
    """
    if not name:
        return 'Unknown', 'Unknown'

    cleaned = CLEAN_NAME_PATTERN.sub('', name).strip().upper()
    parts = SPLIT_NAME_PATTERN.split(cleaned)

    if len(parts) <= 2:
        # Fast path: simple names
        first = parts[0] if parts else 'Unknown'
        last = parts[-1] if len(parts) > 1 else 'Unknown'
        return first, last
    else:
        # Complex names: use HumanName for accuracy
        hn = HumanName(cleaned)
        first = hn.first or 'Unknown'
        last = hn.last or 'Unknown'
        return first, last

def parse_float_field(text):
    """Parse a float from text, handling commas, dollar signs, and other formatting"""
    if not text:
        return 0.0

    # Use regex to find the first valid number pattern
    match = FLOAT_PATTERN.search(str(text))
    if match:
        # Remove commas and dollar signs
        cleaned = match.group().replace(',', '').replace('$', '')
        try:
            return float(cleaned)
        except ValueError:
            pass

    return 0.0

def parse_grants(xml_content, xml_filename: str, filer_ein: str, filer_name: str, tax_year: int, known_eins, form_type: str, backfill_entries=None, seen_backfill_keys=None, log_error=None) -> List[Dict[str, Any]]:
    grants = []
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(xml_content, parser)
        root = tree.getroot()
        grant_xpaths = GRANT_XPATHS.get(form_type, [])
        for xpath in grant_xpaths:
            elements = xpath(root)
            for elem in elements:
                ein_elem = elem.xpath(GRANT_EIN_XPATHS[0].path, namespaces=NAMESPACES)
                name_elem = elem.xpath(GRANT_NAME_XPATHS[0].path, namespaces=NAMESPACES)
                amount_elem = elem.xpath(GRANT_AMOUNT_XPATHS[0].path, namespaces=NAMESPACES)
                grantee_name = (name_elem[0].text.strip() if name_elem and name_elem[0].text else "Unknown")

                # Check for US address first (domestic grant)
                us_address = elem.xpath(GRANT_US_ADDRESS_XPATH.path, namespaces=NAMESPACES)
                if us_address:
                    # Domestic grant
                    grant_ein = ein_elem[0].text.strip() if ein_elem else "Unknown"
                    is_foreign = False
                else:
                    # Check for foreign address
                    foreign_address = elem.xpath(GRANT_FOREIGN_ADDRESS_XPATH.path, namespaces=NAMESPACES)
                    if foreign_address:
                        # Foreign grant - use Address factory method
                        country_elem = elem.xpath(GRANT_COUNTRY_XPATH.path, namespaces=NAMESPACES)
                        country_code = country_elem[0].text.strip() if country_elem else None
                        if country_code:
                            # Create foreign address using factory method
                            foreign_addr = Address.create_foreign_address(
                                country_code=country_code,
                                address_type="grant"
                            )
                            # Use the EIN from the foreign address for grant processing
                            grant_ein = foreign_addr.ein  # This will be the country number
                            grantee_name = foreign_addr.name  # This will be the country name
                        else:
                            grant_ein = "999"
                            grantee_name = "Foreign_Unknown"
                        is_foreign = True
                    else:
                        # No address found, assume domestic
                        grant_ein = ein_elem[0].text.strip() if ein_elem else "Unknown"
                        is_foreign = False
                if grant_ein == "Unknown" and not is_foreign:
                    continue
                if amount_elem and amount_elem[0].text:
                    try:
                        grant_amt = int(parse_float_field(amount_elem[0].text.strip()))
                        if grant_amt > 0:
                            grants.append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'grant_ein': grant_ein,
                                'grant_amt': grant_amt,
                                'tax_year': tax_year
                            })
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        logging.error(f"Error parsing grants from {xml_filename}: {e}")
    return grants

def parse_contributions(xml_content, xml_filename: str, filer_ein: str, filer_name: str, tax_year: int, form_type: str) -> List[Dict[str, Any]]:
    contributions = []
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(BytesIO(xml_content), parser)
        root = tree.getroot()
        schedule_c_xpaths = SCHEDULE_C_XPATHS.get(form_type, [])
        for xpath in schedule_c_xpaths:
            elements = xpath(root)
            for elem in elements:
                amount_elem = elem.xpath(SCHEDULE_C_AMOUNT_XPATHS[0].path, namespaces=NAMESPACES)
                recipient_elem = elem.xpath(SCHEDULE_C_RECIPIENT_XPATHS[0].path, namespaces=NAMESPACES)
                ein_elem = elem.xpath(SCHEDULE_C_EIN_XPATHS[0].path, namespaces=NAMESPACES)
                recipient_name = recipient_elem[0].text.strip() if recipient_elem else "Unknown"
                recipient_ein = ein_elem[0].text.strip() if ein_elem else "Unknown"
                if recipient_ein == "Unknown":
                    continue
                if amount_elem and amount_elem[0].text:
                    try:
                        amount = int(parse_float_field(amount_elem[0].text.strip()))
                        if amount > 0:
                            contributions.append({
                                'filer_ein': filer_ein,
                                'filer_name': filer_name,
                                'recipient_ein': recipient_ein,
                                'amount': amount,
                                'tax_year': tax_year
                            })
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        logging.error(f"Error parsing contributions from {xml_filename}: {e}")
    return contributions

def split_zip_code(zip_code_str):
    """
    Split a ZIP code string into 5-digit zip_code and 4-digit zip4 components.

    Args:
        zip_code_str: String containing ZIP code (can be 5 or 9 digits)

    Returns:
        tuple: (zip_code, zip4) where zip_code is first 5 digits, zip4 is last 4 digits
               Both can be None if input is invalid
    """
    if not zip_code_str:
        return None, None

    # Remove any non-digit characters
    cleaned = re.sub(r'\D', '', str(zip_code_str))

    if len(cleaned) == 5:
        # Standard 5-digit ZIP
        return cleaned, None
    elif len(cleaned) == 9:
        # 9-digit ZIP code
        return cleaned[:5], cleaned[5:]
    elif len(cleaned) == 10 and cleaned[5] == '0':
        # Sometimes formatted as XXXXX-XXXX, handle the dash
        return cleaned[:5], cleaned[6:] if len(cleaned) > 6 else None
    else:
        # Invalid format, return as-is for zip_code (first 5 digits if possible)
        if len(cleaned) >= 5:
            return cleaned[:5], cleaned[5:9] if len(cleaned) >= 9 else None
        else:
            return cleaned, None


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


def extract_address(root, filename: str, filer_ein: str, quiet: bool = False, logger=None) -> Optional[Address]:
    """Extract address from XML - moved from xml_processor.py"""
    from models.address import Address
    from logging_utils import log_debug, log_info

    if not quiet and logger is not None and log_debug is not None:
        log_debug(logger, "DEBUG: Starting address extraction for EIN %s in %s", filer_ein, filename)

    # Extract filer name for address
    name_xpaths = COMMON_XPATHS["filer_name_xpaths"]

    filer_name = "Unknown"
    for xpath in name_xpaths:
        try:
            result = xpath(root)
            if result and result[0].text:
                filer_name = result[0].text.strip()
                if not quiet and logger is not None and log_debug is not None:
                    log_debug(logger, "DEBUG: Found filer name: '%s' for EIN %s", filer_name, filer_ein)
                break
        except:
            continue

    # Create Address object with basic fields and call prep_for_insert
    address = Address(
        ein=filer_ein,
        name=filer_name,
        address_type="filer"
    )

    # Extract address components directly from XML using proper XPaths
    # Get individual address fields from ReturnHeader/Filer
    address_line1_xpaths = COMMON_XPATHS["filer_address_line1"]

    for xpath in address_line1_xpaths:
        try:
            result = xpath(root)
            if result and result[0].text:
                address.address_line1 = result[0].text.strip()
                if not quiet and logger is not None and log_debug is not None:
                    log_debug(logger, "DEBUG: Found address_line1: '%s' for EIN %s using xpath %s", address.address_line1, filer_ein, xpath.path)
                break
        except:
            continue

    # Debug: if no address_line1 found, log the XML structure around address
    if not address.address_line1 and not quiet:
        # Try to find any USAddress elements and log their structure
        try:
            us_addresses = root.xpath(".//USAddress", namespaces={})
            if us_addresses and logger is not None and log_debug is not None:
                log_debug(logger, "DEBUG: Found %d USAddress elements for EIN %s", len(us_addresses), filer_ein)
                for i, addr_elem in enumerate(us_addresses[:1]):  # Just log first one
                    log_debug(logger, "DEBUG: USAddress %d content: %s...", i, ET.tostring(addr_elem, encoding='unicode')[:500])
            elif logger is not None and log_debug is not None:
                log_debug(logger, "DEBUG: No USAddress elements found for EIN %s", filer_ein)
        except Exception as e:
            if logger is not None and log_debug is not None:
                log_debug(logger, "DEBUG: Error checking USAddress structure for EIN %s: %s", filer_ein, str(e))

    # Address line 2
    address_line2_xpaths = COMMON_XPATHS["filer_address_line2"]

    for xpath in address_line2_xpaths:
        try:
            result = xpath(root)
            if result and result[0].text:
                address.address_line2 = result[0].text.strip()
                break
        except:
            continue

    # City
    city_xpaths = COMMON_XPATHS["filer_city"]

    for xpath in city_xpaths:
        try:
            result = xpath(root)
            if result and result[0].text:
                address.city = result[0].text.strip()
                break
        except:
            continue

    # State
    state_xpaths = COMMON_XPATHS["filer_state"]

    for xpath in state_xpaths:
        try:
            result = xpath(root)
            if result and result[0].text:
                address.state = result[0].text.strip()
                break
        except:
            continue

    # ZIP Code
    zip_xpaths = COMMON_XPATHS["filer_zip_code"]

    for xpath in zip_xpaths:
        try:
            result = xpath(root)
            if result and result[0].text:
                address.zip_code = result[0].text.strip()
                break
        except:
            continue

    if not quiet and logger is not None and log_debug is not None:
        log_debug(logger, "DEBUG: About to call prep_for_insert for EIN %s, address_line1='%s', city='%s', state='%s', zip='%s'", filer_ein, address.address_line1, address.city, address.state, address.zip_code)

    address.prep_for_insert()

    if not quiet and logger is not None and log_debug is not None:
        log_debug(logger, "DEBUG: After prep_for_insert for EIN %s: canonical_address='%s', po_box='%s', colocator='%s'", filer_ein, address.canonical_address, address.po_box, address.colocator)

    if address.canonical_address:
        if not quiet and logger is not None and log_info is not None:
            log_info(logger, "DEBUG: Created Address object - canonical_address='%s', po_box='%s', colocator='%s'", address.canonical_address, address.po_box, address.colocator)
        return address
    else:
        if not quiet and logger is not None and log_info is not None:
            log_info(logger, "DEBUG: No canonical_address created - canonical_address='%s', po_box='%s', colocator='%s'", address.canonical_address, address.po_box, address.colocator)
        return None


def parse_political_contribution_element(elem, filer_ein: str, tax_year: int, quiet: bool = False, logger=None) -> Optional[PoliticalContribution]:
    """Parse a single political contribution element from XML - moved from xml_processor.py"""
    from models.political_contribution import PoliticalContribution
    from logging_utils import log_error

    try:
        # Extract recipient name
        name_xpaths = COMMON_XPATHS["political_recipient_name"]

        recipient_name = None
        for xpath in name_xpaths:
            try:
                result = xpath(elem)
                if result and result[0].text:
                    recipient_name = result[0].text.strip()
                    break
            except:
                continue

        # Extract amount
        amount_xpaths = COMMON_XPATHS["political_amount"]

        amount = 0.0
        for xpath in amount_xpaths:
            try:
                result = xpath(elem)
                if result and result[0].text:
                    try:
                        amount = float(result[0].text.strip().replace(',', ''))
                        break
                    except ValueError:
                        continue
            except:
                continue

        # Extract EIN if available
        ein_xpaths = COMMON_XPATHS["political_ein"]

        recipient_ein = None
        for xpath in ein_xpaths:
            try:
                result = xpath(elem)
                if result and result[0].text:
                    raw_ein = result[0].text.strip()
                    if raw_ein and raw_ein.isdigit():
                        recipient_ein = f"{int(raw_ein):09d}"
                        break
            except:
                continue

        if recipient_name and amount > 0:
            contribution = PoliticalContribution(
                filer_ein=filer_ein,
                recipient=recipient_name,
                amount=amount,
                tax_year=tax_year
            )
            return contribution

    except Exception as e:
        if not quiet and log_error is not None and logger is not None:
            log_error(logger, "Error parsing political contribution element: %s", str(e))
        return None

    return None