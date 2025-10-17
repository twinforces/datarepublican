#!/usr/bin/env python3
"""
Database migration script to add processing_version column to XmlFiles table.

This script checks if the processing_version column exists in the XmlFiles table
and adds it if it doesn't. This allows the pipeline to run on existing databases
without requiring a full rebuild.

Usage:
    python add_processing_version_migration.py [db_path]

Arguments:
    db_path: Path to the SQLite database file (default: final/irs990.db)
"""

import sqlite3
import sys
import os

def migrate_database(db_path):
    """Add processing_version column to XmlFiles table if it doesn't exist."""
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Enable foreign keys
        cursor.execute("PRAGMA foreign_keys = ON")

        # Check if processing_version column exists
        cursor.execute("PRAGMA table_info(XmlFiles)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'processing_version' not in columns:
            print("Adding processing_version column to XmlFiles table...")
            cursor.execute("""
                ALTER TABLE XmlFiles
                ADD COLUMN processing_version INTEGER DEFAULT 0
            """)
            print("Column added successfully.")
        else:
            print("processing_version column already exists. No migration needed.")

        conn.commit()
        conn.close()
        return True

    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

def main():
    # Default database path
    default_db_path = "/Volumes/Data/final/irs990.db"

    # Use command line argument if provided
    db_path = sys.argv[1] if len(sys.argv) > 1 else default_db_path

    print(f"Migrating database: {db_path}")
    success = migrate_database(db_path)

    if success:
        print("Migration completed successfully.")
        sys.exit(0)
    else:
        print("Migration failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()