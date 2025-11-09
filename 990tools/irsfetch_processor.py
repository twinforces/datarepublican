#!/usr/bin/env python3
"""
irsfetch_processor.py - IRS ZIP file download and recompression processor

This module handles downloading IRS 990 ZIP files from the IRS website
and recompressing them to standard format for processing.
"""

import os
import sys
import argparse
import time
import zipfile
import shutil
import subprocess
import glob
import re
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
import requests
from bs4 import BeautifulSoup, Tag
import queue

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# Import logging utilities
from logging_utils import log_info, log_error, log_debug, log_warning
from config import global_config

# Import producer-consumer pattern classes
from base_processor import BaseProcessor, BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from pending_database_context import PendingDatabaseContext
from queue_status_display import QueueStatusDisplay


class IRSFetchProducer(BaseProducer):
    """Producer for IRS ZIP file download operations"""

    def __init__(self, db_ops: DatabaseOperations, zips_dir: str = "/Volumes/Data/irs_zips"):
        super().__init__(db_ops, batch_size=10)  # Small batches for years
        self.zips_dir = zips_dir
        self.min_year = 2017
        self.max_year = 2030

        # Ensure zips directory exists
        if not os.path.exists(self.zips_dir):
            os.makedirs(self.zips_dir)

    def get_progress_scope(self, bytes: bool = False) -> Dict[str, Any]:
        """Estimate total IRS files needing download and return appropriate unit"""
        # For IRS fetch, we estimate based on years being processed
        # Each year typically has multiple ZIP files (around 3-5 per year)
        # This is a rough estimate since exact count requires scraping IRS website

        # Estimate 4 ZIP files per year on average (conservative estimate)
        avg_zips_per_year = 4

        # For now, estimate based on a typical range (2017-2030 = 14 years)
        # In practice, this would be based on actual start/end years from config
        estimated_years = 14
        estimated_files = estimated_years * avg_zips_per_year

        if bytes:
            # Estimate total bytes - average IRS ZIP file is about 50MB
            avg_zip_size_bytes = 50 * 1024 * 1024  # 50MB in bytes
            total = estimated_files * avg_zip_size_bytes
            unit = "bytes"
        else:
            total = estimated_files
            unit = "files"

        return {"total": total, "unit": unit}

    def _get_work_batch(self, last_pk: Optional[int] = None) -> Tuple[List[Tuple[int, str]], Optional[int]]:
        """Get a batch of years to process using key-value paging"""
        start_year = last_pk + 1 if last_pk is not None else self.min_year
        batch = []
        end_year = min(start_year + self.batch_size, self.max_year + 1)
        for year in range(start_year, end_year):
            batch.append((year, "download"))
        if batch:
            max_pk = max(year for year, _ in batch)
        else:
            max_pk = None
        return batch, max_pk

    def _process_work_batch_to_context(self, batch: List[Tuple[int, str]]) -> Optional['PendingDatabaseContext']:
        """Process a batch of years into a single merged PendingDatabaseContext"""
        from pending_database_context import PendingDatabaseContext
 
        contexts = []
 
        for year, operation_type in batch:
            if operation_type == "download":
                # Create context for this year
                context = PendingDatabaseContext()
 
                # IRS fetch downloads and recompresses ZIP files, but doesn't create database objects
                # The zip_processor.py will handle creating ZipFile records when it processes the files
                # So we don't create any objects here - IRS fetch is purely file system operations
                # Just add a progress operation to track the download
                progress_op = DatabaseOperation(
                    operation_type=DatabaseOperationType.PROGRESS_UPDATE,
                    data={"count": 1}  # Count each year processed
                )
                context.addOperationToDatabase(progress_op)
 
                contexts.append(context)
 
        if not contexts:
            return None
        return self.merge_pending_contexts(contexts)


