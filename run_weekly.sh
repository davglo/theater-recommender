#!/bin/bash
# run_weekly.sh — one weekly cycle: profile refresh → pool → score → dashboard.
# Self-contained; safe to run by hand or from launchd (Monday 08:00).
#   bash run_weekly.sh              # full run
#   bash run_weekly.sh --dry-run    # print pool + prompt, write nothing

set -uo pipefail
HOME_DIR="/Users/davidglogoza/Claude/theater-recommender"
PY="/usr/bin/python3"
cd "$HOME_DIR" || exit 1
mkdir -p output/logs

LOG="output/logs/run_weekly.sh.log"

# Cap log growth: trim fixed-name append logs past ~500KB to their last 500 lines.
for f in "$LOG" output/logs/launchd.out.log output/logs/launchd.err.log; do
  if [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 512000 ]; then
    tail -n 500 "$f" > "$f.trim" && mv "$f.trim" "$f"
  fi
done

{
  echo "===================="
  echo "Run: $(date)"
  "$PY" run_weekly.py "$@" || { echo "weekly run FAILED"; exit 1; }
  echo "Done: $(date)"
} >> "$LOG" 2>&1
