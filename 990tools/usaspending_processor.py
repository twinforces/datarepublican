#!/usr/bin/env python3
"""
usaspending_processor.py — FY award-transaction CSVs, slim COPY, join later by UEI.

Source: Award Data Archive (files.usaspending.gov/award_data_archive/).
One contracts ZIP + one assistance ZIP per fiscal year. `--start-year` /
`--end-year` (default FY2022–2030, UEI era). Full files republish monthly.
Does **not** download the Postgres database dump. Does **not** write
Addresses — SAM pins already exist; reports join recipient_uei → sam_entities.uei.

CSVs live on Insurrection by default (USASPENDING_DATA_DIR). DuckDB is the
Data volume primary.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database_operations import DatabaseOperations
from download_utils import ensure_download
from logging_utils import log_info, log_warning

ARCHIVE_BASE = "https://files.usaspending.gov/award_data_archive"
# Monthly Full files share one stamp (YYYYMMDD). Default is the Oct 2026 drop.
DEFAULT_STAMP = "20261006"
# Award Data Archive Full files start FY2008. We ingest FY2022+ (UEI; DUNS before that).
ARCHIVE_FY_MIN = 2008
USASPENDING_FY_MIN = 2022
FAMILIES = (("contract", "Contracts"), ("assistance", "Assistance"))
INGEST_VERSION = 1
MARKER_NAME = ".usaspending_ingest.json"
ZIP_NAME_RE = re.compile(
    r"FY(\d{4})_All_(Contracts|Assistance)_Full_(\d{8})\.zip$", re.I
)
STAMP_RE = re.compile(r"Full_(\d{8})\.zip$", re.I)

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


def zip_name(fy: int, kind: str, stamp: str) -> str:
    return f"FY{int(fy)}_All_{kind}_Full_{stamp}.zip"


def clamp_years(start_year: int, end_year: int) -> Tuple[int, int]:
    """Raise start to FY2022. Archive files exist from FY2008; we do not load them."""
    lo = max(int(start_year), USASPENDING_FY_MIN, ARCHIVE_FY_MIN)
    hi = int(end_year)
    return lo, hi


def year_specs(start_year: int, end_year: int, stamp: str) -> List[Tuple[str, int, str]]:
    """(family, fy, filename) for each Full archive in [start_year, end_year]."""
    lo = max(int(start_year), ARCHIVE_FY_MIN)
    hi = int(end_year)
    if hi < lo:
        return []
    out: List[Tuple[str, int, str]] = []
    for fy in range(lo, hi + 1):
        for family, kind in FAMILIES:
            out.append((family, fy, zip_name(fy, kind, stamp)))
    return out


def stamp_from_disk(data_dir: Path) -> Optional[str]:
    stamps = []
    if not data_dir.is_dir():
        return None
    for p in data_dir.glob("FY*_All_*_Full_*.zip"):
        m = STAMP_RE.search(p.name)
        if m:
            stamps.append(m.group(1))
    return max(stamps) if stamps else None


def resolve_stamp(data_dir: Optional[Path] = None, stamp: Optional[str] = None) -> str:
    if stamp:
        return stamp.strip()
    env = os.environ.get("USASPENDING_STAMP", "").strip()
    if env:
        return env
    if data_dir is not None:
        found = stamp_from_disk(data_dir)
        if found:
            return found
    return DEFAULT_STAMP


class UsaSpendingProcessor:
    """Download FY archive ZIPs, unzip, slim COPY, roll up by UEI."""

    def __init__(
        self,
        db_ops: DatabaseOperations,
        data_dir: Optional[Path] = None,
        max_files: Optional[int] = None,
        start_year: int = 2022,
        end_year: int = 2030,
        stamp: Optional[str] = None,
    ):
        self.db_ops = db_ops
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.max_files = max_files  # row cap per CSV (smoke)
        self.start_year = int(start_year)
        self.end_year = int(end_year)
        self.stamp = stamp

    def run(
        self,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        stamp: Optional[str] = None,
    ) -> Dict[str, int]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        stats: Dict[str, int] = {"files": 0, "actions": 0, "ueis": 0, "years": 0}
        raw0 = self.start_year if start_year is None else int(start_year)
        raw1 = self.end_year if end_year is None else int(end_year)
        fy0, fy1 = clamp_years(raw0, raw1)
        if fy0 != raw0:
            log_info(f"USASpending start FY{raw0} raised to FY{fy0} (UEI floor)")
        use_stamp = resolve_stamp(self.data_dir, stamp or self.stamp)
        specs = year_specs(fy0, fy1, use_stamp)
        log_info(
            f"USASpending FY{fy0}–FY{fy1} stamp={use_stamp} ({len(specs)} archive names)"
        )

        present: List[Tuple[str, int, str, Path]] = []
        for family, fy, name in specs:
            dest = self.data_dir / name
            url = f"{ARCHIVE_BASE}/{name}"
            log_info(f"USASpending {family} FY{fy}: {url}")
            try:
                ensure_download(url, dest, resume=True, timeout=0)
            except (RuntimeError, subprocess.CalledProcessError) as exc:
                msg = str(exc)
                if dest.exists() and dest.stat().st_size > 1_000_000:
                    log_warning(f"USASpending {name} download error; using local file")
                    present.append((family, fy, name, dest))
                    continue
                if "curl 22" in msg or "HTTP error" in msg:
                    log_warning(f"USASpending skip missing archive {name}: {exc}")
                    continue
                raise
            if not dest.exists() or dest.stat().st_size == 0:
                log_warning(f"USASpending skip missing archive {name}")
                continue
            present.append((family, fy, name, dest))

        if not present:
            raise RuntimeError(
                f"no USASpending Full archives for FY{fy0}–FY{fy1} stamp={use_stamp}"
            )

        marker = self.data_dir / MARKER_NAME
        self._ensure_table()
        copied_any = False
        years_done = set()
        for fy in sorted({fy for _, fy, _, _ in present}):
            year_rows = [(fam, name, path) for fam, y, name, path in present if y == fy]
            year_paths = [path for _, _, path in year_rows]
            if self._year_current(fy, year_paths, marker):
                log_info(f"USASpending FY{fy} unchanged — skip COPY")
                years_done.add(fy)
                continue
            for family, name, zip_path in year_rows:
                self._replace_family(fy, family)
                for csv_path in self._extract_csvs(zip_path):
                    n = self._copy_csv(csv_path, family, fy)
                    stats["files"] += 1
                    stats["actions"] += n
                    copied_any = True
                    log_info(
                        f"USASpending copied {n:,} {family} FY{fy} rows from {csv_path.name}"
                    )
            years_done.add(fy)
            self._merge_marker(marker, year_paths)

        if copied_any:
            self._rebuild_by_uei()
            self._index()
        stats["years"] = len(years_done)
        stats.update(self._existing_counts(stats))
        log_info(f"=== USASpending complete: {stats} ===")
        return stats

    def _ensure_table(self) -> None:
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute(ACTIONS_TABLE_SQL)

    def _replace_family(self, fy: int, family: str) -> None:
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute(
                "DELETE FROM usaspending_actions WHERE fiscal_year = ? AND award_family = ?",
                [int(fy), family],
            )

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

    def _marker_files(self, marker: Path) -> Dict[str, int]:
        if not marker.exists():
            return {}
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if meta.get("ingest_version") != INGEST_VERSION:
            return {}
        out: Dict[str, int] = {}
        for item in meta.get("files") or []:
            path = item.get("path")
            size = item.get("size")
            if path and isinstance(size, int):
                out[path] = size
        return out

    def _year_current(self, fy: int, paths: List[Path], marker: Path) -> bool:
        marked = self._marker_files(marker)
        for p in paths:
            if marked.get(str(p.resolve())) != p.stat().st_size:
                return False
        try:
            count = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM usaspending_actions WHERE fiscal_year = ?",
                (int(fy),),
            ).fetchone()[0]
        except Exception:
            return False
        return int(count) > 0

    def _merge_marker(self, marker: Path, paths: List[Path]) -> None:
        files = self._marker_files(marker)
        for p in paths:
            files[str(p.resolve())] = p.stat().st_size
        payload = {
            "ingest_version": INGEST_VERSION,
            "files": [
                {"path": path, "size": size, "name": Path(path).name}
                for path, size in sorted(files.items())
            ],
        }
        marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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


