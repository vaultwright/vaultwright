"""
scaffold.py — create the vault structure from config/domains.yaml.

Two jobs, one module:
- `scaffold()`        — the vault skeleton: a folder + inbox/ per domain, the
                        digests/ folder, and (UC-13) the empty projects/ root.
                        Run via scripts/setup.sh after editing domains.yaml.
- `scaffold_project()`— a single new project folder with INDEX.md + STATE.md
                        pre-filled from templates (USE_CASES.md UC-13). Run via
                        scripts/new-project.sh.

Idempotent: creates what is missing, never deletes, never overwrites an existing
project.
"""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from . import config, projects

# Project templates ship in the repo at templates/project/.
TEMPLATE_DIR = config.REPO_ROOT / "templates" / "project"


# ── vault skeleton ───────────────────────────────────────────────────────────
def scaffold(cfg: config.Config | None = None) -> None:
    """Create <vault>/<domain>/inbox/ for every domain, plus digests/ and (when
    the projects layer is enabled) the empty projects/ root."""
    cfg = cfg or config.load()
    created = []

    for domain in sorted(cfg.domains):
        inbox = cfg.inbox(domain)
        if not inbox.exists():
            created.append(inbox)
        inbox.mkdir(parents=True, exist_ok=True)

    digests = cfg.digests_dir()
    if not digests.exists():
        created.append(digests)
    digests.mkdir(parents=True, exist_ok=True)

    # USE_CASES UC-13 — the projects/ root. Projects themselves are added later
    # with scaffold_project(); setup just makes sure the parent folder exists.
    if cfg.projects_enabled:
        projects_root = cfg.projects_root()
        if not projects_root.exists():
            created.append(projects_root)
        projects_root.mkdir(parents=True, exist_ok=True)

    print(f"Vault: {cfg.vault_path}")
    if created:
        for path in created:
            print(f"  created  {path}")
    else:
        print("  all folders already present")
    print(f"Domains: {', '.join(sorted(cfg.domains))}")
    if cfg.projects_enabled:
        print(f"Projects: enabled -> {cfg.projects_root()}")


# ── single project ───────────────────────────────────────────────────────────
def _render_template(filename: str, fields: dict) -> str:
    """Read templates/project/<filename> and substitute {{KEY}} placeholders."""
    text = (TEMPLATE_DIR / filename).read_text(encoding="utf-8")
    for key, value in fields.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def scaffold_project(
    slug: str,
    name: str | None = None,
    cfg: config.Config | None = None,
) -> Path:
    """Create a new project folder under <vault>/projects/ from templates.

    Writes INDEX.md + STATE.md with frontmatter pre-filled (slug, project name,
    created / last_updated dates, status: active). Idempotent — if the project
    already exists it is left untouched. Returns the project folder path.

    The slug must be lowercase kebab-case and not made only of generic
    stopwords (so a question can actually resolve to it — SPEC §6, §9).
    """
    cfg = cfg or config.load()
    if not cfg.projects_enabled:
        raise SystemExit(
            "The projects layer is disabled. Add a `projects:` block with "
            "`enabled: true` to config/domains.yaml, then re-run setup."
        )

    slug = (slug or "").strip().lower()
    ok, err = projects.validate_slug(slug)
    if not ok:
        raise SystemExit(f"Cannot create project: {err}")

    display = (name or projects.deslug(slug).title()).strip().replace('"', "'")
    proj_dir = cfg.projects_root() / slug
    index_path = proj_dir / "INDEX.md"
    if index_path.exists():
        print(f"Project '{slug}' already exists at {proj_dir} — nothing to do.")
        return proj_dir

    proj_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    fields = {
        "SLUG": slug,
        "PROJECT": display,
        "ALIASES": projects.deslug(slug),
        "CREATED": today,
        "LAST_UPDATED": today,
    }
    for filename in ("INDEX.md", "STATE.md"):
        (proj_dir / filename).write_text(
            _render_template(filename, fields), encoding="utf-8"
        )

    print(f"Created project '{slug}' at {proj_dir}")
    print("  INDEX.md + STATE.md written.")
    print("  Next: edit the aliases in INDEX.md, then add a canonical doc from")
    print("  templates/project/DOC.md (e.g. plan.md).")
    return proj_dir


# ── CLI ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the vault skeleton, or scaffold a single project.",
    )
    parser.add_argument(
        "--project", metavar="SLUG",
        help="scaffold a new project with this slug (USE_CASES UC-13)",
    )
    parser.add_argument(
        "--name", metavar="NAME",
        help="display name for the new project (used with --project)",
    )
    args = parser.parse_args()

    if args.project:
        scaffold_project(args.project, args.name)
    else:
        scaffold()


if __name__ == "__main__":
    main()
