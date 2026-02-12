#!/usr/bin/env python3
"""
geocoding_api_processor.py - Cleaned, consolidated, geopy-integrated version
Preserves all original logic + adds thread-safe geopy for supported services.
"""

import json
import re
import os
import sys
import time
import threading
import gc
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import requests
from urllib.parse import quote
from censusbatchgeocoder import geocode
from geopy.exc import GeocoderTimedOut, GeocoderServiceError, GeocoderQuotaExceeded
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim, Photon, OpenCage
from openai import OpenAI
from pydantic import BaseModel, Field

try:
    import tqdm
except ImportError:
    tqdm = None

from base_processor import BaseProcessor
from config import global_config
from constants import GEOCODING_BATCH_SIZE, CONSUMER_BATCH_SIZE
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_error, log_debug, log_warning
from models.geocoding import Geocoding
from pending_database_context import PendingDatabaseContext
from pipeline import PipelineStage, Pipeline, WorkUnit, ResultWorkUnit

# === API Configuration ===
API_CONFIG = {
    'ENABLE_CENSUS_RAW': True,
    'ENABLE_GROK': True,
    'ENABLE_PHOTON': True,
    'ENABLE_NOMINATIM': False,
    'ENABLE_LIBRESTREET': False,
    'ENABLE_OPENCAGE': False,
    'ENABLE_GOOGLE_MAPS': False,
    'ENABLE_NAME_SEARCH': False,
}

owner_mapping = {
                'charity': ('Charities', 'charity_id'),
                'grant': ('Grants', 'grant_id'),
                'contractor': ('Contractors', 'contractor_id'),
                'politicalcontribution': ('PoliticalContributions', 'political_id'),
                'political': ('PoliticalContributions', 'political_id')
            }

# === Structured Output for Grok-4 ===
class GeocodeResult(BaseModel):
    id: str = Field(..., description="1-based index in batch")
    lat: Optional[float] = None
    long: Optional[float] = None
    matched_address: Optional[str] = None
    reason: Optional[str] = None

class BatchGeocodeOutput(BaseModel):
    results: List[GeocodeResult]

@dataclass
class GeocodingWorkUnit(WorkUnit):
    _parsed_normalized: Optional[Dict[str, str]] = field(default=None, init=False)

    @property
    def geocoding_id(self) -> str: return self.data['geocoding_id']
    @property
    def normalized_address(self) -> any: return self.data['normalized_address']
    @property
    def attempt_count(self) -> int: return self.data['attempt_count']
    @property
    def canonical_address(self) -> str: return self.data['canonical_address']
    @property
    def address_count(self) -> int: return self.data.get('address_count', 0)
    @property
    def geocoding_status(self) -> str: return self.data['geocoding_status']

    @property
    def parsed_normalized(self) -> Dict[str, str]:
        if self._parsed_normalized is None:
            norm = self.normalized_address
            if isinstance(norm, str):
                try: self._parsed_normalized = json.loads(norm)
                except json.JSONDecodeError:
                    self._parsed_normalized = {}
                    log_warning(f"JSON parse fail for {self.geocoding_id}")
            elif isinstance(norm, dict): self._parsed_normalized = norm
            else: self._parsed_normalized = {}
        return self._parsed_normalized

    @classmethod
    def work_item(cls, stage: str, data: Dict) -> "GeocodingWorkUnit":
        if not isinstance(data,Dict):
            raise ValueError(f"Expected dict for work item, got {type(data)}")
        return cls(type='work', data=data, stage=stage)
    
    @classmethod
    def batch(cls, stage: str, work_units: List["WorkUnit"]) -> "GeocodingWorkUnit":
        return cls(type='batch', stage=stage, items=work_units)
    
    def __str__(self) -> str:
        if self.is_batch():
            return f"<GeoBatch {self.stage} size={len(self.items)}>"
        return f"<GeoWorkUnit {self.type} stage={self.stage}/>"


