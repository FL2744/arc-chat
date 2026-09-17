#!/bin/sh
set -eu
cd "$(dirname "$0")"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
exec .venv/bin/python helper.py
