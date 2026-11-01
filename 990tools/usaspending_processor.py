#!/usr/bin/env python3
"""
usaspending_processor.py — FY award-transaction CSVs, slim COPY, join later by UEI.

Source: Award Data Archive (files.usaspending.gov/award_data_archive/).
One contracts ZIP + one assistance ZIP per fiscal year. Does **not** download
the Postgres database dump. Does **not** write Addresses — SAM pins already
exist; reports join recipient_uei → sam_entities.uei.

CSVs live on Insurrection by default (USASPENDING_DATA_DIR). DuckDB is the
Data volume primary.
"""

from __future__ import annotations

import csv
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database_operations import DatabaseOperations
from download_utils import ensure_download
from logging_utils import log_info, log_warning

ARCHIVE_BASE = "https://files.usaspending.gov/award_data_archive"
# Latest full-year pair as of 2026-10-14 archive stamp.
DEFAULT_FILES = (
    ("contract", 2025, "FY2025_All_Contracts_Full_20261006.zip"),
    ("assistance", 2025, "FY2025_All_Assistance_Full_20261006.zip"),
)
INGEST_VERSION = 1
MARKER_NAME = ".usaspending_ingest.json"
ZIP_NAME_RE = re.compile(
    r"FY(\d{4})_All_(Contracts|Assistance)_Full_\d{8}\.zip$", re.I
)

ACTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS usaspending_actions (
    award_family VARCHAR,
    fiscal_year INTEGER,
    award_key VARCHAR,
    award_id VARCHAR,
    recipient_uei VARCHAR,
    recipient_name VARCHAR,
    obligation DOUBLE,
    total_obligated DOUBLE,
    action_date DATE,
    awarding_agency VARCHAR,
    program_code VARCHAR,
    recipient_city VARCHAR,
    recipient_state VARCHAR,
    recipient_zip VARCHAR,
    pop_city VARCHAR,
    pop_state VARCHAR,
    pop_zip VARCHAR
)
"""

BY_UEI_SQL = """
CREATE OR REPLACE TABLE usaspending_by_uei AS
SELECT
    recipient_uei AS uei,
    SUM(COALESCE(obligation, 0)) AS obligation,
    SUM(CASE WHEN award_family = 'contract' THEN COALESCE(obligation, 0) ELSE 0 END)
        AS contract_obligation,
    SUM(CASE WHEN award_family = 'assistance' THEN COALESCE(obligation, 0) ELSE 0 END)
        AS assistance_obligation,
    COUNT(*)::BIGINT AS action_n,
    COUNT(DISTINCT award_key)::BIGINT AS award_n,
    MAX(recipient_name) AS recipient_name
