#!/usr/bin/env python3
"""
ppp_processor.py — SBA Paycheck Protection Program FOIA ingest.

Source: https://data.sba.gov/dataset/ppp-foia
Public CSVs have borrower name + street, not EIN. ~12 files, ~11.8M loans.
DuckDB COPY for ppp_loans. Addresses: Address.py writes a CSV, then COPY
into Addresses (same canonicalize as DOT/OFAC). Ingest only.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Set

from database_operations import DatabaseOperations
from download_utils import ensure_download
from logging_utils import log_info, log_warning
from models import Address, PppLoan

DATASET_PAGE = "https://data.sba.gov/dataset/ppp-foia"
DISTRIBUTION_PREFIX = (
    "https://data.sba.gov/sites/default/files/distribution/SBA-OCA-2022-07-001/"
)
# Last known vintage (filename stamp 240930; objects re-uploaded 2026-06-26).
FALLBACK_CSV_NAMES = [
    "public_150k_plus_240930.csv",
    *[f"public_up_to_150k_{i}_240930.csv" for i in range(1, 13)],
]
INGEST_VERSION = 3  # Address.py → CSV → DuckDB COPY
CSV_HREF_RE = re.compile(
    r"https://data\.sba\.gov/sites/default/files/distribution/[^\"'\s>]+\.csv",
    re.I,
)

# Heap during COPY. PK/UNIQUE on a growing table makes each file probe every
# existing loan_number (time per file ≈ linear in table size → quadratic total).
PPP_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ppp_loans (
    id UUID,
    loan_number VARCHAR NOT NULL,
    date_approved DATE,
    processing_method VARCHAR,
    borrower_name VARCHAR,
    borrower_address VARCHAR,
    borrower_city VARCHAR,
    borrower_state VARCHAR,
    borrower_zip VARCHAR,
    loan_status VARCHAR,
    current_approval_amount DOUBLE,
    jobs_reported INTEGER,
    naics_code VARCHAR,
    business_type VARCHAR,
    nonprofit BOOLEAN,
    forgiveness_amount DOUBLE,
    forgiveness_date DATE,
    originating_lender VARCHAR,
    servicing_lender_name VARCHAR,
    rural_urban VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

PPP_INSERT_COLS = """
id, loan_number, date_approved, processing_method,
borrower_name, borrower_address, borrower_city,
borrower_state, borrower_zip, loan_status,
current_approval_amount, jobs_reported, naics_code,
business_type, nonprofit, forgiveness_amount,
forgiveness_date, originating_lender,
servicing_lender_name, rural_urban
"""

LOAN_SRC_CSV = "ppp_loan_addr_src.csv"
ADDRESS_CSV = "ppp_borrower_addresses.csv"
PROGRESS_EVERY = 50_000
ADDRESS_CSV_COLS = (
    "address_id",
    "ein",
    "owner_id",
    "master_id",
    "name",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "zip_code",
    "zip4",
    "po_box",
    "canonical_address",
    "address_type",
    "geocoding_id",
    "latitude",
    "longitude",
    "colocator",
)
# Secondary Addresses indexes — drop for the 11M load on a 16GB host, rebuild after.
# PK stays. Same names as address_deduplication_processor._ADDRESS_INDEX_DDL.
PPP_DROP_ADDRESS_INDEXES = (
    "idx_addresses_ein",
    "idx_addresses_zip_code",
    "idx_addresses_type",
    "idx_addresses_geocoding",
    "idx_addresses_master_id",
    "idx_addresses_canonical",
    "idx_dedup_canon_groups",
    "idx_addresses_colocator",
    "idx_addresses_loose_colocator",
    "idx_addresses_canonical_covering",
)
PPP_REBUILD_ADDRESS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_addresses_ein ON Addresses(ein)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_zip_code ON Addresses(zip_code)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_type ON Addresses(address_type)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_geocoding ON Addresses(geocoding_id)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_master_id ON Addresses(master_id)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_canonical ON Addresses(canonical_address)",
    "CREATE INDEX IF NOT EXISTS idx_dedup_canon_groups ON Addresses (canonical_address, address_id)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_colocator ON Addresses (colocator)",
    "CREATE INDEX IF NOT EXISTS idx_addresses_loose_colocator ON Addresses(loose_colocator)",
)
# Covering index OOMs on 16GB; geocoding already did once — retry with spill, skip if still OOM.
PPP_OPTIONAL_INDEXES = frozenset(
    {
        "idx_addresses_canonical_covering",
        "idx_addresses_geocoding",
    }
)
PPP_LOADED_BORROWER_MIN = 11_000_000

COPY_SELECT = """
SELECT
    uuidv7(),
    CAST(LoanNumber AS VARCHAR),
    COALESCE(
        TRY_CAST(DateApproved AS DATE),
        CAST(TRY_STRPTIME(CAST(DateApproved AS VARCHAR), '%m/%d/%Y') AS DATE)
    ),
    CAST(ProcessingMethod AS VARCHAR),
    CAST(BorrowerName AS VARCHAR),
    CAST(BorrowerAddress AS VARCHAR),
    CAST(BorrowerCity AS VARCHAR),
    CAST(BorrowerState AS VARCHAR),
    CAST(BorrowerZip AS VARCHAR),
    CAST(LoanStatus AS VARCHAR),
    TRY_CAST(CurrentApprovalAmount AS DOUBLE),
    TRY_CAST(JobsReported AS INTEGER),
    CAST(NAICSCode AS VARCHAR),
    CAST(BusinessType AS VARCHAR),
    UPPER(TRIM(CAST(NonProfit AS VARCHAR))) IN ('Y', 'YES'),
    TRY_CAST(ForgivenessAmount AS DOUBLE),
    COALESCE(
        TRY_CAST(ForgivenessDate AS DATE),
        CAST(TRY_STRPTIME(CAST(ForgivenessDate AS VARCHAR), '%m/%d/%Y') AS DATE)
    ),
    CAST(OriginatingLender AS VARCHAR),
    CAST(ServicingLenderName AS VARCHAR),
    CAST(RuralUrbanIndicator AS VARCHAR)
