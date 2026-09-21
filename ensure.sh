#!/usr/bin/env bash
# Idempotent: ensure supervise (and thus daemon) is running.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if flock -n "$DIR/supervise.lock" -c true 2>/dev/null; then
  # lock free → supervise not running
  nohup "$DIR/supervise.sh" >/dev/null 2>&1 &
  disown || true
  echo "started supervise"
else
  echo "supervise already running"
fi
# show pids
[[ -f "$DIR/supervise.pid" ]] && echo "supervise.pid=$(cat "$DIR/supervise.pid")"
[[ -f "$DIR/daemon.pid" ]] && echo "daemon.pid=$(cat "$DIR/daemon.pid")"
pgrep -af 'wa-listen/(supervise.sh|daemon.py)' | grep -v ensure || true
