# pipeline.py — THE ONE TRUE PIPELINE
# All your features. All correct. All yours.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional, Callable, Tuple, Generic, TypeVar
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pending_database_context import PendingDatabaseContext
from database_operations import DatabaseOperations
from queue_status_display import QueueStatusDisplay
from constants import BATCH_SIZE, CONSUMER_BATCH_SIZE
# Generics first
W = TypeVar("W", bound="WorkUnit")
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
BATCHER_TIMEOUT = 10.0  # seconds — consider making adaptive later
CONSUMER_THRESHOLD = CONSUMER_BATCH_SIZE  # when to flush merged PDCs

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

    def is_work_item(self) -> bool:
        return self.type == 'work'

    def is_result(self) -> bool:
        return self.type == 'result'

    def is_sentinel(self) -> bool:
        return self.type == 'sentinel'

    def is_batch(self) -> bool:
        return self.type == 'batch'

    def copy(self) -> "WorkUnit":
        return WorkUnit(
            type=self.type,
            data=self.data,
            stage=self.stage,
            producer_id=self.producer_id,
            items=[item.copy() for item in self.items]
        )

    def __str__(self) -> str:
        if self.is_batch():
            return f"<Batch {self.stage} size={len(self.items)}>"
        return f"<WorkUnit {self.type} stage={self.stage}/>"

DEBUG_PIPELINE = True

