#!/usr/bin/env python3
"""
geocoding_api_processor.py - Cleaned, consolidated, geopy-integrated version
Preserves all original logic + adds thread-safe geopy for supported services.
"""

import json
import logging
import re
import os
import sys
import time
import threading
import gc
import gzip
import csv
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import requests
from urllib.parse import quote
from censusbatchgeocoder import geocode
from geopy.exc import (
    GeocoderTimedOut, GeocoderServiceError, GeocoderQuotaExceeded,
    GeocoderAuthenticationFailure,
)
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim, Photon, OpenCage
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

try:
    import tqdm
except ImportError:
    tqdm = None

from base_processor import BaseProcessor
from config import global_config
from constants import (
    GEOCODING_BATCH_SIZE, GEOCODING_FEED_BATCH_SIZE, GEOCODING_API_WORKERS,
    CENSUS_API_BATCH_SIZE, GEOCODE_CONSUMER_BATCH_SIZE, GEOCODE_CHECKPOINT_INTERVAL,
    GEOCODING_IN_FLIGHT_CAP, GEOCODING_GROK_WORKERS, GEOCODING_GROK_BATCH_SIZE,
    GROK_GEOCODE_MODEL_DEFAULT, GROK_FAILURE_CODES, grok_failure_status,
    GEOCODE_MAPS_CO_WORKERS, GEOCODE_MAPS_CO_MIN_DELAY,
    GEOCODING_PHOTON_WORKERS, GEOCODING_PHOTON_MIN_DELAY,
    GEOCODING_PHOTON_SELF_HOSTED_WORKERS, GEOCODING_PHOTON_SELF_HOSTED_MIN_DELAY,
    DEFAULT_FINAL_DIR,
)
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_error, log_debug, log_warning
from models.geocoding import Geocoding
from pending_database_context import PendingDatabaseContext
from pipeline import PipelineStage, Pipeline, WorkUnit, ResultWorkUnit
import os

archive_path = os.path.join(os.path.dirname(DEFAULT_FINAL_DIR), 'geocode_archive_distinct.tsv.gz')

# geopy RateLimiter logs full tracebacks on retries — keep at ERROR
for _geopy_logger in ('geopy', 'geopy.geocoders', 'geopy.extra.rate_limiter'):
    logging.getLogger(_geopy_logger).setLevel(logging.ERROR)

# === API Configuration ===
API_CONFIG = {
    'ENABLE_CENSUS_RAW': True,
    'ENABLE_GROK': False,  # inline Grok removed — use geolocate_grok step (batch API)
    'ENABLE_PHOTON': True,
    'ENABLE_NOMINATIM': True,
    'ENABLE_GEOCODE_MAPS_CO': True,  # parallel Nominatim-class stage when key present; probe disables on 401

    'ENABLE_LIBRESTREET': False,  # librestreet.org NXDOMAIN — was 4.4k wasted connection attempts/run
    'ENABLE_OPENCAGE': True,
    'ENABLE_GOOGLE_MAPS': False,
    'ENABLE_NAME_SEARCH': False,
}

owner_mapping = {
                'charity': ('Charities', 'charity_id'),
                'filer': ('Charities', 'charity_id'),
                'grant': ('Grants', 'grant_id'),
                'contractor': ('Contractors', 'contractor_id'),
                'officer': ('Officers', 'officer_id'),
                'politicalcontribution': ('PoliticalContributions', 'political_id'),
                'political': ('PoliticalContributions', 'political_id')
            }

# === Structured Output for Grok-4 ===
class GeocodeResult(BaseModel):
    id: str = Field(..., description="geocoding_id UUID from the prompt")
    lat: Optional[float] = None
    long: Optional[float] = None
    matched_address: Optional[str] = None
    failure_code: Optional[str] = Field(
        None,
        description="Required when lat/long are null. One of: NOTA, VAGUE, AMBIG, REDACT, UNKN",
    )
    reason: Optional[str] = Field(None, description="Brief explanation of match or failure")

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


@dataclass
class GeocodeRunStats:
    """Per-run counters for geolocate_new diagnostics."""
    fed: int = 0
    preprocess_match: int = 0
    census_match: int = 0
    census_strip_match: int = 0
    other_match: int = 0
    grok_queued: int = 0
    grok_match: int = 0
    grok_fail: int = 0
    save_errors: int = 0

    def log_summary(self, step: str = "geolocate_new"):
        log_info(
            f"Geocode run summary ({step}): fed={self.fed:,} preprocess={self.preprocess_match:,} "
            f"census={self.census_match:,} census_strip={self.census_strip_match:,} "
            f"other={self.other_match:,} grok_queued={self.grok_queued:,} "
            f"grok_match={self.grok_match:,} grok_fail={self.grok_fail:,} save_errors={self.save_errors:,}"
        )
        print(
            f"[{step}] SUMMARY fed={self.fed:,} preprocess={self.preprocess_match:,} "
            f"census={self.census_match:,} census_strip={self.census_strip_match:,} "
            f"other={self.other_match:,} grok_queued={self.grok_queued:,} "
            f"grok_match={self.grok_match:,} grok_fail={self.grok_fail:,} save_errors={self.save_errors:,}",
            flush=True,
        )


