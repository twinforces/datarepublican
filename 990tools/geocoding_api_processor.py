#!/usr/bin/env python3
"""
geocoding_api_processor.py - Fully corrected and cleaned version
Fixed: broken/massively duplicated fallback logic in _process_work_item
Preserved: all pattern matching, owners handling, colocator updates, batching
"""

import time
import json
import re
import os
import sys
import openai
import random
import queue
import threading
import gc
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from openai import OpenAI

try:
    import censusgeocode as cg
    import requests
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    from opencage.geocoder import OpenCageGeocode
except ImportError:
    cg = None
    Nominatim = None
    OpenCageGeocode = None

try:
    import tqdm
except ImportError:
    tqdm = None

from base_processor import BaseProcessor, WorkUnit
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models.geocoding import Geocoding
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config
from constants import GEOCODING_BATCH_SIZE, GEOCODING_MAX_UPDATES_PER_BATCH, CONSUMER_BATCH_SIZE, OPTIMIZE_THRESHOLD
from pending_database_context import PendingDatabaseContext


# === API Configuration ===
# Centralized configuration for enabling/disabling geocoding APIs and their priorities
API_CONFIG = {
    # Census APIs
    'ENABLE_CENSUS_RAW': True,        # Tier 1: Raw census batch geocoding
    'ENABLE_CENSUS_PATTERNS': True,   # Tier 2: Census with pattern preprocessing

    # AI/ML APIs
    'ENABLE_GROK': True,             # Tier 3: Grok AI geocoding (disabled due to expense)

    # Open-source geocoding services
    'ENABLE_PHOTON': True,            # Photon (Komoot) geocoding
    'ENABLE_NOMINATIM': False,         # OpenStreetMap Nominatim
    'ENABLE_LIBRESTREET': False,       # LibreStreet geocoding

    # Commercial geocoding services
    'ENABLE_OPENCAGE': False,          # OpenCage geocoding
    'ENABLE_GOOGLE_MAPS': False,       # Google Maps geocoding
    'ENABLE_NAME_SEARCH': False,       # Google search-based geocoding
}

# API priority order for fallback processing (higher priority = tried first)
# Note: Grok is only used in batch processing (Tier 3), not individual fallbacks
API_PRIORITY = [
    'Photon',
    'LibreStreet',
    'Nominatim',
    'OpenCage',
    'Google_Maps',
    'Name_Search',
]


