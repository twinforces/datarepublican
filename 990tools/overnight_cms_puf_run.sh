#!/usr/bin/env bash
# CMS Physician PUF 2024 + LEIE → DuckDB. CSVs already on Insurrection.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${CMS_PUF_LOG:-$ROOT/logs/overnight_cms_puf_${TS}.log}"
STATUS="$ROOT/overnight_cms_puf_status.txt"
PIDFILE="$ROOT/overnight_cms_puf.pid"
export CMS_PUF_DATA_DIR="${CMS_PUF_DATA_DIR:-/Volumes/Insurrection/990data/cms_data/medicare_puf}"
export DB="${DB:-/Volumes/Data/final/irs990.duckdb}"
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-12GB}"

mkdir -p "$ROOT/logs" 2>/dev/null || true
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=cms_puf"
  echo "data_dir=$CMS_PUF_DATA_DIR"
  echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== CMS PUF ingest START pid=$$ memory=$DUCKDB_MEMORY_LIMIT ==="
df -h /Volumes/Data /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i -s python3 -u cms_puf_processor.py >>"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "cms_puf_processor completed successfully"
else log "cms_puf_processor exited non-zero: rc=$rc"; fi
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
} >"$STATUS"
log "=== CMS PUF ingest END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
