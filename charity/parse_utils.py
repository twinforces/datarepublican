from xpath_utils import find_element

def parse_int_field(root, xpaths_dict, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None, verbose=False):
    """
    Parse an integer field from XML using a list of precompiled XPaths.
    
    Args:
        root: The root element to evaluate XPaths against.
        xpaths_dict: Dictionary containing precompiled XPaths for the field.
        field: The name of the field being parsed (e.g., "receipt").
        namespaces: Dictionary of namespace mappings.
        xml_filename: The name of the XML file being processed.
        context: Dictionary containing contextual information (e.g., EIN, tax year, form type).
        xpath_cache: Dictionary to cache XPath results.
        log_error: Logging function to use for error messages.
        xpath_match_stats: Dictionary to track XPath match statistics.
        verbose: Boolean to enable verbose logging.
    
    Returns:
        Integer value of the field, or 0 if not found.
    """
    form_type = context.get('form_type', 'Unknown')
    elem = find_element(root, xpaths_dict[field], namespaces, xpath_cache, field=field, form_type=form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
    if elem is None:
        if verbose:
            log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", 
                      field, context.get('filer_ein', 'Unknown'), xml_filename, [xpath.path for xpath in xpaths_dict[field]], 
                      ein=context.get('filer_ein', 'Unknown'))
        return 0
    value = parse_int(elem.text)
    if verbose:
        log_error("Parsed {} ${} for EIN {} in {}", 
                  field, value, context.get('filer_ein', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_string_field(root, xpaths_dict, field, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None, verbose=False, default="Unknown", return_element=False):
    """
    Parse a string field from XML using a list of precompiled XPaths.
    
    Args:
        root: The root element to evaluate XPaths against.
        xpaths_dict: Dictionary containing precompiled XPaths for the field.
        field: The name of the field being parsed (e.g., "filer_name").
        namespaces: Dictionary of namespace mappings.
        xml_filename: The name of the XML file being processed.
        context: Dictionary containing contextual information (e.g., EIN, tax year, form type).
        xpath_cache: Dictionary to cache XPath results.
        log_error: Logging function to use for error messages.
        xpath_match_stats: Dictionary to track XPath match statistics.
        verbose: Boolean to enable verbose logging.
        default: Default value to return if the field is not found.
        return_element: If True, return the lxml.etree.Element instead of the text content.
    
    Returns:
        If return_element is False, the string value of the field, or the default value if not found.
        If return_element is True, the lxml.etree.Element if found, or None if not found.
    """
    form_type = context.get('form_type', 'Unknown')
    elem = find_element(root, xpaths_dict[field], namespaces, xpath_cache, field=field, form_type=form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
    if elem is None:
        if verbose:
            log_error("Missing {} for EIN {} in {}. Tried XPaths: {}", 
                      field, context.get('filer_ein', 'Unknown'), xml_filename, [xpath.path for xpath in xpaths_dict[field]], 
                      ein=context.get('filer_ein', 'Unknown'))
        return default if not return_element else None
    if return_element:
        return elem
    value = elem.text.strip() if elem.text else default
    if verbose:
        log_error("Parsed {} {} for EIN {} in {}", 
                  field, value, context.get('filer_ein', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
    return value

def parse_total(root, xpaths_dict, elements_key, value_key, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None, verbose=False, debug_eins=None):
    """
    Parse a total value by summing up multiple elements (e.g., officer compensation, grants).
    
    Args:
        root: The root element to evaluate XPaths against.
        xpaths_dict: Dictionary containing precompiled XPaths for elements and values.
        elements_key: Key in xpaths_dict for the elements to iterate over.
        value_key: Key in xpaths_dict for the value to extract from each element.
        namespaces: Dictionary of namespace mappings.
        xml_filename: The name of the XML file being processed.
        context: Dictionary containing contextual information (e.g., EIN, tax year, form type).
        xpath_cache: Dictionary to cache XPath results.
        log_error: Logging function to use for error messages.
        xpath_match_stats: Dictionary to track XPath match statistics.
        verbose: Boolean to enable verbose logging.
        debug_eins: Set of EINs for extra debugging.
    
    Returns:
        Total integer value summed from all matching elements.
    """
    form_type = context.get('form_type', 'Unknown')
    total = 0
    debug_eins = debug_eins or set()
    elements = []
    for xpath in xpaths_dict[elements_key]:
        result = xpath(root)
        elements.extend(result)

    for elem in elements:
        value_elem = find_element(elem, xpaths_dict[value_key], namespaces, xpath_cache, field=value_key, form_type=form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
        if value_elem is not None:
            value = parse_int(value_elem.text)
            if verbose or context.get('filer_ein', 'Unknown') in debug_eins:
                log_error("Raw {} value: {} for EIN {} in {}", 
                          value_key, value_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, 
                          ein=context.get('filer_ein', 'Unknown'))
            total += value
    if verbose:
        log_error("Parsed total {} ${} for EIN {} in {}", 
                  elements_key, total, context.get('filer_ein', 'Unknown'), xml_filename, 
                  ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_schedule(root, xpaths_dict, schedule_key, sub_elements_key, value_key, namespaces, xml_filename, context, xpath_cache, log_error=None, xpath_match_stats=None, verbose=False, debug_eins=None):
    """
    Parse a total value by finding a schedule and summing sub-elements within it.
    
    Args:
        root: The root element to evaluate XPaths against.
        xpaths_dict: Dictionary containing precompiled XPaths for the schedule, sub-elements, and values.
        schedule_key: Key in xpaths_dict for the schedule to find (e.g., "grant_elements_f").
        sub_elements_key: Key in xpaths_dict for the sub-elements within the schedule (e.g., "grant_sub_elements_f").
        value_key: Key in xpaths_dict for the value to extract from each sub-element (e.g., "grant_value").
        namespaces: Dictionary of namespace mappings.
        xml_filename: The name of the XML file being processed.
        context: Dictionary containing contextual information (e.g., EIN, tax year, form type).
        xpath_cache: Dictionary to cache XPath results.
        log_error: Logging function to use for error messages.
        xpath_match_stats: Dictionary to track XPath match statistics.
        verbose: Boolean to enable verbose logging.
        debug_eins: Set of EINs for extra debugging.
    
    Returns:
        Total integer value summed from all matching sub-elements.
    """
    form_type = context.get('form_type', 'Unknown')
    total = 0
    debug_eins = debug_eins or set()
    schedule_field = f"schedule_{schedule_key.split('_')[1]}"  # e.g., "schedule_grant_elements_f" -> "schedule_f"

    # Find all schedule elements using parse_string_field with return_element=True
    schedule_elems = []
    for xpath in xpaths_dict[schedule_key]:
        elem = parse_string_field(root, {schedule_field: [xpath]}, schedule_field, namespaces, xml_filename, context, xpath_cache, log_error=log_error, xpath_match_stats=xpath_match_stats, verbose=verbose, default=None, return_element=True)
        if elem is not None:
            schedule_elems.append(elem)

    for schedule in schedule_elems:
        # Find sub-elements within the schedule
        sub_elements = []
        for sub_xpath in xpaths_dict[sub_elements_key]:
            result = sub_xpath(schedule)
            sub_elements.extend(result)

        for sub_elem in sub_elements:
            value_elem = find_element(sub_elem, xpaths_dict[value_key], namespaces, xpath_cache, field=value_key, form_type=form_type, log_error=log_error, xpath_match_stats=xpath_match_stats)
            if value_elem is not None:
                amount = parse_int(value_elem.text)
                if verbose or context.get('filer_ein', 'Unknown') in debug_eins:
                    log_error("Raw {} value: {} for EIN {} in {}", 
                              value_key, value_elem.text, context.get('filer_ein', 'Unknown'), xml_filename, 
                              ein=context.get('filer_ein', 'Unknown'))
                total += amount
                if context.get('filer_ein', 'Unknown') in debug_eins:
                    log_error("{} Grant: ${} in Schedule {} for EIN {}, File {}", 
                              'CHAI' if context.get('filer_ein', 'Unknown') == '271414646' else 'Amnesty', 
                              amount, schedule_key.split('_')[1].upper(), context.get('filer_ein', 'Unknown'), xml_filename, 
                              ein=context.get('filer_ein', 'Unknown'))
                elif amount > 5_000_000:
                    log_error("Found CashGrantAmt ${} in Schedule {} for EIN {}, File {}", 
                              amount, schedule_key.split('_')[1].upper(), context.get('filer_ein', 'Unknown'), xml_filename, 
                              ein=context.get('filer_ein', 'Unknown'))
    return total

def parse_int(value):
    """
    Parse a string value into an integer, handling potential errors.
    
    Args:
        value: The string value to parse.
    
    Returns:
        Integer value, or 0 if parsing fails.
    """
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError, AttributeError):
        return 0
    
import re

NON_ALPHA_PATTERN = re.compile(r'[^a-zA-Z\-]')
WHITESPACE_PATTERN = re.compile(r'\s+')
ORG_TYPE_PATTERN = re.compile(r'501\(c\)\((\d+)\)')
MONEY_PATTERN = re.compile(r'\$(\d+\.\d{2}|\d+)')

def clean_name(name):
    """
    Clean a name by removing all non-alphabetic characters except hyphens,
    allowing names like "Rodham-Clinton", and normalizing whitespace.
    
    Args:
        name (str): The raw name to clean.
    
    Returns:
        str: The cleaned name.
    """
    # Keep alphabetic characters and hyphens, remove everything else
    cleaned = NON_ALPHA_PATTERN.sub(' ', name)
    # Replace multiple spaces with a single space and strip
    cleaned = WHITESPACE_PATTERN.sub(' ', cleaned).strip()
    return cleaned