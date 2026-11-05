#!/usr/bin/env bash
# PUF × PFS minutes → cms_puf_service_hours / cms_puf_npi_hours. No building sidecar.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${CMS_PUF_HOURS_LOG:-$ROOT/logs/overnight_cms_puf_hours_${TS}.log}"
STATUS="$ROOT/overnight_cms_puf_hours_status.txt"
PIDFILE="$ROOT/overnight_cms_puf_hours.pid"
export DB="${DB:-/Volumes/Data/final/irs990.duckdb}"
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-12GB}"

mkdir -p "$ROOT/logs"
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=cms_puf_hours"
  echo "db=$DB"
  echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== PUF hours START pid=$$ memory=$DUCKDB_MEMORY_LIMIT ==="

set +e
caffeinate -i -s python3 -u cms_puf_hours_processor.py >>"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "cms_puf_hours_processor completed successfully"
else log "cms_puf_hours_processor exited non-zero: rc=$rc"; fi
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
  echo "db=$DB"
} >"$STATUS"
log "=== PUF hours END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