# ==============================
# Base PipelineStage
# ==============================
class PipelineStage(Generic[W]):
    def __init__(
        self,
        name: str,
        workers: int,
        batch_size: int,
        handler: Callable[[List[W]], List[Tuple[bool, W]]],
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
            self.workers = 1
        self.batch_size = batch_size
        self.handler: Callable[[List[W]], List[Tuple[bool, W]]] = handler
        self.pipeline = pipeline
        self.next_on_success = next_on_success
        self.next_on_failure = next_on_failure
        self.is_final_failure = is_final_failure
        self.max_workers = max_workers

        self.input_queue = queue.Queue()
        self.worker_queue = queue.Queue()
        self.executor = None
        self.current_workers = 0
        self.batcher_thread = None

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
        self._start_batcher()
        self._start_workers()

    def _start_batcher(self):
        def batcher():
            pending = []
            while True:
                try:
                    unit = self.input_queue.get(timeout=BATCHER_TIMEOUT)
                    if unit.is_sentinel():
                        if pending:
                            self._push_batch(pending)
                            pending = []
                        self.worker_queue.put(unit)
                        continue

                    if unit.is_batch():
                        pending.extend(unit.items)
                    else:
                        pending.append(unit)

                    while len(pending) >= self.batch_size:
                        batch_chunk = pending[:self.batch_size]
                        if DEBUG_PIPELINE:
                            print(f"[{self.name}] BATCHING {len(batch_chunk)} units")
                        self._push_batch(batch_chunk)
                        pending = pending[self.batch_size:]

                except queue.Empty:
                    if pending:
                        self._push_batch(pending)
                        pending = []
                except Exception as e:
                    print(f"ERROR in {self.name} batcher: {e.__class__.__name__}: {str(e)}")

        self.batcher_thread = threading.Thread(target=batcher, daemon=True, name=f"{self.name}_batcher")
        self.batcher_thread.start()

    def _push_batch(self, batch_units: List[WorkUnit]):
        if not batch_units:
            return
        if len(batch_units) == 1:
            self.worker_queue.put(batch_units[0])
        else:
            self.worker_queue.put(WorkUnit.batch(self.name, batch_units))

    def _start_workers(self):
        for _ in range(self.workers):
            self._spawn_worker()

    def _spawn_worker(self):
        self.current_workers += 1
        self.metrics['nThreads'] = self.current_workers
        print(f"SPAWNING WORKER for {self.name} — total: {self.current_workers}")

        def worker():
            if DEBUG_PIPELINE:
                print(f"WORKER STARTED for {self.name}")
            pending = []

            while True:
                try:
                    unit = self.worker_queue.get(timeout=1.0)
                    if DEBUG_PIPELINE:
                        print(f"[{self.name}] GOT → {unit!s} (pending={len(pending)})")
                except queue.Empty:
                    if pending:
                        self._process_batch(pending)
                        pending = []
                    continue

                if unit.is_sentinel():
                    if pending:
                        self._process_batch(pending)
                    self.current_workers -= 1
                    self.metrics['nThreads'] = self.current_workers
                    return

                current = self.worker_queue.qsize()
                self.metrics['current_queue'] = current
                self.metrics['peak_queue'] = max(self.metrics['peak_queue'], current + 1)

                if unit.is_result():
                    # Normal stages forward results to consumer
                    if DEBUG_PIPELINE:
                        print(f"[{self.name}] FORWARDING RESULT → {unit!s}")
                    self.pipeline.result_consumer.put(unit)
                    self.metrics['success'] += 1
                    self.pipeline._record_global_success()
                    self.worker_queue.task_done()
                    continue

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
        if not batch_units:
            return

        self.metrics['total'] += len(batch_units)
        self.pipeline._record_global_total(len(batch_units))

        try:
            if DEBUG_PIPELINE:
                print(f"[{self.name}] PROCESSING BATCH of {len(batch_units)} units")
            handler_results = self.handler(batch_units)

            if len(handler_results) != len(batch_units):
                raise ValueError(f"Handler mismatch: {len(handler_results)} vs {len(batch_units)}")

            successes = []
            failures = []

            for original, (success, payload) in zip(batch_units, handler_results):
                updated = original.copy()
                updated.data = payload
                if success:
                    successes.append(updated)
                else:
                    failures.append(updated)

            self._forward(successes, self.next_on_success)
            self._forward(failures, self.next_on_failure)

        except Exception as e:
            print(f"ERROR in {self.name} batch: {e.__class__.__name__}: {str(e)}")
            failures = [u.copy() for u in batch_units]
            self._forward(failures, self.next_on_failure)
            self.metrics['failure'] += len(batch_units)

    def _forward(self, units: List[WorkUnit], target: Optional["PipelineStage"]):
        if not units:
            return
        if target:
            if len(units) == 1:
                target.put(units[0])
            else:
                target.put(self.pipeline.workunit_class.batch(self.name, units))
        else:
            # Should only happen if no next stage — normally results go to consumer
            for unit in units:
                self.pipeline.result_consumer.put(unit)

    def put(self, unit: WorkUnit):
        if DEBUG_PIPELINE:
            print(f"Adding {unit.type} to {self.name}")
        unit.stage = self.name
        self.input_queue.put(unit)

    def adjust_workers(self, target: int):
        target = max(1, target)
        diff = target - self.current_workers
        if diff > 0:
            print(f"SCALING UP {self.name}: {self.current_workers} → {target}")
            for _ in range(diff):
                self._spawn_worker()
        elif diff < 0:
            print(f"SCALING DOWN {self.name}: {self.current_workers} → {target}")
            for _ in range(-diff):
                self.worker_queue.put(WorkUnit.sentinel())
        self.workers = target
        self.metrics['nThreads'] = target

# ==============================
# Specialized Consumer Stage
# ==============================
class ConsumerStage(PipelineStage):
    def __init__(
        self,
        name: str,
        db_ops: DatabaseOperations,
        threshold: int = CONSUMER_BATCH_SIZE,
        pipeline: Optional["Pipeline"] = None,
    ):
        super().__init__(
            name=name,
            workers=1,
            batch_size=threshold,  # used only as hint / metric
            handler=lambda items: [(True, item) for item in items],  # identity
            pipeline=pipeline,
            is_final_failure=True,
        )
        self.db_ops = db_ops
        self.threshold = threshold

    def set_executor(self, executor: ThreadPoolExecutor):
        self.executor = executor
        # No batcher for consumer — PDC handles merging
        self._start_workers()

    def _start_workers(self):
        self._spawn_worker()

    def _spawn_worker(self):
        self.current_workers = 1
        self.metrics['nThreads'] = 1

        def consumer_worker():
            pending_contexts: List[PendingDatabaseContext] = []
            accumulated_updates = 0

            while True:
                try:
                    unit = self.worker_queue.get(timeout=1.0)
                except queue.Empty:
                    if pending_contexts:
                        self._flush(pending_contexts, accumulated_updates)
                        pending_contexts = []
                        accumulated_updates = 0
                    continue

                if unit.is_sentinel():
                    if pending_contexts:
                        self._flush(pending_contexts, accumulated_updates)
                    return

                if not unit.is_result() or not isinstance(unit.data, PendingDatabaseContext):
                    self.worker_queue.task_done()
                    continue

                ctx = unit.data
                pending_contexts.append(ctx)
                # Use estimated_updates if available, otherwise fallback
                added = ctx.estimated_updates or ctx.getTotalObjectCount() or 1
                accumulated_updates += added

                if accumulated_updates >= self.threshold:
                    self._flush(pending_contexts, accumulated_updates)
                    pending_contexts = []
                    accumulated_updates = 0

                self.metrics['success'] += 1
                self.pipeline._record_global_success()
                self.worker_queue.task_done()

        self.executor.submit(consumer_worker)

    def _flush(self, contexts: List[PendingDatabaseContext], count: int):
        if not contexts:
            return
        print(f"Consumer flushing {len(contexts)} PDCs (~{count:,} updates)")
        merged = PendingDatabaseContext.merge(contexts)
        merged.save_to_database(self.db_ops)

# ==============================
# Pipeline
# ==============================
class Pipeline(Generic[W]):
    def __init__(
        self,
        stages: List[PipelineStage[W]],
        db_ops: DatabaseOperations,
        backpressure_enabled: bool = BACKPRESSURE_ENABLED_DEFAULT,
        chain_on: str = 'success',
        workunit_class: type[W] = WorkUnit,
    ):
        if chain_on not in ('success', 'failure'):
            raise ValueError(f"Invalid chain_on: {chain_on}")
        self.chain_on = chain_on
        self.stages = {s.name: s for s in stages}
        self.order = [s.name for s in stages]
        self.db_ops = db_ops
        self.workunit_class = workunit_class

        for stage in self.stages.values():
            stage.pipeline = self
        self.backpressure_enabled = backpressure_enabled

        # Feed handler depends on chain_on: return (continue_flag, raw_data) to forward on chain path
        continue_flag = False if chain_on == 'failure' else True
        self.feed_stage = PipelineStage(
            name="feed",
            workers=1,
            batch_size=10000,
            handler=lambda batch: [(continue_flag, wu) for wu in batch],  # unpack to raw payload, flag for chain
            pipeline=self,
        )

        self.result_consumer = ConsumerStage(
            name="result",
            db_ops=db_ops,
            threshold=CONSUMER_THRESHOLD,
            pipeline=self,
        )

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

        for stage in [self.feed_stage, self.result_consumer] + stages:
            stage.set_executor(ThreadPoolExecutor(max_workers=stage.max_workers, thread_name_prefix=stage.name))

        if ADAPTIVE_BACKPRESSURE:
            self._start_adaptive_monitor()

    def _record_global_total(self, count: int = 1):
        self.metrics['overall']['total'] += count

    def _record_global_success(self):
        self.metrics['overall']['success'] += 1

    def feed(self, items: List[Any]):
        batch = self.workunit_class.batch("feed", [self.workunit_class.work_item("feed", item) for item in items])
        self.feed_stage.put(batch)

    def run_with_provider(self, provider, max_items: Optional[int] = None):
        total = provider.get_total_work()
        if max_items is not None:
            total = min(total, max_items)
        self.metrics['overall']['expected'] = total
        print(f"Pipeline starting — {total:,} items")

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

        print("Last batch sent — starting graceful shutdown...")
        self.shutdown()
        print(f"Pipeline complete — {processed:,} processed")

    def shutdown(self):
        print("Initiating graceful shutdown — poisoning feed stage only")

        for _ in range(self.feed_stage.current_workers + 10):
            self.feed_stage.input_queue.put(WorkUnit.sentinel())

        stages_in_order = [self.feed_stage] + list(self.stages.values()) + [self.result_consumer]

        for stage in stages_in_order:
            print(f"Waiting for {stage.name} to drain "
                  f"(input_q={stage.input_queue.qsize()}, "
                  f"worker_q={stage.worker_queue.qsize()}, "
                  f"unfinished={stage.worker_queue.unfinished_tasks})")
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
                        target = max(target - 2, max(1, MIN_WORKERS.get(stage_name, 1)))
                    if target != stage.workers:
                        print(f"ADAPTING {stage_name}: {stage.workers} → {target}")
                        stage.adjust_workers(target)

        threading.Thread(target=monitor, daemon=True).start()