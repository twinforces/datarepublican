#!/usr/bin/env python3
"""
export_processor.py - TSV export functionality for IRS 990 data processing

This module handles all TSV export operations for the IRS 990 processor,
including charities, grants, contractors, and political contributions.

Refactored to use producer-consumer pattern with ThreadPoolManager for parallelism.
"""

import os
from pathlib import Path
from typing import List, Tuple, Dict, Any
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from base_processor import BaseProducer, BaseConsumer, ThreadPoolManager, ThreadPoolConfig, PoolConfig
from logging_utils import get_logger


class ExportProducer(BaseProducer):
    """
    Producer for TSV export operations.

    Collects data from database and creates export operations for parallel processing.
    """

    def __init__(self, db_ops: DatabaseOperations, export_type: str, thread_pool_config: ThreadPoolConfig = None):
        super().__init__(db_ops, batch_size=1000, thread_pool_config=thread_pool_config)
        self.export_type = export_type

    def _get_work_batch(self, offset: int) -> List[Dict[str, Any]]:
        """Get a batch of data for export based on export type"""
        if self.export_type == "charities":
            return self.db_ops.get_latest_charities_for_export_batch(offset, self.batch_size)
        elif self.export_type == "grants":
            return self.db_ops.get_grants_for_export_batch(offset, self.batch_size)
        elif self.export_type == "contractors":
            return self.db_ops.get_contractors_for_export_batch(offset, self.batch_size)
        elif self.export_type == "political_contributions":
            return self.db_ops.get_political_contributions_for_export_batch(offset, self.batch_size)
        elif self.export_type == "officers":
            return self.db_ops.get_officers_for_export_batch(offset, self.batch_size)
        else:
            raise ValueError(f"Unknown export type: {self.export_type}")

    def _process_work_batch(self, batch: List[Dict[str, Any]]) -> List[DatabaseOperation]:
        """Process batch of data into export operations"""
        operations = []

        for row in batch:
            # Create export operation with the row data
            operation = DatabaseOperation(
                operation_type=DatabaseOperationType.CUSTOM,
                data={
                    "export_type": self.export_type,
                    "row_data": row
                }
            )
            operations.append(operation)

        return operations