class GeocodingAPIProcessor(BaseProcessor):
    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_BATCH_SIZE):
        super().__init__(db_ops)
        self.batch_size = batch_size
        self._thread_local = threading.local()
        self.geocoding_patterns = self._load_geocoding_patterns()

        preprocess_stage = PipelineStage("preprocess", 4, 5000, self._preprocess_handler)
        census_stage_raw = PipelineStage("census", 1, 1000, self._census_handler_raw)
        census_stage_strip = PipelineStage("census_strip", 1, 1000, self._census_handler_strip)

        stages = [preprocess_stage, census_stage_raw, census_stage_strip]

        if API_CONFIG['ENABLE_GROK']:
            stages.append(PipelineStage("grok", 4, 15, self._grok_handler))
        if API_CONFIG['ENABLE_PHOTON']:
            stages.append(PipelineStage("photon", 8, 1, self._photon_handler))
        if API_CONFIG['ENABLE_NOMINATIM']:
            stages.append(PipelineStage("nominatim", 4, 1, self._nominatim_handler))
        if API_CONFIG['ENABLE_OPENCAGE']:
            stages.append(PipelineStage("opencage", 4, 1, self._opencage_handler))
        if API_CONFIG['ENABLE_LIBRESTREET']:
            stages.append(PipelineStage("librestreet", 6, 1, self._librestreet_handler))

        stages.append(PipelineStage("fail", 1, 1000, self._final_fail_handler, is_final_failure=True))

        self.pipeline: Pipeline[GeocodingWorkUnit] = Pipeline(stages=stages, db_ops=db_ops, chain_on='failure',workunit_class=GeocodingWorkUnit)

    @property
    def _geocoders(self):
        if not hasattr(self._thread_local, 'geocoders'):
            self._thread_local.geocoders = {}
        return self._thread_local.geocoders

    def _geocode_with_geopy(self, unit: GeocodingWorkUnit, geocoder_key: str, source_name: str,
                            prefer_structured: bool = True, rate_limit_min_delay: float = 0.0,
                            max_retries: int = 2) -> tuple[bool, Any]:
        if geocoder_key not in self._geocoders:
            if geocoder_key == "nominatim":
                geo = Nominatim(user_agent="irs990-geocoder/1.0")
                limiter = RateLimiter(geo.geocode, min_delay_seconds=1.1, max_retries=3)
            elif geocoder_key == "photon":
                geo = Photon()
                limiter = RateLimiter(geo.geocode, min_delay_seconds=0.4, max_retries=max_retries)
            elif geocoder_key == "opencage":
                key = os.getenv('OPENCAGE_API_KEY')
                if not key: return False, unit
                geo = OpenCage(api_key=key)
                limiter = RateLimiter(geo.geocode, min_delay_seconds=0.6, max_retries=max_retries)
            else: raise ValueError(geocoder_key)
            self._geocoders[geocoder_key] = limiter

        limiter = self._geocoders[geocoder_key]

        parsed = unit.parsed_normalized
        query = None
        if prefer_structured and geocoder_key == "nominatim" and parsed:
            query = {k: v for k, v in {
                'street': parsed.get('street', '') or parsed.get('address_line1', ''),
                'city': parsed.get('city', ''),
                'state': parsed.get('state', ''),
                'postalcode': parsed.get('zip', '')
            }.items() if v}

        if not query:
            parts = [parsed.get('street', '') or parsed.get('address_line1', ''),
                     parsed.get('city', ''), parsed.get('state', ''), parsed.get('zip', '')]
            query = ', '.join(filter(None, parts)).strip(', ') or unit.canonical_address.strip()

        if not query: return False, unit

        try:
            loc = limiter(query)
            if loc:
                return True, self._apply_successful_geocode(
                    unit, loc.latitude, loc.longitude, f"Match:{source_name}", loc.address or unit.canonical_address
                )
        except Exception as e:
            log_warning(f"{source_name} failed {unit.geocoding_id}: {e}")
        return False, unit

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
        """Strip leading C/O patterns from address strings."""
        if not address:
            log_debug(f"C/O strip: Empty address, returning as-is")
            return address
        if not re.match(r'(?i)^c/?o', address):
            log_debug(f"C/O strip: No C/O prefix found in '{address}', returning as-is")
            return address
        log_debug(f"C/O strip: Processing C/O address '{address}'")
        if "Tull Charitable Foundation" in address:
            print(f"DEBUG_TULL: C/O stripping initiated for '{address}'")
        comma_pattern = r'^(?:c/?o,?)\s*[^,\d]*,\s*'
        if re.search(comma_pattern, address, re.IGNORECASE):
            result = re.sub(comma_pattern, '', address, flags=re.IGNORECASE).strip()
            log_debug(f"C/O strip: Used comma pattern, result: '{result}'")
            if "Tull Charitable Foundation" in address:
                print(f"DEBUG_TULL: C/O stripping - comma pattern matched, result: '{result}'")
            return result
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
        """Extract entity name from C/O addresses for search-oriented queries."""
        if not address or not re.match(r'(?i)^c/?o', address):
            return ""
        comma_match = re.match(r'^(?:c/?o,?)\s*([^,\d]*),\s*(.*)', address, flags=re.IGNORECASE)
        if comma_match:
            entity = comma_match.group(1).strip()
            if entity: return entity
        digit_match = re.match(r'^(?:c/?o,?)\s*(.*?)(?=\d)', address, flags=re.IGNORECASE)
        if digit_match:
            entity = digit_match.group(1).strip()
            if entity: return entity
        no_delimiter_match = re.match(r'^(?:c/?o,?)\s*(.+)', address, flags=re.IGNORECASE)
        if no_delimiter_match:
            entity = no_delimiter_match.group(1).strip()
            entity = re.sub(r'[,.]$', '', entity).strip()
            if entity: return entity
        return ""

    def _parse_co_address(self, address: str, api_type: str, zip_code: str = "") -> str:
        """Parse C/O addresses differently based on API type."""
        if not address:
            return address
        if api_type.lower() == 'census':
            return self._strip_co_from_address(address)
        elif api_type.lower() == 'grok':
            if not re.match(r'(?i)^c/?o', address):
                return address
            comma_match = re.match(r'^(?:c/?o,?)\s*([^,\d]*),\s*(.*)', address, flags=re.IGNORECASE)
            if comma_match:
                entity = comma_match.group(1).strip()
                rest = comma_match.group(2).strip()
                zip_match = re.search(r'\b(\d{5})\b', rest)
                zip_part = zip_match.group(1) if zip_match else zip_code
                if zip_part:
                    return f"{entity}, {zip_part}"
            digit_match = re.match(r'^(?:c/?o,?)\s*(.*?)(?=\d)(.*)', address, flags=re.IGNORECASE)
            if digit_match:
                entity = digit_match.group(1).strip()
                rest = digit_match.group(2).strip()
                zip_match = re.search(r'\b(\d{5})\b', rest)
                zip_part = zip_match.group(1) if zip_match else zip_code
                if zip_part:
                    return f"{entity}, {zip_part}"
            no_delimiter_match = re.match(r'^(?:c/?o,?)\s*(.+)', address, flags=re.IGNORECASE)
            if no_delimiter_match:
                entity = no_delimiter_match.group(1).strip()
                entity = re.sub(r'[,.]$', '', entity).strip()
                if entity and zip_code:
                    return f"{entity}, {zip_code}"
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
            
            if addr_type in owner_mapping:
                table, col = owner_mapping[addr_type]
                updates.setdefault(table, []).append({col: str(owner_id), 'colocator': colocator})
        for table, items in updates.items():
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={'table': table, 'updates': items, 'id_column': list(items[0].keys())[0]}
            ))

    def _apply_successful_geocode(self, unit: GeocodingWorkUnit, lat: float, lon: float, status: str, matched: str) -> ResultWorkUnit:
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
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Addresses', 'set_clause': 'colocator = ?', 'where_clause': 'geocoding_id = ?', 'params': (colocator, gid)}
        ))
        self._update_owner_colocators(ctx, gid, colocator)
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': item.get('address_count', 1)}
        ))
        return  ResultWorkUnit.result("result", ctx)

    def _preprocess_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        results = []
        now = datetime.now().isoformat()

        for unit in batch:
            addr = unit.canonical_address
            gid = unit.geocoding_id
            zip_code = unit.parsed_normalized.get('zip', '')

           

            pattern = self._check_geocoding_patterns(addr, zip_code, 'safe')
            if pattern:
                if pattern['status' ]== 'owners':
                    ctx = PendingDatabaseContext()
                    self._update_owner_colocators(ctx,unit.data['geocoding_id'], pattern['colocator'])
                    results.append((True, Pipeline.result("result", ctx)))
                    continue
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
                results.append((True, ResultWorkUnit.result("result", ctx)))
                continue

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
                unit.data['canonical_address'] = stripped
                results.append((False, unit))
            else:
                results.append((False, unit))

        return results
    
    def _census_handler_raw(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return self._census_handler(batch, do_strip=False)
        
    def _census_handler_strip(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return self._census_handler(batch, do_strip=True)

    def _census_handler(self, batch: List[GeocodingWorkUnit], do_strip=False) -> List[tuple[bool, GeocodingWorkUnit]]:
        results = []
        census_data = []
        now = datetime.now().isoformat()

        for unit in batch:
            parsed = unit.parsed_normalized.copy()
            # Optional in-memory C/O strip for stripped path
            if do_strip:
                stripped_addr = self._strip_co_from_address(unit.canonical_address)
                # Update parsed street if present (most important field)
                if 'street' in parsed:
                    parsed['street'] = self._strip_co_from_address(parsed['street'])
                # Optionally log or track that we stripped
                log_debug(f"Census stripped path: {unit.geocoding_id} → stripped canonical")
            if not parsed:
                log_warning(f"Census fallback to canonical {unit.geocoding_id}")
                parts = unit.canonical_address.split(', ')
                street = ', '.join(parts[:-3]) if len(parts) > 3 else ''
                city = parts[-3] if len(parts) > 2 else ''
                state = parts[-2] if len(parts) > 1 else ''
                zip_code = parts[-1] if parts else ''
                parsed = {'street': street, 'city': city, 'state': state, 'zip': zip_code}

            census_entry = {
                'id': unit.geocoding_id,
                'address': parsed.get('street', ''),
                'city': parsed.get('city', ''),
                'state': parsed.get('state', ''),
                'zipcode': parsed.get('zip', '')
            }
            census_data.append(census_entry)

        try:
            geocoded_results = geocode(census_data, return_type='locations')
            for res, unit in zip(geocoded_results, batch):
                if res.get('is_match',  "No_Match") != "No_Match" and res.get('latitude',"No_Match"):
                    lat = res.get('latitude')
                    lon = res.get('longitude')
                    matched = res.get('matched_address', '')
                    result_unit = self._apply_successful_geocode(unit, lat, lon, "Match:Census", matched)
                    results.append((True, result_unit))
                else:
                    unit.data['attempt_count'] = unit.attempt_count + 1
                    results.append((False, unit))
        except Exception as e:
            log_error(f"Census failed: {e}")
            for unit in batch:
                unit.data['attempt_count'] = unit.attempt_count + 1
                results.append((False, unit))
        return results

    def _grok_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        if not batch: return []

        client = OpenAI(api_key=os.getenv("X_API_KEY"), base_url="https://api.x.ai/v1")

        items = batch
        prompt_lines = []
        for i, unit in enumerate(items, 1):
            canon = unit.canonical_address.strip()
            norm_str = json.dumps(unit.parsed_normalized, separators=(',', ':')) if unit.parsed_normalized else ""
            prompt_lines.append(f"ID: {unit.geocoding_id} | Raw: \"{canon}\" | Parsed JSON: {norm_str}")
        system_prompt = """You are an expert geocoder for messy IRS 990 nonprofit addresses.
Ignore C/O, Attn:, See Statement, personal names unless tied to known org.
Use known entity HQs when obvious (e.g. First Citizens → Raleigh area).
Return lat/long only if ≥75% confident in a real street location."""

        user_prompt = f"""Analyze these addresses and geocode them:

        {'\n'.join(prompt_lines)}

        CRITICAL: For each result, set 'id' to the EXACT geocoding_id UUID shown at the start of the line (e.g. "ID: 550e8400-e29b-41d4-a716-446655440000 | ...").

        Rules recap:
        - Ignore C/O, Attn:, See Statement, personal names unless tied to known org.
        - Use known entity HQs when obvious (e.g. First Citizens → Raleigh area).
        - Return lat/long only if ≥75% confident in a real street location.

        For each, provide:
        - id: the geocoding_id UUID string (required, exact match)
        - lat / long: float or null
        - matched_address: best full address or null
        - reason: optional short note if partial match

        Output ONLY the structured results array."""
        try:
            schema = BatchGeocodeOutput.model_json_schema()
            response = client.chat.completions.create(
                model="grok-4-1-fast",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "batch_geocode", "strict": True, "schema": schema}
                },
                temperature=0.0,
                max_tokens=4000,
                timeout=300
            )
            raw = response.choices[0].message.content.strip()
            parsed = BatchGeocodeOutput.model_validate_json(raw)

            results = []
            parsed_dict = {res.id: res for res in parsed.results}  # map by UUID
            for unit, res_item in zip(items, parsed.results):
                res_item = parsed_dict.get(f"{unit.geocoding_id}", None)
                if res_item and res_item.lat is not None and res_item.long is not None:
                    result_unit = self._apply_successful_geocode(
                        unit, res_item.lat, res_item.long, "Match:Grok-4", res_item.matched_address or unit.canonical_address
                    )
                    results.append((True, result_unit))
                else:
                    results.append((False, unit))
            return results
        except Exception as e:
            log_error(f"Grok failed: {e}")
            return [(False, unit) for unit in batch]

    def _photon_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return [self._geocode_with_geopy(u, "photon", "Photon", prefer_structured=False) for u in batch]

    def _nominatim_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return [self._geocode_with_geopy(u, "nominatim", "Nominatim", prefer_structured=True) for u in batch]

    def _opencage_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return [self._geocode_with_geopy(u, "opencage", "OpenCage", prefer_structured=False) for u in batch]

    def _librestreet_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        results = []
        for unit in batch:
            parsed = unit.parsed_normalized
            address_str = unit.canonical_address
            if parsed:
                street = parsed.get('street', '')
                city = parsed.get('city', '')
                state = parsed.get('state', '')
                zip_code = parsed.get('zip', '')
                constructed = f"{street}, {city}, {state} {zip_code}".strip(', ')
                if constructed: address_str = constructed

            try:
                url = f"https://librestreet.org/search.php?q={quote(address_str)}&format=json"
                r = requests.get(url, timeout=10)
                data = r.json()
                if data:
                    loc = data[0]
                    lat = float(loc['lat'])
                    lon = float(loc['lon'])
                    matched = loc['display_name']
                    result_unit = self._apply_successful_geocode(unit, lat, lon, "Match:LibreStreet", matched)
                    results.append((True, result_unit))
                    continue
            except Exception as e:
                log_warning(f"LibreStreet failed {unit.geocoding_id}: {e}")
            results.append((False, unit))
        return results

    def _final_fail_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
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
            result = ResultWorkUnit(ctx,stage='fail')
        return [(True,             result)] #only need one

    def get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[GeocodingWorkUnit], Optional[str]]:
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

        work_units = []
        new_pk = last_pk

        for row in rows:
            data = {
                'geocoding_id': row[0],
                'normalized_address': row[1],
                'attempt_count': row[2],
                'canonical_address': row[3],
                'address_count': row[4] or 0,
                'geocoding_status': row[5]
            }
            wu = GeocodingWorkUnit.work_item("feed", data)
            work_units.append(wu)
            new_pk = row[0]

        return work_units, new_pk    
    
    def get_total_work(self) -> int:
        query = """
            SELECT COALESCE(SUM(address_count), 0)
            FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
        """
        result = self.db_ops.execute_query(query)
        return int(result.fetchone()[0])

    def get_work_count(self, max_files=None) -> int:
        base_query = """
            SELECT COALESCE(SUM(address_count), 0)
            FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
        """
        if max_files is None:
            result = self.db_ops.execute_query(base_query)
            return result.fetchone()[0]
        query = f"""
            SELECT COALESCE(SUM(address_count), 0)
            FROM (
                SELECT address_count
                FROM Geocoding
                WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
                ORDER BY geocoding_id
                LIMIT {max_files}
            ) sub
        """
        result = self.db_ops.execute_query(query)
        return result.fetchone()[0]

    def get_progress_config(self, max_files=None):
        total = self.get_work_count(max_files=(max_files or global_config.max_files))
        return total, "addresses", "Geocoding addresses"

    def process_pending_geocoding_records(self, max_files=None, progress_bar=None) -> int:
        self.setup_address_counts()
        print("Starting geocoding processing")
        processed = self.pipeline.run_with_provider(self, max_items=max_files)
        print(f"Geocoding complete: {processed} records processed")
        return processed

    def setup_address_counts(self):
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

    def _get_custom_metrics(self) -> Dict[str, Any]:
        if not hasattr(self, 'pipeline'):
            return {'current_step': 'geolocate'}
        status = self.pipeline.get_status()
        metrics = status['metrics']
        metrics['current_step'] = 'geolocate'
        return metrics