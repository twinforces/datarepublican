import re
from collections import defaultdict
import json

INPUT_TSV = 'distinct_grantee_names.tsv'

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

min_total_grants = 250   # tune this

print("Loading TSV with pure Python...")

core_to_grants = defaultdict(int)
core_to_variants = defaultdict(set)

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
        name = raw_name.upper()
        grant_count = int(fields[count_idx]) if count_idx is not None else 1

        # Narrow set check for core discovery
        if PLACE_INDICATORS.search(name):
            parts = MIDDLE_SEPARATORS.split(name, maxsplit=1)
            if len(parts) >= 2:
                intro = parts[0].strip()
                if len(intro.split()) >= 2 and intro not in BLACKLIST_CORES:
                    if len(intro.split()) >= 3 or intro in WHITELIST_CORES:
                        core_to_grants[intro] += grant_count
                        core_to_variants[intro].add(name)

print(f"Narrow subset {len(core_to_grants)} with geo qualifier processed.")

# Build rules
rules = {}
for core, total_grants in core_to_grants.items():
    if total_grants >= min_total_grants or core in WHITELIST_CORES:
        # Find all matches in full dataset using word boundary
        regex = re.compile(r'\b' + re.escape(core) + r'\b', re.IGNORECASE)
        full_matches = set()
        # Re-scan the file for full matches (fast enough for 2.2M lines)
        with open(INPUT_TSV, 'r', encoding='utf-8') as f:
            f.readline()  # skip header
            for line in f:
                fields = line.strip().split('\t')
                if len(fields) > name_idx:
                    name = fields[name_idx].strip().upper()
                    if regex.search(name):
                        full_matches.add(name)
        rules[core] = sorted(full_matches)

print(f"Final selected cores (min {min_total_grants} total grants): {len(rules):,}\n")

with open('name_rules.json', 'w') as f:
    json.dump(rules, f, indent=2)

print(f"Saved {len(rules):,} rules to name_rules.json")

print("\nTop 15 rules by number of variants:")
for core in sorted(rules.keys(), key=lambda c: len(rules[c]), reverse=True)[:15]:
    print(f"'{core}' → {len(rules[core]):,} variants")