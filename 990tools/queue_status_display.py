# queue_status_display.py
from __future__ import annotations
from queue import Queue
import time
import threading
from typing import Callable, Dict, Any, Optional, Set, List
from tqdm import tqdm
import psutil

from logging_utils import get_logger
from config import global_config

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# One source of truth: which gauges belong to which pipeline step(s)
# Add a new metric → one line here → it auto-shows only when relevant
# ----------------------------------------------------------------------
GAUGE_REGISTRY = [
    # (metric_key, description, applies_to_steps, total_fn, value_fn, format_fn)
    ("parsed_files_count",       "Parsed Files",       {"xml"},          lambda m: m.get("parsed_files_total", 100),     lambda m: m["parsed_files_count"],       lambda v: f"Count {v:,}"),
    ("pdc_operations_count",     "PDC Operations",     {"all"}, lambda m: m.get("pdc_operations_total", 10000), lambda m: m["pdc_operations_count"],   lambda v: f"Count {v:,}"),
    ("pdc_updates_count",        "PDC Updates",        {"all"}, lambda m: m.get("pdc_updates_total", 10000),    lambda m: m["pdc_updates_count"],      lambda v: f"Count {v:,}"),
    ("zip_cache_size",           "Zip Cache Size",     {"xml"},          lambda _: 36,                                    lambda m: m["zip_cache_size"],         lambda v: f"Count {v}"),
    ("outstanding_geocode_requests", "Outstanding Geocode", {"geolocate"}, lambda _: 10000,                           lambda m: m["outstanding_geocode_requests"], lambda v: f"Count {v:,}"),
    ("census_calls",             "Census Calls",       {"geolocate"},      lambda _: 100,   lambda m: m["census_calls"],           lambda v: f"Count {v}"),
    ("photon_calls",             "Photon Calls",       {"geolocate"},      lambda _: 100,   lambda m: m["photon_calls"],           lambda v: f"Count {v}"),
    ("librestreet_calls",        "LibreStreet Calls",  {"geolocate"},      lambda _: 100,   lambda m: m["librestreet_calls"],      lambda v: f"Count {v}"),
    ("nominatim_calls",          "Nominatim Calls",    {"geolocate"},      lambda _: 100,   lambda m: m["nominatim_calls"],        lambda v: f"Count {v}"),
    ("grok_calls",               "Grok Calls",         {"geolocate"},      lambda _: 100,   lambda m: m["grok_calls"],             lambda v: f"Count {v}"),
    ("opencage_calls",           "OpenCage Calls",     {"geolocate"},      lambda _: 100,   lambda m: m["opencage_calls"],         lambda v: f"Count {v}"),
    ("google_maps_calls",        "Google Maps Calls",  {"geolocate"},      lambda _: 100,   lambda m: m["google_maps_calls"],      lambda v: f"Count {v}"),
    ("name_search_calls",        "Name Search Calls",  {"geolocate"},      lambda _: 100,   lambda m: m["name_search_calls"],      lambda v: f"Count {v}"),
    # Example of "all steps" gauge
    ("processed_total",          "Total Processed",    {"all"},            lambda m: m.get("total_expected", 1),           lambda m: m.get("processed_total", 0), lambda v: f"Count {v:,}"),
]


