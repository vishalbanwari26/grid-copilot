#!/usr/bin/env bash
# Build the static GitHub Pages demo into web/dist.
# Needs a recording first: see scripts/record_demo.py (writes web/demo-data).
set -euo pipefail
cd "$(dirname "$0")/../web"
test -d demo-data || { echo "web/demo-data is missing: run scripts/record_demo.py first" >&2; exit 1; }
VITE_STATIC=1 VITE_BASE="${VITE_BASE:-/grid-copilot/}" npm run build
cp -R demo-data dist/demo-data
touch dist/.nojekyll
echo "built web/dist ($(du -sh dist | cut -f1))"
