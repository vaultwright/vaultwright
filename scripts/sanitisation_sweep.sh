#!/usr/bin/env bash
# sanitisation_sweep.sh — pre-publish personal-data sweep for Vaultwright.
#
# Fails (exit 1) if any personal identifier from the source AI-OS appears in the
# repo. Run before every publish; also runs in CI. Rationale: the project's
# SANITISATION_AUDIT.md (§6 — mandatory pre-publish verification).
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$ROOT"

# Identifiers that must never appear in the published kit.
PATTERNS=(
  rasta kanocz rkanocz forman coreteq osvc profiq niseko ondra
  csob revolut assioma garmin strava trainingpeaks "rasta-claw"
)

FAIL=0
echo "Vaultwright sanitisation sweep — scanning $ROOT"

# Excluded by name — none of these are part of the published repo:
#  - this script itself (it holds the denylist);
#  - BUILD_NOTES.md (a dev-only file documenting the publishing rules);
#  - .claude/ — agent-team workflow, gitignored, never shipped;
#  - CLAUDE.md — operator persistent memory, gitignored, never shipped;
#  - personal dogfood configs (domains.*.yaml) — gitignored, never shipped, so
#    CI (which checks out only committed files) never sees them. The shipped
#    template config/domains.yaml has only one dot, is NOT matched by the
#    domains.*.yaml glob, and is still scanned.
EXCLUDES=(--exclude=sanitisation_sweep.sh --exclude=BUILD_NOTES.md
          --exclude=CLAUDE.md
          --exclude='domains.*.yaml')

for pat in "${PATTERNS[@]}"; do
  hits=$(grep -rniI "$pat" . \
            --exclude-dir=.git --exclude-dir=.venv --exclude-dir=venv --exclude-dir=.claude \
            "${EXCLUDES[@]}" 2>/dev/null || true)
  if [ -n "$hits" ]; then
    echo "FAIL: identifier '$pat' found:"
    echo "$hits" | sed 's/^/  /'
    FAIL=1
  fi
done

# Absolute home paths must not be hardcoded.
hits=$(grep -rnI "/Users/" . \
          --exclude-dir=.git --exclude-dir=.venv --exclude-dir=venv --exclude-dir=.claude \
          "${EXCLUDES[@]}" 2>/dev/null || true)
if [ -n "$hits" ]; then
  echo "FAIL: hardcoded /Users/ path found:"
  echo "$hits" | sed 's/^/  /'
  FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  echo "SWEEP FAILED — do not publish. Strip the hits above."
  exit 1
fi
echo "SWEEP PASSED — no personal identifiers found."
