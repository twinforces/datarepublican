#!/usr/bin/env bash
# FEC bulk zip download only. No DuckDB. Safe while USASpending holds the DB.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${FEC_DOWNLOAD_LOG:-$ROOT/logs/overnight_fec_download_${TS}.log}"
STATUS="$ROOT/overnight_fec_download_status.txt"
PIDFILE="$ROOT/overnight_fec_download.pid"
export FEC_DATA_DIR="${FEC_DATA_DIR:-/Volumes/Insurrection/990data/cms_data/fec}"
export FEC_CYCLES="${FEC_CYCLES:-2024,2026}"

mkdir -p "$ROOT/logs" "$FEC_DATA_DIR" 2>/dev/null || true
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "steps=fec_download"
  echo "data_dir=$FEC_DATA_DIR"
  echo "cycles=$FEC_CYCLES"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== FEC zip download START pid=$$ cycles=$FEC_CYCLES ==="
log "data_dir=$FEC_DATA_DIR"
df -h /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i python3 -u fec_processor.py --download-only >>"$LOG" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then log "FEC zip download completed successfully"
else log "FEC zip download exited non-zero: rc=$rc"; fi
df -h /Volumes/Insurrection | tee -a "$LOG"
{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
} >"$STATUS"
log "=== FEC zip download END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
