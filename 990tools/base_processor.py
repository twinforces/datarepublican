#!/usr/bin/env python3
"""
base_processor.py - Base classes for processor implementations

This module contains base classes that provide common functionality
for all processor implementations in the IRS 990 processing system.
"""

import logging
import sys
import cProfile
import pstats
import io
import time
import threading
import queue
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_error, log_info, log_debug, log_warning, get_logger, start_progress_reporting
from config import global_config


class PoolConfig:
    """
    Configuration for a thread pool.

    Defines the parameters for managing a pool of worker threads,
    including the number of workers, queue size, batch size, and timeouts.
    """

    def __init__(self,
                 max_workers: int = 4,
                 queue_size: int = 1000,
                 batch_size: int = 100,
                 stall_threshold: int = 30,
                 shutdown_timeout: float = 30.0):
        """
        Initialize pool configuration.

        Args:
            max_workers: Maximum number of worker threads
            queue_size: Maximum size of the work queue
            batch_size: Size of batches for processing
            stall_threshold: Time in seconds before considering a worker stalled
            shutdown_timeout: Time in seconds to wait for threads to shutdown
        """
        self.max_workers = max_workers
        self.queue_size = queue_size
        self.batch_size = batch_size
        self.stall_threshold = stall_threshold
        self.shutdown_timeout = shutdown_timeout


class ThreadPoolConfig:
    """
    Configuration for thread pool management.

    Defines the overall configuration for thread pool operations,
    including producer and consumer pool configurations.
    """

    def __init__(self,
                 producer_config: PoolConfig = None,
                 consumer_config: PoolConfig = None,
                 enable_profiling: bool = False):
        """
        Initialize thread pool configuration.

        Args:
            producer_config: Configuration for producer thread pool
            consumer_config: Configuration for consumer thread pool
            enable_profiling: Whether to enable profiling for thread operations
        """
        self.producer_config = producer_config or PoolConfig(max_workers=4)
        self.consumer_config = consumer_config or PoolConfig(max_workers=1)  # Single consumer for DB safety
        self.enable_profiling = enable_profiling


