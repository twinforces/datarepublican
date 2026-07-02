#!/usr/bin/env python3
"""
geolocate_archive_processor.py - Export successful geocodes for the next rebuild.

Runs last in the geolocate trilogy (after geolocate_new). Writes
{final_dir}/geocode_archive_distinct.tsv.gz with columns:
  canonical_address, colocator

colocator is LL:lat:lon for successes, or grok:<CODE> for Grok-classified failures
(e.g. grok:NOTA). Merges with any existing archive so prior results are preserved.
"""

import os
import gzip
import csv
import shutil
from typing import Dict

from database_operations import DatabaseOperations
from logging_utils import log_info, log_warning
from config import global_config


class GeolocateArchiveProcessor:
    """Export distinct canonical_address → colocator pairs from successful geocodes."""

    SUCCESS_STATUSES = (
        'Match', 'Non_Exact', 'Match:Archive',
        'Tie', 'Exact',  # legacy status strings if present
    )
    SUCCESS_STATUS_PREFIXES = ('Match:',)

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

    def run(self, output_file: str = "geocode_archive_distinct.tsv.gz") -> int:
        output_path = os.path.join(global_config.final_dir, output_file)
        log_info(f"=== Starting geolocate_archive → {output_path} ===")

        rows = self._fetch_archive_pairs()
        log_info(f"Fetched {len(rows):,} distinct geocode archive pairs from DB")

        merged = self._merge_existing_archive(output_path, rows)
        log_info(f"Merged archive: {len(merged):,} distinct pairs (existing + new)")

        self._write_archive(output_path, merged)
        log_info(f"=== geolocate_archive complete: {len(merged):,} rows written ===")
        return len(merged)

    def _fetch_archive_pairs(self) -> Dict[str, str]:
        status_list = ", ".join(f"'{s}'" for s in self.SUCCESS_STATUSES)
        prefix_checks = " OR ".join(
            f"g.geocoding_status LIKE '{pfx}%'" for pfx in self.SUCCESS_STATUS_PREFIXES
        )
        result = self.db_ops.execute_query(f"""
            SELECT
                g.canonical_address,
                COALESCE(
                    NULLIF(TRIM(g.colocator), ''),
                    ANY_VALUE(a.colocator)
                ) AS colocator
            FROM Geocoding g
            LEFT JOIN Addresses a ON a.geocoding_id = g.geocoding_id
            WHERE (
                g.geocoding_status IN ({status_list})
                OR {prefix_checks}
                OR g.geocoding_status LIKE 'grok:%'
            )
              AND g.canonical_address IS NOT NULL
              AND TRIM(g.canonical_address) != ''
              AND COALESCE(NULLIF(TRIM(g.colocator), ''), NULLIF(TRIM(a.colocator), '')) IS NOT NULL
            GROUP BY g.canonical_address
        """).fetchall()

        return {str(canon): str(coloc) for canon, coloc in result if canon and coloc}

    def _merge_existing_archive(self, output_path: str, new_rows: Dict[str, str]) -> Dict[str, str]:
        merged = dict(new_rows)
        if not os.path.exists(output_path):
            return merged

        try:
            with gzip.open(output_path, 'rt', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    canon = row.get('canonical_address')
                    coloc = row.get('colocator')
                    if canon and coloc and canon not in merged:
                        merged[canon] = coloc
            log_info(f"Merged {len(merged) - len(new_rows):,} pairs from existing archive")
        except Exception as e:
            log_warning(f"Could not read existing archive for merge: {e}")
        return merged

    def _write_archive(self, output_path: str, rows: Dict[str, str]) -> None:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        tmp_path = output_path + ".tmp"
        try:
            with gzip.open(tmp_path, 'wt', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter='\t', lineterminator='\n')
                writer.writerow(['canonical_address', 'colocator'])
                for canon in sorted(rows.keys()):
                    writer.writerow([canon, rows[canon]])
            shutil.move(tmp_path, output_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)