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

from censusbatchgeocoder import batch_geocode
import requests
from urllib.parse import quote
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from opencage.geocoder import OpenCageGeocode
import csv
from io import StringIO

try:
    import tqdm
except ImportError:
    tqdm = None

from base_processor import BaseProcessor
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from models.geocoding import Geocoding
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config
from constants import GEOCODING_BATCH_SIZE, GEOCODING_MAX_UPDATES_PER_BATCH, CONSUMER_BATCH_SIZE, OPTIMIZE_THRESHOLD
from pending_database_context import PendingDatabaseContext
from pipeline import PipelineStage,Pipeline,WorkUnit
if 'GeocodingAPIProcessor' in sys.modules:
    print("WARNING: GeocodingAPIProcessor already imported — check for duplicate imports!")
    
from pydantic import BaseModel, Field
from typing import List, Optional

# Define the exact output schema — Grok-4 enforces this
class GeocodeResult(BaseModel):
    id: int = Field(..., description="The 1-based index of the address in the batch")
    lat: Optional[float] = Field(None, description="Latitude if confident match")
    long: Optional[float] = Field(None, description="Longitude if confident match")
    matched_address: Optional[str] = Field(None, description="Best matched address string")
    reason: Optional[str] = Field(None, description="Optional explanation if partial confidence")

class BatchGeocodeOutput(BaseModel):
    results: List[GeocodeResult] = Field(..., description="Array of results, one per input address")

