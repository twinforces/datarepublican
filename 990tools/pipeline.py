# pipeline.py — THE ONE TRUE PIPELINE
# All your features. All correct. All yours.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional, Callable
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pending_database_context import PendingDatabaseContext
from database_operations import DatabaseOperation, DatabaseOperationType
from datetime import datetime


# ==============================
# Constants
# ==============================
BACKPRESSURE_ENABLED_DEFAULT = True
ADAPTIVE_BACKPRESSURE = True
BACKPRESSURE_THRESHOLD = 2000
IDLE_THRESHOLD = 500
ADAPTIVE_INTERVAL = 30
MIN_WORKERS = {'grok': 4, 'census': 1}
MAX_WORKERS = {'grok': 32, 'census': 8}
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

    def __str__(self) -> str:
        if self.is_batch():
            return f"<Batch {self.stage} size={len(self.items)}>[{self.items}]</Batch"
        return f"<WorkUnit {self.type} stage={self.stage}/>"


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

        def worker():
            pending = []

            while True:
                try:
                    unit = self.queue.get(timeout=10)
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

                # Update queue depth
                current = self.queue.qsize()
                self.metrics['current_queue'] = current
                self.metrics['peak_queue'] = max(self.metrics['peak_queue'], current + 1)

                if unit.is_result():
                    unit.data.save_to_database()
                    self.metrics['success'] += 1
                    self.pipeline._record_global_success()
                    self.queue.task_done()
                    continue

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
        data_list = [u.data for u in batch_units]
        self.metrics['total'] += len(batch_units)
        self.pipeline._record_global_total(len(batch_units))

        try:
            results = self.handler(data_list)

            for unit, (success, payload) in zip(batch_units, results):
                if success:
                    self.success(payload)
                else:
                    self.failure(payload)
        except Exception as e:
            print(f"ERROR {self.name}: {e}")
            for unit in batch_units:
                self.failure(unit.data)

    def success(self, payload: Any):
        if self.next_on_success:
            self.next_on_success.put(WorkUnit.work_item(self.next_on_success.name, payload))
        else:
            if isinstance(payload, PendingDatabaseContext):
                self.pipeline.result_consumer.put(WorkUnit.result(self.name, payload))

    def failure(self, payload: Any):
        self.metrics['failure'] += 1
        if self.next_on_failure:
            self.next_on_failure.put(WorkUnit.work_item(self.next_on_failure.name, payload))
        elif self.is_final_failure:
            # Producer handles No_Match
            self.pipeline.result_consumer.put(WorkUnit.work_item("result", payload))

    def put(self, unit: WorkUnit):
        unit.stage = self.name
        self.queue.put(unit)
        
    def adjust_workers(self, target_workers: int):
        """Dynamically adjust number of worker threads for this stage"""
        current = self.current_workers

        if target_workers > current:
            print(f"SCALING UP {self.name}: {current} → {target_workers}")
            for _ in range(target_workers - current):
                self._spawn_worker()
        elif target_workers < current:
            print(f"SCALING DOWN {self.name}: {current} → {target_workers}")
            # Send sentinels to kill excess workers
            diff = current - target_workers
            for _ in range(diff):
                self.queue.put(WorkUnit.sentinel(999))

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
        for name in self.order:
            self.metrics['stages'][name] = {
                'total': 0,
                'success': 0,
                'failure': 0,
                'current_queue': 0,
                'peak_queue': 0,
                'nThreads': self.stages[name]['workers'],
            }

        # Set executors
        for stage in [self.feed_stage, self.result_consumer] + stages:
            stage.set_executor(ThreadPoolExecutor(max_workers=stage.max_workers))

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
        self.metrics['overall']['expected'] = total
        print(f"Pipeline starting — {total:,} items")

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

        self.shutdown()
        print(f"Pipeline complete — {processed:,} processed")

    def shutdown(self):
        for stage in [self.feed_stage] + list(self.stages.values()):
            for _ in range(stage.current_workers + 10):
                stage.queue.put(WorkUnit.sentinel())

        for stage in [self.feed_stage] + list(self.stages.values()):
            stage.queue.join()

        self.result_consumer.queue.join()

    def get_status(self) -> dict:
        prefixed = {}
        for stage_name, stage in ({"feed": self.feed_stage, **self.stages, "result": self.result_consumer}).items():
            prefix = f"{stage_name}_"
            for k, v in stage.metrics.items():
                prefixed[f"{prefix}{k}"] = v

        prefixed.update({
            'overall_total': self.overall_total,
            'overall_success': self.overall_success,
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
                    current_queue = stage.metrics['current_queue']
                    current_workers = stage.current_workers

                    target = stage.workers

                    if current_queue > 3000:
                        target = min(target + 4, MAX_WORKERS.get(stage_name, 32))
                    elif current_queue < IDLE_THRESHOLD:
                        target = max(target - 2, MIN_WORKERS.get(stage_name, 1))

                    if target != stage.workers:
                        print(f"ADAPTING {stage_name}: {stage.workers} → {target} workers")
                        stage.adjust_workers(target)

        threading.Thread(target=monitor, daemon=True).start()
    
    