#!/usr/bin/env python3
"""
address_deduplication_processor.py - Address deduplication processor

This module handles deduplication of addresses in the database by creating
master-child relationships based on canonical_address matching.

Now uses Producer-Consumer pattern for safe batch processing with DuckDB.
"""

import logging
import time
from typing import Optional, List, Dict, Any
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning
from config import global_config
from constants import ADDRESS_BATCH_SIZE, ADDRESS_QUEUE_SIZE
from base_processor import BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig


class AddressDeduplicationProducer(BaseProducer):
    """
    Producer for address deduplication - collects deduplication operations in batches.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.
    """

    def _get_work_batch(self, offset: int) -> List[Dict[str, Any]]:
        """Get a batch of address deduplication work items"""
        # Calculate last_address_id based on offset and batch_size
        # For pagination, we need to track the last processed address_id
        # Since we're using max(address_id) in the query, we can use offset to determine pagination
        last_address_id = None
        if offset > 0:
            # For subsequent batches, we need to find the last address_id from previous batches
            # This is a simplified approach - in practice, we might need to track this more carefully
            # For now, we'll use None for the first batch and track it in the processor
            pass

        # Use the processor's batch_size as the limit, respecting global_config.max_files
        batch_limit = self.batch_size
        if global_config.max_files and global_config.max_files < batch_limit:
            batch_limit = global_config.max_files

        return self.db_ops.get_address_deduplication_batch(last_address_id, batch_limit)

    def _process_work_batch(self, batch: List[Dict[str, Any]]) -> List[DatabaseOperation]:
        """Process a batch of deduplication work into operations"""
        operations = []

        # Create deduplication operations for each canonical address
        for dedup_info in batch:
            operation = DatabaseOperation(
                operation_type=DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH,
                data=dedup_info
            )
            operations.append(operation)

        # Create single progress update operation for the entire batch
        if batch:
            canonical_addresses_processed = len(batch)
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": canonical_addresses_processed}  # Count the number of canonical addresses processed
            )
            operations.append(progress_op)
            self.log_debug(f"Created progress update operation for {canonical_addresses_processed} canonical addresses")

        return operations


