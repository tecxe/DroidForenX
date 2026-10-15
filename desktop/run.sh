#!/usr/bin/env bash
# Quick-launch script for development (no build required).
# Works on Linux (Kali, Debian, Ubuntu, Fedora) and macOS.
set -euo pipefail

cd "$(dirname "$0")/.."

# Prefer the project virtualenv; fall back to whatever python3 is on PATH.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
else
    echo "No Python interpreter found. Install python3 or create .venv." >&2
    exit 1
fi

# Bundle official Android platform-tools on first run if adb is not available.
if [ ! -x "tools/platform-tools/adb" ] && ! command -v adb >/dev/null 2>&1; then
    echo "adb not found; fetching official Android platform-tools..." >&2
    "$PY" setup_adb.py || echo "adb install failed - continue if adb is on PATH" >&2
fi

exec "$PY" -m desktop.main "$@"