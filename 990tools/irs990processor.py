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
from processing_strategy import (
    ParallelXMLProcessingStrategy,
    GeocodingBatchStrategy,
    AddressMatchingStrategy,
    StubCharityCreationStrategy
)
from zip_processor import ZipProcessor
from percentile_calculator import PercentileCalculator
from export_processor import TSVExporter
from logging_utils import IRS990Logger

# Import parsing functions
from parse_990 import parse_990
from parse_990ez import parse_990ez
from parse_990pf import parse_990pf
from parse_utils import parse_grants

# Import dataclasses
from irs990processorDC import Charity, Officer, Grant, Contractor, PoliticalContribution

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
                 final_dir: str = DEFAULT_FINAL_DIR, verbose: bool = False, quiet: bool = False, max_files: Optional[int] = None, log_sql: bool = False, workers: int = MAX_WORKERS):
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
        self.logger = IRS990Logger.setup_logger(
            name="irs990",
            level=logging.ERROR if quiet else (logging.DEBUG if verbose else logging.WARNING)
        )

        # Initialize components
        self.db_ops = DatabaseOperations(self.db_path, log_sql=log_sql)
        self.zip_processor = ZipProcessor(self.db_ops, zips_dir)
        self.xml_processing_strategy = ParallelXMLProcessingStrategy(self.db_ops, self.logger, log_sql=log_sql)
        self.geocoding_strategy = GeocodingBatchStrategy(self.db_ops, self.logger, log_sql=log_sql)
        self.address_matching_strategy = AddressMatchingStrategy(self.db_ops, self.logger, log_sql=log_sql)
        self.stub_charity_strategy = StubCharityCreationStrategy(self.db_ops, self.logger, log_sql=log_sql)
        self.percentile_calculator = PercentileCalculator(self.db_ops)
        # Initialize TSV exporter
        self.tsv_exporter = self.db_ops.get_export_operations(final_dir)
        # Initialize bulk operations
        self.bulk_ops = self.db_ops.get_bulk_operations()

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
        self.workers = workers

    def _init_database(self):
        """Initialize DuckDB database with schema"""
        # The database is already initialized by DatabaseOperations
        # Just ensure the path is absolute
        if not os.path.isabs(self.db_path):
            self.db_path = os.path.join(self.final_dir, self.db_path)
        # Use the existing connection from DatabaseOperations
        self.db_conn = self.db_ops.db_conn

    def log_error(self, msg: str, *args, ein: Optional[str] = None, exc_info: bool = False):
        """Log error with optional EIN context"""
        if ein:
            self.logger.error(f"EIN {ein}: {msg}", *args, exc_info=exc_info)
        else:
            self.logger.error(msg, *args, exc_info=exc_info)

    def log_info(self, msg: str, *args, ein: Optional[str] = None):
        """Log info with optional EIN context"""
        if ein:
            self.logger.info(f"EIN {ein}: {msg}", *args)
        else:
            self.logger.info(msg, *args)

    def log_debug(self, msg: str, *args, ein: Optional[str] = None):
        """Log debug with optional EIN context"""
        if ein:
            self.logger.debug(f"EIN {ein}: {msg}", *args)
        else:
            self.logger.debug(msg, *args)

    # Main processing methods that delegate to modules

    def process_zip_files(self, start_year: int, end_year: int):
        """Process ZIP files and register XML files (steps 2-4)"""
        self.log_info(f"Processing ZIP files from {start_year} to {end_year}")
        return self.zip_processor.process_zip_files(start_year, end_year)

    def process_xml_files(self):
        """Parse XML files and extract data to dataclasses (step 5)"""
        self.log_info("Processing XML files and extracting data")
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



    def _process_single_xml(self, xml_id: int, zip_path: str, filename: str, internal_path: str):
        """Process a single XML file"""
        try:
            self.log_debug(f"Processing XML {filename} (ID: {xml_id})")

            # Extract XML content from ZIP using Python zipfile (unzip has issues with these ZIP files)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                with zip_ref.open(internal_path) as xml_file:
                    xml_content = xml_file.read()

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
                self.log_error(f"Unsupported form type {form_type} in {filename}")
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
                        self.log_info(f"TRACE: Formatted EIN: '{formatted_ein}' (valid 9-digit)")
                        return formatted_ein
                    else:
                        self.logger.warning(f"TRACE: Non-digit EIN found: '{raw_ein}', returning 'Unknown'")
                        return "Unknown"
            except Exception as e:
                self.log_debug(f"XPath {xpath.path} failed: {e}")
                continue
        self.logger.warning("TRACE: No EIN found in XML, returning 'Unknown'")
        return "Unknown"

    def _mark_xml_error(self, xml_id: int, error_msg: str):
        """Mark XML file as having an error"""
        self.db_ops.execute_query("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (CURRENT_PROCESSING_VERSION, error_msg, xml_id))
        self.db_ops.commit()

    def _mark_xml_processed(self, xml_id: int):
        """Mark XML file as processed"""
        self.db_ops.execute_query("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?
            WHERE xml_id = ?
        """, (CURRENT_PROCESSING_VERSION, xml_id))
        self.db_ops.commit()

    def _parse_990_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990 data"""
        # Use the refactored parse_990 function that returns dataclasses directly
        charity, officers = parse_990(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        # Extract grants, contractors, and political contributions
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
        charity, officers = parse_990ez(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        # Extract grants, contractors, and political contributions
        grants: List[Grant] = self._extract_grants_990ez(root, filename, filer_ein, tax_year)
        contractors: List[Contractor] = self._extract_contractors_990ez(root, filename, filer_ein, tax_year)
        contributions: List[PoliticalContribution] = self._extract_political_contributions_990ez(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def _parse_990pf_data(self, root, filename: str, filer_ein: str, tax_year: int, form_type: str) -> Tuple[Optional[Charity], List[Officer], List[Grant], List[Contractor], List[PoliticalContribution]]:
        """Parse Form 990PF data"""
        # Use the refactored parse_990pf function that returns dataclasses directly
        charity, officers = parse_990pf(root, filename, {}, filer_ein, tax_year, form_type, log_error=self.log_error)

        # Extract grants, contractors, and political contributions
        grants = self._extract_grants_990pf(root, filename, filer_ein, tax_year)
        contractors = self._extract_contractors_990pf(root, filename, filer_ein, tax_year)
        contributions = self._extract_political_contributions_990pf(root, filename, filer_ein, tax_year)

        return charity, officers, grants, contractors, contributions

    def geolocate_addresses(self):
        """Geolocate addresses using census API (step 7)"""
        self.log_info("Starting address geolocation")

        # DEBUG: Check if we have addresses to geocode
        addresses = self.db_ops.get_addresses_for_geocoding()
        self.log_info(f"DEBUG: Found {len(addresses)} addresses for geocoding")

        if not addresses:
            self.log_error("No addresses found for geocoding - XML processing may not have completed successfully")
            return 0

        return self.geocoding_strategy.execute(addresses)

    def _geolocate_batch(self, batch: List[Tuple]) -> List[Tuple]:
        """Geolocate a batch of addresses"""
        # This method is now handled by geolocation_processor.py
        return []

    def match_grants_by_address(self):
        """Match grants with unknown EINs by address or colocator (step 9)"""
        self.log_info("Matching grants with unknown EINs by address/colocator")
        return self.address_matching_strategy.execute()

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

    def export_final_tsvs(self):
        """Export final TSV files (step 11)"""
        self.log_info("Exporting final TSV files")
        if self.tsv_exporter:
            self.tsv_exporter.export_final_tsvs()


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
    parser.add_argument("--step", choices=["all", "zip", "xml", "address", "geolocate",
                                          "match", "percentiles", "export"],
                        default="all", help="Processing step to run")

    args = parser.parse_args()

    processor = IRS990Processor(
        db_path=args.db_path,
        zips_dir=args.zips_dir,
        out_dir=args.out_dir,
        anal_dir=args.anal_dir,
        final_dir=args.final_dir,
        verbose=args.verbose,
        quiet=args.quiet,
        max_files=args.max_files,
        log_sql=args.log_sql,
        workers=args.workers
    )

    try:
        if args.step in ["all", "zip"]:
            processor.process_zip_files(args.start_year, args.end_year)

        if args.step in ["all", "xml"]:
            processor.process_xml_files()

        if args.step in ["all", "address"]:
            # Address processing is part of XML processing
            pass

        if args.step in ["all", "geolocate"]:
            processor.geolocate_addresses()

        if args.step in ["all", "match"]:
            processor.match_grants_by_address()

        if args.step in ["all", "percentiles"]:
            processor.calculate_percentiles()

        if args.step in ["all", "export"]:
            processor.export_final_tsvs()

    except Exception as e:
        processor.logger.error(f"Processing failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()