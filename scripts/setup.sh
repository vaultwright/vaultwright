#!/usr/bin/env bash
# setup.sh — one-time Vaultwright setup: venv, dependencies, vault skeleton.
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$ROOT"

echo "[1/3] Creating virtual environment (.venv)…"
python3 -m venv .venv
# Use the venv Python explicitly — avoids CommandLineTools python3 shadowing
PYTHON="$ROOT/.venv/bin/python3"
PIP="$ROOT/.venv/bin/pip"
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r requirements.txt
echo "  deps installed (anthropic, PyYAML, python-telegram-bot)"

# _shared/ is on PYTHONPATH at runtime (run.sh / test.sh) — no pip install needed.
# Editable installs of _shared/ are skipped here to avoid setuptools version conflicts.
SHARED_ROOT="$HOME/projects/_shared"
if [ -d "$SHARED_ROOT" ]; then
  echo "  _shared/ found at $SHARED_ROOT — will be on PYTHONPATH at runtime"
else
  echo "  _shared/ not found — standalone mode"
fi

echo "[2/3] Checking config…"
if [ ! -f config/domains.yaml ]; then
  echo "  config/domains.yaml is missing — restore it from the repo template." >&2
  exit 1
fi

echo "[3/3] Creating vault skeleton from config/domains.yaml…"
PYTHONPATH="$ROOT/src" "$PYTHON" -m vaultwright.scaffold

echo
echo "Setup complete. Next: follow docs/SETUP.md to create your Telegram bot,"
echo "fill in .env, then start the bot with:  bash scripts/run.sh bot"
