# logging_utils.py - Enhanced logging utilities

import logging
from typing import Optional, Callable

# Enhanced logging functions with progress reporting integration
def log_info(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, **kwargs):
    """Log info message with optional EIN context"""
    if not logger.isEnabledFor(logging.INFO):
        return
    if ein:
        logger.info(f"[EIN:{ein}] {msg}", *args, **kwargs)
    else:
        logger.info(msg, *args, **kwargs)

def log_error(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False, **kwargs):
    """Log error message with optional EIN context - always shown even in quiet mode"""
    if not logger.isEnabledFor(logging.ERROR):
        return
    if ein:
        logger.error(f"[EIN:{ein}] {msg}", *args, exc_info=exc_info, **kwargs)
    else:
        logger.error(msg, *args, exc_info=exc_info, **kwargs)

def log_debug(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, **kwargs) -> None:
    """Log debug message with optional EIN context"""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    if ein:
        logger.debug(f"[EIN:{ein}] {msg}", *args, **kwargs)
    else:
        logger.debug(msg, *args, **kwargs)
        
def log_warning(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, **kwargs):
    """Log warning message with optional EIN context - always shown even in quiet mode"""
    if not logger.isEnabledFor(logging.WARNING):
        return
    if ein:
        logger.warning(f"[EIN:{ein}] {msg}", *args, **kwargs)
    else:
        logger.warning(msg, *args, **kwargs)

def start_progress_reporting(total: int, desc: str = "Processing", unit: str = "items", disable: bool = False):
    """Start progress reporting with tqdm"""
    try:
        from tqdm import tqdm
        return tqdm(total=total, desc=desc, unit=unit, disable=disable)
    except ImportError:
        # Fallback if tqdm not available
        return None

def stop_progress_reporting():
    """Stop progress reporting"""
    # This is a no-op for now, tqdm handles its own cleanup
    pass

def update_progress(pbar, n: int = 1):
    """Update progress bar"""
    if pbar:
        pbar.update(n)

def set_progress_description(pbar, desc: str):
    """Set progress bar description"""
    if pbar:
        pbar.set_description(desc)

def get_logger(name: str) -> logging.Logger:
    """Get a configured logger"""
    return logging.getLogger(name)

def create_stub_log_error(logger: logging.Logger) -> Callable:
    """Factory function to create stub_log_error function"""
    def stub_log_error(msg_format, *args, ein=None, exc_info=False):
        """Fallback stub that uses proper logging with location info"""
        effective_logger = get_logger(__name__) if logger is None else logger
        log_error(effective_logger, msg_format.format(*args) if args else msg_format, ein=ein, exc_info=exc_info)
    return stub_log_error

def create_stub_log_debug(logger: logging.Logger) -> Callable:
    """Factory function to create stub_log_debug function"""
    def stub_log_debug(msg_format, *args, ein=None, exc_info=False):
        """Fallback stub that uses proper logging with location info"""
        effective_logger = get_logger(__name__) if logger is None else logger
        log_debug(effective_logger, msg_format.format(*args) if args else msg_format, ein=ein, exc_info=exc_info)
    return stub_log_debug