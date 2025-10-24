#!/usr/bin/env python3
"""
extract_processor.py - XML file extraction by EIN

This module handles extracting XML files for specific EINs from ZIP archives
to a specified destination directory.
"""

import os
import zipfile
import shutil
from pathlib import Path
from typing import List, Optional

from logging_utils import get_logger, log_info, log_error, log_debug, log_warning
from config import global_config


class ExtractProcessor:
    """Handles XML file extraction by EIN"""

    def __init__(self, db_ops, zips_dir: str):
        self.db_ops = db_ops
        self.zips_dir = zips_dir
        self.logger = get_logger("extract_processor")

    def extract_xml_files(self, eins: List[str], dest_dir: str) -> int:
        """Extract XML files for specified EINs to destination directory"""
        log_info(self.logger, f"Extracting XML files for {len(eins)} EINs to {dest_dir}")

        # Create destination directory
        os.makedirs(dest_dir, exist_ok=True)

        extracted_count = 0

        for ein in eins:
            try:
                # Find XML files for this EIN
                xml_files = self._find_xml_files_for_ein(ein)

                if not xml_files:
                    log_warning(self.logger, f"No XML files found for EIN {ein}")
                    continue

                log_debug(self.logger, f"Found {len(xml_files)} XML files for EIN {ein}")

                # Extract each XML file with custom naming
                for xml_file_info in xml_files:
                    if self._extract_single_xml_with_custom_name(xml_file_info, dest_dir, ein):
                        extracted_count += 1

            except Exception as e:
                log_error(self.logger, f"Failed to extract files for EIN {ein}: {e}")

        log_info(self.logger, f"Extraction complete. Extracted {extracted_count} XML files.")
        return extracted_count

    def _find_xml_files_for_ein(self, ein: str) -> List[tuple]:
        """Find XML files for a given EIN"""
        query = """
            SELECT xf.filename, zf.file_path, xf.internal_path, xf.tax_year
            FROM XmlFiles xf
            JOIN ZipFiles zf ON xf.zip_id = zf.zip_id
            WHERE xf.ein = ?
            ORDER BY xf.tax_year, xf.filename
        """
        return self.db_ops.execute_query(query, (ein,)).fetchall()

    def _extract_single_xml_with_custom_name(self, xml_info: tuple, dest_dir: str, ein: str) -> bool:
        """Extract a single XML file with custom naming: <ein>_<tax_year>.xml"""
        filename, zip_path, internal_path, tax_year = xml_info

        try:
            # Create custom filename
            custom_filename = f"{ein}_{tax_year}.xml"
            dest_path = os.path.join(dest_dir, custom_filename)

            # Open ZIP file
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Extract to temporary location first
                temp_path = dest_path + '.tmp'
                with zip_ref.open(internal_path) as source, open(temp_path, 'wb') as target:
                    shutil.copyfileobj(source, target)

                # Move to final location
                os.rename(temp_path, dest_path)

                log_debug(self.logger, f"Extracted {filename} as {custom_filename} to {dest_path}")
                return True

        except Exception as e:
            log_error(self.logger, f"Failed to extract {filename}: {e}")
            # Clean up temp file if it exists
            temp_path = os.path.join(dest_dir, f"{ein}_{tax_year}.xml") + '.tmp'
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            return False