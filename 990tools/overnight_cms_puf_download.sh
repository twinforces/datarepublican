#!/usr/bin/env bash
# CMS Physician PUF + LEIE download only. No DuckDB.
# By Provider has Bene_Avg_Age. By Provider and Service is NPI×HCPCS volume.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${CMS_PUF_LOG:-$ROOT/logs/overnight_cms_puf_download_${TS}.log}"
STATUS="$ROOT/overnight_cms_puf_download_status.txt"
PIDFILE="$ROOT/overnight_cms_puf_download.pid"
export CMS_PUF_DATA_DIR="${CMS_PUF_DATA_DIR:-/Volumes/Insurrection/990data/cms_data/medicare_puf}"

mkdir -p "$ROOT/logs" "$CMS_PUF_DATA_DIR" 2>/dev/null || true
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=cms_puf_download"
  echo "data_dir=$CMS_PUF_DATA_DIR"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== CMS PUF zip download START pid=$$ ==="
log "data_dir=$CMS_PUF_DATA_DIR"
df -h /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i python3 -u - <<'PY' >>"$LOG" 2>&1
from pathlib import Path
import os
from download_utils import ensure_download
from logging_utils import log_info

dest = Path(os.environ["CMS_PUF_DATA_DIR"])
dest.mkdir(parents=True, exist_ok=True)
files = [
    (
        "https://data.cms.gov/sites/default/files/2026-05/7323ba02-52e7-4a86-b2ce-ad210c25d9aa/MUP_PHY_R26_P05_V10_D24_Prov.csv",
        dest / "MUP_PHY_R26_P05_V10_D24_Prov.csv",
    ),
    (
        "https://data.cms.gov/sites/default/files/2026-05/b5ebab5a-f490-418a-9bce-4b9f31419356/PHY_R26_P05_V10_D24_Prov_Svc.csv",
        dest / "PHY_R26_P05_V10_D24_Prov_Svc.csv",
    ),
    (
        "https://data.cms.gov/sites/default/files/2026-05/e534c74b-79b8-4892-8a95-5a17e2dfec9f/MUP_PHY_R26_P05_V10_D24_Geo.csv",
        dest / "MUP_PHY_R26_P05_V10_D24_Geo.csv",
    ),
    (
        "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv",
        dest / "LEIE_UPDATED.csv",
    ),
]
failed = 0
for url, path in files:
    log_info(f"CMS PUF {path.name}")
    try:
        ensure_download(url, path)
    except RuntimeError as exc:
        log_info(f"CMS PUF failed {path.name}: {exc}")
        failed += 1
raise SystemExit(1 if failed else 0)
PY
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "CMS PUF download completed successfully"
else log "CMS PUF download exited non-zero: rc=$rc"; fi
df -h /Volumes/Insurrection | tee -a "$LOG"
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
} >"$STATUS"
log "=== CMS PUF download END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
