#!/usr/bin/env python3
"""
constants.py - Centralized constants for IRS 990 processing

This module contains all shared constants used across the IRS 990 processing system.
Consolidated from multiple files to provide a single source of truth.
"""

import re

# Valid US state and territory abbreviations
VALID_STATES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW', 'AA', 'AE', 'AP'
}

# State full name to abbreviation mapping
STATE_NAME_TO_ABBREV = {
    'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR', 'CALIFORNIA': 'CA',
    'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE', 'FLORIDA': 'FL', 'GEORGIA': 'GA',
    'HAWAII': 'HI', 'IDAHO': 'ID', 'ILLINOIS': 'IL', 'INDIANA': 'IN', 'IOWA': 'IA',
    'KANSAS': 'KS', 'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME', 'MARYLAND': 'MD',
    'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN', 'MISSISSIPPI': 'MS', 'MISSOURI': 'MO',
    'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV', 'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ',
    'NEW MEXICO': 'NM', 'NEW YORK': 'NY', 'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH',
    'OKLAHOMA': 'OK', 'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC',
    'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT', 'VERMONT': 'VT',
    'VIRGINIA': 'VA', 'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV', 'WISCONSIN': 'WI', 'WYOMING': 'WY',
    'DISTRICT OF COLUMBIA': 'DC', 'PUERTO RICO': 'PR', 'VIRGIN ISLANDS': 'VI', 'GUAM': 'GU',
    'AMERICAN SAMOA': 'AS', 'NORTHERN MARIANA ISLANDS': 'MP', 'FEDERATED STATES OF MICRONESIA': 'FM',
    'MARSHALL ISLANDS': 'MH', 'PALAU': 'PW', 'ARMED FORCES AMERICAS': 'AA', 'ARMED FORCES EUROPE': 'AE',
    'ARMED FORCES PACIFIC': 'AP'
}

# Regular expressions for parsing
MONEY_PATTERN = re.compile(r'[^\d.]+')
FLOAT_PATTERN = re.compile(r'-?\d*\.?\d+')
PO_BOX_REGEX = re.compile(
    r'(?i)\b(?:p\.?o\.?\s*b(?:ox)?|pobox|po box|post\s+office\s+box|box)\b'
    r'(?:\s*(?:#|no\.?|number)?\s*)?([-\w\d]+)',
    re.IGNORECASE  # explicit flag kept for clarity even though inline (?i) works
)
PO_BOX_NUMBER_REGEX = re.compile(r'[-\w\d]+')

# Organization type suffixes for different form types
ORG_TYPE_SUFFIXES = {
    '990': ['c3', 'c4', 'c5', 'c6', 'c7', 'c8', 'c9', 'c10', 'c11', 'c12', 'c13', 'c14', 'c15', 'c16', 'c17', 'c18', 'c19', 'c20', 'c21', 'c22', 'c23', 'c24', 'c25', 'c26', 'c27'],
    '990EZ': ['c3', 'c4', 'c5', 'c6'],
    '990PF': ['pf']
}

# Debug EINs for testing
DEBUG_EINS = {
    '811001051',  # Test charity
    '956051973',  # Test charity
    '841388632',  # Test charity
    '814435171',  # Test charity
    '344412114',  # Test charity
    '742473501',  # Test charity
    '611040784',  # Test charity
    '811913093',  # Test charity
    '352274351',  # Test charity
    '834271563',  # Test charity
    '420806064',  # Test charity
    '016174485',  # Test charity
    '362197334',  # Test charity
    '570467970',  # Test charity
    '581497053',  # Test charity
    '810452293',  # Test charity
    '112889824',  # Test charity
    '812409587',  # Test charity
    '311430662',  # Test charity
    '770533427',  # Test charity
    '562056835',  # Test charity
    '561460874',  # Test charity
    '381779460',  # Test charity
    '222810656',  # Test charity
    '346608208',  # Test charity
    '593505120',  # Test charity
    '474407194',  # Test charity
    '462737357',  # Test charity
    '232108521',  # Test charity
    '760776639',  # Test charity
    '756032949',  # Test charity
}

# Processing constants
CURRENT_PROCESSING_VERSION = 3
OPTIMIZE_THRESHOLD = 1000000
ENABLE_AUTO_CHECKPOINTS = False

