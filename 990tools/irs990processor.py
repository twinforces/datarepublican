#!/usr/bin/env python3
"""
990processor.py - Comprehensive IRS 990 data processing module

This module replaces the collection of separate scripts with a unified,
database-driven processing pipeline for IRS Form 990 data.

Key Features:
- Dataclass-based data models for type safety and clarity
- DuckDB database storage with proper relationships
- Geolocation using censusgeocode API
- Comprehensive error handling and logging
- Threaded processing for performance
"""

import os
import sys
import argparse
import time
import zipfile
import threading
import logging
import cProfile
import pstats
import io
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from datetime import datetime
from queue import Queue
from io import BytesIO
from lxml import etree as ET  # type: ignore

try:
    import censusgeocode as cg
except ImportError:
    cg = None

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# Import extracted modules
from database_operations import DatabaseOperations
from processing_strategy import ParallelXMLProcessingStrategy
from geolocation_processor import GeolocationProcessor
from zip_processor import ZipProcessor
from percentile_calculator import PercentileCalculator
from export_processor import TSVExporter
from address_matcher import AddressMatcher
from address_deduplication_processor import AddressDeduplicationProcessor
from irsfetch_processor import IRSFetchProcessor
from address_deduplication_processor import AddressDeduplicationProcessor
from officer_deduplication_processor import OfficerDeduplicationProcessor
from photo_processor import PhotoProcessor
from extract_processor import ExtractProcessor
from logging_utils import get_logger, log_info, log_error, log_debug, log_warning
from config import global_config

# Import parsing functions
from parse_990 import parse_990
from parse_990ez import parse_990ez
from parse_990pf import parse_990pf
from parse_utils import parse_grants

# Import dataclasses
from models import Charity, Officer, Grant, Contractor, PoliticalContribution

# Constants
DEFAULT_DB_PATH = "irs990.duckdb"
DEFAULT_ZIPS_DIR = "/Volumes/Data/irs_zips"
DEFAULT_OUT_DIR = "/Volumes/Data/tsvs"
DEFAULT_ANAL_DIR = "/Volumes/Data/atsvs"
DEFAULT_FINAL_DIR = "/Volumes/Data/final"

# Processing version constants
CURRENT_PROCESSING_VERSION = 2  # Increment when processing logic changes (refactored)

# Threading constants
MAX_WORKERS = 16
QUEUE_SIZE = 1000
BATCH_SIZE = 100

# Import XPath constants from xpaths.py
from xpaths import XPATHS_990, XPATHS_990EZ, XPATHS_990PF


