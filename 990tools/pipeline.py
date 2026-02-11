# pipeline.py — THE ONE TRUE PIPELINE
# All your features. All correct. All yours.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional, Callable, Tuple, Generic, TypeVar, Union
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pending_database_context import PendingDatabaseContext
from database_operations import DatabaseOperations
from queue_status_display import QueueStatusDisplay
from constants import BATCH_SIZE, CONSUMER_BATCH_SIZE
import logging_utils

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
BATCHER_TIMEOUT = 1.0  # seconds — consider making adaptive later
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

@dataclass
class ResultWorkUnit(WorkUnit):
    """Specialized WorkUnit for pipeline results (PendingDatabaseContext)."""
    type: str = field(default='result', init=False)
    data: PendingDatabaseContext = field(default_factory=PendingDatabaseContext)

    @classmethod
    def result(cls, stage: str, context: PendingDatabaseContext) -> "ResultWorkUnit":
        return cls(stage=stage, data=context)

    def is_result(self) -> bool:
        return self.type == 'result'
    
DEBUG_PIPELINE = True

class AtomicCounter:
    def __init__(self, initial=0):
        self._value = initial
        self._lock = threading.Lock()

    def inc(self, amount=1):
        with self._lock:
            self._value += amount
        return self._value

    def dec(self, amount=1):
        with self._lock:
            self._value -= amount
        return self._value

    def get(self):
        with self._lock:
            return self._value

    def set(self, value):
        with self._lock:
            self._value = value
        
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
        pipeline: Optional["Pipeline[W]"] = None,
        *,
        next_on_success: Optional["PipelineStage[W]"] = None,
        next_on_failure: Optional["PipelineStage[W]"] = None,
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
        self.current_workers = AtomicCounter(0)
        self.batcher_thread = None        
        self.stop_event = threading.Event()
        self.workload = AtomicCounter(0)


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
            while not self.stop_event.is_set():
                unit = None
                try:
                    unit = self.input_queue.get(timeout=BATCHER_TIMEOUT)
                    if unit.is_sentinel():
                        print(f"[{self.name}] batcher got sentinel, unfinished_tasks now {self.input_queue.unfinished_tasks}")
                        if pending:
                            self._push_batch(pending)
                            pending = []
                        self.worker_queue.put(unit) # pass sentinel to workers
                        break #always break on sentinel
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
                        if self.stop_event.is_set(): break
                        continue
                except Exception as e:
                    print(f"ERROR in {self.name} batcher: {e.__class__.__name__}: {str(e)}")
                    continue
                finally:
                    if unit is not None:
                        print(f"[{self.name}] batcher got sentinel, unfinished_tasks now 1/2 {self.input_queue.unfinished_tasks}")
                        self.input_queue.task_done()
                        print(f"[{self.name}] batcher got sentinel, unfinished_tasks now 2/2 {self.input_queue.unfinished_tasks}")
            print(f"[{self.name}] batcher exited")

        self.batcher_thread = threading.Thread(target=batcher, daemon=True, name=f"{self.name}_batcher")
        self.batcher_thread.start()

    def _push_batch(self, batch_units: List[W]):
        if not batch_units:
            return
        if len(batch_units) == 1:
            self.workload.inc()
            self.worker_queue.put(batch_units[0])
        else:
            # Use the pipeline's workunit_class to create a batch of the correct type
            batch_obj = self.pipeline.workunit_class.batch(self.name, batch_units)
            self.workload.inc(len(batch_units))
            self.worker_queue.put(batch_obj)

    def _start_workers(self):
        for _ in range(self.workers):
            self._spawn_worker()

    def _spawn_worker(self):
        self.current_workers.inc()
        self.metrics['nThreads'] = self.current_workers.get()
        print(f"SPAWNING WORKER for {self.name} — total: {self.current_workers}")

        def worker():
            if DEBUG_PIPELINE:
                print(f"WORKER STARTED for {self.name}")
            pending = []

            exited=False
            while not self.stop_event.is_set() and not exited:
                unit = None
                try:
                    unit = self.worker_queue.get(timeout=1.0)
                    if unit and DEBUG_PIPELINE:
                        print(f"[{self.name}] GOT → {unit!s} (pending={len(pending)})")
                    if unit.is_sentinel():
                        if pending:
                            if DEBUG_PIPELINE: print(f"Sentinel in queue for {self.name} {len(pending)} to do")

                            self._process_batch(pending)
                            pending = []
                        self.worker_queue.task_done()
                        exited=True
                        self.current_workers.dec()
                        self.metrics['nThreads'] = self.current_workers.get()
                        break  # Explicit break on sentinel
                        return

                    if unit.is_batch():
                        pending.extend(unit.items)
                    else:
                        pending.append(unit)

                    while len(pending) >= self.batch_size:
                        batch_chunk = pending[:self.batch_size]
                        self._process_batch(batch_chunk)
                        pending = pending[self.batch_size:]

                except queue.Empty:
                    if pending:
                        self._process_batch(pending)
                        pending = []
                        if self.stop_event.is_set(): break
                        continue
                except Exception as e:
                    log_error(f"ERROR in {self.name} worker: {e}")
            print(f"[{self.name}] worker exited")  # Debug print for exit confirmation
            self.current_workers.dec()
            self.metrics['nThreads'] = self.current_workers.get()
        self.executor.submit(worker)

    def _process_batch(self, batch: List[W]):
        if not batch:
            return
        self.metrics['total'] += len(batch)
        self.metrics['current_queue'] = self.input_queue.qsize() + self.worker_queue.qsize()
        self.metrics['peak_queue'] = max(self.metrics['peak_queue'], self.metrics['current_queue'])
        if DEBUG_PIPELINE:
            print(f"[{self.name}] PROCESSING BATCH of {len(batch)} units")
        try:
            results = self.handler(batch)
            success_units = []
            failure_units = []

            for continue_flag, output in results:
                if isinstance(output, ResultWorkUnit):
                    # Success with result context — forward to consumer as ResultWorkUnit
                    self.pipeline.result_consumer.put(output)
                    if continue_flag:
                        self.metrics['success'] += 1
                    else:
                        self.metrics['failure'] += 1
                else:
                    # Forward the original unit (W subclass)
                    if continue_flag:
                        success_units.append(output)
                    else:
                        failure_units.append(output)

            if DEBUG_PIPELINE:
                sname = self.next_on_success.name if self.next_on_success else "None"
                fname = self.next_on_failure.name if self.next_on_failure else "None"
                print(f"[{self.name}] FORWARDING {len(success_units)} success [{sname}], {len(failure_units)} failure units [{fname}]") 
            self._forward(success_units, self.next_on_success)
            self._forward(failure_units, self.next_on_failure)

        except Exception as e:
            log_error(f"Handler failed in {self.name}: {e}")
            self._forward(batch, self.next_on_failure)
            self.metrics['failure'] += len(batch)
        finally:
            self.worker_queue.task_done()
            self.workload.dec(len(batch))

    def _forward(self, units: List[W], target: Optional["PipelineStage[W]"]):
        if not units:
            return
        for u in units:
            u.stage = target.name
        if target:
            if len(units) == 1:
                target.put(units[0])
            else:
                batch = self.pipeline.workunit_class.batch(self.name, units)
                target.put(batch)
        else:
            # Should only happen if no next stage — normally results go to consumer
            for unit in units:
                self.pipeline.result_consumer.put(unit)

    def put(self, unit: W):
        self.input_queue.put(unit)
        self.metrics['total'] += 1 if unit.is_work_item() else len(unit.items) if unit.is_batch() else 0

    def adjust_workers(self, new_workers: int):
        delta = new_workers - self.current_workers.get()
        if delta > 0:
            for _ in range(delta):
                self._spawn_worker()
        self.workers = new_workers
