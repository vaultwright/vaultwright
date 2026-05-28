#!/usr/bin/env bash
# test.sh — run offline tests with _shared/ on the path.
# Usage: bash scripts/test.sh
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
PYTHON="$ROOT/.venv/bin/python3"
SHARED_ROOT="$HOME/projects/_shared"

if [ ! -f "$PYTHON" ]; then
  echo "No .venv — run scripts/setup.sh first" >&2
  exit 1
fi

# Build PYTHONPATH — explicit venv Python, _shared/ if present
if [ -d "$SHARED_ROOT" ]; then
  PYTHONPATH="$ROOT/src:$SHARED_ROOT"
else
  PYTHONPATH="$ROOT/src"
fi
export PYTHONPATH

echo "Python: $("$PYTHON" --version)"
echo "PYTHONPATH: $PYTHONPATH"
echo ""

echo "=== capture tests ==="
"$PYTHON" "$ROOT/tests/test_capture.py"
echo "=== query tests ==="
"$PYTHON" "$ROOT/tests/test_query.py"
echo "=== all tests passed ==="
