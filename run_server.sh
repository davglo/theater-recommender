#!/bin/bash
# run_server.sh — write-back dashboard server on 127.0.0.1:8757.
# Run by hand or as an always-on launchd service (KeepAlive).

set -uo pipefail
HOME_DIR="/Users/davidglogoza/Claude/theater-recommender"
PY="/usr/bin/python3"
cd "$HOME_DIR" || exit 1
mkdir -p output/logs
exec "$PY" server.py