class IRS990Processor:
    """Main processor class for IRS 990 data"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, zips_dir: str = DEFAULT_ZIPS_DIR,
                 out_dir: str = DEFAULT_OUT_DIR, anal_dir: str = DEFAULT_ANAL_DIR,
                 final_dir: str = DEFAULT_FINAL_DIR, verbose: bool = False, quiet: bool = False, max_files: Optional[int] = None, log_sql: bool = False, workers: int = MAX_WORKERS, dbUI: bool = False, profile_seconds: Optional[int] = None, progress: str = "files", extract: Optional[List[str]] = None, extract_dest: Optional[str] = None, nostats: bool = False):
        # Set global config from parameters (for backward compatibility)
        global_config.db_path = db_path if db_path != DEFAULT_DB_PATH else os.path.join(final_dir, "irs990.duckdb")
        global_config.final_dir = final_dir
        global_config.zips_dir = zips_dir
        global_config.out_dir = out_dir
        global_config.anal_dir = anal_dir
        global_config.final_dir = final_dir
        global_config.verbose = verbose
        global_config.quiet = quiet
        global_config.max_files = max_files
        global_config.log_sql = log_sql
        global_config.workers = workers
        global_config.dbUI = dbUI
        global_config.profile_seconds = profile_seconds
        global_config.progress = progress
        global_config.extract = extract
        global_config.extract_dest = extract_dest
        global_config.nostats = nostats

        # Determine database path
        if db_path == DEFAULT_DB_PATH:
            db_path = os.path.join(final_dir, "irs990.duckdb")
        self.db_path = db_path
        self.final_dir = final_dir  # Store final_dir for later use
        self.zips_dir = zips_dir
        self.out_dir = out_dir
        self.anal_dir = anal_dir
        self.final_dir = final_dir
        self.verbose = verbose
        self.quiet = quiet
        self.max_files = max_files
        self.log_sql = log_sql

        # Setup logging
        self.logger = get_logger("irs990")
        if global_config.is_quiet():
            self.logger.setLevel(logging.ERROR)
        elif global_config.is_verbose():
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.WARNING)

        # Initialize components
        self.db_ops = DatabaseOperations(self.db_path, log_sql=global_config.log_sql, dbUI=global_config.dbUI)
        self.zip_processor = ZipProcessor(self.db_ops, global_config.zips_dir)
        self.xml_processing_strategy = ParallelXMLProcessingStrategy(self.db_ops, self.logger, workers=global_config.workers)
        self.geolocation_processor = GeolocationProcessor(self.db_ops)
        self.address_matcher = AddressMatcher(self.db_ops)
        self.percentile_calculator = PercentileCalculator(self.db_ops)
        # Initialize TSV exporter
        self.tsv_exporter = TSVExporter(self.db_ops, global_config.final_dir)
        # Initialize IRS fetch processor
        self.irs_fetch_processor = IRSFetchProcessor(global_config.zips_dir)
        # Initialize photo processor
        self.photo_processor = PhotoProcessor(self.db_ops)
        # Initialize extract processor
        self.extract_processor = ExtractProcessor(self.db_ops, global_config.zips_dir)
        # Initialize address deduplication processor
        self.address_dedup_processor = AddressDeduplicationProcessor(self.db_ops)
        # Initialize officer deduplication processor
        self.officer_dedup_processor = OfficerDeduplicationProcessor(self.db_ops)
        # Initialize bulk operations
        self.bulk_ops = self.db_ops.get_bulk_operations()

        # Initialize stats processor
        self.stats_processor = self.db_ops.get_stats_processor()

        # Initialize database
        self._init_database()

        # Initialize instance variables
        self.zips_dir = zips_dir
        self.out_dir = out_dir
        self.anal_dir = anal_dir
        self.final_dir = final_dir
        self.verbose = verbose
        self.quiet = quiet
        self.max_files = max_files
        self.log_sql = log_sql
        self.dbUI = dbUI
        self.workers = workers
        self.profile_seconds = profile_seconds
        self.progress = progress
        self.extract = extract
        self.extract_dest = extract_dest
        self.nostats = nostats

    def _init_database(self):
        """Initialize DuckDB database with schema"""
        # The database is already initialized by DatabaseOperations
        # Just ensure the path is absolute
        if not os.path.isabs(self.db_path):
            self.db_path = os.path.join(self.final_dir, self.db_path)
        # Use the existing connection from DatabaseOperations
        self.db_conn = self.db_ops.db_conn

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context - always shown even in quiet mode"""
        log_error(self.logger, msg, *args, ein=ein, exc_info=exc_info)

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if not global_config.is_quiet():
            log_info(self.logger, msg, *args, ein=ein)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if not global_config.is_quiet():
            log_debug(self.logger, msg, *args, ein=ein)

    def log_warning(self, msg: str, *args, ein: Optional[str] = None):
        """Log warning with optional EIN context - always shown even in quiet mode"""
        log_warning(self.logger, msg, *args, ein=ein)

        """Recompress ZIP files to standard format using 7z and zip"""
        import glob
        import shutil
        import subprocess

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
            self.log_debug(f"Using temp directory: {temp_path}")

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
            return

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

    # Main processing methods that delegate to modules

    def fetch_irs_zips(self, start_year: int, end_year: int):
        """Fetch IRS 990 ZIP files from IRS website and recompress (step 1)"""
        self.log_info(f"Fetching IRS 990 ZIP files from {start_year} to {end_year}")
        return self.irs_fetch_processor.fetch_irs_zips(start_year, end_year)

    def process_zip_files(self, start_year: int, end_year: int):
        """Process ZIP files and register XML files (step 3)"""
        self.log_info(f"Processing ZIP files from {start_year} to {end_year}")
        return self.zip_processor.process_zip_files(start_year, end_year)

    def process_xml_files(self):
        """Parse XML files and extract data to dataclasses (step 5)"""
        self.log_info("Processing XML files and extracting data")

        # Handle profiling if requested
        if self.profile_seconds:
            return self._profile_xml_processing()

        return self.xml_processing_strategy.execute(self.max_files)

    def _get_xml_files_to_process(self) -> List[Tuple]:
        """Get list of XML files to process from database"""
        query = """
            SELECT xml_id, zip_id, filename, internal_path
            FROM XmlFiles
            WHERE processed = FALSE
            ORDER BY xml_id
        """
        if self.max_files:
            query += f" LIMIT {self.max_files}"
        result = self.db_ops.execute_query(query)
        return result.fetchall()

    def _process_xml_files_parallel(self, xml_files: List[Tuple]):
        """Process XML files using producer-consumer pattern for threading safety"""
        # Use the parallel processing strategy instead of inline implementation
        self.xml_processing_strategy.execute(self.max_files)



    def _process_single_xml(self, xml_id: str, zip_path: str, filename: str, internal_path: str):
        """Process a single XML file"""
        try:
            self.log_debug(f"Processing XML {filename} (ID: {xml_id})")

            # Validate zip_path is actually a file path, not a UUID
            if zip_path and isinstance(zip_path, str) and not ('/' in zip_path or '\\' in zip_path):
                raise ValueError(f"zip_path parameter contains UUID instead of file path: {zip_path}")

            # Extract XML content from ZIP using cached connection
            xml_content = self._extract_xml_from_zip_cached(zip_path, internal_path)

            self.log_debug(f"Extracted XML content for {filename}, size: {len(xml_content)} bytes")

            # Parse XML
            parser = ET.XMLParser(recover=True)
            tree = ET.parse(BytesIO(xml_content), parser)
            root = tree.getroot()

            # Extract basic metadata
            form_type = self._extract_form_type(root)
            tax_year = self._extract_tax_year(root)
            filer_ein = self._extract_filer_ein(root)

            self.log_debug(f"Extracted metadata for {filename}: form_type={form_type}, tax_year={tax_year}, ein={filer_ein}")

            if not filer_ein or filer_ein == "Unknown":
                self.log_error(f"Skipping XML {filename}: invalid EIN {filer_ein}")
                return ('error', xml_id)

            # Extract data based on form type
            if form_type == "990":
                charity, officers, grants, contractors, contributions = self._parse_990_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990EZ":
                charity, officers, grants, contractors, contributions = self._parse_990ez_data(root, filename, filer_ein, tax_year, form_type)
            elif form_type == "990PF":
                charity, officers, grants, contractors, contributions = self._parse_990pf_data(root, filename, filer_ein, tax_year, form_type)
            else:
                self.log_info(f"Unsupported form type {form_type} in {filename}")
                return ('error', xml_id)

            if charity:
                self.log_debug(f"Successfully parsed {filename}: charity={charity.ein}, grants={len(grants)}, officers={len(officers)}")
                self.log_debug(f"Returning tuple: charity={type(charity)}, officers={type(officers)}, grants={type(grants)}, contractors={type(contractors)}, contributions={type(contributions)}")
                return charity, officers, grants, contractors, contributions
            else:
                self.log_error(f"Failed to extract charity data from {filename}")
                return ('error', xml_id)

        except Exception as e:
            self.log_error(f"Failed to process XML {filename}: {e}", exc_info=True)
            return ('error', xml_id)

        return None

    def _extract_form_type(self, root) -> str:
        """Extract form type from XML"""
        for xpath in XPATHS_990["form_type"] + XPATHS_990EZ["form_type"] + XPATHS_990PF["form_type"]:
            try:
                result = xpath(root)
                if result:
                    return result[0].text
            except:
                continue
        return "Unknown"

    def _extract_tax_year(self, root) -> int:
        """Extract tax year from XML"""
        for xpath in XPATHS_990["tax_year"] + XPATHS_990EZ["tax_year"] + XPATHS_990PF["tax_year"]:
            try:
                result = xpath(root)
                if result:
                    year_str = result[0].text
                    if year_str and year_str.isdigit():
                        return int(year_str)
            except:
                continue
        return 0  # Default fallback

    def _extract_filer_ein(self, root) -> str:
        """Extract filer EIN from XML"""
        for xpath in XPATHS_990["filer_ein"] + XPATHS_990EZ["filer_ein"] + XPATHS_990PF["filer_ein"]:
            try:
                result = xpath(root)
                if result:
                    raw_ein = result[0].text.strip()
                    self.log_info(f"TRACE: Found raw EIN: '{raw_ein}' using xpath: {xpath.path}")
                    if raw_ein.isdigit():
                        formatted_ein = f"{int(raw_ein):09d}"
                        log_info(self.logger, f"TRACE: Formatted EIN: '{formatted_ein}' (valid 9-digit)")
                        return formatted_ein
                    else:
                        log_warning(self.logger, f"TRACE: Non-digit EIN found: '{raw_ein}', returning 'Unknown'")
                        return "Unknown"
            except Exception as e:
                self.log_debug(f"XPath {xpath.path} failed: {e}")
                continue
        log_warning(self.logger, "TRACE: No EIN found in XML, returning 'Unknown'")
        return "Unknown"

    def _mark_xml_error(self, xml_id: str, error_msg: str):
        """Mark XML file as having an error"""
        self.db_ops.execute_query("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (CURRENT_PROCESSING_VERSION, error_msg, xml_id))
        self.db_ops.commit()

    def _mark_xml_processed(self, xml_id: str):
        """Mark XML file as processed"""
        self.db_ops.execute_query("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (CURRENT_PROCESSING_VERSION, "success", xml_id))
        self.db_ops.commit()

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990 data"""
        # Use the refactored parse_990 function that returns dataclasses directly
        charity, officers, grants, contractors, contributions, address = parse_990(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        # Extract grants, contractors, and political contributions (override the empty lists from parse_990)
        grants = self._extract_grants_990(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _extract_grants_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990"""
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990EZ"""
        # Similar to 990 but using EZ-specific parsing
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990EZ")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_grants_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Grant]:
        """Extract grants from Form 990PF"""
        # Similar to 990 but using PF-specific parsing
        grants = []
        # Use existing parsing logic from parse_utils
        xml_content = BytesIO(ET.tostring(root))
        grants_data = parse_grants(xml_content, filename, filer_ein, "", tax_year, set(), "990PF")
        for grant_data in grants_data:
            grant = Grant(
                filer_ein=filer_ein,
                filer_name="",  # Will be filled from charity data
                grant_ein=grant_data.get("grant_ein"),
                grant_amt=grant_data.get("grant_amt", 0),
                tax_year=tax_year
            )
            grants.append(grant)
        return grants

    def _extract_contractors_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990"""
        contractors = []
        # TODO: Implement contractor extraction from Schedule L or other sections
        return contractors

    def _extract_contractors_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990EZ"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_contractors_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[Contractor]:
        """Extract contractors from Form 990PF"""
        return self._extract_contractors_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990"""
        contributions = []
        # TODO: Implement political contribution extraction
        return contributions

    def _extract_political_contributions_990ez(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990EZ"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _extract_political_contributions_990pf(self, root, filename: str, filer_ein: str, tax_year: int) -> List[PoliticalContribution]:
        """Extract political contributions from Form 990PF"""
        return self._extract_political_contributions_990(root, filename, filer_ein, tax_year)

    def _parse_990ez_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990EZ data"""
        # Use the refactored parse_990ez function that returns dataclasses directly
        charity, officers, grants, contractors, contributions, address = parse_990ez(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        # Extract grants, contractors, and political contributions (override the empty lists from parse_990ez)
        grants = self._extract_grants_990ez(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990ez(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990ez(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _parse_990pf_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990PF data"""
        # Use the refactored parse_990pf function that returns dataclasses directly
        charity, officers, grants, contractors, contributions, address = parse_990pf(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        # Extract grants, contractors, and political contributions (override the empty lists from parse_990pf)
        grants = self._extract_grants_990pf(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990pf(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990pf(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _extract_xml_from_zip_cached(self, zip_path: str, internal_path: str) -> bytes:
        """Extract XML content from ZIP file using persistent cache"""
        # Use class-level cache to persist across calls
        if not hasattr(IRS990Processor, '_zip_cache'):
            IRS990Processor._zip_cache = {}
            IRS990Processor._zip_cache_lock = threading.Lock()

        zip_key = str(zip_path)

        with IRS990Processor._zip_cache_lock:
            if zip_key not in IRS990Processor._zip_cache:
                # Open ZIP file and cache the connection
                IRS990Processor._zip_cache[zip_key] = zipfile.ZipFile(zip_path, 'r')
                self.log_debug(f"Opened and cached ZIP connection for {zip_path}")

            zip_ref = IRS990Processor._zip_cache[zip_key]

        # Extract XML content from cached connection
        with zip_ref.open(internal_path) as f:
            return f.read()

    def _profile_xml_processing(self) -> int:
        """Profile XML processing for exactly profile_seconds duration"""
        self.log_info(f"Starting {self.profile_seconds}-second profiling benchmark...")

        # Start profiling
        profiler = cProfile.Profile()
        profiler.enable()

        start_time = time.time()
        target_duration = self.profile_seconds

        processed_files = 0
        error_count = 0
        total_files_attempted = 0

        try:
            # Get unprocessed XML files
            xml_files = self._get_xml_files_to_process()
            self.log_info(f"Found {len(xml_files)} unprocessed XML files")

            # Process files for exactly target_duration seconds
            for xml_file_tuple in xml_files:
                xml_id, zip_id, filename, internal_path = xml_file_tuple
                if self.profile_seconds is not None and time.time() - start_time >= self.profile_seconds:
                    self.log_info(f"Reached {target_duration}-second limit after processing {processed_files} files")
                    break

                total_files_attempted += 1

                # Get ZIP file path from cache
                zip_path = self.db_ops.get_zip_file_path(zip_id)
                if not zip_path:
                    error_count += 1
                    continue

                try:
                    # Process single XML file
                    result = self._process_single_xml(xml_id, zip_path, filename, internal_path)
                    if result:
                        processed_files += 1
                        # Mark as processed
                        self.db_conn.execute("""
                            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
                            WHERE xml_id = ?
                        """, (CURRENT_PROCESSING_VERSION, "success", xml_id))
                        self.db_conn.commit()
                    else:
                        error_count += 1

                except Exception as e:
                    error_count += 1
                    if not global_config.is_quiet():
                        self.log_error(f"XML processing failed for {filename}: {e}")

        except Exception as e:
            self.log_info(f"Pipeline execution error: {e}")
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

        # Generate profiling report
        s = io.StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
        ps.print_stats(50)  # Top 50 functions by cumulative time

        profiling_output = s.getvalue()

        # Save profiling data
        profile_txt = f"pipeline_profile_{self.profile_seconds}s_{self.workers}workers.txt"
        profile_stats = f"pipeline_profile_{self.profile_seconds}s_{self.workers}workers.stats"

        with open(profile_txt, "w") as f:
            f.write(f"=== IRS 990 Pipeline {self.profile_seconds}-Second Benchmark Report ===\n")
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
        profiler.dump_stats(profile_stats)

        self.log_info(f"Benchmark complete. Results saved to:")
        self.log_info(f"  - {profile_txt} (human-readable report)")
        self.log_info(f"  - {profile_stats} (binary stats for further analysis)")

        # Print summary to console
        self.log_info("=== Benchmark Summary ===")
        self.log_info(f"Execution time: {execution_time:.2f} seconds")
        self.log_info(f"Files processed: {processed_files}")
        self.log_info(f"Processing rate: {processing_rate:.2f} files/sec")
        self.log_info(f"Error rate: {error_rate:.2f}%")
        self.log_info(f"Throughput: {throughput:.2f} files/min")
        self.log_info("Top 10 most time-consuming functions:")
        lines = profiling_output.split('\n')
        for line in lines[:15]:  # First 15 lines contain the top functions
            if line.strip():
                self.log_info(line)

        return processed_files

    def geolocate_addresses(self):
        """Geolocate addresses using census API (step 7)"""
        return self.geolocation_processor.geolocate_addresses()

    def process_officer_photos(self):
        """Process officer photos using Google Knowledge Graph API (step 8)"""
        self.log_info("Starting officer deduplication")
        dedup_result = self.officer_dedup_processor.deduplicate_officers()
        self.log_info(f"Officer deduplication complete. Processed {dedup_result} duplicates.")

        self.log_info("Processing officer photos using Google Knowledge Graph API")
        return self.photo_processor.process_officer_photos()

    def _geolocate_batch(self, batch: List[Tuple]) -> List[Tuple]:
        """Geolocate a batch of addresses"""
        # This method is now handled by geolocation_processor.py
        return []

    def match_grants_by_address(self):
        """Match grants with unknown EINs by address or colocator (step 9)"""
        self.log_info("Matching grants with unknown EINs by address/colocator")
        return self.address_matcher.match_grants_by_address()

    def _find_charity_by_address(self, name: str, address: str, zip_code: str, po_box: str, tax_year: int) -> Optional[str]:
        """Find charity EIN by address/colocator matching"""
        # This method is now handled by address_matcher.py
        return None

    def _create_stub_charity(self, name: str, address: str, zip_code: str, po_box: str, tax_year: int) -> Optional[str]:
        """Create a stub charity record for unmatched grants"""
        # This method is now handled by address_matcher.py
        return None

    def calculate_percentiles(self):
        """Calculate percentile rankings by org type and tax year (step 10)"""
        self.log_info("Calculating percentile rankings")
        return self.percentile_calculator.calculate_percentiles()

    def _calculate_percentile(self, value: float, sorted_values: List[float]) -> float:
        """Calculate percentile rank for a value in a sorted list"""
        # This method is now handled by percentile_calculator.py
        return 0.0

    def deduplicate_addresses(self):
        """Deduplicate addresses and create master-child relationships (step 4)"""
        self.log_info("Deduplicating addresses and creating master-child relationships")
        return self.address_dedup_processor.deduplicate_addresses()

    def export_final_tsvs(self):
        """Export final TSV files (step 11)"""
        self.log_info("Exporting final TSV files")
        self.tsv_exporter.export_final_tsvs()

    def extract_xml_files(self, eins: List[str], dest_dir: str):
        """Extract XML files for specified EINs (utility function)"""
        self.log_info(f"Extracting XML files for {len(eins)} EINs to {dest_dir}")
        return self.extract_processor.extract_xml_files(eins, dest_dir)


def main():
    """Command-line interface"""
    parser = argparse.ArgumentParser(description="IRS 990 Data Processor")
    parser.add_argument("--start-year", type=int, default=2017, help="Start year for processing (default: 2017)")
    parser.add_argument("--end-year", type=int, default=2030, help="End year for processing (default: 2030)")
    parser.add_argument("--zips-dir", default=DEFAULT_ZIPS_DIR, help="ZIP files directory")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory")
    parser.add_argument("--anal-dir", default=DEFAULT_ANAL_DIR, help="Analysis directory")
    parser.add_argument("--final-dir", default=DEFAULT_FINAL_DIR, help="Final output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode - minimal logging")
    parser.add_argument("--max-files", type=int, default=None, help="Maximum number of XML files to process (default: no limit)")
    parser.add_argument("--log-sql", action="store_true", help="Enable SQL logging")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"Number of worker threads (default: {MAX_WORKERS})")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path (default: irs990.duckdb)")
    parser.add_argument("--dbUI", action="store_true", help="Start database UI alongside processing")
    parser.add_argument("--profile", type=int, help="Profile XML processing for N seconds and exit")
    parser.add_argument("--step", choices=["all", "irsfetch", "zip", "xml", "address", "geolocate",
                                           "match", "percentiles", "export"],
                          default="all", help="Processing step to run (deprecated: use --start-step and --stop-step)")
    parser.add_argument("--start-step", choices=["irsfetch", "zip", "xml", "address", "geolocate",
                                                  "photos", "match", "percentiles", "export"],
                           help="Starting step for processing")
    parser.add_argument("--stop-step", choices=["irsfetch", "zip", "xml", "address", "geolocate",
                                                 "photos", "match", "percentiles", "export"],
                           help="Stopping step for processing")
    parser.add_argument("--progress", choices=["files", "bytes"], default="files",
                           help="Progress tracking type (default: files)")
    parser.add_argument("--extract", nargs='+', help="List of EINs to extract XML files for")
    parser.add_argument("--extract-dest", help="Destination directory for extracted XML files")
    parser.add_argument("--nostats", action="store_true", help="Skip stats report generation after each step")

    args = parser.parse_args()

    # Define processing steps in order
    steps = ["irsfetch", "zip", "xml", "address", "geolocate",
                                            "photos", "match", "percentiles", "export"]

    # Define step actions
    step_actions = {
        "irsfetch": lambda: processor.fetch_irs_zips(args.start_year, args.end_year),
        "zip": lambda: processor.process_zip_files(args.start_year, args.end_year),
        "xml": lambda: processor.process_xml_files(),
        "address": lambda: processor.deduplicate_addresses(),  # Deduplicate addresses and create master-child relationships
        "geolocate": lambda: processor.geolocate_addresses(),
        "photos": lambda: processor.process_officer_photos(),
        "match": lambda: processor.match_grants_by_address(),
        "percentiles": lambda: processor.calculate_percentiles(),
        "export": lambda: processor.export_final_tsvs()
    }

    # Handle backward compatibility with --step
    if args.step != "all":
        args.start_step = args.step
        args.stop_step = args.step

    # Set defaults if not specified
    if not args.start_step:
        args.start_step = "irsfetch"
    if not args.stop_step:
        args.stop_step = "export"

    # Validate start and stop steps
    if steps.index(args.start_step) > steps.index(args.stop_step):
        parser.error("--start-step must come before --stop-step in the processing order")

    # Set global config from parsed args
    global_config.set_from_args(args)

    processor = IRS990Processor(
        db_path=global_config.db_path,
        zips_dir=global_config.zips_dir,
        out_dir=global_config.out_dir,
        anal_dir=global_config.anal_dir,
        final_dir=global_config.final_dir,
        verbose=global_config.verbose,
        quiet=global_config.quiet,
        max_files=global_config.max_files,
        log_sql=global_config.log_sql,
        workers=global_config.workers,
        dbUI=global_config.dbUI,
        profile_seconds=global_config.profile_seconds,
        nostats=global_config.nostats
    )

    # Handle extract mode
    if args.extract and args.extract_dest:
        processor.log_info(f"Extracting XML files for EINs: {args.extract}")
        result = processor.extract_xml_files(args.extract, args.extract_dest)
        processor.log_info(f"Extraction complete. Extracted {result} files.")
        sys.exit(0)

    # Handle profiling mode
    if args.profile:
        processor.log_info(f"Running profiling for {args.profile} seconds...")
        result = processor.process_xml_files()
        processor.log_info(f"Profiling complete. Processed {result} files.")
        sys.exit(0)

    try:
        # Execute steps from start_step to stop_step
        start_idx = steps.index(args.start_step)
        stop_idx = steps.index(args.stop_step)

        for i in range(start_idx, stop_idx + 1):
            step = steps[i]
            processor.log_info(f"Starting step: {step}")
            action = step_actions[step]
            if action:
                action()
            processor.log_info(f"Completed step: {step}")

            # Optimize database after each major processing step
            processor.log_info(f"Optimizing database after {step}")
            processor.db_ops.optimize_database()

            # Generate stats report after each step (unless --nostats is specified)
            if not global_config.nostats:
                try:
                    report_file = processor.stats_processor.generate_stats_report(f"after_{step}", f"Completed step: {step}")
                    processor.log_info(f"Stats report generated: {report_file}")
                except Exception as e:
                    if not processor.quiet:
                        log_warning(processor.logger, f"Failed to generate stats report for step {step}: {e}")

    except Exception as e:
        if not processor.quiet:
            log_error(processor.logger, f"Processing failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()