# === API Configuration ===
# Centralized configuration for enabling/disabling geocoding APIs and their priorities
API_CONFIG = {
    # Census APIs
    'ENABLE_CENSUS_RAW': True,        # Tier 1: Raw census batch geocoding
    'ENABLE_CENSUS_PATTERNS': False,   # BUGGY Tier 2: Census with pattern preprocessing

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

@dataclass
class GeocodingWorkUnit(WorkUnit):
    """
    Specialized WorkUnit for geocoding tasks. Assumes `data` is a dict from the DB row.
    Provides typed properties for access and caches parsed normalized_address.
    """
    _parsed_normalized: Optional[Dict[str, str]] = field(default=None, init=False)

    @property
    def geocoding_id(self) -> str:
        return self.data['geocoding_id']

    @property
    def normalized_address(self) -> any:  # Raw value (str or dict)
        return self.data['normalized_address']

    @property
    def attempt_count(self) -> int:
        return self.data['attempt_count']

    @property
    def canonical_address(self) -> str:
        return self.data['canonical_address']

    @property
    def address_count(self) -> int:
        return self.data.get('address_count', 0)

    @property
    def geocoding_status(self) -> str:
        return self.data['geocoding_status']

    @property
    def parsed_normalized(self) -> Dict[str, str]:
        if self._parsed_normalized is None:
            norm = self.normalized_address
            if isinstance(norm, str):
                try:
                    self._parsed_normalized = json.loads(norm)
                except json.JSONDecodeError:
                    self._parsed_normalized = {}
                    log_warning(f"Failed to parse normalized_address for geocoding_id {self.geocoding_id}")
            elif isinstance(norm, dict):
                self._parsed_normalized = norm
            else:
                self._parsed_normalized = {}
                log_warning(f"Invalid normalized_address type for geocoding_id {self.geocoding_id}")
        return self._parsed_normalized

    # Override work_item classmethod to return GeocodingWorkUnit
    @classmethod
    def work_item(cls, stage: str, data: Dict) -> "GeocodingWorkUnit":
        return cls(type='work', data=data, stage=stage)

class GeocodingAPIProcessor(BaseProcessor):
    
    def _preprocess_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        results = []
        now = datetime.now().isoformat()

        for unit in batch:  # Use GeocodingWorkUnit
            addr = unit.canonical_address
            gid = unit.geocoding_id
            zip_code = unit.parsed_normalized.get('zip', '')  # Now from structured

            # 1. Owners — skip geocoding, just progress (update to use status)
            if unit.geocoding_status == 'owners':
                ctx = PendingDatabaseContext()
                ctx.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                    data={'count': unit.address_count}
                ))
                results.append((True, ctx))
                continue

            # 2. Safe patterns — match and progress
            pattern = self._check_geocoding_patterns(addr, zip_code, 'safe')
            if pattern:
                ctx = PendingDatabaseContext()
                update = {
                    'geocoding_id': gid,
                    'last_attempt': now,
                    'attempt_count': unit.attempt_count + 1,
                    'geocoding_status': pattern.get('status', 'Pattern_Match'),
                }
                if pattern.get('lat') is not None:
                    update['lat'] = pattern['lat']
                    update['lon'] = pattern['lon']
                if pattern.get('formatted_address'):
                    update['matched_address'] = pattern['formatted_address']

                ctx.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={'table': 'Geocoding', 'updates': [update], 'id_column': 'geocoding_id'}
                ))
                ctx.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                    data={'count': unit.address_count}
                ))
                results.append((True, ctx))
                continue

            # 3. C/O stripping — update canonical_address
            stripped = self._strip_co_from_address(addr)
            if stripped != addr:
                ctx = PendingDatabaseContext()
                ctx.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Geocoding',
                        'updates': [{'geocoding_id': gid, 'canonical_address': stripped}],
                        'id_column': 'geocoding_id'
                    }
                ))
                # Also update normalized 'street' for consistency (if C/O was in street)
                parsed = unit.parsed_normalized
                if parsed and 'street' in parsed and re.match(r'(?i)^c/?o', parsed['street']):
                    stripped_street = self._strip_co_from_address(parsed['street'])
                    if stripped_street != parsed['street']:
                        updated_norm = json.dumps({**parsed, 'street': stripped_street})
                        ctx.addOperationToDatabase(DatabaseOperation(
                            operation_type=DatabaseOperationType.GENERIC_UPDATE,
                            data={
                                'table': 'Geocoding',
                                'updates': [{'geocoding_id': gid, 'normalized_address': updated_norm}],
                                'id_column': 'geocoding_id'
                            }
                        ))
                # Modify unit for downstream (mutate data)
                unit.data['canonical_address'] = stripped
                results.append((False, unit))
            else:
                results.append((False, unit))

        return results
    
    def _grok_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        """
        Grok AI batch geocoding handler — now using Grok-4 family with structured outputs.

        Key changes:
        - Model: grok-4-1-fast (fast + reliable structured JSON) instead of grok-2-latest
        - Uses Pydantic schema + xAI SDK structured mode → 100% schema-compliant JSON
        - Simplified prompt: model follows schema, no need for heavy formatting instructions
        - Uses unit.geocoding_id as 'id' in schema for traceability
        - Falls back to manual parse only if structured fails (very rare)
        - Small batch size preserved due to cost
        """
        if not batch:
            return []

        # Assuming xai_sdk is imported; fallback to openai-compatible client if needed
        try:
            from xai_sdk import Client as XAIClient
            client = XAIClient(api_key=os.getenv("X_API_KEY"))
        except ImportError:
            # Fallback to openai client (compatible endpoint)
            from openai import OpenAI
            client = OpenAI(api_key=os.getenv("X_API_KEY"), base_url="https://api.x.ai/v1")

        items = [unit for unit in batch]  # GeocodingWorkUnit instances

        # Build prompt — much lighter now
        prompt_lines = []
        for i, unit in enumerate(items, 1):
            canon = unit.canonical_address.strip()
            norm_str = json.dumps(unit.parsed_normalized, separators=(',', ':')) if unit.parsed_normalized else ""
            prompt_lines.append(f"{i}. Raw: \"{canon}\" | Parsed JSON: {norm_str}")

        system_prompt = """You are an expert geocoder for messy IRS 990 nonprofit addresses.
    Ignore C/O, Attn:, See Statement, personal names unless tied to known org.
    Use known entity HQs when obvious (e.g. First Citizens → Raleigh area).
    Return lat/long only if ≥75% confident in a real street location."""

        user_prompt = f"""Analyze these addresses and geocode them:

    {'\n'.join(prompt_lines)}

    For each, provide:
    - id: the number above (1..{len(items)})
    - lat / long: float or null
    - matched_address: best full address or null
    - reason: optional short note if partial match

    Output ONLY the structured results array."""

        try:
            # Structured call — preferred with xAI SDK
            if hasattr(client, 'chat') and hasattr(client.chat, 'create_with_schema'):  # pseudo-check; adjust per actual SDK
                response = client.chat.create(
                    model="grok-4-1-fast",  # or "grok-4-fast" if 4.1 not available
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_model=BatchGeocodeOutput,  # Pydantic → enforced schema
                    temperature=0.0,
                    max_tokens=4000,
                    timeout=300
                )
                parsed = response  # Already BatchGeocodeOutput instance

            else:
                # OpenAI-compatible fallback with json_schema
                schema = BatchGeocodeOutput.model_json_schema()
                response = client.chat.completions.create(
                    model="grok-4-1-fast",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "batch_geocode",
                            "strict": True,
                            "schema": schema
                        }
                    },
                    temperature=0.0,
                    max_tokens=4000,
                    timeout=300
                )
                raw = response.choices[0].message.content.strip()
                parsed = BatchGeocodeOutput.model_validate_json(raw)

            # Map back to units (id is 1-based index)
            results = []
            for unit, res_item in zip(items, parsed.results):
                if res_item.lat is not None and res_item.long is not None:
                    ctx = self._apply_successful_geocode(
                        unit,
                        res_item.lat,
                        res_item.long,
                        "Match:Grok-4",
                        res_item.matched_address or unit.canonical_address
                    )
                    results.append((True, ctx))
                else:
                    results.append((False, unit))
            return results

        except Exception as e:
            log_error(f"Grok-4 batch failed: {str(e)}")
            # Optional: retry once with grok-4-fast or fall back to grok-2-latest
            return [(False, unit) for unit in batch]
        
    def _final_fail_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        ctx = PendingDatabaseContext()
        now = datetime.now().isoformat()

        for unit in batch:
            gid = unit.geocoding_id

            ctx.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Geocoding',
                    'updates': [{
                        'geocoding_id': gid,
                        'geocoding_status': 'No_Match',
                        'last_attempt': now,
                        'attempt_count': unit.attempt_count + 1,
                        'matched_address': 'Failed all stages',
                    }],
                    'id_column': 'geocoding_id'
                }
            ))

            ctx.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': unit.address_count}
            ))

        # Return success for the batch
        return [(True, ctx)] * len(batch)
    
    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_BATCH_SIZE):
        super().__init__(db_ops)
        self.batch_size = batch_size
        self.geocoding_patterns = self._load_geocoding_patterns()
          # Queue for parallel Grok processing
        preprocess_stage = PipelineStage(name="preprocess", workers=4, batch_size=5000, handler=self._preprocess_handler)
        census_stage = PipelineStage(name="census", workers=1, batch_size=1000, handler=self._census_handler)
        stages = [
            preprocess_stage,
            census_stage,
        ]
        preprocess_stage.next_on_failure = census_stage
        last_stage = census_stage
        if API_CONFIG.get('ENABLE_GROK', True):
            grok_stage = PipelineStage(name="grok", workers=8, batch_size=20, handler=self._grok_handler)
            stages.append(grok_stage)

    # Individual fallback stages (single item each)
        if API_CONFIG.get('ENABLE_PHOTON', True):
            photon_stage = PipelineStage(name="photon", workers=6, batch_size=1, handler=self._photon_handler)
            stages.append(photon_stage)
        if API_CONFIG.get('ENABLE_LIBRESTREET', True):
            librestreet_stage = PipelineStage(name="librestreet", workers=6, batch_size=1, handler=self._librestreet_handler)
            stages.append(librestreet_stage)
        if API_CONFIG.get('ENABLE_NOMINATIM', True):
            nominatim_stage = PipelineStage(name="nominatim", workers=6, batch_size=1, handler=self._nominatim_handler)
            stages.append(nominatim_stage)
        if API_CONFIG.get('ENABLE_OPENCAGE', True):
            opencage_stage = PipelineStage(name="opencage", workers=6, batch_size=1, handler=self._opencage_handler)
            stages.append(opencage_stage)
        # Final failure stage
        final_fail_stage = PipelineStage(name="fail", workers=1, batch_size=1000, handler=self._final_fail_handler, is_final_failure=True)
        stages.append(final_fail_stage)
        # Create pipeline
        # Link stages together
        for i in range(len(stages) - 1):
            stages[i].next_on_failure = stages[i + 1]
        print(f"Pipeline stages: {[s.name for s in stages]}")
        self.pipeline = Pipeline(stages,db_ops )
        for stage in stages[:-1]:
            stage.next_on_success = self.pipeline.result_consumer        


    def setup_address_counts(self):
        print("Setup: Populating address_count if needed")
        result = self.db_ops.execute_query("SELECT COUNT(DISTINCT address_count) FROM Geocoding")
        if result and result.fetchone()[0] > 1:
            print("address_count already populated")
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
            print("address_count populated")
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
            print(f"Loaded {len(patterns)} geocoding patterns")
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
            print(f"DEBUG_TULL: C/O stripping initiated for '{address}'")

        # Try comma-separated entity first (e.g., "C/O The Organization, Branchport...")
        comma_pattern = r'^(?:c/?o,?)\s*[^,\d]*,\s*'
        if re.search(comma_pattern, address, re.IGNORECASE):
            result = re.sub(comma_pattern, '', address, flags=re.IGNORECASE).strip()
            log_debug(f"C/O strip: Used comma pattern, result: '{result}'")
            if "Tull Charitable Foundation" in address:
                print(f"DEBUG_TULL: C/O stripping - comma pattern matched, result: '{result}'")
            return result

        # Otherwise, entity until first digit (e.g., "C/O Foundation 3...")
        digit_pattern = r'^(?:c/?o,?)\s*.*?(?=\d)'
        if re.search(digit_pattern, address, re.IGNORECASE):
            result = re.sub(digit_pattern, '', address, flags=re.IGNORECASE).strip()
            log_debug(f"C/O strip: Used digit pattern, result: '{result}'")
            if "Tull Charitable Foundation" in address:
                print(f"DEBUG_TULL: C/O stripping - digit pattern matched, result: '{result}'")
            return result

        log_debug(f"C/O strip: No pattern matched for '{address}', returning original")
        if "Tull Charitable Foundation" in address:
            print(f"DEBUG_TULL: C/O stripping - no pattern matched, returning original: '{address}'")
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

    def _apply_successful_geocode(self, unit: WorkUnit, lat: float, lon: float, status: str, matched: str) -> tuple[bool, PendingDatabaseContext]:
        item = unit.data
        ctx = PendingDatabaseContext()
        now = datetime.now().isoformat()
        gid = item['geocoding_id']
        rlat = round(lat, 4)
        rlon = round(lon, 4)
        colocator = f"LL:{rlat}:{rlon}"

        update = {
            'geocoding_id': gid,
            'last_attempt': now,
            'attempt_count': item.get('attempt_count', 0) + 1,
            'latitude': rlat,
            'longitude': rlon,
            'geocoding_status': status,
            'matched_address': matched or item['canonical_address']
        }
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [update], 'id_column': 'geocoding_id'}
        ))

        # Update Addresses table colocator
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Addresses', 'set_clause': 'colocator = ?', 'where_clause': 'geocoding_id = ?', 'params': (colocator, gid)}
        ))

        # Update owner tables
        self._update_owner_colocators(ctx, gid, colocator)

        # Progress
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': item.get('address_count', 1)}
        ))

        return True, ctx
    
    def _geocode_single_with_structured_fallback(
        self,
        unit: GeocodingWorkUnit,
        geocode_callable: callable,   # (query: str | dict) -> dict | None   with keys 'lat', 'lon', 'matched_address'?
        source_name: str,
        prefer_structured: bool = True,
        timeout: float = 10.0
    ) -> tuple[bool, Any]:  # (success_flag, ctx_or_unit)
        """
        Unified geocoding helper for single-item services (Photon, Nominatim, etc.).
        Prefers structured dict query when available; falls back to string.
        """
        parsed = unit.parsed_normalized
        query = None

        if prefer_structured and parsed:
            query_dict = {
                'street': parsed.get('street', '') or parsed.get('address_line1', ''),
                'city': parsed.get('city', ''),
                'state': parsed.get('state', ''),
                'postalcode': parsed.get('zip', '') or parsed.get('zip_code', '')
            }
            query_dict = {k: v for k, v in query_dict.items() if v}
            if query_dict:
                query = query_dict

        if query is None:
            # Build string fallback
            parts = [
                parsed.get('street', '') or parsed.get('address_line1', ''),
                parsed.get('city', ''),
                parsed.get('state', ''),
                parsed.get('zip', '')
            ]
            query = ', '.join(filter(None, parts)).strip(', ')
            if not query:
                query = unit.canonical_address.strip()

        try:
            result = geocode_callable(query)
            if result and 'lat' in result and 'lon' in result:
                return True, self._apply_successful_geocode(
                    unit,
                    result['lat'],
                    result['lon'],
                    f"Match:{source_name}",
                    result.get('matched_address', unit.canonical_address)
                )
        except Exception as e:
            log_warning(f"{source_name} geocoding failed for {unit.geocoding_id}: {str(e)}")

        return False, unit
    
    
    def _geocode_via_geopy(
        self,
        unit: GeocodingWorkUnit,
        geolocator_callable,          # e.g. self.nominatim or lambda q: self.photon(q)
        source_name: str,
        prefer_structured: bool = True
    ) -> tuple[bool, Any]:
        parsed = unit.parsed_normalized
        query = None

        if prefer_structured:
            if source_name == "Nominatim":  # supports structured
                query = {
                    'street': parsed.get('street', '') or parsed.get('address_line1', ''),
                    'city': parsed.get('city', ''),
                    'state': parsed.get('state', ''),
                    'postalcode': parsed.get('zip', '')
                }
                query = {k: v for k, v in query.items() if v}
            # Photon doesn't support it → fall to string anyway

        if not query:
            # string fallback (works for all)
            parts = [
                parsed.get('street', '') or parsed.get('address_line1', ''),
                parsed.get('city', ''),
                parsed.get('state', ''),
                parsed.get('zip', '')
            ]
            query = ', '.join(filter(None, parts)).strip(', ') or unit.canonical_address.strip()

        try:
            location = geolocator_callable(query)
            if location:
                return True, self._apply_successful_geocode(
                    unit,
                    location.latitude,
                    location.longitude,
                    f"Match:{source_name}",
                    location.address or unit.canonical_address
                )
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            log_warning(f"{source_name} timed out/service error for {unit.geocoding_id}: {e}")
        except Exception as e:
            log_warning(f"{source_name} failed: {e}")

        return False, unit
    
    def _photon_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        return self._geopy_handler(batch, "Photon")

    def _nominatim_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        return self._geopy_handler(batch, "Nominatim")

    def _librestreet_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        results = []
        now = datetime.now().isoformat()

        for unit in batch:
            item = unit.data
            addr = item['canonical_address']

            try:
                res = self._geocode_with_librestreet(addr, item.get('normalized_address'))
                if res and res.get('match'):
                    results.append(self._apply_successful_geocode(item, res['lat'], res['lon'], "Match:LibreStreet", res['formatted_address']))
                else:
                    results.append((False, item))
            except Exception as e:
                log_debug(f"LibreStreet failed for {item['geocoding_id']}: {e}")
                results.append((False, item))

        return results

    def _opencage_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        results = []
        now = datetime.now().isoformat()

        for unit in batch:
            item = unit.data
            addr = item['canonical_address']

            try:
                res = self._geocode_with_opencage(addr, item.get('normalized_address'))
                if res and res.get('match'):
                    results.append(self._apply_successful_geocode(item, res['lat'], res['lon'], "Match:OpenCage", res['formatted_address']))
                else:
                    results.append((False, item))
            except Exception as e:
                log_debug(f"OpenCage failed for {item['geocoding_id']}: {e}")
                results.append((False, item))

        return results
    
    def _census_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
        results = []
        census_data = []
        now = datetime.now().isoformat()

        for unit in batch:  # Now using GeocodingWorkUnit
            if not isinstance(unit, GeocodingWorkUnit):
                # Fallback for plain WorkUnit (unlikely)
                item = unit.data
                parsed = {}  # Would need to parse here if needed
            else:
                parsed = unit.parsed_normalized

            if not parsed:
                # Fallback to constructing from canonical (rare, if JSON failed)
                log_warning(f"Census: Falling back to canonical for {unit.geocoding_id}")
                parts = unit.canonical_address.split(', ')
                street = ', '.join(parts[:-3]) if len(parts) > 3 else ''
                city = parts[-3] if len(parts) > 2 else ''
                state = parts[-2] if len(parts) > 1 else ''
                zip_code = parts[-1] if parts else ''
                parsed = {'street': street, 'city': city, 'state': state, 'zip': zip_code}

            census_entry = {
                'unique_id': unit.geocoding_id,
                'street': parsed.get('street', ''),
                'city': parsed.get('city', ''),
                'state': parsed.get('state', ''),
                'zip': parsed.get('zip', '')
            }
            census_data.append(census_entry)

        try:
            geocoded_results = batch_geocode(census_data, return_type='locations')  # Assumes library returns list of dicts in order
            for res, unit in zip(geocoded_results, batch):
                if res.get('is_match', False):  # Library-specific key; adjust if needed
                    lat = res.get('latitude')  # Or res['coordinates']['y']
                    lon = res.get('longitude')  # Or res['coordinates']['x']
                    matched = res.get('matched_address', '')
                    ctx = self._apply_successful_geocode(unit, lat, lon, "Match:Census", matched, now)
                    results.append((True, ctx))
                else:
                    # Update attempt count even on failure
                    unit.data['attempt_count'] = unit.attempt_count + 1  # Mutate for downstream
                    results.append((False, unit))
        except Exception as e:
            log_error(f"Census batch failed: {e}")
            for unit in batch:
                unit.data['attempt_count'] = unit.attempt_count + 1
                results.append((False, unit))

        return results
    
    def _geocode_with_google_maps(self, address_str, normalized_address=None):
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
            url = f"https://maps.googleapis.com/maps/api/geocode/json?address={quote(address_str)}&key={key}"
            r = requests.get(url, timeout=10)
            data = r.json()
            if data.get('status') == 'OK' and data.get('results'):
                loc = data['results'][0]['geometry']['location']
                return {'match': True, 'lat': loc['lat'], 'lon': loc['lng'], 'formatted_address': data['results'][0]['formatted_address']}
        except: pass
        return None

    def _geocode_with_opencage(self, address_str, normalized_address=None):
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
            if isinstance(results, list) and results:
                r = results[0]
            elif isinstance(results, dict) and 'results' in results and isinstance(results['results'], list) and results['results']:
                r = results['results'][0]
            else:
                return None
            if isinstance(r, dict) and 'geometry' in r and isinstance(r['geometry'], dict) and 'lat' in r['geometry'] and 'lng' in r['geometry']:
                return {'match': True, 'lat': r['geometry']['lat'], 'lon': r['geometry']['lng'], 'formatted_address': r.get('formatted', address_str)}
            else:
                return None
        except: pass
        return None
    
    def _geocode_with_librestreet(self, address_str, normalized_address=None):

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
            url = f"https://librestreet.org/search.php?q={quote(address_str)}&format=json"
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
            url = f"https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q={quote(query)}"
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
        
    # Minor update to _build_grok_prompt to use GeocodingWorkUnit
    def _build_grok_prompt(self, units: List[GeocodingWorkUnit]) -> str:
        lines = []
        for i, unit in enumerate(units, 1):
            canon = unit.canonical_address.strip()
            norm = unit.normalized_address  # Raw
            if isinstance(norm, dict):
                norm_str = json.dumps(norm, separators=(',', ':'))
            else:
                norm_str = str(norm)
            lines.append(f"{i}. Raw: \"{canon}\" | JSON: {norm_str}"))

        return f"""You are the world's best geocoder for broken IRS 990 nonprofit addresses.

Here is every address exactly as stored in the database — some have clean parsed JSON, some have garbage, some have OCR errors, some have C/O junk.

Your job: figure out the real physical location anyway.

CRITICAL FORMATTING INSTRUCTION — YOU MUST OBEY THIS EXACTLY:

Return ONE AND ONLY ONE ONLY JSON message containing the COMPLETE array for ALL addresses in this request.

It must be a single valid JSON array that starts with [ and ends with ] with no text before or after.

Correct example:
[{{"id":1,"lat":null,"long":null,"matched_address":null,"reason":null}},
{{"id":2,"lat":35.7796,"long":-78.6382,"matched_address":"4300 Six Forks Rd, Raleigh, NC 27609","reason":null}}]
Do NOT stream. Do NOT explain. Do NOT use markdown.

Rules:
- Ignore C/O, c/o, Attn, "See Statement", "Unknown"
- Expand: Ma=Madison Ave, Fith=Fifth, Hami=Hamilton, Po=Point, Finl Plz=Financial Plaza
- Known entities → use real HQ (First Citizens → Raleigh, GMA → Boston, etc.)
- Personal name + city/state → assume office or trustee home
- Return lat/long if ≥75% confident
- If 75–89% confident, add "reason": "best match from name+title+ZIP"
- When you find a real street address (not just city), always return it in matched_address.

Addresses:
{"\n".join(lines)}

Return ONE AND ONLY ONE JSON message containing the COMPLETE array for ALL {len(lines)} addresses.

Return the complete JSON array now. Nothing else.
"""


    

    def _final_fail_handler(self, batch: List[WorkUnit]) -> List[tuple[bool, WorkUnit]]:
            """
            Final failure stage — called when an item fails all previous stages.
            Builds a PendingDatabaseContext with No_Match for all items.
            Returns (True, ctx) so pipeline saves it.
            """
            ctx = PendingDatabaseContext()
            now = datetime.now().isoformat()

            for unit in batch:
                item = unit.data
                gid = item['geocoding_id']

                ctx.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Geocoding',
                        'updates': [{
                            'geocoding_id': gid,
                            'geocoding_status': 'No_Match',
                            'last_attempt': now,
                            'attempt_count': item.get('attempt_count', 0) + 1,
                            'matched_address': 'Failed all stages',
                        }],
                        'id_column': 'geocoding_id'
                    }
                ))

                # Progress update — each item counts
                ctx.addOperationToDatabase(DatabaseOperation(
                    operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                    data={'count': item.get('address_count', 1)}
                ))

            # Return success so pipeline saves the context
            return [(True, ctx)] * len(batch)  # one ctx for whole batch is fine

    def _get_custom_metrics(self) -> Dict[str, Any]:
        """Return live pipeline metrics for QueueStatusDisplay"""
        if not hasattr(self, 'pipeline'):
            return {'current_step': 'geolocate'}
        status = self.pipeline.get_status()
        metrics = status['metrics']
        metrics['current_step'] = 'geolocate'
        return metrics


    def get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict], Optional[str]]:
        print(f"####DEBUG: get_work_batch called with last_pk={last_pk}, batch_size={self.batch_size}")
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
        print(f"####DEBUG: got {len(rows)} rows")
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
    
    def custom_feed(self, items: List[Dict]):
        wu_items = [GeocodingWorkUnit.work_item("feed", item) for item in items]
        batch_wu = GeocodingWorkUnit.batch("feed", wu_items) if len(wu_items) > 1 else wu_items[0]
        self.pipeline.feed_stage.put(batch_wu)

    def process_pending_geocoding_records(self, max_files=None, progress_bar=None) -> int:
        self.setup_address_counts()
        print("Starting geocoding processing")
        processed = self.pipeline.run_with_provider(self, max_items=max_files)
        print(f"Geocoding complete: {processed} records processed")
        return processed

    def setup_address_counts(self):
        # Update address_count for records where it's NULL
        update_query = """
            UPDATE Geocoding
            SET address_count = (
                SELECT COUNT(*)
                FROM Addresses
                WHERE Addresses.geocoding_id = Geocoding.geocoding_id
            )
            WHERE address_count IS NULL
        """
        self.db_ops.execute_query(update_query)

    def get_total_work(self) -> int:
        query = """
            SELECT COALESCE(SUM(address_count), 0)
            FROM Geocoding
            WHERE geocoding_status IS NULL
            OR geocoding_status IN ('pending', 'owners')
        """
        result = self.db_ops.execute_query(query)
        count = result.fetchone()[0]
        return int(count)
    
    