class ThreadPoolManager:
    """
    Generalized thread pool manager for producer-consumer patterns.

    Manages worker threads, work queues, and coordination between producers
    and consumers. Ensures thread-safe operations and proper shutdown handling.
    """

    def __init__(self, config: ThreadPoolConfig, logger: logging.Logger = None):
        """
        Initialize the thread pool manager.

        Args:
            config: Thread pool configuration
            logger: Logger instance for thread operations
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.work_queue = queue.Queue(maxsize=config.producer_config.queue_size)
        self.result_queue = queue.Queue()
        self.shutdown_event = threading.Event()
        self.producer_threads = []
        self.consumer_threads = []
        self._lock = threading.Lock()

    def start_producer_pool(self, work_items: List[Any], producer_func: Callable, *args, **kwargs) -> None:
        """
        Start producer threads to process work items.

        Args:
            work_items: List of items to be processed by producers
            producer_func: Function to be executed by each producer thread
            *args: Additional positional arguments for producer_func
            **kwargs: Additional keyword arguments for producer_func
        """
        # Clear producer threads from previous runs to prevent accumulation
        self.producer_threads.clear()
        num_producers = min(self.config.producer_config.max_workers, len(work_items))
        self.log_info(f"Starting {num_producers} producer threads")

        for i in range(num_producers):
            thread = threading.Thread(
                target=self._producer_wrapper,
                args=(work_items, producer_func, i, num_producers, args, kwargs)
            )
            thread.daemon = True
            self.producer_threads.append(thread)
            thread.start()

    def start_consumer_pool(self, consumer_func: Callable, num_producers: int = 1, *args, **kwargs) -> None:
        """
        Start consumer threads to process results.

        Args:
            consumer_func: Function to be executed by each consumer thread
            num_producers: Number of producer threads (for sentinel counting)
            *args: Additional positional arguments for consumer_func
            **kwargs: Additional keyword arguments for consumer_func
        """
        num_consumers = self.config.consumer_config.max_workers
        self.log_info(f"Starting {num_consumers} consumer threads")

        for i in range(num_consumers):
            thread = threading.Thread(
                target=self._consumer_wrapper,
                args=(consumer_func, i, num_producers, args, kwargs)
            )
            thread.daemon = False  # Consumer threads must not be daemon threads for proper shutdown
            self.consumer_threads.append(thread)
            thread.start()

    def wait_for_completion(self) -> None:
        """
        Wait for all producer and consumer threads to complete.
        """
        # Wait for producers to finish
        self.log_info("Waiting for producer threads to complete...")
        for i, thread in enumerate(self.producer_threads):
            thread.join()  # Wait indefinitely for producer threads (they are daemon threads)
            self.log_debug(f"Producer thread {i} completed")

        # Wait for consumers to finish
        self.log_info("Waiting for consumer threads to complete...")
        for i, thread in enumerate(self.consumer_threads):
            thread.join()  # Wait indefinitely for consumer threads (they are now non-daemon)
            self.log_debug(f"Consumer thread {i} completed")

    def shutdown(self) -> None:
        """
        Shutdown the thread pool manager and clean up resources.
        """
        self.log_info("Shutting down thread pool manager")
        self.shutdown_event.set()

        # Wait for threads to finish
        self.wait_for_completion()

        # Clear thread lists
        self.producer_threads.clear()
        self.consumer_threads.clear()

    def _producer_wrapper(self, work_items: List[Any], producer_func: Callable,
                         thread_id: int, num_threads: int, args: tuple, kwargs: dict) -> None:
        """
        Wrapper for producer thread execution.

        Args:
            work_items: List of work items to process
            producer_func: Producer function to execute
            thread_id: ID of this producer thread
            num_threads: Total number of producer threads
            args: Additional positional arguments
            kwargs: Additional keyword arguments
        """
        try:
            self.log_debug(f"Producer thread {thread_id} starting")
            producer_func(work_items, self.work_queue, self.result_queue,
                         thread_id, num_threads, *args, **kwargs)
            self.log_debug(f"Producer thread {thread_id} completed")
        except Exception as e:
            self.log_error(f"Producer thread {thread_id} error: {e}", exc_info=True)

    def _consumer_wrapper(self, consumer_func: Callable, thread_id: int, num_producers: int,
                          args: tuple, kwargs: dict) -> None:
        """
        Wrapper for consumer thread execution.

        Args:
            consumer_func: Consumer function to execute
            thread_id: ID of this consumer thread
            num_producers: Number of producer threads (for sentinel counting)
            args: Additional positional arguments
            **kwargs: Additional keyword arguments
        """
        try:
            self.log_debug(f"Consumer thread {thread_id} starting")
            consumer_func(self.result_queue, thread_id, num_producers, *args, **kwargs)
            self.log_debug(f"Consumer thread {thread_id} completed")
        except Exception as e:
            self.log_error(f"Consumer thread {thread_id} error: {e}", exc_info=True)

    def log_info(self, msg: str, *args, **kwargs):
        """Log info message"""
        if self.logger:
            log_info(self.logger, msg, *args, **kwargs)

    def log_debug(self, msg: str, *args, **kwargs):
        """Log debug message"""
        if self.logger:
            log_debug(self.logger, msg, *args, **kwargs)

    def log_warning(self, msg: str, *args, **kwargs):
        """Log warning message"""
        if self.logger:
            log_warning(self.logger, msg, *args, **kwargs)

    def log_error(self, msg: str, *args, exc_info: bool = False, **kwargs):
        """Log error message"""
        if self.logger:
            log_error(self.logger, msg, *args, exc_info=exc_info, **kwargs)


class BaseProducer:
    """
    Base Producer class for operation collection.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class MUST NOT perform any database writes directly.
    Producers collect DatabaseOperation objects and send them to consumers.
    Only Consumer classes may execute database operations.

    This is a superclass for all *_producer.py classes to provide common functionality.
    """

    def __init__(self, db_ops: DatabaseOperations, batch_size: int = 1000, thread_pool_config: Optional[ThreadPoolConfig] = None):
        self.db_ops = db_ops
        self.batch_size = batch_size
        self.logger = get_logger(self.__class__.__name__)
        self._profile_seconds = None
        self._profiler = None
        self.thread_pool_config = thread_pool_config or ThreadPoolConfig()
        self.thread_pool_manager = None

    def collect_operations(self) -> List[DatabaseOperation]:
        """
        DEPRECATED: Use collect_contexts() instead for PDC-based processing.
        This method is kept for backward compatibility only.
        """
        # Convert PDC to operations for backward compatibility
        context = self.collect_contexts()
        if not context or context.isEmpty():
            return []

        # Convert context to operations
        return context.save_to_database(self.db_ops)

    def collect_contexts(self) -> 'PendingDatabaseContext':
        """
        Collect PendingDatabaseContext objects for processing.

        This is the preferred method for processors that can use PendingDatabaseContext
        to collect related objects before converting to DatabaseOperation objects.

        Returns:
            List of PendingDatabaseContext objects for the consumer to process
        """
        # Check if profiling is enabled
        if global_config.profile_seconds:
            return self._collect_contexts_with_profiling()

        # Setup progress bar
        progress_scope = self.get_progress_scope(bytes=global_config.progress == "bytes")
        total = progress_scope.get("total", 0)
        unit = progress_scope.get("unit", "items")
        start_progress_reporting(total=total, desc="Collecting contexts", unit=unit)

        # Create a single master context to accumulate all work
        from pending_database_context import PendingDatabaseContext
        master_context = PendingDatabaseContext()

        offset = 0
        work_items_processed = 0

        self.log_info(f"Starting to collect contexts (batch_size={self.batch_size})")

        while True:
            # Get next batch of work items
            batch = self._get_work_batch(offset)

            if not batch:
                # No more work to process
                break

            # Process this batch into a single context
            context = self._process_work_batch_to_context(batch)
            if context:
                # Merge this context into the master context
                master_context = PendingDatabaseContext.merge([master_context, context])

            # Count work items processed (each batch represents one work item)
            work_items_processed += len(batch)

            # Check if we've reached the global limit
            if global_config.max_files and work_items_processed >= global_config.max_files:
                self.log_info(f"Reached max_files limit: {global_config.max_files} work items")
                break

            offset += self.batch_size

            # Log progress
            if work_items_processed % 100 == 0:
                self.log_info(f"Collected contexts for {work_items_processed} work items so far")

        self.log_info(f"Collected master context with {work_items_processed} work items")
        return master_context

    def _process_work_batch_to_context(self, batch) -> Optional['PendingDatabaseContext']:
        """
        Process a batch of work items into a single PendingDatabaseContext object.

        This method should be overridden by subclasses that want to use
        PendingDatabaseContext instead of direct DatabaseOperation objects.

        Args:
            batch: Batch of work items to process

        Returns:
            Single PendingDatabaseContext object, or None if no work was processed
        """
        # Default implementation - subclasses should override this
        raise NotImplementedError("Subclasses using PendingDatabaseContext must implement _process_work_batch_to_context")

    def _collect_contexts_with_profiling(self) -> 'PendingDatabaseContext':
        """
        Collect contexts with profiling enabled for the specified duration.
        """
        self._profile_seconds = global_config.profile_seconds
        self._profiler = cProfile.Profile()

        self.log_info(f"Starting profiling for {self._profile_seconds} seconds during context collection")

        # Start profiling
        self._profiler.enable()
        start_time = time.time()

        # Create a single master context to accumulate all work
        from pending_database_context import PendingDatabaseContext
        master_context = PendingDatabaseContext()

        offset = 0
        work_items_processed = 0

        try:
            while True:
                # Check if profiling time has elapsed
                if time.time() - start_time >= self._profile_seconds:
                    self.log_info(f"Profiling time limit ({self._profile_seconds}s) reached during context collection")
                    break

                # Get next batch of work items
                batch = self._get_work_batch(offset)

                if not batch:
                    # No more work to process
                    break

                # Process this batch into a single context
                context = self._process_work_batch_to_context(batch)
                if context:
                    # Merge this context into the master context
                    master_context = PendingDatabaseContext.merge([master_context, context])

                # Count work items processed (each batch represents one work item)
                work_items_processed += len(batch)

                # Check if we've reached the global limit
                if global_config.max_files and work_items_processed >= global_config.max_files:
                    self.log_info(f"Reached max_files limit: {global_config.max_files} work items")
                    break

                offset += self.batch_size

                # Log progress
                if work_items_processed % 100 == 0:
                    self.log_info(f"Collected contexts for {work_items_processed} work items so far")

        finally:
            # Stop profiling
            self._profiler.disable()
            end_time = time.time()
            execution_time = end_time - start_time

            self.log_info(f"Context collection profiling complete. Time: {execution_time:.2f}s, Work items: {work_items_processed}")

            # Generate profiling report
            self._generate_profiling_report("collect_contexts", execution_time, work_items_processed)

        return master_context

    def collect_contexts_parallel(self, max_workers: int = None) -> 'PendingDatabaseContext':
        """
        Collect contexts using thread pool for parallel processing.

        Args:
            max_workers: Maximum number of producer threads (overrides config)

        Returns:
            List of PendingDatabaseContext objects for the consumer to execute
        """
        # Initialize thread pool manager
        self.thread_pool_manager = ThreadPoolManager(self.thread_pool_config, self.logger)

        # Override max_workers if specified
        if max_workers is not None:
            self.thread_pool_config.producer_config.max_workers = max_workers

        # Create a single master context to accumulate all work
        from pending_database_context import PendingDatabaseContext
        master_context = PendingDatabaseContext()

        work_items_processed = 0

        self.log_info(f"Starting parallel context collection (max_workers={self.thread_pool_config.producer_config.max_workers})")

        try:
            # Get all work items first (for parallel distribution)
            all_work_items = []
            offset = 0
            while True:
                batch = self._get_work_batch(offset)
                if not batch:
                    break
                all_work_items.extend(batch)

                # Check global limit
                if global_config.max_files and len(all_work_items) >= global_config.max_files:
                    all_work_items = all_work_items[:global_config.max_files]
                    break

                offset += self.batch_size

            if not all_work_items:
                return master_context

            # Start producer pool
            self.thread_pool_manager.start_producer_pool(
                all_work_items,
                self._parallel_work_batch_processor
            )

            # Collect results from result queue and merge into master context
            sentinels_received = 0
            num_producers = len(self.thread_pool_manager.producer_threads)
            while sentinels_received < num_producers:
                try:
                    result = self.thread_pool_manager.result_queue.get(timeout=1.0)
                    if result is None:  # Sentinel
                        sentinels_received += 1
                    elif isinstance(result, list):
                        # Each result is a list of contexts from one producer
                        # Merge them into the master context
                        if result:
                            master_context = PendingDatabaseContext.merge([master_context] + result)
                        work_items_processed += len(result)
                    self.thread_pool_manager.result_queue.task_done()
                except queue.Empty:
                    if self.thread_pool_manager.shutdown_event.is_set():
                        break
                    continue

            # Wait for completion
            self.thread_pool_manager.wait_for_completion()

        finally:
            # Cleanup
            if self.thread_pool_manager:
                self.thread_pool_manager.shutdown()

        self.log_info(f"Collected master context with {work_items_processed} work items (parallel)")
        return master_context

    def _parallel_work_batch_processor(self, work_items: List[Any], work_queue: queue.Queue,
                                      result_queue: queue.Queue, thread_id: int, num_threads: int) -> None:
        """
        Process work items in parallel using thread pool.

        Args:
            work_items: List of work items to process
            work_queue: Queue for intermediate work (not used in this implementation)
            result_queue: Queue to put results
            thread_id: ID of this thread
            num_threads: Total number of threads
        """
        try:
            # Create a single context for this thread's work
            from pending_database_context import PendingDatabaseContext
            thread_context = PendingDatabaseContext()

            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                work_item = work_items[i]

                # Process single work item into context and merge into thread context
                context = self._process_work_batch_to_context([work_item])
                if context:
                    thread_context = PendingDatabaseContext.merge([thread_context, context])

                # Log progress
                if (i + 1) % 50 == 0:
                    self.log_info(f"Producer {thread_id}: processed {i + 1}/{len(work_items)} work items")

            # Put the single merged context in result queue
            result_queue.put(thread_context)

        except Exception as e:
            self.log_error(f"Parallel work processor {thread_id} error: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)

    def _get_work_batch(self, offset: int) -> List[Any]:
        """Get a batch of work items - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement _get_work_batch")

    def _process_work_batch(self, batch: List[Any]) -> List[DatabaseOperation]:
        """DEPRECATED: Use _process_work_batch_to_context instead for PDC-based processing"""
        # Convert PDC to operations for backward compatibility
        context = self._process_work_batch_to_context(batch)
        if not context or context.isEmpty():
            return []
        return context.save_to_database(self.db_ops)

    def get_progress_scope(self) -> Dict[str, Any]:
        """Get the scope of work for progress bar setup - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement get_progress_scope")

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

    def _collect_operations_with_profiling(self) -> List[DatabaseOperation]:
        """DEPRECATED: Use PDC-based profiling instead"""
        # Convert PDC to operations for backward compatibility
        context = self._collect_contexts_with_profiling()
        if not context or context.isEmpty():
            return []
        return context.save_to_database(self.db_ops)

    def _generate_profiling_report(self, operation_name: str, execution_time: float, work_items_processed: int):
        """
        Generate profiling report files similar to profile_pipeline.py examples.
        """
        if not self._profiler:
            return

        # Get worker count from global config
        worker_count = getattr(global_config, 'workers', 1)

        # Create timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Generate filenames
        stats_filename = f"pipeline_profile_{timestamp}_{operation_name}_{worker_count}workers.stats"
        txt_filename = f"pipeline_profile_{timestamp}_{operation_name}_{worker_count}workers.txt"

        # Generate profiling stats
        s = io.StringIO()
        ps = pstats.Stats(self._profiler, stream=s).sort_stats('cumulative')
        ps.print_stats(50)  # Top 50 functions by cumulative time
        profiling_output = s.getvalue()

        # Calculate metrics
        processing_rate = work_items_processed / execution_time if execution_time > 0 else 0
        throughput = work_items_processed / execution_time * 60  # items per minute

        # Save stats file
        self._profiler.dump_stats(stats_filename)

        # Save human-readable report
        with open(txt_filename, "w") as f:
            f.write(f"=== IRS 990 {operation_name.title()} Profiling Report ===\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Execution Time: {execution_time:.2f} seconds\n")
            f.write(f"Work Items Processed: {work_items_processed}\n")
            f.write(f"Processing Rate: {processing_rate:.2f} items/second\n")
            f.write(f"Throughput: {throughput:.2f} items/minute\n")
            f.write(f"Worker Threads: {worker_count}\n\n")
            f.write("=== Top 50 Functions by Cumulative Time ===\n")
            f.write(profiling_output)

        self.log_info(f"Profiling complete. Results saved to:")
        self.log_info(f"  - {txt_filename} (human-readable report)")
        self.log_info(f"  - {stats_filename} (binary stats for further analysis)")

        # Print summary to console
        self.log_info("=== Profiling Summary ===")
        self.log_info(f"Execution time: {execution_time:.2f} seconds")
        self.log_info(f"Work items processed: {work_items_processed}")
        self.log_info(f"Processing rate: {processing_rate:.2f} items/sec")
        self.log_info(f"Throughput: {throughput:.2f} items/min")
        self.log_info("Top 10 most time-consuming functions:")
        lines = profiling_output.split('\n')
        for line in lines[:15]:  # First 15 lines contain the top functions
            if line.strip():
                self.log_info(line)


class ProcessorCoordinator:
    """
    Coordinator for processor operations using producer-consumer pattern.

    This class provides a standardized way to coordinate any processor that follows
    the collect operations → bulk execute pattern. It centralizes the threading logic
    and ensures all processors work consistently.
    """

    def __init__(self, db_ops: DatabaseOperations, logger: logging.Logger = None):
        self.db_ops = db_ops
        self.logger = logger or logging.getLogger(__name__)

    def process_with_producer_consumer(self, producer: 'BaseProducer', consumer: 'BaseConsumer',
                                      max_files: Optional[int] = None, workers: int = 4) -> int:
        """
        Process using producer-consumer pattern with standardized coordination.

        Args:
            producer: Producer instance that collects operations
            consumer: Consumer instance that executes operations
            max_files: Maximum number of files/items to process
            workers: Number of worker threads for parallel processing

        Returns:
            Number of items processed
        """
        # Set global max_files limit if specified
        if max_files is not None:
            global_config.max_files = max_files

        # Collect context using producer
        context = producer.collect_contexts_parallel(max_workers=workers)

        if not context or context.isEmpty():
            producer.log_info("No context to process")
            return 0

        # Execute context using consumer
        processed_count = consumer.execute_contexts_batch(context)

        producer.log_info(f"Processing complete: {processed_count} objects inserted")
        return processed_count


def dump_threads_handler(signum, frame):
    """Signal handler: Dumps formatted stack traces for all live threads."""
    try:
        import os
        # Get frame snapshots for all threads.
        frames = sys._current_frames()

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Stack traces for {len(frames)} threads (PID: {os.getpid()})", file=sys.stderr)
        print(f"Time: {time.ctime()}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        for thread_id, frame in frames.items():
            thread_name = threading.get_ident() == thread_id and "Main" or f"Thread-{thread_id}"
            print(f"\nThread: {thread_name} (ID: 0x{thread_id:x})", file=sys.stderr)

            # Extract and format the stack.
            stack_lines = traceback.format_stack(frame)
            print("".join(stack_lines), file=sys.stderr)

        print(f"{'='*60}\n", file=sys.stderr)
        sys.stderr.flush()  # Ensure output in signal context.
    except Exception as e:
        print(f"Error in thread dump: {e}", file=sys.stderr)
        sys.stderr.flush()


def setup_thread_dump_handler():
    """Setup SIGUSR1 handler for thread stack dumps (available in all processors)"""
    try:
        import signal
        signal.signal(signal.SIGUSR1, dump_threads_handler)
    except (OSError, ValueError):
        # Signal not available on this platform
        pass


class BaseConsumer:
    """
    Base Consumer class for database operations execution.

    PRODUCER-CONSUMER PATTERN WARNING:
    This class is responsible for executing database operations.
    Only consumers may perform database writes. Producers must never write to the database.

    This is a superclass for all *_consumer.py classes to provide common functionality.
    """

    def __init__(self, db_ops: DatabaseOperations, thread_pool_config: Optional[ThreadPoolConfig] = None):
        self.db_ops = db_ops
        self.logger = logging.getLogger(__name__)
        self._profile_seconds = None
        self._profiler = None
        self.thread_pool_config = thread_pool_config or ThreadPoolConfig()
        self.thread_pool_manager = None

    def execute_operations_batch(self, operations: List[DatabaseOperation], progress_callback=None) -> int:
        """
        DEPRECATED: Use execute_contexts_batch instead for PDC-based processing.

        Execute a batch of database operations.

        Args:
            operations: List of DatabaseOperation objects to execute
            progress_callback: Optional callback for progress updates

        Returns:
            Number of operations processed
        """
        # Convert operations to PDC and execute
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()

        # Add all operations to context
        for operation in operations:
            context.addOperationToDatabase(operation)

        return self.execute_contexts_batch(context, progress_callback)

    def execute_contexts_batch(self, context: 'PendingDatabaseContext', progress_callback=None) -> int:
        """
        Execute a PendingDatabaseContext object.

        This method executes all operations directly. PDC handles all operation execution internally.

        Args:
            context: PendingDatabaseContext object to execute
            progress_callback: Optional callback for progress updates

        Returns:
            Total number of objects inserted
        """
        if not context:
            return 0

        # Execute the context (handles all operations)
        ids = context.save_to_database(self.db_ops)

        return len(ids) if ids else 0
    
    def _execute_tail_operations(self, operations_by_type, processed_count):
        """Execute tail operations (optimize and progress updates)"""
        # Handle OPTIMIZE_DATABASE operations next to last, then progress bar
        if DatabaseOperationType.OPTIMIZE_DATABASE.value in operations_by_type:
            for operation in operations_by_type[DatabaseOperationType.OPTIMIZE_DATABASE.value]:
                self._execute_optimize_operation(operation)
                processed_count += 1

        progress_operations = 0
        if DatabaseOperationType.PROGRESS_UPDATE.value in operations_by_type:
            progress_operations = len(operations_by_type[DatabaseOperationType.PROGRESS_UPDATE.value])
            for operation in operations_by_type[DatabaseOperationType.PROGRESS_UPDATE.value]:
                from logging_utils import update_progress
                progress_count = operation.data.get("count", 0)
                self.log_debug(f"DEBUG: Processing PROGRESS_UPDATE operation with count={progress_count}")
                update_progress(n=progress_count)  # Use global progress bar
                processed_count += 1
        return processed_count

    def _execute_optimize_operation(self, operation):
        """Execute database optimization operation"""
        if operation.operation_type != DatabaseOperationType.OPTIMIZE_DATABASE:
            return

        log_info(self.logger, "Starting database optimization...")
        try:
            # Call the optimize_database method from DatabaseOperations
            self.db_ops.optimize_database()
            log_info(self.logger, "Database optimization completed successfully")
        except Exception as e:
            log_error(self.logger, f"Database optimization failed: {e}", exc_info=True)
            raise

    def _execute_progress_update_operation(self, operation):
        """Execute progress update operation"""
        if operation.operation_type != DatabaseOperationType.PROGRESS_UPDATE:
            return

        from logging_utils import update_progress
        progress_count = operation.data.get("count", 0)
        self.log_debug(f"DEBUG: Processing PROGRESS_UPDATE operation with count={progress_count}")
        update_progress(n=progress_count)

    
    def execute_operations_parallel(self, operations: List[DatabaseOperation], progress_callback=None) -> int:
        """DEPRECATED: Use PDC-based parallel execution instead"""
        # Convert operations to PDC and execute
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()

        # Add all operations to context
        for operation in operations:
            context.addOperationToDatabase(operation)

        return self.execute_contexts_batch(context, progress_callback)

    def _parallel_operations_processor(self, work_queue: queue.Queue, result_queue: queue.Queue,
                                      thread_id: int, num_producers: int, operations: List[DatabaseOperation],
                                      progress_callback=None) -> None:
        """DEPRECATED: Use PDC-based parallel processing instead"""
        # Convert operations to PDC and execute
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()

        # Add all operations to context
        for operation in operations:
            context.addOperationToDatabase(operation)

        processed = self.execute_contexts_batch(context, progress_callback)
        result_queue.put(processed)
        result_queue.put(None)  # Signal completion

    def _process_batch_operations(self, operations: List[DatabaseOperation], progress_callback=None) -> int:
        """DEPRECATED: Use PDC-based batch processing instead"""
        # Convert operations to PDC and execute
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()

        # Add all operations to context
        for operation in operations:
            context.addOperationToDatabase(operation)

        return self.execute_contexts_batch(context, progress_callback)

    def _execute_operations_batch_with_profiling(self, operations: List[DatabaseOperation], progress_callback=None) -> int:
        """DEPRECATED: Use PDC-based profiling instead"""
        # Convert operations to PDC and execute
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()

        # Add all operations to context
        for operation in operations:
            context.addOperationToDatabase(operation)

        return self.execute_contexts_batch(context, progress_callback)

    def _process_operations_batch(self, operations_by_type):
        """DEPRECATED: Use PDC-based processing instead"""
        # Convert operations to PDC and execute
        from pending_database_context import PendingDatabaseContext
        context = PendingDatabaseContext()

        # Add all operations to context
        for op_list in operations_by_type.values():
            for operation in op_list:
                context.addOperationToDatabase(operation)

        return self.execute_contexts_batch(context)