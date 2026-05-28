#!/usr/bin/env bash
# git_autocommit.sh — commit the vault to git on a schedule (USE_CASES.md UC-8).
#
# The vault path is read from config/domains.yaml. Initialises a git repo in the
# vault on first run. Pushes if a remote is configured. No-op when nothing changed.
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

# config.load() respects VAULTWRIGHT_CONFIG env var — pass it through so the
# correct vault path is used when running with a personal config.
VAULT="$(PYTHONPATH="$ROOT/src" VAULTWRIGHT_CONFIG="${VAULTWRIGHT_CONFIG:-}" python3 -c 'from vaultwright import config; print(config.load().vault_path)')"
if [ ! -d "$VAULT" ]; then
  echo "vault not found: $VAULT — run scripts/setup.sh first" >&2
  exit 1
fi
cd "$VAULT"

[ -d .git ] || git init --quiet

git add -A
if git diff --cached --quiet; then
  exit 0   # nothing changed — no empty commit
fi
git commit --quiet -m "vault: autosave $(date '+%Y-%m-%d %H:%M')"

if git remote | grep -q .; then
  git push --quiet 2>/dev/null || true
fi
