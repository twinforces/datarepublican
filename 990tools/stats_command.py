#!/usr/bin/env python3
"""
stats_command.py - Command-line tool to generate database statistics reports

Usage: python stats_command.py <step_name> [notes]

This command generates a markdown report with row counts for all database tables,
saved as stats_<step_name>.md
"""

import sys
import os
from database_operations import DatabaseOperations

def main():
    if len(sys.argv) < 2:
        print("Usage: python stats_command.py <step_name> [notes]", file=sys.stderr)
        print("Example: python stats_command.py initial_load 'After loading sample XML files'", file=sys.stderr)
        sys.exit(1)

    step_name = sys.argv[1]
    notes = sys.argv[2] if len(sys.argv) > 2 else ""

    # Find database file
    db_path = None
    for candidate in ['irs990.db', 'final/irs990.db', '../final/irs990.db']:
        if os.path.exists(candidate):
            db_path = candidate
            break

    if not db_path:
        print("Error: Could not find database file (irs990.db)", file=sys.stderr)
        sys.exit(1)

    try:
        # Connect to database (read-only mode for stats)
        db_ops = DatabaseOperations(db_path)

        # Generate report
        stats_processor = db_ops.get_stats_processor()
        report_file = stats_processor.generate_stats_report(step_name, notes)

        print(f"Statistics report generated: {report_file}")

        # Also print summary to stdout
        counts = stats_processor.get_table_counts()
        total = sum(counts.values())
        print(f"\nTotal records: {total:,}")
        for table, count in counts.items():
            print(f"{table}: {count:,}")

        db_ops.close()

    except Exception as e:
        print(f"Error generating stats report: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()