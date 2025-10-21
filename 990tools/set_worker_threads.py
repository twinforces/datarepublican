#!/usr/bin/env python3
"""
set_worker_threads.py - Binary search script to find optimal worker thread count

This script performs a binary search to find the optimal number of worker threads
for IRS 990 processing. It tests different worker counts and measures performance
to determine the sweet spot for parallelism.

Usage:
    python set_worker_threads.py [--min-workers MIN] [--max-workers MAX] [--iterations N] [--files COUNT]

Example:
    python set_worker_threads.py --min-workers 2 --max-workers 32 --iterations 3 --files 1000
"""

import subprocess
import time
import argparse
import sys
import os

def run_test(workers, files=1000):
    """Run a processing test with given worker count and return time"""
    cmd = f'python irs990processor.py --quiet --start-step xml --stop-step xml --max-files {files} --workers {workers}'
    print(f'Testing with {workers} workers...')

    start_time = time.time()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=os.path.dirname(__file__))
    end_time = time.time()

    elapsed = end_time - start_time

    if result.returncode == 0:
        print('.2f')
        return elapsed
    else:
        print(f'  Failed: {result.stderr[:200]}...')
        return None

def binary_search_workers(min_workers=2, max_workers=32, iterations=3, files=1000):
    """Perform binary search for optimal worker count"""
    best_workers = None
    best_time = float('inf')

    print('Starting binary search for optimal worker count...')
    print(f'Testing range: {min_workers}-{max_workers} workers, {iterations} iterations, {files} files each')

    for iteration in range(iterations):
        print(f'\nIteration {iteration + 1}:')

        # Test current bounds
        times = {}

        # Test min bound
        time_min = run_test(min_workers, files)
        if time_min is not None:
            times[min_workers] = time_min

        # Test max bound
        time_max = run_test(max_workers, files)
        if time_max is not None:
            times[max_workers] = time_max

        # Test midpoint
        mid_workers = (min_workers + max_workers) // 2
        if mid_workers not in times:
            time_mid = run_test(mid_workers, files)
            if time_mid is not None:
                times[mid_workers] = time_mid

        # Find best in this iteration
        if times:
            iter_best_workers = min(times.keys(), key=lambda w: times[w])
            iter_best_time = times[iter_best_workers]

            print('.2f')

            # Update overall best
            if iter_best_time < best_time:
                best_time = iter_best_time
                best_workers = iter_best_workers

            # Narrow search space around the best
            if iter_best_workers == min_workers:
                max_workers = mid_workers
            elif iter_best_workers == max_workers:
                min_workers = mid_workers
            else:
                # Best is midpoint, search around it
                min_workers = max(min_workers, iter_best_workers - 2)
                max_workers = min(max_workers, iter_best_workers + 2)

    print('.2f')
    return best_workers, best_time

def main():
    parser = argparse.ArgumentParser(description='Find optimal worker thread count for IRS 990 processing')
    parser.add_argument('--min-workers', type=int, default=2, help='Minimum worker count to test')
    parser.add_argument('--max-workers', type=int, default=32, help='Maximum worker count to test')
    parser.add_argument('--iterations', type=int, default=3, help='Number of binary search iterations')
    parser.add_argument('--files', type=int, default=1000, help='Number of files to process per test')

    args = parser.parse_args()

    if args.min_workers < 2:
        print("Error: Minimum workers must be at least 2")
        sys.exit(1)

    optimal_workers, best_time = binary_search_workers(
        args.min_workers, args.max_workers, args.iterations, args.files
    )

    print("\nOptimal configuration:")
    print(f"  Workers: {optimal_workers}")
    print(".2f")
    print(".1f")

if __name__ == '__main__':
    main()