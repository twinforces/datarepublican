#!/usr/bin/env bash
# CHECKPOINT Data irs990.duckdb, then ditto to Insurrection.
# Refuse if dest is larger than src. Do not run while a writer holds Data.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
TS=$(date +%Y%m%d_%H%M%S)
LOG="${COPY_LOG:-$ROOT/logs/overnight_duckdb_copy_${TS}.log}"
STATUS="$ROOT/overnight_duckdb_copy_status.txt"
PIDFILE="$ROOT/overnight_duckdb_copy.pid"
SRC="${SRC:-/Volumes/Data/final/irs990.duckdb}"
DEST="${DEST:-/Volumes/Insurrection/990data/irs990.duckdb}"

mkdir -p "$ROOT/logs" "$(dirname "$DEST")"
echo $$ >"$PIDFILE"
{
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=running"
  echo "src=$SRC"
  echo "dest=$DEST"
} >"$STATUS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
log "=== DuckDB copy START pid=$$ ==="
log "src=$SRC dest=$DEST"

src_size=$(stat -f %z "$SRC")
dest_size=0
if [ -f "$DEST" ]; then
  dest_size=$(stat -f %z "$DEST")
fi
log "src_size=$src_size dest_size=$dest_size"

if [ "$dest_size" -gt "$src_size" ]; then
  log "REFUSE dest ($dest_size) > src ($src_size)"
  {
    echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
    echo "pid=$$"
    echo "log=$LOG"
    echo "status=failed"
    echo "exit_code=2"
  } >"$STATUS"
  exit 2
fi

log "CHECKPOINT $SRC"
python3 - <<PY >>"$LOG" 2>&1
import duckdb
con = duckdb.connect("$SRC")
con.execute("CHECKPOINT")
con.close()
print("checkpoint ok")
PY
rc=$?
if [ "$rc" -ne 0 ]; then
  log "CHECKPOINT failed rc=$rc"
  {
    echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
    echo "pid=$$"
    echo "log=$LOG"
    echo "status=failed"
    echo "exit_code=$rc"
  } >"$STATUS"
  exit "$rc"
fi

src_size=$(stat -f %z "$SRC")
log "post-checkpoint src_size=$src_size"
log "remove dest + wal"
rm -f "$DEST" "${DEST}.wal"
log "ditto $SRC → $DEST"
set +e
caffeinate -i -s ditto "$SRC" "$DEST" >>"$LOG" 2>&1
rc=$?
set -e
dest_size=$(stat -f %z "$DEST" 2>/dev/null || echo 0)
log "ditto rc=$rc dest_size=$dest_size src_size=$src_size"
if [ "$rc" -ne 0 ] || [ "$dest_size" != "$src_size" ]; then
  log "copy size mismatch or ditto failed"
  {
    echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
    echo "pid=$$"
    echo "log=$LOG"
    echo "status=failed"
    echo "exit_code=${rc:-3}"
    echo "src_size=$src_size"
    echo "dest_size=$dest_size"
  } >"$STATUS"
  exit "${rc:-3}"
fi

{
  echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "pid=$$"
  echo "log=$LOG"
  echo "status=success"
  echo "exit_code=0"
  echo "src_size=$src_size"
  echo "dest_size=$dest_size"
} >"$STATUS"
log "=== DuckDB copy END rc=0 ==="
echo "done rc=0 $(date '+%Y-%m-%d %H:%M:%S')" >>"$PIDFILE"
exit 0
