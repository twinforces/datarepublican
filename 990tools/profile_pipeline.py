#!/usr/bin/env python3
"""
profile_pipeline.py - Profile the IRS 990 processing pipeline

This script runs the 990processor pipeline with cProfile to collect
performance metrics and identify bottlenecks.
"""

import cProfile
import pstats
import io
import time
import sys
import os
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import importlib.util
spec = importlib.util.spec_from_file_location("nine_nine_zero_processor", "990processor.py")
processor_module = importlib.util.module_from_spec(spec)
sys.modules["nine_nine_zero_processor"] = processor_module
spec.loader.exec_module(processor_module)
IRS990Processor = processor_module.IRS990Processor

def profile_pipeline():
    """Profile the pipeline execution for exactly 120 seconds"""

    # Use the existing database and directories
    processor = IRS990Processor(
        db_path="/Volumes/Data/final/irs990.db",
        zips_dir="/Volumes/Data/irs_zips",
        out_dir="/Volumes/Data/tsvs",
        anal_dir="/Volumes/Data/atsvs",
        final_dir="/Volumes/Data/final",
        verbose=True
    )

    print("Starting 120-second profiling benchmark...")

    # Start profiling
    profiler = cProfile.Profile()
    profiler.enable()

    start_time = time.time()
    target_duration = 120.0  # 120 seconds

    processed_files = 0
    error_count = 0
    total_files_attempted = 0

    try:
        # Get unprocessed XML files
        processor.db_cursor.execute("""
            SELECT xml_id, zip_id, filename, internal_path
            FROM XmlFiles
            WHERE processed = FALSE OR processing_version < 1
            ORDER BY zip_id, filename
        """)

        xml_files = processor.db_cursor.fetchall()
        print(f"Found {len(xml_files)} unprocessed XML files")

        # Process files for exactly 120 seconds
        for xml_id, zip_id, filename, internal_path in xml_files:
            if time.time() - start_time >= target_duration:
                print(f"Reached 120-second limit after processing {processed_files} files")
                break

            total_files_attempted += 1

            # Get ZIP file path
            processor.db_cursor.execute("SELECT file_path FROM ZipFiles WHERE zip_id = ?", (zip_id,))
            zip_path_result = processor.db_cursor.fetchone()
            if not zip_path_result:
                error_count += 1
                continue

            zip_path = zip_path_result[0]

            try:
                # Process single XML file
                result = processor._process_single_xml(xml_id, zip_path, filename, internal_path)
                if result:
                    processed_files += 1
                    # Mark as processed
                    processor.db_cursor.execute("""
                        UPDATE XmlFiles SET processed = TRUE, processing_version = 1
                        WHERE xml_id = ?
                    """, (xml_id,))
                    processor.db_conn.commit()
                else:
                    error_count += 1

            except Exception as e:
                error_count += 1
                processor.log_error(f"XML processing failed for {filename}: {e}")

    except Exception as e:
        print(f"Pipeline execution error: {e}")
        import traceback
        traceback.print_exc()

    end_time = time.time()
    execution_time = end_time - start_time

    # Stop profiling
    profiler.disable()

    # Calculate metrics
    processing_rate = processed_files / execution_time if execution_time > 0 else 0
    error_rate = (error_count / total_files_attempted * 100) if total_files_attempted > 0 else 0
    throughput = processed_files / execution_time * 60  # files per minute

    print(".2f")
    print(f"Files processed: {processed_files}")
    print(f"Files attempted: {total_files_attempted}")
    print(f"Errors: {error_count}")
    print(".2f")
    print(".2f")
    print(".2f")

    # Generate profiling report
    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(50)  # Top 50 functions by cumulative time

    profiling_output = s.getvalue()

    # Save profiling data
    with open("pipeline_profile_120s.txt", "w") as f:
        f.write("=== IRS 990 Pipeline 120-Second Benchmark Report ===\n")
        f.write(f"Execution Time: {execution_time:.2f} seconds\n")
        f.write(f"Files Processed: {processed_files}\n")
        f.write(f"Files Attempted: {total_files_attempted}\n")
        f.write(f"Errors: {error_count}\n")
        f.write(f"Processing Rate: {processing_rate:.2f} files/second\n")
        f.write(f"Error Rate: {error_rate:.2f}%\n")
        f.write(f"Throughput: {throughput:.2f} files/minute\n\n")
        f.write("=== Top 50 Functions by Cumulative Time ===\n")
        f.write(profiling_output)

    # Also save stats file for further analysis
    profiler.dump_stats("pipeline_profile_120s.stats")

    print("Benchmark complete. Results saved to:")
    print("  - pipeline_profile_120s.txt (human-readable report)")
    print("  - pipeline_profile_120s.stats (binary stats for further analysis)")

    # Print summary to console
    print("\n=== Benchmark Summary ===")
    print(".2f")
    print(f"Files processed: {processed_files}")
    print(f"Processing rate: {processing_rate:.2f} files/sec")
    print(f"Error rate: {error_rate:.2f}%")
    print(f"Throughput: {throughput:.2f} files/min")
    print("\nTop 10 most time-consuming functions:")
    lines = profiling_output.split('\n')
    for line in lines[:15]:  # First 15 lines contain the top functions
        if line.strip():
            print(line)

if __name__ == "__main__":
    profile_pipeline()