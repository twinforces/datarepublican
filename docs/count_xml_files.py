#!/usr/bin/env python3
"""
count_xml_files.py - Query database to count XmlFile records
"""

import sys
import os

# Add current directory to path for imports
sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '990tools'))

from database_operations import DatabaseOperations
from config import global_config

def main():
    # Use default database path from config
    db_path = global_config.db_path

    print(f"Connecting to database: {db_path}")

    # Initialize database operations (read-only mode for safety)
    db_ops = DatabaseOperations(db_path=db_path)

    try:
        # Execute COUNT query on XmlFiles table
        result = db_ops.execute_query("SELECT COUNT(*) FROM XmlFiles")
        count = result.fetchone()[0]

        print(f"Actual count of XmlFile records in database: {count}")

        # Check if it matches expected total
        expected_total = 2874835
        if count == expected_total:
            print(f"✓ SUCCESS: Count matches expected total of {expected_total:,}")
            return 0
        else:
            print(f"✗ FAILURE: Count {count:,} does not match expected total of {expected_total:,}")
            print(f"Difference: {count - expected_total:,}")
            return 1

    except Exception as e:
        print(f"Error querying database: {e}")
        return 1

    finally:
        if hasattr(db_ops, 'close'):
            db_ops.close()

if __name__ == "__main__":
    sys.exit(main())