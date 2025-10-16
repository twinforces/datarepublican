from lxml import etree

def precompile_xpaths(xpaths, namespaces):
    """
    Precompile a list of XPath strings into etree.XPath objects.
    If an XPath is already compiled, use it as-is.
    """
    compiled_xpaths = []
    for xpath in xpaths:
        if isinstance(xpath, etree.XPath):
            compiled_xpaths.append(xpath)
        else:
            compiled_xpaths.append(etree.XPath(xpath, namespaces=namespaces))
    return compiled_xpaths

def find_element(root, xpaths, namespaces, xpath_cache=None, field=None, form_type=None, log_error=None, xpath_match_stats=None):
    """
    Find an element using a list of XPaths, with caching to avoid redundant evaluations.

    Args:
        root: The root element to evaluate XPaths against.
        xpaths: List of XPaths (either strings or precompiled etree.XPath objects).
        namespaces: Dictionary of namespace mappings.
        xpath_cache: Dictionary to cache XPath results (optional).
        field: Field name for tracking match statistics (optional).
        form_type: Form type (e.g., "990", "990EZ", "990PF") for stats tracking (optional).
        log_error: Logging function to use for error messages (optional).
        xpath_match_stats: Dictionary to track XPath match statistics (optional).

    Returns:
        The first matching element, or None if no match is found.
    """
    # Initialize the XPath cache if not provided
    if xpath_cache is None:
        xpath_cache = {}

    # Use a dummy log_error if not provided
    if log_error is None:
        def log_error(msg, *args, **kwargs):
            pass  # Silent dummy logger

    # Create a unique key for the cache based on root, xpath, and namespaces
    root_id = id(root)
    namespaces_key = tuple(sorted(namespaces.items())) if namespaces else None

    compiled_xpaths = precompile_xpaths(xpaths, namespaces)

    for xpath in compiled_xpaths:
        # Check the cache first
        cache_key = (root_id, xpath, namespaces_key)
        if cache_key in xpath_cache:
            elem = xpath_cache[cache_key]
            if elem is not None:
                return elem
            continue

        # Evaluate the XPath if not in cache
        try:
            elem = xpath(root)
            if elem:
                xpath_cache[cache_key] = elem[0]
                # Only update stats, don't log individual matches
                if field and form_type and xpath_match_stats is not None:
                    stats_key = f"{form_type}:{field}:{xpath}"
                    xpath_match_stats[stats_key] += 1
                return elem[0]
        except etree.XPathEvalError as e:
            xml_snippet = etree.tostring(root, encoding='unicode', method='xml')[:2000]
            if log_error:
                log_error("XPath error for %s: %s. XML snippet: %s", xpath, e, xml_snippet)
            non_ns_xpath = xpath.replace('irs:', '').replace('{http://www.irs.gov/efile}', '')
            try:
                elem = root.xpath(non_ns_xpath, namespaces=None)
                if elem:
                    xpath_cache[cache_key] = elem[0]
                    if field and form_type and xpath_match_stats is not None:
                        stats_key = f"{form_type}:{field}:{xpath}"
                        xpath_match_stats[stats_key] += 1
                    return elem[0]
            except etree.XPathEvalError as e:
                if log_error:
                    log_error("Non-namespaced XPath error for %s: %s. XML snippet: %s", non_ns_xpath, e, xml_snippet)

        # Cache the None result to avoid re-evaluating
        xpath_cache[cache_key] = None

    return None