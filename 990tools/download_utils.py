#!/usr/bin/env python3
"""Idempotent curl-based downloads for pipeline data sources."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from logging_utils import log_info, log_warning


@dataclass(frozen=True)
class RemoteMeta:
    url: str
    content_length: Optional[int]
    last_modified: Optional[datetime]
    etag: Optional[str]
    status_code: int


def _parse_http_date(value: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def _parse_curl_headers(blob: str) -> tuple[int, dict[str, str]]:
    headers: dict[str, str] = {}
    status_code = 0
    for line in blob.splitlines():
        if line.upper().startswith("HTTP/"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                status_code = int(parts[1])
                headers = {}
        elif ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip().lower()] = val.strip()
    return status_code, headers


def _curl_header_dump(url: str, timeout: int, *extra: str) -> Optional[str]:
    cmd = ["curl", "-sL", "--max-time", str(timeout), *extra, url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise RuntimeError("curl not found on PATH") from None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout


def _meta_from_dump(url: str, dump: str) -> Optional[RemoteMeta]:
    status_code, headers = _parse_curl_headers(dump)
    if status_code not in (200, 206):
        return None
    cl = headers.get("content-length")
    cr = headers.get("content-range")
    length = int(cl) if cl and cl.isdigit() else None
    if length is None and cr:
        # bytes 0-0/2039742911
        if "/" in cr:
            total = cr.rsplit("/", 1)[-1].strip()
            if total.isdigit():
                length = int(total)
    return RemoteMeta(
        url=url,
        content_length=length,
        last_modified=_parse_http_date(headers.get("last-modified", "")),
        etag=headers.get("etag"),
        status_code=status_code,
    )


def curl_remote_meta(url: str, timeout: int = 60) -> Optional[RemoteMeta]:
    """Remote size/etag. HEAD first; range GET if HEAD is empty or non-200.

    files.usaspending.gov sometimes answers HEAD with nothing while GET works.
    A real 404 returns None without retries.
    """
    for attempt in range(3):
        for extra in (("-I",), ("-D", "-", "-o", "/dev/null", "-r", "0-0")):
            dump = _curl_header_dump(url, timeout, *extra)
            if not dump:
                continue
            status, _headers = _parse_curl_headers(dump)
            meta = _meta_from_dump(url, dump)
            if meta is not None:
                return meta
            if status in (403, 404, 410):
                return None
        if attempt < 2:
            time.sleep(1 + attempt)
    return None


def _load_sidecar(meta_path: Path) -> Optional[dict]:
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_sidecar(meta_path: Path, url: str, remote: RemoteMeta, local_path: Path) -> None:
    payload = {
        "url": url,
        "etag": remote.etag,
        "content_length": remote.content_length,
        "last_modified": remote.last_modified.isoformat() if remote.last_modified else None,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "local_size": local_path.stat().st_size if local_path.exists() else 0,
    }
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_local_current(local_path: Path, meta_path: Path, remote: RemoteMeta) -> bool:
    """True when local file matches remote metadata (size + etag/last-modified)."""
    if not local_path.exists() or local_path.stat().st_size == 0:
        return False

    local_size = local_path.stat().st_size
    saved = _load_sidecar(meta_path)
    if saved:
        if saved.get("url") != remote.url:
            return False
        if remote.etag and saved.get("etag") == remote.etag:
            if remote.content_length is None or saved.get("content_length") == remote.content_length:
                return True
        if (
            remote.content_length is not None
            and saved.get("content_length") == remote.content_length
            and saved.get("local_size") == local_size
        ):
            return True

    if remote.content_length is not None and local_size != remote.content_length:
        return False

    if remote.last_modified is not None:
        local_mtime = datetime.fromtimestamp(local_path.stat().st_mtime, tz=timezone.utc)
        if local_mtime >= remote.last_modified:
            return True
        return False

    # No remote timestamp — size match is enough.
    return remote.content_length is None or local_size == remote.content_length


def implausible_remote_length(local_size: int, remote_len: Optional[int]) -> bool:
    """HEAD/range sometimes returns a tiny error-page length against a GB zip."""
    if remote_len is None or local_size <= 0:
        return False
    return local_size >= 10_000_000 and remote_len < 1_000_000


def should_resume_partial(
    local_size: int,
    remote_len: Optional[int],
    saved: Optional[dict],
    same_object: bool,
) -> bool:
    """Keep a mid-GET partial. Wipe only when a sidecar proves a different object."""
    if local_size <= 0:
        return False
    if remote_len and local_size < remote_len and (not saved or same_object):
        return True
    if not saved and not remote_len:
        return True
    return False


def curl_download(
    url: str,
    dest: Path,
    *,
    resume: bool = True,
    timeout: int = 0,
) -> None:
    """Download url to dest using curl. Resume on curl 18/28/56; keep partials."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Optional[subprocess.CompletedProcess] = None
    for attempt in range(1, 6):
        cmd = [
            "curl",
            "-L",
            "--fail",
            "--retry",
            "8",
            "--retry-delay",
            "5",
        ]
        if resume and dest.exists() and dest.stat().st_size > 0:
            cmd.extend(["-C", "-"])
        cmd.extend(["-o", str(dest)])
        if timeout > 0:
            cmd.extend(["--max-time", str(timeout)])
        cmd.append(url)
        last = subprocess.run(cmd)
        if last.returncode == 0:
            return
        size = dest.stat().st_size if dest.exists() else 0
        # 18 needs bytes on disk. 28/52/56 can happen with an empty dest.
        retryable = last.returncode in (28, 52, 56) or (
            last.returncode == 18 and resume and size > 0
        )
        if retryable:
            log_warning(
                f"curl {last.returncode} on {dest.name} ({size:,} bytes) "
                f"attempt {attempt}; retry"
            )
            time.sleep(min(5 * attempt, 30))
            continue
        break
    if last is None:
        raise RuntimeError(f"curl did not run for {url}")
    if last.returncode == 22:
        if dest.exists() and dest.stat().st_size < 64_000:
            dest.unlink(missing_ok=True)
        raise RuntimeError(f"HTTP error downloading {url} (curl 22)")
    last.check_returncode()


