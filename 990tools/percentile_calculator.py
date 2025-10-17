#!/usr/bin/env python3
"""
percentile_calculator.py - Percentile calculations for IRS 990 data

This module calculates percentile rankings for charities by organization type and tax year.
"""

import logging
from typing import List, Tuple, Dict
from collections import defaultdict
from tqdm import tqdm

from database_operations import DatabaseOperations

# Set up logger
logger = logging.getLogger(__name__)


class PercentileCalculator:
    """Handles percentile calculations for charity metrics"""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

    def calculate_percentiles(self) -> int:
        """Calculate percentile rankings by org type and tax year (step 10)"""
        print("Calculating percentile rankings")

        # Get all charities grouped by org_type and tax_year
        charities = self.db_ops.get_charities_for_percentiles()
        logger.info(f"Retrieved {len(charities)} charity records for percentile calculation")

        # Check for EIN duplicates - these are expected data characteristics, not bugs
        # The get_latest.py script already handles selecting the latest XML file for duplicates
        ein_counts = defaultdict(int)
        for charity in charities:
            ein_counts[charity[2]] += 1  # charity[2] is ein

        duplicates = {ein: count for ein, count in ein_counts.items() if count > 1}
        if duplicates:
            logger.info(f"Found {len(duplicates)} EINs with multiple entries (expected for multi-year data): {list(duplicates.keys())[:10]}...")

        # Group by org_type and tax_year
        groups = defaultdict(lambda: defaultdict(list))
        for org_type, tax_year, ein, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct in charities:
            key = (org_type, tax_year)
            groups[org_type][tax_year].append({
                'ein': ein,
                'comp_pct': comp_pct,
                'travel_pct': travel_pct,
                'conferences_pct': conferences_pct,
                'grants_pct': grants_pct,
                'foreign_expenses_pct': foreign_expenses_pct
            })

        logger.info(f"Grouped charities into {len(groups)} org types with {sum(len(years) for years in groups.values())} total groups")

        total_groups = sum(len(years) for years in groups.values())
        processed_groups = 0

        # Calculate percentiles for each group
        with tqdm(total=total_groups, desc="Calculating percentiles") as pbar:
            for org_type, years in groups.items():
                for tax_year, org_charities in years.items():
                    if len(org_charities) < 2:
                        processed_groups += 1
                        pbar.update(1)
                        continue  # Need at least 2 for meaningful percentiles

                    # Extract values for each metric, filtering out NULL values and converting strings to floats
                    comp_values = []
                    for c in org_charities:
                        if c['comp_pct'] is not None:
                            try:
                                val = float(c['comp_pct']) if isinstance(c['comp_pct'], str) else c['comp_pct']
                                comp_values.append(val)
                            except (ValueError, TypeError):
                                continue

                    travel_values = []
                    for c in org_charities:
                        if c['travel_pct'] is not None:
                            try:
                                val = float(c['travel_pct']) if isinstance(c['travel_pct'], str) else c['travel_pct']
                                travel_values.append(val)
                            except (ValueError, TypeError):
                                continue

                    conferences_values = []
                    for c in org_charities:
                        if c['conferences_pct'] is not None:
                            try:
                                val = float(c['conferences_pct']) if isinstance(c['conferences_pct'], str) else c['conferences_pct']
                                conferences_values.append(val)
                            except (ValueError, TypeError):
                                continue

                    grants_values = []
                    for c in org_charities:
                        if c['grants_pct'] is not None:
                            try:
                                val = float(c['grants_pct']) if isinstance(c['grants_pct'], str) else c['grants_pct']
                                grants_values.append(val)
                            except (ValueError, TypeError):
                                continue

                    foreign_values = []
                    for c in org_charities:
                        if c['foreign_expenses_pct'] is not None:
                            try:
                                val = float(c['foreign_expenses_pct']) if isinstance(c['foreign_expenses_pct'], str) else c['foreign_expenses_pct']
                                foreign_values.append(val)
                            except (ValueError, TypeError):
                                continue


                    # Calculate percentiles for each charity
                    for charity in org_charities:
                        ein = charity['ein']

                        # Compensation percentile
                        comp_ptile = None
                        if charity['comp_pct'] is not None and comp_values:
                            try:
                                comp_val = float(charity['comp_pct']) if isinstance(charity['comp_pct'], str) else charity['comp_pct']
                                comp_values_sorted = sorted(comp_values)
                                comp_ptile = self._calculate_percentile(comp_val, comp_values_sorted)
                            except (ValueError, TypeError):
                                comp_ptile = None

                        # Travel percentile
                        travel_ptile = None
                        if charity['travel_pct'] is not None and travel_values:
                            try:
                                travel_val = float(charity['travel_pct']) if isinstance(charity['travel_pct'], str) else charity['travel_pct']
                                travel_values_sorted = sorted(travel_values)
                                travel_ptile = self._calculate_percentile(travel_val, travel_values_sorted)
                            except (ValueError, TypeError):
                                travel_ptile = None

                        # Conferences percentile
                        conferences_ptile = None
                        if charity['conferences_pct'] is not None and conferences_values:
                            try:
                                conferences_val = float(charity['conferences_pct']) if isinstance(charity['conferences_pct'], str) else charity['conferences_pct']
                                conferences_values_sorted = sorted(conferences_values)
                                conferences_ptile = self._calculate_percentile(conferences_val, conferences_values_sorted)
                            except (ValueError, TypeError):
                                conferences_ptile = None

                        # Grants percentile
                        grants_ptile = None
                        if charity['grants_pct'] is not None and grants_values:
                            try:
                                grants_val = float(charity['grants_pct']) if isinstance(charity['grants_pct'], str) else charity['grants_pct']
                                grants_values_sorted = sorted(grants_values)
                                grants_ptile = self._calculate_percentile(grants_val, grants_values_sorted)
                            except (ValueError, TypeError):
                                grants_ptile = None

                        # Foreign expenses percentile
                        foreign_ptile = None
                        if charity['foreign_expenses_pct'] is not None and foreign_values:
                            try:
                                foreign_val = float(charity['foreign_expenses_pct']) if isinstance(charity['foreign_expenses_pct'], str) else charity['foreign_expenses_pct']
                                foreign_values_sorted = sorted(foreign_values)
                                foreign_ptile = self._calculate_percentile(foreign_val, foreign_values_sorted)
                            except (ValueError, TypeError):
                                foreign_ptile = None

                        # Update database
                        self.db_ops.update_charity_percentiles(
                            ein, tax_year, comp_ptile, travel_ptile, conferences_ptile, grants_ptile, foreign_ptile
                        )

                    processed_groups += 1
                    pbar.update(1)
                    print(f"Calculated percentiles for {org_type} {tax_year}: {len(org_charities)} charities")

        print(f"Percentile calculation complete: {processed_groups} groups processed")
        return processed_groups

    def _calculate_percentile(self, value: float, sorted_values: List[float]) -> float:
        """Calculate percentile rank for a value in a sorted list"""
        if not sorted_values:
            return 0.0

        # Find position
        for i, v in enumerate(sorted_values):
            if value <= v:
                return (i / len(sorted_values)) * 100.0

        return 100.0  # Value is higher than all others