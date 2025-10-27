#!/usr/bin/env python3
"""
zip_processor.py - ZIP file processing for IRS 990 data

This module handles the processing of IRS ZIP files, including
downloading, listing contents, and registering files in the database.
Includes ZIP file connection caching for improved performance.
"""

import os
import zipfile
from pathlib import Path
from typing import List

# ZipFile and XMLFile are imported from database_operations
from database_operations import DatabaseOperations
from models import ZipFile, XMLFile
from logging_utils import start_progress_reporting, stop_progress_reporting, update_progress


class ZipProcessor:
    """Handles ZIP file processing operations"""


    def __init__(self, db_ops: DatabaseOperations, zips_dir: str):
        self.db_ops = db_ops
        self.zips_dir = zips_dir

    def process_zip_files(self, start_year: int, end_year: int) -> List[Path]:
        """Process ZIP files and register XML files (steps 2-4)"""
        print(f"Processing ZIP files from {start_year} to {end_year}")

        # Step 2: Read the directory with the zip files as specified in the args
        zip_files = []
        for year in range(start_year, end_year + 1):
            year_str = f"{year}"
            zip_pattern = f"{year}*.zip"
            for zip_path in Path(self.zips_dir).glob(zip_pattern):
                zip_files.append(zip_path)

        print(f"Found {len(zip_files)} ZIP files to process")

        # Step 3: Pull the list of zip files available from the IRS site, see if there are any new ones to be downloaded
        # For now, we'll work with existing files - download logic can be added later

        # Step 4: Use command line tools to get a listing of each zip file, and register the zip as ZipFile in the database,
        # and the contents as XMLFile
        # Start thread-safe progress reporting
        progress_reporter = start_progress_reporting(
            total=len(zip_files),
            desc="Processing ZIP files",
            unit="zip"
        )

        processed_zips = []
        for zip_path in zip_files:
            try:
                # Check if ZIP file is already processed
                zip_filename = zip_path.name
                existing_zip = self.db_ops.execute_query(
                    "SELECT status FROM ZipFiles WHERE filename = ?",
                    (zip_filename,)
                ).fetchone()

                if existing_zip and existing_zip[0] == 'processed':
                    print(f"Skipping already processed ZIP file: {zip_filename}")
                    update_progress(progress_reporter, 1)
                    continue

                self._process_single_zip(zip_path)
                processed_zips.append(zip_path)
                update_progress(progress_reporter, 1)
            except Exception as e:
                print(f"Failed to process ZIP {zip_path}: {e}")
                update_progress(progress_reporter, 1)

        # Stop progress reporting
        stop_progress_reporting()

        return processed_zips

    def _process_single_zip(self, zip_path: Path):
        """Process a single ZIP file"""
        zip_filename = zip_path.name
        zip_year = int(zip_filename[:4]) if zip_filename[:4].isdigit() else 0

        # Create ZipFile object
        zip_file = ZipFile(
            filename=zip_filename,
            file_path=str(zip_path),
            tax_year=zip_year,
            file_size=zip_path.stat().st_size if zip_path.exists() else None,
            status='downloaded'
        )

        # Use filename + size as simple integrity check (no checksum needed)
        if zip_path.exists():
            zip_file.checksum = f"{zip_path.name}:{zip_path.stat().st_size}"

        # Insert ZIP file into database
        zip_id = self.db_ops.insert_zip_file(zip_file)
        print(f"Registered ZIP file: {zip_filename} (ID: {zip_id})")

        # Extract XML file listing and sizes in one pass
        xml_sizes = self._get_all_xml_sizes(zip_path)
        xml_files = list(xml_sizes.keys())
        print(f"Found {len(xml_files)} XML files")

        # Batch insert all XML files for this ZIP
        xml_file_objects = []
        for xml_filename in xml_files:
            xml_file = XMLFile(
                zip_id=zip_id,
                filename=xml_filename,
                internal_path=xml_filename,
                file_size=xml_sizes[xml_filename]
            )
            xml_file_objects.append(xml_file)

        # Bulk insert XML files
        if xml_file_objects:
            ids = self.db_ops.bulk_insert(xml_file_objects)
            print(f"Bulk inserted {len(xml_file_objects)} XML files for ZIP {zip_filename}")

        # Update ZIP status
        self.db_ops.update_zip_status(zip_id, 'processed')

    def _get_xml_files_from_zip(self, zip_path: Path) -> List[str]:
        """Get XML files from ZIP"""
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Get XML files from ZIP
            xml_files = [f for f in zip_ref.namelist() if f.endswith('.xml')]
        return xml_files

    def _get_all_xml_sizes(self, zip_path: Path) -> dict:
        """Get all XML file sizes from ZIP in one pass, handling corrupt files gracefully"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                xml_sizes = {}
                for info in zip_ref.filelist:
                    if info.filename.endswith('.xml'):
                        xml_sizes[info.filename] = info.file_size
                return xml_sizes
        except zipfile.BadZipFile as e:
            print(f"Corrupt ZIP file {zip_path}: {e}")
            return {}
        except Exception as e:
            print(f"Error reading ZIP file {zip_path}: {e}")
            return {}

