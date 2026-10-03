#!/usr/bin/env bash
# Detached: SBA PPP FOIA → ppp_loans + ppp_borrower Addresses.
# Does not run the global address-dedup step (that's the long one).
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${PPP_LOG:-$ROOT/logs/overnight_ppp_${TS}.log}"
STATUS="$ROOT/overnight_ppp_status.txt"
PIDFILE="$ROOT/overnight_ppp.pid"
DB="/Volumes/Data/final/irs990.duckdb"
FINAL="/Volumes/Data/final"
# 16GB host: leave headroom so CREATE INDEX can spill to duckdb_tmp.
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-8GB}"
export DUCKDB_TEMP_DIRECTORY="${DUCKDB_TEMP_DIRECTORY:-/Volumes/Data/final/duckdb_tmp}"

mkdir -p "$ROOT/logs" "$FINAL/cms_data/ppp" "$FINAL/duckdb_tmp" 2>/dev/null || true
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=ppp"
  echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== PPP FOIA ingest START pid=$$ memory=$DUCKDB_MEMORY_LIMIT ==="
log "step: ppp only (address colocator not included)"
df -h /Volumes/Data | tee -a "$LOG"

set +e
caffeinate -i -s python3 -u irs990processor.py \
  --step ppp \
  --final-dir "$FINAL" \
  --db-path "$DB" \
  --workers 8 \
  --db-threads 1 \
  --nostats \
  -v \
  >>"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "irs990processor completed successfully"
else log "irs990processor exited non-zero: rc=$rc"; fi
df -h /Volumes/Data | tee -a "$LOG"
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
} >"$STATUS"
log "=== PPP FOIA ingest END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