class IRSFetchConsumer(BaseConsumer):
    """Consumer for IRS ZIP file operations"""

    def __init__(self, db_ops: DatabaseOperations, zips_dir: str = "/Volumes/Data/irs_zips"):
        super().__init__(db_ops)
        self.zips_dir = zips_dir
        self.downloaded_zips = 0

    def _process_operations_batch(self, operations_by_type):
        """Process IRS fetch operations using standardized pattern"""
        # IRS fetch doesn't create database objects - it just downloads and recompresses files
        # The zip_processor.py will handle creating ZipFile records when it processes the files
        # This consumer is essentially a no-op for database operations
        pass

    def _execute_zip_operation(self, operation):
        """Execute a ZIP file operation"""
        data = operation.data
        year = data["year"]
        zips_dir = data["zips_dir"]
        operation_type = data["operation"]

        if operation_type == "download_and_recompress":
            # Download and recompress ZIP files for the year
            self._download_and_recompress_year(year, zips_dir)

    def _execute_operations_batch(self, operations):
        """Execute a batch of operations using the consumer pattern"""
        # All operations are now handled by the base class execute_operations_batch method
        # which properly groups and executes operations in dependency order
        self.execute_operations_batch(operations)

    def _download_and_recompress_year(self, year: int, zips_dir: str):
        """Download and recompress ZIP files for a specific year"""
        log_info(f"Processing year {year}")

        # Download ZIP files
        if not self._download_irs_zips(year, year, zips_dir):
            log_error(f"Failed to download ZIP files for {year}")
            return

        # Recompress ZIP files
        if not self._recompress_zips(zips_dir):
            log_error(f"Failed to recompress ZIP files for {year}")
            return

        log_info(f"Successfully processed year {year}")

    def _download_irs_zips(self, start_year: int, end_year: int, zips_dir: str):
        """Download IRS 990 ZIP files from IRS website"""
        downloaded_in_batch = 0
        def download_file(url, dest_folder):
            nonlocal downloaded_in_batch
            if self.exit_processing:
                return
            filename = url.split('/')[-1]
            dest_path = os.path.join(dest_folder, filename)
            if os.path.exists(dest_path):
                log_info(f"Skipping {filename} - already exists")
                return
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self.shutdown_event.is_set():
                        return
                    f.write(chunk)
            log_info(f"Downloaded {filename}")
            downloaded_in_batch += 1

        # Fetch the page once
        base_url = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"
        response = requests.get(base_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Parse all ZIP links and group by year
        zip_links_by_year = {}
        for a in soup.find_all('a', href=True):
            if isinstance(a, Tag):
                href = a.get('href')
                if href and isinstance(href, str) and href.endswith('.zip') and 'TEOS_XML' in href:
                    # Extract year from href using regex (assuming year is 4 digits)
                    year_match = re.search(r'(\d{4})', href)
                    if year_match:
                        year = int(year_match.group(1))
                        if start_year <= year <= end_year:
                            if year not in zip_links_by_year:
                                zip_links_by_year[year] = []
                            zip_links_by_year[year].append(href)

        # Download files for each year
        for year in range(start_year, end_year + 1):
            if year not in zip_links_by_year:
                log_warning(f"No ZIP files found for {year}")
                continue

            for link in zip_links_by_year[year]:
                full_url = f"https://www.irs.gov{link}" if link.startswith('/') else link
                download_file(full_url, zips_dir)

        self.downloaded_zips += downloaded_in_batch
        return True

    def _recompress_zips(self, zips_dir: str):
        """Recompress ZIP files to standard format using 7z and zip"""
        def check_tools():
            """Check if required tools (7z, zip) are available."""
            for tool in ["7z", "zip"]:
                if shutil.which(tool) is None:
                    raise RuntimeError(f"Error: {tool} is not installed. Install with 'brew install {tool}'.")

        def check_compression(zip_file):
            """Check if ZIP file uses unsupported compression methods."""
            try:
                with zipfile.ZipFile(zip_file, "r") as zf:
                    for file_info in zf.infolist():
                        if file_info.compress_type not in (0, 8):  # ZIP_STORED=0, ZIP_DEFLATED=8
                            return True, f"Unsupported compression type {file_info.compress_type} in {file_info.filename}"
                return False, "All files use supported compression (Stored or Deflated)"
            except zipfile.BadZipFile as e:
                return True, f"Malformed ZIP file: {e}"
            except Exception as e:
                return True, f"Error reading ZIP: {e}"

        def recompress_zip(zip_file):
            """Recompress a single ZIP file using Deflate."""
            base_name = os.path.basename(zip_file)
            log_info(f"Recompressing {zip_file}...")
            log_debug(f"Current working directory: {os.getcwd()}")

            # Clean temp directory
            temp_dir = os.path.join(zips_dir, "temp")
            if os.path.exists(temp_dir):
                for item in Path(temp_dir).glob("*"):
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)

            # Create temp directory
            os.makedirs(temp_dir, exist_ok=True)
            temp_path = temp_dir

            # Extract with 7z
            try:
                subprocess.run(
                    ["7z", "x", zip_file, f"-o{temp_path}", "-y"],
                    check=True,
                    capture_output=True,
                    text=True
                )
            except subprocess.CalledProcessError as e:
                error_msg = f"Error extracting {zip_file}: {e.stderr}"
                log_error(error_msg)
                return False

            # Count extracted files
            extracted_files = len(list(Path(temp_path).rglob("*.xml")))
            log_info(f"Extracted {extracted_files} files from {zip_file}.")

            # Recompress with zip in one go
            temp_zip = os.path.join(temp_path, "temp.zip")
            log_debug(f"Creating temp ZIP: {temp_zip}")
            os.chdir(temp_path)
            log_debug(f"Changed to directory: {os.getcwd()}")
            try:
                # Compress all XML files in one zip command
                subprocess.run(
                    ["zip", "-r", "-Z", "deflate", temp_zip, "."],
                    check=True,
                    capture_output=True,
                    text=True
                )
            except subprocess.CalledProcessError as e:
                error_msg = f"Error recompressing {zip_file}: {e.stderr}"
                log_error(error_msg)
                os.chdir(zips_dir)
                return False

            # Verify temp.zip exists
            if not os.path.exists(temp_zip):
                error_msg = f"Error: {temp_zip} was not created for {zip_file}."
                log_error(error_msg)
                os.chdir(zips_dir)
                return False

            # Move to output directory using absolute path
            output_zip = os.path.join(zips_dir, "recompressed", base_name)
            os.makedirs(os.path.dirname(output_zip), exist_ok=True)
            log_debug(f"Moving {temp_zip} to {output_zip}")
            try:
                shutil.move(temp_zip, output_zip)
            except (OSError, shutil.Error) as e:
                error_msg = f"Error moving {temp_zip} to {output_zip}: {e}"
                log_error(error_msg)
                os.chdir(zips_dir)
                return False

            # Return to base directory and clean up
            os.chdir(zips_dir)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            log_info(f"Successfully recompressed {zip_file} to recompressed/{base_name}")
            return True

        check_tools()

        # Scan ZIPs - only check files that don't have recompressed versions
        zip_files = glob.glob(os.path.join(zips_dir, "20*.zip"))
        if not zip_files:
            raise FileNotFoundError("No ZIP files found matching pattern '20*.zip'.")

        # Filter to only new/unprocessed files
        recompressed_dir = os.path.join(zips_dir, "recompressed")
        os.makedirs(recompressed_dir, exist_ok=True)

        to_check = []
        for zip_file in zip_files:
            base_name = os.path.basename(zip_file)
            recompressed_path = os.path.join(recompressed_dir, base_name)
            if not os.path.exists(recompressed_path):
                to_check.append(zip_file)

        if not to_check:
            log_info("All ZIP files already have recompressed versions. Skipping recompression.")
            return True

        log_info(f"Found {len(to_check)} ZIP files to check for recompression.")

        to_recompress = []
        for zip_file in to_check:
            log_debug(f"Checking {zip_file}...")
            needs_recompress, reason = check_compression(zip_file)
            if needs_recompress:
                log_info(f"  {reason}")
                to_recompress.append(zip_file)
            else:
                log_info(f"  {reason}. Skipping.")

        if not to_recompress:
            log_info("No ZIP files need recompression.")
            return True

        log_info(f"ZIP files to recompress: {len(to_recompress)}")
 
        # Recompress
        success_count = 0
        for zip_file in to_recompress:
            if self.exit_processing:
                log_info("Shutdown requested during ZIP recompression")
                return False
            if recompress_zip(zip_file):
                success_count += 1
            else:
                log_error(f"Failed to recompress {zip_file}.")
                return False
 
        log_info(f"Recompression complete. Successfully recompressed {success_count} files.")
        return True


