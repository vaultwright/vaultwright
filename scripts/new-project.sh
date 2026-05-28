#!/usr/bin/env bash
# new-project.sh — scaffold a new project folder in the vault (USE_CASES UC-13).
#   bash scripts/new-project.sh <slug> "<Display Name>"
#
# <slug> must be lowercase kebab-case, e.g. q3-website-relaunch.
# The display name is optional — it defaults to the de-slugged slug.
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
PYTHON="$ROOT/.venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
  echo "No .venv — run scripts/setup.sh first" >&2
  exit 1
fi

SLUG="${1:-}"
NAME="${2:-}"
if [ -z "$SLUG" ]; then
  echo "Usage: bash scripts/new-project.sh <slug> \"<Display Name>\"" >&2
  echo "Example: bash scripts/new-project.sh q3-website-relaunch \"Q3 Website Relaunch\"" >&2
  exit 1
fi

# Include _shared/ on the path for parity with the other scripts (the projects
# layer itself needs only the standard library + PyYAML).
SHARED_ROOT="$HOME/projects/_shared"
if [ -d "$SHARED_ROOT" ]; then
  PYTHONPATH="$ROOT/src:$SHARED_ROOT"
else
  PYTHONPATH="$ROOT/src"
fi
export PYTHONPATH

if [ -n "$NAME" ]; then
  exec "$PYTHON" -m vaultwright.scaffold --project "$SLUG" --name "$NAME"
else
  exec "$PYTHON" -m vaultwright.scaffold --project "$SLUG"
fi