def ensure_download(
    url: str,
    dest: Path,
    *,
    resume: bool = True,
    timeout: int = 0,
    force: bool = False,
) -> bool:
    """
    Download url to dest if missing or stale.
    Returns True if a download occurred, False if skipped (already current).
    """
    meta_path = dest.with_suffix(dest.suffix + ".meta.json")
    remote = curl_remote_meta(url)
    if dest.exists() and dest.stat().st_size > 0:
        if remote is None:
            log_warning(f"Remote unreachable for {url}; using existing {dest}")
            return False
        if implausible_remote_length(dest.stat().st_size, remote.content_length):
            log_warning(
                f"Implausible remote length {remote.content_length} vs "
                f"local {dest.stat().st_size:,} for {dest.name}; keeping local"
            )
            return False
        if not force and is_local_current(dest, meta_path, remote):
            log_info(f"Up to date: {dest.name} ({dest.stat().st_size:,} bytes)")
            return False
    elif remote is None:
        # HEAD/range often empty on files.usaspending.gov while GET works.
        log_warning(f"No HEAD/range for {url}; trying full GET")
        remote = RemoteMeta(
            url=url,
            content_length=None,
            last_modified=None,
            etag=None,
            status_code=0,
        )

    # Stale or missing: never curl -C onto an old full file. Resume only makes
    # sense for a partial download of the *same* remote object; splicing a new
    # OFAC/CMS body onto a previous version produces "junk after document element".
    use_resume = resume
    if dest.exists():
        local_size = dest.stat().st_size
        remote_len = remote.content_length
        same_object = False
        saved = _load_sidecar(meta_path)
        if saved and remote.etag and saved.get("etag") and saved.get("etag") == remote.etag:
            same_object = True
        if (
            saved
            and remote.last_modified
            and saved.get("last_modified")
            and saved.get("last_modified") == remote.last_modified.isoformat()
        ):
            same_object = True
        # Partial of this object: resume. First GET has no sidecar; do not
        # treat local_size < Content-Length as a stale full file.
        if should_resume_partial(local_size, remote_len, saved, same_object):
            use_resume = True
            shown = f"{local_size:,}/{remote_len:,}" if remote_len else f"{local_size:,}"
            log_info(f"Resuming partial {dest.name} ({shown} bytes)")
        else:
            use_resume = False
            log_info(f"Replacing stale {dest.name} via sidecar file (dest stays until GET succeeds)")

    download_target = dest
    if dest.exists() and dest.stat().st_size > 0 and not use_resume:
        download_target = Path(str(dest) + ".part")
        if download_target.exists():
            try:
                download_target.unlink()
            except OSError:
                pass

    log_info(f"Downloading {url} → {download_target}")
    curl_download(url, download_target, resume=use_resume, timeout=timeout)
    final_size = download_target.stat().st_size if download_target.exists() else 0
    if remote.content_length and final_size < remote.content_length:
        if download_target != dest:
            download_target.unlink(missing_ok=True)
        raise RuntimeError(
            f"Download incomplete for {dest.name}: "
            f"{final_size} < {remote.content_length}"
        )
    if final_size <= 0:
        raise RuntimeError(f"Download empty for {dest.name}")
    if download_target != dest:
        download_target.replace(dest)
    _save_sidecar(meta_path, url, remote, dest)
    log_info(f"Downloaded {dest.name} ({final_size:,} bytes)")
    return True