class GeocodingAPIProcessor(BaseProcessor):
    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_BATCH_SIZE):
        super().__init__(db_ops)
        self.batch_size = batch_size
        self.geocoding_patterns = self._load_geocoding_patterns()
        # API call counters
        self.census_calls = 0
        self.photon_calls = 0
        self.librestreet_calls = 0
        self.nominatim_calls = 0
        self.opencage_calls = 0
        self.google_maps_calls = 0
        self.name_search_calls = 0
        self.grok_calls = 0
        # Queue for parallel Grok processing
        self.api_queue = queue.Queue(maxsize=10000)
        self.grok_workers = []
        if cg is None and not global_config.is_quiet():
            log_warning("censusgeocode library not available. Install with: pip install censusgeocode")

    def setup_address_counts(self):
        log_info("Setup: Populating address_count if needed")
        result = self.db_ops.execute_query("SELECT COUNT(DISTINCT address_count) FROM Geocoding")
        if result and result.fetchone()[0] > 1:
            log_info("address_count already populated")
            return

        conn = self.db_ops._get_thread_local_connection()
        try:
            conn.execute("BEGIN TRANSACTION")
            conn.execute("""
                UPDATE Geocoding SET address_count = (
                    SELECT COUNT(*) FROM Addresses WHERE Addresses.geocoding_id = Geocoding.geocoding_id
                )
            """)
            conn.commit()
            log_info("address_count populated")
        except Exception as e:
            conn.rollback()
            log_error(f"Failed to populate address_count: {e}", exc_info=True)
            raise

    def _load_geocoding_patterns(self) -> List[Dict[str, Any]]:
        path = os.path.join(os.path.dirname(__file__), 'geocoding_patterns.json')
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            patterns = data.get('patterns', [])
            patterns.sort(key=lambda x: x.get('priority', 999))
            log_info(f"Loaded {len(patterns)} geocoding patterns")
            return patterns
        except Exception as e:
            log_warning(f"Failed to load geocoding_patterns.json: {e}")
            return []

    def _strip_co_from_address(self, address: str) -> str:
        """Strip leading C/O patterns from address strings.

        Handles variations like:
        - "C/O Tull Charitable Foundation 191, Atlanta, Ga, 30303" -> "191, Atlanta, Ga, 30303"
        - "C/O Foundation 3 Park Avenue 24Th, New York, Ny, 10016" -> "3 Park Avenue 24Th, New York, Ny, 10016"
        - Case insensitive matching
        """
        if not address:
            log_debug(f"C/O strip: Empty address, returning as-is")
            return address

        # Check if it's a C/O address
        if not re.match(r'(?i)^c/?o', address):
            log_debug(f"C/O strip: No C/O prefix found in '{address}', returning as-is")
            return address

        log_debug(f"C/O strip: Processing C/O address '{address}'")

        # Special logging for Tull address
        if "Tull Charitable Foundation" in address:
            log_info(f"DEBUG_TULL: C/O stripping initiated for '{address}'")

        # Try comma-separated entity first (e.g., "C/O The Organization, Branchport...")
        comma_pattern = r'^(?:c/?o,?)\s*[^,\d]*,\s*'
        if re.search(comma_pattern, address, re.IGNORECASE):
            result = re.sub(comma_pattern, '', address, flags=re.IGNORECASE).strip()
            log_debug(f"C/O strip: Used comma pattern, result: '{result}'")
            if "Tull Charitable Foundation" in address:
                log_info(f"DEBUG_TULL: C/O stripping - comma pattern matched, result: '{result}'")
            return result

        # Otherwise, entity until first digit (e.g., "C/O Foundation 3...")
        digit_pattern = r'^(?:c/?o,?)\s*.*?(?=\d)'
        if re.search(digit_pattern, address, re.IGNORECASE):
            result = re.sub(digit_pattern, '', address, flags=re.IGNORECASE).strip()
            log_debug(f"C/O strip: Used digit pattern, result: '{result}'")
            if "Tull Charitable Foundation" in address:
                log_info(f"DEBUG_TULL: C/O stripping - digit pattern matched, result: '{result}'")
            return result

        log_debug(f"C/O strip: No pattern matched for '{address}', returning original")
        if "Tull Charitable Foundation" in address:
            log_info(f"DEBUG_TULL: C/O stripping - no pattern matched, returning original: '{address}'")
        return address

    def _extract_entity_from_co_address(self, address: str) -> str:
        """Extract entity name from C/O addresses for search-oriented queries.

        Returns the entity name without C/O prefix, cleaned for natural language queries.
        """
        if not address or not re.match(r'(?i)^c/?o', address):
            return ""

        # Try comma-separated entity first (e.g., "C/O The Organization, Branchport...")
        comma_match = re.match(r'^(?:c/?o,?)\s*([^,\d]*),\s*(.*)', address, flags=re.IGNORECASE)
        if comma_match:
            entity = comma_match.group(1).strip()
            if entity:
                return entity

        # Otherwise, entity until first digit (e.g., "C/O Foundation 3 Park Avenue...")
        digit_match = re.match(r'^(?:c/?o,?)\s*(.*?)(?=\d)', address, flags=re.IGNORECASE)
        if digit_match:
            entity = digit_match.group(1).strip()
            if entity:
                return entity

        # Handle cases with no clear delimiter (e.g., "C/O THE ORGANIZATION")
        # Extract everything after C/O as the entity name
        no_delimiter_match = re.match(r'^(?:c/?o,?)\s*(.+)', address, flags=re.IGNORECASE)
        if no_delimiter_match:
            entity = no_delimiter_match.group(1).strip()
            # Clean up common trailing punctuation
            entity = re.sub(r'[,.]$', '', entity).strip()
            if entity:
                return entity

        return ""

    def _parse_co_address(self, address: str, api_type: str, zip_code: str = "") -> str:
        """Parse C/O addresses differently based on API type.

        For Census: Strip C/O completely to get clean street addresses
        For Grok: Extract entity name and format as "<entity>, <zip code>"
        """
        if not address:
            return address

        if api_type.lower() == 'census':
            return self._strip_co_from_address(address)
        elif api_type.lower() == 'grok':
            # Check if it's a C/O address
            if not re.match(r'(?i)^c/?o', address):
                return address

            # Try comma-separated entity first (e.g., "C/O The Organization, Branchport...")
            comma_match = re.match(r'^(?:c/?o,?)\s*([^,\d]*),\s*(.*)', address, flags=re.IGNORECASE)
            if comma_match:
                entity = comma_match.group(1).strip()
                rest = comma_match.group(2).strip()
                zip_match = re.search(r'\b(\d{5})\b', rest)
                zip_part = zip_match.group(1) if zip_match else zip_code
                if zip_part:
                    return f"{entity}, {zip_part}"

            # Otherwise, entity until first digit (e.g., "C/O Foundation 3 Park Avenue...")
            digit_match = re.match(r'^(?:c/?o,?)\s*(.*?)(?=\d)(.*)', address, flags=re.IGNORECASE)
            if digit_match:
                entity = digit_match.group(1).strip()
                rest = digit_match.group(2).strip()
                zip_match = re.search(r'\b(\d{5})\b', rest)
                zip_part = zip_match.group(1) if zip_match else zip_code
                if zip_part:
                    return f"{entity}, {zip_part}"

            # Handle cases with no clear delimiter (e.g., "C/O THE ORGANIZATION")
            # Extract everything after C/O as the entity name
            no_delimiter_match = re.match(r'^(?:c/?o,?)\s*(.+)', address, flags=re.IGNORECASE)
            if no_delimiter_match:
                entity = no_delimiter_match.group(1).strip()
                # Clean up common trailing punctuation
                entity = re.sub(r'[,.]$', '', entity).strip()
                if entity and zip_code:
                    return f"{entity}, {zip_code}"

            # Final fallback - return original address if no zip available
            return address
        else:
            return address

    def _build_api_fallbacks(self, geocoding_id: str = None, canonical_address: str = None) -> List[Tuple[str, callable]]:
        """Build the list of enabled API fallbacks in priority order."""
        fallbacks = []

        # Map API names to their methods and config flags
        # Note: Grok is only used in batch processing, not individual fallbacks
        api_methods = {
            'Photon': (self._geocode_with_photon, 'ENABLE_PHOTON'),
            'LibreStreet': (self._geocode_with_librestreet, 'ENABLE_LIBRESTREET'),
            'Nominatim': (self._geocode_with_nominatim, 'ENABLE_NOMINATIM'),
            'OpenCage': (self._geocode_with_opencage, 'ENABLE_OPENCAGE'),
            'Google_Maps': (self._geocode_with_google_maps, 'ENABLE_GOOGLE_MAPS'),
            'Name_Search': (lambda addr, norm: self._geocode_with_name_search(geocoding_id, canonical_address, norm), 'ENABLE_NAME_SEARCH'),
        }

        # Build fallbacks list in priority order, only including enabled APIs
        for api_name in API_PRIORITY:
            if api_name in api_methods:
                method, config_flag = api_methods[api_name]
                if API_CONFIG.get(config_flag, False):
                    fallbacks.append((api_name, method))

        # Special handling for Google Maps final fallback (environment variable override)
        if os.getenv('ENABLE_GOOGLE_MAPS_FALLBACK', 'false').lower() == 'true' and API_CONFIG.get('ENABLE_GOOGLE_MAPS', False):
            fallbacks.append(("Google_Maps_Final", self._geocode_with_google_maps))

        return fallbacks

    def _check_geocoding_patterns(self, canonical_address: str, zip_code: str = "", pattern_type: str = 'all') -> Optional[Dict[str, Any]]:
        safe_names = ['privacy_addresses', 'various_addresses', 'incomplete_addresses', 'no_street_number']
        dangerous_names = ['po_box_addresses', 'co_addresses', 'mall_complexes', 'major_institutions', 'registered_agents_2025']

        patterns = [p for p in self.geocoding_patterns if pattern_type == 'all' or p.get('name') in (safe_names if pattern_type == 'safe' else dangerous_names)]

        for pattern in patterns:
            if 'patterns' in pattern:
                for sub in pattern['patterns']:
                    if re.search(sub.get('regex', ''), canonical_address, re.IGNORECASE):
                        status = 'owners' if sub.get('colocator') else sub.get('status', 'pending')
                        return {**sub, 'action': sub.get('action', 'match'), 'status': status}
            else:
                regex = pattern.get('regex', '')
                if not re.search(regex, canonical_address, re.IGNORECASE):
                    continue
                colocator = pattern.get('colocator', '')
                if '{zip}' in colocator and zip_code:
                    colocator = colocator.replace('{zip}', zip_code)
                if '{city}' in colocator:
                    m = re.search(r', ([^,]+), ([A-Z]{2}),', canonical_address)
                    if m: colocator = colocator.replace('{city}', m.group(1).strip())
                if '{state}' in colocator:
                    m = re.search(r', ([A-Z]{2}),', canonical_address)
                    if m: colocator = colocator.replace('{state}', m.group(1).strip())
                if '{entity}' in colocator:
                    m = re.match(r'c/o\s+(.+?)[,]', canonical_address, re.IGNORECASE)
                    if m: colocator = colocator.replace('{entity}', m.group(1).strip())
                if '{location}' in colocator:
                    m = re.search(r'(.+?)\s+(mall|center|plaza)', canonical_address, re.IGNORECASE)
                    if m: colocator = colocator.replace('{location}', m.group(1).strip())
                status = 'owners' if colocator else pattern.get('status', 'pending')
                return {
                    'colocator': colocator,
                    'status': status,
                    'action': pattern.get('action', 'match'),
                    'regex': regex,
                    'fields': pattern.get('fields')
                }
        return None

    def _update_owner_colocators(self, context: PendingDatabaseContext, geocoding_id: str, colocator: str):
        result = self.db_ops.execute_query(
            "SELECT address_type, owner_id FROM Addresses WHERE geocoding_id = ?", (geocoding_id,)
        )
        updates = {}
        for row in result.fetchall():
            if not row[1]: continue
            addr_type, owner_id = row[0], row[1]
            mapping = {
                'charity': ('Charities', 'charity_id'),
                'grant': ('Grants', 'grant_id'),
                'contractor': ('Contractors', 'contractor_id'),
                'politicalcontribution': ('PoliticalContributions', 'political_id'),
                'political': ('PoliticalContributions', 'political_id')
            }
            if addr_type in mapping:
                table, col = mapping[addr_type]
                updates.setdefault(table, []).append({col: str(owner_id), 'colocator': colocator})
        for table, items in updates.items():
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={'table': table, 'updates': items, 'id_column': list(items[0].keys())[0]}
            ))

    def _apply_successful_geocode(self, context: PendingDatabaseContext, geocoding_id: str,
                                  attempt_count: int, now: str, lat: float, lon: float,
                                  status: str, result: Dict, matched_address: str = None):
        rlat, rlon = round(lat, 4), round(lon, 4)
        colocator = f"LL:{rlat}:{rlon}"

        update = {
            'geocoding_id': geocoding_id,
            'last_attempt': now,
            'attempt_count': attempt_count + 1,
            'latitude': rlat,
            'longitude': rlon,
            'geocoding_status': status,
            'matched_address': matched_address or result.get('formatted_address', 'unknown')
        }
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [update], 'id_column': 'geocoding_id'}
        ))
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Addresses', 'set_clause': 'colocator = ?', 'where_clause': 'geocoding_id = ?', 'params': (colocator, geocoding_id)}
        ))
        self._update_owner_colocators(context, geocoding_id, colocator)

    def _process_owners_work_item(self, work_item: Dict[str, Any]) -> PendingDatabaseContext:
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()
        gid = work_item['geocoding_id']
        attempt = int(work_item['attempt_count'])

        result = self.db_ops.execute_query("SELECT colocator FROM Addresses WHERE geocoding_id = ? LIMIT 1", (gid,))
        row = result.fetchone()
        colocator = row[0] if row else None

        update = {
            'geocoding_id': gid,
            'last_attempt': now,
            'attempt_count': attempt + 1,
            'latitude': None,
            'longitude': None,
            'geocoding_status': 'Match:Pattern'
        }
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [update], 'id_column': 'geocoding_id'}
        ))
        if colocator:
            self._update_owner_colocators(context, gid, colocator)

        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': work_item.get('address_count', 0)}
        ))
        return context

    def _is_valid_street_address(self, address: str) -> bool:
        """Check if an address looks like a valid street address that geocoding APIs can handle.

        A valid street address should:
        - Start with a number (street number)
        - Contain street name elements (street, avenue, road, etc.)
        - Not be just a city/state/zip combination
        """
        if not address:
            return False

        # Must start with a digit (street number)
        if not re.match(r'^\d', address.strip()):
            return False

        # Must contain at least one street-type word
        street_indicators = ['street', 'st', 'avenue', 'ave', 'road', 'rd', 'drive', 'dr', 'lane', 'ln',
                           'way', 'place', 'pl', 'court', 'ct', 'circle', 'cir', 'boulevard', 'blvd',
                           'parkway', 'pkwy', 'highway', 'hwy', 'route', 'rte']
        has_street_indicator = any(indicator in address.lower() for indicator in street_indicators)

        # Allow some flexibility - if it has a number and comma (suggesting city/state), it might still be valid
        # But reject if it's just "number, city, state, zip" pattern
        if ',' in address:
            parts = [p.strip() for p in address.split(',')]
            if len(parts) >= 3 and re.match(r'^\d+$', parts[0]) and not has_street_indicator:
                # Looks like "123, City, State, Zip" - probably not a valid street address
                return False

        return has_street_indicator or len(address.split()) > 2  # At least some complexity

    def _handle_pattern_match(self, context: PendingDatabaseContext, work_item: Dict, pattern: Dict, now: str):
        gid = work_item['geocoding_id']
        attempt = int(work_item['attempt_count'])
        canonical = work_item['canonical_address']
        normalized = work_item['normalized_address']

        if pattern['action'] == 'strip':
            regex = pattern.get('regex', '')
            if 'fields' in pattern:
                try:
                    data = json.loads(normalized) if isinstance(normalized, str) else normalized or {}
                    modified = False
                    for field in pattern['fields']:
                        if field in data and data[field]:
                            cleaned = re.sub(regex, '', data[field], flags=re.IGNORECASE).strip()
                            if cleaned != data[field]:
                                data[field] = cleaned
                                modified = True

                    if modified:
                        # Check if the cleaned address is valid for geocoding
                        street_valid = self._is_valid_street_address(data.get('street', ''))
                        if street_valid:
                            update = {'geocoding_id': gid, 'normalized_address': json.dumps(data), 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'pending'}
                        else:
                            # Invalid street address after stripping - don't set to pending, let it continue to APIs
                            update = {'geocoding_id': gid, 'normalized_address': json.dumps(data), 'last_attempt': now, 'attempt_count': attempt + 1}
                    else:
                        update = {'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}
                except:
                    update = {'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}
            else:
                cleaned = re.sub(regex, '', canonical, flags=re.IGNORECASE)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                if self._check_geocoding_patterns(cleaned, pattern_type='all') and self._check_geocoding_patterns(cleaned, pattern_type='all').get('action') == 'strip':
                    update = {'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}
                else:
                    # Check if the cleaned canonical address is valid for geocoding
                    if self._is_valid_street_address(cleaned):
                        update = {'geocoding_id': gid, 'canonical_address': cleaned, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'pending'}
                        # Also update normalized street to the cleaned canonical for geocoding consistency
                        if isinstance(work_item['normalized_address'], dict) and 'street' in work_item['normalized_address']:
                            work_item['normalized_address']['street'] = cleaned
                            update['normalized_address'] = json.dumps(work_item['normalized_address'])
                    else:
                        # Invalid street address after stripping - don't set to pending, let it continue to APIs
                        update = {'geocoding_id': gid, 'canonical_address': cleaned, 'last_attempt': now, 'attempt_count': attempt + 1}
                        # Also update normalized street to the cleaned canonical for geocoding consistency
                        if isinstance(work_item['normalized_address'], dict) and 'street' in work_item['normalized_address']:
                            work_item['normalized_address']['street'] = cleaned
                            update['normalized_address'] = json.dumps(work_item['normalized_address'])
        else:
            update = {
                'geocoding_id': gid,
                'last_attempt': now,
                'attempt_count': attempt + 1,
                'latitude': None,
                'longitude': None,
                'geocoding_status': pattern.get('status', 'owners')
            }
            if pattern.get('colocator'):
                context.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={'table': 'Addresses', 'set_clause': 'colocator = ?', 'where_clause': 'geocoding_id = ?', 'params': (pattern['colocator'], gid)}
                ))
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [update], 'id_column': 'geocoding_id'}
        ))
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': work_item.get('address_count', 0)}
        ))
    def _process_work_item(self, work_item: Dict[str, Any]) -> PendingDatabaseContext:
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()
        gid = work_item['geocoding_id']
        normalized = work_item['normalized_address']
        canonical = work_item['canonical_address']
        attempt = int(work_item['attempt_count'])

        if work_item['geocoding_status'] == 'owners':
            return self._process_owners_work_item(work_item)

        zip_code = ""
        if isinstance(normalized, str):
            try:
                zip_code = json.loads(normalized).get('zip', '')
            except:
                pass

        pattern = self._check_geocoding_patterns(canonical, zip_code, 'all')
        if pattern:
            self._handle_pattern_match(context, work_item, pattern, now)
            return context

        if cg is None:
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={'table': 'Geocoding', 'updates': [{'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'failed'}], 'id_column': 'geocoding_id'}
            ))
            context.addOperationToDatabase(DatabaseOperation(operation_type=DatabaseOperationType.PROGRESS_UPDATE, data={'count': work_item.get('address_count', 0)}))
            return context

        try:
            record = {}
            if isinstance(normalized, str):
                try: record = json.loads(normalized)
                except: pass
            elif isinstance(normalized, dict):
                record = normalized.copy()
            record.pop('id', None)

            results = cg.addressbatch([record])
            result = results[0] if results else {}
            if result.get('match') and result.get('lat') and result.get('lon'):
                lat, lon = float(result['lat']), float(result['lon'])
                status = 'Match' if result.get('matchtype', 'Exact') == 'Exact' else f"Match:{result.get('matchtype')}"
                self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, result)
                context.addOperationToDatabase(DatabaseOperation(operation_type=DatabaseOperationType.PROGRESS_UPDATE, data={'count': work_item.get('address_count', 0)}))
                return context
        except Exception as e:
            log_warning(f"Census geocode failed for {gid}: {e}")

        # Strip C/O from canonical address before fallback processing
        stripped_canonical = self._strip_co_from_address(canonical)
        if stripped_canonical != canonical:
            log_debug(f"Fallback processing for {gid}: Stripped C/O from canonical '{canonical}' -> '{stripped_canonical}'")
            canonical = stripped_canonical

        # Strip C/O from normalized address for fallback processing
        normalized_for_fallback = normalized
        if isinstance(normalized, str):
            try:
                norm_dict = json.loads(normalized)
                if 'street' in norm_dict and norm_dict['street']:
                    original_street = norm_dict['street']
                    norm_dict['street'] = self._strip_co_from_address(norm_dict['street'])
                    if original_street != norm_dict['street']:
                        log_debug(f"Fallback processing for {gid}: Stripped C/O from normalized street '{original_street}' -> '{norm_dict['street']}'")
                        normalized_for_fallback = json.dumps(norm_dict)
            except:
                pass
        elif isinstance(normalized, dict) and 'street' in normalized and normalized['street']:
            original_street = normalized['street']
            normalized_copy = normalized.copy()
            normalized_copy['street'] = self._strip_co_from_address(normalized_copy['street'])
            if original_street != normalized_copy['street']:
                log_debug(f"Fallback processing for {gid}: Stripped C/O from normalized street '{original_street}' -> '{normalized_copy['street']}'")
                normalized_for_fallback = normalized_copy

        fallbacks = self._build_api_fallbacks(gid, canonical)
        log_debug(f"Fallback processing for {gid}: canonical='{canonical}', normalized='{normalized_for_fallback}'")

        for name, func in fallbacks:
            try:
                log_debug(f"Trying fallback {name} for {gid}")
                res = func(canonical, normalized_for_fallback)
                if res and res.get('match'):
                    lat, lon = float(res['lat']), float(res['lon'])
                    status = f"Match:{name.replace('_', ' ')}"
                    log_debug(f"Fallback {name} succeeded for {gid}: {status}")
                    self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, res, res.get('formatted_address'))
                    context.addOperationToDatabase(DatabaseOperation(operation_type=DatabaseOperationType.PROGRESS_UPDATE, data={'count': work_item.get('address_count', 0)}))
                    return context
            except Exception as e:
                log_debug(f"Fallback {name} failed for {gid}: {e}")

        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [{'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}], 'id_column': 'geocoding_id'}
        ))
        context.addOperationToDatabase(DatabaseOperation(operation_type=DatabaseOperationType.PROGRESS_UPDATE, data={'count': work_item.get('address_count', 0)}))
        return context

    
    def _geocode_with_google_maps(self, address_str, normalized_address=None):
        self.google_maps_calls += 1
        key = os.getenv('GOOGLE_MAPS_API_KEY')
        if not key: return None

        # Parse normalized_address for structured data
        normalized_data = {}
        if isinstance(normalized_address, str):
            try:
                normalized_data = json.loads(normalized_address)
            except:
                pass
        elif isinstance(normalized_address, dict):
            normalized_data = normalized_address.copy()

        # Construct address string from normalized data if available
        if normalized_data:
            street = normalized_data.get('street', '')
            city = normalized_data.get('city', '')
            state = normalized_data.get('state', '')
            zip_code = normalized_data.get('zip', '')
            constructed_address = f"{street}, {city}, {state} {zip_code}".strip(', ')
            if constructed_address:
                address_str = constructed_address

        try:
            url = f"https://maps.googleapis.com/maps/api/geocode/json?address={requests.utils.quote(address_str)}&key={key}"
            r = requests.get(url, timeout=10)
            data = r.json()
            if data.get('status') == 'OK' and data.get('results'):
                loc = data['results'][0]['geometry']['location']
                return {'match': True, 'lat': loc['lat'], 'lon': loc['lng'], 'formatted_address': data['results'][0]['formatted_address']}
        except: pass
        return None

    def _geocode_with_nominatim(self, address_str, normalized_address=None):
        self.nominatim_calls += 1
        if Nominatim is None: return None

        # Parse normalized_address for structured data
        normalized_data = {}
        if isinstance(normalized_address, str):
            try:
                normalized_data = json.loads(normalized_address)
            except:
                pass
        elif isinstance(normalized_address, dict):
            normalized_data = normalized_address.copy()

        # Construct address string from normalized data if available
        if normalized_data:
            street = normalized_data.get('street', '')
            city = normalized_data.get('city', '')
            state = normalized_data.get('state', '')
            zip_code = normalized_data.get('zip', '')
            constructed_address = f"{street}, {city}, {state} {zip_code}".strip(', ')
            if constructed_address:
                address_str = constructed_address

        try:
            geolocator = Nominatim(user_agent="irs990-geocoder")
            time.sleep(0.1)
            location = geolocator.geocode(address_str, timeout=10)
            if location:
                return {'match': True, 'lat': location.latitude, 'lon': location.longitude, 'formatted_address': location.address}
        except: pass
        return None

    def _geocode_with_photon(self, address_str, normalized_address=None):
        self.photon_calls += 1

        log_debug(f"Photon: Original address_str='{address_str}', normalized_address='{normalized_address}'")

        # Parse normalized_address for structured data
        normalized_data = {}
        if isinstance(normalized_address, str):
            try:
                normalized_data = json.loads(normalized_address)
            except:
                pass
        elif isinstance(normalized_address, dict):
            normalized_data = normalized_address.copy()

        # Construct address string from normalized data if available
        if normalized_data:
            street = normalized_data.get('street', '')
            city = normalized_data.get('city', '')
            state = normalized_data.get('state', '')
            zip_code = normalized_data.get('zip', '')
            constructed_address = f"{street}, {city}, {state} {zip_code}".strip(', ')
            if constructed_address:
                log_debug(f"Photon: Using constructed address '{constructed_address}' instead of '{address_str}'")
                address_str = constructed_address

        try:
            url = f"https://photon.komoot.io/api/?q={requests.utils.quote(address_str)}&limit=1"
            r = requests.get(url, timeout=10)
            data = r.json()
            if data.get('features'):
                coords = data['features'][0]['geometry']['coordinates']
                props = data['features'][0]['properties']
                addr = props.get('name') or address_str
                return {'match': True, 'lat': coords[1], 'lon': coords[0], 'formatted_address': addr}
        except: pass
        return None

    def _geocode_with_opencage(self, address_str, normalized_address=None):
        self.opencage_calls += 1
        if OpenCageGeocode is None: return None
        key = os.getenv('OPENCAGE_API_KEY')
        if not key: return None

        # Parse normalized_address for structured data
        normalized_data = {}
        if isinstance(normalized_address, str):
            try:
                normalized_data = json.loads(normalized_address)
            except:
                pass
        elif isinstance(normalized_address, dict):
            normalized_data = normalized_address.copy()

        try:
            geocoder = OpenCageGeocode(key)
            # Use structured query if normalized data is available
            if normalized_data:
                query = {
                    'street': normalized_data.get('street', ''),
                    'city': normalized_data.get('city', ''),
                    'state': normalized_data.get('state', ''),
                    'postalcode': normalized_data.get('zip', '')
                }
                # Remove empty fields
                query = {k: v for k, v in query.items() if v}
                results = geocoder.geocode(query=query) if query else geocoder.geocode(address_str)
            else:
                results = geocoder.geocode(address_str)
            if results and len(results) > 0:
                r = results[0]
                return {'match': True, 'lat': r['geometry']['lat'], 'lon': r['geometry']['lng'], 'formatted_address': r.get('formatted', address_str)}
        except: pass
        return None
    
    def _geocode_with_librestreet(self, address_str, normalized_address=None):
        self.librestreet_calls += 1

        # Parse normalized_address for structured data
        normalized_data = {}
        if isinstance(normalized_address, str):
            try:
                normalized_data = json.loads(normalized_address)
            except:
                pass
        elif isinstance(normalized_address, dict):
            normalized_data = normalized_address.copy()

        # Construct address string from normalized data if available
        if normalized_data:
            street = normalized_data.get('street', '')
            city = normalized_data.get('city', '')
            state = normalized_data.get('state', '')
            zip_code = normalized_data.get('zip', '')
            constructed_address = f"{street}, {city}, {state} {zip_code}".strip(', ')
            if constructed_address:
                address_str = constructed_address

        try:
            url = f"https://librestreet.org/search.php?q={requests.utils.quote(address_str)}&format=json"
            r = requests.get(url, timeout=10)
            data = r.json()
            if data:
                loc = data[0]
                return {
                    'match': True,
                    'lat': float(loc['lat']),
                    'lon': float(loc['lon']),
                    'formatted_address': loc['display_name']
                }
        except: pass
        return None

    def _geocode_with_name_search(self, geocoding_id: str, canonical_address: str, normalized_address=None):
        self.name_search_calls += 1
        try:
            result = self.db_ops.execute_query("SELECT name, zip_code FROM Addresses WHERE geocoding_id = ? LIMIT 1", (geocoding_id,))
            row = result.fetchone()
            if not row or not row[0] or not row[1]: return None
            org, zip_code = row[0], row[1]
            query = f'"{org}" {zip_code} address'
            key = os.getenv('GOOGLE_SEARCH_API_KEY')
            cx = os.getenv('GOOGLE_SEARCH_ENGINE_ID')
            if not key or not cx: return None
            time.sleep(1)
            url = f"https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q={requests.utils.quote(query)}"
            r = requests.get(url, timeout=10)
            data = r.json()
            if 'items' in data:
                for item in data['items'][:3]:
                    text = item.get('title', '') + ' ' + item.get('snippet', '')
                    addr_match = re.search(r'\d+ [A-Za-z0-9\s,.-]+, [A-Za-z\s]+, [A-Z]{2} \d{5}', text)
                    if addr_match and str(zip_code) in addr_match.group(0):
                        found = addr_match.group(0)
                        gm = self._geocode_with_google_maps(found)
                        if gm: return gm
        except: pass
        return None

    def _start_grok_workers(self):
        """Start 10 parallel Grok worker threads."""
        print("###DEBUG### START: Starting 10 Grok worker threads")
        for i in range(10):
            t = threading.Thread(target=self._grok_worker, args=(i,), name=f'GrokWorker-{i+1}')
            t.daemon = True
            self.grok_workers.append(t)
            t.start()

    def _grok_worker(self, worker_id: int):
        """Worker thread that processes batches from api_queue and puts results to result_queue."""
        print(f"###DEBUG### WORKER {worker_id}: Started")
        while True:
            batch = self.api_queue.get()
            if batch is None:  # Sentinel
                self.api_queue.task_done()
                # Put worker sentinel to result_queue
                self.result_queue.put(WorkUnit.sentinel(1000 + worker_id))
                print(f"###DEBUG### WORKER {worker_id}: Received sentinel, putting sentinel and exiting")
                break

            print(f"###DEBUG### WORKER {worker_id}: Processing batch of {len(batch)} items")
            try:
                context = self._process_grok_batch(batch)
                print(f"###DEBUG### WORKER {worker_id}: Putting result context to result_queue")
                self.result_queue.put(WorkUnit.result(context))
            except Exception as e:
                print(f"###DEBUG### WORKER {worker_id}: Error processing batch: {e}")
                # For failed batch, mark all as No_Match
                context = PendingDatabaseContext()
                now = datetime.now().isoformat()
                for item in batch:
                    gid = item['geocoding_id']
                    attempt = int(item['attempt_count'])
                    print(f"###DEBUG### WORKER_ERROR: Setting {gid} to No_Match due to batch processing error, canonical={item.get('canonical_address', '')}")
                    context.addOperationToDatabase(DatabaseOperation(
                        operation_type=DatabaseOperationType.GENERIC_UPDATE,
                        data={
                            'table': 'Geocoding',
                            'updates': [{
                                'geocoding_id': gid,
                                'last_attempt': now,
                                'attempt_count': attempt + 1,
                                'geocoding_status': 'No_Match'
                            }],
                            'id_column': 'geocoding_id'
                        }
                    ))
                    context.addOperationToDatabase(DatabaseOperation(
                        operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                        data={'count': item.get('address_count', 0)}
                    ))
                print(f"###DEBUG### WORKER {worker_id}: Putting error result context to result_queue")
                self.result_queue.put(WorkUnit.result(context))
            finally:
                self.api_queue.task_done()

        print(f"###DEBUG### WORKER {worker_id}: Completed")
        
    def _build_grok_prompt(self, addresses: List[Dict[str, Any]]) -> str:
        numbered = []
        for i, item in enumerate(addresses, 1):
            canonical = item.get("canonical_address", "").strip()
            normalized = item.get("normalized_address", {})
            
            # Safely extract normalized dict
            if isinstance(normalized, str):
                try:
                    normalized = json.loads(normalized)
                except:
                    normalized = {}
            street = (normalized.get("street") or "").strip()
            city   = (normalized.get("city") or "").strip()
            state  = (normalized.get("state") or "").strip()
            zipc   = (normalized.get("zip") or "").strip()

            # Build the best possible query
            if canonical.lower().startswith("c/o") or street.lower().startswith("c/o"):
                # Strategy: give Grok BOTH the raw line AND the parsed parts so it can reason
                context = f"Raw: {canonical} | Parsed → Street: {street} | City: {city} | State: {state} | ZIP: {zipc}"
                query = f"Geocode this care-of foundation/trust/bank address (common in IRS 990 forms): \"{context}\". Return the exact street address of the entity or office that handles the foundation, not a P.O. box."
            else:
                context = f"Raw: {canonical} | Street: {street} | City: {city} | State: {state} | ZIP: {zipc}"
                query = f"Geocode this address from IRS 990 forms (often truncated or abbreviated): \"{context}\"."

            # Common abbreviation fixes Grok now handles extremely well when hinted
            hint = (
                "Common patterns to expand: "
                "“Ma” → Madison Avenue, "
                "“Fith” → Fifth, "
                "“Hami” → Hamilton, "
                "“Po” → Point Drive, "
                "“Finl Plz” → Financial Plaza, "
                "“S National A” → South National Avenue, "
                "“2 Piedmont” → 3565 Piedmont Road NE (Atlanta), "
                "“52 Spring” → 52 Spring St NW (Concord NC), "
                "“33 S Street” → 33 S State St (Chicago)"
            )

            full_query = f"{i}. {query}\n   {hint}"
            numbered.append(full_query)

        prompt = f"""You are an expert geocoder for messy U.S. nonprofit addresses extracted from IRS Form 990 filings.
            Your only job is to return coordinates for the physical location these foundations, banks, or trustees actually use — never a P.O. box, never null when a real office exists.

            Return ONLY a JSON array with exactly {len(addresses)} objects, no markdown, no extra text:

            [
            {{"id": 1, "lat": 40.7128, "long": -74.0060, "matched_address": "Full resolved street address, City, ST ZIP"}},
            {{"id": 2, "lat": null, "long": null, "matched_address": null}},
            ...
            ]

            Rules you MUST follow:
            - If the address contains "C/O" or "c/o", ignore the prefix and geocode the entity/office that follows.
            - Expand obvious abbreviations (Ma → Madison Ave, Fith → Fifth, etc.).
            - When you recognize a known foundation (Cannon Foundation, Sapelo, Henry Luce, Skadden, Robins, Dobbs, etc.), use its real headquarters or trustee office.
            - Prefer precision over guessing: if you are ≥95% sure, return lat/long; otherwise null.
            - matched_address must be the complete, corrected street address you actually geocoded.

            Input addresses:

            {chr(10).join(numbered)}

            Now return the JSON array and nothing else."""
        print(f"###DEBUG### GROK_PROMPT: {prompt}")
        return prompt

    def _geocode_with_grok_batch(self, addresses: List[Dict[str, Any]]):
        """Batch geocode with Grok, cribbed from test_grok.py"""
        print(f"###DEBUG### GROK_BATCH: Processing {len(addresses)} addresses")
        if not os.getenv("X_API_KEY"):
            print("###DEBUG### GROK_BATCH: ERROR NO API KEY!")
            return None

        self.grok_calls += len(addresses)

        # Create client per call for thread safety
        client = OpenAI(api_key=os.environ["X_API_KEY"], base_url="https://api.x.ai/v1")
        print("###DEBUG### GROK_BATCH: Created OpenAI client")

        prompt = self._build_grok_prompt(addresses)  

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "geocoding_results",
                "strict": True,
                "schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "lat": {"type": ["number", "null"]},
                            "long": {"type": ["number", "null"]},
                            "matched_address": {"type": ["string", "null"]}
                        },
                        "required": ["id", "lat", "long", "matched_address"],
                        "additionalProperties": False
                    },
                    "minItems": len(addresses),
                    "maxItems": len(addresses)
                }
            }
        }

        for attempt in range(3):
            start = time.time()
            try:
                response = client.chat.completions.create(
                    model="grok-4-latest",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1200,
                    temperature=0.0,
                    response_format=response_format,
                    timeout=300
                )
                elapsed = time.time() - start

                result = json.loads(response.choices[0].message.content)
                print(f"###DEBUG### GROK_BATCH: SUCCESS - {len(result)} results in {elapsed:.2f}s")
                # Debug: log the raw results
                for i, res in enumerate(result, 1):
                    print(f"###DEBUG### GROK_RAW_RESULT {i}: id={res.get('id')}, lat={res.get('lat')}, long={res.get('long')}, matched={res.get('matched_address')}")
                return {
                    "status": "SUCCESS",
                    "time_ms": round(elapsed * 1000, 1),
                    "attempt": attempt + 1,
                    "call": self.grok_calls,
                    "results": result
                }
            except Exception as e:
                elapsed = time.time() - start
                if attempt < 2:
                    wait = (2 ** attempt) + random.random()
                    log_info(f"Grok batch retry {attempt + 2}/3 in {wait:.1f}s... ({e})")
                    time.sleep(wait)
                else:
                    print(f"###DEBUG### GROK_BATCH: FAILED_ALL_RETRIES - {str(e)}")
                    return {"status": "FAILED_ALL_RETRIES", "error": str(e), "call": self.grok_calls}

        return {"status": "FAILED", "call": self.grok_calls}

    def _process_grok_batch(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        """Process a batch of items with batched Grok, return PDC."""
        print(f"###DEBUG### GROK_CONTEXT: Creating new PendingDatabaseContext for batch of {len(batch)} items")
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()
        self.grok_calls += 1

        # Prepare addresses for batch Grok
        addresses = []
        item_map = {}
        for idx, item in enumerate(batch, 1):
            normalized = item['normalized_address']
            canonical = item['canonical_address']
            if isinstance(normalized, str):
                try:
                    addr = json.loads(normalized)
                except:
                    addr = {"street": normalized, "city": "", "state": "", "zip": ""}
            else:
                addr = normalized.copy() if normalized else {}
            addresses.append({
                "canonical_address": canonical,
                "normalized_address": addr
            })
            item_map[idx] = item
            print(f"###DEBUG### GROK_PREP: idx={idx}, gid={item['geocoding_id']}, canonical={canonical}, normalized={normalized}, addr={addr}")

            # Special logging for Tull address in Grok processing
            if "Tull Charitable Foundation" in canonical:
                log_info(f"DEBUG_TULL: Grok batch prep - idx={idx}, geocoding_id={item['geocoding_id']}, street='{addr.get('street', '')}', zip='{addr.get('zip', '')}'")

        # Call batched Grok
        grok_result = self._geocode_with_grok_batch(addresses)
        grok_success_count = 0
        grok_failure_count = 0
        if grok_result and grok_result.get("status") == "SUCCESS":
            for res in grok_result["results"]:
                idx = res["id"]
                item = item_map[idx]
                gid = item['geocoding_id']
                attempt = int(item['attempt_count'])

                lat = res.get("lat")
                lon = res.get("long")
                matched = res.get("matched_address")

                print(f"###DEBUG### GROK_RESULT: gid={gid}, lat={lat}, lon={lon}, matched={matched}, canonical={item.get('canonical_address', '')}")

                # Special logging for Tull address Grok results
                if "Tull Charitable Foundation" in item.get('canonical_address', ''):
                    log_info(f"DEBUG_TULL: Grok result - geocoding_id={gid}, lat={lat}, lon={lon}, matched_address='{matched}'")

                if lat is not None and lon is not None:
                    status = "Match:Grok"
                    print(f"###DEBUG### GROK_SUCCESS: Setting {gid} to Match:Grok")
                    if "Tull Charitable Foundation" in item.get('canonical_address', ''):
                        log_info(f"DEBUG_TULL: Grok SUCCESS - geocoding_id={gid}, status={status}")
                    self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, res, matched)
                    grok_success_count += 1
                else:
                    # Grok failed, move to tier4 (individual APIs)
                    print(f"###DEBUG### GROK_FAILED: {gid} has null lat/lon, falling back to individual APIs")
                    if "Tull Charitable Foundation" in item.get('canonical_address', ''):
                        log_info(f"DEBUG_TULL: Grok FAILURE - geocoding_id={gid}, null lat/lon, falling back to individual APIs")
                    self._update_geocoding_stage(context, gid, 'tier4', now)
                    self._process_fallbacks_for_item(context, item, now)
                    grok_failure_count += 1
            log_info(f"DEBUG_GROK: Batch processed {len(addresses)} addresses - Success: {grok_success_count}, Failures: {grok_failure_count}")
        else:
            log_info(f"DEBUG_GROK: Batch failed with status={grok_result.get('status') if grok_result else 'None'}, processing {len(batch)} addresses individually")
            # Batch failed, move all to tier4 and fallback individually
            for item in batch:
                self._update_geocoding_stage(context, item['geocoding_id'], 'tier4', now)
                self._process_fallbacks_for_item(context, item, now)
                grok_failure_count += 1

        # Progress updates
        for item in batch:
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': item.get('address_count', 0)}
            ))

        print(f"###DEBUG### GROK_CONTEXT: Context created with {len(context.operations)} operations, estimated_updates={context.estimated_updates}")
        return context

    def _consumer_worker(self, num_producers: int):
        """Modified consumer worker that processes all result_queue items."""
        # Consumer thread: allow writes
        self.db_ops.set_allow_write(True)
        log_info("CONSUMER THREAD STARTED")
        current_batch = []
        current_estimated = 0
        sentinels_received = 0
        api_sentinels_sent = False

        def save_current_batch():
            nonlocal current_batch, current_estimated
            if not current_batch:
                return

            print(f"###DEBUG### CONSUMER_MERGE: Merging {len(current_batch)} contexts")
            merged = PendingDatabaseContext.merge(current_batch)
            print(f"###DEBUG### CONSUMER_MERGE: Merged context has {len(merged.operations)} operations, estimated_updates={merged.estimated_updates}")

            potential_total = self.total_objects_saved + merged.getTotalObjectCount()
            current_optimize_needed = potential_total // OPTIMIZE_THRESHOLD
            optimize_added = False
            if current_optimize_needed > self.last_optimize:
                op = DatabaseOperation(DatabaseOperationType.OPTIMIZE_DATABASE, data=None)
                merged.operations.append(op)
                optimize_added = True
                log_info(f"Appending OPTIMIZE_DATABASE to batch after {potential_total} objects")

            log_info(f"Saving batch of {len(current_batch)} contexts to database (estimated_updates: {merged.estimated_updates})")
            self.update_pdc_size_gauge(merged)
            print(f"###DEBUG### CONSUMER_SAVE: Calling save_to_database with {len(merged.operations)} operations")
            merged.save_to_database(self.db_ops)
            print(f"###DEBUG### CONSUMER_SAVE: save_to_database completed successfully")
            #self.db_ops.db_conn.execute("CHECKPOINT")
            log_info(f"Successfully saved batch of {len(current_batch)} contexts + checkpointed")
            self.total_objects_saved += merged.getTotalObjectCount()
            self.total_processed += len(current_batch)

            current_batch = []
            current_estimated = 0
            merged = None
            gc.collect()

            if optimize_added:
                self.last_optimize = current_optimize_needed

        while True:
            try:
                item = self.result_queue.get_nowait()
            except queue.Empty:
                if sentinels_received >= num_producers:
                    if not api_sentinels_sent:
                        # Send sentinels to api_queue to stop Grok workers
                        print("###DEBUG### CONSUMER: Sending 10 sentinels to api_queue")
                        for _ in range(10):
                            self.api_queue.put(None)
                        api_sentinels_sent = True
                if sentinels_received >= num_producers + 10:
                    # All main and worker sentinels received, exit
                    print(f"###DEBUG### CONSUMER: Received all {sentinels_received} sentinels, exiting")
                    if current_batch:
                        save_current_batch()
                    break
                time.sleep(1.1)
                continue

            self.result_queue.task_done()

            if item.is_sentinel():
                sentinels_received += 1
                print(f"###DEBUG### CONSUMER: Received sentinel {sentinels_received}/{num_producers}")
                continue

            context = item.data
            print(f"###DEBUG### CONSUMER: Processing context with {len(context.operations)} operations")

            # Preflight: check if adding this context would exceed the size limit
            if current_estimated + context.estimated_updates > GEOCODING_MAX_UPDATES_PER_BATCH and current_batch:
                # Save current batch before adding this large context
                save_current_batch()

            # Add context to current batch
            current_batch.append(context)
            current_estimated += context.estimated_updates

            # Save if batch size reached
            if len(current_batch) >= CONSUMER_BATCH_SIZE:
                save_current_batch()

        log_info("CONSUMER THREAD COMPLETED")

    def _process_fallbacks_for_item(self, context: PendingDatabaseContext, item: Dict[str, Any], now: str):
        """Process fallbacks for a single item."""
        gid = item['geocoding_id']
        attempt = int(item['attempt_count'])
        canonical = item['canonical_address']

        # Special logging for Tull address entering fallback processing
        if "Tull Charitable Foundation" in canonical:
            log_info(f"DEBUG_TULL: Entering Tier 4 fallback processing - geocoding_id={gid}, canonical='{canonical}'")

        # Strip C/O from canonical address before fallback processing
        stripped_canonical = self._strip_co_from_address(canonical)
        if stripped_canonical != canonical:
            log_debug(f"Fallback processing for {gid}: Stripped C/O from canonical '{canonical}' -> '{stripped_canonical}'")
            canonical = stripped_canonical

        # Parse normalized and strip C/O from street field
        normalized = item['normalized_address']
        if isinstance(normalized, str):
            try:
                norm_parsed = json.loads(normalized)
            except:
                norm_parsed = {"street": normalized, "city": "", "state": "", "zip": ""}
        else:
            norm_parsed = normalized.copy() if normalized else {}

        # Strip C/O from street field in normalized address
        if 'street' in norm_parsed and norm_parsed['street']:
            original_street = norm_parsed['street']
            norm_parsed['street'] = self._strip_co_from_address(norm_parsed['street'])
            if original_street != norm_parsed['street']:
                log_debug(f"Fallback processing for {gid}: Stripped C/O from normalized street '{original_street}' -> '{norm_parsed['street']}'")

        success = False
        fallbacks = self._build_api_fallbacks(gid, canonical)
        if "Tull Charitable Foundation" in canonical:
            log_info(f"DEBUG_TULL: Tier 4 - trying {len(fallbacks)} fallback APIs: {[name for name, _ in fallbacks]}")
        for name, func in fallbacks:
            try:
                if "Tull Charitable Foundation" in canonical:
                    log_info(f"DEBUG_TULL: Tier 4 - trying fallback API: {name}")
                res = func(norm_parsed, canonical)
                if res and res.get('match'):
                    lat = float(res['lat'])
                    lon = float(res['lon'])
                    status = f"Match:{name.replace('_', ' ')}"
                    matched_address = res.get('formatted_address')
                    if "Tull Charitable Foundation" in canonical:
                        log_info(f"DEBUG_TULL: Tier 4 SUCCESS - {name} matched, geocoding_id={gid}, status={status}, lat={lat}, lon={lon}")
                    self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, res, matched_address)
                    success = True
                    break
                else:
                    if "Tull Charitable Foundation" in canonical:
                        log_info(f"DEBUG_TULL: Tier 4 - {name} failed to match")
            except Exception as e:
                log_debug(f"Fallback {name} failed for {gid}: {e}")
                if "Tull Charitable Foundation" in canonical:
                    log_info(f"DEBUG_TULL: Tier 4 - {name} exception: {e}")

        if not success:
            # No_Match
            print(f"###DEBUG### FALLBACK_FAILED: Setting {gid} to No_Match after all fallbacks failed, canonical={item.get('canonical_address', '')}")
            if "Tull Charitable Foundation" in canonical:
                log_info(f"DEBUG_TULL: Tier 4 FINAL FAILURE - all {len(fallbacks)} fallback APIs failed, setting to No_Match")
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [{
                        'geocoding_id': gid,
                        'last_attempt': now,
                        'attempt_count': attempt + 1,
                        'geocoding_status': 'No_Match'
                    }],
                    'id_column': 'geocoding_id'
                }
            ))

    def _get_custom_metrics(self) -> Dict[str, Any]:
        try:
            pending = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = 'pending'").fetchone()[0]
            if global_config.max_files:
                pending = min(pending, global_config.max_files)
            matched = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding WHERE geocoding_status LIKE 'Match%'").fetchone()[0]
            return {
                'current_step': 'geolocate',
                'outstanding_geocode_requests': pending,
                'geocoded_addresses': matched,
                'census_calls': self.census_calls,
                'photon_calls': self.photon_calls,
                'librestreet_calls': self.librestreet_calls,
                'nominatim_calls': self.nominatim_calls,
                'opencage_calls': self.opencage_calls,
                'google_maps_calls': self.google_maps_calls,
                'name_search_calls': self.name_search_calls,
                'grok_calls': self.grok_calls,
                **super()._get_custom_metrics()
            }
        except: return super()._get_custom_metrics()

    def _feed_thread(self, work_queue, max_files=None, num_producers=4):
        last_pk = None
        total_processed = 0

        while not self.exit_processing:
            batch, last_pk = self._get_work_batch(last_pk)
            if not batch:
                break

            # If we're about to exceed max_files, trim the batch
            if max_files is not None:
                remaining = max_files - total_processed
                if remaining <= 0:
                    break
                if len(batch) > remaining:
                    batch = batch[:remaining]

            work_queue.put(WorkUnit.batch(batch))
            total_processed += len(batch)

            if max_files is not None and total_processed >= max_files:
                break

        # Send sentinels
        for _ in range(num_producers):
            work_queue.put(WorkUnit.sentinel(_))


    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict], Optional[str]]:
        where_parts = ["geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')"]
        params = []

        if last_pk is not None:
            where_parts.append("geocoding_id > ?")
            params.append(last_pk)

        where_clause = " AND ".join(where_parts)

        query = f"""
            SELECT geocoding_id, normalized_address, attempt_count, canonical_address, address_count, geocoding_status
            FROM Geocoding
            WHERE {where_clause}
            ORDER BY geocoding_id
            LIMIT {self.batch_size}
        """

        result = self.db_ops.execute_query(query, tuple(params))
        rows = result.fetchall()

        batch = []
        new_pk = last_pk

        for row in rows:
            batch.append({
                'geocoding_id': row[0],
                'normalized_address': row[1],
                'attempt_count': row[2],
                'canonical_address': row[3],
                'address_count': row[4] or 0,
                'geocoding_status': row[5]
            })
            new_pk = row[0]

        return batch, new_pk

    def get_work_count(self, max_files=None) -> int:
        """
        Return the total number of child addresses that will be processed,
        respecting max_files and the correct processing order.
        """
        base_query = """
            SELECT COALESCE(SUM(address_count), 0)
            FROM Geocoding
            WHERE geocoding_status IS NULL
            OR geocoding_status IN ('pending', 'owners')
        """

        if max_files is None:
            result = self.db_ops.execute_query(base_query)
            return result.fetchone()[0]

        # When max_files is set, we need to sum address_count for the first N geocoding records
        # in processing order (by geocoding_id)
        query = f"""
            SELECT COALESCE(SUM(address_count), 0)
            FROM (
                SELECT address_count
                FROM Geocoding
                WHERE geocoding_status IS NULL
                OR geocoding_status IN ('pending', 'owners')
                ORDER BY geocoding_id
                LIMIT {max_files}
            ) sub
        """
        result = self.db_ops.execute_query(query)
        return result.fetchone()[0]

    def get_progress_config(self, max_files=None):
        total = self.get_work_count(max_files = (max_files or global_config.max_files))
        return total, "addresses", "Geocoding addresses"

    def process_pending_geocoding_records(self, max_files=None, progress_bar=None) -> int:
        if cg is None:
            log_warning("censusgeocode not available")
            return 0
        self.setup_address_counts()
        log_info("Starting geocoding processing")
        processed = self.process_parallel(max_files=max_files)
        log_info(f"Geocoding complete: {processed} records processed")
        return processed

    def process_parallel(self, max_files=None, workers=4) -> int:
        # Start Grok workers before main processing
        print("###DEBUG### PROCESS_PARALLEL: Starting Grok workers")
        self._start_grok_workers()

        # Call parent process_parallel
        print("###DEBUG### PROCESS_PARALLEL: Calling super().process_parallel")
        processed = super().process_parallel(max_files=max_files, workers=workers)

        # Wait for Grok workers to complete
        print("###DEBUG### PROCESS_PARALLEL: Waiting for Grok workers to complete")
        for i, t in enumerate(self.grok_workers):
            t.join()
            print(f"###DEBUG### PROCESS_PARALLEL: Grok worker {i} completed")
        print("###DEBUG### PROCESS_PARALLEL: All Grok workers completed")

        return processed
    
    def _update_geocoding_stage(self, context: PendingDatabaseContext, geocoding_id: str, stage: str, now: str):
        """Update the geocoding stage for a specific geocoding record."""
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={
                'table': 'Geocoding',
                'updates': [{
                    'geocoding_id': geocoding_id,
                    'geocoding_stage': stage,
                    'last_attempt': now
                }],
                'id_column': 'geocoding_id'
            }
        ))

    def _apply_patterns_to_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Apply geocoding patterns to an item and return modified item."""
        canonical = item['canonical_address']
        zip_code = ""

        if isinstance(item['normalized_address'], str):
            try:
                zip_code = json.loads(item['normalized_address']).get('zip', '')
            except:
                pass

        pattern = self._check_geocoding_patterns(canonical, zip_code, 'all')
        if pattern and pattern.get('action') == 'strip':
            # Apply pattern stripping to normalized address
            regex = pattern.get('regex', '')
            if 'fields' in pattern:
                try:
                    normalized = item['normalized_address']
                    if isinstance(normalized, str):
                        data = json.loads(normalized)
                    else:
                        data = normalized.copy() if normalized else {}

                    modified = False
                    for field in pattern['fields']:
                        if field in data and data[field]:
                            cleaned = re.sub(regex, '', data[field], flags=re.IGNORECASE).strip()
                            if cleaned != data[field]:
                                data[field] = cleaned
                                modified = True

                    if modified:
                        item = item.copy()
                        item['normalized_address'] = json.dumps(data)
                        item['canonical_address'] = canonical  # Keep original canonical for now
                except:
                    pass
        return item

    def _process_census_tier(self, items: List[Dict[str, Any]], context: PendingDatabaseContext,
                           now: str, stage: str, batch_size: int) -> List[Dict[str, Any]]:
        """Process items through a census tier, returning failures."""
        if not items:
            return []

        # Update stage for all items
        for item in items:
            self._update_geocoding_stage(context, item['geocoding_id'], stage, now)

        # Apply patterns if this is tier2
        if stage == 'tier2':
            dangerous_matches = []
            dangerous_failures = []  # Dangerous matches that should continue to next tier
            non_matches = []
            dangerous_count = 0
            dangerous_failure_count = 0
            for item in items:
                zip_code = ""
                if isinstance(item['normalized_address'], str):
                    try:
                        zip_code = json.loads(item['normalized_address']).get('zip', '')
                    except:
                        pass
                pattern = self._check_geocoding_patterns(item['canonical_address'], zip_code, 'dangerous')
                if pattern:
                    # Check if this is a C/O pattern that might result in invalid address
                    if pattern.get('regex') == "(?i)^c/o\\s+(.+?),\\s*":
                        # For C/O addresses, check if stripping results in valid address
                        stripped = self._strip_co_from_address(item['canonical_address'])
                        is_valid = self._is_valid_street_address(stripped)
                        if "Tull Charitable Foundation" in item['canonical_address']:
                            log_info(f"DEBUG_TULL: Tier 2 C/O check - stripped='{stripped}', is_valid={is_valid}")
                        if not is_valid:
                            # Invalid address after stripping - let it continue to next tier
                            dangerous_failures.append(item)
                            dangerous_failure_count += 1
                            if "Tull Charitable Foundation" in item['canonical_address']:
                                log_info(f"DEBUG_TULL: Tier 2 - C/O address with invalid stripped result, continuing to next tier")
                            continue
                    dangerous_matches.append((item, pattern))
                    dangerous_count += 1
                else:
                    modified_item = self._apply_patterns_to_item(item)
                    non_matches.append(modified_item)
            # Handle dangerous matches that should be processed as patterns
            for item, pattern in dangerous_matches:
                self._handle_pattern_match(context, item, pattern, now)
            # Process census on non_matches and dangerous failures
            items = non_matches + dangerous_failures
            log_info(f"DEBUG_PATTERNS: Tier 2 - {dangerous_count} addresses matched dangerous patterns, {dangerous_failure_count} C/O addresses continue to next tier, {len(non_matches)} non-matches continue to Census")

        # Process in batches
        failed_items = []
        for i in range(0, len(items), batch_size):
            batch_items = items[i:i + batch_size]
            batch_failures = self._process_single_census_batch(batch_items, context, now)
            failed_items.extend(batch_failures)

        return failed_items

    def _process_single_census_batch(self, items: List[Dict[str, Any]], context: PendingDatabaseContext, now: str) -> List[Dict[str, Any]]:
        """Process a single batch of items with census geocoding, returning failures."""
        if not items:
            return []

        # Prepare batch for censusgeocode
        records = []
        id_map = {}
        for item in items:
            record = {}
            normalized = item['normalized_address']
            if isinstance(normalized, str):
                try:
                    record = json.loads(normalized)
                except:
                    pass
            elif isinstance(normalized, dict):
                record = normalized.copy()

            # Strip C/O from street address before sending to Census API
            if 'street' in record and record['street']:
                original_street = record['street']
                record['street'] = self._strip_co_from_address(record['street'])
                if original_street != record['street']:
                    log_debug(f"Census batch: Stripped C/O from street '{original_street}' -> '{record['street']}' for geocoding_id {item['geocoding_id']}")
                    if "Tull Charitable Foundation" in item['canonical_address']:
                        log_info(f"DEBUG_TULL: Census API prep - C/O stripped from street: '{original_street}' -> '{record['street']}'")

            record.pop('id', None)
            record['id'] = str(item['geocoding_id'])
            records.append(record)
            id_map[record['id']] = item

            # Log Tull address preparation for Census
            if "Tull Charitable Foundation" in item['canonical_address']:
                log_info(f"DEBUG_TULL: Census API prep - prepared record for geocoding_id {item['geocoding_id']}: street='{record.get('street', '')}', city='{record.get('city', '')}', state='{record.get('state', '')}', zip='{record.get('zip', '')}'")

        try:
            self.census_calls += 1
            results = cg.addressbatch(records)
        except Exception as e:
            log_error(f"Census batch call failed: {e}")
            # All items failed
            for item in items:
                context.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Geocoding',
                        'updates': [{
                            'geocoding_id': item['geocoding_id'],
                            'last_attempt': now,
                            'attempt_count': int(item['attempt_count']) + 1,
                            'geocoding_status': 'failed'
                        }],
                        'id_column': 'geocoding_id'
                    }
                ))
            return items

        # Process results and collect failures
        failed_items = []
        for result in results:
            gid_str = result.get('id')
            if not gid_str or gid_str not in id_map:
                continue
            item = id_map[gid_str]
            gid = item['geocoding_id']
            attempt = int(item['attempt_count'])

            # Special logging for Tull address Census results
            if "Tull Charitable Foundation" in item['canonical_address']:
                log_info(f"DEBUG_TULL: Census API result - geocoding_id={gid}, match={result.get('match')}, lat={result.get('lat')}, lon={result.get('lon')}, matchtype={result.get('matchtype')}, exact_match={result.get('exact_match')}")

            if result.get('match') and result.get('lat') and result.get('lon'):
                lat = float(result['lat'])
                lon = float(result['lon'])
                matchtype = result.get('matchtype', 'Exact')
                status = 'Match' if matchtype == 'Exact' else f'Match:{matchtype}'
                if "Tull Charitable Foundation" in item['canonical_address']:
                    log_info(f"DEBUG_TULL: Census API SUCCESS - geocoding_id={gid}, status={status}, lat={lat}, lon={lon}")
                self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, result)
            else:
                if "Tull Charitable Foundation" in item['canonical_address']:
                    log_info(f"DEBUG_TULL: Census API FAILURE - geocoding_id={gid}, no match or missing coordinates")
                failed_items.append(item)

        return failed_items

    def _enqueue_grok_batches(self, items: List[Dict[str, Any]], batch_size: int):
        """Enqueue items for Grok processing in batches."""
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            print(f"###DEBUG### ENQUEUE: Putting batch of {len(batch)} items into api_queue")
            self.api_queue.put(batch)

    def _process_batch(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        """
        Batch processor implementing 4-tier geocoding approach:
        - Tier 1: Census(raw) batch 1000 on original addresses
        - Tier 2: Census(patterns) batch 1000 on failures (apply patterns first)
        - Tier 3: Grok batch 20 on failures
        - Tier 4: Individual APIs on failures
        """
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()

        # Check if Tull address is in this batch
        tull_in_batch = any("Tull Charitable Foundation" in item['canonical_address'] for item in batch)
        if tull_in_batch:
            tull_items = [item for item in batch if "Tull Charitable Foundation" in item['canonical_address']]
            log_info(f"DEBUG_TULL: Tull Charitable Foundation address entered _process_batch - {len(tull_items)} instances found")
            for item in tull_items:
                log_info(f"DEBUG_TULL: Batch entry - geocoding_id={item['geocoding_id']}, canonical='{item['canonical_address']}', normalized='{item['normalized_address']}', status={item['geocoding_status']}")

        # Debug counters for pipeline flow
        total_addresses = len(batch)
        owners_count = 0
        pattern_safe_count = 0
        api_items_count = 0
        tier1_success = 0
        tier1_failures = 0
        tier2_success = 0
        tier2_failures = 0
        tier3_enqueued = 0
        tier4_processed = 0

        # Separate owners and pattern-matched items from those needing API calls
        owners_items = []
        pattern_items = []
        api_items = []

        for item in batch:
            if item['geocoding_status'] == 'owners':
                owners_items.append(item)
                owners_count += 1
                if "Tull Charitable Foundation" in item['canonical_address']:
                    log_info(f"DEBUG_TULL: Routing decision - routed to OWNERS processing")
            else:
                zip_code = ""
                if isinstance(item['normalized_address'], str):
                    try:
                        zip_code = json.loads(item['normalized_address']).get('zip', '')
                    except:
                        pass

                # Check all pattern types for Tull address
                if "Tull Charitable Foundation" in item['canonical_address']:
                    safe_pattern = self._check_geocoding_patterns(item['canonical_address'], zip_code, 'safe')
                    dangerous_pattern = self._check_geocoding_patterns(item['canonical_address'], zip_code, 'dangerous')
                    all_patterns = self._check_geocoding_patterns(item['canonical_address'], zip_code, 'all')
                    log_info(f"DEBUG_TULL: Pattern matching - safe={safe_pattern is not None}, dangerous={dangerous_pattern is not None}, all={all_patterns is not None}")
                    if safe_pattern:
                        log_info(f"DEBUG_TULL: Safe pattern details - action={safe_pattern.get('action')}, status={safe_pattern.get('status')}")
                    if dangerous_pattern:
                        log_info(f"DEBUG_TULL: Dangerous pattern details - action={dangerous_pattern.get('action')}, status={dangerous_pattern.get('status')}")
                    if all_patterns:
                        log_info(f"DEBUG_TULL: All patterns details - action={all_patterns.get('action')}, status={all_patterns.get('status')}")

                pattern = self._check_geocoding_patterns(item['canonical_address'], zip_code, 'safe')
                if pattern:
                    pattern_items.append((item, pattern))
                    pattern_safe_count += 1
                    if "Tull Charitable Foundation" in item['canonical_address']:
                        log_info(f"DEBUG_TULL: Routing decision - routed to PATTERN MATCHING (safe)")
                else:
                    api_items.append(item)
                    api_items_count += 1
                    if "Tull Charitable Foundation" in item['canonical_address']:
                        log_info(f"DEBUG_TULL: Routing decision - routed to API GEOCODING PIPELINE")

        # Process owners (no API call)
        for item in owners_items:
            subctx = self._process_owners_work_item(item)
            context.operations.extend(subctx.operations)
            context.estimated_updates += subctx.estimated_updates

        # Process pattern matches
        for item, pattern in pattern_items:
            self._handle_pattern_match(context, item, pattern, now)

        # If nothing needs API, return early
        if not api_items:
            total_addresses = sum(item.get('address_count', 0) for item in batch)
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': total_addresses}
            ))
            return context

        # === 4-Tier Geocoding Pipeline ===

        current_items = api_items
        tier_failures = []

        # Tier 1: Census(raw) batch 1000 on original addresses
        if API_CONFIG.get('ENABLE_CENSUS_RAW', True):
            # Check if Tull address is in current_items
            tull_in_tier1 = any("Tull Charitable Foundation" in item['canonical_address'] for item in current_items)
            if tull_in_tier1:
                log_info(f"DEBUG_TULL: Entering Tier 1 (Census Raw) - address present in {len(current_items)} items")
            tier1_failures = self._process_census_tier(current_items, context, now, 'tier1', 1000)
            tier1_success = len(current_items) - len(tier1_failures)
            tier1_failures_count = len(tier1_failures)
            current_items = tier1_failures
            if tull_in_tier1:
                log_info(f"DEBUG_TULL: Tier 1 complete - Success: {tier1_success}, Failures: {tier1_failures_count}, Remaining: {len(current_items)}")
            log_info(f"DEBUG_PIPELINE: Tier 1 complete - Success: {tier1_success}, Failures: {tier1_failures_count}, Remaining: {len(current_items)}")
        else:
            log_info("Tier 1 (Census Raw) disabled, skipping to next tier")

        # Tier 2: Census(patterns) batch 1000 on failures (apply patterns first)
        if API_CONFIG.get('ENABLE_CENSUS_PATTERNS', True) and current_items:
            # Check if Tull address is in current_items
            tull_in_tier2 = any("Tull Charitable Foundation" in item['canonical_address'] for item in current_items)
            if tull_in_tier2:
                log_info(f"DEBUG_TULL: Entering Tier 2 (Census Patterns) - address present in {len(current_items)} items")
            tier2_failures = self._process_census_tier(current_items, context, now, 'tier2', 1000)
            tier2_success = len(current_items) - len(tier2_failures)
            tier2_failures_count = len(tier2_failures)
            current_items = tier2_failures
            if tull_in_tier2:
                log_info(f"DEBUG_TULL: Tier 2 complete - Success: {tier2_success}, Failures: {tier2_failures_count}, Remaining: {len(current_items)}")
            log_info(f"DEBUG_PIPELINE: Tier 2 complete - Success: {tier2_success}, Failures: {tier2_failures_count}, Remaining: {len(current_items)}")
        elif not API_CONFIG.get('ENABLE_CENSUS_PATTERNS', True):
            log_info("Tier 2 (Census Patterns) disabled, skipping to next tier")

        # Tier 3: Grok batch 20 on failures
        if API_CONFIG.get('ENABLE_GROK', False) and current_items:
            # Check if Tull address is in current_items
            tull_in_tier3 = any("Tull Charitable Foundation" in item['canonical_address'] for item in current_items)
            if tull_in_tier3:
                log_info(f"DEBUG_TULL: Entering Tier 3 (Grok Batch) - address present in {len(current_items)} items")
            # Update stage to tier3 for Grok items
            for item in current_items:
                self._update_geocoding_stage(context, item['geocoding_id'], 'tier3', now)
            # Enqueue for Grok processing
            self._enqueue_grok_batches(current_items, 20)
            tier3_enqueued = len(current_items)
            current_items = []  # Grok handles all remaining items
            if tull_in_tier3:
                log_info(f"DEBUG_TULL: Tier 3 - Enqueued {tier3_enqueued} addresses for Grok processing")
            log_info(f"DEBUG_PIPELINE: Tier 3 - Enqueued {tier3_enqueued} addresses for Grok processing")
        elif not API_CONFIG.get('ENABLE_GROK', False):
            log_info("Tier 3 (Grok) disabled, skipping to individual API fallbacks")

        # Tier 4: Individual API fallbacks for any remaining items
        # This happens asynchronously via the Grok workers calling _process_fallbacks_for_item
        # or synchronously if Grok is disabled
        if current_items and not API_CONFIG.get('ENABLE_GROK', False):
            tier4_processed = len(current_items)
            # If Grok is disabled, process individual API fallbacks synchronously
            enabled_fallbacks = self._build_api_fallbacks()
            if enabled_fallbacks:
                for item in current_items:
                    gid = item['geocoding_id']
                    attempt = int(item['attempt_count'])
                    canonical = item['canonical_address']

                    # Try each enabled fallback API
                    success = False
                    for name, func in enabled_fallbacks:
                        try:
                            # Parse normalized address for API calls
                            normalized = item['normalized_address']
                            if isinstance(normalized, str):
                                try:
                                    norm_parsed = json.loads(normalized)
                                except:
                                    norm_parsed = {"street": normalized, "city": "", "state": "", "zip": ""}
                            else:
                                norm_parsed = normalized.copy() if normalized else {}
        
                            # Strip C/O from street field in normalized address
                            if 'street' in norm_parsed and norm_parsed['street']:
                                original_street = norm_parsed['street']
                                norm_parsed['street'] = self._strip_co_from_address(norm_parsed['street'])
                                if original_street != norm_parsed['street']:
                                    log_debug(f"Tier 4 processing for {gid}: Stripped C/O from normalized street '{original_street}' -> '{norm_parsed['street']}'")
        
                            res = func(canonical, norm_parsed)
                            if res and res.get('match'):
                                lat = float(res['lat'])
                                lon = float(res['lon'])
                                status = f"Match:{name.replace('_', ' ')}"
                                matched_address = res.get('formatted_address')
                                self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, res, matched_address)
                                success = True
                                break
                        except Exception as e:
                            log_debug(f"Fallback {name} failed for {gid}: {e}")

                    if not success:
                        # No API succeeded, mark as No_Match
                        context.addOperationToDatabase(DatabaseOperation(
                            operation_type=DatabaseOperationType.GENERIC_UPDATE,
                            data={
                                'table': 'Geocoding',
                                'updates': [{
                                    'geocoding_id': gid,
                                    'last_attempt': now,
                                    'attempt_count': attempt + 1,
                                    'geocoding_status': 'No_Match'
                                }],
                                'id_column': 'geocoding_id'
                            }
                        ))
            else:
                # No fallback APIs enabled, mark all as No_Match
                for item in current_items:
                    gid = item['geocoding_id']
                    attempt = int(item['attempt_count'])
                    context.addOperationToDatabase(DatabaseOperation(
                        operation_type=DatabaseOperationType.GENERIC_UPDATE,
                        data={
                            'table': 'Geocoding',
                            'updates': [{
                                'geocoding_id': gid,
                                'last_attempt': now,
                                'attempt_count': attempt + 1,
                                'geocoding_status': 'No_Match'
                            }],
                            'id_column': 'geocoding_id'
                        }
                    ))
            log_info(f"DEBUG_PIPELINE: Tier 4 complete - Processed {tier4_processed} addresses with individual API fallbacks")

        # Pipeline summary logging
        log_info(f"DEBUG_PIPELINE_SUMMARY: Batch processed {total_addresses} addresses - "
                f"Owners: {owners_count}, SafePatterns: {pattern_safe_count}, API_Items: {api_items_count}, "
                f"Tier1_Success: {tier1_success}, Tier2_Success: {tier2_success}, Tier3_Grok: {tier3_enqueued}")

        # Final progress
        total_addresses = sum(item.get('address_count', 0) for item in batch)
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': total_addresses}
        ))

        return context