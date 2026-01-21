# pipeline.py — THE ONE TRUE PIPELINE
# All your features. All correct. All yours.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional, Callable, Tuple
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pending_database_context import PendingDatabaseContext
from database_operations import DatabaseOperation, DatabaseOperationType
from datetime import datetime
from queue_status_display import QueueStatusDisplay
from constants import BATCH_SIZE

# ==============================
# Constants
# ==============================
BACKPRESSURE_ENABLED_DEFAULT = False
ADAPTIVE_BACKPRESSURE = False
BACKPRESSURE_THRESHOLD = 2000
IDLE_THRESHOLD = 500
ADAPTIVE_INTERVAL = 30
MIN_WORKERS = {'grok': 4, 'census': 1}
MAX_WORKERS = {'grok': 32, 'census': 8}
ADAPTIVE_STAGES = ['grok', 'census']
MONITOR_UPDATE = 30
BATCHER_TIMEOUT = 10.0  # Timeout for batcher to flush partial batches (seconds) TODO, make this adaptive collect avg time for a stage, wait max (this,avg)

# ==============================
# WorkUnit
# ==============================
@dataclass
class WorkUnit:
    type: str  # 'work', 'result', 'sentinel', 'batch'
    data: Any = None
    stage: Optional[str] = None
    producer_id: Optional[int] = None
    items: List["WorkUnit"] = field(default_factory=list)

    @classmethod
    def work_item(cls, stage: str, data: Any) -> "WorkUnit":
        return cls(type='work', data=data, stage=stage)

    @classmethod
    def result(cls, stage: str, context: PendingDatabaseContext) -> "WorkUnit":
        return cls(type='result', data=context, stage=stage)

    @classmethod
    def sentinel(cls, producer_id: int = 999) -> "WorkUnit":
        return cls(type='sentinel', producer_id=producer_id)

    @classmethod
    def batch(cls, stage: str, work_units: List["WorkUnit"]) -> "WorkUnit":
        return cls(type='batch', stage=stage, items=work_units)

    def is_work_item(self) -> bool: return self.type == 'work'
    def is_result(self) -> bool: return self.type == 'result'
    def is_sentinel(self) -> bool: return self.type == 'sentinel'
    def is_batch(self) -> bool: return self.type == 'batch'
    
    def copy(self) -> "WorkUnit":
        """
        Return a shallow copy of this WorkUnit.
        For batches, recursively copies the contained items.
        """
        return WorkUnit(
            type=self.type,
            data=self.data,
            stage=self.stage,
            producer_id=self.producer_id,
            items=[item.copy() for item in self.items]
        )

    def __str__(self) -> str:
        if self.is_batch():
            return f"<Batch {self.stage} size={len(self.items)}>[{self.items}]</Batch"
        return f"<WorkUnit {self.type} stage={self.stage}/>"

