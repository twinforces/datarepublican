#!/usr/bin/env python3
"""Award descriptions + PSC/CFDA titles from USASpending archive CSVs → DuckDB FTS.

Slim `usaspending_actions` stays as-is (no description column). This builds
`usaspending_award_text` at award_key grain (longest description wins) and a
DuckDB FTS index over description / PSC title / program title.

Reads the CSVs already extracted on Insurrection. Does not download the
Postgres dump. Not on --step all.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb

from logging_utils import log_info, log_warning
from usaspending_processor import (
    _header_map,
    _quote_ident,
    clamp_years,
    csv_headers,
    default_data_dir,
    pick_column,
)

CSV_NAME_RE = re.compile(
    r"FY(\d{4})_All_(Contracts|Assistance)_Full_(\d{8})(?:_\d+)?\.csv$", re.I
)
MARKER_NAME = ".usaspending_text_ingest.json"
TEXT_VERSION = 1

FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "award_key": (
        "contract_award_unique_key",
        "assistance_award_unique_key",
    ),
    "award_id": ("award_id_piid", "award_id_fain", "fain", "piid"),
    "recipient_uei": ("recipient_uei", "awardee_or_recipient_uei"),
    "recipient_name": (
        "recipient_name",
        "recipient_name_raw",
        "awardee_or_recipient_legal",
    ),
    "description": (
        "transaction_description",
        "prime_award_base_transaction_description",
        "award_description",
    ),
    "psc": ("product_or_service_code",),
    "psc_description": ("product_or_service_code_description",),
    "program_code": ("naics_code", "cfda_number", "assistance_listing_number"),
    "program_title": ("naics_description", "cfda_title"),
}

TEXT_TABLE_SQL = """
CREATE TABLE usaspending_award_text (
    award_key VARCHAR,
    award_family VARCHAR,
    fiscal_year INTEGER,
    award_id VARCHAR,
    recipient_uei VARCHAR,
    recipient_name VARCHAR,
    description VARCHAR,
    psc VARCHAR,
    psc_description VARCHAR,
    program_code VARCHAR,
    program_title VARCHAR
)
"""
MERGE_PARTITIONS = 32


def _say(msg: str) -> None:
    print(msg, flush=True)
    log_info(msg)


def parse_csv_name(name: str) -> Optional[Tuple[str, int]]:
    m = CSV_NAME_RE.match(name)
    if not m:
        return None
    fy = int(m.group(1))
    kind = m.group(2).lower()
    family = "contract" if kind == "contracts" else "assistance"
    return family, fy


def list_csv_sources(data_dir: Path, fy0: int, fy1: int) -> List[Tuple[str, int, Path]]:
    out: List[Tuple[str, int, Path]] = []
    for path in sorted(data_dir.glob("FY*_All_*_Full_*.csv")):
        parsed = parse_csv_name(path.name)
        if not parsed:
            continue
        family, fy = parsed
        if fy < fy0 or fy > fy1:
            continue
        if path.stat().st_size == 0:
            continue
        out.append((family, fy, path))
    return out


def _expr(hmap: Dict[str, str], logical: str) -> str:
    raw = pick_column(hmap, *FIELD_ALIASES[logical])
    if logical == "description":
        parts = []
        for cand in FIELD_ALIASES["description"]:
            col = pick_column(hmap, cand)
            if col:
                parts.append(f"nullif(trim(CAST({_quote_ident(col)} AS VARCHAR)), '')")
        return f"COALESCE({', '.join(parts)})" if parts else "CAST(NULL AS VARCHAR)"
    if raw is None:
        return "CAST(NULL AS VARCHAR)"
    ident = _quote_ident(raw)
    return f"nullif(trim(CAST({ident} AS VARCHAR)), '')"


def collapse_select_sql(headers: List[str], family: str, fiscal_year: int) -> str:
    hmap = _header_map(headers)
    key = _expr(hmap, "award_key")
    desc = _expr(hmap, "description")
    psc_d = _expr(hmap, "psc_description")
    prog_t = _expr(hmap, "program_title")
    return f"""
        {key} AS award_key,
        '{family}' AS award_family,
        {int(fiscal_year)} AS fiscal_year,
        {_expr(hmap, 'award_id')} AS award_id,
        {_expr(hmap, 'recipient_uei')} AS recipient_uei,
        {_expr(hmap, 'recipient_name')} AS recipient_name,
        {desc} AS description,
        {_expr(hmap, 'psc')} AS psc,
        {psc_d} AS psc_description,
        {_expr(hmap, 'program_code')} AS program_code,
        {prog_t} AS program_title
    """


def collapse_group_sql(headers: List[str], family: str, fiscal_year: int) -> str:
    inner = collapse_select_sql(headers, family, fiscal_year)
    return f"""
        SELECT
            award_key,
            any_value(award_family) AS award_family,
            max(fiscal_year) AS fiscal_year,
            any_value(award_id) AS award_id,
            any_value(recipient_uei) AS recipient_uei,
            any_value(recipient_name) AS recipient_name,
            arg_max(description, length(coalesce(description, ''))) AS description,
            any_value(psc) AS psc,
            arg_max(psc_description, length(coalesce(psc_description, ''))) AS psc_description,
            any_value(program_code) AS program_code,
            any_value(program_title) AS program_title
        FROM (
            SELECT {inner}
            FROM read_csv(?, header=true, ignore_errors=true, auto_detect=true,
                          sample_size=50000, all_varchar=true)
            WHERE { _expr(_header_map(headers), 'award_key') } IS NOT NULL
        )
        GROUP BY 1
    """


def merge_parquets_sql(nparts: int, part: int) -> str:
    """One hash bucket so the warehouse merge fits in 12GB (full GROUP BY OOM'd)."""
    return f"""
        SELECT
            award_key,
            arg_max(award_family, fiscal_year) AS award_family,
            max(fiscal_year) AS fiscal_year,
            arg_max(award_id, fiscal_year) AS award_id,
            arg_max(recipient_uei, fiscal_year) AS recipient_uei,
            arg_max(recipient_name, fiscal_year) AS recipient_name,
            arg_max(CAST(description AS VARCHAR), length(coalesce(CAST(description AS VARCHAR), ''))) AS description,
            arg_max(CAST(psc AS VARCHAR), fiscal_year) AS psc,
            arg_max(CAST(psc_description AS VARCHAR), length(coalesce(CAST(psc_description AS VARCHAR), ''))) AS psc_description,
            arg_max(CAST(program_code AS VARCHAR), fiscal_year) AS program_code,
            arg_max(CAST(program_title AS VARCHAR), length(coalesce(CAST(program_title AS VARCHAR), ''))) AS program_title
        FROM read_parquet(?, union_by_name=true)
        WHERE (hash(CAST(award_key AS VARCHAR)) % {int(nparts)}) = {int(part)}
        GROUP BY 1
    """


class UsaSpendingTextProcessor:
    def __init__(
        self,
        db_path: str,
        data_dir: Optional[Path] = None,
        start_year: int = 2022,
        end_year: int = 2030,
        max_files: Optional[int] = None,
    ):
        self.db_path = db_path
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.start_year = int(start_year)
        self.end_year = int(end_year)
        self.max_files = max_files
        self.parts_dir = self.data_dir / "text_parts"
        self.merge_partitions = int(os.environ.get("USASPENDING_TEXT_PARTITIONS", MERGE_PARTITIONS))

    def run(self) -> Dict[str, int]:
        fy0, fy1 = clamp_years(self.start_year, self.end_year)
        sources = list_csv_sources(self.data_dir, fy0, fy1)
        if self.max_files and self.max_files > 0:
            sources = sources[: int(self.max_files)]
        if not sources:
            raise RuntimeError(
                f"no extracted USASpending CSVs in {self.data_dir} for FY{fy0}–FY{fy1}"
            )
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        marker = self.data_dir / MARKER_NAME
        mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "12GB")
        _say(
            f"USASpending text FY{fy0}–FY{fy1} files={len(sources)} "
            f"db={self.db_path} mem={mem}"
        )
        conn = duckdb.connect(
            ":memory:",
            config={"memory_limit": mem, "threads": "1", "preserve_insertion_order": "false"},
        )
        wrote = 0
        skipped = 0
        try:
            for family, fy, csv_path in sources:
                part = self.parts_dir / (csv_path.stem + ".parquet")
                if self._part_current(part, csv_path, marker):
                    skipped += 1
                    _say(f"skip {csv_path.name}: parquet current")
                    continue
                headers = csv_headers(csv_path)
                if pick_column(
                    _header_map(headers),
                    "contract_award_unique_key",
                    "assistance_award_unique_key",
                ) is None:
                    log_warning(f"skip {csv_path.name}: no award unique key")
                    continue
                sql = collapse_group_sql(headers, family, fy)
                _say(f"collapse {csv_path.name} ({csv_path.stat().st_size:,} bytes) → {part.name}")
                dest = str(part).replace("'", "''")
                conn.execute(
                    f"COPY ({sql}) TO '{dest}' (FORMAT PARQUET)",
                    [str(csv_path)],
                )
                n = int(conn.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(part)]).fetchone()[0])
                self._mark_part(marker, csv_path, part, n)
                wrote += 1
                _say(f"{csv_path.name}: {n:,} award keys")
        finally:
            conn.close()

        parts = sorted(self.parts_dir.glob("*.parquet"))
        if not parts:
            raise RuntimeError("no text parquets written")
        glob = str(self.parts_dir / "*.parquet")
        stats = self._load_warehouse(glob, mem)
        stats["parquet_files"] = len(parts)
        stats["collapsed"] = wrote
        stats["skipped"] = skipped
        _say(f"=== USASpending text complete: {stats} ===")
        return stats

    def _load_warehouse(self, parquet_glob: str, mem: str) -> Dict[str, int]:
        tmp = self.parts_dir / "_duckdb_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        nparts = max(1, self.merge_partitions)
        _say(
            f"merge parquets → usaspending_award_text db={self.db_path} "
            f"partitions={nparts} tmp={tmp}"
        )
        conn = duckdb.connect(
            self.db_path,
            config={
                "memory_limit": mem,
                "threads": "1",
                "preserve_insertion_order": "false",
                "temp_directory": str(tmp),
            },
        )
        stats: Dict[str, int] = {}
        try:
            conn.execute("DROP TABLE IF EXISTS usaspending_award_text")
            conn.execute(TEXT_TABLE_SQL)
            for i in range(nparts):
                _say(f"merge partition {i + 1}/{nparts}")
                conn.execute(
                    f"INSERT INTO usaspending_award_text {merge_parquets_sql(nparts, i)}",
                    [parquet_glob],
                )
                conn.execute("CHECKPOINT")
            n = int(conn.execute("SELECT COUNT(*) FROM usaspending_award_text").fetchone()[0])
            desc_n = int(
                conn.execute(
                    "SELECT COUNT(*) FROM usaspending_award_text "
                    "WHERE description IS NOT NULL AND description <> ''"
                ).fetchone()[0]
            )
            stats["usaspending_award_text"] = n
            stats["with_description"] = desc_n
            _say(f"usaspending_award_text: {n:,} awards ({desc_n:,} with description)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usaspending_award_text_uei "
                "ON usaspending_award_text(recipient_uei)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usaspending_award_text_psc "
                "ON usaspending_award_text(psc)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_usaspending_award_text_key "
                "ON usaspending_award_text(award_key)"
            )
            self._build_fts(conn)
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        return stats

    def _build_fts(self, conn: duckdb.DuckDBPyConnection) -> None:
        try:
            conn.execute("INSTALL fts")
            conn.execute("LOAD fts")
            try:
                conn.execute("PRAGMA drop_fts_index('usaspending_award_text')")
            except duckdb.Error:
                pass
            conn.execute(
                """
                PRAGMA create_fts_index(
                    'usaspending_award_text',
                    'award_key',
                    'description',
                    'psc_description',
                    'program_title',
                    overwrite=1
                )
                """
            )
            _say("FTS index usaspending_award_text (description, psc_description, program_title)")
        except duckdb.Error as exc:
            log_warning(f"FTS index skipped: {exc}")

    def _load_marker(self, marker: Path) -> dict:
        if not marker.exists():
            return {"version": TEXT_VERSION, "files": {}}
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"version": TEXT_VERSION, "files": {}}

    def _part_current(self, part: Path, csv_path: Path, marker: Path) -> bool:
        if not part.exists() or part.stat().st_size == 0:
            return False
        meta = self._load_marker(marker).get("files", {}).get(csv_path.name) or {}
        return int(meta.get("csv_size") or 0) == csv_path.stat().st_size

    def _mark_part(self, marker: Path, csv_path: Path, part: Path, rows: int) -> None:
        payload = self._load_marker(marker)
        payload["version"] = TEXT_VERSION
        payload.setdefault("files", {})[csv_path.name] = {
            "csv_size": csv_path.stat().st_size,
            "parquet": part.name,
            "rows": rows,
        }
        marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="USASpending award text + FTS")
    p.add_argument("--db", default=os.environ.get("DB", "/Volumes/Data/final/irs990.duckdb"))
    p.add_argument("--data-dir", default=None)
    p.add_argument("--start-year", type=int, default=int(os.environ.get("USASPENDING_START_YEAR", "2022")))
    p.add_argument("--end-year", type=int, default=int(os.environ.get("USASPENDING_END_YEAR", "2030")))
    p.add_argument("--max-files", type=int, default=None, help="Cap CSV count (smoke)")
    args = p.parse_args()
    UsaSpendingTextProcessor(
        args.db,
        Path(args.data_dir) if args.data_dir else None,
        start_year=args.start_year,
        end_year=args.end_year,
        max_files=args.max_files,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
