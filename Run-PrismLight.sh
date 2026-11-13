#!/bin/sh
set -eu
PRISM_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$PRISM_ROOT/.venv/bin/python" ]; then
    echo 'Set up first: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt'
    exit 1
fi
if [ ! -f "$PRISM_ROOT/vendor/openrgb/manifest.json" ]; then
    "$PRISM_ROOT/.venv/bin/python" "$PRISM_ROOT/tools/fetch_openrgb.py"
fi
exec "$PRISM_ROOT/.venv/bin/python" "$PRISM_ROOT/main.py" "$@"
