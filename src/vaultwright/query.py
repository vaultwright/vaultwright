"""
query.py — answer a question from the vault (USE_CASES.md UC-9 + UC-10).

Capture writes *into* the vault; this reads *out* of it. Given a question, it
searches the vault (see search.py), feeds the top-matching notes to the LLM, and
returns an answer **grounded in those notes, with the source notes cited**.

Two modes, one machinery:
- UC-9  retrieval Q&A   — a fact back out of your notes ("what are the open bugs?").
- UC-10 reasoning/advice — a judgement over your notes ("what should I focus on?").
`detect_mode()` picks the mode from the wording; the only difference is the prompt.

Honesty guarantees (project rule — never invent):
- If the search finds nothing, the answer says so. No guess.
- The LLM is instructed to answer ONLY from the supplied notes and to admit when
  they do not cover the question.
- The cited sources are appended deterministically from the notes actually
  retrieved — so an answer always names where it came from, even if the model
  forgets to. If the LLM is unreachable, the raw matched excerpts are returned
  instead of a fabricated answer.

Read-only by design. This module never writes to the vault and never triggers an
action — querying is in scope, agentic execution is explicitly not (USE_CASES.md
non-goals).

Channel-agnostic and config-driven: `answer_question()` takes a plain string and
a Config and returns a `QueryAnswer`. The Telegram bot is a thin adapter over it;
it imports cleanly into any other host.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
from dataclasses import dataclass, field

from .classifier import DEFAULT_MODEL
from .config import Config
from .projects import Project, resolve_project, retrieval_paths
from .search import (
    SearchHit, load_note_as_hit, query_terms, relevant_excerpt, search_vault,
)

log = logging.getLogger("vaultwright.query")

# Per-note and total character budgets for the context handed to the LLM. Keeps
# the prompt cheap and bounded regardless of how large the matched notes are.
# The non-project path splits _TOTAL_BUDGET fairly across the retrieved notes so
# a later note is never silently dropped — and feeds each note a query-centred
# excerpt (not a head slice), so an answer buried mid-note still reaches the LLM.
_PER_NOTE_BUDGET = 1500
_TOTAL_BUDGET = 24000

# How many supporting (non-canonical) notes from inside a resolved project to
# append after its canonical docs — kept small so the canonical set dominates.
_PROJECT_SUPPORT_LIMIT = 3

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday")

# Wording that signals the user wants judgement/advice (UC-10), not just a fact.
_ADVICE_RE = re.compile(
    r"\b(should|shall|advice|advise|suggest|suggestion|recommend|recommendation|"
    r"worth it|better to|better off|focus on|prioriti|what do you think|"
    r"help me decide|opinion|idea to|ought to|do you reckon)\w*",
    re.IGNORECASE,
)


@dataclass
class QueryAnswer:
    """Outcome of answering one question from the vault."""

    reply: str                     # full text to send back to the user
    answer: str                    # just the answer body (no sources footer)
    found: bool                    # were any relevant notes found?
    mode: str                      # "qa" (UC-9) | "advice" (UC-10)
    source: str                    # "llm" | "no-llm" | "no-context"
    citations: list[str] = field(default_factory=list)   # cited note relpaths
    project: str | None = None                           # resolved project slug (UC-13)
    stale: list[str] = field(default_factory=list)        # canonical docs past staleness_days


# ── mode detection ───────────────────────────────────────────────────────────
def detect_mode(question: str) -> str:
    """Classify a question as a fact lookup ('qa') or an advice request ('advice')."""
    return "advice" if _ADVICE_RE.search(question or "") else "qa"


# ── temporal grounding (USE_CASES UC-13, SPEC §8.1) ──────────────────────────
def temporal_context(today: datetime.date | None = None) -> str:
    """Build the CONTEXT line that grounds the LLM in the real current date.

    Prepended to every query — project-scoped or not. This is the whole fix for
    time-relative questions ("what's the focus this week?"): retrieval stays
    lexical, but the model can now resolve "this week" / "today" / "next"
    against a real date when it reads a date-structured document. Harmless for
    plain fact-lookup. `today` is injectable so the behaviour is deterministic
    under test.
    """
    day = today or datetime.date.today()
    monday = day - datetime.timedelta(days=day.weekday())
    sunday = monday + datetime.timedelta(days=6)
    iso_week = day.isocalendar()[1]
    return (
        f"CONTEXT: Today is {day.isoformat()} ({_WEEKDAYS[day.weekday()]}). "
        f"The current week is {monday.isoformat()} to {sunday.isoformat()} "
        f"(ISO week {iso_week})."
    )


# ── prompts ──────────────────────────────────────────────────────────────────
_QA_SYSTEM = """You answer the user's question using ONLY the notes provided below, which come from their personal Markdown vault.

