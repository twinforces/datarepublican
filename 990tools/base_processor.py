#!/usr/bin/env python3
"""
base_processor.py - Base classes for processor implementations

This module contains base classes that provide common functionality
for all processor implementations in the IRS 990 processing system.
"""

import logging
import sys
from typing import Optional, List, Dict, Any
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_error, log_info, log_debug, log_warning, get_logger
from config import global_config


class BaseProducer:
    """
    Base Producer class for operation collection.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.

    This is a superclass for all *_producer.py classes to provide common functionality.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000):
        self.db_ops = db_ops
        self.batch_size = batch_size
        self.logger = get_logger(self.__class__.__name__)

    def collect_operations(self) -> List[DatabaseOperation]:
        """
        Collect operations for processing.

        Returns:
            List of DatabaseOperation objects for the consumer to execute
        """
        operations = []
        offset = 0
        work_items_processed = 0

        self.log_info(f"Starting to collect operations (batch_size={self.batch_size})")

        while True:
            # Get next batch of work items
            batch = self._get_work_batch(offset)

            if not batch:
                # No more work to process
                break

            # Process this batch into operations
            batch_operations = self._process_work_batch(batch)
            operations.extend(batch_operations)

            # Count work items processed (each batch represents one work item)
            work_items_processed += len(batch)

            # Check if we've reached the global limit
            if global_config.max_files and work_items_processed >= global_config.max_files:
                # Truncate operations to match the limit
                # Find how many operations to keep based on work items processed
                operations_to_keep = 0
                work_count = 0
                for op in operations:
                    operations_to_keep += 1
                    if op.operation_type.value == "progress_update":
                        work_count += 1
                        if work_count >= global_config.max_files:
                            break

                operations = operations[:operations_to_keep]
                self.log_info(f"Reached max_files limit: {global_config.max_files} work items")
                break

            offset += self.batch_size

            # Log progress
            if work_items_processed % 100 == 0:
                self.log_info(f"Collected operations for {work_items_processed} work items so far")

        self.log_info(f"Collected total of {len(operations)} operations for {work_items_processed} work items")
        return operations

    def _get_work_batch(self, offset: int) -> List[Any]:
        """Get a batch of work items - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement _get_work_batch")

    def _process_work_batch(self, batch: List[Any]) -> List[DatabaseOperation]:
        """Process a batch of work items into operations - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement _process_work_batch")

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


class BaseConsumer:
    """
    Base Consumer class for database operations execution.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.

    This is a superclass for all *_consumer.py classes to provide common functionality.
    """

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops
        self.logger = logging.getLogger(__name__)

    def execute_operations_batch(self, operations: List[DatabaseOperation], progress_callback=None) -> int:
        """
        Execute a batch of database operations.

        Args:
            operations: List of DatabaseOperation objects to execute
            progress_callback: Optional callback for progress updates

        Returns:
            Number of operations processed
        """
        if not operations:
            return 0

        # Group operations by type for efficient processing
        operations_by_type = {}
        for operation in operations:
            if not isinstance(operation, DatabaseOperation):
                log_error(self.logger, f"Invalid operation type: {type(operation)}, expected DatabaseOperation")
                continue
            op_type = operation.operation_type.value
            if op_type not in operations_by_type:
                operations_by_type[op_type] = []
            operations_by_type[op_type].append(operation)

        # Call subclass-specific processing and get count
        processed_count = self._process_operations_batch(operations_by_type)

        # Handle PROGRESS_UPDATE operations AFTER the actual work is done
        progress_operations = 0
        if DatabaseOperationType.PROGRESS_UPDATE.value in operations_by_type:
            progress_operations = len(operations_by_type[DatabaseOperationType.PROGRESS_UPDATE.value])
            for operation in operations_by_type[DatabaseOperationType.PROGRESS_UPDATE.value]:
                from logging_utils import update_progress
                progress_count = operation.data.get("count", 0)
                update_progress(n=progress_count)  # Use global progress bar

        # Return total operations processed (progress updates + actual operations)
        return progress_operations + processed_count

    def _process_operations_batch(self, operations_by_type):
        """Process operations batch - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement _process_operations_batch")