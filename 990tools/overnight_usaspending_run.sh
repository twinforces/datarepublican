#!/usr/bin/env bash
# USASpending Award Data Archive → slim usaspending_actions + by_uei.
# Default FY2022–2030 (UEI). Do not inherit 990 START_YEAR=2017. Missing FYs skip.
# ZIPs land on Insurrection. DuckDB stays on Data. No Postgres dump. No Addresses.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${USASPENDING_LOG:-$ROOT/logs/overnight_usaspending_${TS}.log}"
STATUS="$ROOT/overnight_usaspending_status.txt"
PIDFILE="$ROOT/overnight_usaspending.pid"
DB="${DB:-/Volumes/Data/final/irs990.duckdb}"
FINAL="${FINAL:-/Volumes/Data/final}"
export USASPENDING_DATA_DIR="${USASPENDING_DATA_DIR:-/Volumes/Insurrection/990data/cms_data/usaspending}"
START_YEAR="${USASPENDING_START_YEAR:-2022}"
END_YEAR="${USASPENDING_END_YEAR:-2030}"
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-8GB}"
export DUCKDB_TEMP_DIRECTORY="${DUCKDB_TEMP_DIRECTORY:-$FINAL/duckdb_tmp}"

mkdir -p "$ROOT/logs" "$USASPENDING_DATA_DIR" "$FINAL/duckdb_tmp" 2>/dev/null || true
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=usaspending"
  echo "data_dir=$USASPENDING_DATA_DIR"
  echo "start_year=$START_YEAR"
  echo "end_year=$END_YEAR"
  echo "stamp=${USASPENDING_STAMP:-}"
  echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== USASpending slim ingest START pid=$$ memory=$DUCKDB_MEMORY_LIMIT ==="
log "data_dir=$USASPENDING_DATA_DIR db=$DB FY${START_YEAR}-${END_YEAR} stamp=${USASPENDING_STAMP:-default}"
df -h /Volumes/Data /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i -s python3 -u irs990processor.py \
  --step usaspending \
  --start-year "$START_YEAR" \
  --end-year "$END_YEAR" \
  --final-dir "$FINAL" \
  --db-path "$DB" \
  --workers 2 \
  --db-threads 1 \
  --nostats \
  -v \
  >>"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "irs990processor completed successfully"
else log "irs990processor exited non-zero: rc=$rc"; fi
df -h /Volumes/Data /Volumes/Insurrection | tee -a "$LOG"
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
} >"$STATUS"
log "=== USASpending slim ingest END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
