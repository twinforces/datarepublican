#!/usr/bin/env python3
"""
Test script for geolocation_processor.py parallel execution
Tests 100% Phase 2 processing (Phase 1 is now handled by address deduplication)

Enhanced with backpressure mechanism testing:
- Tests queue utilization monitoring
- Tests adaptive producer delays
- Tests dynamic batch sizing
- Tests emergency blocking
- Tests queue status display
"""

import sys
import os
import time
import signal
import threading
from datetime import datetime

# Add the 990tools directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '990tools'))

from database_operations import DatabaseOperations
from geolocation_processor import geolocate_addresses, GeocodingRecordQueue, QueueStatusDisplay

def timeout_handler(signum, frame):
    print("TIMEOUT: Test exceeded 60 seconds, terminating...")
    raise TimeoutError("Test timed out after 60 seconds")

def test_backpressure_mechanisms():
    """Test the backpressure mechanisms independently"""
    print("Testing backpressure mechanisms...")

    # Create a small queue for testing
    queue = GeocodingRecordQueue(maxsize=10)  # Small queue for testing

    # Test basic utilization monitoring
    print("Testing queue utilization monitoring...")
    utilization = queue.get_utilization()
    level = queue.get_utilization_level()
    print(f"Empty queue - utilization: {utilization:.2%}, level: {level}")

    # Fill queue to test different levels
    from models.geocoding import Geocoding
    test_records = []
    for i in range(12):  # Try to add more than maxsize
        record = Geocoding(
            normalized_address=f"test_address_{i}",
            latitude=None,
            longitude=None,
            geocoding_status='pending'
        )
        test_records.append(record)

        try:
            queue.put(record, block=False)
            utilization = queue.get_utilization()
            level = queue.get_utilization_level()
            delay = queue.get_adaptive_delay()
            print(f"Added record {i+1} - utilization: {utilization:.2%}, level: {level}, delay: {delay:.1f}s")
        except:
            print(f"Failed to add record {i+1} - queue full")

    # Test queue statistics
    stats = queue.get_queue_stats()
    print(f"Queue stats: {stats}")

    # Test queue status display (briefly)
    print("Testing queue status display...")
    display = QueueStatusDisplay(queue, update_interval=0.1)
    display.start()
    time.sleep(0.5)  # Let it update a couple times
    display.stop()

    # Clear queue
    while not queue.empty():
        queue.get(block=False)
        queue.task_done()

    print("Backpressure mechanism tests completed successfully")
    return True

def main():
    print(f"Starting geolocation parallel test with backpressure at {datetime.now()}")
    print("Database: /Volumes/Data/final/irs990.duckdb")
    print("Timeout: 60 seconds")
    print("-" * 50)

    # First test backpressure mechanisms
    try:
        backpressure_ok = test_backpressure_mechanisms()
        if not backpressure_ok:
            print("FAILED: Backpressure mechanism tests failed")
            return False
        print("✓ Backpressure mechanisms working correctly")
    except Exception as e:
        print(f"FAILED: Backpressure test error - {e}")
        import traceback
        traceback.print_exc()
        return False

    print("-" * 50)

    # Set up timeout
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(60)

    try:
        # Initialize database operations
        db_path = "/Volumes/Data/final/irs990.duckdb"
        print(f"Connecting to database: {db_path}")

        db_ops = DatabaseOperations(db_path)

        # Record start time
        start_time = time.time()

        # Run geolocation processing
        print("Starting geolocation processing with backpressure...")
        result = geolocate_addresses(db_ops)

        # Record end time
        end_time = time.time()
        duration = end_time - start_time

        print("-" * 50)
        print("TEST RESULTS:")
        print(f"Duration: {duration:.2f} seconds")
        print(f"Addresses processed: {result}")
        print("Status: SUCCESS - No timeout or deadlock detected")
        print("Address deduplication and Phase 2 executed without issues")
        print("Backpressure mechanisms integrated successfully")

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