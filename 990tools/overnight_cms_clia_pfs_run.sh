#!/usr/bin/env bash
# CMS CLIA POS + PFS work RVU/time → DuckDB on Data. Files on Insurrection.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${CMS_CLIA_PFS_LOG:-$ROOT/logs/overnight_cms_clia_pfs_${TS}.log}"
STATUS="$ROOT/overnight_cms_clia_pfs_status.txt"
PIDFILE="$ROOT/overnight_cms_clia_pfs.pid"
export CMS_CLIA_PFS_DATA_DIR="${CMS_CLIA_PFS_DATA_DIR:-/Volumes/Insurrection/990data/cms_data/clia_pfs}"
export DB="${DB:-/Volumes/Data/final/irs990.duckdb}"
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-12GB}"

mkdir -p "$ROOT/logs" "$CMS_CLIA_PFS_DATA_DIR"
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=cms_clia_pfs"
  echo "data_dir=$CMS_CLIA_PFS_DATA_DIR"
  echo "db=$DB"
  echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== CLIA/PFS ingest START pid=$$ memory=$DUCKDB_MEMORY_LIMIT ==="
df -h /Volumes/Data /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i -s python3 -u cms_clia_pfs_processor.py >>"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "cms_clia_pfs_processor completed successfully"
else log "cms_clia_pfs_processor exited non-zero: rc=$rc"; fi
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
  echo "db=$DB"
} >"$STATUS"
log "=== CLIA/PFS ingest END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
