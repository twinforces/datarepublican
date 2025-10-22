# logging_utils.py - Enhanced logging utilities with thread-safe progress reporting

import logging
import threading
import queue
import time
from typing import Optional, Callable
from tqdm import tqdm

class ThreadSafeProgressReporter:
    """Thread-safe progress reporting mechanism for multi-threaded operations"""

    def __init__(self, total: Optional[int] = None, desc: str = "", unit: str = "it", disable: bool = False):
        self.total = total
        self.desc = desc
        self.unit = unit
        self.disable = disable
        self.progress_queue = queue.Queue(maxsize=1000)
        self.stop_event = threading.Event()
        self.progress_thread = None
        self.pbar = None

    def start(self):
        """Start the progress reporting thread"""
        if self.progress_thread is not None:
            return

        self.progress_thread = threading.Thread(target=self._progress_worker, daemon=True)
        self.progress_thread.start()

    def stop(self):
        """Stop the progress reporting thread"""
        if self.progress_thread is None:
            return

        self.stop_event.set()
        self.progress_queue.put(None)  # Signal to stop
        self.progress_thread.join(timeout=1.0)

        if self.pbar:
            self.pbar.close()

    def update(self, n: int = 1):
        """Update progress by n steps"""
        if not self.stop_event.is_set():
            try:
                self.progress_queue.put(('update', n), block=False)
            except queue.Full:
                pass  # Drop updates if queue is full

    def set_description(self, desc: str):
        """Set progress bar description"""
        if not self.stop_event.is_set():
            try:
                self.progress_queue.put(('desc', desc), block=False)
            except queue.Full:
                pass

    def set_total(self, total: int):
        """Set total for progress bar"""
        if not self.stop_event.is_set():
            try:
                self.progress_queue.put(('total', total), block=False)
            except queue.Full:
                pass

    def _progress_worker(self):
        """Worker thread that manages the tqdm progress bar"""
        with tqdm(total=self.total, desc=self.desc, unit=self.unit, disable=self.disable) as pbar:
            self.pbar = pbar

            while not self.stop_event.is_set():
                try:
                    # Check for stop signal first
                    if self.stop_event.is_set():
                        break

                    # Wait up to 10 seconds for progress updates
                    item = self.progress_queue.get(timeout=10.0)
                    if item is None:  # Stop signal
                        break

                    action, value = item
                    if action == 'update':
                        pbar.update(value)
                    elif action == 'desc':
                        pbar.set_description(value)
                    elif action == 'total':
                        pbar.total = value
                        pbar.refresh()

                except queue.Empty:
                    # No progress updates in 10 seconds, continue waiting
                    continue
                except Exception as e:
                    # Log error but continue
                    print(f"Progress worker error: {e}")
                    break

# Global progress reporter instance
_progress_reporter = None

def get_progress_reporter(total: Optional[int] = None, desc: str = "", unit: str = "it", disable: bool = False) -> ThreadSafeProgressReporter:
    """Get or create a global thread-safe progress reporter"""
    global _progress_reporter
    if _progress_reporter is None:
        _progress_reporter = ThreadSafeProgressReporter(total=total, desc=desc, unit=unit, disable=disable)
    return _progress_reporter

def start_progress_reporting(total: Optional[int] = None, desc: str = "", unit: str = "it", disable: bool = False):
    """Start global progress reporting"""
    global _progress_reporter
    if _progress_reporter:
        _progress_reporter.stop()
    _progress_reporter = ThreadSafeProgressReporter(total=total, desc=desc, unit=unit, disable=disable)
    _progress_reporter.start()
    return _progress_reporter

def stop_progress_reporting():
    """Stop global progress reporting"""
    global _progress_reporter
    if _progress_reporter:
        _progress_reporter.stop()
        _progress_reporter = None

def update_progress(n: int = 1):
    """Update global progress"""
    global _progress_reporter
    if _progress_reporter:
        _progress_reporter.update(n)

def set_progress_description(desc: str):
    """Set global progress description"""
    global _progress_reporter
    if _progress_reporter:
        _progress_reporter.set_description(desc)

def set_progress_total(total: int):
    """Set global progress total"""
    global _progress_reporter
    if _progress_reporter:
        _progress_reporter.set_total(total)

# Enhanced logging functions with progress reporting integration
def log_info(logger: logging.Logger, msg: str, ein: Optional[str] = None, *args, **kwargs):
    """Log info message with optional EIN context"""
    if ein:
        msg = f"[EIN:{ein}] {msg}"
    logger.info(msg, *args, **kwargs)

def log_error(logger: logging.Logger, msg: str, ein: Optional[str] = None, exc_info: bool = False, *args, **kwargs):
    """Log error message with optional EIN context - always shown even in quiet mode"""
    if ein:
        msg = f"[EIN:{ein}] {msg}"
    logger.error(msg, *args, exc_info=exc_info, **kwargs)

def log_debug(logger: logging.Logger, msg: str, ein: Optional[str] = None, *args, **kwargs):
    """Log debug message with optional EIN context"""
    if ein:
        msg = f"[EIN:{ein}] {msg}"
    logger.debug(msg, *args, **kwargs)

def log_warning(logger: logging.Logger, msg: str, ein: Optional[str] = None, *args, **kwargs):
    """Log warning message with optional EIN context - always shown even in quiet mode"""
    if ein:
        msg = f"[EIN:{ein}] {msg}"
    logger.warning(msg, *args, **kwargs)

def get_logger(name: str) -> logging.Logger:
    """Get a configured logger"""
    return logging.getLogger(name)