Rules:
- Base every statement strictly on the provided notes. Do not add outside knowledge or assumptions.
- If the notes do not contain the answer, say so plainly — e.g. "Your notes don't cover that." Never guess or invent.
- Cite the notes you draw on by their number, like [1] or [2].
- Be concise and direct. Answer the question; do not pad.
- Plain text only — no Markdown headings or tables."""

_ADVICE_SYSTEM = """You are the user's thinking partner. They want a judgement or a recommendation, reasoned over ONLY the notes provided below from their personal Markdown vault.

Rules:
- Ground your reasoning in the provided notes and cite them by number, like [1] or [2].
- You may reason, weigh, and infer — but introduce no facts that the notes do not support.
- Give a clear recommendation, then the key reasons for it, briefly.
- If the notes are too thin to advise well, say so honestly — the quality of advice is limited by what has been captured. Do not compensate by inventing.
- Plain text only — no Markdown headings or tables."""


# ── context assembly ─────────────────────────────────────────────────────────
def _trim(text: str, budget: int) -> str:
    text = text.strip()
    if len(text) <= budget:
        return text
    return text[:budget].rsplit(" ", 1)[0].rstrip() + " …"


def _build_context(
    hits: list[SearchHit],
    *,
    terms: list[str] | None = None,
    per_note_budget: int = _PER_NOTE_BUDGET,
    total_budget: int = _TOTAL_BUDGET,
    warn_overflow: bool = False,
    fair_share: bool = False,
) -> str:
    """Render retrieved notes as a numbered block for the LLM prompt.

    Two modes:

    - **project (default)** — sequential whole-document load: each canonical doc
      gets up to the remaining `total_budget` (UC-13; SPEC §8.2 — the canonical
      set is small by design, an overflow is logged so the doc can be split or
      `supersedes` used).
    - **fair_share (UC-9 / UC-10)** — `total_budget` is split evenly across every
      retrieved note, and each note contributes a *query-centred* excerpt
      (`relevant_excerpt`), not a head slice. This guarantees a later note is
      never silently dropped (so every cited note really is in the context) and
      that an answer buried mid-note still reaches the LLM.
    """
    if not hits:
        return ""

    if fair_share:
        share = max(1, total_budget // len(hits))
        blocks = []
        for i, hit in enumerate(hits, start=1):
            body = relevant_excerpt(hit.body, terms or [], share)
            blocks.append(f"[{i}] {hit.relpath} (title: {hit.title})\n{body}")
        return "\n\n".join(blocks)

    blocks, used = [], 0
    for i, hit in enumerate(hits, start=1):
        remaining = total_budget - used
        if remaining <= 0:
            if warn_overflow:
                log.warning(
                    "project context budget (%d chars) exhausted at note "
                    "[%d] %s — later docs were dropped. Split the doc or use a "
                    "`supersedes` field to prune stale ones.",
                    total_budget, i, hit.relpath,
                )
            break
        body = _trim(hit.body, min(per_note_budget, remaining))
        used += len(body)
        blocks.append(f"[{i}] {hit.relpath} (title: {hit.title})\n{body}")
    return "\n\n".join(blocks)


def _sources_footer(hits: list[SearchHit]) -> str:
    lines = ["Sources:"]
    for i, hit in enumerate(hits, start=1):
        lines.append(f"[{i}] {hit.relpath}")
    return "\n".join(lines)


# ── LLM call ─────────────────────────────────────────────────────────────────
def _call_llm(system: str, user: str, *, temperature: float) -> str:
    """Call the LLM for a grounded answer. Raises on any failure (caller falls back)."""
    try:
        from shared.llm import call as llm_call  # _shared/ on PYTHONPATH (dogfood)
        return llm_call(
            system=system, user=user, max_tokens=700, temperature=temperature, timeout=30.0
        )
    except ImportError:
        pass
    # Standalone path — open-core install without _shared/.
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic  # lazy

    model = os.getenv("VAULTWRIGHT_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic(api_key=api_key, timeout=30.0)
    resp = client.messages.create(
        model=model,
        max_tokens=700,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    if not text:
        raise ValueError("empty LLM response")
    return text


# ── fallback (no LLM) ────────────────────────────────────────────────────────
def _no_llm_answer(hits: list[SearchHit]) -> str:
    """Honest degraded answer: show the matched notes, synthesise nothing."""
    lines = [
        "I can't reach the language model right now, so I can't compose an "
        "answer — but here is what your vault has on this:",
        "",
    ]
    for i, hit in enumerate(hits, start=1):
        lines.append(f"[{i}] {hit.relpath}")
        if hit.snippet:
            lines.append(f"    {hit.snippet}")
    return "\n".join(lines)


# ── project-scoped retrieval (USE_CASES UC-13) ───────────────────────────────
def _parse_iso(value: str | None) -> datetime.date | None:
    """Parse an ISO date string to a date; None when absent or unparseable."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _stale_docs(
    project: Project, cfg: Config, today: datetime.date | None
) -> list:
    """Canonical project docs whose `last_updated` is past `staleness_days`."""
    cutoff = (today or datetime.date.today()) - datetime.timedelta(
        days=cfg.staleness_days
    )
    stale = []
    for doc in project.canonical_docs:
        updated = _parse_iso(doc.last_updated)
        if updated is not None and updated < cutoff:
            stale.append(doc)
    return stale


