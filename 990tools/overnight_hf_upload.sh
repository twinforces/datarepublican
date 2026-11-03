#!/usr/bin/env bash
# Export+upload selected DuckDB tables to piercewetter3/irs-990-parsed.
# Waits for a read_only lock. Parquet lands on Insurrection (Data is tight).
# --tables does not rewrite the Hub README.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${HF_LOG:-$ROOT/logs/overnight_hf_${TS}.log}"
STATUS="$ROOT/overnight_hf_status.txt"
PIDFILE="$ROOT/overnight_hf.pid"
DB="${DB:-/Volumes/Data/final/irs990.duckdb}"
OUT="${HF_OUT:-/Volumes/Insurrection/990data/parquet_export_990}"
TABLES="${HF_TABLES:-fec_committees,fec_candidate_spendings,fec_committee_transactions,fec_individual_contributions,fec_operating_expenditures,usaspending_actions,usaspending_by_uei,cms_puf_provider,cms_puf_service,cms_puf_geo,cms_leie}"
ENVRC="$(cd "$ROOT/.." && pwd)/.envrc"

mkdir -p "$ROOT/logs" "$OUT"
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=waiting_for_db"
  echo "out=$OUT"
  echo "tables=$TABLES"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== HF upload START pid=$$ ==="
log "db=$DB out=$OUT tables=$TABLES"

if [ -f "$ENVRC" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENVRC"
  set +a
  log "sourced $(basename "$ENVRC") (token present=$( [ -n "${HF_TOKEN:-}" ] && echo yes || echo no ))"
fi

while true; do
  if python3 - <<PY >>"$LOG" 2>&1
import duckdb
con = duckdb.connect("$DB", read_only=True)
print("lock ok", con.execute("SELECT 1").fetchone()[0])
con.close()
PY
  then
    log "DuckDB read_only available"
    break
  fi
  log "DB locked — retry in 120s"
  sleep 120
done

{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=uploading"
  echo "out=$OUT"
  echo "tables=$TABLES"
} >"$STATUS"

set +e
caffeinate -i -s python3 -u upload_to_hf.py --db "$DB" --out "$OUT" --tables "$TABLES" >>"$LOG" 2>&1
rc=$?
set -e

{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=$([[ $rc -eq 0 ]] && echo success || echo failed)"
  echo "exit_code=$rc"
  echo "out=$OUT"
  echo "tables=$TABLES"
} >"$STATUS"
log "=== HF upload END rc=$rc ==="
echo "done rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit $rc
