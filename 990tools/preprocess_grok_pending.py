#!/usr/bin/env python3
"""
preprocess_grok_pending.py — One-shot preprocess pass over grok_pending rows.

Re-applies structured short-circuits + geocoding_patterns.json without API/Grok spend.
Many grok_pending rows predate current FA/MILITARY/VENDOR/PARTIAL rules.

Matched → Match:PatternOwners; unmatched stay grok_pending for geolocate_grok.

Usage:
  python3 preprocess_grok_pending.py --dry-run
  python3 preprocess_grok_pending.py
  python3 preprocess_grok_pending.py --max-rows 10000
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from config import global_config
from constants import GEOCODING_PREPROCESS_BATCH_SIZE
from database_operations import DatabaseOperations
from geocoding_api_processor import GeocodingAPIProcessor, GeocodingWorkUnit

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
DEFAULT_FINAL_DIR = "/Volumes/Data/final"
STATUS = "grok_pending"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[grok-prep {ts}] {msg}", flush=True)


def _json_safe(val: Any) -> Any:
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if hasattr(val, "hex"):
        return str(val)
    return str(val)


def fetch_batch(
    db_ops: DatabaseOperations,
    limit: int,
    after_id: Optional[str] = None,
) -> List[tuple]:
    if after_id:
        return db_ops.execute_query(
            """
            SELECT geocoding_id, normalized_address, attempt_count, canonical_address, address_count
            FROM Geocoding
            WHERE geocoding_status = ? AND geocoding_id > ?
            ORDER BY geocoding_id
            LIMIT ?
            """,
            (STATUS, after_id, limit),
        ).fetchall()
    return db_ops.execute_query(
        """
        SELECT geocoding_id, normalized_address, attempt_count, canonical_address, address_count
        FROM Geocoding
        WHERE geocoding_status = ?
        ORDER BY geocoding_id
        LIMIT ?
        """,
        (STATUS, limit),
    ).fetchall()


def rows_to_units(rows: List[tuple]) -> List[GeocodingWorkUnit]:
    units = []
    for row in rows:
        norm = row[1]
        if isinstance(norm, str):
            try:
                norm = json.loads(norm)
            except json.JSONDecodeError:
                norm = {}
        data = {
            "geocoding_id": _json_safe(row[0]),
            "normalized_address": norm,
            "attempt_count": row[2] or 0,
            "canonical_address": row[3],
            "address_count": row[4] or 0,
            "geocoding_status": STATUS,
        }
        units.append(GeocodingWorkUnit.work_item("grok_pending_preprocess", data))
    return units


def count_status(db_ops: DatabaseOperations, status: str) -> int:
    row = db_ops.execute_query(
        "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = ?",
        (status,),
    ).fetchone()
    return int(row[0]) if row else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Preprocess grok_pending with pattern rules")
    parser.add_argument("--db-path", default=DEFAULT_DB)
    parser.add_argument("--final-dir", default=DEFAULT_FINAL_DIR)
    parser.add_argument("--batch-size", type=int, default=GEOCODING_PREPROCESS_BATCH_SIZE)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Public Photon only — avoid hanging probe on offline self-host
    os.environ.pop("PHOTON_DOMAIN", None)
    os.environ.pop("PHOTON_SCHEME", None)

    global_config.final_dir = args.final_dir
    global_config.db_path = args.db_path

    if args.dry_run:
        import duckdb

        con = duckdb.connect(args.db_path, read_only=True)
        pending = con.execute(
            "SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = ?",
            [STATUS],
        ).fetchone()[0]
        con.close()
        log(f"DRY RUN — {pending:,} {STATUS} rows would be scanned")
        return 0

    DatabaseOperations.closePool()
    DatabaseOperations.bootstrap(args.db_path, dbUI=False)
    db_ops = DatabaseOperations(args.db_path, threads=1, init_schema=False)
    proc = GeocodingAPIProcessor(db_ops)
    proc.pipeline_step = "grok_pending_preprocess"

    total_pending = count_status(db_ops, STATUS)
    log(f"Starting grok_pending preprocess: {total_pending:,} rows (batch={args.batch_size:,})")

    scanned = 0
    matched = 0
    batches = 0
    budget = args.max_rows
    after_id: Optional[str] = None

    while True:
        if budget is not None and scanned >= budget:
            break
        limit = args.batch_size
        if budget is not None:
            limit = min(limit, budget - scanned)
            if limit <= 0:
                break

        rows = fetch_batch(db_ops, limit, after_id=after_id)
        if not rows:
            break

        after_id = str(rows[-1][0])
        units = rows_to_units(rows)
        _, batch_matched = proc.apply_preprocess_batch(units)
        scanned += len(rows)
        matched += batch_matched
        batches += 1
        remaining = count_status(db_ops, STATUS)
        log(
            f"batch {batches}: scanned={len(rows):,} matched={batch_matched:,} "
            f"cumulative={matched:,}/{scanned:,} grok_pending_left={remaining:,}"
        )
        if len(rows) < limit:
            break

    owners = count_status(db_ops, "Match:PatternOwners")
    remaining = count_status(db_ops, STATUS)
    log("")
    log("════ GROK_PENDING PREPROCESS DONE ════")
    log(f"  scanned:       {scanned:,}")
    log(f"  matched:       {matched:,} → Match:PatternOwners")
    log(f"  grok_pending:  {remaining:,}")
    log(f"  PatternOwners: {owners:,}")
    log("═" * 36)
    db_ops.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