FROM read_csv(?, header=true, ignore_errors=true, parallel=true)
"""


class PppProcessor:
    def __init__(
        self,
        db_ops: DatabaseOperations,
        data_dir: str | Path,
        max_files: Optional[int] = None,
    ):
        self.db_ops = db_ops
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files

    def run(self) -> Dict[str, int]:
        log_info("=== Starting PPP step (FOIA CSV → ppp_loans + Addresses) ===")
        stats = {"downloads": 0, "files": 0, "loans": 0, "addresses": 0, "nonprofits": 0}
        urls = self._discover_csv_urls()
        if self.max_files:
            urls = urls[: self.max_files]
        paths: List[Path] = []
        for url in urls:
            dest = self.data_dir / Path(url).name
            try:
                if ensure_download(url, dest, timeout=0):
                    stats["downloads"] += 1
            except RuntimeError as exc:
                log_warning(f"PPP download failed {url}: {exc}")
                if not dest.exists() or dest.stat().st_size == 0:
                    continue
            if dest.exists() and dest.stat().st_size > 0:
                paths.append(dest)
        if not paths:
            log_warning("No PPP CSVs available — skipping ingest")
            return stats
        stats["files"] = len(paths)

        marker = self.data_dir / ".ppp_foia_ingest.json"
        if self._ingest_current(paths, marker):
            return self._existing_counts(stats)

        if self._loans_loaded():
            log_info("PPP loans already loaded — skipping loan COPY")
            stats.update(self._existing_loan_counts())
        else:
            stats.update(self._copy_loans(paths))
        stats.update(self._promote_addresses())
        self._save_ingest_marker(marker, paths)
        log_info(f"=== PPP complete: {stats} ===")
        return stats

    def _discover_csv_urls(self) -> List[str]:
        html = ""
        try:
            with urllib.request.urlopen(DATASET_PAGE, timeout=60) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except OSError as exc:
            log_warning(f"PPP dataset page unreachable: {exc}")
        found = sorted(set(CSV_HREF_RE.findall(html)))
        csvs = [u for u in found if "public_" in u.lower() and u.lower().endswith(".csv")]
        if csvs:
            log_info(f"PPP FOIA: {len(csvs)} CSVs from dataset page")
            return csvs
        log_warning("PPP FOIA: using fallback CSV names (240930 vintage)")
        return [DISTRIBUTION_PREFIX + name for name in FALLBACK_CSV_NAMES]

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
                "SELECT COUNT(*) FROM ppp_loans"
            ).fetchone()[0]
        except Exception:
            return False
        if int(count) <= 0:
            return False
        if not self._address_indexes_complete():
            return False
        log_info(f"Skipping PPP ingest — source unchanged ({int(count):,} loans)")
        return True

    def _existing_counts(self, stats: Dict[str, int]) -> Dict[str, int]:
        stats["loans"] = int(
            self.db_ops.execute_query("SELECT COUNT(*) FROM ppp_loans").fetchone()[0]
        )
        stats["addresses"] = int(
            self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Addresses WHERE address_type = 'ppp_borrower'"
            ).fetchone()[0]
        )
        try:
            stats["nonprofits"] = int(
                self.db_ops.execute_query(
                    "SELECT COUNT(*) FROM ppp_loans WHERE nonprofit"
                ).fetchone()[0]
            )
        except Exception:
            stats["nonprofits"] = 0
        return stats

    def _loans_loaded(self) -> bool:
        try:
            count = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM ppp_loans"
            ).fetchone()[0]
        except Exception:
            return False
        return int(count) > 0

    def _existing_loan_counts(self) -> Dict[str, int]:
        loans = int(
            self.db_ops.execute_query("SELECT COUNT(*) FROM ppp_loans").fetchone()[0]
        )
        try:
            nps = int(
                self.db_ops.execute_query(
                    "SELECT COUNT(*) FROM ppp_loans WHERE nonprofit"
                ).fetchone()[0]
            )
        except Exception:
            nps = 0
        return {"loans": loans, "nonprofits": nps}

    def _save_ingest_marker(self, marker: Path, paths: List[Path]) -> None:
        payload = {
            "ingest_version": INGEST_VERSION,
            "files": [
                {"path": str(p.resolve()), "size": p.stat().st_size} for p in paths
            ],
        }
        marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _copy_loans(self, paths: List[Path]) -> Dict[str, int]:
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute("DROP TABLE IF EXISTS ppp_loans")
            conn.execute(PPP_TABLE_SQL)
            for path in paths:
                log_info(f"PPP COPY {path.name} ({path.stat().st_size:,} bytes)")
                t0 = time.perf_counter()
                before = conn.execute("SELECT COUNT(*) FROM ppp_loans").fetchone()[0]
                conn.execute(
                    f"INSERT INTO ppp_loans ({PPP_INSERT_COLS}) {COPY_SELECT}",
                    [str(path)],
                )
                after = conn.execute("SELECT COUNT(*) FROM ppp_loans").fetchone()[0]
                log_info(
                    f"  +{after - before:,} loans (table {after:,}) in "
                    f"{time.perf_counter() - t0:.1f}s"
                )
            log_info("PPP building loan_number unique index (after heap COPY)")
            t0 = time.perf_counter()
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX idx_ppp_loans_number ON ppp_loans(loan_number)"
                )
            except Exception as exc:
                log_warning(f"PPP unique loan_number failed ({exc}); non-unique index")
                conn.execute(
                    "CREATE INDEX idx_ppp_loans_number ON ppp_loans(loan_number)"
                )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ppp_loans_id ON ppp_loans(id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ppp_loans_name ON ppp_loans(borrower_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ppp_loans_nonprofit ON ppp_loans(nonprofit)"
            )
            log_info(f"  indexes in {time.perf_counter() - t0:.1f}s")
        return self._existing_loan_counts()

    def _loan_src_csv(self) -> Path:
        return self.data_dir / LOAN_SRC_CSV

    def _address_csv(self) -> Path:
        return self.data_dir / ADDRESS_CSV

    def _promote_addresses(self) -> Dict[str, int]:
        n = self._ppp_borrower_count()
        dest = self._address_csv()
        if n >= PPP_LOADED_BORROWER_MIN and dest.exists():
            log_info(
                f"  {n:,} ppp_borrower already in Addresses — skip CSV/COPY, "
                "rebuild missing indexes"
            )
            self._rebuild_address_indexes()
            return {"addresses": n}
        log_info("PPP Addresses: Address.py → CSV → COPY")
        src = self._dump_loan_src_csv()
        dest = self._write_address_csv(src)
        stats = self._copy_address_csv(dest)
        self._rebuild_address_indexes()
        return stats

    def _dump_loan_src_csv(self) -> Path:
        dest = self._loan_src_csv()
        if dest.exists() and dest.stat().st_size > 0:
            log_info(f"  reusing loan dump {dest.name} ({dest.stat().st_size:,} bytes)")
            return dest
        log_info(f"  dumping ppp_loans address columns → {dest}")
        t0 = time.perf_counter()
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute("SET preserve_insertion_order = false")
            conn.execute(
                f"""
                COPY (
                    SELECT
                        CAST(id AS VARCHAR) AS id,
                        loan_number,
                        borrower_name,
                        borrower_address,
                        borrower_city,
                        borrower_state,
                        borrower_zip
                    FROM ppp_loans
                    ORDER BY id
                ) TO '{dest}' (HEADER, DELIMITER ',', QUOTE '"')
                """
            )
        log_info(
            f"  dumped {dest.name} ({dest.stat().st_size:,} bytes) "
            f"in {time.perf_counter() - t0:.1f}s"
        )
        return dest

    def _write_address_csv(self, src: Path) -> Path:
        dest = self._address_csv()
        last_owner = _last_csv_owner_id(dest)
        mode = "a" if last_owner else "w"
        if last_owner:
            log_info(f"  resuming address CSV after owner_id {last_owner}")
        log_info(f"  Address.py writing {dest.name}")
        t0 = time.perf_counter()
        written = 0
        seen = 0
        skipping = bool(last_owner)
        with src.open("r", encoding="utf-8", newline="") as inf, dest.open(
            mode, encoding="utf-8", newline=""
        ) as outf:
            reader = csv.DictReader(inf)
            writer = csv.DictWriter(
                outf,
                fieldnames=list(ADDRESS_CSV_COLS),
                extrasaction="ignore",
                lineterminator="\n",
            )
            if mode == "w":
                writer.writeheader()
            for row in reader:
                seen += 1
                src_id = row.get("id") or ""
                if skipping:
                    if src_id == last_owner:
                        skipping = False
                    continue
                loan = PppLoan(
                    id=src_id,
                    loan_number=row.get("loan_number") or "",
                    borrower_name=row.get("borrower_name"),
                    borrower_address=row.get("borrower_address"),
                    borrower_city=row.get("borrower_city"),
                    borrower_state=row.get("borrower_state"),
                    borrower_zip=row.get("borrower_zip"),
                )
                address = loan.build_address()
                if address is None:
                    continue
                writer.writerow(_address_csv_row(address))
                written += 1
                if written % PROGRESS_EVERY == 0:
                    log_info(
                        f"  address CSV +{written:,} "
                        f"({seen:,} loans, {time.perf_counter() - t0:.0f}s)"
                    )
        log_info(
            f"  address CSV +{written:,} new rows in {time.perf_counter() - t0:.1f}s "
            f"({dest.stat().st_size:,} bytes)"
        )
        return dest

    def _copy_address_csv(self, dest: Path) -> Dict[str, int]:
        log_info(f"  COPY {dest.name} into Addresses (indexes dropped)")
        t0 = time.perf_counter()
        cols = ", ".join(ADDRESS_CSV_COLS)
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute("SET preserve_insertion_order = false")
            conn.execute("SET threads = 1")
            for name in PPP_DROP_ADDRESS_INDEXES:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            conn.execute("DELETE FROM Addresses WHERE address_type = 'ppp_borrower'")
            conn.execute(
                f"""
                INSERT INTO Addresses ({cols})
                SELECT
                    CAST(address_id AS UUID),
                    NULLIF(ein, ''),
                    CAST(owner_id AS UUID),
                    CAST(NULLIF(master_id, '') AS UUID),
                    name,
                    NULLIF(address_line1, ''),
                    NULLIF(address_line2, ''),
                    NULLIF(city, ''),
                    NULLIF(state, ''),
                    NULLIF(zip_code, ''),
                    NULLIF(zip4, ''),
                    NULLIF(po_box, ''),
                    NULLIF(canonical_address, ''),
                    address_type,
                    CAST(NULLIF(geocoding_id, '') AS UUID),
                    TRY_CAST(NULLIF(latitude, '') AS DOUBLE),
                    TRY_CAST(NULLIF(longitude, '') AS DOUBLE),
                    NULLIF(colocator, '')
                FROM read_csv(
                    ?,
                    header=true,
                    delim=',',
                    quote='"',
                    escape='"',
                    all_varchar=true,
                    ignore_errors=false,
                    parallel=true
                )
                """,
                [str(dest)],
            )
            n = int(
                conn.execute(
                    "SELECT COUNT(*) FROM Addresses WHERE address_type = 'ppp_borrower'"
                ).fetchone()[0]
            )
            log_info(
                f"  COPY loaded {n:,} ppp_borrower in {time.perf_counter() - t0:.1f}s"
            )
        return {"addresses": n}

    def _ppp_borrower_count(self) -> int:
        try:
            row = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Addresses WHERE address_type = 'ppp_borrower'"
            ).fetchone()
            return int(row[0] or 0)
        except Exception as exc:
            log_warning(f"ppp_borrower count failed: {exc}")
            return 0

    def _address_index_names(self, conn) -> Set[str]:
        rows = conn.execute(
            """
            SELECT index_name FROM duckdb_indexes()
            WHERE table_name = 'Addresses'
            """
        ).fetchall()
        return {r[0] for r in rows if r and r[0]}

    def _address_indexes_complete(self) -> bool:
        required = []
        for ddl in PPP_REBUILD_ADDRESS_INDEX_DDL:
            name = _index_name_from_ddl(ddl)
            if name and name not in PPP_OPTIONAL_INDEXES:
                required.append(name)
        try:
            with self.db_ops.acquire_write_conn() as conn:
                have = self._address_index_names(conn)
        except Exception:
            return False
        return all(name in have for name in required)

    def _tune_index_conn(self, conn) -> None:
        conn.execute("SET preserve_insertion_order = false")
        conn.execute("SET threads = 1")
        tmp = Path(
            os.environ.get(
                "DUCKDB_TEMP_DIRECTORY",
                str(self.data_dir.parent.parent / "duckdb_tmp"),
            )
        )
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            conn.execute(f"SET temp_directory = '{tmp}'")
        except Exception as exc:
            log_info(f"  temp_directory unchanged ({exc})")
        try:
            conn.execute("SET max_temp_directory_size = '80GB'")
        except Exception:
            pass

    def _rebuild_address_indexes(self) -> None:
        log_info("PPP rebuilding Addresses secondary indexes (one at a time, threads=1)")
        with self.db_ops.acquire_write_conn() as conn:
            self._tune_index_conn(conn)
        for ddl in PPP_REBUILD_ADDRESS_INDEX_DDL:
            name = _index_name_from_ddl(ddl) or ddl
            t0 = time.perf_counter()
            try:
                with self.db_ops.acquire_write_conn() as conn:
                    conn.execute("SET preserve_insertion_order = false")
                    conn.execute("SET threads = 1")
                    have = self._address_index_names(conn)
                    if name in have:
                        log_info(f"    skip {name} (already present)")
                        continue
                    log_info(f"    building {name}")
                    conn.execute(ddl)
                log_info(f"    {name} in {time.perf_counter() - t0:.1f}s")
            except Exception as exc:
                err = str(exc).lower()
                oom = (
                    "out of memory" in err
                    or "failed to pin" in err
                    or "could not allocate" in err
                )
                if oom:
                    log_warning(
                        f"    {name} OOM after {time.perf_counter() - t0:.1f}s — "
                        f"skip (bigger machine later): {exc}"
                    )
                    continue
                raise


def _address_csv_row(address: Address) -> Dict[str, str]:
    data = address.to_dict()
    row: Dict[str, str] = {}
    for col in ADDRESS_CSV_COLS:
        value = data.get(col)
        row[col] = "" if value is None else str(value)
    return row


def _index_name_from_ddl(ddl: str) -> Optional[str]:
    parts = ddl.split()
    for i, part in enumerate(parts):
        if part.upper() == "EXISTS" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def _last_csv_owner_id(path: Path) -> Optional[str]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    last = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            last = row.get("owner_id") or last
    return last
