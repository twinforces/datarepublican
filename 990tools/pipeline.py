# pipeline.py
from __future__ import annotations
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Dict, Any
from base_processor import WorkUnit


class Pipeline:
    """
    SEDA pipeline using your existing WorkUnit.
    Each stage has its own queue and ThreadPoolExecutor.
    Failures automatically flow to next stage.
    Successes are saved immediately.
    """

    def __init__(self, stages: List[dict]):
        """
        stages = [
            {
                'name': 'census',
                'workers': 4,
                'batch_size': 1000,
                'handler': callable(batch: List[WorkUnit]) -> List[WorkUnit],
                'next_on_failure': 'grok'
            },
            ...
        ]
        """
        self.stages = {s['name']: s for s in stages}
        self.order = [s['name'] for s in stages]
        self.queues: Dict[str, queue.Queue] = {name: queue.Queue() for name in self.order}
        self.executors: Dict[str, ThreadPoolExecutor] = {}
        self._start_workers()

    def _start_workers(self):
        for stage_name, cfg in self.stages.items():
            executor = ThreadPoolExecutor(max_workers=cfg['workers'])
            self.executors[stage_name] = executor

            def make_worker(stage_cfg=cfg, q=self.queues[stage_name]):
                def worker():
                    while True:
                        batch = []
                        drained = False
                        for _ in range(stage_cfg['batch_size']):
                            try:
                                unit = q.get(timeout=1)
                                if unit.is_sentinel():
                                    drained = True
                                    break
                                if unit.is_work_item():
                                    batch.append(unit.data)
                            except queue.Empty:
                                break

                        if drained:
                            return

                        if not batch:
                            continue

                        try:
                            # Handler returns list of WorkUnits (success or failure)
                            results: List[WorkUnit] = stage_cfg['handler'](batch)

                            for result_unit in results:
                                if result_unit.is_result():
                                    # Save to DB immediately
                                    result_unit.data.save_to_database()
                                elif result_unit.is_work_item():
                                    # Failure — route to next stage
                                    next_stage = stage_cfg.get('next_on_failure')
                                    if next_stage:
                                        self.queues[next_stage].put(result_unit)
                                    else:
                                        # Final stage failure → No_Match
                                        self._mark_no_match(result_unit.data)
                        except Exception as e:
                            print(f"###ERROR### Stage {stage_name} handler crashed: {e}")
                            # On crash, route all forward
                            next_stage = stage_cfg.get('next_on_failure')
                            if next_stage:
                                for item in batch:
                                    self.queues[next_stage].put(WorkUnit.work_item(item))
                        finally:
                            for _ in batch:
                                q.task_done()

                return worker

            for _ in range(cfg['workers']):
                executor.submit(make_worker())

    def _mark_no_match(self, item):
        # Your existing logic to set No_Match
        print(f"###FINAL FAILURE### {item['geocoding_id']} → No_Match")

    def feed(self, items: List[Any], start_stage: str = None):
        first = start_stage or self.order[0]
        for item in items:
            self.queues[first].put(WorkUnit.work_item(item))

    def shutdown(self, wait: bool = True):
        # Send poison pills
        for stage_name in self.order:
            cfg = self.stages[stage_name]
            for _ in range(cfg['workers']):
                self.queues[stage_name].put(WorkUnit.sentinel(999))

        if wait:
            for q in self.queues.values():
                q.join()
            for ex in self.executors.values():
                ex.shutdown(wait=True)