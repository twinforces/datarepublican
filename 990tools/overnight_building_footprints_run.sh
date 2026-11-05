#!/usr/bin/env bash
# Microsoft US Building Footprints → Insurrection sidecar DuckDB.
# Download zip, unzip, measure, drop geojson, next state. Tennessee first.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${BUILDING_LOG:-$ROOT/logs/overnight_building_footprints_${TS}.log}"
STATUS="$ROOT/overnight_building_footprints_status.txt"
PIDFILE="$ROOT/overnight_building_footprints.pid"
export BUILDING_DATA_DIR="${BUILDING_DATA_DIR:-/Volumes/Insurrection/990data/buildings}"
export BUILDING_DB="${BUILDING_DB:-/Volumes/Insurrection/990data/buildings.duckdb}"
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-12GB}"

mkdir -p "$ROOT/logs" "$BUILDING_DATA_DIR"
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "db=$BUILDING_DB"
  echo "data_dir=$BUILDING_DATA_DIR"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== building footprints START pid=$$ ==="
log "db=$BUILDING_DB data_dir=$BUILDING_DATA_DIR states=${BUILDING_STATES:-all}"
df -h /Volumes/Insurrection | tee -a "$LOG"

set +e
caffeinate -i -s python3 -u building_footprints_processor.py \
  --db "$BUILDING_DB" \
  --data-dir "$BUILDING_DATA_DIR" \
  >>"$LOG" 2>&1
rc=$?
set -e

{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
  echo "db=$BUILDING_DB"
} >"$STATUS"
log "=== building footprints END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
