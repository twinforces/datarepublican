#!/usr/bin/env bash
# USASpending award descriptions + DuckDB FTS from extracted archive CSVs.
# Slim usaspending_actions is unchanged. Postgres dump stays parked.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${USASPENDING_TEXT_LOG:-$ROOT/logs/overnight_usaspending_text_${TS}.log}"
STATUS="$ROOT/overnight_usaspending_text_status.txt"
PIDFILE="$ROOT/overnight_usaspending_text.pid"
export USASPENDING_DATA_DIR="${USASPENDING_DATA_DIR:-/Volumes/Insurrection/990data/cms_data/usaspending}"
export DB="${DB:-/Volumes/Data/final/irs990.duckdb}"
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-12GB}"
START_YEAR="${USASPENDING_START_YEAR:-2022}"
END_YEAR="${USASPENDING_END_YEAR:-2030}"

mkdir -p "$ROOT/logs" "$USASPENDING_DATA_DIR"
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=usaspending_text"
  echo "data_dir=$USASPENDING_DATA_DIR"
  echo "db=$DB"
  echo "start_year=$START_YEAR"
  echo "end_year=$END_YEAR"
  echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== USASpending text/FTS START pid=$$ memory=$DUCKDB_MEMORY_LIMIT ==="
df -h /Volumes/Data /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i -s python3 -u usaspending_text_processor.py \
  --start-year "$START_YEAR" \
  --end-year "$END_YEAR" \
  >>"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "usaspending_text_processor completed successfully"
else log "usaspending_text_processor exited non-zero: rc=$rc"; fi
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
  echo "db=$DB"
} >"$STATUS"
log "=== USASpending text/FTS END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