FROM usaspending_actions
WHERE recipient_uei IS NOT NULL AND recipient_uei <> ''
GROUP BY 1
"""

# Logical field → CSV header candidates (lowercase).
FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "award_key": (
        "contract_award_unique_key",
        "assistance_award_unique_key",
    ),
    "award_id": (
        "award_id_piid",
        "award_id_fain",
        "fain",
        "piid",
    ),
    "recipient_uei": ("recipient_uei", "awardee_or_recipient_uei"),
    "recipient_name": (
        "recipient_name",
        "recipient_name_raw",
        "awardee_or_recipient_legal",
    ),
    "obligation": ("federal_action_obligation",),
    "total_obligated": (
        "total_dollars_obligated",
        "total_obligated_amount",
        "total_funding_amount",
    ),
    "action_date": ("action_date",),
    "awarding_agency": (
        "awarding_agency_name",
        "awarding_toptier_agency_name",
    ),
    "program_code": ("naics_code", "cfda_number", "assistance_listing_number"),
    "recipient_city": (
        "recipient_city_name",
        "recipient_location_city_name",
    ),
    "recipient_state": (
        "recipient_state_code",
        "recipient_location_state_code",
    ),
    "recipient_zip": (
        "recipient_zip_5_code",
        "recipient_location_zip5",
        "recipient_zip_4_code",
        "recipient_location_zip4",
    ),
    "pop_city": (
        "primary_place_of_performance_city_name",
        "place_of_performance_city_name",
    ),
    "pop_state": (
        "primary_place_of_performance_state_code",
        "place_of_performance_state_code",
    ),
    "pop_zip": (
        "primary_place_of_performance_zip_5_code",
        "primary_place_of_performance_zip5",
        "place_of_performance_zip5",
        "primary_place_of_performance_zip_4",
    ),
}


def default_data_dir(final_dir: Optional[str] = None) -> Path:
    env = os.environ.get("USASPENDING_DATA_DIR")
    if env:
        return Path(env)
    if final_dir:
        return Path(final_dir) / "cms_data" / "usaspending"
    return Path("/Volumes/Insurrection/990data/cms_data/usaspending")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def csv_headers(path: Path) -> List[str]:
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        row = next(csv.reader(f))
    return [h.strip() for h in row]


def _header_map(headers: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for h in headers:
        key = h.lower()
        if key not in out:
            out[key] = h
    return out


def pick_column(header_map: Dict[str, str], *candidates: str) -> Optional[str]:
    for c in candidates:
        if c.lower() in header_map:
            return header_map[c.lower()]
    return None


def select_sql(headers: List[str], family: str, fiscal_year: int) -> str:
    """DuckDB SELECT list mapping archive headers onto usaspending_actions."""
    hmap = _header_map(headers)
    parts: List[str] = [f"'{family}' AS award_family", f"{int(fiscal_year)} AS fiscal_year"]

    def expr(logical: str, *, zip5: bool = False) -> str:
        raw = pick_column(hmap, *FIELD_ALIASES[logical])
        if raw is None:
            return "NULL"
        ident = _quote_ident(raw)
        if zip5:
            return f"LEFT(CAST({ident} AS VARCHAR), 5)"
        if logical in ("obligation", "total_obligated"):
            return f"TRY_CAST({ident} AS DOUBLE)"
        if logical == "action_date":
            return f"TRY_CAST({ident} AS DATE)"
        return f"CAST({ident} AS VARCHAR)"

    parts.append(f"{expr('award_key')} AS award_key")
    parts.append(f"{expr('award_id')} AS award_id")
    parts.append(f"{expr('recipient_uei')} AS recipient_uei")
    parts.append(f"{expr('recipient_name')} AS recipient_name")
    parts.append(f"{expr('obligation')} AS obligation")
    parts.append(f"{expr('total_obligated')} AS total_obligated")
    parts.append(f"{expr('action_date')} AS action_date")
    parts.append(f"{expr('awarding_agency')} AS awarding_agency")
    parts.append(f"{expr('program_code')} AS program_code")
    parts.append(f"{expr('recipient_city')} AS recipient_city")
    parts.append(f"{expr('recipient_state')} AS recipient_state")
    parts.append(f"{expr('recipient_zip', zip5=True)} AS recipient_zip")
    parts.append(f"{expr('pop_city')} AS pop_city")
    parts.append(f"{expr('pop_state')} AS pop_state")
    parts.append(f"{expr('pop_zip', zip5=True)} AS pop_zip")
    return ",\n        ".join(parts)


def parse_zip_name(name: str) -> Optional[Tuple[str, int]]:
    m = ZIP_NAME_RE.match(name)
    if not m:
        return None
    fy = int(m.group(1))
    kind = m.group(2).lower()
    family = "contract" if kind == "contracts" else "assistance"
    return family, fy


class UsaSpendingProcessor:
    """Download FY archive ZIPs, unzip, slim COPY, roll up by UEI."""

    def __init__(
        self,
        db_ops: DatabaseOperations,
        data_dir: Optional[Path] = None,
        max_files: Optional[int] = None,
    ):
        self.db_ops = db_ops
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.max_files = max_files  # row cap per CSV (smoke)

    def run(self) -> Dict[str, int]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        stats: Dict[str, int] = {"files": 0, "actions": 0, "ueis": 0}
        specs = list(DEFAULT_FILES)
        paths: List[Path] = []
        for family, fy, name in specs:
            dest = self.data_dir / name
            url = f"{ARCHIVE_BASE}/{name}"
            log_info(f"USASpending {family} FY{fy}: {url}")
            ensure_download(url, dest, resume=True, timeout=0)
            if not dest.exists() or dest.stat().st_size == 0:
                raise RuntimeError(f"missing archive {dest}")
            paths.append(dest)

        marker = self.data_dir / MARKER_NAME
        if self._ingest_current(paths, marker):
            return self._existing_counts(stats)

        self._ensure_table()
        for family, fy, name in specs:
            zip_path = self.data_dir / name
            for csv_path in self._extract_csvs(zip_path):
                n = self._copy_csv(csv_path, family, fy)
                stats["files"] += 1
                stats["actions"] += n
                log_info(
                    f"USASpending copied {n:,} {family} FY{fy} rows from {csv_path.name}"
                )

        self._rebuild_by_uei()
        self._index()
        stats.update(self._existing_counts(stats))
        self._save_marker(marker, paths)
        log_info(f"=== USASpending complete: {stats} ===")
        return stats

    def _ensure_table(self) -> None:
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute(ACTIONS_TABLE_SQL)
            conn.execute("DELETE FROM usaspending_actions")

    def _extract_csvs(self, zip_path: Path) -> List[Path]:
        if not zipfile.is_zipfile(zip_path):
            raise RuntimeError(f"not a zip: {zip_path}")
        with zipfile.ZipFile(zip_path) as zf:
            names = sorted(n for n in zf.namelist() if n.lower().endswith(".csv"))
            if not names:
                raise RuntimeError(f"no CSV in {zip_path.name}")
            src_mtime = zip_path.stat().st_mtime
            outs: List[Path] = []
            for inner in names:
                out = self.data_dir / Path(inner).name
                if out.exists() and out.stat().st_mtime >= src_mtime and out.stat().st_size > 0:
                    log_info(f"Using extracted {out.name}")
                    outs.append(out)
                    continue
                log_info(f"Extracting {inner} → {out}")
                with zf.open(inner) as src, open(out, "wb") as dest:
                    while True:
                        chunk = src.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        dest.write(chunk)
                outs.append(out)
        return outs

    def _copy_csv(self, csv_path: Path, family: str, fy: int) -> int:
        headers = csv_headers(csv_path)
        if pick_column(_header_map(headers), "recipient_uei", "awardee_or_recipient_uei") is None:
            raise RuntimeError(f"{csv_path.name} has no recipient_uei column: {headers[:12]}")
        select = select_sql(headers, family, fy)
        limit_sql = ""
        if self.max_files and self.max_files > 0:
            limit_sql = f" LIMIT {int(self.max_files)}"
        sql = f"""
            INSERT INTO usaspending_actions
            SELECT {select}
            FROM read_csv(?, header=true, ignore_errors=true, auto_detect=true,
                          sample_size=50000, all_varchar=true)
            {limit_sql}
        """
        with self.db_ops.acquire_write_conn() as conn:
            before = conn.execute("SELECT COUNT(*) FROM usaspending_actions").fetchone()[0]
            conn.execute(sql, [str(csv_path)])
            after = conn.execute("SELECT COUNT(*) FROM usaspending_actions").fetchone()[0]
        return int(after) - int(before)

    def _rebuild_by_uei(self) -> None:
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute(BY_UEI_SQL)

    def _index(self) -> None:
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usaspending_actions_uei "
                "ON usaspending_actions(recipient_uei)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usaspending_by_uei "
                "ON usaspending_by_uei(uei)"
            )

    def _ingest_current(self, paths: List[Path], marker: Path) -> bool:
        if not marker.exists():
            return False
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if meta.get("ingest_version") != INGEST_VERSION:
            return False
        want = sorted((str(p.resolve()), p.stat().st_size) for p in paths)
        got = [(x.get("path"), x.get("size")) for x in meta.get("files") or []]
        if want != sorted((str(a), b) for a, b in got):
            return False
        try:
            count = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM usaspending_actions"
            ).fetchone()[0]
        except Exception:
            return False
        if int(count) <= 0:
            return False
        log_info(f"Skipping USASpending ingest — source unchanged ({int(count):,} actions)")
        return True

    def _existing_counts(self, stats: Dict[str, int]) -> Dict[str, int]:
        try:
            stats["actions"] = int(
                self.db_ops.execute_query(
                    "SELECT COUNT(*) FROM usaspending_actions"
                ).fetchone()[0]
            )
        except Exception:
            stats["actions"] = 0
        try:
            stats["ueis"] = int(
                self.db_ops.execute_query(
                    "SELECT COUNT(*) FROM usaspending_by_uei"
                ).fetchone()[0]
            )
        except Exception:
            stats["ueis"] = 0
        return stats

    def _save_marker(self, marker: Path, paths: List[Path]) -> None:
        payload = {
            "ingest_version": INGEST_VERSION,
            "files": [
                {"path": str(p.resolve()), "size": p.stat().st_size, "name": p.name}
                for p in paths
            ],
        }
        marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
