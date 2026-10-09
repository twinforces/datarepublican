#!/usr/bin/env python3
"""
sam_processor.py — SAM.gov Entity Registration Public V2 ingest.

Source: monthly UTF-8 extract (UEI + CAGE + legal name + phy/mail street).
No EIN/TIN in the public file. DuckDB COPY for sam_entities. Addresses:
SamEntity.build_addresses() writes a CSV, then COPY into Addresses.
Ingest only — does not run address-dedup.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import quote

from database_operations import DatabaseOperations
from download_utils import curl_download, ensure_download
from logging_utils import log_info, log_warning
from models import Address, SamEntity

LISTFILES_URL = (
    "https://sam.gov/api/prod/fileextractservices/v1/api/listfiles"
    "?privacy=Public&domain=Entity%20Registration/Public%20V2"
)
DOWNLOAD_TMPL = (
    "https://sam.gov/api/prod/fileextractservices/v1/api/download/"
    "Entity%20Registration/Public%20V2/{filename}?privacy=Public"
)
FALLBACK_ZIP = "SAM_PUBLIC_UTF-8_MONTHLY_V2_20261005.ZIP"
MONTHLY_UTF8_RE = re.compile(
    r"SAM_PUBLIC_UTF-8_MONTHLY_V2_(\d{8})\.ZIP$", re.I
)
INGEST_VERSION = 1
USER_AGENT = "990tools-sam-ingest/1 (+https://datarepublican.com)"

# 0-based indexes from GSA Public V2 layout (PDF 1-based in comments).
COL_UEI = 0  # 1 ueiSAM
COL_CAGE = 3  # 4
COL_EXTRACT = 5  # 6 A/E
COL_PURPOSE = 6  # 7 Z1–Z5
COL_REG_DATE = 7  # 8
COL_EXP_DATE = 8  # 9
COL_LAST_UPDATE = 9  # 10
COL_ACTIVATION = 10  # 11
COL_LEGAL = 11  # 12
COL_DBA = 12  # 13
COL_PHY_L1 = 15  # 16
COL_PHY_L2 = 16  # 17
COL_PHY_CITY = 17  # 18
COL_PHY_STATE = 18  # 19
COL_PHY_ZIP = 19  # 20
COL_PHY_ZIP4 = 20  # 21
COL_PHY_CC = 21  # 22
COL_URL = 26  # 27
COL_BUS_TYPES = 31  # 32
COL_PRIMARY_NAICS = 32  # 33
COL_MAIL_L1 = 39  # 40
COL_MAIL_L2 = 40  # 41
COL_MAIL_CITY = 41  # 42
COL_MAIL_ZIP = 42  # 43
COL_MAIL_ZIP4 = 43  # 44
COL_MAIL_CC = 44  # 45
COL_MAIL_STATE = 45  # 46  (state is last, unlike physical)
MIN_DAT_FIELDS = 46

ENTITY_SRC_CSV = "sam_entity_addr_src.csv"
ADDRESS_CSV = "sam_entity_addresses.csv"
SLIM_CSV = "sam_entities_slim.csv"
PROGRESS_EVERY = 50_000
SAM_LOADED_ENTITY_MIN = 500_000
SAM_LOADED_ADDR_MIN = 400_000

SAM_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sam_entities (
    id UUID,
    uei VARCHAR NOT NULL,
    cage VARCHAR,
    extract_code VARCHAR,
    purpose VARCHAR,
    registration_date DATE,
    expiration_date DATE,
    last_update_date DATE,
    activation_date DATE,
    legal_name VARCHAR,
    dba_name VARCHAR,
    phy_line1 VARCHAR,
    phy_line2 VARCHAR,
    phy_city VARCHAR,
    phy_state VARCHAR,
    phy_zip VARCHAR,
    phy_zip4 VARCHAR,
    phy_country VARCHAR,
    mail_line1 VARCHAR,
    mail_line2 VARCHAR,
    mail_city VARCHAR,
    mail_state VARCHAR,
    mail_zip VARCHAR,
    mail_zip4 VARCHAR,
    mail_country VARCHAR,
    entity_url VARCHAR,
    business_types VARCHAR,
    primary_naics VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

SAM_INSERT_COLS = """
id, uei, cage, extract_code, purpose,
registration_date, expiration_date, last_update_date, activation_date,
legal_name, dba_name,
phy_line1, phy_line2, phy_city, phy_state, phy_zip, phy_zip4, phy_country,
mail_line1, mail_line2, mail_city, mail_state, mail_zip, mail_zip4, mail_country,
entity_url, business_types, primary_naics
"""

SLIM_CSV_COLS = (
    "uei",
    "cage",
    "extract_code",
    "purpose",
    "registration_date",
    "expiration_date",
    "last_update_date",
    "activation_date",
    "legal_name",
    "dba_name",
    "phy_line1",
    "phy_line2",
    "phy_city",
    "phy_state",
    "phy_zip",
    "phy_zip4",
    "phy_country",
    "mail_line1",
    "mail_line2",
    "mail_city",
    "mail_state",
    "mail_zip",
    "mail_zip4",
    "mail_country",
    "entity_url",
    "business_types",
    "primary_naics",
)

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

SAM_DROP_ADDRESS_INDEXES = (
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
SAM_REBUILD_ADDRESS_INDEX_DDL = (
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
SAM_OPTIONAL_INDEXES = frozenset(
    {
        "idx_addresses_canonical_covering",
        "idx_addresses_geocoding",
    }
)

COPY_SELECT = """
SELECT
    uuidv7(),
    CAST(uei AS VARCHAR),
    CAST(cage AS VARCHAR),
    CAST(extract_code AS VARCHAR),
    CAST(purpose AS VARCHAR),
    COALESCE(
        TRY_CAST(registration_date AS DATE),
        CAST(TRY_STRPTIME(CAST(registration_date AS VARCHAR), '%Y%m%d') AS DATE)
    ),
    COALESCE(
        TRY_CAST(expiration_date AS DATE),
        CAST(TRY_STRPTIME(CAST(expiration_date AS VARCHAR), '%Y%m%d') AS DATE)
    ),
    COALESCE(
        TRY_CAST(last_update_date AS DATE),
        CAST(TRY_STRPTIME(CAST(last_update_date AS VARCHAR), '%Y%m%d') AS DATE)
    ),
    COALESCE(
        TRY_CAST(activation_date AS DATE),
        CAST(TRY_STRPTIME(CAST(activation_date AS VARCHAR), '%Y%m%d') AS DATE)
    ),
    CAST(legal_name AS VARCHAR),
    CAST(dba_name AS VARCHAR),
    CAST(phy_line1 AS VARCHAR),
    CAST(phy_line2 AS VARCHAR),
    CAST(phy_city AS VARCHAR),
    CAST(phy_state AS VARCHAR),
    CAST(phy_zip AS VARCHAR),
    CAST(phy_zip4 AS VARCHAR),
    CAST(phy_country AS VARCHAR),
    CAST(mail_line1 AS VARCHAR),
    CAST(mail_line2 AS VARCHAR),
    CAST(mail_city AS VARCHAR),
    CAST(mail_state AS VARCHAR),
    CAST(mail_zip AS VARCHAR),
    CAST(mail_zip4 AS VARCHAR),
    CAST(mail_country AS VARCHAR),
    CAST(entity_url AS VARCHAR),
    CAST(business_types AS VARCHAR),
    CAST(primary_naics AS VARCHAR)
