"""
search.py — lexical full-text search over the vault (USE_CASES.md UC-9).

Finds the notes most relevant to a question by keyword overlap — no embeddings,
no vector store, no index to maintain. For a personal vault (hundreds to a few
thousand small Markdown files) a scan-and-score pass is fast and has zero moving
parts. The MVP deliberately stays lexical; semantic search is a roadmap item.

This module is channel-agnostic and config-driven: `search_vault()` takes a
plain query string and a Config and returns ranked `SearchHit`s. Nothing here
knows about Telegram — it imports cleanly into any host (see the portability
design principle in USE_CASES.md).
"""
from __future__ import annotations

import datetime
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import Config

# Tokenisation utilities — use shared.text if available (dogfood / _shared/ on
# PYTHONPATH), otherwise fall back to the inline copies so the open-core repo
# stays self-contained with no external dependency.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_'-]*")  # used in _score / _best_snippet

try:
    from shared.text import STOPWORDS as _STOPWORDS, stem as _stem, tokenize, query_terms
except ImportError:
    # Standalone fallback — open-core install without _shared/.
    _STOPWORDS = frozenset("""
a an the this that these those and or but if then else of in on at to for from
by with about as is are was were be been being it its it's i me my we our you
your he she they them do does did done have has had what when where who whom
which why how can could should would will shall may might must not no yes any
some all each more most other into over under again here there one two
""".split())

    def _stem(token: str) -> str:  # type: ignore[misc]
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            return token[:-1]
        return token

    def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:  # type: ignore[misc]
        out: list[str] = []
        for raw in _TOKEN_RE.findall(text.lower()):
            if len(raw) < 2:
                continue
            if not keep_stopwords and raw in _STOPWORDS:
                continue
            out.append(_stem(raw))
        return out

    def query_terms(question: str) -> list[str]:  # type: ignore[misc]
        seen: dict[str, None] = {}
        for tok in tokenize(question):
            seen.setdefault(tok, None)
        return list(seen)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
# Same block, but capturing the inner YAML — used by read_frontmatter().
_FRONTMATTER_CAPTURE_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[-_]?\d{0,6}[-_]?")

# A file bigger than this is almost certainly not a hand-written note — skip it
# so one stray export can't dominate a scan.
_MAX_FILE_BYTES = 512 * 1024


@dataclass
class SearchHit:
    """One vault note matched against a query."""

    path: Path          # absolute path on disk
    relpath: str        # path relative to the vault root — used as the citation
    title: str          # human-readable title (first heading, or de-slugged name)
    score: float        # relevance score; higher is better
    snippet: str        # short matched excerpt, for display
    body: str           # note body with frontmatter stripped, for LLM context


# tokenize / query_terms / _stem / _STOPWORDS are imported above from shared.text
# (if _shared/ is on PYTHONPATH) or defined inline in the ImportError fallback.

# ── note parsing ─────────────────────────────────────────────────────────────
def strip_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block, if present."""
    return _FRONTMATTER_RE.sub("", text, count=1)


def read_frontmatter(text: str) -> dict:
    """Parse a leading YAML frontmatter block into a dict.

    Returns {} when there is no frontmatter, when the block is not valid YAML,
    or when it does not parse to a mapping. Never raises — a malformed note must
    not break a scan. Used by the projects layer (USE_CASES UC-13).
    """
    match = _FRONTMATTER_CAPTURE_RE.match(text or "")
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _derive_title(path: Path, body: str) -> str:
    """A readable title: the first Markdown heading, else the de-slugged filename."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading:
                return heading
        elif line:
            break  # first non-blank line is not a heading — stop looking
    name = _DATE_PREFIX_RE.sub("", path.stem)
    name = name.replace("-", " ").replace("_", " ").strip()
    return name or path.stem


# ── scoring ──────────────────────────────────────────────────────────────────
def _frontmatter_text(raw: str) -> str:
    """Extract frontmatter as tokenizer-friendly plain text for scoring.

    Replaces underscores in key names with spaces so `weight_kg: 73.3` becomes
    `weight kg 73.3` — allowing "weight" queries to match structured fields like
    `weight_kg` and `body_fat_pct`. Returns "" when there is no frontmatter.
    """
    match = _FRONTMATTER_CAPTURE_RE.match(raw or "")
    if not match:
        return ""
    # Replace underscores with spaces so weight_kg → "weight kg"
    return match.group(1).replace("_", " ")