DEBUG_PIPELINE = True
# ==============================
# PipelineStage
# ==============================
class PipelineStage:
    def __init__(
        self,
        name: str,
        workers: int,
        batch_size: int,
        handler: Callable[[List[WorkUnit]], List[Tuple[bool, Any]]],
        pipeline: Optional["Pipeline"] = None,
        *,
        next_on_success: Optional["PipelineStage"] = None,
        next_on_failure: Optional["PipelineStage"] = None,
        is_final_failure: bool = False,
        max_workers: int = 64,
        is_consumer: bool = False
    ):
        self.name = name
        self.workers = workers
        if DEBUG_PIPELINE:
            self.workers = 1
        self.batch_size = batch_size
        self.handler = handler
        self.pipeline = pipeline
        self.next_on_success = next_on_success
        self.next_on_failure = next_on_failure
        self.is_final_failure = is_final_failure
        self.max_workers = max_workers

        self.input_queue = queue.Queue()  # Upstream puts here
        self.worker_queue = queue.Queue()  # Workers pull from here (batches/singles/sentinels)
        self.executor = None  # Set by set_executor
        self.current_workers = 0
        self.batcher_thread = None  # Set in _start_batcher

        self.metrics = {
            'total': 0,
            'success': 0,
            'failure': 0,
            'current_queue': 0,  # Refers to worker_queue.qsize()
            'peak_queue': 0,
            'nThreads': 0,
        }

    def set_executor(self, executor: ThreadPoolExecutor):
        self.executor = executor
        self._start_batcher()
        self._start_workers()

    def _start_batcher(self):
        """Start a dedicated thread to accumulate from input_queue and push batches to worker_queue."""

        def batcher():
            pending = []
            while True:
                try:
                    unit = self.input_queue.get(timeout=BATCHER_TIMEOUT)
                    if unit.is_sentinel():
                        if pending:
                            self._push_batch(pending)
                            pending = []
                        self.worker_queue.put(unit)  # Propagate sentinel
                        continue  # Keep running until all sentinels processed (workers will exit)

                    if unit.is_batch():
                        pending.extend(unit.items)
                    else:
                        pending.append(unit)

                    while len(pending) >= self.batch_size:  # Handle large incoming
                        batch_chunk = pending[:self.batch_size]
                        self._push_batch(batch_chunk)
                        pending = pending[self.batch_size:]

                except queue.Empty:
                    if pending:
                        self._push_batch(pending)
                        pending = []
                except Exception as e:
                    print(f"ERROR in {self.name} batcher: {e.__class__.__name__}: {str(e)}")
                    # Continue to avoid stalling; log and skip bad unit if needed

        self.batcher_thread = threading.Thread(target=batcher, daemon=True, name=f"{self.name}_batcher")
        self.batcher_thread.start()

    def _push_batch(self, batch_units: List[WorkUnit]):
        """Push a batch (or single if len==1) to worker_queue."""
        if not batch_units:
            return
        if len(batch_units) == 1:
            self.worker_queue.put(batch_units[0])
        else:
            self.worker_queue.put(WorkUnit.batch(self.stage, batch_units))

    def _start_workers(self):
        for _ in range(self.workers):
            self._spawn_worker()

    def _spawn_worker(self):
        self.current_workers += 1
        self.metrics['nThreads'] = self.current_workers
        print(f"SPAWNING WORKER for {self.name} — total workers: {self.current_workers}")
        def worker():
            if DEBUG_PIPELINE: print(f"WORKER STARTED for {self.name}")
            pending = []

            while True:
                try:
                    unit = self.worker_queue.get(timeout=1.0)
                    if DEBUG_PIPELINE: print(f"[{self.name}] GOT → {unit!s} (pending={len(pending)}, q={self.worker_queue.qsize()})")                
                except queue.Empty:
                    if pending:
                        if DEBUG_PIPELINE: print(f"Empty queue for {self.name} {len(pending)} to do")
                        self._process_batch(pending)
                        pending = []
                    continue

                if unit.is_sentinel():
                    if pending:
                        if DEBUG_PIPELINE: print(f"Sentinel in queue for {self.name} {len(pending)} to do")
                        self._process_batch(pending)
                    self.current_workers -= 1
                    self.metrics['nThreads'] = self.current_workers
                    return

                # Update queue depth (worker_queue)
                current = self.worker_queue.qsize()
                self.metrics['current_queue'] = current
                self.metrics['peak_queue'] = max(self.metrics['peak_queue'], current + 1)

                if unit.is_result():
                    if DEBUG_PIPELINE: print(f"Result Item for {self.name}")
                    if self == self.pipeline.result_consumer:
                        unit.data.save_to_database()
                    else:
                        self.pipeline.result_consumer.put(WorkUnit.result(self.name, unit.data))
                    self.metrics['success'] += 1
                    self.pipeline._record_global_success()
                    self.worker_queue.task_done()
                    continue
                
                if DEBUG_PIPELINE: print(f"got work item {unit.type} for {self.name} {len(pending)} to do")
                if unit.is_batch():
                    pending.extend(unit.items)
                else:
                    pending.append(unit)

                if len(pending) >= self.batch_size:
                    self._process_batch(pending[:self.batch_size])
                    pending = pending[self.batch_size:]

                self.worker_queue.task_done()

        self.executor.submit(worker)

    def _process_batch(self, batch_units: List[WorkUnit]):
        """
        Process a batch of WorkUnits using the handler.
        The handler returns List[Tuple[bool, Any]] where Any is either:
        - modified payload (dict) → continue as work item
        - PendingDatabaseContext → treat as final result
        """
        if not batch_units:
            return

        self.metrics['total'] += len(batch_units)
        self.pipeline._record_global_total(len(batch_units))

        try:
            handler_results = self.handler(batch_units)  # → List[Tuple[bool, Any]]

            if len(handler_results) != len(batch_units):
                raise ValueError(
                    f"Handler {self.name} returned {len(handler_results)} results "
                    f"for {len(batch_units)} inputs (length mismatch)"
                )

            successes = []
            failures = []

            for original_unit, (is_success, payload) in zip(batch_units, handler_results):
                # Create a new/fresh WorkUnit with updated payload
                # This preserves stage/producer/etc while allowing payload mutation
                updated_unit = original_unit.copy()
                updated_unit.data = payload

                if is_success:
                    successes.append(updated_unit)
                else:
                    failures.append(updated_unit)

            # Batch-forward groups
            self._forward(successes, self.next_on_success)
            self._forward(failures, self.next_on_failure)

        except Exception as e:
            print(f"ERROR in {self.name} batch processing: {e.__class__.__name__}: {str(e)}")
            # On batch-level failure → fail all original items
            failures = [unit.copy() for unit in batch_units]  # Copy to avoid mutation
            self._forward(failures, self.next_on_failure)
            self.metrics['failure'] += len(batch_units)
        
    def _forward(self, units: List[WorkUnit], target_stage: Optional["PipelineStage"]):
        """Forward a group of units to a target stage (batched if >1), or to results if None."""
        if not units:
            return

        if target_stage:
            # Put to target's input_queue (for its batcher to handle)
            if len(units) == 1:
                target_stage.put(units[0])
            else:
                target_stage.put(WorkUnit.batch(self.name, units))
        else:
            # No target: treat as final, forward to result_consumer as results
            for unit in units:
                if not isinstance(unit.data, PendingDatabaseContext):
                    raise ValueError(f"Final forward in {self.name} expected PendingDatabaseContext, got {type(unit.data)}")
                self.pipeline.result_consumer.put(WorkUnit.result(self.name, unit.data))

    def put(self, unit: WorkUnit):
        if DEBUG_PIPELINE: print(f"Adding {unit.type} to {self.name}")
        unit.stage = self.name
        self.input_queue.put(unit)  # Upstream puts to input_queue for batching
        
    def adjust_workers(self, target_workers: int):
        current = self.current_workers

        # Never go below 1 worker
        target_workers = max(1, target_workers)

        diff = target_workers - current

        if diff > 0:
            print(f"SCALING UP {self.name}: {current} → {target_workers}")
            for _ in range(diff):
                self._spawn_worker()
        elif diff < 0:
            print(f"SCALING DOWN {self.name}: {current} → {target_workers}")
            for _ in range(-diff):
                self.worker_queue.put(WorkUnit.sentinel())

        self.workers = target_workers
        self.metrics['nThreads'] = target_workers

