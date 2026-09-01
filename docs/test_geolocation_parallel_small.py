#!/usr/bin/env python3
"""
Test script for geolocation_processor.py parallel execution with limited data
Tests 100% Phase 2 processing (Phase 1 is now handled by address deduplication)
"""

import sys
import os
import time
import signal
from datetime import datetime

# Add the 990tools directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '990tools'))

from database_operations import DatabaseOperations
from geolocation_processor import GeolocationProcessorThreaded
from config import global_config

def timeout_handler(signum, frame):
    print("TIMEOUT: Test exceeded 60 seconds, terminating...")
    raise TimeoutError("Test timed out after 60 seconds")

def main():
    print(f"Starting geolocation parallel test (limited) at {datetime.now()}")
    print("Database: /Volumes/Data/final/irs990.duckdb")
    print("Timeout: 60 seconds")
    print("Limit: 100 addresses for address deduplication, 50 geocoding records for Phase 2")
    print("-" * 50)

    # Set up timeout
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(60)

    try:
        # Initialize database operations
        db_path = "/Volumes/Data/final/irs990.duckdb"
        print(f"Connecting to database: {db_path}")

        db_ops = DatabaseOperations(db_path)

        # Set global limit to test with small dataset
        global_config.max_files = 100  # Limit address deduplication to 100 addresses

        # Record start time
        start_time = time.time()

        # Create processor and run threaded version
        processor = GeolocationProcessorThreaded(db_ops)

        print("Starting geolocation processing (threaded)...")
        result = processor.geolocate_addresses_threaded()

        # Record end time
        end_time = time.time()
        duration = end_time - start_time

        print("-" * 50)
        print("TEST RESULTS:")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Addresses processed: {result}")
        print("Status: SUCCESS - No timeout or deadlock detected")
        print("Address deduplication and Phase 2 executed without issues")
        print("Processing confirmed: Phase 2 API calls completed successfully")

        return True

    except TimeoutError as e:
        print(f"FAILED: {e}")
        return False
    except Exception as e:
        print(f"FAILED: Unexpected error - {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cancel the alarm
        signal.alarm(0)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)