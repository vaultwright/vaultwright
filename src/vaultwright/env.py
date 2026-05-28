"""
env.py — minimal .env loader (stdlib only).

Loads KEY=VALUE pairs into os.environ without overwriting existing values.
Avoids a hard dependency on python-dotenv.

Search order (first file found wins):
  1. Explicit path passed to load_env()
  2. VAULTWRIGHT_ENV environment variable
  3. <vaultwright-repo-root>/.env        (standard open-core install)
  4. Three levels up from repo root       (dogfood: self-funding project root)
"""
from __future__ import annotations

import os
from pathlib import Path

# Two levels up from src/vaultwright/ → the vaultwright repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

def _dogfood_env_path() -> Path:
    """`.env` three levels above the repo root — the self-funding project root
    when Vaultwright is dogfooded inside it.

    Guarded: a standalone open-core install can sit too shallow in the
    filesystem for `parents[2]` to exist (e.g. `/opt/vaultwright`). Indexing it
    unguarded raises IndexError at import time and crashes the program before it
    runs, so fall back to the /dev/null sentinel (never matched as a file) when
    there is no such ancestor.
    """
    parents = REPO_ROOT.parents
    return parents[2] / ".env" if len(parents) >= 3 else Path("/dev/null")


# Candidate paths tried in order when no explicit path is given.
_CANDIDATES: list[Path] = [
    Path(os.environ.get("VAULTWRIGHT_ENV", "")) if os.environ.get("VAULTWRIGHT_ENV") else Path("/dev/null"),
    REPO_ROOT / ".env",
    _dogfood_env_path(),   # ~/projects/self-funding/.env when dogfooding
]


def _parse_env(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines, ignoring comments and blank lines."""
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def load_env(path: Path | None = None) -> None:
    """Load .env into os.environ (does not overwrite existing vars).

    Pass an explicit path to skip the search. Otherwise the first existing
    candidate in _CANDIDATES is used.
    """
    if path is not None:
        candidates = [path]
    else:
        candidates = _CANDIDATES

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            for key, value in _parse_env(candidate.read_text(encoding="utf-8")).items():
                os.environ.setdefault(key, value)
            return  # first match wins
