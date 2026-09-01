#!/usr/bin/env python3
import censusgeocode as cg

# Test the API response structure
api_record = {
    'street': '7272 Greenville Ave',
    'city': 'Dallas',
    'state': 'TX',
    'zip': '75231'
}

print("API record:", api_record)

try:
    api_results = cg.addressbatch([api_record])
    print("API results:", api_results)
    if api_results:
        result = api_results[0]
        print("First result:", result)
        print("Keys:", list(result.keys()))
        print("address:", result.get('address'))
        print("city:", result.get('city'))
        print("state:", result.get('state'))
        print("zip:", result.get('zip'))
        print("match:", result.get('match'))
        print("matchtype:", result.get('matchtype'))
except Exception as e:
    print("Error:", e)