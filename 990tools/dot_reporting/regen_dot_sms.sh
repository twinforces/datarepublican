#!/bin/bash
# DOT SMS ship: refresh Rogues gallery nav, national DOT details (4 slices),
# by-state DOT details (4 slices), then master_index. Stable undated paths.
# SMS attach lives in generate_address_reports.py / generate_state_reports.py.
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DR="dot_reporting"
DB="${IRS990_DB_PATH:-/Volumes/Data/final/irs990.duckdb}"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${DOT_SMS_LOG:-$DR/regen_dot_sms_${TS}.log}"
PIDFILE="$DR/regen_dot_sms.pid"
failed=0

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

rebuild_master() {
  python3 -u "$DR/build_master_index.py" --reports-dir "$DR/reports" >>"$LOG" 2>&1 || true
}

run_gallery() {
  log "---- Rogues gallery unsafe_colocators ----"
  if python3 -u "$DR/generate_safety_gallery.py" \
      --db-path "$DB" --max-clusters 100 >>"$LOG" 2>&1; then
    log "---- OK gallery ----"
  else
    log "---- FAIL gallery rc=$? ----"
    failed=1
  fi
  rebuild_master
}

run_dot_national() {
  local slice="$1"
  local out="$DR/reports/${slice}_clusters"
  log "---- NATIONAL dot slice=$slice → $out ----"
  if python3 -u "$DR/generate_address_reports.py" \
      --db-path "$DB" --slice-by "$slice" --output-dir "$out" >>"$LOG" 2>&1; then
    log "---- OK national dot $slice ----"
  else
    log "---- FAIL national dot $slice rc=$? ----"
    failed=1
  fi
  rebuild_master
}

run_dot_state() {
  local slice="$1"
  local out="$DR/reports/dot_${slice}_by_state"
  log "---- STATE-FULL dot slice=$slice → $out ----"
  if python3 -u "$DR/generate_state_reports.py" \
      --db-path "$DB" --focus dot --slice-by "$slice" \
      --output-dir "$out" >>"$LOG" 2>&1; then
    log "---- OK state dot $slice ----"
    local nst npg
    nst=$(find "$out/states" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
    npg=$(find "$out/states" -name '*.html' 2>/dev/null | wc -l | tr -d ' ')
    log "     states=$nst html≈$npg"
  else
    log "---- FAIL state dot $slice rc=$? ----"
    failed=1
  fi
  rebuild_master
}

echo $$ > "$PIDFILE"
log "=== DOT SMS REGEN START db=$DB pid=$$ log=$LOG ==="
if [ ! -r "$DB" ]; then
  log "FATAL: cannot read $DB"
  rm -f "$PIDFILE"
  exit 1
fi

log "=== PHASE 0: Rogues gallery ==="
run_gallery

log "=== PHASE 1: National DOT (4 slices) ==="
for slice in colocator zipcode loose_colocator address; do
  run_dot_national "$slice"
done

log "=== PHASE 2: By-state DOT (4 slices) ==="
for slice in colocator zipcode loose_colocator address; do
  run_dot_state "$slice"
done

rebuild_master
log "=== DOT SMS REGEN DONE failed=$failed ==="
log "Master index: $DR/reports/master_index.html"
rm -f "$PIDFILE"
exit $failed
