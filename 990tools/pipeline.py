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
        handler: Callable[[List[Any]], List[tuple[bool, Any]]],
        pipeline: Optional["Pipeline"] = None,
        *,
        next_on_success: Optional["PipelineStage"] = None,
        next_on_failure: Optional["PipelineStage"] = None,
        is_final_failure: bool = False,
        max_workers: int = 64,
    ):
        self.name = name
        self.workers = workers
        if DEBUG_PIPELINE:
            self.workers=1
        self.batch_size = batch_size
        self.handler = handler
        self.pipeline = pipeline
        self.next_on_success = next_on_success
        self.next_on_failure = next_on_failure
        self.is_final_failure = is_final_failure
        self.max_workers = max_workers

        self.queue = queue.Queue()
        self.executor = None  # Set by set_executor
        self.current_workers = 0

        self.metrics = {
            'total': 0,
            'success': 0,
            'failure': 0,
            'current_queue': 0,
            'peak_queue': 0,
            'nThreads': 0,
        }

    def set_executor(self, executor: ThreadPoolExecutor):
        self.executor = executor
        self._start_workers()

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
                    unit = self.queue.get(timeout=1.0)
                    if DEBUG_PIPELINE: print(f"[{self.name}] GOT → {unit!s} (pending={len(pending)}, q={self.queue.qsize()})")                
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

                # Update queue depth
                current = self.queue.qsize()
                self.metrics['current_queue'] = current
                self.metrics['peak_queue'] = max(self.metrics['peak_queue'], current + 1)

                if unit.is_result():
                    if DEBUG_PIPELINE: print(f"Result Item for {self.name}")
                    unit.data.save_to_database()
                    self.metrics['success'] += 1
                    self.pipeline._record_global_success()
                    self.queue.task_done()
                    continue
                
                if DEBUG_PIPELINE: print(f"got work item {unit.type} for {self.name} {len(pending)} to do")
                if unit.is_batch():
                    pending.extend(unit.items)
                else:
                    pending.append(unit)

                if len(pending) >= self.batch_size:
                    self._process_batch(pending[:self.batch_size])
                    pending = pending[self.batch_size:]

                self.queue.task_done()

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

            for original_unit, (is_success, payload) in zip(batch_units, handler_results):
                # Create a new/fresh WorkUnit with updated payload
                # This preserves stage/producer/etc while allowing payload mutation
                updated_unit = original_unit.copy()
                updated_unit.data = payload

                if is_success:
                    self.success(updated_unit)
                else:
                    self.failure(updated_unit)  # failure expects payload, not unit


        except Exception as e:
            print(f"ERROR in {self.name} batch processing: {e.__class__.__name__}: {str(e)}")
            # On batch-level failure → fail all original items
            for unit in batch_units:
                self.failure(unit.data)
            self.metrics['failure'] += len(batch_units)
        
    def success(self, unit: WorkUnit):
        if self.next_on_success:
            forwarded = unit.copy()
            forwarded.stage = self.next_on_success.name
            self.next_on_success.put(forwarded)
        else:
            self.pipeline.result_consumer.put(
                WorkUnit.result(self.name, unit.data)
            )

    def failure(self, unit: WorkUnit):
        self.metrics['failure'] += 1
        if self.next_on_failure:
            forwarded = unit.copy()
            forwarded.stage = self.next_on_failure.name
            self.next_on_failure.put(forwarded)
        elif self.is_final_failure:
            # Producer handles final No_Match
            self.pipeline.result_consumer.put(
                WorkUnit.work_item("result", unit.data)  # or .result() if preferred
            )
        else:
            # Drop or log — depending on your policy
            print(f"Final drop of failed item in {self.name}: {unit.data.get('geocoding_id')}")
            
    def put(self, unit: WorkUnit):
        if DEBUG_PIPELINE: print(f"Adding {unit.type} to {self.name}")
        unit.stage = self.name
        self.queue.put(unit)
        
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
                self.queue.put(WorkUnit.sentinel())

        self.workers = target_workers
        self.metrics['nThreads'] = target_workers

class Pipeline:
    def __init__(
        self,
        stages: List[PipelineStage],
        backpressure_enabled: bool = BACKPRESSURE_ENABLED_DEFAULT,
    ):
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
            next_on_success=stages[0] if stages else None,
        )

        self.result_consumer = PipelineStage(
            name="result",
            workers=1,
            batch_size=1,
            handler=lambda items: [(True, item) for item in items],
            pipeline=self,
        )

        # Link chains
        for i, stage in enumerate(stages):
            if i + 1 < len(stages):
                stage.next_on_success = stages[i + 1]

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
        self.overall_total += count

    def _record_global_success(self):
        self.overall_success += 1

    def feed(self, items: List[Any]):
        batch = WorkUnit.batch("feed", [WorkUnit.work_item("feed", item) for item in items])
        self.feed_stage.put(batch)

    def run_with_provider(self, provider, max_items: Optional[int] = None):
        total = provider.get_total_work()
        if max_items is not None:
            total = min(total, max_items)
        self.metrics['overall']['expected'] = total
        self.overall_total= total
        print(f"Pipeline starting — {total:,} items")

        # Start QueueStatusDisplay — owned by Pipeline
        first_stage_queue = self.feed_stage.queue if hasattr(self, 'feed_stage') else list(self.stages.values())[0].queue
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

        # Step 1: Send enough sentinels to kill all feed workers
        # (we add extra as safety net)
        for _ in range(self.feed_stage.current_workers + 10):
            self.feed_stage.queue.put(WorkUnit.sentinel())

        # Step 2: Join stages in strict topological order
        stages_in_order = [self.feed_stage] + list(self.stages.values()) + [self.result_consumer]

        for stage in stages_in_order:
            print(f"Waiting for {stage.name} to drain (queue={stage.queue.qsize()}, unfinished={stage.queue.unfinished_tasks})")
            
            # queue.join() blocks until all .task_done() calls match .put() calls
            stage.queue.join()
            
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
    