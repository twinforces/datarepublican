#!/usr/bin/env python3

import sys
sys.path.append('.')
from extract_charities import parse_xml_file

# Enable verbose logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Set verbose mode in extract_charities
import extract_charities
extract_charities.verbose = True

# Set up logging for parse_990 module
import parse_990
parse_990.set_logger(None, None, True, set())

# Test the main extraction function
xml_file = 'test_xmls/CHAI.990.xml'
with open(xml_file, 'rb') as f:
    xml_content = f.read()

result = parse_xml_file(xml_content, 'CHAI.990.xml', '2023', 'test_xmls/CHAI.990.xml')

if result and len(result) >= 6:
    results, org_type, xml_name, officer_entries, contractors, political_contributions = result
    print('Extraction successful!')
    print('Results:', len(results))
    print('Org type:', org_type)
    print('Officers found:', len(officer_entries))
    print('Contractors found:', len(contractors))
    print('Political contributions found:', len(political_contributions))

    if contractors:
        print('Sample contractor:', contractors[0])
        print('Total contractor amount:', sum(c.get('amount', 0) for c in contractors))
        print('All contractors:')
        for i, c in enumerate(contractors):
            print(f'  {i+1}. {c.get("name", "Unknown")}: ${c.get("amount", 0)}')
else:
    print('Extraction failed or returned unexpected result')