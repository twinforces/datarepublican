#!/usr/bin/env python3
"""CMS CLIA labs + Physician Fee Schedule work RVU/time into DuckDB.

Tables (CREATE OR REPLACE, main irs990.duckdb):
  cms_clia          Provider of Services Clinical Laboratories (latest quarter)
  cms_pfs_rvu       PPRRVU non-QPP: work RVU + PE/MP + intra/pre/post fractions
  cms_pfs_work_time CY PFS final-rule physician work minutes (efficiency-adjusted)

No Addresses. Not on --step all. CLIA has no NPI (PRVDR_NUM is the CLIA id).
hcpcs_codes stays descriptor-only; this is the time/RVU grain for a later
reasonableness grade (Tot_Srvcs × minutes).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import duckdb

from download_utils import ensure_download
from logging_utils import log_info, log_warning

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
DEFAULT_DATA_DIR = Path("/Volumes/Insurrection/990data/cms_data/clia_pfs")
CMS_ORIGIN = "https://www.cms.gov"
DATA_JSON = "https://data.cms.gov/data.json"
CLIA_TITLE = "Provider of Services File - Clinical Laboratories"
CLIA_LANDING = (
    "https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/"
    "provider-of-services-file-clinical-laboratories"
)
DEFAULT_CLIA_CSV = (
    "https://data.cms.gov/sites/default/files/2026-10/"
    "fb46beda-9e19-4141-882e-7da5fd35b6da/Clia_Data.Q3_2026.csv"
)
DEFAULT_RVU_ZIP = f"{CMS_ORIGIN}/files/zip/rvu26d-updated-08-26-2026.zip"
DEFAULT_WORK_TIME_ZIP = (
    f"{CMS_ORIGIN}/files/zip/"
    "cy-2026-pfs-final-rule-physician-work-time-updated-01-08-2026.zip"
)
WORK_TIME_PAGE = (
    f"{CMS_ORIGIN}/medicare/payment/fee-schedules/physician/"
    "federal-regulation-notices/cms-1832-f"
)
PFS_RVU_INDEX = (
    f"{CMS_ORIGIN}/medicare/payment/fee-schedules/physician/pfs-relative-value-files"
)
RVU_SKIP = 9  # copyright preamble; header is the 10th line (0-based index 9)
RVU_MEMBER = re.compile(r"PPRRVU.*nonQPP.*\.csv$", re.I)
WORK_TIME_MEMBER = re.compile(r"Work Times\.csv$", re.I)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cms_clia_prvdr ON cms_clia(PRVDR_NUM)",
    "CREATE INDEX IF NOT EXISTS idx_cms_clia_state ON cms_clia(STATE_CD)",
    "CREATE INDEX IF NOT EXISTS idx_cms_clia_zip ON cms_clia(ZIP_CD)",
    "CREATE INDEX IF NOT EXISTS idx_cms_pfs_rvu_hcpcs ON cms_pfs_rvu(hcpcs)",
    "CREATE INDEX IF NOT EXISTS idx_cms_pfs_work_time_cpt ON cms_pfs_work_time(cpt_code)",
)


def default_data_dir() -> Path:
    return Path(os.environ.get("CMS_CLIA_PFS_DATA_DIR", str(DEFAULT_DATA_DIR)))


def http_get_text(url: str, *, timeout: int = 120) -> str:
    req = Request(url, headers={"User-Agent": "990tools-clia-pfs/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_get_json(url: str, *, timeout: int = 180) -> Any:
    req = Request(url, headers={"User-Agent": "990tools-clia-pfs/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _title_date(title: str) -> Optional[date]:
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", title or "")
    if not m:
        return None
    y, mo, d = m.group(1).split("-")
    try:
        return date(int(y), int(mo), int(d))
    except ValueError:
        return None


def pick_latest_clia_csv(datasets: Iterable[dict]) -> Optional[Tuple[str, str]]:
    """Return (downloadURL, title) for the newest Clinical Laboratories POS CSV."""
    best: Optional[Tuple[date, str, str]] = None
    for d in datasets:
        title = d.get("title") or ""
        if CLIA_TITLE.lower() not in title.lower():
            continue
        when = _title_date(title) or date.min
        for dist in d.get("distribution") or []:
            fmt = (dist.get("format") or dist.get("mediaType") or "").upper()
            url = dist.get("downloadURL") or dist.get("downloadUrl")
            if not url or "CSV" not in fmt:
                continue
            cand = (when, url, title)
            if best is None or cand[0] > best[0]:
                best = cand
    if best is None:
        return None
    return best[1], best[2]


def discover_latest_clia_csv() -> str:
    try:
        payload = http_get_json(DATA_JSON)
        datasets = payload.get("dataset", payload) if isinstance(payload, dict) else payload
        picked = pick_latest_clia_csv(datasets)
        if picked:
            url, title = picked
            log_info(f"CLIA catalog pick {title} → {url}")
            return url
        log_warning("CLIA catalog had no Clinical Laboratories CSV")
    except Exception as exc:
        log_warning(f"CLIA catalog fetch failed ({exc})")
    return DEFAULT_CLIA_CSV


def discover_latest_rvu_zip() -> str:
    try:
        html = http_get_text(PFS_RVU_INDEX)
        pages = re.findall(
            r'href="(/medicare/payment/fee-schedules/physician/pfs-relative-value-files/rvu\d{2}[a-z])"',
            html,
            flags=re.I,
        )
        for rel in pages[:8]:
            try:
                sub = http_get_text(urljoin(CMS_ORIGIN, rel))
            except Exception:
                continue
            zips = re.findall(r'href="(/files/zip/rvu[^"]+\.zip)"', sub, flags=re.I)
            if zips:
                return urljoin(CMS_ORIGIN, zips[0])
    except Exception as exc:
        log_warning(f"RVU zip discovery failed ({exc})")
    return DEFAULT_RVU_ZIP


def discover_latest_work_time_zip() -> str:
    try:
        html = http_get_text(WORK_TIME_PAGE)
        zips = re.findall(
            r'href="(/files/zip/[^"]*physician-work-time[^"]*\.zip)"',
            html,
            flags=re.I,
        )
        if zips:
            return urljoin(CMS_ORIGIN, zips[0])
    except Exception as exc:
        log_warning(f"work-time zip discovery failed ({exc})")
    return DEFAULT_WORK_TIME_ZIP


def unzip_member(zip_path: Path, dest_dir: Path, prefer: re.Pattern[str]) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        pick = next((n for n in names if prefer.search(n) and not n.endswith("/")), None)
        if not pick:
            raise FileNotFoundError(
                f"No member matching {prefer.pattern!r} in {zip_path.name}: {names[:12]}"
            )
        zf.extract(pick, dest_dir)
        return dest_dir / pick


def download_sources(
    data_dir: Path,
    *,
    skip_discover: bool = False,
    skip_download: bool = False,
) -> Dict[str, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    clia_url = DEFAULT_CLIA_CSV if skip_discover else discover_latest_clia_csv()
    rvu_url = DEFAULT_RVU_ZIP if skip_discover else discover_latest_rvu_zip()
    time_url = DEFAULT_WORK_TIME_ZIP if skip_discover else discover_latest_work_time_zip()
    clia_csv = data_dir / "clia_pos.csv"
    rvu_zip = data_dir / "pfs_rvu.zip"
    time_zip = data_dir / "pfs_work_time.zip"
    if not skip_download:
        log_info(f"CLIA GET {clia_url}")
        ensure_download(clia_url, clia_csv)
        log_info(f"RVU GET {rvu_url}")
        ensure_download(rvu_url, rvu_zip)
        log_info(f"work-time GET {time_url}")
        ensure_download(time_url, time_zip)
    for path, label in ((clia_csv, "CLIA CSV"), (rvu_zip, "RVU zip"), (time_zip, "work-time zip")):
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"missing {label} {path}")
    rvu_csv = unzip_member(rvu_zip, data_dir / "rvu", RVU_MEMBER)
    time_csv = unzip_member(time_zip, data_dir / "work_time", WORK_TIME_MEMBER)
    return {"clia": clia_csv, "rvu": rvu_csv, "work_time": time_csv}


def load_clia(conn: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    log_info(f"CLIA COPY {csv_path.name} ({csv_path.stat().st_size:,} bytes)")
    conn.execute(
        """
        CREATE OR REPLACE TABLE cms_clia AS
        SELECT * FROM read_csv(
            ?,
            header=true,
            auto_detect=true,
            ignore_errors=true,
            sample_size=50000
        )
        """,
        [str(csv_path)],
    )
    n = int(conn.execute("SELECT COUNT(*) FROM cms_clia").fetchone()[0])
    log_info(f"cms_clia: {n:,} rows")
    return n


def load_pfs_rvu(conn: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    """PPRRVU non-QPP. skip=9 so line 10 is the header. Keep modifier rows."""
    log_info(f"PFS RVU COPY {csv_path.name} skip={RVU_SKIP}")
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE cms_pfs_rvu AS
        SELECT
            upper(trim(CAST(HCPCS AS VARCHAR))) AS hcpcs,
            nullif(trim(CAST(MOD AS VARCHAR)), '') AS mod,
            nullif(trim(CAST(DESCRIPTION AS VARCHAR)), '') AS description,
            nullif(trim(CAST(CODE AS VARCHAR)), '') AS status_code,
            TRY_CAST(RVU AS DOUBLE) AS work_rvu,
            TRY_CAST("PE RVU" AS DOUBLE) AS pe_rvu_nonfac,
            TRY_CAST("PE RVU_1" AS DOUBLE) AS pe_rvu_fac,
            TRY_CAST(RVU_1 AS DOUBLE) AS mp_rvu,
            TRY_CAST(TOTAL AS DOUBLE) AS total_nonfac,
            TRY_CAST(TOTAL_1 AS DOUBLE) AS total_fac,
            nullif(trim(CAST(DAYS AS VARCHAR)), '') AS glob_days,
            TRY_CAST(OP AS DOUBLE) AS pre_op,
            TRY_CAST(OP_1 AS DOUBLE) AS intra_op,
            TRY_CAST(OP_2 AS DOUBLE) AS post_op
        FROM read_csv(
            ?,
            header=true,
            skip={RVU_SKIP},
            auto_detect=true,
            ignore_errors=true,
            quote='"',
            escape='"',
            sample_size=-1
        )
        WHERE HCPCS IS NOT NULL
          AND trim(CAST(HCPCS AS VARCHAR)) != ''
        """,
        [str(csv_path)],
    )
    n = int(conn.execute("SELECT COUNT(*) FROM cms_pfs_rvu").fetchone()[0])
    log_info(f"cms_pfs_rvu: {n:,} rows")
    return n