class QueueStatusDisplay:
    """
    Fully dynamic, step-aware queue + system monitor.
    Gauges are created lazily and only shown when the current pipeline step needs them.
    """

    def __init__(
        self,
        tracking_queue: Queue,
        update_interval: float = 1.0,
        estimated_size_per_item_mb: float = 0.002,
        custom_metrics_func: Optional[Callable[[], Dict[str, Any]]] = None,
    ):
        self.queue = tracking_queue
        self.update_interval = update_interval
        self.est_item_bytes = estimated_size_per_item_mb * 1024 * 1024
        self.custom_metrics_func = custom_metrics_func

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process = psutil.Process()

        # Fixed bars (always shown)
        self.queue_bar = tqdm(total=100, desc="Queue Status", position=1, leave=True,
                              bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}')
        self.mem_bar = tqdm(total=100, desc="Memory Usage", position=2, leave=True,
                            bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}')

        # Lazily created gauges
        self._gauges: Dict[str, tqdm] = {}
        self._peaks: Dict[str, float] = {}
        self._active_keys: Set[str] = set()

    def start(self):
        if not self._thread:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        for bar in [self.queue_bar, self.mem_bar] + list(self._gauges.values()):
            bar.close()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._update_all()
                time.sleep(self.update_interval)
            except Exception as e:
                logger.exception(f"QueueStatusDisplay loop crashed: {e}")
                break

    def _update_all(self):
        custom = self.custom_metrics_func() if self.custom_metrics_func else {}
        current_step = custom.get("current_step", "unknown")  # e.g. "parse", "geolocate", "address"

        self._update_queue_bar(custom)
        self._update_memory_bar()
        self._prune_and_create_gauges(custom, current_step)
        self._update_active_gauges(custom)

    def _prune_and_create_gauges(self, metrics: dict, current_step: str):
        wanted_keys = {
            key for key, _, steps, _, _, _ in GAUGE_REGISTRY
            if (steps == {"all"} or current_step in steps) and key in metrics
        }

        # Close gauges that are no longer relevant
        for key in self._active_keys - wanted_keys:
            self._gauges[key].close()
            del self._gauges[key]
            self._peaks.pop(key, None)

        # Create new gauges that just became relevant
        next_position = 3  # start after memory bar
        for key, desc, _, _, _, _ in GAUGE_REGISTRY:
            if key in wanted_keys and key not in self._gauges:
                bar = tqdm(total=100, desc=desc, position=next_position, leave=True,
                           bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}')
                self._gauges[key] = bar
                self._peaks[key] = 0
                next_position += 1

        self._active_keys = wanted_keys

    def _update_active_gauges(self, metrics: dict):
        for key, desc, _, total_fn, value_fn, format_fn in GAUGE_REGISTRY:
            if key not in self._active_keys:
                continue
            value = value_fn(metrics)
            total = total_fn(metrics)
            self._peaks[key] = max(self._peaks[key], value)

            bar = self._gauges[key]
            pct = min((value / max(total, 1)) * 100, 100) if total > 0 else 0

            bar.n = int(pct)
            bar.set_postfix_str(f"{format_fn(value)} · Peak {self._peaks[key]:,.0f}")
            bar.refresh()

    def _update_queue_bar(self, custom: dict):
        try:
            stats = self.queue.get_queue_stats()
        except AttributeError:
            stats = {"current_size": self.queue.qsize(), "current_utilization": 1.0}

        size = stats.get("current_size", self.queue.qsize())
        utilization = stats.get("current_utilization", 1.0 if size else 0.0) * 100
        level = ["low", "medium", "high", "critical"][min(size // 10, 3)]

        est_gb = size * self.est_item_bytes / (1024**3)
        parts = [level.upper(), f"Size {size}", f"Est {est_gb:.2f}GB"]
        self.queue_bar.n = int(utilization)
        self.queue_bar.set_postfix_str(" · ".join(parts))
        self.queue_bar.refresh()

    def _update_memory_bar(self):
        mem_gb = self._process.memory_info().rss / (1024**3)
        peak = max(getattr(self, "_mem_peak", mem_gb), mem_gb)
        self.__dict__["_mem_peak"] = peak

        sys_mem = psutil.virtual_memory()
        pct = mem_gb / (sys_mem.total / (1024**3)) * 100
        status = ["LOW", "MEDIUM", "HIGH", "CRITICAL"][min(int(pct // 25), 3)]

        postfix = f"{mem_gb:.1f}GB · Peak {peak:.1f}GB · Free {sys_mem.available/(1024**3):.1f}GB · {status}"
        self.mem_bar.n = int(pct)
        self.mem_bar.set_postfix_str(postfix)
        self.mem_bar.refresh()