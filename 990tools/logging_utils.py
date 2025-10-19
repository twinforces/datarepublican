#!/usr/bin/env python3
"""
logging_utils.py - Centralized logging configuration for IRS 990 processing
"""

import logging
import sys
from typing import Optional


class IRS990Logger:
    """Centralized logging configuration for IRS 990 processing"""

    @staticmethod
    def setup_logger(name: str = "irs990", level: int = logging.WARNING,
                    log_file: Optional[str] = None) -> logging.Logger:
        """Setup and return a configured logger"""

        logger = logging.getLogger(name)
        logger.setLevel(level)

        # Remove existing handlers to avoid duplicates
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # Console handler
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler if specified
        if log_file:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        return logger

    @staticmethod
    def get_logger(name: str = "irs990") -> logging.Logger:
        """Get existing logger or create default one"""
        logger = logging.getLogger(name)
        if not logger.handlers:
            return IRS990Logger.setup_logger(name)
        return logger