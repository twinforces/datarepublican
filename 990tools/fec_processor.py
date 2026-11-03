#!/usr/bin/env python3
"""
fec_processor.py - FEC bulk data download, preprocess, raw ingest, and production load.

Pipeline per cycle/file:
  1. curl download zip (idempotent)
  2. extract main .txt
  3. fix broken pipe-delimited lines (concatenate until column count matches)
  4. Stream fixed pipe-delimited CSV → fec_* tables + Addresses (owner_id polymorphic link)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database_operations import DatabaseOperations
from download_utils import ensure_download
from logging_utils import log_info, log_warning
from models import (
    Address,
    FecCandidateSpending,
    FecCommittee,
    FecCommitteeTransaction,
    FecIndividualContribution,
    FecOperatingExpenditure,
)

PROMOTE_BATCH_SIZE = 15_000
CHECKPOINT_EVERY_BATCHES = 10
DELETE_BATCH_SIZE = 50_000


FEC_BASE_URL = "https://www.fec.gov/files/bulk-downloads/"
FEC_HEADER_URL = "https://www.fec.gov/files/bulk-downloads/data_dictionaries/{header_base}_header_file.csv"

# (header_base, zip_prefix, expected_fields, raw_table, min_cycle)
# expected_fields from FEC bulk file layouts (see grabfecdata.py / data dictionaries)
FEC_FILE_TYPES: Tuple[Tuple[str, str, int, str, int], ...] = (
    ("cm", "cm", 31, "fec_raw_committees", 1978),
    ("indiv", "indiv", 24, "fec_raw_individuals", 1980),
    ("oth", "oth", 20, "fec_raw_committee_transactions", 1980),
    ("pas2", "pas2", 22, "fec_raw_pas2", 1980),
    ("oppexp", "oppexp", 23, "fec_raw_oppexp", 2004),
)


def default_data_dir(final_dir: Optional[str] = None) -> Path:
    """FEC zips/extracts. FEC_DATA_DIR wins; else {final_dir}/cms_data/fec."""
    env = os.environ.get("FEC_DATA_DIR")
    if env:
        return Path(env)
    if final_dir:
        return Path(final_dir) / "cms_data" / "fec"
    return Path("/Volumes/Data/final/cms_data/fec")


def parse_cycles(raw: str) -> Optional[List[int]]:
    raw = (raw or "").strip()
    if not raw:
        return None
    cycles: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            cycles.append(int(part))
    return cycles or None


def cycle_zip_names(cycle: int) -> List[str]:
    yy = str(cycle)[2:]
    names: List[str] = []
    for _header_base, zip_prefix, _expected, _raw, min_cycle in FEC_FILE_TYPES:
        if cycle < min_cycle:
            continue
        names.append(f"{zip_prefix}{yy}.zip")
    return names


def zip_url(cycle: int, zip_name: str) -> str:
    return f"{FEC_BASE_URL}{cycle}/{zip_name}"


def extract_marker_path(txt_path: Path) -> Path:
    return txt_path.with_name(txt_path.name + ".from_zip.json")


def extract_is_stale(zip_path: Path, txt_path: Path) -> bool:
    """True when txt is missing or was not extracted from this zip (size+mtime)."""
    marker = extract_marker_path(txt_path)
    if not txt_path.exists() or not marker.exists():
        return True
    try:
        meta = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    st = zip_path.stat()
    return meta.get("zip_mtime") != st.st_mtime or meta.get("zip_size") != st.st_size


def write_extract_marker(zip_path: Path, txt_path: Path) -> None:
    st = zip_path.stat()
    extract_marker_path(txt_path).write_text(
        json.dumps({"zip_mtime": st.st_mtime, "zip_size": st.st_size}),
        encoding="utf-8",
    )


def should_reload_cycle(downloaded: bool, extract_stale: bool) -> bool:
    """Replace DuckDB cycle rows when the zip is new or we just extracted it."""
    return downloaded or extract_stale


def ingest_marker_path(data_dir: Path, raw_table: str, cycle: int) -> Path:
    return data_dir / f".fec_ingested_{raw_table}_{cycle}.json"


def ingest_is_stale(zip_path: Path, marker: Path) -> bool:
    if not zip_path.exists() or not marker.exists():
        return True
    try:
        meta = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    st = zip_path.stat()
    return meta.get("zip_mtime") != st.st_mtime or meta.get("zip_size") != st.st_size


def write_ingest_marker(zip_path: Path, marker: Path) -> None:
    st = zip_path.stat()
    marker.write_text(
        json.dumps({"zip_mtime": st.st_mtime, "zip_size": st.st_size}),
        encoding="utf-8",
    )


def download_cycle_zips(data_dir: Path, cycles: List[int]) -> Dict[str, int]:
    """Download bulk zips only. Does not open DuckDB."""
    stats = {"downloads": 0, "skipped": 0, "failed": 0}
    data_dir.mkdir(parents=True, exist_ok=True)
    for cycle in cycles:
        for name in cycle_zip_names(cycle):
            dest = data_dir / name
            try:
                if ensure_download(zip_url(cycle, name), dest):
                    stats["downloads"] += 1
                    log_info(f"FEC downloaded {name}")
                else:
                    stats["skipped"] += 1
            except RuntimeError as exc:
                log_warning(f"FEC download skipped ({name}): {exc}")
                stats["failed"] += 1
    log_info(f"FEC zip download: {stats}")
    return stats


@dataclass(frozen=True)
class FecFileConfig:
    cycle: int
    zip_name: str
    txt_name: str
    header_base: str
    expected_fields: int
    raw_table: str


class FECProcessor:
    """Download, preprocess, and ingest FEC bulk files into irs990.duckdb."""

    def __init__(
        self,
        db_ops: DatabaseOperations,
        data_dir: str | Path,
        cycles: Optional[List[int]] = None,
    ):
        self.db_ops = db_ops
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cycles = cycles or list(range(2000, 2028, 2))
        self._header_cache: Dict[str, str] = {}

    def run(self) -> Dict[str, int]:
        log_info("=== Starting FEC step (download → preprocess → ingest) ===")
        stats: Dict[str, int] = {"downloads": 0, "ingested_cycles": 0, "rows_promoted": 0}

        for cfg in self._file_configs():
            got = self._ensure_file(cfg, stats)
            if got is None:
                continue
            txt_path, reload_cycle = got
            self._reload_cycle = reload_cycle
            header = self._fec_header(cfg.header_base)
            fixed = self._fix_dangling_lines(txt_path, cfg.expected_fields, header)
            promoted = self._promote_cycle(cfg, fixed)
            write_ingest_marker(
                self.data_dir / cfg.zip_name,
                ingest_marker_path(self.data_dir, cfg.raw_table, cfg.cycle),
            )
            stats["rows_promoted"] += promoted
            stats["ingested_cycles"] += 1

        log_info(f"=== FEC complete: {stats} ===")
        return stats

    def _file_configs(self) -> List[FecFileConfig]:
        configs: List[FecFileConfig] = []
        for cycle in self.cycles:
            yy = str(cycle)[2:]
            for header_base, zip_prefix, expected_fields, raw_table, min_cycle in FEC_FILE_TYPES:
                if cycle < min_cycle:
                    continue
                zip_name = f"{zip_prefix}{yy}.zip"
                configs.append(
                    FecFileConfig(
                        cycle=cycle,
                        zip_name=zip_name,
                        txt_name="",
                        header_base=header_base,
                        expected_fields=expected_fields,
                        raw_table=raw_table,
                    )
                )
        return configs

    def _ensure_file(
        self, cfg: FecFileConfig, stats: Dict[str, int]
    ) -> Optional[Tuple[Path, bool]]:
        url = zip_url(cfg.cycle, cfg.zip_name)
        zip_path = self.data_dir / cfg.zip_name

        downloaded = False
        try:
            downloaded = bool(ensure_download(url, zip_path))
            if downloaded:
                stats["downloads"] += 1
        except RuntimeError as exc:
            log_warning(f"FEC download skipped ({cfg.zip_name}): {exc}")
            if not zip_path.exists():
                return None

        txt_name = self._main_txt_in_zip(zip_path)
        if not txt_name:
            return None

        txt_path = self.data_dir / txt_name
        stale_extract = extract_is_stale(zip_path, txt_path)
        stale_ingest = ingest_is_stale(
            zip_path, ingest_marker_path(self.data_dir, cfg.raw_table, cfg.cycle)
        )
        need_extract = should_reload_cycle(downloaded, stale_extract)
        reload_cycle = need_extract or stale_ingest
        if need_extract:
            log_info(f"Extracting {txt_name} from {cfg.zip_name}")
            subprocess.run(
                ["unzip", "-o", "-qq", str(zip_path), txt_name, "-d", str(self.data_dir)],
                check=True,
            )
            if txt_path.exists():
                write_extract_marker(zip_path, txt_path)
        if not txt_path.exists():
            return None
        return txt_path, reload_cycle

    def _main_txt_in_zip(self, zip_path: Path) -> Optional[str]:
        if not zip_path.exists():
            return None
        proc = subprocess.run(
            ["unzip", "-l", str(zip_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.endswith(".txt") and "by_date/" not in line:
                return line.split()[-1]
        return None

    def _fec_header(self, header_base: str) -> str:
        if header_base in self._header_cache:
            return self._header_cache[header_base]
        url = FEC_HEADER_URL.format(header_base=header_base)
        proc = subprocess.run(
            ["curl", "-sL", "--fail", "--retry", "2", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            self._header_cache[header_base] = ""
            return ""
        fields = [f.strip() for f in proc.stdout.strip().split(",") if f.strip()]
        header = "|".join(fields)
        self._header_cache[header_base] = header
        return header

    def _fix_dangling_lines(
        self, txt_path: Path, expected_fields: int, header: str
    ) -> Path:
        fixed_path = txt_path.with_suffix(".fixed.txt")
        marker = fixed_path.with_suffix(".fixed.meta.json")

        src_mtime = txt_path.stat().st_mtime
        if fixed_path.exists() and marker.exists():
            try:
                meta = json.loads(marker.read_text(encoding="utf-8"))
                if meta.get("source_mtime") == src_mtime and meta.get("expected_fields") == expected_fields:
                    return fixed_path
            except (json.JSONDecodeError, OSError):
                pass

        log_info(f"Fixing FEC line breaks: {txt_path.name} (expect {expected_fields} fields)")
        with open(txt_path, "r", encoding="latin1", errors="ignore") as src, open(
            fixed_path, "w", encoding="utf-8", newline="\n"
        ) as out:
            if header:
                out.write(header + "\n")

            current = ""
            for raw_line in src:
                line = raw_line.rstrip("\n\r")
                if not line.strip():
                    continue

                if not current:
                    current = line
                else:
                    current += line

                fields = current.split("|")
                first = fields[0] if fields else ""
                # Record-type lines start with a letter; whole when field count matches.
                whole = len(fields) >= expected_fields
                if not whole and first and first[0].isalpha() and len(first) >= 3:
                    # Committees often start with C00… — still require field count.
                    whole = len(fields) >= expected_fields

                if whole:
                    out.write(current + "\n")
                    current = ""

            if current.strip():
                out.write(current + "\n")

        marker.write_text(
            json.dumps({"source_mtime": src_mtime, "expected_fields": expected_fields}),
            encoding="utf-8",
        )
        return fixed_path

    @staticmethod
    def _prepare_fec_conn(conn) -> None:
        mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "12GB")
        conn.execute("SET preserve_insertion_order=false")
        conn.execute("SET threads=1")
        conn.execute(f"SET memory_limit='{mem}'")

    @staticmethod
    def _iter_fec_rows(fixed_path: Path):
        with open(fixed_path, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="|"):
                yield row

    def _existing_cycle_rows(self, conn, owner_table: str, cycle: int) -> int:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {owner_table} WHERE report_year = ?",
            (cycle,),
        ).fetchone()
        return int(row[0]) if row else 0

    def _promote_streaming(
        self,
        cycle: int,
        fixed_path: Path,
        address_type: str,
        owner_table: str,
        key_field: str,
        log_label: str,
        build_row,
    ) -> int:
        total = 0
        existing = 0
        owners: List = []
        addresses: List[Address] = []
        batches = 0
        skip_rows = 0

        with self.db_ops.acquire_write_conn() as conn:
            self._prepare_fec_conn(conn)
            existing = self._existing_cycle_rows(conn, owner_table, cycle)
            reload_cycle = getattr(self, "_reload_cycle", False)
            if existing and not reload_cycle:
                skip_rows = existing
                log_info(
                    f"  {log_label} cycle {cycle}: resuming — skip {skip_rows:,} file rows, keep existing DB rows"
                )
                print(
                    f"  {log_label} cycle {cycle}: resuming — skip {skip_rows:,} file rows",
                    flush=True,
                )
            else:
                if existing and reload_cycle:
                    log_info(
                        f"  {log_label} cycle {cycle}: zip updated — replacing {existing:,} rows"
                    )
                self._delete_cycle(conn, address_type, owner_table, cycle)

            skipped = 0
            for row in self._iter_fec_rows(fixed_path):
                if not self._trim(row.get(key_field)):
                    continue
                if skipped < skip_rows:
                    skipped += 1
                    continue

                owner, addr = build_row(row, cycle)
                owners.append(owner)
                if addr is not None:
                    addresses.append(addr)
                if len(owners) < PROMOTE_BATCH_SIZE:
                    continue
                self._flush_promote_batch(
                    conn,
                    owners,
                    addresses,
                    checkpoint=batches % CHECKPOINT_EVERY_BATCHES == CHECKPOINT_EVERY_BATCHES - 1,
                )
                total += len(owners)
                batches += 1
                if batches % 20 == 0:
                    msg = (
                        f"  {log_label} cycle {cycle}: +{total:,} this run "
                        f"({existing + total:,} total)"
                    )
                    log_info(msg)
                    print(msg, flush=True)
                owners = []
                addresses = []

            if owners:
                self._flush_promote_batch(conn, owners, addresses, checkpoint=True)
                total += len(owners)

        promoted = existing + total
        log_info(f"  {log_label} cycle {cycle}: {promoted:,} production rows")
        print(f"  {log_label} cycle {cycle}: {promoted:,} production rows", flush=True)
        return promoted

    def _promote_cycle(self, cfg: FecFileConfig, fixed_path: Path) -> int:
        promoters = {
            "fec_raw_committees": self._promote_committees,
            "fec_raw_individuals": self._promote_individuals,
            "fec_raw_committee_transactions": self._promote_committee_transactions,
            "fec_raw_pas2": self._promote_pas2,
            "fec_raw_oppexp": self._promote_oppexp,
        }
        fn = promoters.get(cfg.raw_table)
        if not fn:
            return 0
        return fn(cfg.cycle, fixed_path)

    @staticmethod
    def _trim(value) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _parse_fec_date(value, fallback_year: int) -> str:
        text = str(value).strip() if value is not None else ""
        if len(text) == 8 and text.isdigit():
            return f"{text[4:]}-{text[:2]}-{text[2:4]}"
        if "/" in text:
            parts = [p.strip() for p in text.split("/")]
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                mm, dd, yyyy = parts
                return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
        if text:
            return text
        return f"{fallback_year}-01-01"

    def _delete_cycle(self, conn, address_type: str, owner_table: str, cycle: int) -> None:
        """Delete one cycle in LIMIT batches. A single 29M DELETE OOMs at 8GB."""
        addr_left = self._batched_delete(
            conn,
            table="Addresses",
            pk="address_id",
            id_select=(
                f"SELECT a.address_id FROM Addresses a "
                f"INNER JOIN {owner_table} o ON a.owner_id = o.id "
                f"WHERE a.address_type = ? AND o.report_year = ?"
            ),
            params=(address_type, cycle),
            label=f"Addresses {address_type} {cycle}",
        )
        owner_left = self._batched_delete(
            conn,
            table=owner_table,
            pk="id",
            id_select=f"SELECT id FROM {owner_table} WHERE report_year = ?",
            params=(cycle,),
            label=f"{owner_table} {cycle}",
        )
        log_info(
            f"  deleted cycle {cycle}: {addr_left:,} addresses, {owner_left:,} {owner_table}"
        )

    def _batched_delete(
        self,
        conn,
        *,
        table: str,
        pk: str,
        id_select: str,
        params: tuple,
        label: str,
        batch: int = DELETE_BATCH_SIZE,
    ) -> int:
        deleted = 0
        while True:
            conn.execute("DROP TABLE IF EXISTS _fec_del")
            conn.execute(
                f"CREATE TEMP TABLE _fec_del AS {id_select} LIMIT {int(batch)}",
                params,
            )
            n = int(conn.execute("SELECT COUNT(*) FROM _fec_del").fetchone()[0] or 0)
            if n == 0:
                conn.execute("DROP TABLE IF EXISTS _fec_del")
                return deleted
            conn.execute(
                f"DELETE FROM {table} WHERE {pk} IN (SELECT {pk} FROM _fec_del)"
            )
            conn.execute("DROP TABLE IF EXISTS _fec_del")
            deleted += n
            conn.execute("CHECKPOINT")
            if deleted == n or deleted % (batch * 10) == 0:
                log_info(f"  deleting {label}: {deleted:,} so far")
                print(f"  deleting {label}: {deleted:,} so far", flush=True)
        return deleted

    def _flush_promote_batch(self, conn, owners, addresses, checkpoint: bool = False) -> None:
        if owners:
            self.db_ops.bulk_insert(owners, conn=conn, commit_batches=True)
        if addresses:
            self.db_ops.bulk_insert(addresses, conn=conn, commit_batches=True)
        if checkpoint:
            conn.execute("CHECKPOINT")

    def _promote_committees(self, cycle: int, fixed_path: Path) -> int:
        def build_row(row, report_year):
            cmte = FecCommittee(
                fec_cmte_id=row.get("CMTE_ID") or "",
                name=row.get("CMTE_NM") or "",
                treasurer_name=self._trim(row.get("TRES_NM")),
                report_year=report_year,
            )
            cmte.prep_for_insert()
            addr = None
            if self._trim(row.get("CMTE_ST1")):
                addr = cmte.build_address(
                    address_line1=self._trim(row.get("CMTE_ST1")),
                    address_line2=self._trim(row.get("CMTE_ST2")),
                    city=self._trim(row.get("CMTE_CITY")),
                    state=self._trim(row.get("CMTE_ST")),
                    zip_code=self._trim(row.get("CMTE_ZIP")),
                )
            return cmte, addr

        return self._promote_streaming(
            cycle,
            fixed_path,
            "fec_committee",
            "fec_committees",
            "CMTE_ID",
            "fec_committees",
            build_row,
        )

    def _promote_individuals(self, cycle: int, fixed_path: Path) -> int:
        def build_row(row, report_year):
            contrib = FecIndividualContribution(
                fec_sub_id=row.get("SUB_ID") or "",
                fec_cmte_id=row.get("CMTE_ID") or "",
                contributor_name=row.get("NAME") or "",
                contribution_amount=float(row.get("TRANSACTION_AMT") or 0),
                contribution_date=self._parse_fec_date(row.get("TRANSACTION_DT"), report_year),
                occupation=self._trim(row.get("OCCUPATION")),
                employer=self._trim(row.get("EMPLOYER")),
                report_year=report_year,
            )
            contrib.prep_for_insert()
            addr = None
            if self._trim(row.get("CITY")):
                addr = contrib.build_address(
                    city=self._trim(row.get("CITY")),
                    state=self._trim(row.get("STATE")),
                    zip_code=self._trim(row.get("ZIP_CODE")),
                )
            return contrib, addr

        return self._promote_streaming(
            cycle,
            fixed_path,
            "fec_contributor",
            "fec_individual_contributions",
            "SUB_ID",
            "fec_individual_contributions",
            build_row,
        )

    def _promote_committee_transactions(self, cycle: int, fixed_path: Path) -> int:
        def build_row(row, report_year):
            txn = FecCommitteeTransaction(
                fec_sub_id=row.get("SUB_ID") or "",
                fec_cmte_id=row.get("CMTE_ID") or "",
                other_cmte_id=row.get("OTHER_ID") or "",
                transaction_amount=float(row.get("TRANSACTION_AMT") or 0),
                transaction_date=self._parse_fec_date(row.get("TRANSACTION_DT"), report_year),
                transaction_type=row.get("TRANSACTION_TP") or "",
                report_year=report_year,
            )
            txn.prep_for_insert()
            addr = None
            if self._trim(row.get("CITY")):
                addr = txn.build_address(
                    name=self._trim(row.get("NAME")),
                    city=self._trim(row.get("CITY")),
                    state=self._trim(row.get("STATE")),
                    zip_code=self._trim(row.get("ZIP_CODE")),
                )
            return txn, addr

        return self._promote_streaming(
            cycle,
            fixed_path,
            "fec_committee_transaction",
            "fec_committee_transactions",
            "SUB_ID",
            "fec_committee_transactions",
            build_row,
        )

    def _promote_pas2(self, cycle: int, fixed_path: Path) -> int:
        def build_row(row, report_year):
            purpose = self._trim(row.get("NAME")) or self._trim(row.get("MEMO_TEXT")) or ""
            spending = FecCandidateSpending(
                fec_sub_id=row.get("SUB_ID") or "",
                fec_cand_id=row.get("CAND_ID") or "",
                fec_cmte_id=self._trim(row.get("CMTE_ID")),
                spending_amount=float(row.get("TRANSACTION_AMT") or 0),
                spending_date=self._parse_fec_date(row.get("TRANSACTION_DT"), report_year),
                payee_name=row.get("NAME") or "",
                purpose=purpose,
                report_year=report_year,
            )
            spending.prep_for_insert()
            addr = None
            if self._trim(row.get("CITY")):
                addr = spending.build_address(
                    city=self._trim(row.get("CITY")),
                    state=self._trim(row.get("STATE")),
                    zip_code=self._trim(row.get("ZIP_CODE")),
                )
            return spending, addr

        return self._promote_streaming(
            cycle,
            fixed_path,
            "fec_candidate_spending",
            "fec_candidate_spendings",
            "SUB_ID",
            "fec_candidate_spendings",
            build_row,
        )

    def _promote_oppexp(self, cycle: int, fixed_path: Path) -> int:
        def build_row(row, report_year):
            exp = FecOperatingExpenditure(
                fec_sub_id=row.get("SUB_ID") or "",
                fec_cmte_id=row.get("CMTE_ID") or "",
                payee_name=row.get("NAME") or "",
                expenditure_amount=float(row.get("TRANSACTION_AMT") or 0),
                expenditure_date=self._parse_fec_date(row.get("TRANSACTION_DT"), report_year),
                purpose=row.get("PURPOSE") or "",
                report_year=report_year,
            )
            exp.prep_for_insert()
            addr = None
            if self._trim(row.get("CITY")):
                addr = exp.build_address(
                    city=self._trim(row.get("CITY")),
                    state=self._trim(row.get("STATE")),
                    zip_code=self._trim(row.get("ZIP_CODE")),
                )
            return exp, addr

        return self._promote_streaming(
            cycle,
            fixed_path,
            "fec_operating_expenditure",
            "fec_operating_expenditures",
            "SUB_ID",
            "fec_operating_expenditures",
            build_row,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="FEC bulk zip download / ingest helpers")
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download zips without opening DuckDB",
    )
    args = parser.parse_args()
    if not args.download_only:
        parser.error("pass --download-only (ingest is irs990processor --step fec)")
    cycles = parse_cycles(os.environ.get("FEC_CYCLES", "")) or list(range(2000, 2028, 2))
    data_dir = default_data_dir()
    stats = download_cycle_zips(data_dir, cycles)
    if stats["failed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())