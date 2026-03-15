#!/usr/bin/env python3
"""
grant_match_processor.py - Processor for matching grants to charities based on colocator and name similarity
"""

from typing import List, Dict, Any, Tuple, Optional
import queue
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_debug
from config import global_config
from base_processor import BaseProcessor, WorkUnit
from pending_database_context import PendingDatabaseContext
from collections import defaultdict

def word_jaccard(name1: str, name2: str) -> float:
    """Compute Jaccard similarity on word sets"""
    if not name1 or not name2:
        return 0.0
    words1 = set(name1.lower().split())
    words2 = set(name2.lower().split())
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    return intersection / union if union else 0.0


class GrantMatchProcessor(BaseProcessor):
    """Processor for matching grants to charities"""

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.batch_size = 1000
        self.jaccard_threshold = 0.7

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
        batch = [dict(zip(['grant_id', 'grantee_name', 'colocator', 'tax_year'], row)) for row in rows]
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
            # Fetch charities ONCE for this colocator
            query_char = """
            SELECT ein, filer_name, (COALESCE(govt_amt, 0) + COALESCE(receipt_amt, 0)) AS wealth
            FROM Charities
            WHERE colocator = ?
            """
            charities = self.db_ops.execute_query(query_char, [colocator]).fetchall()
            
            if not charities:
                for item in grants:
                    updates.append({
                        'grant_id': item['grant_id'],
                        'recipient_ein': '8686'  # or 'NO_MATCH' / 'FAIL-86' / whatever you prefer
                    })
                continue  # No charities here, skip all grants for this colocator
            
            # Now process each grant using the shared charities list
            for item in grants:
                grant_id = item['grant_id']
                grantee_name = item['grantee_name']
                
                matches = []
                for row in charities:
                    ein, filer_name, wealth = row
                    jacc = word_jaccard(grantee_name, filer_name)
                    if jacc >= self.jaccard_threshold:
                        matches.append((jacc, ein, wealth))
                
                if matches:
                    # Pick highest jacc, tiebreak by wealth
                    matches.sort(key=lambda x: (-x[0], -x[2]))
                    best_ein = matches[0][1]
                else:
                    # Pick richest
                    best = max(charities, key=lambda x: x[2])
                    best_ein = best[0]
                
                updates.append({'grant_id': grant_id, 'recipient_ein': best_ein})
        
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
        