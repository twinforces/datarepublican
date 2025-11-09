#!/usr/bin/env python3
"""
address_matcher.py - Grant-to-charity matching for IRS 990 data

This module handles matching grants with unknown EINs to charities
based on address and colocator information, aligned with base_processor.py OOP patterns.
"""

import logging
import time
import queue
from typing import List, Tuple, Optional, Dict, Any

try:
    import tqdm
except ImportError:
    tqdm = None

from base_processor import BaseProcessor, BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig, DatabaseOperation, DatabaseOperationType
from database_operations import DatabaseOperations
from pending_database_context import PendingDatabaseContext
from logging_utils import log_info, log_debug, log_error, get_logger
from config import global_config


class AddressMatcherProducer(BaseProducer):
    """Producer for address matching operations"""

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000) -> None:
        super().__init__(db_ops, batch_size=batch_size)
        self.logger = get_logger("address_matcher")

    def _get_work_batch(self, last_pk: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get a batch of unmatched grants using key-value paging on UUID7 str grant_id pks"""
        where_clause = "recipient_ein IS NULL"
        params = None
        if last_pk is not None:
            where_clause += " AND grant_id > ?"
            params = (last_pk,)

        effective_batch_size = min(global_config.max_files, self.batch_size) if global_config.max_files else self.batch_size
        query = f"""
            SELECT grant_id, filer_ein, grant_amt, tax_year
            FROM Grants
            WHERE {where_clause}
            ORDER BY grant_id
            LIMIT ?
        """
        if params is None:
            params = (effective_batch_size,)
        else:
            params = params + (effective_batch_size,)

        result = self.db_ops.execute_query(query, params)
        if result:
            rows = result.fetchall()
            batch_data = [
                {
                    'grant_id': row[0],
                    'filer_ein': row[1],
                    'grant_amt': row[2],
                    'tax_year': row[3]
                }
                for row in rows
            ]

            max_pk = max(row[0] for row in rows) if rows else None

            if not global_config.is_quiet():
                log_debug(f"Retrieved {len(batch_data)} unmatched grants from database (last_pk={last_pk})")

            return batch_data, max_pk
        return [], None

    def _process_work_batch_to_context(self, batch: List[Dict[str, Any]]) -> Optional[PendingDatabaseContext]:
        """Process a batch of unmatched grants into PendingDatabaseContext object"""
        if not batch:
            return None

        context = PendingDatabaseContext()

        operation = DatabaseOperation(
            operation_type=DatabaseOperationType.GENERIC_UPDATE,
            data={
                "batch": batch,
                "operation": "match_grant_batch"
            }
        )
        context.addOperationToDatabase(operation)

        return context


class AddressMatcherConsumer(BaseConsumer):
    """Consumer for address matching operations"""

    def __init__(self, db_ops: DatabaseOperations) -> None:
        super().__init__(db_ops)
        self.logger = get_logger("address_matcher")

    def _process_operations_batch(self, operations_by_type: Dict[str, List[DatabaseOperation]]) -> int:
        """Process address matching operations"""
        if DatabaseOperationType.GENERIC_UPDATE.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.GENERIC_UPDATE.value]:
                if operation.data.get("operation") == "match_grant_batch":
                    self._execute_grant_match_operation(operation)
        return 0

    def _execute_grant_match_operation(self, operation: DatabaseOperation) -> None:
        """Execute a grant matching operation"""
        data = operation.data
        batch = data.get("batch", [])
        if not batch:
            return

        updates = []
        matched_count = 0
        for grant in batch:
            if self.exit_processing:
                break
            grant_id = grant['grant_id']
            filer_ein = grant['filer_ein']
            grant_amt = grant['grant_amt']
            tax_year = grant['tax_year']

            matched_ein = self._find_charity_by_grant_info(grant_id, filer_ein, grant_amt, tax_year)
            if matched_ein:
                updates.append({'grant_id': grant_id, 'recipient_ein': matched_ein})
                matched_count += 1
                if not global_config.is_quiet():
                    log_info(f"Matched grant {grant_id} to charity {matched_ein}")
            else:
                stub_ein = self._create_stub_charity_for_grant(grant_id, filer_ein, grant_amt, tax_year)
                if stub_ein:
                    updates.append({'grant_id': grant_id, 'recipient_ein': stub_ein})
                    if not global_config.is_quiet():
                        log_info(f"Created stub charity {stub_ein} for grant {grant_id}")

        # Build PDC for updates
        context = PendingDatabaseContext()
        for update in updates:
            db_op = DatabaseOperation(
                operation_type=DatabaseOperationType.GENERIC_UPDATE,
                data={
                    'table': 'Grants',
                    'updates': {'recipient_ein': update['recipient_ein']},
                    'key_field': 'grant_id',
                    'key_value': update['grant_id']
                }
            )
            context.addOperationToDatabase(db_op)

        self.update_pdc_size_gauge(context)
        ids = context.save_to_database(self.db_ops)

        if not global_config.is_quiet():
            log_debug(f"Updated {len(updates)} grants, {matched_count} matched")

        return

    def _find_charity_by_grant_info(self, grant_id: str, filer_ein: str, grant_amt: float, tax_year: int) -> Optional[str]:
        """Find charity EIN by grant information - preserve placeholder logic"""
        # This is a simplified implementation
        # In practice, this would need access to grant recipient information
        # For now, return None to create stubs
        return None

    def _create_stub_charity_for_grant(self, grant_id: str, filer_ein: str, grant_amt: float, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants - preserve placeholder logic"""
        # Generate a pseudo-EIN for stub records
        stub_ein = f"STUB{hash(f'{filer_ein}_{grant_id}_{tax_year}') % 1000000000:09d}"

        # Check if stub already exists - simplified for now
        return stub_ein


class AddressMatcher(BaseProcessor):
    """Main processor for grant-to-charity address matching using producer-consumer pattern"""

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000) -> None:
        super().__init__(db_ops)
        self.batch_size = batch_size
        self.logger = get_logger("address_matcher")

        # Initialize producer and consumer
        self.producer = AddressMatcherProducer(db_ops, batch_size)
        self.consumer = AddressMatcherConsumer(db_ops)

        # Initialize thread pool manager
        thread_config = ThreadPoolConfig(
            producer_config=PoolConfig(max_workers=4, queue_size=1000),  # Multiple producers for parallel processing
            consumer_config=PoolConfig(max_workers=1, queue_size=1000)   # Single consumer for DB safety
        )
        self.thread_pool_manager = ThreadPoolManager(thread_config, self)
        self.setup_status_gauges(interval=10.0, queues=[self.thread_pool_manager.result_queue])

    def _get_custom_metrics(self) -> Dict[str, Any]:
        try:
            unmatched_result = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Grants WHERE recipient_ein IS NULL"
            )
            unmatched = unmatched_result.fetchone()[0] if unmatched_result else 0

            total_result = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Grants"
            )
            total = total_result.fetchone()[0] if total_result else 0

            matched = total - unmatched

            return {
                'unmatched_grants': unmatched,
                'matched_grants': matched,
                **super()._get_custom_metrics()
            }
        except Exception as e:
            log_error(f"Error getting custom metrics: {e}")
            return super()._get_custom_metrics()

    def match_grants_by_address(self, progress_bar=None) -> int:
        """Match grants with unknown EINs by address or colocator using producer-consumer pattern.
 
        Args:
            progress_bar: Optional progress bar to update
 
        Returns:
            Number of grants processed
        """
        if not global_config.is_quiet():
            log_info("Matching grants with unknown EINs by address/colocator")

        try:
            # Get total count for progress
            total_result = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Grants WHERE recipient_ein IS NULL"
            )
            total_items = total_result.fetchone()[0] if total_result else 0

            if total_items == 0:
                if not global_config.is_quiet():
                    log_info("No grants with unknown EINs to match")
                return 0

            if progress_bar is None and tqdm is not None:
                progress_bar = tqdm.tqdm(total=total_items, desc="Matching grants", unit="grant")

            # Use standard parallel collection and execution
            context = self.producer.collect_contexts_parallel()

            if context and not context.isEmpty():
                self.consumer.execute_contexts_batch(context)
                if progress_bar:
                    progress_bar.update(total_items)

            if progress_bar is not None:
                progress_bar.close()

            if not global_config.is_quiet():
                log_info(f"Grant matching complete: {total_items} grants processed")

            return total_items

        except Exception as e:
            log_error(f"Grant matching failed: {e}", exc_info=True)
            if progress_bar is not None:
                progress_bar.close()
            return 0