FROM read_csv(?, header=true, ignore_errors=true, parallel=true)
"""


def _cell(fields: List[str], index: int) -> str:
    if index >= len(fields):
        return ""
    return (fields[index] or "").strip()


def is_header_or_trailer(line: str) -> bool:
    text = (line or "").lstrip()
    if not text:
        return True
    upper = text.upper()
    return upper.startswith("BOF") or upper.startswith("EOF")


def split_dat_line(line: str) -> List[str]:
    return line.rstrip("\r\n").split("|")


def parse_entity_row(fields: List[str]) -> Optional[dict]:
    if len(fields) < MIN_DAT_FIELDS:
        return None
    uei = _cell(fields, COL_UEI)
    if not uei or uei.upper().startswith("BOF") or uei.upper().startswith("EOF"):
        return None
    if uei.lower() == "ueisam":
        return None
    return {
        "uei": uei,
        "cage": _cell(fields, COL_CAGE),
        "extract_code": _cell(fields, COL_EXTRACT),
        "purpose": _cell(fields, COL_PURPOSE),
        "registration_date": _cell(fields, COL_REG_DATE),
        "expiration_date": _cell(fields, COL_EXP_DATE),
        "last_update_date": _cell(fields, COL_LAST_UPDATE),
        "activation_date": _cell(fields, COL_ACTIVATION),
        "legal_name": _cell(fields, COL_LEGAL),
        "dba_name": _cell(fields, COL_DBA),
        "phy_line1": _cell(fields, COL_PHY_L1),
        "phy_line2": _cell(fields, COL_PHY_L2),
        "phy_city": _cell(fields, COL_PHY_CITY),
        "phy_state": _cell(fields, COL_PHY_STATE),
        "phy_zip": _cell(fields, COL_PHY_ZIP),
        "phy_zip4": _cell(fields, COL_PHY_ZIP4),
        "phy_country": _cell(fields, COL_PHY_CC),
        "mail_line1": _cell(fields, COL_MAIL_L1),
        "mail_line2": _cell(fields, COL_MAIL_L2),
        "mail_city": _cell(fields, COL_MAIL_CITY),
        "mail_state": _cell(fields, COL_MAIL_STATE),
        "mail_zip": _cell(fields, COL_MAIL_ZIP),
        "mail_zip4": _cell(fields, COL_MAIL_ZIP4),
        "mail_country": _cell(fields, COL_MAIL_CC),
        "entity_url": _cell(fields, COL_URL),
        "business_types": _cell(fields, COL_BUS_TYPES),
        "primary_naics": _cell(fields, COL_PRIMARY_NAICS),
    }


def iter_dat_entities(path: Path, max_rows: Optional[int] = None) -> Iterable[dict]:
    yielded = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for line in handle:
            if is_header_or_trailer(line):
                continue
            row = parse_entity_row(split_dat_line(line))
            if row is None:
                continue
            yield row
            yielded += 1
            if max_rows is not None and yielded >= max_rows:
                return


def dat_to_slim_csv(
    dat_path: Path, dest: Path, max_rows: Optional[int] = None
) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    t0 = time.perf_counter()
    with dest.open("w", encoding="utf-8", newline="") as outf:
        writer = csv.DictWriter(
            outf,
            fieldnames=list(SLIM_CSV_COLS),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in iter_dat_entities(dat_path, max_rows=max_rows):
            writer.writerow(row)
            written += 1
            if written % PROGRESS_EVERY == 0:
                log_info(
                    f"  slim CSV +{written:,} ({time.perf_counter() - t0:.0f}s)"
                )
    log_info(
        f"  slim CSV {written:,} rows in {time.perf_counter() - t0:.1f}s "
        f"({dest.stat().st_size:,} bytes)"
    )
    return written


def _walk_zip_names(obj, out: List[str]) -> None:
    if isinstance(obj, str):
        name = obj.split("/")[-1].split("\\")[-1]
        if name.upper().endswith(".ZIP"):
            out.append(name)
        return
    if isinstance(obj, dict):
        for value in obj.values():
            _walk_zip_names(value, out)
        return
    if isinstance(obj, list):
        for value in obj:
            _walk_zip_names(value, out)


def pick_latest_monthly_utf8(names: Iterable[str]) -> Optional[str]:
    best = None
    best_date = ""
    for name in names:
        match = MONTHLY_UTF8_RE.search(name.strip())
        if not match:
            continue
        stamp = match.group(1)
        if stamp >= best_date:
            best_date = stamp
            best = match.group(0)
    return best


def find_dat(root: Path) -> Optional[Path]:
    if not root.exists():
        return None
    dats = sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".dat"
    )
    return dats[0] if dats else None


def unzip_until_dat(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    found = find_dat(dest)
    if found:
        return found
    _extract_zip(zip_path, dest)
    found = find_dat(dest)
    if found:
        return found
    inners = sorted(
        p for p in dest.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"
    )
    for inner in inners:
        _extract_zip(inner, dest / inner.stem)
        found = find_dat(dest)
        if found:
            return found
    raise RuntimeError(f"No .dat in {zip_path.name} after unzip into {dest}")


def _extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    log_info(f"  unzip {zip_path.name} → {dest}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)


class SamProcessor:
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
        log_info("=== Starting SAM step (Public V2 → sam_entities + Addresses) ===")
        stats = {"downloads": 0, "entities": 0, "addresses": 0, "phy": 0, "mail": 0}
        zip_path = self._download_monthly()
        if zip_path is None:
            log_warning("No SAM monthly ZIP available — skipping ingest")
            return stats
        if zip_path.exists() and zip_path.stat().st_size > 0:
            stats["downloads"] = 1

        marker = self.data_dir / ".sam_entity_ingest.json"
        if self._ingest_current(zip_path, marker):
            return self._existing_counts(stats)

        dat_path = unzip_until_dat(zip_path, self.data_dir / "extracted")
        slim = self._slim_csv()
        dat_to_slim_csv(dat_path, slim, max_rows=self.max_files)

        if self._entities_loaded():
            log_info("SAM entities already loaded — skipping entity COPY")
            stats.update(self._existing_entity_counts())
        else:
            stats.update(self._copy_entities(slim))
        stats.update(self._promote_addresses())
        self._save_ingest_marker(marker, zip_path)
        log_info(f"=== SAM complete: {stats} ===")
        return stats

    def _download_monthly(self) -> Optional[Path]:
        name = self._discover_zip_name()
        url = DOWNLOAD_TMPL.format(filename=quote(name))
        dest = self.data_dir / name
        try:
            if ensure_download(url, dest, timeout=0):
                log_info(f"SAM downloaded {dest.name} ({dest.stat().st_size:,} bytes)")
        except RuntimeError as exc:
            log_warning(f"SAM HEAD/ensure failed ({exc}); GET {name}")
            try:
                if not dest.exists() or dest.stat().st_size == 0:
                    curl_download(url, dest, timeout=0)
            except Exception as exc2:
                log_warning(f"SAM download failed {url}: {exc2}")
                if not dest.exists() or dest.stat().st_size == 0:
                    return None
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return None

    def _discover_zip_name(self) -> str:
        payload = None
        try:
            req = urllib.request.Request(
                LISTFILES_URL, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log_warning(f"SAM listfiles unreachable: {exc}")
        names: List[str] = []
        if payload is not None:
            _walk_zip_names(payload, names)
        picked = pick_latest_monthly_utf8(names)
        if picked:
            log_info(f"SAM monthly UTF-8 V2: {picked}")
            return picked
        log_warning(f"SAM listfiles empty — fallback {FALLBACK_ZIP}")
        return FALLBACK_ZIP

    def _ingest_current(self, zip_path: Path, marker: Path) -> bool:
        if not marker.exists():
            return False
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if meta.get("ingest_version") != INGEST_VERSION:
            return False
        if meta.get("max_files") != self.max_files:
            return False
        if meta.get("path") != str(zip_path.resolve()):
            return False
        if meta.get("size") != zip_path.stat().st_size:
            return False
        try:
            count = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM sam_entities"
            ).fetchone()[0]
        except Exception:
            return False
        if int(count) <= 0:
            return False
        if not self.max_files and int(count) < SAM_LOADED_ENTITY_MIN:
            return False
        if not self._address_indexes_complete():
            return False
        log_info(f"Skipping SAM ingest — source unchanged ({int(count):,} entities)")
        return True

    def _existing_counts(self, stats: Dict[str, int]) -> Dict[str, int]:
        stats.update(self._existing_entity_counts())
        stats.update(self._existing_address_counts())
        return stats

    def _entities_loaded(self) -> bool:
        try:
            count = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM sam_entities"
            ).fetchone()[0]
        except Exception:
            return False
        if self.max_files:
            return int(count) >= int(self.max_files)
        return int(count) >= SAM_LOADED_ENTITY_MIN

    def _existing_entity_counts(self) -> Dict[str, int]:
        try:
            n = int(
                self.db_ops.execute_query(
                    "SELECT COUNT(*) FROM sam_entities"
                ).fetchone()[0]
            )
        except Exception:
            n = 0
        return {"entities": n}

    def _existing_address_counts(self) -> Dict[str, int]:
        phy = self._addr_count("sam_entity_phy")
        mail = self._addr_count("sam_entity_mail")
        return {"addresses": phy + mail, "phy": phy, "mail": mail}

    def _save_ingest_marker(self, marker: Path, zip_path: Path) -> None:
        payload = {
            "ingest_version": INGEST_VERSION,
            "path": str(zip_path.resolve()),
            "size": zip_path.stat().st_size,
            "max_files": self.max_files,
        }
        marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _slim_csv(self) -> Path:
        return self.data_dir / SLIM_CSV

    def _entity_src_csv(self) -> Path:
        return self.data_dir / ENTITY_SRC_CSV

    def _address_csv(self) -> Path:
        return self.data_dir / ADDRESS_CSV

    def _copy_entities(self, slim: Path) -> Dict[str, int]:
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute("DROP TABLE IF EXISTS sam_entities")
            conn.execute(SAM_TABLE_SQL)
            log_info(f"SAM COPY {slim.name} ({slim.stat().st_size:,} bytes)")
            t0 = time.perf_counter()
            conn.execute(
                f"INSERT INTO sam_entities ({SAM_INSERT_COLS}) {COPY_SELECT}",
                [str(slim)],
            )
            n = int(conn.execute("SELECT COUNT(*) FROM sam_entities").fetchone()[0])
            log_info(f"  {n:,} entities in {time.perf_counter() - t0:.1f}s")
            log_info("SAM building uei unique index (after heap COPY)")
            t0 = time.perf_counter()
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX idx_sam_entities_uei ON sam_entities(uei)"
                )
            except Exception as exc:
                log_warning(f"SAM unique uei failed ({exc}); non-unique index")
                conn.execute(
                    "CREATE INDEX idx_sam_entities_uei ON sam_entities(uei)"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sam_entities_id ON sam_entities(id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sam_entities_name "
                "ON sam_entities(legal_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sam_entities_cage "
                "ON sam_entities(cage)"
            )
            log_info(f"  indexes in {time.perf_counter() - t0:.1f}s")
        return self._existing_entity_counts()

    def _promote_addresses(self) -> Dict[str, int]:
        counts = self._existing_address_counts()
        dest = self._address_csv()
        if (
            not self.max_files
            and counts["addresses"] >= SAM_LOADED_ADDR_MIN
            and dest.exists()
        ):
            log_info(
                f"  {counts['addresses']:,} SAM addresses already in Addresses — "
                "skip CSV/COPY, rebuild missing indexes"
            )
            self._rebuild_address_indexes()
            return counts
        log_info("SAM Addresses: Address.py → CSV → COPY")
        src = self._dump_entity_src_csv()
        dest = self._write_address_csv(src)
        stats = self._copy_address_csv(dest)
        self._rebuild_address_indexes()
        return stats

    def _dump_entity_src_csv(self) -> Path:
        dest = self._entity_src_csv()
        if (
            dest.exists()
            and dest.stat().st_size > 0
            and not self.max_files
            and self._existing_entity_counts()["entities"] >= SAM_LOADED_ENTITY_MIN
        ):
            log_info(f"  reusing entity dump {dest.name} ({dest.stat().st_size:,} bytes)")
            return dest
        log_info(f"  dumping sam_entities address columns → {dest}")
        t0 = time.perf_counter()
        with self.db_ops.acquire_write_conn() as conn:
            conn.execute("SET preserve_insertion_order = false")
            conn.execute(
                f"""
                COPY (
                    SELECT
                        CAST(id AS VARCHAR) AS id,
                        uei,
                        legal_name,
                        dba_name,
                        phy_line1, phy_line2, phy_city, phy_state,
                        phy_zip, phy_zip4, phy_country,
                        mail_line1, mail_line2, mail_city, mail_state,
                        mail_zip, mail_zip4, mail_country
                    FROM sam_entities
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
                        continue
                    skipping = False
                entity = SamEntity(
                    id=src_id,
                    uei=row.get("uei") or "",
                    legal_name=row.get("legal_name"),
                    dba_name=row.get("dba_name"),
                    phy_line1=row.get("phy_line1"),
                    phy_line2=row.get("phy_line2"),
                    phy_city=row.get("phy_city"),
                    phy_state=row.get("phy_state"),
                    phy_zip=row.get("phy_zip"),
                    phy_zip4=row.get("phy_zip4"),
                    phy_country=row.get("phy_country"),
                    mail_line1=row.get("mail_line1"),
                    mail_line2=row.get("mail_line2"),
                    mail_city=row.get("mail_city"),
                    mail_state=row.get("mail_state"),
                    mail_zip=row.get("mail_zip"),
                    mail_zip4=row.get("mail_zip4"),
                    mail_country=row.get("mail_country"),
                )
                for address in entity.build_addresses():
                    writer.writerow(_address_csv_row(address))
                    written += 1
                if written and written % PROGRESS_EVERY == 0:
                    log_info(
                        f"  address CSV +{written:,} "
                        f"({seen:,} entities, {time.perf_counter() - t0:.0f}s)"
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
            for name in SAM_DROP_ADDRESS_INDEXES:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            conn.execute(
                "DELETE FROM Addresses WHERE address_type IN "
                "('sam_entity_phy', 'sam_entity_mail')"
            )
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
        counts = self._existing_address_counts()
        log_info(
            f"  COPY loaded {counts['addresses']:,} SAM addresses "
            f"(phy {counts['phy']:,}, mail {counts['mail']:,}) "
            f"in {time.perf_counter() - t0:.1f}s"
        )
        return counts

    def _addr_count(self, address_type: str) -> int:
        if address_type not in ("sam_entity_phy", "sam_entity_mail"):
            return 0
        try:
            row = self.db_ops.execute_query(
                "SELECT COUNT(*) FROM Addresses WHERE address_type = "
                f"'{address_type}'"
            ).fetchone()
            return int(row[0] or 0)
        except Exception as exc:
            log_warning(f"{address_type} count failed: {exc}")
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
        for ddl in SAM_REBUILD_ADDRESS_INDEX_DDL:
            name = _index_name_from_ddl(ddl)
            if name and name not in SAM_OPTIONAL_INDEXES:
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
        log_info("SAM rebuilding Addresses secondary indexes (one at a time, threads=1)")
        with self.db_ops.acquire_write_conn() as conn:
            self._tune_index_conn(conn)
        for ddl in SAM_REBUILD_ADDRESS_INDEX_DDL:
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