# ==============================
# ConsumerStage: Custom class because only the consumer can write to the DB
# ==============================
class ConsumerStage(PipelineStage):
    def __init__(self, name: str, db_ops: DatabaseOperations, threshold: int, pipeline: Optional["Pipeline"] = None):
        super().__init__(
            name=name,
            workers=1,
            batch_size=threshold,
            handler=self._consume_handler,
            pipeline=pipeline,
            max_workers=1,
        )
        self.db_ops = db_ops

    def _consume_handler(self, batch: List[ResultWorkUnit]) -> List[Tuple[bool, Any]]:
        print(f"###ERROR### CONSUMER HANDLER: Processing batch of {len(batch)} units, should never be called")
        merged = PendingDatabaseContext()
        for unit in batch:
            if unit.is_result():
                merged.merge([unit.data])
        self.db_ops.process_pdc(merged)
        return [(True, None) for _ in batch]  # No forward
    
    def _spawn_worker(self):
       self.current_workers.inc()
       self.metrics['nThreads'] = 1
       print(f"SPAWNING WORKER for {self.name} — total: {self.current_workers.get()}")

       def worker():
           if DEBUG_PIPELINE: print(f"WORKER STARTED for Consumer")
           pending_contexts: List[PendingDatabaseContext] = []
           accumulated_updates = 0
           exited=False
           while not self.stop_event.is_set() and not exited:
               unit = None
               try:
                    unit = self.input_queue.get(timeout=1.0)
                    if DEBUG_PIPELINE: print(f"[{self.name}] GOT → {unit!s} (pending={len(pending_contexts)})")
                    if unit and unit.is_result(): 
                        print(f"Result Unit recieved Pending Context, {len(pending_contexts)}")
                    
                    
                        
                    if unit.is_sentinel():
                        if pending_contexts:
                            self._flush_pending_contexts(pending_contexts, accumulated_updates)
                        exited=True
                        pending_contexts = []
                        accumulated_updates = 0
                        print(f"[{self.name}] EXITING on sentinel")
                        break
                    elif unit.is_result() and isinstance(unit.data, PendingDatabaseContext):
                        ctx = unit.data
                        pending_contexts.append(ctx)
                        added = ctx.estimated_updates or ctx.getTotalObjectCount() or 1
                        accumulated_updates += added
                        self.workload.inc(added)
                        print(f"[{self.name}] Added {added} updates to pending contexts, {len(pending_contexts)} {accumulated_updates} {self.batch_size}")
                        if accumulated_updates >= self.batch_size:
                            print(f"[{self.name}] Threshhold Reached — pending={len(pending_contexts)}, accumulated={accumulated_updates}")
                            self._flush_pending_contexts(pending_contexts, accumulated_updates)
                            pending_contexts = []
                            accumulated_updates = 0

                        self.metrics['success'] += 1
                        self.pipeline._record_global_success()
                    else:
                        print(f"Error wrong Unit type in 'result' {unit.type}")
                        
               except queue.Empty:
                   if pending_contexts:
                       self._flush_pending_contexts(pending_contexts, accumulated_updates)
                       pending_contexts = []
                       accumulated_updates = 0
                   continue
               except Exception as e:
                    print(f"[{self.name}] UNEXPECTED ERROR in get(): {e.__class__.__name__}: {str(e)}")
                    # Optional: add traceback
                    import traceback
                    traceback.print_exc()
                    # Continue loop — don't let thread die
                    continue
               finally:
                   if unit: self.input_queue.task_done()

           if pending_contexts:
               self._flush_pending_contexts(pending_contexts, accumulated_updates)
               pending_contexts=[]
           self.current_workers.dec()
           self.metrics['nThreads'] = self.current_workers.get()
           print(f"[{self.name}] worker exited")

       self.executor.submit(worker)

    def _flush_pending_contexts(self, contexts: List[PendingDatabaseContext], count: int):
        if not contexts:
            return
        print(f"Consumer flushing {len(contexts)} PDCs (~{count:,} updates)")
        merged = PendingDatabaseContext.merge(contexts)
        try:
            merged.save_to_database(self.db_ops)
        except Exception as e:
            print(f"[{self.name}] ERROR saving to database: {e}")
        
        self.workload.dec(len(contexts))

