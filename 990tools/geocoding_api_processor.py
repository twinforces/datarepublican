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
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

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
from constants import GEOCODING_BATCH_SIZE
from pending_database_context import PendingDatabaseContext


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

    def _check_geocoding_patterns(self, canonical_address: str, zip_code: str = "") -> Optional[Dict[str, Any]]:
        for pattern in self.geocoding_patterns:
            if 'patterns' in pattern:
                for sub in pattern['patterns']:
                    if re.search(sub.get('regex', ''), canonical_address, re.IGNORECASE):
                        return {**sub, 'action': sub.get('action', 'match')}
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
                return {
                    'colocator': colocator,
                    'status': pattern.get('status', 'owners'),
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
                        update = {'geocoding_id': gid, 'normalized_address': json.dumps(data), 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'pending'}
                    else:
                        update = {'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}
                except:
                    update = {'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}
            else:
                cleaned = re.sub(regex, '', canonical, flags=re.IGNORECASE)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                if self._check_geocoding_patterns(cleaned) and self._check_geocoding_patterns(cleaned).get('action') == 'strip':
                    update = {'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}
                else:
                    update = {'geocoding_id': gid, 'canonical_address': cleaned, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'pending'}
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

        pattern = self._check_geocoding_patterns(canonical, zip_code)
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

        fallbacks = [
            ("Grok", self._geocode_with_grok),
            ("Photon", self._geocode_with_photon),
            ("LibreStreet", self._geocode_with_librestreet),
            ("Nominatim", self._geocode_with_nominatim),
            #("OpenCage", self._geocode_with_opencage),
            #("Google_Maps", self._geocode_with_google_maps),
            #("Name_Search", lambda _: self._geocode_with_name_search(gid, canonical)),
        ]
        if os.getenv('ENABLE_GOOGLE_MAPS_FALLBACK', 'false').lower() == 'true':
            fallbacks.append(("Google_Maps_Final", self._geocode_with_google_maps))

        for name, func in fallbacks:
            try:
                res = func(canonical)
                if res and res.get('match'):
                    lat, lon = float(res['lat']), float(res['lon'])
                    status = f"Match:{name.replace('_', ' ')}"
                    self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, res, res.get('formatted_address'))
                    context.addOperationToDatabase(DatabaseOperation(operation_type=DatabaseOperationType.PROGRESS_UPDATE, data={'count': work_item.get('address_count', 0)}))
                    return context
            except Exception as e:
                log_debug(f"Fallback {name} failed: {e}")

        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={'table': 'Geocoding', 'updates': [{'geocoding_id': gid, 'last_attempt': now, 'attempt_count': attempt + 1, 'geocoding_status': 'No_Match'}], 'id_column': 'geocoding_id'}
        ))
        context.addOperationToDatabase(DatabaseOperation(operation_type=DatabaseOperationType.PROGRESS_UPDATE, data={'count': work_item.get('address_count', 0)}))
        return context

    def _geocode_with_grok(self, address_str):
        """Fallback to Grok API for hard cases."""
        api_key = os.getenv('XAI_API_KEY')  # Your credits key
        self.grok_calls += 1
        if not api_key:
            return None
        try:
            import requests
            prompt = f"Give me the lat/long for '{address_str}'. Respond ONLY with 'lat: X, long: Y' or 'No match'."
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "grok-4-latest",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 50
                },
                timeout=10
            )
            data = response.json()
            reply = data['choices'][0]['message']['content'].strip()
            if 'lat:' in reply and 'long:' in reply:
                lat_match = re.search(r'lat:\s*([\d.-]+)', reply)
                lon_match = re.search(r'long:\s*([\d.-]+)', reply)
                if lat_match and lon_match:
                    return {
                        'match': True,
                        'matchtype': 'Grok',
                        'lat': float(lat_match.group(1)),
                        'lon': float(lon_match.group(1)),
                        'formatted_address': address_str
                    }
        except Exception as e:
            log_debug(f"Grok fallback failed: {e}")
        return None

    def _geocode_with_google_maps(self, address_str):
        self.google_maps_calls += 1
        key = os.getenv('GOOGLE_MAPS_API_KEY')
        if not key: return None
        try:
            url = f"https://maps.googleapis.com/maps/api/geocode/json?address={requests.utils.quote(address_str)}&key={key}"
            r = requests.get(url, timeout=10)
            data = r.json()
            if data.get('status') == 'OK' and data.get('results'):
                loc = data['results'][0]['geometry']['location']
                return {'match': True, 'lat': loc['lat'], 'lon': loc['lng'], 'formatted_address': data['results'][0]['formatted_address']}
        except: pass
        return None

    def _geocode_with_nominatim(self, address_str):
        self.nominatim_calls += 1
        if Nominatim is None: return None
        try:
            geolocator = Nominatim(user_agent="irs990-geocoder")
            time.sleep(0.1)
            location = geolocator.geocode(address_str, timeout=10)
            if location:
                return {'match': True, 'lat': location.latitude, 'lon': location.longitude, 'formatted_address': location.address}
        except: pass
        return None

    def _geocode_with_photon(self, address_str):
        self.photon_calls += 1
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

    def _geocode_with_opencage(self, address_str):
        self.opencage_calls += 1
        if OpenCageGeocode is None: return None
        key = os.getenv('OPENCAGE_API_KEY')
        if not key: return None
        try:
            geocoder = OpenCageGeocode(key)
            results = geocoder.geocode(address_str)
            if results and len(results) > 0:
                r = results[0]
                return {'match': True, 'lat': r['geometry']['lat'], 'lon': r['geometry']['lng'], 'formatted_address': r.get('formatted', address_str)}
        except: pass
        return None
    
    def _geocode_with_librestreet(self, address_str):
        self.librestreet_calls += 1
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

    def _geocode_with_name_search(self, geocoding_id: str, canonical_address: str):
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

    def _get_custom_metrics(self) -> Dict[str, Any]:
        try:
            pending = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = 'pending'").fetchone()[0]
            if global_config.max_files:
                pending = min(pending, global_config.max_files)
            matched = self.db_ops.execute_query("SELECT COUNT(*) FROM Geocoding WHERE geocoding_status LIKE 'Match%'").fetchone()[0]
            return {
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
    
    def _process_batch(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        """
        Batch processor — identical behavior to original, but clean and working.
        This is REQUIRED by BaseProcessor.process_parallel()
        """
        context = PendingDatabaseContext()
        now = datetime.now().isoformat()

        # Separate owners and pattern-matched items from those needing API calls
        owners_items = []
        pattern_items = []
        api_items = []

        for item in batch:
            if item['geocoding_status'] == 'owners':
                owners_items.append(item)
            else:
                zip_code = ""
                if isinstance(item['normalized_address'], str):
                    try:
                        zip_code = json.loads(item['normalized_address']).get('zip', '')
                    except:
                        pass
                pattern = self._check_geocoding_patterns(item['canonical_address'], zip_code)
                if pattern:
                    pattern_items.append((item, pattern))
                else:
                    api_items.append(item)

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

        # === Batch Census call ===
        if cg is None:
            for item in api_items:
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
            total_addresses = sum(item.get('address_count', 0) for item in api_items)
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': total_addresses}
            ))
            return context

        # Prepare batch for censusgeocode (with id mapping)
        records = []
        id_map = {}
        for item in api_items:
            record = {}
            normalized = item['normalized_address']
            if isinstance(normalized, str):
                try: record = json.loads(normalized)
                except: pass
            elif isinstance(normalized, dict):
                record = normalized.copy()
            record.pop('id', None)
            record['id'] = str(item['geocoding_id'])
            records.append(record)
            id_map[record['id']] = item

        try:
            self.census_calls += 1
            results = cg.addressbatch(records)
        except Exception as e:
            log_error(f"Batch Census call failed: {e}")
            for item in api_items:
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
            total_addresses = sum(item.get('address_count', 0) for item in api_items)
            context.addOperationToDatabase(DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={'count': total_addresses}
            ))
            return context

        # Process Census results
        for result in results:
            gid_str = result.get('id')
            if not gid_str or gid_str not in id_map:
                continue
            item = id_map[gid_str]
            gid = item['geocoding_id']
            attempt = int(item['attempt_count'])

            if result.get('match') and result.get('lat') and result.get('lon'):
                lat = float(result['lat'])
                lon = float(result['lon'])
                matchtype = result.get('matchtype', 'Exact')
                status = 'Match' if matchtype == 'Exact' else f'Match:{matchtype}'
                self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, result)
            else:
                # Fallback chain per-item (same as single)
                success = False
                for name, func in [
                    ("Grok", self._geocode_with_grok),
                    ("Photon", self._geocode_with_photon),
                    ("LibreStreet", self._geocode_with_librestreet),
                    ("Nominatim", self._geocode_with_nominatim),
                    #("OpenCage", self._geocode_with_opencage),
                    #("Name_Search", lambda _: self._geocode_with_name_search(gid, item['canonical_address'])),
                    #("Google_Maps", self._geocode_with_google_maps),
                ]:
                    try:
                        res = func(item['canonical_address'])
                        if res and res.get('match'):
                            lat = float(res['lat'])
                            lon = float(res['lon'])
                            status = f"Match:{name.replace('_', ' ')}"
                            self._apply_successful_geocode(context, gid, attempt, now, lat, lon, status, res, res.get('formatted_address'))
                            success = True
                            break
                    except:
                        continue
                if not success:
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

        # Final progress
        total_addresses = sum(item.get('address_count', 0) for item in batch)
        context.addOperationToDatabase(DatabaseOperation(
            operation_type=DatabaseOperationType.PROGRESS_UPDATE,
            data={'count': total_addresses}
        ))

        return context