#!/usr/bin/env bash
# Fire-and-forget IRS download + import overnight.
# Steps: irsfetch → zip → bmf once, then xml with OOM resume (exit 75).
# Survives terminal exit; keeps machine awake via caffeinate.
#
# Do not use `if ! cmd; then rc=$?` — bash inverts $? and reports OOM as 0.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${OVERNIGHT_LOG:-$ROOT/logs/overnight_ingest_${TS}.log}"
STATUS="$ROOT/overnight_ingest_status.txt"
PIDFILE="$ROOT/overnight_ingest.pid"
DB="/Volumes/Data/final/irs990.duckdb"
FINAL="/Volumes/Data/final"
ZIPS="/Volumes/Data/irs_zips"
# 16GB host: last XML run OOM'd Charities INSERT at DuckDB's default 6GB cap.
export DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-8GB}"
export DUCKDB_TEMP_DIRECTORY="${DUCKDB_TEMP_DIRECTORY:-$FINAL/duckdb_tmp}"
# ANALYZE after xml OOMs the 16GB host and can hang CHECKPOINT; xml loop does not need it.
export SKIP_POST_STEP_OPTIMIZE="${SKIP_POST_STEP_OPTIMIZE:-1}"
WORKERS="${WORKERS:-6}"
DB_THREADS="${DB_THREADS:-1}"
XML_OOM_RESTARTS="${XML_OOM_RESTARTS:-120}"
# XML_ONLY=1 skips irsfetch→bmf (resume leftover XML after a dead fetch).
XML_ONLY="${XML_ONLY:-0}"
# XML Address rows append to $FINAL/xml_addresses.tsv; DuckDB insert waits for the address step.
export XML_DEFER_ADDRESSES="${XML_DEFER_ADDRESSES:-1}"
export XML_ADDRESS_TSV="${XML_ADDRESS_TSV:-$FINAL/xml_addresses.tsv}"

mkdir -p "$ROOT/logs" "$FINAL/duckdb_tmp" 2>/dev/null || true

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

write_status() {
  local state="$1"
  {
    echo "started_at=$STARTED_AT"
    echo "pid=$$"
    echo "log=$LOG"
    echo "status=$state"
    echo "step=${CURRENT_STEP:-starting}"
    echo "duckdb_memory=$DUCKDB_MEMORY_LIMIT"
    echo "workers=$WORKERS"
    echo "db_threads=$DB_THREADS"
    echo "xml_attempt=${XML_ATTEMPT:-0}"
    [[ -n "${finished_at:-}" ]] && echo "finished_at=$finished_at"
    [[ -n "${exit_code:-}" ]] && echo "exit_code=$exit_code"
  } >"$STATUS"
}

STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
CURRENT_STEP=starting
XML_ATTEMPT=0
echo $$ >"$PIDFILE"
write_status running

log "=== overnight ingest START pid=$$ memory=$DUCKDB_MEMORY_LIMIT workers=$WORKERS db_threads=$DB_THREADS ==="
log "db=$DB zips=$ZIPS"
df -h /Volumes/Data | tee -a "$LOG"

run_processor() {
  local start="$1" stop="$2"
  CURRENT_STEP="${start}->${stop}"
  write_status running
  log "Launching: $start → $stop"
  # Wait on python's pid, not caffeinate. os._exit(75) under
  # `caffeinate … python` was taking the wrapper down with it.
  python3 -u irs990processor.py \
    --start-step "$start" \
    --stop-step "$stop" \
    --start-year 2017 \
    --end-year 2030 \
    --final-dir "$FINAL" \
    --zips-dir "$ZIPS" \
    --db-path "$DB" \
    --workers "$WORKERS" \
    --db-threads "$DB_THREADS" \
    --nostats \
    -v \
    >>"$LOG" 2>&1 &
  local py_pid=$!
  caffeinate -i -s -w "$py_pid" &
  wait "$py_pid"
  local rc=$?
  log "irs990processor ${start}->${stop} rc=$rc"
  return "$rc"
}

rc=0
if [[ "$XML_ONLY" == "1" ]]; then
  log "XML_ONLY=1 — skipping irsfetch→bmf"
else
  run_processor irsfetch bmf
  rc=$?
  if [[ $rc -ne 0 ]]; then
    log "irsfetch→bmf failed rc=$rc — still attempting xml resume"
  fi
fi

XML_ATTEMPT=0
xml_rc=0
while true; do
  XML_ATTEMPT=$((XML_ATTEMPT + 1))
  log "xml attempt $XML_ATTEMPT / $XML_OOM_RESTARTS"
  run_processor xml xml
  xml_rc=$?
  if [[ $xml_rc -eq 0 ]]; then
    break
  fi
  if [[ $xml_rc -eq 75 && $XML_ATTEMPT -lt $XML_OOM_RESTARTS ]]; then
    log "xml OOM (os._exit 75) — restarting with a clean DuckDB connection"
    continue
  fi
  log "xml stopped rc=$xml_rc after $XML_ATTEMPT attempt(s)"
  break
done

if [[ $rc -eq 0 ]]; then
  rc=$xml_rc
fi

df -h /Volumes/Data | tee -a "$LOG"
finished_at=$(date '+%Y-%m-%d %H:%M:%S')
exit_code=$rc
CURRENT_STEP=done
if [[ $rc -eq 0 ]]; then
  write_status success
  log "irs990processor completed successfully"
else
  write_status failed
  log "overnight ingest failed rc=$rc"
fi

log "=== overnight ingest END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