def _score(
    terms: list[str],
    path: Path,
    body: str,
    *,
    frontmatter_text: str = "",
    frontmatter: dict | None = None,
) -> tuple[float, str]:
    """Score one note against the query terms. Returns (score, best-line snippet).

    Scoring sources, in descending weight:
      +5.0  per term  — filename match (the user's own label)
      +3.0  per term  — heading match (strong structural signal)
      +count          — term frequency in body text
      +0.5× count     — term frequency in frontmatter (structured data,
                        semantically equivalent to body text but lower weight
                        because frontmatter fields repeat across many notes)
      +1.5 × distinct² — breadth bonus (notes covering many query terms win)

    Domain/type signal boost:
      When the note's `type:` or `domain:` frontmatter field matches any query
      term (e.g. `type: health-metrics` on a "weight" query that's clearly about
      health), add +4.0 so semantically specialised notes outrank generic ones
      that happen to repeat a query term more often.
    """
    body_tokens = tokenize(body)
    if not terms:
        return 0.0, ""

    counts = {t: 0 for t in terms}
    for tok in body_tokens:
        if tok in counts:
            counts[tok] += 1

    # Frontmatter term frequency (0.5× weight)
    fm_counts: dict[str, int] = {t: 0 for t in terms}
    if frontmatter_text:
        for tok in tokenize(frontmatter_text):
            if tok in fm_counts:
                fm_counts[tok] += 1

    filename_tokens = set(tokenize(path.name, keep_stopwords=True))
    filename_tokens |= {_stem(t) for t in filename_tokens}
    heading_tokens: set[str] = set()
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            heading_tokens.update(tokenize(line))

    # Domain/type signal: if the note declares a type or domain that overlaps
    # the query terms, it gets a one-time boost.  e.g. `type: health-metrics`
    # when query contains "weight" or "hrv".
    type_boost = 0.0
    if frontmatter:
        for field_name in ("type", "domain"):
            field_val = str(frontmatter.get(field_name, "") or "").lower().strip()
            if not field_val:
                continue   # empty string must not match everything
            if any(t in field_val or field_val in t for t in terms):
                type_boost = 4.0
                break

    score = 0.0
    distinct = 0
    for term in terms:
        hit_anywhere = False
        if counts[term]:
            score += counts[term]          # term frequency in the body
            hit_anywhere = True
        if fm_counts[term]:
            score += fm_counts[term] * 0.5  # frontmatter at half weight
            hit_anywhere = True
        if term in heading_tokens:
            score += 3.0                   # a heading match is a strong signal
            hit_anywhere = True
        if term in filename_tokens:
            score += 5.0                   # the filename is the user's own label
            hit_anywhere = True
        if hit_anywhere:
            distinct += 1

    if not distinct:
        return 0.0, ""

    # Reward breadth: a note touching many of the query's terms beats one that
    # merely repeats a single term, even if raw frequency is similar.
    score += distinct * distinct * 1.5
    score += type_boost

    return score, _best_snippet(terms, body)


def _best_snippet(terms: list[str], body: str, *, width: int = 280) -> str:
    """Build a readable excerpt around the body line covering the most query terms."""
    lines = body.splitlines()
    best_idx, best_hits = -1, 0
    for idx, line in enumerate(lines):
        line_tokens = set(tokenize(line))
        hits = sum(1 for t in terms if t in line_tokens)
        if hits > best_hits:
            best_idx, best_hits = idx, hits

    if best_idx < 0:
        # Matched only via filename — fall back to the opening prose.
        ordered = lines
    else:
        # Start one non-blank line earlier for context (so a heading-only match
        # still pulls in the content beneath it), then read forward.
        start = best_idx
        for j in range(best_idx - 1, -1, -1):
            if lines[j].strip():
                start = j
                break
        ordered = lines[start:]

    chosen: list[str] = []
    for line in ordered:
        clean = line.lstrip("#").strip()
        if clean:
            chosen.append(clean)
        if sum(len(c) for c in chosen) >= width:
            break

    snippet = re.sub(r"\s+", " ", " ".join(chosen)).strip()
    if len(snippet) > width:
        snippet = snippet[:width].rsplit(" ", 1)[0] + "…"
    return snippet


def _head_excerpt(body: str, budget: int) -> str:
    """First `budget` chars of `body`, cut on a word boundary."""
    head = body[:budget]
    return (head.rsplit(" ", 1)[0].rstrip() + " …") if " " in head else head


_HEADING_RE = re.compile(r"^#{1,6}\s")