class AddressDeduplicationConsumer(BaseConsumer):
    """
    Consumer for address deduplication - executes deduplication operations.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.
    """

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)

    def _process_operations_batch(self, operations_by_type) -> int:
        """Process operations batch for address deduplication consumer"""
        total_updated = 0

        # Handle ADDRESS_DEDUPLICATION_BATCH operations
        if DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value]:
                batch_data = operation.data
                master_address_id = batch_data.get("master_address_id")
                child_address_ids = batch_data.get("child_address_ids", [])
                canonical_address = batch_data.get("canonical_address", "")

                if master_address_id and child_address_ids:
                    try:
                        updated_count = self.db_ops.execute_address_deduplication_batch(master_address_id, child_address_ids)
                        total_updated += 1

                        self.log_debug(f"Updated {updated_count} child addresses to point to master {master_address_id} for '{canonical_address[:50]}...'")

                    except Exception as e:
                        self.log_error(f"Failed to execute deduplication for master {master_address_id}: {e}")
                        raise
                else:
                    self.log_warning(f"Invalid deduplication operation data: master={master_address_id}, children={len(child_address_ids) if child_address_ids else 0}")

        return total_updated

    def _process_address_deduplication_batch_operations(self, operations_by_type):
        """Process address deduplication batch operations"""
        if DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value not in operations_by_type:
            return

        dedup_operations = operations_by_type[DatabaseOperationType.ADDRESS_DEDUPLICATION_BATCH.value]

        for operation in dedup_operations:
            batch_data = operation.data
            master_address_id = batch_data.get("master_address_id")
            child_address_ids = batch_data.get("child_address_ids", [])

            if master_address_id and child_address_ids:
                try:
                    updated_count = self.db_ops.execute_address_deduplication_batch(master_address_id, child_address_ids)
                    log_debug(self.logger, f"Updated {updated_count} child addresses to point to master {master_address_id}")
                except Exception as e:
                    log_error(self.logger, f"Failed to execute address deduplication batch for master {master_address_id}: {e}")
                    raise

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not global_config.is_quiet():
            log_info(self.logger, msg, *args, ein=ein)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not global_config.is_quiet():
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)

    def get_progress_scope(self, bytes: bool = False) -> Dict[str, Any]:
        """
        Get the estimated total work scope for address deduplication.

        Args:
            bytes: If True, return total in bytes instead of addresses

        Returns:
            Dictionary with 'total' (estimated work scope) and 'unit' ('addrs' or 'bytes')
        """
        if bytes:
            # Estimate total bytes by summing lengths of canonical addresses needing deduplication
            query = """
                SELECT SUM(LENGTH(canonical_address)) as total_bytes
                FROM (
                    SELECT canonical_address
                    FROM Addresses
                    WHERE canonical_address IS NOT NULL
                        AND canonical_address != ''
                    GROUP BY canonical_address
                    HAVING COUNT(*) > 1
                        AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
                )
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            return {'total': total, 'unit': 'bytes'}
        else:
            # Count total addresses needing deduplication
            query = """
                SELECT SUM(child_count) as total_addresses
                FROM (
                    SELECT SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) - 1 as child_count
                    FROM Addresses
                    WHERE canonical_address IS NOT NULL
                        AND canonical_address != ''
                    GROUP BY canonical_address
                    HAVING COUNT(*) > 1
                        AND SUM(CASE WHEN master_id IS NULL THEN 1 ELSE 0 END) > 1
                )
            """
            result = self.db_ops.execute_query(query)
            row = result.fetchone() if result else None
            total = int(row[0]) if row and row[0] is not None else 0
            return {'total': total, 'unit': 'addrs'}

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, *args, ein=ein, exc_info=exc_info)


class AddressDeduplicationProcessor:
    """
    Main processor for address deduplication using Producer-Consumer pattern.

    This processor coordinates producers and consumers to safely deduplicate
    addresses in batches, following the same pattern as XMLProcessor.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: Optional[int] = None):
        self.db_ops = db_ops
        self.batch_size = batch_size or ADDRESS_BATCH_SIZE
        self.logger = get_logger("address_dedup")

        # Throttle batch_size to global_config.max_files if specified (like XML processor does with --max_files)
        if global_config.max_files and global_config.max_files < self.batch_size:
            self.batch_size = global_config.max_files

        # Create thread pool configuration for address deduplication
        producer_config = PoolConfig(max_workers=4, queue_size=ADDRESS_QUEUE_SIZE, batch_size=self.batch_size)
        consumer_config = PoolConfig(max_workers=1, queue_size=ADDRESS_QUEUE_SIZE, batch_size=self.batch_size)  # Single consumer for DB safety
        self.thread_pool_config = ThreadPoolConfig(producer_config=producer_config, consumer_config=consumer_config)

        # Create producer and consumer instances
        self.producer = AddressDeduplicationProducer(db_ops, self.batch_size, self.thread_pool_config)
        self.consumer = AddressDeduplicationConsumer(db_ops)
        self.thread_pool_manager = None

    def deduplicate_addresses(self, progress_bar=None) -> int:
        """
        Deduplicate addresses by creating master-child relationships using ThreadPoolManager.

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of addresses processed (children updated)
        """
        self.log_info("Starting address deduplication with ThreadPoolManager")

        try:
            # Initialize thread pool manager
            thread_pool_manager = ThreadPoolManager(self.thread_pool_config, self.logger)

            # Initialize global progress bar if not provided
            if progress_bar is None:
                from logging_utils import start_progress_reporting
                progress_scope = self.consumer.get_progress_scope()
                total = progress_scope.get("total", 0)
                unit = progress_scope.get("unit", "addrs")
                start_progress_reporting(
                    total=total,
                    desc="Deduplicating addresses",
                    unit=unit
                )
                self.log_debug(f"Initialized progress bar with total={total} {unit}")
                self.log_info(f"Progress bar initialized: {total} {unit} to process")

            total_updated = 0

            # Collect all deduplication work items with pagination
            all_work_items = []
            last_address_id = None
            canonical_addresses_processed = 0

            while True:
                batch = self.db_ops.get_address_deduplication_batch(last_address_id, self.batch_size)
                if not batch:
                    break
                all_work_items.extend(batch)
                self.log_debug(f"Collected batch of {len(batch)} work items, total so far: {len(all_work_items)}")

                # Update last_address_id for next batch - find the max address_id from this batch
                if batch:
                    # Find the maximum address_id from all items in this batch
                    max_address_id = None
                    for item in batch:
                        master_id = item.get('master_address_id')
                        child_ids = item.get('child_address_ids', [])
                        all_ids = [master_id] + child_ids if master_id else child_ids
                        for addr_id in all_ids:
                            if addr_id and (max_address_id is None or addr_id > max_address_id):
                                max_address_id = addr_id
                    last_address_id = max_address_id

                # Check global limit
                if global_config.max_files and len(all_work_items) >= global_config.max_files:
                    all_work_items = all_work_items[:global_config.max_files]
                    self.log_info(f"Reached max_files limit: {global_config.max_files} work items")
                    break

            if not all_work_items:
                self.log_info("No deduplication work found")
                return 0

            self.log_info(f"Collected total of {len(all_work_items)} work items for processing")

            # Start producer pool to process work items
            thread_pool_manager.start_producer_pool(
                all_work_items,
                self._producer_worker_threaded
            )

            # Start consumer pool to execute operations
            thread_pool_manager.start_consumer_pool(
                self._consumer_worker_threaded,
                len(thread_pool_manager.producer_threads),
                progress_bar
            )
            self.log_debug(f"Started consumer pool with {len(thread_pool_manager.consumer_threads)} threads")

            # Wait for completion
            thread_pool_manager.wait_for_completion()

            # Collect results
            results_collected = 0
            self.log_debug(f"Starting result collection, result_queue size: {thread_pool_manager.result_queue.qsize()}")
            while not thread_pool_manager.result_queue.empty():
                try:
                    result = thread_pool_manager.result_queue.get_nowait()
                    if isinstance(result, int):
                        total_updated += result
                        results_collected += 1
                        self.log_debug(f"Collected result: {result}, total updated so far: {total_updated}, queue size now: {thread_pool_manager.result_queue.qsize()}")
                    thread_pool_manager.result_queue.task_done()
                except:
                    break
            self.log_info(f"Collected {results_collected} results from consumer threads")

            # Cleanup
            thread_pool_manager.shutdown()

            self.log_info(f"Address deduplication completed: {total_updated} addresses updated")
            return total_updated

        except Exception as e:
            self.log_error(f"Address deduplication failed: {e}", exc_info=True)
            return 0

        finally:
            # Ensure progress bar is cleaned up
            if progress_bar:
                from logging_utils import stop_progress_reporting
                stop_progress_reporting()
                self.log_debug("Progress bar cleanup completed")

    def _producer_worker_threaded(self, work_items: List[Dict[str, Any]], work_queue, result_queue, thread_id: int, num_threads: int) -> None:
        """Producer worker for ThreadPoolManager: processes work items into operations"""
        try:
            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                work_item = work_items[i]

                # Process single work item into operations
                operations = self.producer._process_work_batch([work_item])

                # Put operations in result queue for consumer
                result_queue.put(operations)
                self.log_debug(f"Producer {thread_id}: put {len(operations)} operations in queue, queue size now: {result_queue.qsize()}")

                # Log progress
                if (i + 1) % 50 == 0:
                    self.log_info(f"Producer {thread_id}: processed {i + 1}/{len(work_items)} work items")

        except Exception as e:
            self.log_error(f"Producer worker {thread_id} failed: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)
            self.log_debug(f"Producer {thread_id}: sent sentinel, queue size now: {result_queue.qsize()}")

    def _consumer_worker_threaded(self, result_queue, thread_id: int, num_producers: int, progress_bar=None) -> None:
        """Consumer worker for ThreadPoolManager: executes operations"""
        try:
            batch_operations = []
            total_updated = 0
            sentinels_received = 0

            while True:
                try:
                    # Get operations from result queue
                    self.log_debug(f"Consumer {thread_id}: waiting for operations, queue size: {result_queue.qsize()}")
                    operations = result_queue.get(timeout=1.0)
                    self.log_debug(f"Consumer {thread_id}: got operations from queue, queue size now: {result_queue.qsize()}")

                    if operations is None:  # Sentinel
                        sentinels_received += 1
                        self.log_debug(f"Consumer {thread_id}: received sentinel {sentinels_received}/{num_producers}")
                        if sentinels_received >= num_producers:
                            break
                        continue

                    if isinstance(operations, list):
                        batch_operations.extend(operations)

                        # Process batch when it reaches a reasonable size
                        if len(batch_operations) >= 50:  # Smaller batch size for address deduplication
                            self.log_debug(f"Consumer {thread_id}: processing batch of {len(batch_operations)} operations")
                            batch_updated = self.consumer.execute_operations_batch(batch_operations)
                            total_updated += batch_updated
                            self.log_debug(f"Consumer {thread_id}: batch processed, updated {batch_updated}, total so far: {total_updated}")
                            batch_operations = []  # Clear for next batch

                    result_queue.task_done()

                except:
                    if self.thread_pool_manager is not None and self.thread_pool_manager.shutdown_event.is_set():
                        break
                    continue

            # Process remaining operations
            if batch_operations:
                self.log_debug(f"Consumer {thread_id}: processing final batch of {len(batch_operations)} operations")
                batch_updated = self.consumer.execute_operations_batch(batch_operations)
                total_updated += batch_updated
                self.log_debug(f"Consumer {thread_id}: final batch processed, updated {batch_updated}, total: {total_updated}")

            # Put final result in result queue
            result_queue.put(total_updated)
            self.log_debug(f"Consumer {thread_id}: put final result {total_updated} in result queue")

        except Exception as e:
            self.log_error(f"Consumer worker {thread_id} failed: {e}", exc_info=True)
            result_queue.put(0)

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, *args, ein=ein, exc_info=exc_info)

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not global_config.is_quiet():
            log_info(self.logger, msg, *args, ein=ein)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not global_config.is_quiet():
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)