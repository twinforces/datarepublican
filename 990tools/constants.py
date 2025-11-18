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
PO_BOX_REGEX = re.compile(r'P(?:.*?\bBOX\b\s+)([-\w\d]+)', re.IGNORECASE)
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
BATCH_SIZE = 100
CONSUMER_BATCH_SIZE = 100  # No reason not to make this larger only applies with backlog
MONITOR_INTERVAL_SECONDS = 30  # Change: Added constant to reduce psutil memory monitoring and QueueStatusDisplay updates to every 30 seconds

# Address processing constants
ADDRESS_BATCH_SIZE = 1000
ADDRESS_QUEUE_SIZE = 1000

# Database constants
DEFAULT_DB_PATH = "irs990.duckdb"
DEFAULT_ZIPS_DIR = "/Volumes/Data/irs_zips"
DEFAULT_OUT_DIR = "/Volumes/Data/tsvs"
DEFAULT_ANAL_DIR = "/Volumes/Data/atsvs"
DEFAULT_FINAL_DIR = "/Volumes/Data/final"

# Bulk insert batch size for database operations
BULK_INSERT_BATCH_SIZE = 100000

# WAL compaction timeout
WAL_COMPACTION_TIMEOUT = 30  # seconds

# Charity deduplication control
ENABLE_CHARITY_DEDUP_CHECK = False  # Controlled by command-line parameter

FULL_DB_PATH = f"{DEFAULT_FINAL_DIR}/${DEFAULT_DB_PATH}"

# Geocoding constants
GEOCODING_BATCH_SIZE = 1000  # Batch size for geocoding operations
GEOCODING_API_BATCH_SIZE = 10000  # Maximum addresses per census API call (as per census docs) - updated to comply with actual API limits
GEOCODING_FAST_WORKERS = 8  # Workers for fast local geocoding record creation
GEOCODING_API_WORKERS = 4   # Workers for slow census API calls

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