MEDICAID_SPENDING_DATASET_PAGE = (
    "https://opendata.hhs.gov/datasets/medicaid-provider-spending/"
)
MEDICAID_SPENDING_BLOB_PREFIX = (
    "https://stopendataprod.blob.core.windows.net/datasets/medicaid-provider-spending/"
)


def discover_medicaid_spending_urls(
    page_url: str = MEDICAID_SPENDING_DATASET_PAGE,
    *,
    timeout: int = 120,
) -> dict[str, Optional[str]]:
    """
    Scrape the HHS Medicaid provider spending dataset page for Azure blob links.

    Returns parquet, duckdb, zip, and csv blob URLs (any may be None).
    """
    try:
        proc = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), page_url],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("curl not found on PATH") from None

    if proc.returncode != 0 or not proc.stdout:
        return {"parquet": None, "duckdb": None, "zip": None, "csv": None}

    urls = set(
        re.findall(
            re.escape(MEDICAID_SPENDING_BLOB_PREFIX) + r"[^\"\\]+",
            proc.stdout,
        )
    )

    def _release_date(url: str) -> str:
        match = re.search(r"/(\d{4}-\d{2}-\d{2})/", url)
        return match.group(1) if match else ""

    parquet_urls = sorted(
        (u for u in urls if u.endswith(".parquet")),
        key=_release_date,
    )
    duckdb_urls = sorted(
        (u for u in urls if u.endswith(".duckdb")),
        key=_release_date,
    )
    zip_urls = sorted(
        (u for u in urls if u.endswith(".csv.zip")),
        key=_release_date,
    )
    csv_urls = sorted(
        (u for u in urls if u.endswith(".csv")),
        key=_release_date,
    )

    return {
        "parquet": parquet_urls[-1] if parquet_urls else None,
        "duckdb": duckdb_urls[-1] if duckdb_urls else None,
        "zip": zip_urls[-1] if zip_urls else None,
        "csv": csv_urls[-1] if csv_urls else None,
    }


def discover_medicaid_spending_download_url(
    page_url: str = MEDICAID_SPENDING_DATASET_PAGE,
) -> Optional[str]:
    """Prefer parquet; fall back to duckdb, csv.zip, then raw csv."""
    urls = discover_medicaid_spending_urls(page_url)
    return (
        urls.get("parquet")
        or urls.get("duckdb")
        or urls.get("zip")
        or urls.get("csv")
    )


NPPES_FILES_PAGE = "https://download.cms.gov/nppes/NPI_Files.html"
NPPES_DOWNLOAD_BASE = "https://download.cms.gov/nppes/"
NPPES_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
NPPES_MONTHLY_V2_RE = re.compile(
    r"NPPES_Data_Dissemination_"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"_(\d{4})_V2\.zip",
    re.IGNORECASE,
)


def _nppes_monthly_release_key(filename: str) -> tuple[int, int]:
    match = NPPES_MONTHLY_V2_RE.search(filename)
    if not match:
        return (0, 0)
    month = match.group(1).title()
    year = int(match.group(2))
    try:
        month_idx = NPPES_MONTH_NAMES.index(month)
    except ValueError:
        month_idx = 0
    return (year, month_idx)


