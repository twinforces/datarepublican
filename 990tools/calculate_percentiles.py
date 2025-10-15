#!/usr/bin/env python3
"""
Calculate percentiles for charity financial metrics by org_type and tax_year.
Updates the Charities table with percentile values after all data is loaded.
"""

import sqlite3
import logging
import numpy as np
from typing import Dict, List, Tuple
from collections import defaultdict

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('calculate_percentiles.log')
    ]
)
logger = logging.getLogger(__name__)

# Percentile columns mapping
PERCENTILE_COLS = {
    'comp_pct': 'comp_ptile',
    'travel_pct': 'travel_ptile',
    'conferences_pct': 'conferences_ptile',
    'grants_pct': 'grants_ptile',
    'foreign_expenses_pct': 'foreign_expenses_ptile'
}

def get_db_connection(db_path: str = "/Volumes/Data/final/irs990.db"):
    """Get database connection."""
    return sqlite3.connect(db_path)

def get_groups_to_process(conn: sqlite3.Connection) -> List[Tuple[str, int]]:
    """Get all unique (org_type, tax_year) combinations that need percentile calculation."""
    cursor = conn.execute("""
        SELECT DISTINCT org_type, tax_year
        FROM Charities
        WHERE comp_ptile IS NULL OR travel_ptile IS NULL OR conferences_ptile IS NULL
            OR grants_ptile IS NULL OR foreign_expenses_ptile IS NULL
        ORDER BY org_type, tax_year
    """)
    return cursor.fetchall()

def get_group_data(conn: sqlite3.Connection, org_type: str, tax_year: int) -> Dict[str, List[float]]:
    """Get all percentage data for a specific org_type and tax_year group."""
    cursor = conn.execute("""
        SELECT comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct
        FROM Charities
        WHERE org_type = ? AND tax_year = ?
    """, (org_type, tax_year))

    data = defaultdict(list)
    for row in cursor.fetchall():
        comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct = row

        # Parse float values, handle n/a and nulls
        for col_name, value in [
            ('comp_pct', comp_pct),
            ('travel_pct', travel_pct),
            ('conferences_pct', conferences_pct),
            ('grants_pct', grants_pct),
            ('foreign_expenses_pct', foreign_expenses_pct)
        ]:
            if value is not None and value != 'n/a' and value != '':
                try:
                    val = float(value)
                    if not np.isnan(val):
                        data[col_name].append(val)
                except (ValueError, TypeError):
                    continue

    return dict(data)

def calculate_percentiles(values: List[float]) -> Dict[float, float]:
    """Calculate percentiles for a list of values."""
    if len(values) < 2:
        # For groups with 1 or 0 values, assign percentile 50.0 or handle appropriately
        percentile = 50.0 if len(values) == 1 else np.nan
        return {val: percentile for val in values}

    # Sort values
    sorted_values = np.array(sorted(values))

    # Calculate percentile ranks
    percentiles = {}
    for val in values:
        if val in sorted_values:
            # Find the rank (0-based index in sorted array)
            rank = np.searchsorted(sorted_values, val, sorter=np.argsort(sorted_values))
            # Convert to percentile (0-100)
            percentile = (rank / (len(sorted_values) - 1)) * 100 if len(sorted_values) > 1 else 50.0
            percentiles[val] = min(percentile, 100.0)  # Cap at 100

    return percentiles

def update_charity_percentiles(conn: sqlite3.Connection, charity_id: int,
                              percentile_updates: Dict[str, float]):
    """Update percentile values for a specific charity."""
    update_cols = []
    update_values = []

    for pct_col, ptile_col in PERCENTILE_COLS.items():
        if ptile_col in percentile_updates:
            update_cols.append(f"{ptile_col} = ?")
            update_values.append(percentile_updates[ptile_col])

    if update_cols:
        query = f"""
            UPDATE Charities
            SET {', '.join(update_cols)}
            WHERE charity_id = ?
        """
        update_values.append(charity_id)
        conn.execute(query, update_values)

def process_group(conn: sqlite3.Connection, org_type: str, tax_year: int):
    """Process percentile calculation for a single org_type/tax_year group."""
    logger.info(f"Processing percentiles for {org_type} {tax_year}")

    # Get all data for this group
    group_data = get_group_data(conn, org_type, tax_year)

    if not group_data:
        logger.warning(f"No valid data found for {org_type} {tax_year}")
        return

    # Calculate percentiles for each metric
    metric_percentiles = {}
    for metric, values in group_data.items():
        if values:
            percentiles = calculate_percentiles(values)
            metric_percentiles[metric] = percentiles
            logger.debug(f"Calculated {len(percentiles)} percentiles for {metric}")
        else:
            logger.warning(f"No valid values for {metric} in {org_type} {tax_year}")

    # Get all charities in this group and update their percentiles
    cursor = conn.execute("""
        SELECT charity_id, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct
        FROM Charities
        WHERE org_type = ? AND tax_year = ?
    """, (org_type, tax_year))

    updates_count = 0
    for row in cursor.fetchall():
        charity_id, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct = row

        # Prepare percentile updates
        percentile_updates = {}

        for col_name, value in [
            ('comp_pct', comp_pct),
            ('travel_pct', travel_pct),
            ('conferences_pct', conferences_pct),
            ('grants_pct', grants_pct),
            ('foreign_expenses_pct', foreign_expenses_pct)
        ]:
            if value is not None and value != 'n/a' and value != '':
                try:
                    val = float(value)
                    if not np.isnan(val) and col_name in metric_percentiles:
                        percentile = metric_percentiles[col_name].get(val)
                        if percentile is not None and not np.isnan(percentile):
                            ptile_col = PERCENTILE_COLS[col_name]
                            percentile_updates[ptile_col] = round(percentile, 2)
                except (ValueError, TypeError):
                    continue

        # Update the charity record
        if percentile_updates:
            update_charity_percentiles(conn, charity_id, percentile_updates)
            updates_count += 1

    conn.commit()
    logger.info(f"Updated {updates_count} charities for {org_type} {tax_year}")

def main(db_path: str = "/Volumes/Data/final/irs990.db"):
    """Main function to calculate percentiles for all groups."""
    logger.info("Starting percentile calculation process")

    conn = get_db_connection(db_path)

    try:
        # Get all groups that need processing
        groups = get_groups_to_process(conn)
        logger.info(f"Found {len(groups)} groups to process")

        # Process each group
        for org_type, tax_year in groups:
            try:
                process_group(conn, org_type, tax_year)
            except Exception as e:
                logger.error(f"Error processing group {org_type} {tax_year}: {e}")
                continue

        logger.info("Percentile calculation completed successfully")

    except Exception as e:
        logger.error(f"Error in percentile calculation: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Calculate percentiles for charity financial metrics")
    parser.add_argument('--db-path', default="/Volumes/Data/final/irs990.db",
                       help='Path to the SQLite database file')

    args = parser.parse_args()
    main(db_path=args.db_path)