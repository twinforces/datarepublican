#!/usr/bin/env python3
"""
models/xml_file.py - XML file data model

This module contains the XMLFile dataclass and related business logic.
XML files represent individual IRS 990 filings extracted from ZIP archives.
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class XMLFile:
    """Represents an individual IRS 990 XML filing"""

    xml_id: Optional[int] = None
    zip_id: int = 0
    filename: str = ""
    internal_path: str = ""
    ein: Optional[str] = None
    tax_year: Optional[int] = None
    form_type: Optional[str] = None
    processed: bool = False
    processing_version: int = 0
    error_message: Optional[str] = None

    def is_processed_successfully(self) -> bool:
        """Check if XML file was processed without errors"""
        return self.processed and not self.error_message

    def mark_processed(self, error_message: Optional[str] = None):
        """Mark XML file as processed"""
        self.processed = True
        self.processed_at = datetime.now()
        self.error_message = error_message

    def to_dict(self) -> dict:
        """Convert to dictionary for database operations"""
        return {
            'xml_id': self.xml_id,
            'zip_id': self.zip_id,
            'filename': self.filename,
            'internal_path': self.internal_path,
            'ein': self.ein,
            'tax_year': self.tax_year,
            'form_type': self.form_type,
            'processed': self.processed,
            'processing_version': self.processing_version,
            'error_message': self.error_message
        }