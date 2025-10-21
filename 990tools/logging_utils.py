#!/usr/bin/env python3
"""
logging_utils.py - Standardized logging utilities for IRS 990 processing

This module provides standardized logging patterns and UUID constants for
log traceability across the IRS 990 processing system.
"""

import logging
import time
from typing import Optional

# Location-based logging utilities

def get_location_tag(filename: str, lineno: int) -> str:
    """Generate a location tag as filename:lineno"""
    return f"{filename}:{lineno}"

def log_with_location(logger: logging.Logger, level: int, message: str,
                     frame=None, **kwargs) -> None:
    """Log a message with file location tag for easy reference"""
    if frame is not None:
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        location_tag = get_location_tag(filename, lineno)
        kwargs['loc'] = location_tag

    log_with_context(logger, level, message, **kwargs)

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
    """Log info message with context"""
    log_with_context(logger, logging.INFO, message, ein, **kwargs)

def log_error(logger: logging.Logger, message: str, ein: Optional[str] = None, exc_info: bool = False, **kwargs) -> None:
    """Log error message with context"""
    log_with_context(logger, logging.ERROR, message, ein, **kwargs)
    if exc_info:
        logger.exception("Exception details:")

def log_debug(logger: logging.Logger, message: str, ein: Optional[str] = None, **kwargs) -> None:
    """Log debug message with context"""
    log_with_context(logger, logging.DEBUG, message, ein, **kwargs)

def log_warning(logger: logging.Logger, message: str, ein: Optional[str] = None, **kwargs) -> None:
    """Log warning message with context"""
    log_with_context(logger, logging.WARNING, message, ein, **kwargs)

# Location-aware logging functions (use these for precise location tracking)
def log_info_at(logger: logging.Logger, message: str, frame=None,
                ein: Optional[str] = None, **kwargs) -> None:
    """Log info message with file location tag"""
    log_with_location(logger, logging.INFO, message, frame=frame, ein=ein, **kwargs)

def log_error_at(logger: logging.Logger, message: str, frame=None,
                 ein: Optional[str] = None, exc_info: bool = False, **kwargs) -> None:
    """Log error message with file location tag"""
    log_with_location(logger, logging.ERROR, message, frame=frame, ein=ein, **kwargs)
    if exc_info:
        logger.exception("Exception details:")

def log_debug_at(logger: logging.Logger, message: str, frame=None,
                 ein: Optional[str] = None, **kwargs) -> None:
    """Log debug message with file location tag"""
    log_with_location(logger, logging.DEBUG, message, frame=frame, ein=ein, **kwargs)

def log_warning_at(logger: logging.Logger, message: str, frame=None,
                   ein: Optional[str] = None, **kwargs) -> None:
    """Log warning message with file location tag"""
    log_with_location(logger, logging.WARNING, message, frame=frame, ein=ein, **kwargs)