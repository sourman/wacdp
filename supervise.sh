#!/usr/bin/env bash
# Respawns daemon.py forever. Single supervise instance via flock.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/supervise.log"
LOCK="$DIR/supervise.lock"
DAEMON="$DIR/daemon.py"
BACKOFF=2
MAX_BACKOFF=60

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date '+%F %T') supervise already running; exit" >>"$LOG"
  exit 0
fi
echo $$ >"$DIR/supervise.pid"
echo "$(date '+%F %T') supervise start pid=$$" >>"$LOG"

backoff=$BACKOFF
while true; do
  echo "$(date '+%F %T') launching daemon" >>"$LOG"
  set +e
  WA_LISTEN_LOG_FILE_ONLY=1 /usr/bin/python3 "$DAEMON" >>"$DIR/daemon.log" 2>&1
  code=$?
  set -e
  echo "$(date '+%F %T') daemon exited code=$code; restart in ${backoff}s" >>"$LOG"
  # exit 0 from flock contention is fine — don't spin hot
  if [[ "$code" -eq 0 ]]; then
    backoff=$BACKOFF
  fi
  sleep "$backoff"
  if [[ "$code" -ne 0 ]]; then
    backoff=$(( backoff * 2 ))
    if [[ "$backoff" -gt "$MAX_BACKOFF" ]]; then backoff=$MAX_BACKOFF; fi
  else
    backoff=$BACKOFF
  fi
done
