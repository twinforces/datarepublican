import json
from lxml import etree
from collections import defaultdict

# Global dictionary to track XPath match statistics
xpath_match_stats = defaultdict(int)

# Global cache for precompiled XPath objects
XPATH_CACHE = {}

def precompile_xpaths(xpaths, namespaces):
    """Precompile a list of XPaths into lxml.etree.XPath objects."""
    cache_key = tuple(xpaths) + (tuple(sorted(namespaces.items())) if namespaces else ())
    if cache_key in XPATH_CACHE:
        return XPATH_CACHE[cache_key]
    
    compiled_xpaths = []
    for xpath in xpaths:
        try:
            compiled_xpaths.append(etree.XPath(xpath, namespaces=namespaces))
        except etree.XPathSyntaxError as e:
            # Log the error and continue with the next XPath
            continue
    XPATH_CACHE[cache_key] = compiled_xpaths
    return compiled_xpaths

def find_element(root, xpaths, namespaces, xpath_cache=None, field=None):
    # Initialize the XPath cache if not provided
    if xpath_cache is None:
        xpath_cache = {}

    # Create a unique key for the cache based on root, xpaths, and namespaces
    root_id = id(root)
    namespaces_key = tuple(sorted(namespaces.items())) if namespaces else None
    xpaths_key = tuple(xpaths)

    # Compile XPaths if not already cached
    compiled_xpaths = precompile_xpaths(xpaths, namespaces)

    for xpath, compiled_xpath in zip(xpaths, compiled_xpaths):
        # Check the cache first
        cache_key = (root_id, xpath, namespaces_key)
        if cache_key in xpath_cache:
            elem = xpath_cache[cache_key]
            if elem is not None:
                if field:
                    xpath_match_stats[f"{field}:{xpath}"] += 1
                return elem
            continue

        # Evaluate the XPath if not in cache
        try:
            elem = compiled_xpath(root)
            if elem:
                xpath_cache[cache_key] = elem[0]
                if field:
                    xpath_match_stats[f"{field}:{xpath}"] += 1
                return elem[0]
        except etree.XPathEvalError as e:
            xml_snippet = etree.tostring(root, encoding='unicode', method='xml')[:2000]
            error_msg = f"XPath error for {xpath}: {e}. XML snippet: {xml_snippet}"
            non_ns_xpath = xpath.replace('irs:', '').replace('{http://www.irs.gov/efile}', '')
            try:
                non_ns_compiled = etree.XPath(non_ns_xpath, namespaces=None)
                elem = non_ns_compiled(root)
                if elem:
                    xpath_cache[cache_key] = elem[0]
                    if field:
                        xpath_match_stats[f"{field}:{xpath}"] += 1
                    return elem[0]
            except etree.XPathEvalError as e:
                error_msg += f"\nNon-namespaced XPath error for {non_ns_xpath}: {e}. XML snippet: {xml_snippet}"

        # Cache the None result to avoid re-evaluating
        xpath_cache[cache_key] = None

    return None

def save_xpath_stats():
    # Save the XPath match statistics to a file
    with open("xpath_stats.json", "w") as f:
        json.dump(dict(xpath_match_stats), f, indent=4)