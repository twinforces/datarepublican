#!/usr/bin/env python3
"""
logging_utils.py - Standardized logging utilities for IRS 990 processing

This module provides standardized logging patterns and UUID constants for
log traceability across the IRS 990 processing system.
"""

import logging
import inspect
import os
from typing import Optional

# Location-based logging utilities

def get_debug_info():
    """Get debug information about the caller's location"""
    frame = inspect.currentframe().f_back  # Get caller's frame
    if frame is None:
        return {'file': '<unknown>', 'line': 0, 'function': '<unknown>'}

    filename = os.path.basename(frame.f_code.co_filename) if frame.f_code.co_filename != '<string>' else '<interactive>'
    return {
        'file': filename,
        'line': frame.f_lineno,
        'function': frame.f_code.co_name
    }

# Standardized logging format
LOG_FORMAT = '%(asctime)s [%(levelname)8s] %(name)s: %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Set up a standardized logger"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger

def get_logger(name: str) -> logging.Logger:
    """Get or create a standardized logger"""
    return setup_logger(name)

# Context-aware logging helpers
def log_with_context(logger: logging.Logger, level: int, message: str,
                     ein: Optional[str] = None, **kwargs) -> None:
    """Log a message with optional context information"""
    if ein or kwargs:
        context_parts = []
        if ein:
            context_parts.append(f"ein={ein}")
        for key, value in kwargs.items():
            context_parts.append(f"{key}={value}")

        message = f"[{','.join(context_parts)}] {message}"

    logger.log(level, message)

# Convenience functions for common log levels
def log_info(logger: logging.Logger, message: str, ein: Optional[str] = None, **kwargs) -> None:
    """Log info message with context and location"""
    # Add location info to message for precise identification
    info = get_debug_info()
    location_tag = f"[{info['file']}:{info['line']}:{info['function']}]"
    message = f"{location_tag} {message}"
    log_with_context(logger, logging.INFO, message, ein, **kwargs)

def log_error(logger: logging.Logger, message: str, ein: Optional[str] = None, exc_info: bool = False, **kwargs) -> None:
    """Log error message with context and location"""
    # Add location info to message for precise identification
    info = get_debug_info()
    location_tag = f"[{info['file']}:{info['line']}:{info['function']}]"
    message = f"{location_tag} {message}"
    log_with_context(logger, logging.ERROR, message, ein, **kwargs)
    if exc_info:
        logger.exception("Exception details:")

def log_debug(logger: logging.Logger, message: str, ein: Optional[str] = None, **kwargs) -> None:
    """Log debug message with context and location"""
    # Add location info to message for precise identification
    info = get_debug_info()
    location_tag = f"[{info['file']}:{info['line']}:{info['function']}]"
    message = f"{location_tag} {message}"
    log_with_context(logger, logging.DEBUG, message, ein, **kwargs)

def log_warning(logger: logging.Logger, message: str, ein: Optional[str] = None, **kwargs) -> None:
    """Log warning message with context and location"""
    # Add location info to message for precise identification
    info = get_debug_info()
    location_tag = f"[{info['file']}:{info['line']}:{info['function']}]"
    message = f"{location_tag} {message}"
    log_with_context(logger, logging.WARNING, message, ein, **kwargs)

# All logging functions now include location info automatically