def load_work_time(conn: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    log_info(f"PFS work-time COPY {csv_path.name}")
    conn.execute(
        """
        CREATE OR REPLACE TABLE cms_pfs_work_time AS
        SELECT
            upper(trim(CAST(cpt_code AS VARCHAR))) AS cpt_code,
            TRY_CAST(Pre_Evaluation_Time AS DOUBLE) AS pre_evaluation_min,
            TRY_CAST(Pre_Positioning_time AS DOUBLE) AS pre_positioning_min,
            TRY_CAST(Pre_Service_Scrub_Dress_Wait_tim AS DOUBLE) AS pre_scrub_min,
            TRY_CAST(Median_Intra_Service_Time AS DOUBLE) AS intra_min,
            TRY_CAST(Immediate_post_Service_time AS DOUBLE) AS post_min,
            TRY_CAST(Total_time AS DOUBLE) AS total_min,
            TRY_CAST("Efficiency Adjusted Total_time" AS DOUBLE) AS adj_total_min,
            TRY_CAST("Efficiency Adjusted Intra_time" AS DOUBLE) AS adj_intra_min
        FROM read_csv(
            ?,
            header=true,
            auto_detect=true,
            ignore_errors=true,
            sample_size=-1
        )
        WHERE cpt_code IS NOT NULL
          AND trim(CAST(cpt_code AS VARCHAR)) != ''
        """,
        [str(csv_path)],
    )
    n = int(conn.execute("SELECT COUNT(*) FROM cms_pfs_work_time").fetchone()[0])
    log_info(f"cms_pfs_work_time: {n:,} rows")
    return n


class CmsCliaPfsProcessor:
    def __init__(self, db_path: str, data_dir: Path | None = None):
        self.db_path = db_path
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()

    def run(
        self,
        *,
        skip_discover: bool = False,
        skip_download: bool = False,
        download_only: bool = False,
    ) -> Dict[str, int]:
        paths = download_sources(
            self.data_dir, skip_discover=skip_discover, skip_download=skip_download
        )
        if download_only:
            log_info(f"download-only: { {k: str(v) for k, v in paths.items()} }")
            return {k: v.stat().st_size for k, v in paths.items()}
        mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "12GB")
        log_info(f"CLIA/PFS ingest db={self.db_path} mem={mem}")
        conn = duckdb.connect(
            self.db_path,
            config={
                "memory_limit": mem,
                "threads": "1",
                "preserve_insertion_order": "false",
            },
        )
        stats: Dict[str, int] = {}
        try:
            stats["cms_clia"] = load_clia(conn, paths["clia"])
            stats["cms_pfs_rvu"] = load_pfs_rvu(conn, paths["rvu"])
            stats["cms_pfs_work_time"] = load_work_time(conn, paths["work_time"])
            for sql in INDEXES:
                conn.execute(sql)
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        log_info(f"=== CLIA/PFS complete: {stats} ===")
        return stats


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest CMS CLIA POS + PFS RVU/work time")
    p.add_argument("--db", default=os.environ.get("DB", DEFAULT_DB))
    p.add_argument("--data-dir", default=None)
    p.add_argument("--download-only", action="store_true")
    p.add_argument("--skip-download", action="store_true")
    p.add_argument(
        "--skip-discover",
        action="store_true",
        help="Use hardcoded CY2026 URLs instead of catalog/page scrape",
    )
    args = p.parse_args()
    CmsCliaPfsProcessor(args.db, Path(args.data_dir) if args.data_dir else None).run(
        skip_discover=args.skip_discover,
        skip_download=args.skip_download,
        download_only=args.download_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
