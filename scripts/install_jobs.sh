#!/usr/bin/env bash
# install_jobs.sh — install the Vaultwright launchd jobs (USE_CASES.md UC-7, UC-8).
#   com.vaultwright.digest  — weekly digest (Mondays 08:00)
#   com.vaultwright.backup  — vault git autosave (hourly)
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS" "$ROOT/logs"

for job in digest backup; do
  src="$ROOT/launchd/com.vaultwright.${job}.plist.template"
  dst="$LAUNCH_AGENTS/com.vaultwright.${job}.plist"
  sed "s|__VAULTWRIGHT_HOME__|$ROOT|g" "$src" > "$dst"
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "installed: $dst"
done

echo
echo "Done. Installed jobs:"
echo "  com.vaultwright.digest  — weekly digest (Mondays 08:00)"
echo "  com.vaultwright.backup  — vault git autosave (hourly)"
echo "Remove with: launchctl unload ~/Library/LaunchAgents/com.vaultwright.*.plist"
