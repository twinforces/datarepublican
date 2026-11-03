#!/usr/bin/env bash
# FEC bulk zips → fec_* tables + Addresses. Default cycles 2024,2026.
# Zips/extracts land on Insurrection. DuckDB stays on Data.
# Wait until no other irs990processor holds the DB (USASpending ingest).
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${FEC_LOG:-$ROOT/logs/overnight_fec_${TS}.log}"
STATUS="$ROOT/overnight_fec_status.txt"
PIDFILE="$ROOT/overnight_fec.pid"
DB="${DB:-/Volumes/Data/final/irs990.duckdb}"
FINAL="${FINAL:-/Volumes/Data/final}"
export FEC_DATA_DIR="${FEC_DATA_DIR:-/Volumes/Insurrection/990data/cms_data/fec}"
export FEC_CYCLES="${FEC_CYCLES:-2024,2026}"
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-12GB}"
export DUCKDB_TEMP_DIRECTORY="${DUCKDB_TEMP_DIRECTORY:-$FINAL/duckdb_tmp}"

mkdir -p "$ROOT/logs" "$FEC_DATA_DIR" "$FINAL/duckdb_tmp" 2>/dev/null || true
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=fec"
  echo "data_dir=$FEC_DATA_DIR"
  echo "cycles=$FEC_CYCLES"
  echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== FEC bulk ingest START pid=$$ memory=$DUCKDB_MEMORY_LIMIT ==="
log "data_dir=$FEC_DATA_DIR db=$DB cycles=$FEC_CYCLES"
df -h /Volumes/Data /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i -s python3 -u irs990processor.py \
  --step fec \
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
log "=== FEC bulk ingest END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
