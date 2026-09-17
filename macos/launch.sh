#!/bin/sh
set -eu
RESOURCES=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$candidate" ] && "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
    exec "$candidate" "$RESOURCES/launcher.py"
  fi
done
/usr/bin/osascript -e 'display dialog "ARC Chat requires Python 3.10 or newer. Install Python from python.org, then open ARC Chat again." with title "ARC Chat" buttons {"OK"} default button "OK"'