class ExportConsumer(BaseConsumer):
    """
    Consumer for TSV export operations.

    Writes data to TSV files concurrently.
    """

    def __init__(self, db_ops: DatabaseOperations, final_dir: str, thread_pool_config: ThreadPoolConfig = None):
        super().__init__(db_ops, thread_pool_config=thread_pool_config)
        self.final_dir = final_dir
        self.file_handles = {}  # Cache file handles for each export type
        self.headers_written = set()  # Track which headers have been written

    def _process_operations_batch(self, operations_by_type: Dict[str, List[DatabaseOperation]]) -> int:
        """Process export operations and write to TSV files"""
        processed_count = 0

        # Handle custom export operations
        if "custom" in operations_by_type:
            for operation in operations_by_type["custom"]:
                export_type = operation.data["export_type"]
                row_data = operation.data["row_data"]

                self._write_row_to_file(export_type, row_data)
                processed_count += 1

        return processed_count

    def _write_row_to_file(self, export_type: str, row_data: Dict[str, Any]):
        """Write a single row to the appropriate TSV file"""
        # Get or create file handle
        if export_type not in self.file_handles:
            output_path = self._get_output_path(export_type)
            self.file_handles[export_type] = open(output_path, 'w', encoding='utf-8')

            # Write header if not already written
            if export_type not in self.headers_written:
                header = self._get_header(export_type)
                self.file_handles[export_type].write('\t'.join(header) + '\n')
                self.headers_written.add(export_type)

        # Write the row data
        safe_row = self._format_row(row_data)
        self.file_handles[export_type].write('\t'.join(safe_row) + '\n')

    def _get_output_path(self, export_type: str) -> Path:
        """Get the output path for the export type"""
        if export_type == "charities":
            return Path(self.final_dir) / "charities_latest.tsv"
        elif export_type == "grants":
            return Path(self.final_dir) / "grants_latest.tsv"
        elif export_type == "contractors":
            return Path(self.final_dir) / "contractors_latest.tsv"
        elif export_type == "political_contributions":
            return Path(self.final_dir) / "political_contributions_latest.tsv"
        elif export_type == "officers":
            return Path(self.final_dir) / "officers_latest.tsv"
        else:
            raise ValueError(f"Unknown export type: {export_type}")

    def _get_header(self, export_type: str) -> List[str]:
        """Get the header for the export type"""
        if export_type == "charities":
            return [
                "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt",
                "org_type", "total_exp", "prog_exp", "travel_amt", "conferences_amt",
                "officer_comp", "comp_pct", "comp_ptile", "travel_pct", "travel_ptile",
                "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
                "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio",
                "total_assets", "form_type", "denominator", "foreign_office", "foreign_expenses",
                "grants_to_others", "domestic_misrep_flag", "xml_name"
            ]
        elif export_type == "grants":
            return [
                "filer_ein", "filer_name", "grant_ein", "grant_amt", "tax_year",
                "filer_colocator", "grantee_colocator"
            ]
        elif export_type == "contractors":
            return [
                "filer_ein", "name", "amount", "ein", "address", "zip_code",
                "po_box", "tax_year", "colocator"
            ]
        elif export_type == "political_contributions":
            return [
                "filer_ein", "recipient", "amount", "recipient_address",
                "recipient_zip", "recipient_po_box", "tax_year", "colocator"
            ]
        elif export_type == "officers":
            return [
                "charity_id", "first_name", "last_name", "compensation", "tax_year", "photo_url"
            ]
        else:
            raise ValueError(f"Unknown export type: {export_type}")

    def _format_row(self, row_data: Dict[str, Any]) -> List[str]:
        """Format row data, converting None to empty string and escaping tabs/newlines"""
        safe_row = []
        for value in row_data:
            if value is None:
                safe_row.append('')
            else:
                # Escape tabs and newlines
                str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                safe_row.append(str_value)
        return safe_row

    def close_files(self):
        """Close all open file handles"""
        for fh in self.file_handles.values():
            fh.close()
        self.file_handles.clear()