# Threading constants
MAX_WORKERS = 16
QUEUE_SIZE = 1000
BATCH_SIZE = 1000
CONSUMER_BATCH_SIZE = 10000  # Default for XML/address pipelines
GEOCODE_CONSUMER_BATCH_SIZE = 500  # Smaller flushes — each PDC can fan out to Grants/Addresses
GEOCODE_CHECKPOINT_INTERVAL = 25000  # FORCE CHECKPOINT during drain; ~25k uncommitted worst-case on crash
MONITOR_INTERVAL_SECONDS = 30  # Change: Added constant to reduce psutil memory monitoring and QueueStatusDisplay updates to every 30 seconds
CONSUMER_MAX_IDLE_SECONDS = 300.0
CONSUMER_POLL_TIMEOUT = 5.0

# Address processing constants
ADDRESS_BATCH_SIZE = 1000
ADDRESS_QUEUE_SIZE = 1000
FEED_BATCH_SIZE = 500

# Database constants
DEFAULT_DB_PATH = "irs990.duckdb"
DEFAULT_ZIPS_DIR = "/Volumes/Data/irs_zips"
DEFAULT_OUT_DIR = "/Volumes/Data/tsvs"
DEFAULT_ANAL_DIR = "/Volumes/Data/atsvs"
DEFAULT_FINAL_DIR = "/Volumes/Data/final"

# Bulk insert batch size for database operations
BULK_INSERT_BATCH_SIZE = 100000

# Bulk update batch size for database operations (smaller for updates)
BULK_UPDATE_BATCH_SIZE = 10

# WAL compaction timeout
WAL_COMPACTION_TIMEOUT = 30  # seconds

# Charity deduplication control
ENABLE_CHARITY_DEDUP_CHECK = False  # Controlled by command-line parameter

FULL_DB_PATH = f"{DEFAULT_FINAL_DIR}/${DEFAULT_DB_PATH}"

# Geocoding constants
CENSUS_API_BATCH_SIZE = 10_000  # Census batch API limit per HTTP call (per geocoding.geo.census.gov docs)
GEOCODING_API_WORKERS = 4       # Parallel census stage workers (each does one CENSUS_API_BATCH_SIZE call)
GEOCODING_FEED_BATCH_SIZE = CENSUS_API_BATCH_SIZE  # One DB pull = one census HTTP request
GEOCODING_IN_FLIGHT_CAP = 10_000  # Match API throughput — 100k caused 4h+ feed stall while Grok drained
GEOCODING_GROK_WORKERS = 12
GEOCODING_GROK_BATCH_SIZE = 25       # addresses per realtime/batch prompt
GEOCODING_GROK_EXPORT_ROWS = 5_000   # grok_pending rows per xAI batch job
GEOCODING_GROK_POLL_INTERVAL = 60      # seconds between batch status checks
GROK_FAILURES_EXPORT_FILE = "grok_failures_for_patterns.tsv.gz"  # written when grok_pending drained
GROK_GEOCODE_MODEL_DEFAULT = "grok-4-latest"  # json_schema verified; override via GROK_GEOCODE_MODEL

# Grok geocode failure taxonomy — stored as geocoding_status grok:<CODE> and archive colocator.
# Collected for pattern-rule mining: cluster by code + canonical_address to derive preprocess rules.
# POBOX omitted (preprocess already handles); FOREIGN omitted (country filter catches most).
GROK_FAILURE_CODES = frozenset({
    "NOTA",    # Not an address (org name, "see statement", narrative text)
    "VAGUE",   # Too incomplete (city/state only, missing street number/name)
    "AMBIG",   # Multiple plausible US matches — cannot disambiguate
    "REDACT",  # Intentionally redacted / privacy placeholder
    "UNKN",    # Unclassified (foreign slip-through, API/parse miss, or Grok unsure)
})


def grok_failure_status(code: str | None) -> str:
    """Normalize Grok failure_code → geocoding_status like grok:NOTA."""
    normalized = (code or "UNKN").upper().strip()
    if normalized not in GROK_FAILURE_CODES:
        normalized = "UNKN"
    return f"grok:{normalized}"


def is_grok_failure_status(status: str | None) -> bool:
    return bool(status and status.startswith("grok:"))


def is_grok_failure_colocator(colocator: str | None) -> bool:
    return bool(colocator and colocator.startswith("grok:"))
