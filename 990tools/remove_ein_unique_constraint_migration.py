#!/usr/bin/env python3
"""
remove_ein_unique_constraint_migration.py - Database migration to remove UNIQUE constraint on ein column

This script removes the UNIQUE constraint from the ein column in the Charities table.
Since the schema.sql was updated but existing databases need to be migrated, this script
handles the migration for databases that already have the UNIQUE constraint.

Usage:
    python remove_ein_unique_constraint_migration.py [db_path]

Arguments:
    db_path: Path to the SQLite database file (default: final/irs990.db)
"""

import sqlite3
import sys
import os
import logging

# Set up logger
logger = logging.getLogger(__name__)

def migrate_ein_constraint(db_path: str):
    """Remove UNIQUE constraint from ein column in Charities table"""

    if not os.path.exists(db_path):
        logger.error(f"Database file not found: {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Enable foreign keys
        cursor.execute("PRAGMA foreign_keys = ON")

        # Check current table schema
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='Charities'")
        result = cursor.fetchone()

        if not result:
            logger.error("Charities table not found in database")
            return False

        create_sql = result[0]
        logger.info("Current Charities table schema:")
        logger.info(create_sql)

        # Check if ein column has UNIQUE constraint
        # SQLite doesn't have a direct way to check constraints, but we can look at the SQL
        has_unique_constraint = 'ein TEXT NOT NULL UNIQUE' in create_sql or 'UNIQUE(ein)' in create_sql

        if not has_unique_constraint:
            # Check if there's a separate unique index on ein
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='Charities' AND sql LIKE '%UNIQUE%' AND sql LIKE '%ein%'")
            unique_index = cursor.fetchone()

            if unique_index:
                logger.info(f"Found unique index on ein: {unique_index[0]}")
                has_unique_constraint = True
            else:
                logger.info("No UNIQUE constraint found on ein column. No migration needed.")
                return True

        if has_unique_constraint:
            logger.info("Removing UNIQUE constraint from ein column...")

            # Get all column definitions
            cursor.execute("PRAGMA table_info(Charities)")
            columns = cursor.fetchall()

            # Create new table schema without UNIQUE constraint on ein
            new_columns = []
            for col in columns:
                col_name = col[1]
                col_type = col[2]
                col_notnull = "NOT NULL" if col[3] else ""
                col_default = f"DEFAULT {col[4]}" if col[4] else ""
                col_pk = "PRIMARY KEY" if col[5] else ""

                if col_name == 'ein':
                    # Remove UNIQUE from ein column
                    new_col_def = f"{col_name} {col_type} {col_notnull} {col_default} {col_pk}".strip()
                else:
                    new_col_def = f"{col_name} {col_type} {col_notnull} {col_default} {col_pk}".strip()

                new_columns.append(new_col_def)

            new_create_sql = f"CREATE TABLE Charities_new (\n    {',\n    '.join(new_columns)}\n);"

            logger.info("New Charities table schema:")
            logger.info(new_create_sql)

            # Create new table
            cursor.execute(new_create_sql)

            # Copy all data from old table to new table
            column_names = [col[1] for col in columns]
            select_sql = f"SELECT {', '.join(column_names)} FROM Charities"
            insert_sql = f"INSERT INTO Charities_new ({', '.join(column_names)}) {select_sql}"

            cursor.execute(insert_sql)

            # Drop old table
            cursor.execute("DROP TABLE Charities")

            # Rename new table
            cursor.execute("ALTER TABLE Charities_new RENAME TO Charities")

            # Recreate indexes (excluding any unique index on ein)
            logger.info("Recreating indexes...")

            # Drop any existing unique index on ein
            cursor.execute("DROP INDEX IF EXISTS idx_charities_ein")

            # Recreate standard indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_ein ON Charities(ein)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_tax_year ON Charities(tax_year)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_org_type ON Charities(org_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_form_type ON Charities(form_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_denominator ON Charities(denominator)")

            # Recreate triggers
            logger.info("Recreating triggers...")
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_charities_timestamp
                AFTER UPDATE ON Charities
                BEGIN
                    UPDATE Charities SET updated_at = CURRENT_TIMESTAMP WHERE charity_id = NEW.charity_id;
                END
            """)

            conn.commit()
            logger.info("Successfully removed UNIQUE constraint from ein column")
            return True

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        return False
    finally:
        conn.close()

def main():
    # Default database path
    default_db_path = "final/irs990.db"

    # Use command line argument if provided
    db_path = sys.argv[1] if len(sys.argv) > 1 else default_db_path

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    logger.info(f"Migrating database: {db_path}")
    success = migrate_ein_constraint(db_path)

    if success:
        logger.info("Migration completed successfully.")
        sys.exit(0)
    else:
        logger.error("Migration failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()