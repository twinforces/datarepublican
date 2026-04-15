import re
from collections import defaultdict
import json

INPUT_TSV = 'distinct_grantee_names.tsv'
OUTPUT_JSON = 'name_rules.json'

MIDDLE_SEPARATORS = re.compile(r'\s+(?:OF|FOR|-|&|/|AND|,)\s+')

PLACE_INDICATORS = re.compile(
    r'(?:OF|FOR|-|&|/|AND|,)\s+(?:NEW YORK|LOS ANGELES|CHICAGO|HOUSTON|SAN FRANCISCO|WASHINGTON|'
    r'CALIFORNIA|TEXAS|FLORIDA|ILLINOIS|PENNSYLVANIA|OHIO|GEORGIA|NORTH CAROLINA|MICHIGAN|'
    r'NEW JERSEY|VIRGINIA|ARIZONA|MASSACHUSETTS|STATE|COUNTY|CITY|METRO|GREATER|AREA|REGION|'
    r'DIVISION|CHAPTER|USA|US|WORLD|INTERNATIONAL)', re.IGNORECASE
)

BLACKLIST_CORES = {
    'THE FOUNDATION', 'NATIONAL ASSOCIATION', 'THE CENTER', 'THE UNIVERSITY',
    'OUR LADY', 'COMMUNITY FOUNDATION',
    'THE FRIENDS', 'FOR THE LOVE', 'DO IT FOR THE LOVE',
    'FRIENDS', 'UNIVERSITY', 'CITY', 'BOYS', 'CENTER', 'TOWN',
    'FOUNDATION', 'CHURCH', 'ASSOCIATION', 'SOCIETY', 'VILLAGE', 'ALLIANCE',
    'AMERICAN SOCIETY',
    'AMERICAN FRIENDS', 'AMERICAN ASSOCIATION', 'INTERNATIONAL FELLOWSHIP',
    'CONFEDERATED TRIBES', 'THE ART', 'THE FRIENDS OF', 'THE NATIONAL CENTER', 
    'THE RESEARCH FOUNDATION',

    # Tight noise list - only true over-collapsing terms
    'TRUSTEES', 'REGENTS', 'BOARD', 'COLLEGE', 'STATE UNIVERSITY',
    'STATE COLLEGE', 'HISTORICAL SOCIETY', 'ALUMNI ASSOCIATION',
    'RECTOR', 'NATIONAL CONFERENCE', 'CITIZENS COMMITTEE',
    'RESEARCH FOUNDATION', 'YORK COLLEGE', 'MEDICAL COLLEGE',
    'REGENTS UNIVERSITY', 'COLLEGE FOUNDATION'
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

min_total_grants = 250

def clean_canonical(name: str) -> str:
    name = re.sub(r'^THE\s+', '', name, flags=re.IGNORECASE)
    name = name.replace('MAKE A WISH FOUNDATION', 'MAKE-A-WISH FOUNDATION')
    name = name.replace('YOUNG MENS CHRISTIAN ASSOCIATION', "YOUNG MEN'S CHRISTIAN ASSOCIATION")
    name = name.replace('B&GC', 'BOYS AND GIRLS CLUB')
    name = name.replace('BOYS GIRLS CLUBS', 'BOYS AND GIRLS CLUBS')
    name = re.sub(r',\s*$', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

print("Reading entire TSV once into memory...")
data = []
total_names_seen = 0

with open(INPUT_TSV, 'r', encoding='utf-8') as f:
    header = f.readline().strip().split('\t')
    name_idx = header.index('grantee_name')
    count_idx = header.index('grant_count') if 'grant_count' in header else None

    for line in f:
        fields = line.strip().split('\t')
        if len(fields) <= max(name_idx, count_idx or 0):
            continue
        raw_name = fields[name_idx].strip()
        if not raw_name:
            continue
        name_upper = raw_name.upper()
        try:
            grant_count = int(fields[count_idx]) if count_idx is not None else 1
        except (ValueError, IndexError):
            grant_count = 1
        data.append((name_upper, grant_count))
        total_names_seen += 1

print(f"Loaded {total_names_seen:,} names into memory.")

print("Discovering candidate cores from geo-qualified names...")
core_to_grants = defaultdict(int)
core_to_variants_narrow = defaultdict(set)

for name, grant_count in data:
    if PLACE_INDICATORS.search(name):
        parts = MIDDLE_SEPARATORS.split(name, maxsplit=1)
        if len(parts) >= 2:
            intro = parts[0].strip()
            if len(intro.split()) >= 2 and intro not in BLACKLIST_CORES:
                core_to_grants[intro] += grant_count
                core_to_variants_narrow[intro].add(name)

print(f"Narrow subset with geo qualifier: {len(core_to_grants):,}")

print("Building rules and merging variants (this may take a minute)...")
rules = defaultdict(list)

for core, total_grants in core_to_grants.items():
    if total_grants >= min_total_grants or core in WHITELIST_CORES:
        canonical = clean_canonical(core)
        
        regex = re.compile(r'\b' + re.escape(core) + r'\b', re.IGNORECASE)
        full_matches = set()
        for name, _ in data:
            if regex.search(name):
                full_matches.add(name)
        
        rules[canonical].extend(full_matches)

# Final sort: longer cores first to reduce collisions
final_rules = {}
for canonical in sorted(rules.keys(), key=len, reverse=True):
    final_rules[canonical] = sorted(set(rules[canonical]))

print(f"Final selected cores (min {min_total_grants} total grants): {len(final_rules):,}\n")

with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
    json.dump(final_rules, f, indent=2)

print(f"Saved {len(final_rules):,} rules to {OUTPUT_JSON}")

print("\nTop 15 rules by number of variants:")
for core in sorted(final_rules.keys(), key=lambda c: len(final_rules[c]), reverse=True)[:15]:
    print(f"'{core}' → {len(final_rules[core]):,} variants")