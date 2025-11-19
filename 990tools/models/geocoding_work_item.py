#!/usr/bin/env python3
"""
models/geocoding_work_item.py - Geocoding work item for API processing

This dataclass represents a unit of work for geocoding API calls, standardizing
the queue items as real objects per project conventions. Includes validation to
ensure data integrity before queuing.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class GeocodingWorkItem:
    """
    Represents a geocoding work item for API processing.

    Contains the geocoding record information needed to make API calls
    and update the database with results.
    """
    geocoding_id: str
    normalized_address: str
    address_id: str = None
    attempt_count: int = 0
    related_address_ids: list[str] = None  # List of related address_ids (master/child relationships)
    geocoding_obj: 'Geocoding' = None  # The full Geocoding object

    def __post_init__(self):
        """Validate the work item post-initialization."""
        if not self.geocoding_id:
            raise ValueError("geocoding_id cannot be empty")
        if not self.normalized_address or not self.normalized_address.strip():
            raise ValueError("normalized_address cannot be empty")
        if self.attempt_count < 0:
            raise ValueError("attempt_count cannot be negative")