GEOCODE_MAPS_CO_WORKERS = 20           # 1M plan: 20 req/sec
GEOCODE_MAPS_CO_MIN_DELAY = 1.0        # per-thread delay → ~20 concurrent req/sec at full worker load
GEOCODING_PHOTON_WORKERS = 12          # public photon.komoot.io (throttled)
GEOCODING_PHOTON_MIN_DELAY = 0.4
GEOCODING_PHOTON_SELF_HOSTED_WORKERS = 32
GEOCODING_PHOTON_SELF_HOSTED_MIN_DELAY = 0  # own VPS — no politeness throttle
GEOCODING_BATCH_SIZE = GEOCODING_FEED_BATCH_SIZE  # alias for feed/get_work_batch
GEOCODING_API_BATCH_SIZE = 10000  # unused legacy; census stage uses CENSUS_API_BATCH_SIZE
GEOCODING_FAST_WORKERS = 8  # Workers for fast local geocoding record creation
GEOCODING_MAX_UPDATES_PER_BATCH = 10000  # Maximum estimated updates per batch to prevent commit hangs

# Address abbreviation expansions
STREET_FIXES = {
    'St': 'Street', 'Saint': 'Street', 'Ave': 'Avenue', 'Av': 'Avenue',
    'Blvd': 'Boulevard', 'Dr': 'Drive', 'Ln': 'Lane', 'Rd': 'Road',
    'Cir': 'Circle', 'Ct': 'Court', 'Pl': 'Place', 'Ter': 'Terrace',
    'Pkwy': 'Parkway', 'Hwy': 'Highway', 'Sq': 'Square',
    'Cres': 'Crescent', 'Plz': 'Plaza', 'Xing': 'Crossing', 'Way': 'Way',
    'Aly': 'Alley', 'Loop': 'Loop', 'Rdg': 'Ridge', 'Trl': 'Trail'
}
UNIT_FIXES = {
    'Ste': 'Suite', 'Apt': 'Apartment', 'Unit': 'Unit', 'Bldg': 'Building',
    'Fl': 'Floor', 'Rm': 'Room', 'Dept': 'Department', 'Ofc': 'Office',
    'SPC': 'Space', 'LOT': 'Lot', 'TRLR': 'Trailer', 'BSMT': 'Basement'
}

# Keywords for parsing Schedule O expenses
TRAVEL_KEYWORDS = [
    "TRAVEL", "BANK", "MEAL", "MEALS", "ENTERTAINMENT", "OFFICE", "TECHNICAL", "INTERNET",
    "LUNCHEON", "BRUNCH", "BREAKFAST", "LUNCH", "DINNER", "PARTY", "BANQUET", "RECEPTION",
    "CATERING", "FOOD", "BEVERAGE", "REFRESHMENT", "SNACK", "SUPPER", "PICNIC", "BARBECUE",
    "HOTEL", "LODGING", "ACCOMMODATION", "TRANSPORTATION", "MILEAGE", "TAXI", "UBER", "LYFT",
    "AIRFARE", "FLIGHT", "BUS", "TRAIN", "SUBWAY", "PARKING", "TOLL", "GAS", "FUEL",
    "RENTAL", "CAR", "VEHICLE", "AUTO", "POSTAGE", "SHIPPING", "MAIL", "DELIVERY",
    "SUPPLIES", "EQUIPMENT", "SOFTWARE", "HARDWARE", "MAINTENANCE", "REPAIR", "SERVICE",
    "UTILITIES", "ELECTRIC", "WATER", "GAS", "PHONE", "TELEPHONE", "CELL", "MOBILE",
    "INSURANCE", "LEGAL", "ACCOUNTING", "AUDIT", "TAX", "FEE", "CHARGE", "COST",
    "EXPENSE", "SUBSCRIPTION", "MEMBERSHIP", "DUES", "DONATION", "CONTRIBUTION"
]

CONFERENCE_KEYWORDS = [
    "CONFERENCE", "MEETING", "CONVENTION", "SEMINAR", "WORKSHOP", "SYMPOSIUM", "FORUM",
    "RETREAT", "SUMMIT", "GATHERING", "ASSEMBLY", "SESSION", "TRAINING", "EDUCATION",
    "CONTINUING", "PROFESSIONAL", "DEVELOPMENT", "CERTIFICATION", "CERTIFICATE",
    "WEBINAR", "VIRTUAL", "ONLINE", "DISTANCE", "REMOTE", "CLASS", "COURSE", "PROGRAM",
    "LECTURE", "PRESENTATION", "PANEL", "DISCUSSION", "ROUND TABLE", "WORKGROUP",
    "COMMITTEE", "BOARD", "COUNCIL", "ASSOCIATION", "ORGANIZATION", "GROUP", "TEAM",
    "COLLABORATION", "COORDINATION", "PLANNING", "STRATEGY", "EXECUTIVE", "LEADERSHIP",
    "MANAGEMENT", "ADMINISTRATION", "OPERATIONS", "COORDINATION", "OUTREACH", "ADVOCACY"
]