def _staleness_notes(stale_docs: list) -> str:
    """One soft-warning line per stale canonical doc (SPEC §8.3)."""
    lines = []
    for doc in stale_docs:
        when = doc.last_updated or "an unknown date"
        lines.append(
            f'Note: "{doc.relpath}" was last updated {when} '
            f"and may be out of date."
        )
    return "\n".join(lines)


def _project_hits(
    project: Project, question: str, cfg: Config
) -> list[SearchHit]:
    """Assemble the retrieval set for a resolved project.

    INDEX + STATE + every canonical doc, each loaded WHOLE (a project's
    canonical set is small by design), followed by a few supporting
    non-canonical notes scored from inside the project folder. Superseded docs
    are already excluded by `retrieval_paths`.
    """
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for path in retrieval_paths(project, cfg):
        hit = load_note_as_hit(path, cfg, query=question)
        if hit is None:
            continue
        hits.append(hit)
        seen.add(hit.relpath)
    # Supporting context: top-ranked non-canonical notes within the project.
    # A superseded doc never resurfaces here — it is excluded from all project
    # retrieval, not just the canonical set (SPEC §4.3).
    skip = seen | set(project.superseded)
    for extra in search_vault(
        question, cfg, limit=_PROJECT_SUPPORT_LIMIT, scope=project.root
    ):
        if extra.relpath not in skip:
            hits.append(extra)
            skip.add(extra.relpath)
    return hits


# ── public API ───────────────────────────────────────────────────────────────
def answer_question(
    question: str,
    cfg: Config,
    *,
    hits: list[SearchHit] | None = None,
    mode: str | None = None,
    limit: int = 5,
    today: datetime.date | None = None,
) -> QueryAnswer:
    """Answer `question` from the vault, grounded in real notes and cited.

    UC-9 (fact lookup) and UC-10 (advice) both run through here — `mode` is
    auto-detected from the wording unless passed explicitly. Pass `hits` to
    reuse a search already done (e.g. in tests) instead of searching again.

    UC-13: when the projects layer is enabled and the question resolves to a
    project, retrieval is project-scoped — the project's canonical docs are
    loaded whole and a staleness note is added for any overdue doc. Every
    query, project or not, is grounded with today's date. When no project
    matches (or the layer is disabled) the path is exactly UC-9 / UC-10.
    `today` is injectable for deterministic tests.
    """
    mode = mode or detect_mode(question)
    project_slug: str | None = None
    stale_docs: list = []
    is_project = False

    if hits is None:
        project = (
            resolve_project(question, cfg) if cfg.projects_enabled else None
        )
        if project is not None:
            hits = _project_hits(project, question, cfg)
            stale_docs = _stale_docs(project, cfg, today)
            project_slug = project.slug
            is_project = True
        else:
            hits = search_vault(question, cfg, limit=limit)

    stale = [doc.relpath for doc in stale_docs]

    # UC-9 acceptance: no relevant notes -> an honest answer, never a guess.
    if not hits:
        msg = (
            "I don't have anything in your vault about that. "
            "Capture a note on it first, or try rephrasing the question."
        )
        return QueryAnswer(
            reply=msg, answer=msg, found=False, mode=mode, source="no-context",
            project=project_slug, stale=stale,
        )

    if is_project:
        budget = cfg.project_context_budget
        context = _build_context(
            hits, per_note_budget=budget, total_budget=budget,
            warn_overflow=True,
        )
    else:
        context = _build_context(
            hits, terms=query_terms(question), fair_share=True,
        )

    system = _ADVICE_SYSTEM if mode == "advice" else _QA_SYSTEM
    temperature = 0.3 if mode == "advice" else 0.0
    grounding = temporal_context(today)
    user = (
        f"{grounding}\n\n"
        f"NOTES FROM THE VAULT:\n\n{context}\n\n"
        f"---\nQUESTION: {question.strip()}"
    )

    citations = [hit.relpath for hit in hits]
    stale_note = _staleness_notes(stale_docs)

    try:
        answer = _call_llm(system, user, temperature=temperature)
        reply = f"{answer}\n\n{_sources_footer(hits)}"
        source = "llm"
    except Exception:
        # LLM unreachable — return the grounded excerpts, invent nothing.
        answer = _no_llm_answer(hits)
        reply = answer  # already lists its sources inline
        source = "no-llm"

    if stale_note:
        reply = f"{reply}\n\n{stale_note}"

    return QueryAnswer(
        reply=reply,
        answer=answer,
        found=True,
        mode=mode,
        source=source,
        citations=citations,
        project=project_slug,
        stale=stale,
    )
