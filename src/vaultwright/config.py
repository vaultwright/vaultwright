"""
config.py — Vaultwright configuration loader.

Single source of truth for paths, domains, intents, and tunables. Everything is
driven by config/domains.yaml, so a user shapes the whole system without touching
code (see USE_CASES.md UC-6 — "bring your own domains").
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Repo root = two levels up from this file: src/vaultwright/config.py -> repo/
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "config" / "domains.yaml"


@dataclass
class Config:
    """Resolved Vaultwright configuration."""

    vault_path: Path
    domains: dict          # name -> {"description": str}
    intents: dict          # name -> description str
    confidence_threshold: float
    projects: dict = field(default_factory=dict)   # raw `projects:` block (USE_CASES UC-13)
    exclude: list = field(default_factory=list)    # extra dir names kept out of the search corpus
    raw: dict = field(default_factory=dict, repr=False)

    def inbox(self, domain: str) -> Path:
        """Absolute path to a domain's inbox folder."""
        if domain not in self.domains:
            raise KeyError(
                f"Unknown domain '{domain}'. Known: {sorted(self.domains)}"
            )
        return self.vault_path / domain / "inbox"

    def digests_dir(self) -> Path:
        """Absolute path to the vault's digests folder (USE_CASES UC-7)."""
        return self.vault_path / "digests"

    # ── projects layer (USE_CASES UC-13) ────────────────────────────────────
    # Backward compatible: when the `projects:` block is absent, projects_enabled
    # is False and every project-aware code path is skipped — the system behaves
    # exactly as it did before UC-13.
    @property
    def projects_enabled(self) -> bool:
        """True when the config opts the projects layer in."""
        return bool(self.projects.get("enabled", False))

    def projects_root(self) -> Path:
        """Absolute path to the vault's projects/ folder (one subfolder per project)."""
        return self.vault_path / str(self.projects.get("path", "projects"))

    @property
    def staleness_days(self) -> int:
        """A canonical project doc older than this triggers a soft staleness note."""
        return int(self.projects.get("staleness_days", 14))

    @property
    def project_context_budget(self) -> int:
        """Max chars of project docs fed to the LLM per project-scoped answer."""
        return int(self.projects.get("context_budget", 28000))

    # ── search corpus hygiene ────────────────────────────────────────────────
    @property
    def exclude_dirs(self) -> set[str]:
        """Directory names skipped by the note walk (`search.iter_notes`).

        Always skips dot-directories and other universal non-content folders; the
        optional `exclude:` config list adds vault-specific ones (e.g. a
        raw-import staging folder whose duplicate copies would pollute results).
        A note is skipped when any component of its path matches a name here.
        """
        universal = {".obsidian", ".trash", ".git", "node_modules"}
        return universal | {str(d).strip() for d in self.exclude if str(d).strip()}


def load(config_file: Path | None = None) -> Config:
    """Load and validate config/domains.yaml into a Config.

    Resolution order (first wins):
      1. Explicit ``config_file`` argument
      2. ``VAULTWRIGHT_CONFIG`` environment variable
      3. ``<repo>/config/domains.yaml`` (built-in default)

    This means ``VAULTWRIGHT_CONFIG=config/domains.personal.yaml bash scripts/setup.sh``
    scaffolds vault folders in the personal vault, not the template ``~/vault``.
    """
    if config_file is not None:
        path = Path(config_file)
    else:
        env_cfg = os.environ.get("VAULTWRIGHT_CONFIG", "").strip()
        path = Path(env_cfg) if env_cfg else CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config not found: {path}\n"
            f"Copy the domains.yaml template from the repo and edit it."
        )

    data = yaml.safe_load(path.read_text()) or {}

    vault_path = Path(os.path.expanduser(str(data.get("vault_path", "~/vault"))))
    vault_path = vault_path.resolve()

    domains = data.get("domains") or {}
    if not domains:
        raise ValueError(f"No domains defined in {path}. Define at least one.")

    intents = data.get("intents") or {}
    threshold = float(data.get("confidence_threshold", 0.70))
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"confidence_threshold must be 0.0–1.0, got {threshold}")

    # Optional projects: block (USE_CASES UC-13). Absent -> {} -> feature disabled.
    projects = data.get("projects") or {}

    # Optional exclude: list — extra directory names kept out of the search corpus.
    exclude = data.get("exclude") or []

    return Config(
        vault_path=vault_path,
        domains=domains,
        intents=intents,
        confidence_threshold=threshold,
        projects=projects,
        exclude=exclude,
        raw=data,
    )


if __name__ == "__main__":
    cfg = load()
    print(f"vault_path: {cfg.vault_path}")
    print(f"domains:    {', '.join(sorted(cfg.domains))}")
    print(f"intents:    {', '.join(sorted(cfg.intents))}")
    print(f"threshold:  {cfg.confidence_threshold}")
    print(f"projects:   {'enabled' if cfg.projects_enabled else 'disabled'}"
          + (f" -> {cfg.projects_root()}" if cfg.projects_enabled else ""))