class IRSFetchProcessor(BaseProcessor):
    """Processor for downloading and recompressing IRS ZIP files using producer-consumer pattern"""
  
    def __init__(self, zips_dir: str = "/Volumes/Data/irs_zips", db_path: str = "irs990.db"):
        self.db_ops = DatabaseOperations(db_path=db_path, read_only=False)
        super().__init__(self.db_ops)
        self.zips_dir = zips_dir
        self.downloaded_zips = 0
  
        # Initialize producer and consumer
        self.producer = IRSFetchProducer(self.db_ops, zips_dir)
        self.consumer = IRSFetchConsumer(self.db_ops, zips_dir)
  
        # Initialize thread pool manager
        thread_config = ThreadPoolConfig(
            producer_config=PoolConfig(max_workers=2, queue_size=20),  # Download 2 years at a time
            consumer_config=PoolConfig(max_workers=1, queue_size=10)   # Single consumer for file operations
        )
        self.thread_pool_manager = ThreadPoolManager(thread_config,self)

        # Initialize QueueStatusDisplay for visual monitoring (will be started by fetch_irs_zips)
        self.queue_status_display = None

        # Ensure zips directory exists
        if not os.path.exists(self.zips_dir):
            os.makedirs(self.zips_dir)

    def _get_custom_metrics(self) -> Dict[str, Any]:
        """Custom metrics for IRS fetch processor."""
        return {'downloaded_zips': self.downloaded_zips}

    def fetch_irs_zips(self, start_year: int, end_year: int) -> bool:
        """Download and recompress IRS 990 ZIP files from IRS website"""
        log_info(f"Fetching IRS 990 ZIP files from {start_year} to {end_year}")
        self.setup_status_gauges(interval=10.0)

        # Initialize and start QueueStatusDisplay for visual monitoring
        # Note: IRSFetchProcessor doesn't use a result queue, so we'll create a simple queue for monitoring
        import queue
        monitoring_queue = queue.Queue()
        self.queue_status_display = QueueStatusDisplay(monitoring_queue, update_interval=30.0)
        self.queue_status_display.start()

        try:
            # Process each year sequentially
            for year in range(start_year, end_year + 1):
                if self.exit_processing:
                    log_info("Shutdown requested during IRS ZIP fetch")
                    return False
                success = self._download_and_recompress_year(year, self.zips_dir)
                if not success:
                    log_error(f"Failed to process year {year}")
                    return False
  
            log_info("IRS ZIP file fetch and recompression complete")

            # Stop QueueStatusDisplay
            if self.queue_status_display:
                self.queue_status_display.stop()

            return True

        except Exception as e:
            log_error(f"IRS fetch processing failed: {e}", exc_info=True)
            # Stop QueueStatusDisplay on error
            if self.queue_status_display:
                self.queue_status_display.stop()
            return False

    def _download_and_recompress_year(self, year: int, zips_dir: str) -> bool:
        """Download and recompress ZIP files for a specific year"""
        log_info(f"Processing year {year}")

        # Download ZIP files
        if not self.consumer._download_irs_zips(year, year, zips_dir):
            log_error(f"Failed to download ZIP files for {year}")
            return False

        # Recompress ZIP files
        if not self.consumer._recompress_zips(zips_dir):
            log_error(f"Failed to recompress ZIP files for {year}")
            return False

        log_info(f"Successfully processed year {year}")
        return True

    def _producer_wrapper(self, work_items: List[Tuple[int, str]], work_queue, result_queue, thread_id: int, num_threads: int):
        """Wrapper for producer thread execution"""
        try:
            log_debug(f"Producer thread {thread_id} starting")

            # Distribute work items among threads
            for i in range(thread_id, len(work_items), num_threads):
                year, operation_type = work_items[i]

                # Create operation to download ZIP files for this year
                operation = DatabaseOperation(
                    operation_type=DatabaseOperationType.INSERT_ZIP_FILE,
                    data={
                        "year": year,
                        "zips_dir": self.zips_dir,
                        "operation": "download_and_recompress"
                    }
                )

                # Put operation in result queue for consumer
                result_queue.put(operation)

                log_debug(f"Producer {thread_id}: queued operation for year {year}")

        except Exception as e:
            log_error(f"Producer thread {thread_id} error: {e}", exc_info=True)
        finally:
            # Signal completion
            result_queue.put(None)

    def _consumer_wrapper(self, result_queue, thread_id: int, num_producers: int):
        """Wrapper for consumer thread execution"""
        try:
            log_debug(f"Consumer thread {thread_id} starting")
            sentinels_received = 0

            while True:
                try:
                    operation = result_queue.get(timeout=1.0)
                    if operation is None:  # Sentinel
                        sentinels_received += 1
                        if sentinels_received >= num_producers:
                            break
                        continue

                    # Execute the operation
                    if isinstance(operation, DatabaseOperation):
                        self.consumer._execute_zip_operation(operation)

                    result_queue.task_done()

                except queue.Empty:
                    if self.thread_pool_manager and self.thread_pool_manager.exit_processing:
                        break
                    continue

        except Exception as e:
            log_error(f"Consumer thread {thread_id} error: {e}", exc_info=True)
        finally:
            # Signal completion
            pass

    # Legacy methods moved to consumer - keeping for backward compatibility
    def _download_irs_zips(self, start_year: int, end_year: int):
        """Legacy method - now handled by consumer"""
        return self.consumer._download_irs_zips(start_year, end_year, self.zips_dir)

    def _recompress_zips(self):
        """Legacy method - now handled by consumer"""
        return self.consumer._recompress_zips(self.zips_dir)


def main():
    """Command-line interface for IRS fetch processor"""
    parser = argparse.ArgumentParser(description="IRS ZIP File Fetch and Recompress Processor")
    parser.add_argument("--start-year", type=int, default=2017, help="Start year for fetching (default: 2017)")
    parser.add_argument("--end-year", type=int, default=2030, help="End year for fetching (default: 2030)")
    parser.add_argument("--zips-dir", default="/Volumes/Data/irs_zips", help="ZIP files directory")
    parser.add_argument("--db-path", default="irs990.db", help="Database path")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode - minimal logging")

    args = parser.parse_args()

    # Set quiet mode before initializing
    if args.quiet:
        global_config.quiet = True

    processor = IRSFetchProcessor(zips_dir=args.zips_dir, db_path=args.db_path)

    try:
        success = processor.fetch_irs_zips(args.start_year, args.end_year)
        if success:
            log_info("IRS fetch processing completed successfully")
            sys.exit(0)
        else:
            log_error("IRS fetch processing failed")
            sys.exit(1)
    except Exception as e:
        log_error(f"IRS fetch processing failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup database connection
        if hasattr(processor, 'db_ops'):
            processor.db_ops.close()


if __name__ == "__main__":
    main()