#!/usr/bin/env python3
"""
grant_match_processor.py - Processor for matching grants to charities
"""

from typing import List, Dict, Any, Tuple, Optional
import queue
import re
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_debug
from config import global_config
from base_processor import BaseProcessor, WorkUnit
from pending_database_context import PendingDatabaseContext
from collections import defaultdict

COMPANY_SUFFIX_REGEX = re.compile(r'\s+(INC|CORP|LLC|FOUNDATION)$', re.IGNORECASE)

def word_jaccard(name1: str, name2: str) -> float:
    """Compute Jaccard similarity on word sets"""
    if not name1 or not name2:
        return 0.0
    if name1.lower() == name2.lower():
        return 1.0
    cleaned1 = COMPANY_SUFFIX_REGEX.sub('', name1.lower()).strip()
    cleaned2 = COMPANY_SUFFIX_REGEX.sub('', name2.lower()).strip()
    if cleaned1 == cleaned2:
        return 1.0
    words1 = set(cleaned1.lower().split())
    words2 = set(cleaned2.lower().split())
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    return intersection / union if union else 0.0


class GrantMatchProcessor(BaseProcessor):
    """Processor for matching grants to charities"""

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.batch_size = 1000
        self.jaccard_threshold = 0.7
        self.distance_km = 40.23  # 25 mi in km

    def match_grants(self) -> int:
        """Run the grant matching process"""
        return self.process_parallel(global_config.max_files, global_config.workers)

    def get_work_count(self, max_files: Optional[int] = None) -> int:
        query = "SELECT COUNT(*) FROM Grants WHERE recipient_ein IS NULL and colocator IS NOT NULL"
        result = self.db_ops.execute_query(query)
        total = result.fetchone()[0]
        if max_files:
            total = min(total, max_files)
        return total

    def get_progress_config(self, max_files: Optional[int] = None) -> Tuple[int, str, str]:
        total = self.get_work_count(max_files)
        return total, 'grants', 'Matching grants to charities'

    def _feed_thread(self, work_queue: queue.Queue, max_files: Optional[int], num_producers: int):
        last_id = None
        enqueued = 0
        while True:
            if self.exit_processing:
                break
            batch, new_last = self._get_work_batch(last_id)
            if not batch:
                break
            work_queue.put(WorkUnit.batch(batch))
            enqueued += len(batch)
            last_id = new_last
            if max_files and enqueued >= max_files:
                break
        for i in range(num_producers):
            work_queue.put(WorkUnit.sentinel(i))

    def _get_work_batch(self, last_id: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        query = """
        SELECT grant_id, grantee_name, colocator
        FROM Grants
        WHERE recipient_ein IS NULL and colocator IS NOT NULL
        """
        params: Tuple = (self.batch_size,)
        if last_id:
            query += " AND grant_id > ?"
            params = (last_id,) + params
        query += " ORDER BY grant_id LIMIT ?"
        rows = self.db_ops.execute_query(query, params).fetchall()
        batch = [dict(zip(['grant_id', 'grantee_name', 'colocator', 'grantee_sndx'], row)) for row in rows]
        new_last = rows[-1][0] if rows else None
        return batch, new_last

    def _process_batch(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        context = PendingDatabaseContext()
        updates = []
        
        # Group grants by colocator to fetch charities only once per unique colocator
        grants_by_colocator: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in batch:
            colocator = item.get('colocator')
            if colocator:  # Skip if no colocator (though your query filters this)
                grants_by_colocator[colocator].append(item)
        
        for colocator, grants in grants_by_colocator.items():
            # Strategy 1: Exact colocator match
            query_char = """
            SELECT ein, filer_name, (COALESCE(govt_amt, 0) + COALESCE(receipt_amt, 0)) AS wealth, sndx
            FROM Charities
            WHERE colocator = ?
            """
            charities = self.db_ops.execute_query(query_char, [colocator]).fetchall()
            
            if charities:
                # Process with colocator matches
                for item in grants:
                    best_ein = self._get_best_ein(charities, item['grantee_name'], item['grantee_sndx'])
                    updates.append({'grant_id': item['grant_id'], 'recipient_ein': best_ein})
            else:
                # Strategy 2: Geo 25 mi fallback
                for item in grants:
                    best_ein = self._geo_fallback_match(item['grantee_name'], colocator, item['grantee_sndx'])
                    if best_ein:
                        updates.append({'grant_id': item['grant_id'], 'recipient_ein': best_ein})
                    else:
                        # Original global fallback
                        best_ein = self._global_fallback_match(item['grantee_name'])
                        updates.append({'grant_id': item['grant_id'], 'recipient_ein': best_ein or '8686'})
        
        if updates:
            bulk_op = DatabaseOperation(
                DatabaseOperationType.GENERIC_UPDATE,
                data={'table': 'Grants', 'updates': updates, 'id_column': 'grant_id'}
            )
            context.addOperationToDatabase(bulk_op)
        
        progress_op = DatabaseOperation(
            DatabaseOperationType.PROGRESS_UPDATE,
            data={"count": len(batch)}
        )
        context.addOperationToDatabase(progress_op)
        
        return context

    def _get_best_ein(self, charities: List[Tuple[str, str, float, str]], grantee_name: str, grantee_sndx: str) -> str:
        """Get best EIN by jaccard, prefer same sndx, tiebreak wealth"""
        matches = []
        for ein, filer_name, wealth, sndx in charities:
            jacc = word_jaccard(grantee_name, filer_name)
            sndx_match = (sndx == grantee_sndx) * 0.2  # Bonus for sndx match
            score = jacc + sndx_match
            if score >= self.jaccard_threshold:
                matches.append((score, ein, wealth))
        
        if matches:
            matches.sort(key=lambda x: (-x[0], -x[2]))
            return matches[0][1]
        else:
            best = max(charities, key=lambda x: x[2])
            return best[0]

    def _geo_fallback_match(self, grantee_name: str, colocator: str, grantee_sndx: str) -> Optional[str]:
        """Geo fallback: Find charities in 25 mi, then best jaccard/sndx/wealth"""
        zip_code = self._parse_zip(colocator)
        if not zip_code:
            return None
        
        # Get grant zip lat/lon
        row = self.db_ops.execute_query("SELECT lat, lon FROM Zips WHERE zip = ?;", [zip_code]).fetchone()
        if not row:
            return None
        grant_lat, grant_lon = row
        
        # Find candidates in 25 mi
        query = """
        SELECT c.ein, c.filer_name, (COALESCE(c.govt_amt, 0) + COALESCE(c.receipt_amt, 0)) AS wealth, c.sndx
        FROM Charities c
        JOIN Addresses a ON a.owner_id = c.charity_id
        JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
        WHERE g.latitude BETWEEN ? - 0.37 AND ? + 0.37
          AND g.longitude BETWEEN ? - 0.37 AND ? + 0.37
          AND ST_Distance(ST_Point(?, ?), ST_Point(g.longitude, g.latitude)) <= ?
        """
        params = [grant_lat, grant_lat, grant_lon, grant_lon, grant_lon, grant_lat, self.distance_km]
        candidates = self.db_ops.execute_query(query, params).fetchall()
        
        return self._get_best_ein(candidates, grantee_name, grantee_sndx) if candidates else None

    def _global_fallback_match(self, grantee_name: str) -> Optional[str]:
        """Original global name fallback with LIKE and wealth"""
        words = grantee_name.lower().split()[:2]
        like_pattern = '%' + '%'.join(words) + '%'
        query_global = """
        SELECT ein, filer_name, (COALESCE(govt_amt, 0) + COALESCE(receipt_amt, 0)) AS wealth, sndx
        FROM Charities
        WHERE LOWER(filer_name) LIKE ?
        ORDER BY wealth DESC
        LIMIT 50
        """
        global_charities = self.db_ops.execute_query(query_global, [like_pattern]).fetchall()
        
        if not global_charities:
            return None
        
        matches = []
        for ein, filer_name, wealth, sndx in global_charities:
            jacc = word_jaccard(grantee_name, filer_name)
            if jacc >= self.jaccard_threshold:
                matches.append((jacc, ein, wealth))
        
        if matches:
            matches.sort(key=lambda x: (-x[0], -x[2]))
            return matches[0][1]
        else:
            best = max(global_charities, key=lambda x: x[2])
            return best[0]

    def _parse_zip(self, colocator: str) -> Optional[str]:
        if colocator and colocator.startswith('PO:'):
            return colocator.split(':')[1]
        return None