class TSVExporter:
    """Handles TSV export operations for IRS 990 data using producer-consumer pattern"""

    def __init__(self, db_ops: DatabaseOperations, final_dir: str, thread_pool_config: ThreadPoolConfig = None):
        self.db_ops = db_ops
        self.final_dir = final_dir
        self.thread_pool_config = thread_pool_config or ThreadPoolConfig(
            producer_config=PoolConfig(max_workers=4, batch_size=1000),
            consumer_config=PoolConfig(max_workers=1, batch_size=1000)  # Single consumer for file safety
        )
        self.logger = get_logger(self.__class__.__name__)

    def export_final_tsvs(self):
        """Export final TSV files using producer-consumer pattern with ThreadPoolManager"""
        export_types = ["charities", "grants", "contractors", "political_contributions", "officers"]

        for export_type in export_types:
            self._export_single_type_parallel(export_type)

    def _export_single_type_parallel(self, export_type: str):
        """Export a single data type using producer-consumer pattern"""
        self.logger.info(f"Starting parallel export for {export_type}")

        # Create producer and consumer
        producer = ExportProducer(self.db_ops, export_type, self.thread_pool_config)
        consumer = ExportConsumer(self.db_ops, self.final_dir, self.thread_pool_config)

        try:
            # Collect operations using producer
            operations = producer.collect_operations_parallel()

            if not operations:
                self.logger.info(f"No {export_type} data to export")
                return

            # Execute operations using consumer
            processed_count = consumer.execute_operations_parallel(operations)

            self.logger.info(f"Exported {processed_count} {export_type} records")

        finally:
            # Ensure files are closed
            consumer.close_files()

    # Legacy methods for backward compatibility (now use parallel versions)
    def _export_charities_tsv(self):
        """Export charities to TSV (legacy method - now uses parallel processing)"""
        self._export_single_type_parallel("charities")

    def _export_grants_tsv(self):
        """Export grants to TSV (legacy method - now uses parallel processing)"""
        self._export_single_type_parallel("grants")

    def _export_contractors_tsv(self):
        """Export contractors to TSV (legacy method - now uses parallel processing)"""
        self._export_single_type_parallel("contractors")

    def _export_political_contributions_tsv(self):
        """Export political contributions to TSV (legacy method - now uses parallel processing)"""
        self._export_single_type_parallel("political_contributions")

    def _export_officers_tsv(self):
        """Export officers to TSV (legacy method - now uses parallel processing)"""
        self._export_single_type_parallel("officers")

    def _export_charities_tsv(self):
        """Export charities to TSV"""
        charities = self.db_ops.get_latest_charities_for_export()

        output_path = Path(self.final_dir) / "charities_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt",
                "org_type", "total_exp", "prog_exp", "travel_amt", "conferences_amt",
                "officer_comp", "comp_pct", "comp_ptile", "travel_pct", "travel_ptile",
                "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
                "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio",
                "total_assets", "form_type", "denominator", "foreign_office", "foreign_expenses",
                "grants_to_others", "domestic_misrep_flag", "xml_name"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in charities:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        print(f"Exported {len(charities)} charities to {output_path}")

    def _export_grants_tsv(self):
        """Export grants to TSV"""
        grants = self.db_ops.get_grants_for_export()

        output_path = Path(self.final_dir) / "grants_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "filer_ein", "filer_name", "grant_ein", "grant_amt", "tax_year",
                "filer_colocator", "grantee_colocator"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in grants:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        print(f"Exported {len(grants)} grants to {output_path}")

    def _export_contractors_tsv(self):
        """Export contractors to TSV"""
        contractors = self.db_ops.get_contractors_for_export()

        output_path = Path(self.final_dir) / "contractors_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "filer_ein", "name", "amount", "ein", "address", "zip_code",
                "po_box", "tax_year", "colocator"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in contractors:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        print(f"Exported {len(contractors)} contractors to {output_path}")

    def _export_political_contributions_tsv(self):
        """Export political contributions to TSV"""
        contributions = self.db_ops.get_political_contributions_for_export()

        output_path = Path(self.final_dir) / "political_contributions_latest.tsv"
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            header = [
                "filer_ein", "recipient", "amount", "recipient_address",
                "recipient_zip", "recipient_po_box", "tax_year", "colocator"
            ]
            f.write('\t'.join(header) + '\n')

            # Write data rows
            for row in contributions:
                # Convert None to empty string and escape tabs/newlines
                safe_row = []
                for value in row:
                    if value is None:
                        safe_row.append('')
                    else:
                        # Escape tabs and newlines
                        str_value = str(value).replace('\t', '\\t').replace('\n', '\\n')
                        safe_row.append(str_value)
                f.write('\t'.join(safe_row) + '\n')

        print(f"Exported {len(contributions)} political contributions to {output_path}")

    def _export_officers_tsv(self):
        """Export officers to TSV using DuckDB's efficient COPY command"""
        output_path = Path(self.final_dir) / "officers_latest.tsv"

        # Use DuckDB's efficient COPY command to export directly from database
        copy_query = f"""
            COPY (
                SELECT
                    charity_id,
                    first_name,
                    last_name,
                    compensation,
                    tax_year,
                    photo_url
                FROM Officers
                ORDER BY charity_id, last_name, first_name
            ) TO '{output_path}' (HEADER, DELIMITER '\t')
        """

        self.db_ops.execute_query(copy_query)
        print(f"Exported officers to {output_path} using DuckDB COPY")