"""
projects.py — the projects layer (USE_CASES.md UC-13).

A project is a folder under <vault>/projects/. This module discovers projects,
resolves a free-text question to the project it refers to, and assembles the set
of documents to load for a project-scoped answer.

It is the read-side counterpart to UC-6 ("bring your own domains") — "bring your
own projects". Channel-agnostic and config-driven, the same contract as
search.py / query.py: nothing here knows about Telegram, so it imports cleanly
into any host.

Tier 1 by design (SPEC §13): lexical resolution, whole-document retrieval — no
embeddings, no chunking, no index. Read-only: this module never writes a vault.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .search import read_frontmatter

# A project alias that is a single one of these words would match almost any
# message — such slugs are rejected at scaffold time and such aliases are
# ignored by the resolver. Period markers (q1..q4, h1, h2, fy) are dropped as
# standalone match tokens for the same reason.
_GENERIC_ALIASES = frozenset("""
project projects plan plans doc docs document documents index state note notes
task tasks work thing things stuff item items idea ideas todo log logs file
files folder update updates new current main misc general overview
q1 q2 q3 q4 h1 h2 fy
""".split())

# A single slug/name token shorter than this is not used as a standalone match
# phrase — too generic on its own. The full slug, the de-slugged slug, the
# display name, and every multi-word alias are always matched regardless.
_MIN_TOKEN_LEN = 4

_VALID_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ── data model (SPEC §6) ─────────────────────────────────────────────────────
@dataclass
class ProjectDoc:
    """One Markdown document directly inside a project folder."""

    path: Path
    relpath: str               # vault-relative path — used as the citation
    type: str                  # project-index | project-state | project-doc
    canonical: bool            # True -> force-loaded into a project answer
    last_updated: str | None   # ISO date string, or None when absent/unparsed


@dataclass
class Project:
    """A discovered project: its identity, status, and retrieval set."""

    slug: str
    name: str
    status: str                # active | paused | done | archived
    aliases: list[str]
    root: Path
    index: ProjectDoc
    state: ProjectDoc | None
    # INDEX + STATE + every canonical project-doc, minus any superseded doc.
    canonical_docs: list[ProjectDoc] = field(default_factory=list)
    # Vault-relative paths pruned by a `supersedes` field — excluded from ALL
    # project retrieval (canonical set and supporting search alike), so a stale
    # duplicate can never resurface through the side door (SPEC §4.3).
    superseded: list[str] = field(default_factory=list)


# ── slug helpers ─────────────────────────────────────────────────────────────
def deslug(slug: str) -> str:
    """Turn a kebab/snake-case slug into a space-separated phrase."""
    return re.sub(r"[-_]+", " ", str(slug)).strip()


def slug_tokens(text: str) -> list[str]:
    """Split a slug or name into lowercase word tokens."""
    return [t for t in re.split(r"[-_\s]+", str(text).lower()) if t]


def validate_slug(slug: str) -> tuple[bool, str]:
    """Check a project slug is a usable, resolvable identifier.

    Returns (ok, error_message). Rejects an empty or non-kebab-case slug, and a
    slug whose every word is a generic stopword — such a project could not be
    resolved without false hits (SPEC §6, §9).
    """
    s = (slug or "").strip().lower()
    if not s:
        return False, "slug is empty"
    if not _VALID_SLUG_RE.match(s):
        return False, (
            f"slug '{slug}' must be lowercase kebab-case — letters, digits and "
            f"single hyphens only, e.g. q3-website-relaunch"
        )
    if all(tok in _GENERIC_ALIASES for tok in slug_tokens(s)):
        return False, (
            f"slug '{slug}' is too generic — every word in it is a stopword. "
            f"Pick a slug with a distinctive word so questions can resolve to it."
        )
    return True, ""


# ── discovery ────────────────────────────────────────────────────────────────
def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _clean_aliases(raw) -> list[str]:
    """Normalise a frontmatter `aliases` value into a deduped lowercase list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for alias in raw:
        s = str(alias).strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def _collect_supersedes(fm: dict, acc: set[str]) -> None:
    """Add a doc's `supersedes` entries (and their basenames) to `acc`."""
    raw = fm.get("supersedes") or []
    if isinstance(raw, str):
        raw = [raw]
    for entry in raw:
        s = str(entry).strip()
        if s:
            acc.add(s)
            acc.add(Path(s).name)


def _is_superseded(doc: ProjectDoc, keys: set[str]) -> bool:
    """True when another doc's `supersedes` field names this one."""
    return doc.relpath in keys or doc.path.name in keys


def _make_doc(path: Path, cfg: Config, doc_type: str,
              canonical: bool, fm: dict) -> ProjectDoc:
    try:
        relpath = str(path.relative_to(cfg.vault_path))
    except ValueError:
        relpath = path.name
    last_updated = fm.get("last_updated")
    return ProjectDoc(
        path=path,
        relpath=relpath,
        type=doc_type,
        canonical=canonical,
        # yaml parses an unquoted ISO date to a date object — normalise to str.
        last_updated=str(last_updated).strip() if last_updated else None,
    )