class Pipeline:
    def __init__(
        self,
        stages: List[PipelineStage],
        backpressure_enabled: bool = BACKPRESSURE_ENABLED_DEFAULT,
        chain_on: str = 'success',  # 'success' or 'failure' for chaining path
    ):
        if chain_on not in ('success', 'failure'):
            raise ValueError(f"Invalid chain_on: {chain_on}")
        self.chain_on = chain_on
        self.stages = {s.name: s for s in stages}
        self.order = [s.name for s in stages]

        # Set pipeline for stages
        for stage in self.stages.values():
            stage.pipeline = self
        self.backpressure_enabled = backpressure_enabled

        # Built-in feed and result
        self.feed_stage = PipelineStage(
            name="feed",
            workers=1,
            batch_size=10000,
            handler=lambda items: [(True, item) for item in items],
            pipeline=self,
        )
        self.result_consumer = PipelineStage(
            name="result",
            workers=1,
            batch_size=1,
            handler=lambda items: [(True, item) for item in items],
            pipeline=self,
            is_consumer=True,
        )

        # Link chains based on chain_on
        chain_attr = 'next_on_success' if chain_on == 'success' else 'next_on_failure'
        for i, stage in enumerate(stages):
            if i + 1 < len(stages):
                setattr(stage, chain_attr, stages[i + 1])
        if stages:
            setattr(self.feed_stage, chain_attr, stages[0])

        self.metrics = {
            'overall': {'total': 0, 'success': 0, 'failure': 0, 'expected': 0},
            'stages': {}
        }
        for stage in stages:
            self.metrics['stages'][stage.name] = {
                'total': 0,
                'success': 0,
                'failure': 0,
                'current_queue': 0,
                'peak_queue': 0,
                'nThreads': stage.workers,
            }

        # Set executors
        for stage in [self.feed_stage, self.result_consumer] + stages:
            stage.set_executor(ThreadPoolExecutor(max_workers=stage.max_workers, thread_name_prefix=stage.name))

        if ADAPTIVE_BACKPRESSURE:
            self._start_adaptive_monitor()

    def _record_global_total(self, count: int = 1):
        self.metrics['overall']['total'] += count  # Fixed attribute error in original

    def _record_global_success(self):
        self.metrics['overall']['success'] += 1  # Fixed missing attr

    def feed(self, items: List[Any]):
        batch = WorkUnit.batch("feed", [WorkUnit.work_item("feed", item) for item in items])
        self.feed_stage.put(batch)

    def run_with_provider(self, provider, max_items: Optional[int] = None):
        total = provider.get_total_work()
        if max_items is not None:
            total = min(total, max_items)
        self.metrics['overall']['expected'] = total
        print(f"Pipeline starting — {total:,} items")

        # Start QueueStatusDisplay — owned by Pipeline
        first_stage_queue = self.stages[self.order[0]].worker_queue if self.stages else self.feed_stage.worker_queue
        self.queue_status_display = QueueStatusDisplay(
            tracking_queue=first_stage_queue,
            update_interval=MONITOR_UPDATE,
            custom_metrics_func=self.get_status
        )
        self.queue_status_display.start()

        processed = 0
        last_id = None

        while True:
            batch, new_last_id = provider.get_work_batch(last_id)
            if not batch:
                break

            # Respect max_items
            if max_items is not None:
                remaining = max_items - processed
                if remaining <= 0:
                    break
                if len(batch) > remaining:
                    batch = batch[:remaining]

            self.feed(batch)
            processed += len(batch)
            last_id = new_last_id

            if processed % 10000 == 0:
                print(f"→ {processed:,}/{total:,} fed")

        print("last batch sent - starting graceful shutdown...")
        self.shutdown()
        print(f"Pipeline complete — {processed:,} processed")

    def shutdown(self):
        """
        Deterministic shutdown:
        1. Poison only the entry point (feed_stage)
        2. Let sentinels flow through the chain
        3. Join queues in processing order
        """
        print("Initiating graceful shutdown — poisoning feed stage only")

        # Step 1: Send enough sentinels to kill all feed workers (extra as safety)
        for _ in range(self.feed_stage.current_workers + 10):
            self.feed_stage.input_queue.put(WorkUnit.sentinel())

        # Step 2: Join stages in strict topological order
        stages_in_order = [self.feed_stage] + list(self.stages.values()) + [self.result_consumer]

        for stage in stages_in_order:
            print(f"Waiting for {stage.name} to drain (input_q={stage.input_queue.qsize()}, worker_q={stage.worker_queue.qsize()}, unfinished={stage.worker_queue.unfinished_tasks})")
            
            # Join worker_queue (blocks until all task_done match puts)
            stage.worker_queue.join()
            
            print(f"{stage.name} fully drained — {stage.current_workers} workers exited")

        print("Pipeline shutdown complete — all stages drained deterministically")

    def get_status(self) -> dict:
        prefixed = {}
        for stage_name, stage in ({"feed": self.feed_stage, **self.stages, "result": self.result_consumer}).items():
            prefix = f"{stage_name}_"
            for k, v in stage.metrics.items():
                prefixed[f"{prefix}{k}"] = v

        prefixed.update({
            'overall_total': self.metrics['overall']['total'],
            'overall_success': self.metrics['overall']['success'],
            'overall_expected': self.metrics['overall']['expected'],
            'overall_failure': self.metrics['overall']['failure'],
            'in_flight': sum(s.metrics['current_queue'] for s in self.stages.values()),
        })

        return {
            'stages': ['feed'] + self.order + ['result'],
            'metrics': prefixed,
        }
    
    def _start_adaptive_monitor(self):
        def monitor():
            while True:
                time.sleep(MONITOR_UPDATE)
                for stage_name, stage in self.stages.items():
                    if stage_name not in ADAPTIVE_STAGES:
                        continue

                    current_queue = stage.metrics['current_queue']
                    current_workers = stage.current_workers

                    target = stage.workers

                    if current_queue > 3000:
                        target = min(target + 4, MAX_WORKERS.get(stage_name, 32))
                    elif current_queue < IDLE_THRESHOLD:
                        # NEVER DROP TO 0 — always keep at least 1 worker
                        target = max(target - 2, max(1, MIN_WORKERS.get(stage_name, 1)))

                    if target != stage.workers:
                        print(f"ADAPTING {stage_name}: {stage.workers} → {target} workers")
                        stage.adjust_workers(target)

        threading.Thread(target=monitor, daemon=True).start()    