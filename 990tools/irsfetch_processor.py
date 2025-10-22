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
from pathlib import Path
from typing import Optional, List
import requests
from bs4 import BeautifulSoup, Tag

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# Import logging utilities
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning


class IRSFetchProcessor:
    """Processor for downloading and recompressing IRS ZIP files"""

    def __init__(self, zips_dir: str = "/Volumes/Data/irs_zips", quiet: bool = False):
        self.zips_dir = zips_dir
        self.quiet = quiet

        # Setup logging
        self.logger = get_logger("irsfetch")
        if quiet:
            self.logger.setLevel(30)  # WARNING level
        else:
            self.logger.setLevel(20)  # INFO level

        # Ensure zips directory exists
        if not os.path.exists(self.zips_dir):
            os.makedirs(self.zips_dir)

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, ein, exc_info, *args)

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not self.quiet:
            log_info(self.logger, msg, ein, *args)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not self.quiet:
            log_debug(self.logger, msg, ein, *args)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, ein, *args)

    def fetch_irs_zips(self, start_year: int, end_year: int) -> bool:
        """Download and recompress IRS 990 ZIP files from IRS website"""
        self.log_info(f"Fetching IRS 990 ZIP files from {start_year} to {end_year}")

        # Download ZIP files
        if not self._download_irs_zips(start_year, end_year):
            return False

        # Recompress ZIP files
        if not self._recompress_zips():
            return False

        self.log_info("IRS ZIP file fetch and recompression complete")
        return True

    def _download_irs_zips(self, start_year: int, end_year: int):
        """Download IRS 990 ZIP files from IRS website"""
        def download_file(url, dest_folder):
            filename = url.split('/')[-1]
            dest_path = os.path.join(dest_folder, filename)
            if os.path.exists(dest_path):
                self.log_info(f"Skipping {filename} - already exists")
                return
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            self.log_info(f"Downloaded {filename}")

        def valid_year(year):
            try:
                year = int(year)
                if 2015 <= year <= 2025:
                    return year
                raise argparse.ArgumentTypeError("Year must be between 2015 and 2025.")
            except ValueError:
                raise argparse.ArgumentTypeError("Year must be an integer.")

        base_url = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"
        response = requests.get(base_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        for year in range(start_year, end_year + 1):
            year_str = str(year)
            # Find links containing the year and ending in .zip
            zip_links = []
            for a in soup.find_all('a', href=True):
                if isinstance(a, Tag):
                    href = a.get('href')
                    if href and isinstance(href, str) and year_str in href and href.endswith('.zip') and 'TEOS_XML' in href:
                        zip_links.append(href)

            if not zip_links:
                self.log_warning(f"No ZIP files found for {year}")
                continue

            for link in zip_links:
                full_url = f"https://www.irs.gov{link}" if link.startswith('/') else link
                download_file(full_url, self.zips_dir)

        return True

    def _recompress_zips(self):
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
            self.log_info(f"Recompressing {zip_file}...")
            self.log_debug(f"Current working directory: {os.getcwd()}")

            # Clean temp directory
            temp_dir = os.path.join(self.zips_dir, "temp")
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
                self.log_error(error_msg)
                return False

            # Count extracted files
            extracted_files = len(list(Path(temp_path).rglob("*.xml")))
            self.log_info(f"Extracted {extracted_files} files from {zip_file}.")

            # Recompress with zip in one go
            temp_zip = os.path.join(temp_path, "temp.zip")
            self.log_debug(f"Creating temp ZIP: {temp_zip}")
            os.chdir(temp_path)
            self.log_debug(f"Changed to directory: {os.getcwd()}")
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
                self.log_error(error_msg)
                os.chdir(self.zips_dir)
                return False

            # Verify temp.zip exists
            if not os.path.exists(temp_zip):
                error_msg = f"Error: {temp_zip} was not created for {zip_file}."
                self.log_error(error_msg)
                os.chdir(self.zips_dir)
                return False

            # Move to output directory using absolute path
            output_zip = os.path.join(self.zips_dir, "recompressed", base_name)
            os.makedirs(os.path.dirname(output_zip), exist_ok=True)
            self.log_debug(f"Moving {temp_zip} to {output_zip}")
            try:
                shutil.move(temp_zip, output_zip)
            except (OSError, shutil.Error) as e:
                error_msg = f"Error moving {temp_zip} to {output_zip}: {e}"
                self.log_error(error_msg)
                os.chdir(self.zips_dir)
                return False

            # Return to base directory and clean up
            os.chdir(self.zips_dir)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            self.log_info(f"Successfully recompressed {zip_file} to recompressed/{base_name}")
            return True

        check_tools()

        # Scan ZIPs - only check files that don't have recompressed versions
        zip_files = glob.glob(os.path.join(self.zips_dir, "20*.zip"))
        if not zip_files:
            raise FileNotFoundError("No ZIP files found matching pattern '20*.zip'.")

        # Filter to only new/unprocessed files
        recompressed_dir = os.path.join(self.zips_dir, "recompressed")
        os.makedirs(recompressed_dir, exist_ok=True)

        to_check = []
        for zip_file in zip_files:
            base_name = os.path.basename(zip_file)
            recompressed_path = os.path.join(recompressed_dir, base_name)
            if not os.path.exists(recompressed_path):
                to_check.append(zip_file)

        if not to_check:
            self.log_info("All ZIP files already have recompressed versions. Skipping recompression.")
            return True

        self.log_info(f"Found {len(to_check)} ZIP files to check for recompression.")

        to_recompress = []
        for zip_file in to_check:
            self.log_debug(f"Checking {zip_file}...")
            needs_recompress, reason = check_compression(zip_file)
            if needs_recompress:
                self.log_info(f"  {reason}")
                to_recompress.append(zip_file)
            else:
                self.log_info(f"  {reason}. Skipping.")

        if not to_recompress:
            self.log_info("No ZIP files need recompression.")
            return True

        self.log_info(f"ZIP files to recompress: {len(to_recompress)}")

        # Recompress
        success_count = 0
        for zip_file in to_recompress:
            if recompress_zip(zip_file):
                success_count += 1
            else:
                self.log_error(f"Failed to recompress {zip_file}.")
                return False

        self.log_info(f"Recompression complete. Successfully recompressed {success_count} files.")
        return True


def main():
    """Command-line interface for IRS fetch processor"""
    parser = argparse.ArgumentParser(description="IRS ZIP File Fetch and Recompress Processor")
    parser.add_argument("--start-year", type=int, default=2017, help="Start year for fetching (default: 2017)")
    parser.add_argument("--end-year", type=int, default=2030, help="End year for fetching (default: 2030)")
    parser.add_argument("--zips-dir", default="/Volumes/Data/irs_zips", help="ZIP files directory")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode - minimal logging")

    args = parser.parse_args()

    processor = IRSFetchProcessor(zips_dir=args.zips_dir, quiet=args.quiet)

    try:
        success = processor.fetch_irs_zips(args.start_year, args.end_year)
        if success:
            processor.log_info("IRS fetch processing completed successfully")
            sys.exit(0)
        else:
            processor.log_error("IRS fetch processing failed")
            sys.exit(1)
    except Exception as e:
        processor.log_error(f"IRS fetch processing failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()