def _build_project(slug: str, proj_dir: Path, cfg: Config) -> Project | None:
    """Assemble one Project from a project folder. None when INDEX.md is absent."""
    index_path = proj_dir / "INDEX.md"
    if not index_path.is_file():
        return None

    index_fm = read_frontmatter(_read(index_path))
    name = str(index_fm.get("project") or deslug(slug)).strip() or slug
    status = str(index_fm.get("status") or "active").strip().lower()
    aliases = _clean_aliases(index_fm.get("aliases"))
    index_doc = _make_doc(index_path, cfg, "project-index", True, index_fm)

    superseded: set[str] = set()
    _collect_supersedes(index_fm, superseded)

    state_doc: ProjectDoc | None = None
    state_path = proj_dir / "STATE.md"
    if state_path.is_file():
        state_fm = read_frontmatter(_read(state_path))
        _collect_supersedes(state_fm, superseded)
        state_doc = _make_doc(state_path, cfg, "project-state", True, state_fm)

    other_docs: list[ProjectDoc] = []
    for md in sorted(proj_dir.glob("*.md")):
        if md.name in ("INDEX.md", "STATE.md"):
            continue
        fm = read_frontmatter(_read(md))
        _collect_supersedes(fm, superseded)
        doc_type = str(fm.get("type") or "project-doc").strip().lower()
        canonical = bool(fm.get("canonical", False))
        other_docs.append(_make_doc(md, cfg, doc_type, canonical, fm))

    # The canonical retrieval set: INDEX + STATE (canonical implicitly) + every
    # canonical project-doc, with superseded docs removed (SPEC §4, §6).
    canonical_docs: list[ProjectDoc] = [index_doc]
    if state_doc is not None:
        canonical_docs.append(state_doc)
    superseded_relpaths: list[str] = []
    for doc in other_docs:
        if _is_superseded(doc, superseded):
            superseded_relpaths.append(doc.relpath)
        elif doc.canonical:
            canonical_docs.append(doc)

    return Project(
        slug=slug,
        name=name,
        status=status,
        aliases=aliases,
        root=proj_dir,
        index=index_doc,
        state=state_doc,
        canonical_docs=canonical_docs,
        superseded=superseded_relpaths,
    )


def load_projects(cfg: Config) -> dict[str, Project]:
    """Scan <vault>/projects/*/INDEX.md and build slug -> Project.

    Cheap — a directory glob plus a small frontmatter read per project; safe to
    call once per query. Returns {} when the projects layer is disabled or the
    projects/ folder does not exist (SPEC §6).
    """
    out: dict[str, Project] = {}
    if not cfg.projects_enabled:
        return out
    root = cfg.projects_root()
    if not root.is_dir():
        return out
    for index_path in sorted(root.glob("*/INDEX.md")):
        proj_dir = index_path.parent
        project = _build_project(proj_dir.name, proj_dir, cfg)
        if project is not None:
            out[project.slug] = project
    return out


# ── resolution ───────────────────────────────────────────────────────────────
def _match_phrases(project: Project) -> list[str]:
    """Every phrase that, found in a question, resolves to this project.

    Always included: the full slug, the de-slugged slug, the display name, and
    every alias. Individual slug/name tokens are included only when distinctive
    — long enough and not a generic stopword — so a project never grabs a
    question on a word like "plan" or "q3". Sorted longest-first.
    """
    phrases: set[str] = set()
    slug = project.slug.lower()
    phrases.add(slug)
    phrases.add(deslug(slug))
    name = project.name.lower().strip()
    if name:
        phrases.add(name)
    for alias in project.aliases:
        # Drop a single-word generic alias (defensive — scaffold rejects these,
        # but an INDEX.md can be hand-edited after the fact).
        if " " not in alias and alias in _GENERIC_ALIASES:
            continue
        phrases.add(alias)
    for token in slug_tokens(slug) + slug_tokens(name):
        if len(token) >= _MIN_TOKEN_LEN and token not in _GENERIC_ALIASES:
            phrases.add(token)
    return sorted((p for p in phrases if p.strip()), key=len, reverse=True)


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Case-insensitive, word-boundary substring test."""
    if not phrase:
        return False
    return re.search(rf"\b{re.escape(phrase)}\b", haystack) is not None


def resolve_project(
    question: str,
    cfg: Config,
    projects: dict[str, Project] | None = None,
) -> Project | None:
    """Return the project a question refers to, or None.

    Matches the question (case-insensitive, on word boundaries) against each
    project's slug, name, and aliases. On multiple matches the longest — most
    specific — phrase wins. Archived and done projects are excluded from
    auto-resolution but still resolve when the question names their exact slug.
    No match -> None, and the caller falls back to ordinary whole-vault search
    (UC-9 / UC-10 behaviour unchanged).
    """
    if projects is None:
        projects = load_projects(cfg)
    if not projects:
        return None
    q = (question or "").lower()
    if not q.strip():
        return None

    best: Project | None = None
    best_len = 0
    for slug in sorted(projects):                 # deterministic tie-break
        project = projects[slug]
        archived = project.status in ("archived", "done")
        for phrase in _match_phrases(project):
            if len(phrase) <= best_len:
                continue                          # cannot beat the current best
            # Archived/done projects resolve only on an exact slug mention.
            if archived and phrase != project.slug.lower():
                continue
            if _contains_phrase(q, phrase):
                best, best_len = project, len(phrase)
    return best


def retrieval_paths(project: Project, cfg: Config) -> list[Path]:
    """The documents to load for a project-scoped answer.

    INDEX + STATE + every canonical project-doc, with superseded docs already
    removed (the pruning happens in load_projects). The query path loads these
    whole — a project's canonical set is small by design (SPEC §6, §8.2).
    """
    _ = cfg  # part of the contract; resolution already used it in load_projects
    return [doc.path for doc in project.canonical_docs]


# ── CLI inspection ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    from . import config as _config

    _cfg = _config.load()
    if not _cfg.projects_enabled:
        print("Projects layer disabled (no `projects:` block in domains.yaml).")
    else:
        _projects = load_projects(_cfg)
        print(f"Projects root: {_cfg.projects_root()}")
        print(f"Discovered {len(_projects)} project(s):")
        for _slug, _p in sorted(_projects.items()):
            print(f"  {_slug}  [{_p.status}]  "
                  f"{len(_p.canonical_docs)} canonical doc(s)")
