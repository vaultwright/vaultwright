#!/usr/bin/env bash
# run.sh — activate the venv and run a Vaultwright module.
#   bash scripts/run.sh bot       # start the Telegram capture bot
#   bash scripts/run.sh digest    # build + send the weekly digest
#   bash scripts/run.sh commit    # commit the vault to git (UC-8 backup)
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
PYTHON="$ROOT/.venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
  echo "No .venv — run scripts/setup.sh first" >&2
  exit 1
fi

# Include _shared/ so shared.llm / shared.text are importable (dogfood path).
# If _shared/ is absent (open-core install), vaultwright falls back to its own
# inline implementations — no error.
SHARED_ROOT="$HOME/projects/_shared"
if [ -d "$SHARED_ROOT" ]; then
  PYTHONPATH="$ROOT/src:$SHARED_ROOT"
else
  PYTHONPATH="$ROOT/src"
fi
export PYTHONPATH

# 'commit' is a bash script, not a Python module — route it directly.
if [ "${1:-}" = "commit" ]; then
  exec bash "$ROOT/scripts/git_autocommit.sh" "${@:2}"
fi

exec "$PYTHON" -m "vaultwright.${1:-bot}" "${@:2}"
