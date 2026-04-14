import pandas as pd
import re
import json
from collections import defaultdict
from pathlib import Path

INPUT_TSV = 'distinct_grantee_names.tsv'
RULES_FILE = 'name_rules.json'

BLACKLIST_CORES = {
    'THE FOUNDATION', 'NATIONAL ASSOCIATION', 'THE CENTER', 'THE UNIVERSITY',
    'OUR LADY', 'COMMUNITY FOUNDATION',
    'THE FRIENDS', 'FOR THE LOVE', 'DO IT FOR THE LOVE',
    'FRIENDS', 'UNIVERSITY', 'CITY', 'BOYS', 'CENTER', 'TOWN',
    'FOUNDATION', 'CHURCH', 'ASSOCIATION', 'SOCIETY', 'VILLAGE', 'ALLIANCE',
    'AMERICAN SOCIETY',
    'AMERICAN FRIENDS', 'AMERICAN ASSOCIATION', 'INTERNATIONAL FELLOWSHIP',
    'CONFEDERATED TRIBES', 'THE ART', 'THE FRIENDS OF', 'THE NATIONAL CENTER', 
    'THE RESEARCH FOUNDATION'
}

WHITELIST_CORES = {
    'SALVATION ARMY', 'THE SALVATION ARMY', 'UNITED WAY', 
    'AMERICAN RED CROSS', 'THE AMERICAN RED CROSS',
    'BOY SCOUTS', 'GIRL SCOUTS', 'PLANNED PARENTHOOD',
    'CATHOLIC CHARITIES', 'HUMANE SOCIETY', 'FOOD BANK',
    'BIG BROTHERS BIG SISTERS', 'MEALS ON WHEELS',
    'RONALD MCDONALD HOUSE CHARITIES', 'FIRST BAPTIST CHURCH',
    'FIRST UNITED METHODIST CHURCH', 'FIRST PRESBYTERIAN CHURCH',
    'JEWISH FEDERATION', 'ROTARY CLUB', 'JUNIOR ACHIEVEMENT', 
    'FAMILY PROMISE', 'MAKE-A-WISH FOUNDATION', 'GOODWILL INDUSTRIES',
    'CHABAD LUBAVITCH', 'JEWISH FAMILY', 'SECOND HARVEST FOOD BANK',
    'ASSISTANCE LEAGUE', 'GIRLS ON THE RUN',
    'FIRST TEE', 'BRAIN INJURY ASSOCIATION', 'MENTAL HEALTH AMERICA'
}

print("Loading names...")
df = pd.read_csv(INPUT_TSV, sep='\t')
df['name'] = df['grantee_name'].str.strip().str.upper()
names = df['name'].dropna().tolist()
print(f"Loaded {len(names):,} distinct names\n")

# Geo qualifier detection
GEO_QUALIFIER = re.compile(
    r'(?:OF|FOR|-|&|/|AND|,)\s+(?:NEW YORK|LOS ANGELES|CHICAGO|HOUSTON|SAN FRANCISCO|WASHINGTON|'
    r'CALIFORNIA|TEXAS|FLORIDA|ILLINOIS|PENNSYLVANIA|OHIO|GEORGIA|NORTH CAROLINA|MICHIGAN|'
    r'NEW JERSEY|VIRGINIA|ARIZONA|MASSACHUSETTS|STATE|COUNTY|CITY|METRO|GREATER|AREA|REGION|'
    r'DIVISION|CHAPTER|USA|US|WORLD|INTERNATIONAL)', re.IGNORECASE
)

narrow_set = [name for name in names if GEO_QUALIFIER.search(name)]
print(f"Narrow subset with geo qualifier: {len(narrow_set):,} names\n")

# Extract cores from narrow set only
core_to_names = defaultdict(list)
for name in narrow_set:
    parts = re.split(r'\s+(?:OF|FOR|-|&|/|AND|,)\s+', name, maxsplit=1)
    if len(parts) < 2:
        continue
    intro = parts[0].strip()
    if len(intro.split()) >= 2 and intro not in BLACKLIST_CORES:
        if len(intro.split()) >= 3 or intro in WHITELIST_CORES:
            core_to_names[intro].append(name)

min_frequency = 25
rules = {}
for core, name_list in core_to_names.items():
    if len(name_list) >= min_frequency or core in WHITELIST_CORES:
        rules[core] = sorted(set(name_list))  # exact original names

print(f"Generated {len(rules):,} rules\n")

with open(RULES_FILE, 'w') as f:
    json.dump(rules, f, indent=2)

print(f"Saved rules to {RULES_FILE}")
print("Top 10 rules (core → number of exact names):")
for i, (core, names_list) in enumerate(sorted(rules.items(), key=lambda x: -len(x[1]))[:10], 1):
    print(f"  {i:2d}. '{core}' → {len(names_list):,} exact names")