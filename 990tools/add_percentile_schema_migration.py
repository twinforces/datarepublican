#!/usr/bin/env python3
"""
add_percentile_schema_migration.py - Database migration to fix percentile column types

This script alters percentile columns from TEXT to REAL type with NULL support
for invalid values, and adds logic to handle duplicate EINs by selecting latest XML.
"""

import sqlite3
import logging
from pathlib import Path

# Set up logger
logger = logging.getLogger(__name__)

def migrate_percentile_columns(db_path: str):
    """Migrate percentile columns to REAL type with NULL support"""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Enable foreign keys
        cursor.execute("PRAGMA foreign_keys = ON")

        # Check current column types
        cursor.execute("PRAGMA table_info(Charities)")
        columns = cursor.fetchall()
        column_types = {col[1]: col[2] for col in columns}

        percentile_columns = [
            'comp_ptile', 'comp_ptile_value',
            'travel_ptile', 'travel_ptile_value',
            'conferences_ptile', 'conferences_ptile_value',
            'grants_ptile', 'grants_ptile_value',
            'foreign_expenses_ptile', 'foreign_expenses_ptile_value'
        ]

        # Alter columns that are TEXT to REAL
        for col in percentile_columns:
            if col in column_types and 'TEXT' in column_types[col].upper():
                logger.info(f"Converting {col} from {column_types[col]} to REAL")

                # SQLite doesn't support ALTER COLUMN directly for type changes
                # We need to recreate the table with new schema
                # First, get the current schema
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='Charities'")
                create_sql = cursor.fetchone()[0]

                # Create new table with corrected column types
                new_create_sql = create_sql
                for pcol in percentile_columns:
                    # Replace TEXT with REAL for percentile columns
                    import re
                    new_create_sql = re.sub(
                        rf'(\b{pcol}\b\s+)TEXT(\s+|$)',
                        rf'\1REAL\2',
                        new_create_sql,
                        flags=re.IGNORECASE
                    )

                # Rename old table
                cursor.execute("ALTER TABLE Charities RENAME TO Charities_old")

                # Create new table
                cursor.execute(new_create_sql)

                # Copy data, converting invalid text values to NULL
                select_cols = []
                for col_info in columns:
                    col_name = col_info[1]
                    if col_name in percentile_columns and 'TEXT' in column_types[col_name].upper():
                        # Convert invalid text to NULL
                        select_cols.append(f"CASE WHEN {col_name} GLOB '*[0-9]*' THEN CAST({col_name} AS REAL) ELSE NULL END AS {col_name}")
                    else:
                        select_cols.append(col_name)

                select_sql = f"SELECT {', '.join(select_cols)} FROM Charities_old"
                cursor.execute(f"INSERT INTO Charities {select_sql}")

                # Drop old table
                cursor.execute("DROP TABLE Charities_old")

                logger.info(f"Successfully converted {col} to REAL type")
                break  # Only need to do this once for the whole table

        # Recreate indexes (only create indexes for columns that exist)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_ein ON Charities(ein)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_tax_year ON Charities(tax_year)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_org_type ON Charities(org_type)")
        if 'form_type' in column_types:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_form_type ON Charities(form_type)")
        if 'denominator' in column_types:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_denominator ON Charities(denominator)")

        # Recreate triggers
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS update_charities_timestamp
            AFTER UPDATE ON Charities
            BEGIN
                UPDATE Charities SET updated_at = CURRENT_TIMESTAMP WHERE charity_id = NEW.charity_id;
            END
        """)

        conn.commit()
        logger.info("Percentile column migration completed successfully")

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        conn.close()

def handle_duplicate_eins(db_path: str):
    """Handle duplicate EINs by keeping only the latest XML file entry"""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if xml_name column exists
        cursor.execute("PRAGMA table_info(Charities)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]

        if 'xml_name' not in column_names:
            logger.info("xml_name column not found in Charities table, skipping duplicate cleanup")
            return

        # Find duplicate EINs (same EIN, different tax_year or same year with different xml_name)
        cursor.execute("""
            SELECT ein, tax_year, COUNT(*) as count, GROUP_CONCAT(xml_name) as xml_names
            FROM Charities
            GROUP BY ein, tax_year
            HAVING count > 1
        """)

        duplicates = cursor.fetchall()

        if duplicates:
            logger.warning(f"Found {len(duplicates)} duplicate EIN entries to clean up")

            for ein, tax_year, count, xml_names in duplicates:
                # Keep the entry with the latest xml_name (assuming newer files have higher numbers)
                xml_list = xml_names.split(',')
                # Sort by filename to get the "latest" (simple heuristic)
                xml_list.sort(reverse=True)
                keep_xml = xml_list[0]

                # Delete other entries
                cursor.execute("""
                    DELETE FROM Charities
                    WHERE ein = ? AND tax_year = ? AND xml_name != ?
                """, (ein, tax_year, keep_xml))

                logger.info(f"Kept latest XML {keep_xml} for EIN {ein}, tax_year {tax_year}, removed {count-1} duplicates")

        conn.commit()
        logger.info("Duplicate EIN cleanup completed")

    except Exception as e:
        conn.rollback()
        logger.error(f"Duplicate cleanup failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate percentile columns and handle duplicate EINs")
    parser.add_argument("db_path", help="Path to SQLite database file")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    logger.info("Starting database migration...")

    # Migrate percentile columns
    migrate_percentile_columns(str(db_path))

    # Handle duplicate EINs
    handle_duplicate_eins(str(db_path))

    logger.info("Migration completed successfully")