class GeocodingAPIProcessor(BaseProcessor):
    _maps_co_disabled = False
    _librestreet_disabled = False

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = GEOCODING_FEED_BATCH_SIZE):
        super().__init__(db_ops)
        self.batch_size = batch_size
        self._thread_local = threading.local()
        self.geocoding_patterns = self._load_geocoding_patterns()
        self.run_stats = GeocodeRunStats()
        self.pipeline_step = "geolocate_new"
        self._validate_geocoder_api_keys()

        self.free_pipeline = self._build_free_pipeline(db_ops)
        self.grok_pipeline = self._build_grok_pipeline(db_ops)
        self.pipeline = self.free_pipeline
        self.free_pipeline._geocode_processor = self
        self.grok_pipeline._geocode_processor = self

    def _build_free_pipeline(self, db_ops: DatabaseOperations) -> Pipeline[GeocodingWorkUnit]:
        preprocess_stage = PipelineStage("preprocess", 4, 5000, self._preprocess_handler)
        census_stage_raw = PipelineStage(
            "census", GEOCODING_API_WORKERS, CENSUS_API_BATCH_SIZE, self._census_handler_raw,
        )
        census_stage_strip = PipelineStage(
            "census_strip", GEOCODING_API_WORKERS, CENSUS_API_BATCH_SIZE, self._census_handler_strip,
        )
        stages = [preprocess_stage, census_stage_raw, census_stage_strip]
        if API_CONFIG['ENABLE_PHOTON']:
            photon_workers, _ = self._photon_runtime_settings()
            stages.append(PipelineStage("photon", photon_workers, 1, self._photon_handler))
        if API_CONFIG['ENABLE_GEOCODE_MAPS_CO'] and not GeocodingAPIProcessor._maps_co_disabled:
            stages.append(PipelineStage(
                "geocode_maps_co", GEOCODE_MAPS_CO_WORKERS, 1, self._geocode_maps_co_handler,
            ))
        elif API_CONFIG['ENABLE_NOMINATIM']:
            stages.append(PipelineStage("nominatim", 4, 1, self._nominatim_handler))
        if API_CONFIG['ENABLE_OPENCAGE']:
            stages.append(PipelineStage("opencage", 8, 1, self._opencage_handler))
        if API_CONFIG['ENABLE_LIBRESTREET'] and not GeocodingAPIProcessor._librestreet_disabled:
            stages.append(PipelineStage("librestreet", 6, 1, self._librestreet_handler))
        stages.append(PipelineStage(
            "fail", 1, 5000, self._grok_pending_fail_handler, is_final_failure=True,
        ))
        return Pipeline(
            stages=stages,
            db_ops=db_ops,
            chain_on='failure',
            workunit_class=GeocodingWorkUnit,
            consumer_threshold=GEOCODE_CONSUMER_BATCH_SIZE,
            checkpoint_interval=GEOCODE_CHECKPOINT_INTERVAL,
            backpressure_enabled=True,
            in_flight_cap=GEOCODING_IN_FLIGHT_CAP,
        )

    def _build_grok_pipeline(self, db_ops: DatabaseOperations) -> Pipeline[GeocodingWorkUnit]:
        stages = [
            PipelineStage(
                "grok", GEOCODING_GROK_WORKERS, GEOCODING_GROK_BATCH_SIZE, self._grok_handler,
            ),
            PipelineStage("fail", 1, 5000, self._grok_final_fail_handler, is_final_failure=True),
        ]
        return Pipeline(
            stages=stages,
            db_ops=db_ops,
            chain_on='failure',
            workunit_class=GeocodingWorkUnit,
            consumer_threshold=GEOCODE_CONSUMER_BATCH_SIZE,
            checkpoint_interval=GEOCODE_CHECKPOINT_INTERVAL,
            backpressure_enabled=True,
            in_flight_cap=GEOCODING_IN_FLIGHT_CAP,
        )

    @property
    def _geocoders(self):
        if not hasattr(self._thread_local, 'geocoders'):
            self._thread_local.geocoders = {}
        return self._thread_local.geocoders

    def _photon_runtime_settings(self) -> tuple[int, float]:
        domain = (os.getenv("PHOTON_DOMAIN") or "photon.komoot.io").strip().rstrip("/")
        self_hosted = domain not in ("photon.komoot.io", "photon.komoot.de")
        if self_hosted:
            return GEOCODING_PHOTON_SELF_HOSTED_WORKERS, GEOCODING_PHOTON_SELF_HOSTED_MIN_DELAY
        return GEOCODING_PHOTON_WORKERS, GEOCODING_PHOTON_MIN_DELAY

    def _probe_photon(self) -> None:
        domain = (os.getenv("PHOTON_DOMAIN") or "").strip().rstrip("/")
        if not domain or domain in ("photon.komoot.io", "photon.komoot.de"):
            return
        scheme = os.getenv("PHOTON_SCHEME", "http").strip() or "http"
        workers, delay = self._photon_runtime_settings()
        try:
            r = requests.get(
                f"{scheme}://{domain}/api",
                params={"q": "1600 Pennsylvania Ave NW Washington DC"},
                timeout=10,
            )
            if r.status_code != 200:
                log_warning(f"Photon probe {domain} returned HTTP {r.status_code}")
                print(f"[geolocate_new] Photon self-hosted probe HTTP {r.status_code} — stage enabled anyway", flush=True)
            else:
                print(
                    f"[geolocate_new] Photon self-hosted {scheme}://{domain} "
                    f"({workers} workers, {delay}s delay)",
                    flush=True,
                )
        except Exception as e:
            log_warning(f"Photon probe failed for {domain}: {e}")
            print(f"[geolocate_new] Photon self-hosted probe failed ({e}) — stage enabled anyway", flush=True)

    def _validate_geocoder_api_keys(self):
        self._probe_photon()
        self._probe_geocode_maps_co()
        self._probe_librestreet()

    def _geocode_maps_co_api_key(self) -> Optional[str]:
        return os.getenv('GEOCODE_MAPS_API_KEY') or os.getenv('GEOCODE_MAPS_CO_KEY')

    def _maps_co_search(self, params: Dict[str, str]) -> Optional[dict]:
        maps_key = self._geocode_maps_co_api_key()
        if not maps_key:
            return None
        query = dict(params)
        query['api_key'] = maps_key
        query.setdefault('format', 'jsonv2')
        r = requests.get("https://geocode.maps.co/search", params=query, timeout=15)
        if r.status_code == 401:
            raise GeocoderAuthenticationFailure("geocode.maps.co API key unauthorized")
        if r.status_code == 429:
            raise GeocoderQuotaExceeded("geocode.maps.co rate limit")
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
        return None

    def _probe_geocode_maps_co(self) -> None:
        maps_key = self._geocode_maps_co_api_key()
        if not maps_key:
            GeocodingAPIProcessor._maps_co_disabled = True
            API_CONFIG['ENABLE_GEOCODE_MAPS_CO'] = False
            log_info("geocode.maps.co disabled: GEOCODE_MAPS_API_KEY not set")
            print("[geolocate_new] geocode.maps.co disabled (no API key) — skipping stage", flush=True)
            return
        try:
            hit = self._maps_co_search({
                'street': '1600 Pennsylvania Ave NW',
                'city': 'Washington',
                'state': 'DC',
                'postalcode': '20500',
                'country': 'US',
            })
            if not hit:
                log_warning("geocode.maps.co probe returned no results — stage enabled anyway")
            API_CONFIG['ENABLE_NOMINATIM'] = False
            print(
                f"[geolocate_new] geocode.maps.co enabled "
                f"({GEOCODE_MAPS_CO_WORKERS} workers, ~{GEOCODE_MAPS_CO_WORKERS} req/sec) — free Nominatim skipped",
                flush=True,
            )
        except GeocoderAuthenticationFailure:
            GeocodingAPIProcessor._maps_co_disabled = True
            API_CONFIG['ENABLE_GEOCODE_MAPS_CO'] = False
            log_warning("geocode.maps.co disabled: API key returned 401 Unauthorized")
            print("[geolocate_new] geocode.maps.co disabled (401) — skipping stage", flush=True)
        except Exception as e:
            log_debug(f"geocode.maps.co probe inconclusive: {e}")

    def _probe_librestreet(self) -> None:
        """librestreet.org is NXDOMAIN as of 2026-06 — skip stage to avoid per-address connection retries."""
        try:
            r = requests.head("https://librestreet.org/search.php", timeout=5)
            if r.status_code >= 500:
                raise GeocoderServiceError(f"HTTP {r.status_code}")
        except Exception as e:
            GeocodingAPIProcessor._librestreet_disabled = True
            API_CONFIG['ENABLE_LIBRESTREET'] = False
            log_warning(f"LibreStreet disabled: service unreachable ({e})")
            print(f"[geolocate_new] LibreStreet disabled (unreachable) — skipping stage", flush=True)

    def _geocode_with_geopy(self, unit: GeocodingWorkUnit, geocoder_key: str, source_name: str,
                            prefer_structured: bool = True, rate_limit_min_delay: float = 0.0,
                            max_retries: int = 2) -> tuple[bool, Any]:
        if geocoder_key not in self._geocoders:
            if geocoder_key == "nominatim":
                geo = Nominatim(user_agent="irs990-geocoder/1.0")
                limiter = RateLimiter(geo.geocode, min_delay_seconds=1.1, max_retries=3)
            elif geocoder_key == "photon":
                domain = (os.getenv("PHOTON_DOMAIN") or "photon.komoot.io").strip().rstrip("/")
                scheme = os.getenv("PHOTON_SCHEME")
                if not scheme:
                    scheme = "http" if domain not in ("photon.komoot.io", "photon.komoot.de") else "https"
                _, photon_delay = self._photon_runtime_settings()
                geo = Photon(domain=domain, scheme=scheme)
                limiter = RateLimiter(geo.geocode, min_delay_seconds=photon_delay, max_retries=max_retries)
            elif geocoder_key == "geocode_maps_co":
                if GeocodingAPIProcessor._maps_co_disabled:
                    return (False, unit)
                if not self._geocode_maps_co_api_key():
                    return (False, unit)
                limiter = RateLimiter(
                    self._maps_co_search,
                    min_delay_seconds=GEOCODE_MAPS_CO_MIN_DELAY,
                    max_retries=0,
                )
            elif geocoder_key == "opencage":
                opencage_key = os.getenv('OPENCAGE_API_KEY')
                if not opencage_key:
                    return (False, unit)
                geo = OpenCage(api_key=opencage_key)
                limiter = RateLimiter(geo.geocode, min_delay_seconds=0.6, max_retries=max_retries)
            else: raise ValueError(geocoder_key)
            self._geocoders[geocoder_key] = limiter
            
        

        limiter = self._geocoders[geocoder_key]

        parsed = unit.parsed_normalized
        query = None
        if geocoder_key == "geocode_maps_co":
            if prefer_structured and parsed:
                query = {k: v for k, v in {
                    'street': parsed.get('street', '') or parsed.get('address_line1', ''),
                    'city': parsed.get('city', ''),
                    'state': parsed.get('state', ''),
                    'postalcode': parsed.get('zip', ''),
                    'country': 'US',
                }.items() if v}
            if not query:
                parts = [parsed.get('street', '') or parsed.get('address_line1', ''),
                         parsed.get('city', ''), parsed.get('state', ''), parsed.get('zip', '')]
                freeform = ', '.join(filter(None, parts)).strip(', ') or unit.canonical_address.strip()
                query = {'q': freeform} if freeform else None
        elif prefer_structured and geocoder_key == "nominatim" and parsed:
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

        if not query:
            return False, unit

        try:
            if geocoder_key == "geocode_maps_co":
                hit = limiter(query if isinstance(query, dict) else {'q': query})
                if hit:
                    matched = hit.get('display_name') or unit.canonical_address
                    return True, self._apply_successful_geocode(
                        unit, float(hit['lat']), float(hit['lon']),
                        f"Match:{source_name}", matched,
                    )
            else:
                loc = limiter(query)
                if loc:
                    return True, self._apply_successful_geocode(
                        unit, loc.latitude, loc.longitude, f"Match:{source_name}", loc.address or unit.canonical_address
                    )
        except GeocoderAuthenticationFailure:
            if geocoder_key == "geocode_maps_co":
                GeocodingAPIProcessor._maps_co_disabled = True
                log_warning("geocode.maps.co disabled after 401 — skipping remaining lookups")
            else:
                log_warning(f"{source_name} auth failed {unit.geocoding_id}")
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
            return address
        if not re.match(r'(?i)^c/?o', address):
            return address
        log_debug(f"C/O strip: {address[:80]}")
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
        safe_names = [
            'privacy_addresses', 'various_addresses', 'incomplete_addresses', 'no_street_number',
            'mail_drop_addresses', 'rural_route', 'exempt_addresses',
        ]
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
                if '{box}' in colocator:
                    box_m = re.search(r'(?i)p\.?o\.?\s*box\s*#?\s*(\d+)', canonical_address)
                    if box_m:
                        colocator = colocator.replace('{box}', box_m.group(1))
                status = 'owners' if colocator else pattern.get('status', 'pending')
                return {
                    'colocator': colocator,
                    'status': status,
                    'action': pattern.get('action', 'match'),
                    'regex': regex,
                    'fields': pattern.get('fields')
                }
        return None
    
    def _update_geocoding_status(self,ctx: PendingDatabaseContext, unit: GeocodingWorkUnit, geocoding_id: str, status: str):
        now = datetime.now().isoformat()
        item = unit.data
        update = {
            'geocoding_id': geocoding_id,
            'last_attempt': now,
            'attempt_count': item.get('attempt_count', 0) + 1,
            'geocoding_status': status,
        }
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [update], 'id_column': 'geocoding_id'}
        ))
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': item.get('address_count', 1)}
        ))

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

    def _apply_geocoding_only_update(
        self,
        unit: GeocodingWorkUnit,
        status: str,
        *,
        colocator: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        matched: Optional[str] = None,
    ) -> ResultWorkUnit:
        """Persist match on Geocoding only — defer Addresses/owner propagation (OOM + index bug)."""
        item = unit.data
        ctx = PendingDatabaseContext()
        now = datetime.now().isoformat()
        gid = item['geocoding_id']
        update: Dict[str, Any] = {
            'geocoding_id': gid,
            'last_attempt': now,
            'attempt_count': item.get('attempt_count', 0) + 1,
            'geocoding_status': status,
            'matched_address': matched or item['canonical_address'],
        }
        if colocator is not None:
            update['colocator'] = colocator
        if lat is not None:
            update['latitude'] = round(lat, 4)
        if lon is not None:
            update['longitude'] = round(lon, 4)
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [update], 'id_column': 'geocoding_id'}
        ))
        ctx.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': item.get('address_count', 1)}
        ))
        return ResultWorkUnit.result("result", ctx)

    def _apply_successful_geocode(self, unit: GeocodingWorkUnit, lat: float, lon: float, status: str, matched: str) -> ResultWorkUnit:
        if status.startswith('Match:Grok'):
            self.run_stats.grok_match += 1
        elif status not in ('Match:Census', 'Match:Census_Strip'):
            self.run_stats.other_match += 1
        rlat = round(lat, 4)
        rlon = round(lon, 4)
        return self._apply_geocoding_only_update(
            unit, status,
            colocator=f"LL:{rlat}:{rlon}",
            lat=rlat, lon=rlon,
            matched=matched or unit.canonical_address,
        )

    def _apply_po_box_match(self, unit: GeocodingWorkUnit, po_box: str, zip5: str) -> ResultWorkUnit:
        colocator = f"PO:{po_box}:{zip5}"
        lat = lon = None
        try:
            row = self.db_ops.execute_query(
                "SELECT lat, lon FROM Zips WHERE zip = ? LIMIT 1", (zip5,)
            ).fetchone()
            if row:
                lat, lon = row[0], row[1]
        except Exception:
            pass
        return self._apply_geocoding_only_update(
            unit, 'Match:PO',
            colocator=colocator,
            lat=lat, lon=lon,
            matched=unit.canonical_address,
        )

    def _preprocess_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        results = []
        now = datetime.now().isoformat()

        for unit in batch:
            addr = unit.canonical_address
            gid = unit.geocoding_id
            parsed = unit.parsed_normalized
            zip_code = (parsed.get('zip') or '')[:5]

            po_box = parsed.get('po_box')
            if not po_box:
                m = re.search(r'(?i)p\.?o\.?\s*box\s*#?\s*(\d+)', addr)
                if m:
                    po_box = m.group(1)
            if po_box and zip_code:
                results.append((True, self._apply_po_box_match(unit, str(po_box), zip_code)))
                self.run_stats.preprocess_match += 1
                continue

            pattern = self._check_geocoding_patterns(addr, zip_code, 'safe')
            if pattern:
                if pattern['status'] == 'owners':
                    colocator = pattern.get('colocator', '')
                    if '{box}' in colocator and po_box:
                        colocator = colocator.replace('{box}', str(po_box))
                    results.append((True, self._apply_geocoding_only_update(
                        unit, 'Match:PatternOwners', colocator=colocator or None,
                        matched=addr,
                    )))
                    self.run_stats.preprocess_match += 1
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
                self.run_stats.preprocess_match += 1
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
            source_addr = unit.canonical_address
            if do_strip:
                source_addr = self._strip_co_from_address(unit.canonical_address)
                if parsed.get('street'):
                    parsed['street'] = self._strip_co_from_address(parsed['street'])
            if not parsed:
                log_warning(f"Census fallback to canonical {unit.geocoding_id}")
                parts = source_addr.split(', ')
                street = ', '.join(parts[:-3]) if len(parts) > 3 else source_addr
                city = parts[-3] if len(parts) > 2 else ''
                state = parts[-2] if len(parts) > 1 else ''
                zip_code = parts[-1] if parts else ''
                parsed = {'street': street, 'city': city, 'state': state, 'zip': zip_code}
            elif do_strip and (not parsed.get('street') or re.match(r'(?i)^c/?o', parsed.get('street', ''))):
                parts = source_addr.split(', ')
                street = ', '.join(parts[:-3]) if len(parts) > 3 else source_addr
                parsed['street'] = street
                if not parsed.get('city') and len(parts) > 2:
                    parsed['city'] = parts[-3]
                if not parsed.get('state') and len(parts) > 1:
                    parsed['state'] = parts[-2]
                if not parsed.get('zip') and parts:
                    parsed['zip'] = parts[-1]

            census_entry = {
                'id': unit.geocoding_id,
                'address': parsed.get('street', ''),
                'city': parsed.get('city', ''),
                'state': parsed.get('state', ''),
                'zipcode': parsed.get('zip', '')
            }
            census_data.append(census_entry)

        try:
            geocoded_results = geocode(
                census_data, return_type='locations', batch_size=CENSUS_API_BATCH_SIZE,
            )
            n_match = 0
            for res, unit in zip(geocoded_results, batch):
                is_match = (res.get('is_match') or '').strip()
                lat = res.get('latitude')
                if is_match and is_match != 'No_Match' and lat is not None:
                    lon = res.get('longitude')
                    matched = res.get('returned_address') or res.get('matched_address') or ''
                    status = "Match:Census_Strip" if do_strip else "Match:Census"
                    result_unit = self._apply_successful_geocode(unit, lat, lon, status, matched)
                    results.append((True, result_unit))
                    n_match += 1
                    if do_strip:
                        self.run_stats.census_strip_match += 1
                    else:
                        self.run_stats.census_match += 1
                else:
                    unit.data['attempt_count'] = unit.attempt_count + 1
                    results.append((False, unit))
            stage = "census_strip" if do_strip else "census"
            log_info(f"Census {stage}: batch={len(batch)} matched={n_match} ({100*n_match/len(batch):.0f}%)")
            print(f"[geolocate_new] {stage} batch={len(batch)} matched={n_match}/{len(batch)}", flush=True)
        except Exception as e:
            log_error(f"Census failed: {e}")
            for unit in batch:
                unit.data['attempt_count'] = unit.attempt_count + 1
                results.append((False, unit))
        return results
    
    def safe_parse(self, raw: str):
        try:
            return BatchGeocodeOutput.model_validate_json(raw)
        except ValidationError as e:
            print(f"Bad Json from grok,{raw}")
            raise  # or handle gracefully depending on your needs

    @staticmethod
    def grok_geocode_model() -> str:
        return os.getenv("GROK_GEOCODE_MODEL", GROK_GEOCODE_MODEL_DEFAULT)

    @classmethod
    def grok_result_update_fields(
        cls,
        unit: GeocodingWorkUnit,
        res_item: Optional[GeocodeResult],
        *,
        now: str,
    ) -> Dict[str, Any]:
        """Build Geocoding row fields from a Grok GeocodeResult (None → grok:UNKN)."""
        base: Dict[str, Any] = {
            "geocoding_id": unit.geocoding_id,
            "last_attempt": now,
            "attempt_count": unit.attempt_count + 1,
        }
        if res_item and res_item.lat is not None and res_item.long is not None:
            rlat = round(res_item.lat, 4)
            rlon = round(res_item.long, 4)
            return {
                **base,
                "geocoding_status": "Match:Grok-4",
                "matched_address": res_item.matched_address or unit.canonical_address,
                "latitude": rlat,
                "longitude": rlon,
                "colocator": f"LL:{rlat}:{rlon}",
            }
        code = res_item.failure_code if res_item else None
        status = grok_failure_status(code)
        note = (res_item.reason if res_item and res_item.reason else status)
        return {
            **base,
            "geocoding_status": status,
            "matched_address": note[:500],
            "latitude": None,
            "longitude": None,
            "colocator": status,
        }

    @classmethod
    def build_grok_geocode_messages(cls, units: List[GeocodingWorkUnit]) -> tuple[str, str]:
        codes = ", ".join(sorted(GROK_FAILURE_CODES))
        prompt_lines = []
        for unit in units:
            canon = unit.canonical_address.strip()
            norm_str = json.dumps(unit.parsed_normalized, separators=(',', ':')) if unit.parsed_normalized else ""
            prompt_lines.append(f"ID: {unit.geocoding_id} | Raw: \"{canon}\" | Parsed JSON: {norm_str}")
        system_prompt = f"""You are an expert geocoder for messy IRS 990 nonprofit addresses.
These rows already failed Census, Photon, and paid geocoders — analyze WHY and classify failures.
Ignore C/O, Attn:, See Statement, personal names unless tied to a known org HQ.
Use known entity HQs when obvious (e.g. First Citizens → Raleigh area).
Return lat/long only if ≥75% confident in a real US street location.

When you cannot geocode, you MUST set failure_code (not just null coords):
- NOTA: not a postal address (org name only, "see statement", narrative, department label)
- VAGUE: incomplete US address (city/state only, missing street number or name)
- AMBIG: multiple plausible US street matches, cannot pick one
- REDACT: intentionally redacted or privacy placeholder
- UNKN: foreign/non-US slip-through, none of the above, or genuinely unsure

Classify precisely — these labels feed pattern-rule mining to auto-handle similar addresses later."""
        user_prompt = f"""Analyze these addresses — geocode when possible, otherwise classify the failure:

{'\n'.join(prompt_lines)}

CRITICAL: For each result, set 'id' to the EXACT geocoding_id UUID shown at the start of the line.

For each address provide:
- id: geocoding_id UUID (required, exact match)
- lat / long: floats when ≥75% confident in a US street location; otherwise null
- matched_address: best full geocoded address when matched; otherwise null
- failure_code: REQUIRED when lat/long are null — one of: {codes}
- reason: one short sentence explaining the match or why this failure_code applies

Output ONLY the complete valid JSON object matching the schema. Include ALL addresses."""
        return system_prompt, user_prompt

    @classmethod
    def build_grok_geocode_request_body(cls, units: List[GeocodingWorkUnit]) -> dict:
        system_prompt, user_prompt = cls.build_grok_geocode_messages(units)
        schema = BatchGeocodeOutput.model_json_schema()
        return {
            "model": cls.grok_geocode_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "batch_geocode", "strict": False, "schema": schema},
            },
            "temperature": 0.0,
            "max_tokens": 16384,
        }

    def _grok_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        if not batch:
            return []

        client = OpenAI(api_key=os.getenv("X_API_KEY"), base_url="https://api.x.ai/v1")
        items = batch
        body = self.build_grok_geocode_request_body(items)
        grok_model = body["model"]
        raw = None
        try:
            response = client.chat.completions.create(**body, timeout=120)
            raw = (response.choices[0].message.content or "").strip()
            parsed = self.safe_parse(raw)

            results = []
            parsed_dict = {res.id: res for res in parsed.results}  # map by UUID
            n_match = 0
            now = datetime.now().isoformat()
            for unit in items:
                res_item = parsed_dict.get(unit.geocoding_id)
                if res_item and res_item.lat is not None and res_item.long is not None:
                    result_unit = self._apply_successful_geocode(
                        unit, res_item.lat, res_item.long, "Match:Grok-4", res_item.matched_address or unit.canonical_address
                    )
                    results.append((True, result_unit))
                    n_match += 1
                else:
                    fields = self.grok_result_update_fields(unit, res_item, now=now)
                    self.run_stats.grok_fail += 1
                    result_unit = self._apply_geocoding_only_update(
                        unit, fields["geocoding_status"],
                        colocator=fields.get("colocator"),
                        lat=fields.get("latitude"),
                        lon=fields.get("longitude"),
                        matched=fields.get("matched_address"),
                    )
                    results.append((True, result_unit))
            print(
                f"[{self.pipeline_step}] grok batch={len(batch)} matched={n_match}/{len(batch)} model={grok_model}",
                flush=True,
            )
            return results
        except Exception as e:
            if raw:
                log_error(f"Grok failed: {e} | response={raw[:500]}")
            else:
                log_error(f"Grok failed: {e}")
            return [(False, unit) for unit in batch]

    def _geopy_stage_handler(
        self, batch: List[GeocodingWorkUnit], stage: str, geocoder_key: str, source_name: str,
        *, prefer_structured: bool,
    ) -> List[tuple[bool, GeocodingWorkUnit]]:
        results = [
            self._geocode_with_geopy(u, geocoder_key, source_name, prefer_structured=prefer_structured)
            for u in batch
        ]
        n_match = sum(1 for ok, _ in results if ok)
        print(f"[geolocate_new] {stage} batch={len(batch)} matched={n_match}/{len(batch)}", flush=True)
        return results

    def _photon_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return self._geopy_stage_handler(batch, "photon", "photon", "Photon", prefer_structured=False)

    def _nominatim_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return self._geopy_stage_handler(batch, "nominatim", "nominatim", "Nominatim", prefer_structured=True)

    def _geocode_maps_co_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return self._geopy_stage_handler(
            batch, "geocode_maps_co", "geocode_maps_co", "GeocodeMapsCo", prefer_structured=True,
        )

    def _opencage_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        return self._geopy_stage_handler(batch, "opencage", "opencage", "OpenCage", prefer_structured=False)

    def _librestreet_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        if GeocodingAPIProcessor._librestreet_disabled:
            return [(False, unit) for unit in batch]
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

    def _grok_pending_fail_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        self.run_stats.grok_queued += len(batch)
        log_info(f"Grok pending handler: {len(batch)} addresses queued for geolocate_grok")
        print(f"[geolocate_new] grok_pending batch={len(batch)}", flush=True)
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
                        'geocoding_status': 'grok_pending',
                        'last_attempt': now,
                        'attempt_count': unit.attempt_count + 1,
                        'matched_address': None,
                    }],
                    'id_column': 'geocoding_id'
                }
            ))
            ctx.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': unit.address_count}
            ))
        result = ResultWorkUnit(ctx, stage='fail')
        return [(True, result)]

    def _grok_final_fail_handler(self, batch: List[GeocodingWorkUnit]) -> List[tuple[bool, GeocodingWorkUnit]]:
        self.run_stats.grok_fail += len(batch)
        log_info(f"Grok final fail: {len(batch)} addresses exhausted Grok stage")
        print(f"[geolocate_grok] final_fail batch={len(batch)}", flush=True)
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
        result = ResultWorkUnit(ctx, stage='fail')
        return [(True, result)]

    _WORK_ORDER = """
        CASE WHEN canonical_address ILIKE '%po box%' OR canonical_address ILIKE '%p.o.%' THEN 1 ELSE 0 END,
        geocoding_id
    """

    def _rows_to_work_units(self, rows) -> Tuple[List[GeocodingWorkUnit], Optional[str]]:
        work_units = []
        new_pk = None
        for row in rows:
            data = {
                'geocoding_id': row[0],
                'normalized_address': row[1],
                'attempt_count': row[2],
                'canonical_address': row[3],
                'address_count': row[4] or 0,
                'geocoding_status': row[5],
            }
            work_units.append(GeocodingWorkUnit.work_item("feed", data))
            new_pk = row[0]
        return work_units, new_pk

    def get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[GeocodingWorkUnit], Optional[str]]:
        log_debug(f"get_work_batch last_pk={last_pk} batch_size={self.batch_size}")
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
            ORDER BY {self._WORK_ORDER}
            LIMIT {self.batch_size}
        """

        result = self.db_ops.execute_query(query, tuple(params))
        rows = result.fetchall()
        if rows:
            po_ct = sum(1 for r in rows if 'po box' in (r[3] or '').lower() or 'p.o.' in (r[3] or '').lower())
            log_debug(f"get_work_batch fetched {len(rows)} rows ({po_ct} PO boxes)")

        work_units, new_pk = self._rows_to_work_units(rows)
        return work_units, new_pk

    def get_grok_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[GeocodingWorkUnit], Optional[str]]:
        log_debug(f"get_grok_work_batch last_pk={last_pk} batch_size={self.batch_size}")
        where_parts = ["geocoding_status = 'grok_pending'"]
        params: list = []
        if last_pk is not None:
            where_parts.append("geocoding_id > ?")
            params.append(last_pk)
        query = f"""
            SELECT geocoding_id, normalized_address, attempt_count, canonical_address, address_count, geocoding_status
            FROM Geocoding
            WHERE {' AND '.join(where_parts)}
            ORDER BY address_count DESC NULLS LAST, geocoding_id
            LIMIT {self.batch_size}
        """
        rows = self.db_ops.execute_query(query, tuple(params)).fetchall()
        if rows:
            log_debug(f"get_grok_work_batch fetched {len(rows)} grok_pending rows")
        work_units, new_pk = self._rows_to_work_units(rows)
        return work_units, new_pk

    def get_grok_total_work(self) -> int:
        result = self.db_ops.execute_query(
            "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = 'grok_pending'"
        )
        return int(result.fetchone()[0])
    
    def get_total_work(self) -> int:
        query = """
            SELECT COUNT(*)
            FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
        """
        result = self.db_ops.execute_query(query)
        return int(result.fetchone()[0])

    def get_work_count(self, max_files=None) -> int:
        base_query = """
            SELECT COUNT(*)
            FROM Geocoding
            WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
        """
        if max_files is None:
            result = self.db_ops.execute_query(base_query)
            return int(result.fetchone()[0])
        query = f"""
            SELECT COUNT(*)
            FROM (
                SELECT 1
                FROM Geocoding
                WHERE geocoding_status IS NULL OR geocoding_status IN ('pending', 'owners')
                ORDER BY {self._WORK_ORDER}
                LIMIT {max_files}
            ) sub
        """
        result = self.db_ops.execute_query(query)
        return result.fetchone()[0]

    def get_progress_config(self, max_files=None):
        total = self.get_work_count(max_files=(max_files or global_config.max_files))
        return total, "addresses", "Geocoding addresses"

    def process_pending_geocoding_records(self, max_files=None, progress_bar=None) -> int:
        self.run_stats = GeocodeRunStats()
        self.pipeline_step = "geolocate_new"
        self.setup_address_counts()
        archive_done = self.db_ops.execute_query(
            "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = 'Match:Archive'"
        ).fetchone()[0]
        if archive_done > 0:
            log_info(f"Skipping archive re-apply ({archive_done:,} Match:Archive already present)")
        else:
            log_info("Applying geocode archive cache...")
            self.apply_geocode_archive_full_propagation()
        pending = self.get_work_count(max_files=max_files)
        log_info(f"Starting geolocate_new (free APIs) for {pending:,} pending addresses (max_files={max_files})")
        print(f"[geolocate_new] Starting free pipeline: {pending:,} pending (max_files={max_files})", flush=True)
        processed = self.free_pipeline.run_with_provider(self, max_items=max_files) or 0
        self.run_stats.fed = processed
        self.run_stats.log_summary(step="geolocate_new")
        print(f"Geolocate_new complete: {processed} records processed")
        return processed

    def process_grok_pending_realtime(self, max_files=None) -> int:
        """Realtime Grok pipeline — prefer geolocate_grok batch step for bulk."""
        self.run_stats = GeocodeRunStats()
        self.pipeline_step = "geolocate_grok"
        pending = self.get_grok_total_work()
        if max_files is not None:
            pending = min(pending, max_files)
        log_info(f"Starting geolocate_grok realtime pipeline for {pending:,} grok_pending rows")
        print(f"[geolocate_grok] Starting realtime pipeline: {pending:,} grok_pending", flush=True)

        class _GrokProvider:
            def __init__(self, proc: "GeocodingAPIProcessor"):
                self._proc = proc

            def get_work_batch(self, last_pk):
                return self._proc.get_grok_work_batch(last_pk)

            def get_total_work(self):
                return self._proc.get_grok_total_work()

        provider = _GrokProvider(self)
        processed = self.grok_pipeline.run_with_provider(provider, max_items=max_files) or 0
        self.run_stats.fed = processed
        self.run_stats.log_summary(step="geolocate_grok")
        print(f"Geolocate_grok realtime complete: {processed} records processed")
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
            
    def apply_geocode_archive_full_propagation(self, cache_file: str = "geocode_archive_distinct.tsv.gz") -> int:
        """Load the pre-geocoded TSV.gz archive and perform FULL owner propagation using chunked processing to avoid memory exhaustion.

        Streams the archive via gzip + csv in 100000-row chunks, delegates to _apply_chunk_updates for each,
        and guarantees atomicity via a single outer transaction.
        """
        log_info(f"Applying full geocode archive cache + owner propagation from {cache_file} (chunked)...")
        actual_cache_file = os.path.join(global_config.final_dir, cache_file)

        with self.db_ops.acquire_write_conn() as conn:
            # === QUICK IDEMPOTENCY CHECK ===
            already_done = conn.execute("""
                SELECT COUNT(*) 
                FROM Geocoding 
                WHERE geocoding_status = 'Match:Archive'
                LIMIT 1
            """).fetchone()[0]

            if already_done > 0:
                log_info("Archive cache already applied (found 'Match:Archive' status). Skipping all work.")
                return 0
            conn.execute("BEGIN TRANSACTION")

            try:
                log_info(f"Streaming geocode cache from {actual_cache_file}...")
                chunk_size = 100000
                total_updated_geocoding = 0
                total_updated_addresses = 0
                chunk_num = 0
                with gzip.open(actual_cache_file, 'rt', encoding='utf-8', errors='replace') as f:
                    reader = csv.DictReader(f, delimiter='\t')
                    chunk = []
                    for row in reader:
                        if 'canonical_address' not in row or 'colocator' not in row:
                            continue
                        chunk.append((row['canonical_address'], row['colocator']))
                        if len(chunk) >= chunk_size:
                            chunk_num += 1
                            g_count, a_count = self._apply_chunk_updates(chunk, conn, chunk_num)
                            total_updated_geocoding += g_count
                            total_updated_addresses += a_count
                            chunk = []
                    if chunk:
                        chunk_num += 1
                        g_count, a_count = self._apply_chunk_updates(chunk, conn, chunk_num)
                        total_updated_geocoding += g_count
                        total_updated_addresses += a_count
                # Final cleanup and analyze
                for table in ['Geocoding', 'Addresses', 'Charities', 'Grants', 'Officers', 'Contractors', 'PoliticalContributions']:
                    log_info(f"Running VACUUM ANALYZE on {table}...")
                    conn.execute(f"VACUUM ANALYZE {table};")
                    conn.commit()
                log_info(f"Full archive cache + owner propagation complete (chunked)")
                log_info(f"   Updated {total_updated_geocoding:,} Geocoding records")
                log_info(f"   Updated {total_updated_addresses:,} Addresses with colocator")
                log_info(f"   Processed across {chunk_num} chunks")
                return total_updated_geocoding
            except Exception as e:
                conn.rollback()
                log_error(f"Full cache propagation failed: {e}", exc_info=True)
                raise

    def _apply_chunk_updates(self, chunk: list, conn, chunk_num: int) -> tuple[int, int]:
        """Process one chunk of (canonical_address, colocator) tuples from the archive.

        Stages data in a temporary DuckDB table, runs scoped update_map and owner propagations,
        then cleans up. Returns (updated_geocoding_count, updated_addresses_count).
        """
        if not chunk:
            return 0, 0
        log_info(f"Processing chunk {chunk_num} with {len(chunk):,} addresses...")
        # Robust staging using batched VALUES (avoids executemany issues on some DuckDB connections)
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE chunk_cache (
                canonical_address VARCHAR,
                colocator VARCHAR
            )
        """)
        # Sanitize for SQL and batch to keep each INSERT query small
        safe_chunk = []
        for ca, co in chunk:
            ca = str(ca or "").replace("'", "''")
            co = str(co or "").replace("'", "''")
            safe_chunk.append((ca, co))
        batch_size = 2000  # keeps each VALUES clause well under parser limits
        for i in range(0, len(safe_chunk), batch_size):
            sub = safe_chunk[i : i + batch_size]
            values_clause = ", ".join(f"('{ca}', '{co}')" for ca, co in sub)
            conn.execute(f"INSERT INTO chunk_cache VALUES {values_clause}")
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE update_map AS
            SELECT 
                g.geocoding_id,
                c.colocator,
                CASE 
                    WHEN c.colocator LIKE 'LL:%' 
                    THEN CAST(split_part(c.colocator, ':', 2) AS DOUBLE) 
                    ELSE NULL 
                END AS parsed_lat,
                CASE 
                    WHEN c.colocator LIKE 'LL:%' 
                    THEN CAST(split_part(c.colocator, ':', 3) AS DOUBLE) 
                    ELSE NULL 
                END AS parsed_lon
            FROM Geocoding g
            INNER JOIN chunk_cache c 
                    ON g.canonical_address = c.canonical_address
            WHERE g.geocoding_status IN (NULL, 'pending', 'owners', 'No_Match');
        """)
        map_count = conn.execute("SELECT COUNT(*) FROM update_map").fetchone()[0]
        if map_count == 0:
            conn.execute("DROP TABLE IF EXISTS chunk_cache;")
            conn.execute("DROP TABLE IF EXISTS update_map;")
            return 0, 0
        result = conn.execute("""
            UPDATE Geocoding g
            SET 
                geocoding_status = 'Match:Archive',
                last_attempt      = CURRENT_TIMESTAMP,
                attempt_count     = COALESCE(g.attempt_count, 0) + 1,
                matched_address   = 'Loaded from geocode_archive_distinct.tsv.gz',
                latitude          = m.parsed_lat,
                longitude         = m.parsed_lon
            FROM update_map m
            WHERE g.geocoding_id = m.geocoding_id
        """).fetchone()
        updated_geocoding = int(result[0]) if result and result[0] is not None else 0
        conn.execute("""
            UPDATE Addresses a
            SET colocator = m.colocator
            FROM update_map m
            WHERE a.geocoding_id = m.geocoding_id;
        """)
        actual_updated_addresses = conn.execute("""
            SELECT COUNT(*) 
            FROM Addresses 
            WHERE colocator IN (SELECT colocator FROM update_map)
        """).fetchone()[0]
        conn.execute("""
            UPDATE Charities ch
            SET colocator = m.colocator
            FROM (
                SELECT DISTINCT owner_id, ANY_VALUE(m.colocator) AS colocator
                FROM update_map m
                JOIN Addresses a ON a.geocoding_id = m.geocoding_id
                WHERE a.owner_id IS NOT NULL
                  AND a.address_type IN ('charity', 'filer')
                GROUP BY owner_id
            ) m
            WHERE ch.charity_id = m.owner_id;
        """)
        conn.execute("""
            UPDATE Grants gr
            SET colocator = m.colocator
            FROM (
                SELECT DISTINCT owner_id, ANY_VALUE(m.colocator) AS colocator
                FROM update_map m
                JOIN Addresses a ON a.geocoding_id = m.geocoding_id
                WHERE a.owner_id IS NOT NULL
                  AND a.address_type = 'grant'
                GROUP BY owner_id
            ) m
            WHERE gr.grant_id = m.owner_id;
        """)
        conn.execute("""
            UPDATE Contractors con
            SET colocator = m.colocator
            FROM (
                SELECT DISTINCT owner_id, ANY_VALUE(m.colocator) AS colocator
                FROM update_map m
                JOIN Addresses a ON a.geocoding_id = m.geocoding_id
                WHERE a.owner_id IS NOT NULL
                  AND a.address_type = 'contractor'
                GROUP BY owner_id
            ) m
            WHERE con.contractor_id = m.owner_id;
        """)
        conn.execute("""
            UPDATE Officers off
            SET colocator = m.colocator
            FROM (
                SELECT DISTINCT owner_id, ANY_VALUE(m.colocator) AS colocator
                FROM update_map m
                JOIN Addresses a ON a.geocoding_id = m.geocoding_id
                WHERE a.owner_id IS NOT NULL
                  AND a.address_type = 'officer'
                GROUP BY owner_id
            ) m
            WHERE off.officer_id = m.owner_id;
        """)
        conn.execute("""
            UPDATE PoliticalContributions pc
            SET colocator = m.colocator
            FROM (
                SELECT DISTINCT owner_id, ANY_VALUE(m.colocator) AS colocator
                FROM update_map m
                JOIN Addresses a ON a.geocoding_id = m.geocoding_id
                WHERE a.owner_id IS NOT NULL
                  AND a.address_type IN ('politicalcontribution', 'political')
                GROUP BY owner_id
            ) m
            WHERE pc.political_id = m.owner_id;
        """)
        conn.execute("DROP TABLE IF EXISTS chunk_cache;")
        conn.execute("DROP TABLE IF EXISTS update_map;")
        log_info(f"Chunk {chunk_num} complete: {updated_geocoding:,} Geocoding, {actual_updated_addresses:,} Addresses updated")
        return updated_geocoding, actual_updated_addresses

    def _get_custom_metrics(self) -> Dict[str, Any]:
        if not hasattr(self, 'pipeline'):
            return {'current_step': 'geolocate'}
        status = self.pipeline.get_status()
        metrics = status['metrics']
        metrics['current_step'] = 'geolocate'
        return metrics