def relevant_excerpt(body: str, terms: list[str], budget: int) -> str:
    """Up to `budget` chars of `body` — the parts that actually answer the query.

    A head slice (or a single centred window) of a long note often misses the
    answer: in a race-day log the meal table sits under a `### Breakfast`
    heading mid-document, while the query's *entity* terms ("Mamut", "Tour",
    "race") cluster in the title and the race-analysis sections.

    So this works at the **Markdown-section** level. The note is split at
    headings; each section is scored by the query terms in its heading (weighted
    heavily — a heading names what the section is about) and in its body. A
    section whose *heading* matches a query term is force-kept whole, so the
    table beneath `### Breakfast` always travels with that heading. Sections are
    then taken best-scoring first until the budget is spent, and re-ordered into
    document order.

    Term scoring is rarity-weighted: a term saturating the note ("race" in a
    race log) barely discriminates, so a section about a *rare* query term (the
    real topic) outranks one that merely repeats the common one.

    Whole body when it fits; head of the body when nothing matches. Verbatim —
    newlines and Markdown tables are preserved into the LLM context.
    """
    body = body.strip()
    if budget <= 0:
        return ""
    if len(body) <= budget:
        return body

    terms = [t for t in (terms or []) if t]
    if not terms:
        return _head_excerpt(body, budget)

    lines = body.splitlines(keepends=True)

    # Rarity weight — a term occurring many times in this note discriminates
    # less; weight ~ 1 / (1 + occurrences).
    counts = Counter(tokenize(body))
    weight = {t: 1.0 / (1.0 + counts.get(t, 0)) for t in terms}

    # Split into Markdown sections: (start, end, heading_line_or_None).
    sections: list[tuple[int, int, str | None]] = []
    cur_start, cur_heading = 0, None
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            if i > cur_start:
                sections.append((cur_start, i, cur_heading))
            cur_start, cur_heading = i, line
    sections.append((cur_start, len(lines), cur_heading))

    # Score each section; a heading-term match is weighted heavily and force-keeps
    # the section (so a `### Breakfast` table is never dropped on a budget edge).
    scored: list[tuple[float, bool, int, int]] = []   # (score, heading_hit, start, end)
    for start, end, heading in sections:
        heading_hit = False
        sc = 0.0
        if heading:
            htoks = set(tokenize(heading))
            hmatch = sum(weight[t] for t in terms if t in htoks)
            if hmatch > 0:
                heading_hit = True
                sc += 5.0 * hmatch
        for line in lines[start:end]:
            toks = set(tokenize(line))
            sc += sum(weight[t] for t in terms if t in toks)
        scored.append((sc, heading_hit, start, end))

    if not any(sc > 0 for sc, _, _, _ in scored):
        return _head_excerpt(body, budget)

    # Heading-matched sections first (by score), then other scoring sections.
    order = sorted(
        (x for x in scored if x[0] > 0),
        key=lambda x: (x[1], x[0]), reverse=True,
    )
    chosen: list[tuple[int, str]] = []
    used = 0
    for sc, _heading_hit, start, end in order:
        if used >= budget:
            break
        chunk = "".join(lines[start:end])
        if used + len(chunk) > budget:
            chunk = chunk[: budget - used]
        chosen.append((start, chunk))
        used += len(chunk)
    chosen.sort()
    return "\n…\n".join(c.strip() for _, c in chosen).strip()


