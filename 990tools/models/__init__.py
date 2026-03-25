#!/usr/bin/env python3
"""
models/__init__.py - Model package initialization

This package contains all data model classes for the IRS 990 processing system.
Each model represents a database table and contains business logic specific to that entity.
"""

from .address import Address
from .charity import Charity
from .grant import Grant
from .officer import Officer
from .contractor import Contractor
from .political_contribution import PoliticalContribution
from .zip_file import ZipFile
from .xml_file import XMLFile
from .contribution import Contribution
from .geocoding import Geocoding
from .backfill import Backfill
from .pipeline_progress import PipelineProgress
from .authoritative_ein import AuthoritativeEin

__all__ = [
    'Address',
    'Charity',
    'Grant',
    'Officer',
    'Contractor',
    'PoliticalContribution',
    'ZipFile',
    'XMLFile',
    'Contribution',
    'Geocoding',
    'Backfill',
    'PipelineProgress',
    'AuthoritativeEin'
]