def discover_nppes_zip_urls_from_page(
    page_url: str = NPPES_FILES_PAGE,
    *,
    timeout: int = 120,
) -> list[str]:
    """Scrape CMS NPI_Files.html for monthly V.2 dissemination zip links."""
    try:
        proc = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), page_url],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("curl not found on PATH") from None

    if proc.returncode != 0 or not proc.stdout:
        return []

    hrefs = re.findall(
        r"""href=['"]\.?/?([^'"]+\.zip)['"]""",
        proc.stdout,
        flags=re.IGNORECASE,
    )

    monthly_urls: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        name = Path(href).name
        if "Weekly" in name or "Deactivated" in name:
            continue
        if not NPPES_MONTHLY_V2_RE.fullmatch(name):
            continue
        url = NPPES_DOWNLOAD_BASE + name
        if url not in seen:
            seen.add(url)
            monthly_urls.append(url)

    monthly_urls.sort(key=lambda u: _nppes_monthly_release_key(Path(u).name))
    return monthly_urls


OFAC_SDN_ADVANCED_URL = (
    "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn_advanced.xml"
)
OFAC_SDN_LEGACY_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"

FMCSA_COMPANY_CENSUS_URL = (
    "https://data.transportation.gov/api/views/az4n-8mr2/rows.csv?accessType=DOWNLOAD"
)
# SMS monthly public files (current measurement window, not full MCMIS history).
FMCSA_SMS_CRASH_URL = (
    "https://data.transportation.gov/api/views/4wxs-vbns/rows.csv?accessType=DOWNLOAD"
)
FMCSA_SMS_INSPECTION_URL = (
    "https://data.transportation.gov/api/views/rbkj-cgst/rows.csv?accessType=DOWNLOAD"
)
FMCSA_SMS_BASICS_URL = (
    "https://data.transportation.gov/api/views/4y6x-dmck/rows.csv?accessType=DOWNLOAD"
)


def discover_fmcsa_census_url() -> str:
    """Return FMCSA Company Census CSV export URL (data.transportation.gov Socrata)."""
    return FMCSA_COMPANY_CENSUS_URL


def discover_fmcsa_sms_urls() -> dict:
    """Socrata CSV export URLs for SMS crash, inspection, and BASIC (PassProperty)."""
    return {
        "crash": FMCSA_SMS_CRASH_URL,
        "inspection": FMCSA_SMS_INSPECTION_URL,
        "basics": FMCSA_SMS_BASICS_URL,
    }


def discover_ofac_sdn_url() -> str:
    """Return the OFAC SDN advanced XML export URL (stable Treasury endpoint)."""
    meta = curl_remote_meta(OFAC_SDN_ADVANCED_URL)
    if meta and meta.status_code == 200:
        return OFAC_SDN_ADVANCED_URL
    log_warning(
        f"OFAC advanced XML unreachable at {OFAC_SDN_ADVANCED_URL}; "
        f"falling back to legacy {OFAC_SDN_LEGACY_URL}"
    )
    return OFAC_SDN_LEGACY_URL


def discover_nppes_zip_url(
    page_url: str = NPPES_FILES_PAGE,
    *,
    months_back: int = 6,
    base: str = "https://download.cms.gov/nppes/NPPES_Data_Dissemination_{month}_{year}_V2.zip",
) -> Optional[str]:
    """Find the newest monthly NPPES V.2 dissemination zip from CMS NPI_Files.html."""
    urls = discover_nppes_zip_urls_from_page(page_url)
    if urls:
        return urls[-1]

    # Fallback: probe recent month/year filenames when the index page is unreachable.
    now = datetime.now(timezone.utc)
    year, month_idx = now.year, now.month - 1
    for _ in range(months_back):
        month = NPPES_MONTH_NAMES[month_idx]
        url = base.format(month=month, year=year)
        meta = curl_remote_meta(url)
        if meta and meta.status_code == 200:
            return url
        month_idx -= 1
        if month_idx < 0:
            month_idx = 11
            year -= 1
    return None