# ── public API ───────────────────────────────────────────────────────────────
def iter_notes(cfg: Config, *, scope: Path | None = None):
    """Yield every Markdown note path under the vault (recursively, sorted).

    `scope` optionally restricts the walk to a subtree (e.g. one project folder);
    when None the whole vault is walked — today's behaviour, unchanged.
    """
    root = scope if scope is not None else cfg.vault_path
    if not root.exists():
        return
    exclude = cfg.exclude_dirs
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        # Skip non-content folders (dot-dirs, raw-import staging, …) — a note is
        # excluded when any component of its path matches a name in exclude_dirs.
        if exclude.intersection(path.parts):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def load_note_as_hit(
    path: Path, cfg: Config, *, query: str = ""
) -> SearchHit | None:
    """Read one vault note verbatim into a SearchHit, bypassing lexical scoring.

    Used by the projects layer (USE_CASES UC-13) to force-load a canonical
    document whole — `score` is left at 0.0 because the caller, not the ranker,
    decided this note belongs in the context. The snippet is built around the
    query terms when given, so the no-LLM fallback still shows a relevant
    excerpt. Returns None for an unreadable or empty note.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    body = strip_frontmatter(raw).strip()
    if not body:
        return None
    try:
        relpath = str(path.relative_to(cfg.vault_path))
    except ValueError:
        relpath = path.name
    terms = query_terms(query) if query else []
    return SearchHit(
        path=path,
        relpath=relpath,
        title=_derive_title(path, body),
        score=0.0,
        snippet=_best_snippet(terms, body),
        body=body,
    )


def search_vault(
    question: str, cfg: Config, *, limit: int = 5, scope: Path | None = None
) -> list[SearchHit]:
    """Return the `limit` vault notes most relevant to `question`, best first.

    Lexical scoring only — term frequency, with heading and filename matches
    weighted up and breadth of coverage rewarded. Notes that match nothing are
    excluded, so an empty list is an honest "nothing relevant in the vault".

    `scope` optionally restricts the search to a subtree (e.g. one project's
    folder — USE_CASES UC-13); the default None searches the whole vault, the
    behaviour every existing caller relies on. Citations stay vault-relative
    regardless of scope.
    """
    terms = query_terms(question)
    if not terms:
        return []

    root = cfg.vault_path
    hits: list[SearchHit] = []
    for path in iter_notes(cfg, scope=scope):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm_text = _frontmatter_text(raw)
        fm_dict = read_frontmatter(raw)
        body = strip_frontmatter(raw).strip()
        if not body:
            continue
        score, snippet = _score(
            terms, path, body,
            frontmatter_text=fm_text,
            frontmatter=fm_dict,
        )
        if score <= 0:
            continue
        try:
            relpath = str(path.relative_to(root))
        except ValueError:
            relpath = path.name
        hits.append(
            SearchHit(
                path=path,
                relpath=relpath,
                title=_derive_title(path, body),
                score=score,
                snippet=snippet,
                body=body,
            )
        )

    # Sort by score desc; break ties by most-recently-modified, then path.
    hits.sort(key=lambda h: (-h.score, -_mtime(h.path), h.relpath))
    return hits[:limit]


def search_by_date_range(
    question: str,
    cfg: Config,
    window,   # DateWindow — avoid circular import; duck-typed
    *,
    max_notes: int = 20,
    context_budget: int = 24000,
) -> list[SearchHit]:
    """Return notes whose date falls inside `window` (USE_CASES UC-14).

    When the in-range set is ≤ `max_notes`, all are returned (ordered
    chronologically so the LLM reasons over a timeline).  When it exceeds
    `max_notes`, the in-range set is lexically ranked and the top `max_notes`
    are kept — so "threshold sessions last month" still narrows correctly.

    Notes with no resolvable date (no frontmatter `date:` / `created:` and no
    YYYY-MM-DD filename prefix) are excluded from date-range retrieval but
    remain findable by ordinary lexical search.
    """
    from .daterange import note_date  # local import — avoids circular dependency

    terms = query_terms(question)
    in_range: list[tuple[datetime.date, SearchHit]] = []

    for path in iter_notes(cfg):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = read_frontmatter(raw)
        nd = note_date(path, fm)
        if nd is None or not window.contains(nd):
            continue
        body = strip_frontmatter(raw).strip()
        if not body:
            continue
        try:
            relpath = str(path.relative_to(cfg.vault_path))
        except ValueError:
            relpath = path.name
        hit = SearchHit(
            path=path,
            relpath=relpath,
            title=_derive_title(path, body),
            score=0.0,   # date-range hits are chronologically ordered, not scored
            snippet=_best_snippet(terms, body) if terms else "",
            body=body,
        )
        in_range.append((nd, hit))

    if not in_range:
        return []

    if len(in_range) <= max_notes:
        # Chronological order — the LLM reasons over a timeline
        in_range.sort(key=lambda x: x[0])
        return [h for _, h in in_range]

    # Over the cap — lexically rank and keep top max_notes.
    if not terms:
        in_range.sort(key=lambda x: x[0])
        return [h for _, h in in_range[:max_notes]]

    ranked: list[tuple[float, datetime.date, SearchHit]] = []
    for nd, hit in in_range:
        raw_text = hit.body
        fm_text = ""
        try:
            full_raw = hit.path.read_text(encoding="utf-8", errors="ignore")
            fm_text = _frontmatter_text(full_raw)
            fm_dict = read_frontmatter(full_raw)
        except OSError:
            fm_dict = {}
        sc, _ = _score(
            terms, hit.path, raw_text,
            frontmatter_text=fm_text,
            frontmatter=fm_dict,
        )
        ranked.append((sc, nd, hit))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [h for _, _, h in ranked[:max_notes]]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
