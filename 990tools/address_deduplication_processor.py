#!/usr/bin/env python3
"""
address_deduplication_processor.py - Address deduplication processor

This module handles deduplication of addresses in the database by creating
master-child relationships based on canonical_address matching.

Now uses Producer-Consumer pattern for safe batch processing with DuckDB.
"""

import logging
import threading
import queue
import time
from typing import Optional, List, Dict, Any
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning
from config import global_config
from constants import ADDRESS_BATCH_SIZE, ADDRESS_QUEUE_SIZE
from base_processor import BaseProducer, BaseConsumer


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
        return self.db_ops.get_address_deduplication_batch(
            batch_size=self.batch_size,
            offset=offset
        )

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
            progress_op = DatabaseOperation(
                operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                data={"count": len(batch)}  # Count the number of canonical addresses processed
            )
            operations.append(progress_op)

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

        # Create producer and consumer instances
        self.producer = AddressDeduplicationProducer(db_ops, self.batch_size)
        self.consumer = AddressDeduplicationConsumer(db_ops)

    def deduplicate_addresses(self, progress_bar=None) -> int:
        """
        Deduplicate addresses by creating master-child relationships using producer-consumer pattern with threading.

        Args:
            progress_bar: Optional progress bar to update

        Returns:
            Number of addresses processed (children updated)
        """
        self.log_info("Starting address deduplication with Producer-Consumer pattern")

        try:
            # Use producer-consumer pattern with threading for consistency with XML processor
            import queue
            import threading

            # Create shared condition for producer-consumer synchronization
            batch_condition = threading.Condition()

            # Create queues for producer-consumer communication
            operation_queue = queue.Queue(maxsize=1000)  # Queue for DatabaseOperation objects

            # Start consumer thread
            consumer_thread = threading.Thread(
                target=self._consumer_worker,
                args=(operation_queue, progress_bar, batch_condition)
            )
            consumer_thread.daemon = True
            consumer_thread.start()

            # Producer: collect operations and send to consumer
            producer_thread = threading.Thread(
                target=self._producer_worker,
                args=(operation_queue, batch_condition)
            )
            producer_thread.daemon = True
            producer_thread.start()

            # Wait for producer to finish
            producer_thread.join()

            # Signal consumer to finish and wait
            operation_queue.put(None)  # Sentinel value
            consumer_thread.join(timeout=30.0)

            if consumer_thread.is_alive():
                self.log_error("Consumer thread did not finish within timeout")
                return 0

            # Return the total updated count from consumer
            return getattr(consumer_thread, '_total_updated', 0)

        except Exception as e:
            self.log_error(f"Address deduplication failed: {e}", exc_info=True)
            return 0

    def _producer_worker(self, operation_queue, batch_condition):
        """Producer thread: collects deduplication operations"""
        try:
            canonical_addresses_processed = 0

            while True:
                # Get next batch of deduplication work (always first batch since OFFSET removed)
                batch = self.producer._get_work_batch(self.batch_size)

                if not batch:
                    # No more work - all canonical addresses have been processed
                    self.log_info("No more deduplication work found - all canonical addresses processed")
                    break

                # Process batch into operations
                operations = self.producer._process_work_batch(batch)

                # Send operations to consumer
                for operation in operations:
                    operation_queue.put(operation, block=True)

                # Send batch completion marker
                batch_completion = DatabaseOperation(
                    operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                    data={"batch_complete": True, "batch_size": len(batch)}
                )
                operation_queue.put(batch_completion, block=True)

                # Wait for consumer to signal batch completion
                with batch_condition:
                    batch_condition.wait()

                # Count canonical addresses processed
                canonical_addresses_processed += len(batch)

                # Check global max_files limit based on canonical addresses processed
                if global_config.max_files and canonical_addresses_processed >= global_config.max_files:
                    self.log_info(f"Reached max_files limit: {global_config.max_files} canonical addresses")
                    break

                # Log progress periodically
                if canonical_addresses_processed % 1000 == 0:
                    self.log_info(f"Processed {canonical_addresses_processed} canonical addresses so far")

            self.log_info(f"Producer completed: sent operations for {canonical_addresses_processed} canonical addresses")

        except Exception as e:
            self.log_error(f"Producer worker failed: {e}", exc_info=True)

    def _consumer_worker(self, operation_queue, progress_bar, batch_condition):
        """Consumer thread: executes deduplication operations"""
        try:
            batch_operations = []
            total_updated = 0

            while True:
                # Get next operation from queue
                operation = operation_queue.get()

                if operation is None:
                    # Sentinel value - process final batch and exit
                    if batch_operations:
                        batch_updated = self.consumer.execute_operations_batch(batch_operations)
                        total_updated += batch_updated
                        # Progress bar is updated by PROGRESS_UPDATE operations in execute_operations_batch
                    operation_queue.task_done()  # Mark sentinel as done
                    break

                # Check for batch completion marker
                if (operation.operation_type == DatabaseOperationType.PROGRESS_UPDATE and
                    operation.data.get("batch_complete")):
                    # Process any remaining operations in current batch
                    if batch_operations:
                        batch_updated = self.consumer.execute_operations_batch(batch_operations)
                        total_updated += batch_updated
                        batch_operations = []  # Clear for next batch

                    # Signal producer that batch is complete
                    with batch_condition:
                        batch_condition.notify()
                    operation_queue.task_done()
                    continue

                batch_operations.append(operation)

                # Process batch when it reaches a reasonable size
                if len(batch_operations) >= 50:  # Smaller batch size for address deduplication
                    batch_updated = self.consumer.execute_operations_batch(batch_operations)
                    total_updated += batch_updated
                    # Progress bar is updated by PROGRESS_UPDATE operations in execute_operations_batch
                    batch_operations = []  # Clear for next batch

                operation_queue.task_done()  # Mark each operation as done

            # Store total for return
            self._total_updated = total_updated
            self.log_info(f"Consumer processed {total_updated} address updates")

        except Exception as e:
            self.log_error(f"Consumer worker failed: {e}", exc_info=True)
            self._total_updated = 0

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