# logging_utils.py - Enhanced logging utilities

import logging
import inspect
import os
from typing import Optional, Callable
import sys
import traceback
from tqdm import tqdm
from config import global_config

# Global progress bar instance
_global_progress_bar = None

def get_debug_info():
    """Get debug information about the caller's location, traversing up until not in 'log' or 'database' files"""
    frame = inspect.currentframe()
    if frame is None:
        return {'file': '<unknown>', 'line': 0, 'function': '<unknown>'}

    # Traverse up the stack until we're not in a file with 'log' or 'database' in the name
    words = ['log', 'database'] #, 'base']
    while frame:
        filename = frame.f_code.co_filename
        function = frame.f_code.co_name
        if not any(word in filename.lower() for word in words) and not any(word in function.lower() for word in words):           
            break
        frame = frame.f_back

    if frame is None:
        return {'file': '<unknown>', 'line': 0, 'function': '<unknown>'}

    filename = os.path.basename(frame.f_code.co_filename) if frame.f_code.co_filename != '<string>' else '<interactive>'
    return {
        'file': filename,
        'line': frame.f_lineno,
        'function': frame.f_code.co_name
    }

# Enhanced logging functions with progress reporting integration
def log_info(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, **kwargs):
    """Log info message with optional EIN context and debug info"""
    if not logger.isEnabledFor(logging.INFO):
        return
    debug_info = get_debug_info()
    debug_prefix = f"[{debug_info['file']}:{debug_info['line']}:{debug_info['function']}] "
    if ein:
        logger.info(f"{debug_prefix}[EIN:{ein}] {msg}", *args, **kwargs)
    else:
        logger.info(f"{debug_prefix}{msg}", *args, **kwargs)

def log_error(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False, **kwargs):
    """Log error message with optional EIN context and debug info - always shown even in quiet mode"""
    if not logger.isEnabledFor(logging.ERROR):
        return
    debug_info = get_debug_info()
    debug_prefix = f"[{debug_info['file']}:{debug_info['line']}:{debug_info['function']}] "
    if ein:
        logger.error(f"{debug_prefix}[EIN:{ein}] {msg}", *args, exc_info=exc_info, **kwargs)
    else:
        logger.error(f"{debug_prefix}{msg}", *args, exc_info=exc_info, **kwargs)

def log_debug(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, **kwargs) -> None:
    """Log debug message with optional EIN context and debug info"""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    debug_info = get_debug_info()
    debug_prefix = f"[{debug_info['file']}:{debug_info['line']}:{debug_info['function']}] "
    if ein:
        logger.debug(f"{debug_prefix}[EIN:{ein}] {msg}", *args, **kwargs)
    else:
        logger.debug(f"{debug_prefix}{msg}", *args, **kwargs)
        
def log_warning(logger: logging.Logger, msg: str, *args, ein: Optional[str] = None, **kwargs):
    """Log warning message with optional EIN context and debug info - always shown even in quiet mode"""
    if not logger.isEnabledFor(logging.WARNING):
        return
    debug_info = get_debug_info()
    debug_prefix = f"[{debug_info['file']}:{debug_info['line']}:{debug_info['function']}] "
    if ein:
        logger.warning(f"{debug_prefix}[EIN:{ein}] {msg}", *args, **kwargs)
    else:
        logger.warning(f"{debug_prefix}{msg}", *args, **kwargs)

def start_progress_reporting(total: int, desc: str = "Processing", unit: str = "items"):
    """Start progress reporting with tqdm"""
    global _global_progress_bar
    try:
          # Always show progress bar, even in quiet mode
        # Force disable=False to ensure progress bar shows regardless of tty detection
        # Use file=sys.stderr to ensure output goes to stderr even in non-TTY environments
        _global_progress_bar = tqdm(total=total, desc=desc, unit=unit, disable=False, file=sys.stderr)
        return _global_progress_bar
    except ImportError:
        # Fallback if tqdm not available
        _global_progress_bar = None
        return None

def stop_progress_reporting():
    """Stop progress reporting"""
    # This is a no-op for now, tqdm handles its own cleanup
    pass

def update_progress(pbar=None, n: int = 1):
    """Update progress bar"""
    global _global_progress_bar
    if pbar:
        pbar.update(n)
    elif _global_progress_bar:
        _global_progress_bar.update(n)

def set_progress_description(pbar, desc: str):
    """Set progress bar description"""
    if pbar:
        pbar.set_description(desc)

def get_logger(name: str) -> logging.Logger:
    """Get a configured logger"""
    logger = logging.getLogger(name)
    # Ensure logger has a handler if it doesn't have one
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        # Set level to NOTSET so it inherits from root logger, allowing global control
        logger.setLevel(logging.NOTSET)
    return logger

def create_stub_log_error(logger: logging.Logger) -> Callable:
    """Factory function to create stub_log_error function"""
    def stub_log_error(msg, *args, ein=None, exc_info=False):
        """Fallback stub that uses proper logging with location info"""
        effective_logger = logger if logger is not None else get_logger(__name__)
        log_error(effective_logger, msg, *args, ein=ein, exc_info=exc_info)
    return stub_log_error

def create_stub_log_debug(logger: logging.Logger) -> Callable:
    """Factory function to create stub_log_debug function"""
    def stub_log_debug(msg, *args, ein=None, exc_info=False):
        """Fallback stub that uses proper logging with location info"""
        effective_logger = logger if logger is not None else get_logger(__name__)
        log_debug(effective_logger, msg, *args, ein=ein, exc_info=exc_info)
    return stub_log_debug

def dump_traceback(logger: logging.Logger, message: str = "Intentional exception for traceback"):
    """Logs the full stack trace without raising an exception"""
    try:
        raise Exception(message)
    except Exception:
        log_error(logger, f"Traceback dump: {message}", exc_info=True)
        pass