# ==============================
# Pipeline
# ==============================
class Pipeline(Generic[W]):
    
    master= None
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
        self.order = ['feed'] +[s.name for s in stages] + ['result']
        self.db_ops = db_ops
        self.workunit_class = workunit_class

        for stage in self.stages.values():
            stage.pipeline = self
        self.backpressure_enabled = backpressure_enabled

        # Feed handler: pass full W, not .data
        continue_flag = False if chain_on == 'failure' else True
        self.feed_stage = PipelineStage[W](
            name="feed",
            workers=1,
            batch_size=10000,
            handler=lambda batch: [(continue_flag, wu) for wu in batch],
            pipeline=self,
        )
        self.stages["feed"] = self.feed_stage

        self.result_consumer = ConsumerStage(
            name="result",
            db_ops=db_ops,
            threshold=CONSUMER_THRESHOLD,
            pipeline=self,
        )
        self.stages["result"] = self.result_consumer

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

        for stage in [self.feed_stage] + stages+ [self.result_consumer]:
            stage.set_executor(ThreadPoolExecutor(max_workers=stage.max_workers, thread_name_prefix=stage.name))
            

        if ADAPTIVE_BACKPRESSURE:
            self._start_adaptive_monitor()
        Pipeline.master = self

    def _record_global_total(self, count: int = 1):
        self.metrics['overall']['total'] += count

    def _record_global_success(self):
        self.metrics['overall']['success'] += 1

    def feed(self, items: List[Any]):
        if DEBUG_PIPELINE:
            print(f"FEEDING BATCH of {len(items)} items")
        wu_items = [self.workunit_class.work_item("feed", item.data) for item in items]
        batch = self.workunit_class.batch("feed", wu_items)
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
        
    @classmethod
    def result(cls, stage: str, context: PendingDatabaseContext) -> ResultWorkUnit:
        """Create a result unit (non-generic, always ResultWorkUnit)."""
        return ResultWorkUnit(stage=stage, data=context)

    def shutdown(self):
        """
        Deterministic sequential shutdown:
        - Poison each stage one-by-one, waiting for upstream to drain first
        - This lets work/sentinels flow naturally downstream
        """
        print("Initiating graceful shutdown — poisoning sequentially")
        print(f"shutdown order{self.order}")

        for i, stage_name in enumerate(self.order):
            # Poison with sentinels = current_workers + extra for batcher/safety
            stage = self.stages[stage_name]
            nThreads = stage.current_workers.get()
            for _ in range(nThreads ):
                stage.input_queue.put(WorkUnit.sentinel())

            print(f"Poisoned {stage.name} with {nThreads} sentinels")
            if stage.name == 'result':
                 print(f"result sentinels {nThreads}") # this is here for a breakpoint

            # Join input_queue: wait for batcher to flush to worker_queue
            print(f"joining input_queue for {stage.name}")
            stage.input_queue.join()

            print(f"joining worker_queue for {stage.name} {stage.workload.get()}")

            # Join worker_queue: wait for workers to process and exit
            print(f"  -> Joining worker_queue for {stage.name} (unfinished_tasks={stage.worker_queue.unfinished_tasks if hasattr(stage.worker_queue, 'unfinished_tasks') else 'N/A'})")
            stage.worker_queue.join()
            print(f"  -> worker_queue drained - stage {stage.name} done")            
            #stage.executor.shutdown(wait=False)  # Non-blocking shutdown
            #stage.executor._threads.clear()      # Force-clear internal thread refs (undocumented but works)
            #stage.executor=None # Force GC
            # Safety: force any lingering loops to break
            stage.stop_event.set()

            print(f"{stage.name} fully drained — {nThreads} workers exited")
            time.sleep(10) # what's your hurry?

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