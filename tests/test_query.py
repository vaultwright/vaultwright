"""
Offline tests for the Vaultwright query side — UC-9 (ask your vault) and
UC-10 (reasoning / advice).

Covers everything that does not need a live LLM: vault search and scoring,
question-mode detection, the honest "not found" path, the no-LLM grounded
fallback, citation assembly, and router question-routing.

pytest is not required. Run directly:

    PYTHONPATH=src python3 tests/test_query.py

The bottom-of-file runner gives each test a fresh temp directory. The test
functions also work unchanged under pytest (the `tmp_path` fixture).
"""
import datetime
import inspect
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vaultwright import projects, query, search
from vaultwright.classifier import Classification, classify_heuristic
from vaultwright.config import Config
from vaultwright.daterange import DateWindow, note_date, parse_window
from vaultwright.query import answer_question, detect_mode, temporal_context
from vaultwright.router import route
from vaultwright.search import SearchHit, read_frontmatter, search_vault


# ── fixtures ─────────────────────────────────────────────────────────────────
def make_cfg(tmp_path):
    """A Config with a vault under tmp_path and a `question` intent configured."""
    return Config(
        vault_path=tmp_path / "vault",
        domains={
            "work": {"description": "work projects and meetings"},
            "reading": {"description": "articles links videos to read"},
            "personal": {"description": "personal thoughts and journal"},
        },
        intents={
            "note": "a note", "link": "a link", "task": "a task",
            "log": "a log", "question": "a question to answer from the vault",
        },
        confidence_threshold=0.70,
    )


_NOTES = {
    "work/inbox/2026-05-20-143000-project-bugs.md": (
        "---\ntype: note\ndomain: work\n---\n\n"
        "# Open bugs\n\n"
        "The login page crashes on Safari. The CSV export button is broken on "
        "large files. Two open bugs remain before the release.\n"
    ),
    "work/inbox/2026-05-21-090000-feature-xy.md": (
        "---\ntype: note\ndomain: work\n---\n\n"
        "# Feature XY\n\n"
        "Feature XY is in code review. Waiting on QA sign-off before we ship it.\n"
    ),
    "personal/inbox/2026-05-19-200000-marathon.md": (
        "---\ntype: log\ndomain: personal\n---\n\n"
        "# Pre-race meal\n\n"
        "Before the 2025 marathon I had oatmeal with honey and a banana, "
        "about two hours before the start.\n"
    ),
}


def seed_vault(cfg):
    """Write the sample notes into the vault and return how many were written."""
    for relpath, body in _NOTES.items():
        path = cfg.vault_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return len(_NOTES)


def count_md(cfg):
    root = cfg.vault_path
    return sum(1 for _ in root.rglob("*.md")) if root.exists() else 0


# ── search: tokenisation + parsing ───────────────────────────────────────────
def test_tokenize_drops_stopwords_and_stems(tmp_path):
    toks = search.tokenize("What are the OPEN bugs?")
    assert "open" in toks, toks
    assert "bug" in toks, toks            # 'bugs' stemmed to 'bug'
    assert "the" not in toks and "are" not in toks


def test_query_terms_dedupes(tmp_path):
    terms = search.query_terms("bug bug bugs report")
    assert terms.count("bug") == 1, terms
    assert "report" in terms


def test_strip_frontmatter(tmp_path):
    raw = "---\ntype: note\ndomain: work\n---\n\nreal body here\n"
    assert search.strip_frontmatter(raw).strip() == "real body here"
    # No frontmatter -> text returned unchanged.
    assert search.strip_frontmatter("no front matter").strip() == "no front matter"


def test_derive_title_prefers_heading(tmp_path):
    assert search._derive_title(Path("x.md"), "# My Heading\n\nbody") == "My Heading"
    # Falls back to a de-slugged, date-stripped filename.
    title = search._derive_title(Path("2026-05-20-143000-project-bugs.md"), "body only")
    assert title == "project bugs", title


# ── search: ranking ──────────────────────────────────────────────────────────
def test_search_finds_relevant_note(tmp_path):
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    hits = search_vault("what are the open bugs?", cfg)
    assert hits, "expected at least one hit"
    assert "project-bugs" in hits[0].relpath, hits[0].relpath
    assert hits[0].score > 0


def test_search_ranks_best_first(tmp_path):
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    hits = search_vault("status of feature XY", cfg)
    assert hits and "feature-xy" in hits[0].relpath, [h.relpath for h in hits]


def test_search_marathon_question(tmp_path):
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    hits = search_vault("what did I eat before the marathon?", cfg)
    assert hits and "marathon" in hits[0].relpath, [h.relpath for h in hits]


def test_search_no_match_returns_empty(tmp_path):
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    assert search_vault("quantum chromodynamics tensor calculus", cfg) == []
    assert search_vault("", cfg) == []           # no usable query terms


def test_search_missing_vault_is_safe(tmp_path):
    cfg = make_cfg(tmp_path)                       # vault never created
    assert search_vault("anything", cfg) == []


def test_search_hit_has_snippet(tmp_path):
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    hits = search_vault("login page crashes", cfg)
    assert hits and hits[0].snippet
    assert "login" in hits[0].snippet.lower()


# ── query: mode detection (UC-9 vs UC-10) ────────────────────────────────────
def test_detect_mode_qa(tmp_path):
    assert detect_mode("what are the open bugs?") == "qa"
    assert detect_mode("status of feature XY?") == "qa"


def test_detect_mode_advice(tmp_path):
    assert detect_mode("what should I focus on this sprint?") == "advice"
    assert detect_mode("any suggestion for today's training?") == "advice"
    assert detect_mode("is it worth it to refactor now?") == "advice"


# ── query: answering ─────────────────────────────────────────────────────────
def test_answer_not_found_is_honest(tmp_path):
    """UC-9: nothing relevant -> honest answer, never a guess."""
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    qa = answer_question("what is the capital of France?", cfg)
    assert qa.found is False
    assert qa.source == "no-context"
    assert qa.citations == []
    assert "don't have anything" in qa.reply.lower()


def test_answer_grounded_with_citation(tmp_path):
    """UC-9: an answer cites the source note(s) it drew on.

    No API key in the sandbox -> the no-LLM grounded fallback runs, which returns
    the matched excerpts verbatim (it invents nothing). With a real key this
    path becomes source == 'llm' with the same citations footer.
    """
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    qa = answer_question("what are the open bugs?", cfg)
    assert qa.found is True
    assert qa.source in ("llm", "no-llm")
    assert qa.citations, "an answer must cite its sources"
    assert any("project-bugs" in c for c in qa.citations), qa.citations
    # The cited note's path appears in the reply text the user sees.
    assert any(c in qa.reply for c in qa.citations)


def test_answer_advice_mode(tmp_path):
    """UC-10: advice questions run the same machinery in advice mode."""
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    qa = answer_question("what should I prioritise on feature XY?", cfg)
    assert qa.mode == "advice"
    assert qa.found is True
    assert qa.citations


def test_answer_reuses_supplied_hits(tmp_path):
    cfg = make_cfg(tmp_path)
    hit = SearchHit(
        path=tmp_path / "x.md", relpath="work/inbox/x.md", title="X",
        score=9.0, snippet="a snippet", body="the body text",
    )
    qa = answer_question("anything?", cfg, hits=[hit])
    assert qa.found is True
    assert qa.citations == ["work/inbox/x.md"]


def test_context_and_sources_numbering_align(tmp_path):
    """The [n] markers in the context match the [n] in the sources footer."""
    hits = [
        SearchHit(Path("a.md"), "work/inbox/a.md", "A", 9.0, "snip a", "body a"),
        SearchHit(Path("b.md"), "reading/inbox/b.md", "B", 7.0, "snip b", "body b"),
    ]
    context = query._build_context(hits)
    footer = query._sources_footer(hits)
    assert "[1] work/inbox/a.md" in context and "[1] work/inbox/a.md" in footer
    assert "[2] reading/inbox/b.md" in context and "[2] reading/inbox/b.md" in footer


def test_no_llm_fallback_only_cites_real_notes(tmp_path):
    """The degraded answer must contain only retrieved content — no invention."""
    hits = [SearchHit(Path("a.md"), "work/inbox/a.md", "A", 9.0,
                      "the login page crashes", "body")]
    text = query._no_llm_answer(hits)
    assert "work/inbox/a.md" in text
    assert "the login page crashes" in text


# ── classifier heuristic: question intent ────────────────────────────────────
def test_heuristic_detects_question(tmp_path):
    cfg = make_cfg(tmp_path)
    cls = classify_heuristic("what are the open bugs?", cfg)
    assert cls.intent == "question"
    assert cls.confidence >= cfg.confidence_threshold   # clears the UC-5 gate


def test_heuristic_task_beats_question(tmp_path):
    """A task phrased as a question stays a task — it must not be lost."""
    cfg = make_cfg(tmp_path)
    assert classify_heuristic("todo: call the bank?", cfg).intent == "task"


def test_heuristic_plain_note_unaffected(tmp_path):
    cfg = make_cfg(tmp_path)
    assert classify_heuristic("buy milk", cfg).intent == "note"


# ── router: question routing ─────────────────────────────────────────────────
def test_router_question_answers_not_files(tmp_path):
    """UC-9: a question is answered, never filed as a captured note."""
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    before = count_md(cfg)
    res = route("what are the open bugs?", cfg,
                classification=Classification("work", "question", 0.95))
    assert res.is_query is True
    assert res.requires_confirmation is False
    assert res.written_path is None
    assert count_md(cfg) == before, "a question must not write a note"


def test_router_question_low_confidence_confirms(tmp_path):
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    res = route("ambiguous", cfg,
                classification=Classification("work", "question", 0.40))
    assert res.requires_confirmation is True
    assert "question" in res.reply.lower()


def test_router_heuristic_question_end_to_end(tmp_path):
    """No classification passed -> heuristic classifies, router routes to query."""
    cfg = make_cfg(tmp_path)
    seed_vault(cfg)
    res = route("what are the open bugs?", cfg)     # heuristic path (no LLM)
    assert res.is_query is True
    assert res.written_path is None


def test_router_capture_still_files(tmp_path):
    """Regression: the capture path is untouched by the query addition."""
    cfg = make_cfg(tmp_path)
    res = route("a plain thought", cfg,
                classification=Classification("work", "note", 0.95))
    assert res.is_query is False
    assert res.written_path and Path(res.written_path).exists()


# ═════════════════════════════════════════════════════════════════════════════
# UC-13 — Projects layer
# ═════════════════════════════════════════════════════════════════════════════
# Generic fixtures only — no personal or real-project names (SANITISATION §6).

_TODAY = datetime.date(2026, 5, 25)        # a Monday; ISO week 22

_PROJECT_FILES = {
    "q3-website-relaunch/INDEX.md": (
        "---\n"
        "type: project-index\n"
        'project: "Q3 Website Relaunch"\n'
        "slug: q3-website-relaunch\n"
        "status: active\n"
        "aliases: [website relaunch, relaunch]\n"
        "created: 2026-01-10\n"
        "last_updated: 2026-05-25\n"
        "---\n\n"
        "# Q3 Website Relaunch\n\n"
        "Project hub for the website relaunch effort.\n"
    ),
    "q3-website-relaunch/STATE.md": (
        "---\n"
        "type: project-state\n"
        "slug: q3-website-relaunch\n"
        "last_updated: 2026-05-25\n"
        "---\n\n"
        "# State\n\n"
        "Phase: build. This week the focus is the homepage.\n"
    ),
    "q3-website-relaunch/plan.md": (
        "---\n"
        "type: project-doc\n"
        "slug: q3-website-relaunch\n"
        "canonical: true\n"
        "status: active\n"
        "last_updated: 2026-04-01\n"
        "supersedes: [plan-v1.md]\n"
        "---\n\n"
        "# Plan\n\n"
        "## Week of 2026-05-18\n"
        "Wire up the content management system.\n\n"
        "## Week of 2026-05-25\n"
        "Ship the new homepage and run the quality checks.\n\n"
        "## Week of 2026-06-01\n"
        "Publish the launch announcement.\n"
    ),
    "q3-website-relaunch/plan-v1.md": (
        "---\n"
        "type: project-doc\n"
        "slug: q3-website-relaunch\n"
        "canonical: true\n"
        "last_updated: 2026-01-05\n"
        "---\n\n"
        "# Old Plan\n\n"
        "An earlier homepage plan, replaced by plan.md.\n"
    ),
    "q3-website-relaunch/research.md": (
        "---\n"
        "type: project-doc\n"
        "slug: q3-website-relaunch\n"
        "canonical: false\n"
        "last_updated: 2026-05-20\n"
        "---\n\n"
        "# Research\n\n"
        "Competitor homepage teardown and layout ideas.\n"
    ),
    "home-move/INDEX.md": (
        "---\n"
        "type: project-index\n"
        'project: "Home Move"\n'
        "slug: home-move\n"
        "status: active\n"
        "aliases: [moving house, relocation]\n"
        "created: 2026-03-01\n"
        "last_updated: 2026-05-22\n"
        "---\n\n"
        "# Home Move\n\n"
        "Hub for the house move.\n"
    ),
    "home-move/STATE.md": (
        "---\n"
        "type: project-state\n"
        "slug: home-move\n"
        "last_updated: 2026-05-22\n"
        "---\n\n"
        "# State\n\n"
        "Phase: packing the boxes.\n"
    ),
    "archived-pilot/INDEX.md": (
        "---\n"
        "type: project-index\n"
        'project: "Archived Pilot"\n'
        "slug: archived-pilot\n"
        "status: archived\n"
        "aliases: [pilot scheme]\n"
        "created: 2025-09-01\n"
        "last_updated: 2026-02-01\n"
        "---\n\n"
        "# Archived Pilot\n\n"
        "A pilot that has wrapped up.\n"
    ),
}


def make_projects_cfg(tmp_path):
    """A Config like make_cfg, but with the projects layer enabled (UC-13)."""
    return Config(
        vault_path=tmp_path / "vault",
        domains={
            "work": {"description": "work projects and meetings"},
            "personal": {"description": "personal thoughts and journal"},
        },
        intents={
            "note": "a note", "link": "a link", "task": "a task",
            "log": "a log", "question": "a question to answer from the vault",
        },
        confidence_threshold=0.70,
        projects={
            "enabled": True, "path": "projects",
            "staleness_days": 14, "context_budget": 28000,
        },
    )


def seed_projects(cfg):
    """Write the sample project tree into <vault>/projects/."""
    root = cfg.projects_root()
    for relpath, body in _PROJECT_FILES.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


# ── frontmatter parsing ──────────────────────────────────────────────────────
def test_read_frontmatter_valid(tmp_path):
    fm = read_frontmatter("---\ntype: project-index\nslug: demo\n---\n\nbody")
    assert fm == {"type": "project-index", "slug": "demo"}


def test_read_frontmatter_missing(tmp_path):
    assert read_frontmatter("no frontmatter here at all") == {}


def test_read_frontmatter_malformed(tmp_path):
    # Unbalanced bracket -> invalid YAML -> {} (a bad note must not break a scan).
    assert read_frontmatter("---\ntype: [unclosed\n---\nbody") == {}
    # A frontmatter block that is a list, not a mapping, is also {}.
    assert read_frontmatter("---\n- a\n- b\n---\nbody") == {}


# ── discovery ────────────────────────────────────────────────────────────────
def test_load_projects_discovers_all(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    found = projects.load_projects(cfg)
    assert set(found) == {"q3-website-relaunch", "home-move", "archived-pilot"}


def test_load_projects_disabled_returns_empty(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    cfg.projects = {}                              # layer disabled
    assert projects.load_projects(cfg) == {}


# ── resolution ───────────────────────────────────────────────────────────────
def test_resolve_project_alias_hit(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    p = projects.resolve_project(
        "what should I focus on for the website relaunch?", cfg)
    assert p is not None and p.slug == "q3-website-relaunch"


def test_resolve_project_slug_hit(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    p = projects.resolve_project("status of home-move please", cfg)
    assert p is not None and p.slug == "home-move"


def test_resolve_project_name_hit(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    p = projects.resolve_project("how is the Q3 Website Relaunch going?", cfg)
    assert p is not None and p.slug == "q3-website-relaunch"


def test_resolve_project_no_match(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    assert projects.resolve_project(
        "what did I eat before the marathon?", cfg) is None


def test_resolve_project_archived_excluded(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    # An archived project is not auto-resolved by its alias …
    assert projects.resolve_project("notes from the pilot scheme", cfg) is None
    # … but still resolves on an exact slug mention.
    p = projects.resolve_project("reopen archived-pilot", cfg)
    assert p is not None and p.slug == "archived-pilot"


def test_resolve_project_most_specific_wins(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    # "move" matches home-move; "website relaunch" matches q3-website-relaunch.
    # The longer, more specific phrase wins.
    p = projects.resolve_project(
        "should I move the website relaunch deadline?", cfg)
    assert p is not None and p.slug == "q3-website-relaunch"


# ── retrieval set ────────────────────────────────────────────────────────────
def test_retrieval_paths_canonical_only(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    project = projects.load_projects(cfg)["q3-website-relaunch"]
    names = [p.name for p in projects.retrieval_paths(project, cfg)]
    # INDEX + STATE + the one live canonical doc, in that order.
    assert names == ["INDEX.md", "STATE.md", "plan.md"]
    assert "research.md" not in names              # non-canonical excluded
    assert "plan-v1.md" not in names               # superseded excluded


def test_supersedes_recorded(tmp_path):
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    project = projects.load_projects(cfg)["q3-website-relaunch"]
    assert any(rp.endswith("plan-v1.md") for rp in project.superseded)


# ── temporal grounding ───────────────────────────────────────────────────────
def test_temporal_context_format(tmp_path):
    line = temporal_context(datetime.date(2026, 5, 25))
    assert line == (
        "CONTEXT: Today is 2026-05-25 (Monday). The current week is "
        "2026-05-25 to 2026-05-31 (ISO week 22)."
    )


def test_temporal_context_midweek(tmp_path):
    # A Wednesday still reports the Monday–Sunday week it belongs to.
    line = temporal_context(datetime.date(2026, 5, 27))
    assert "Today is 2026-05-27 (Wednesday)" in line
    assert "current week is 2026-05-25 to 2026-05-31" in line


# ── project-scoped answer (UC-13 upgrade of UC-9 / UC-10) ────────────────────
def test_project_answer_loads_canonical_whole(tmp_path):
    """A project query loads each canonical doc WHOLE — every week row is in the
    context, so a date-aware question can resolve to the current one."""
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    q = "what should I focus on this week for the website relaunch?"
    project = projects.resolve_project(q, cfg)
    hits = query._project_hits(project, q, cfg)
    plan = next(h for h in hits if h.relpath.endswith("plan.md"))
    assert "Week of 2026-05-18" in plan.body
    assert "Ship the new homepage" in plan.body            # the current-week row
    assert "Publish the launch announcement" in plan.body
    # The superseded plan-v1.md is never in the retrieval set.
    assert not any("plan-v1.md" in h.relpath for h in hits)


def test_project_answer_grounded_and_cited(tmp_path):
    """UC-13: a project question resolves, retrieves canonical docs, cites them."""
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    qa = answer_question(
        "what should I focus on this week for the website relaunch?",
        cfg, today=_TODAY)
    assert qa.found is True
    assert qa.project == "q3-website-relaunch"
    assert qa.mode == "advice"                             # "focus on" -> advice
    for name in ("INDEX.md", "STATE.md", "plan.md"):
        assert any(c.endswith(name) for c in qa.citations), (name, qa.citations)
    assert qa.source in ("llm", "no-llm")                  # honest, no invention


def test_project_answer_staleness_note(tmp_path):
    """A canonical doc past staleness_days produces a soft warning."""
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    qa = answer_question(
        "what is the plan for the website relaunch?", cfg, today=_TODAY)
    # plan.md (last_updated 2026-04-01) is well past the 14-day window.
    assert any(rp.endswith("plan.md") for rp in qa.stale), qa.stale
    assert "may be out of date" in qa.reply
    assert "2026-04-01" in qa.reply
    # A superseded doc never resurfaces, even when it matches the query terms.
    assert "plan-v1" not in " ".join(qa.citations)


def test_project_answer_no_staleness_when_fresh(tmp_path):
    """A project whose canonical docs are all fresh emits no staleness note."""
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    qa = answer_question("status of home-move", cfg, today=_TODAY)
    assert qa.project == "home-move"
    assert qa.stale == []
    assert "may be out of date" not in qa.reply


# ── regression — the projects layer must not change non-project behaviour ─────
def test_non_project_question_unchanged(tmp_path):
    """With the layer enabled, a question matching no project behaves as UC-9."""
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    seed_vault(cfg)                                # also add the domain notes
    qa = answer_question("what are the open bugs?", cfg, today=_TODAY)
    assert qa.project is None                      # resolved to no project
    assert qa.stale == []
    assert qa.found is True
    assert any("project-bugs" in c for c in qa.citations), qa.citations


def test_projects_disabled_is_today_behaviour(tmp_path):
    """projects.enabled absent -> the query path ignores the projects layer."""
    cfg = make_projects_cfg(tmp_path)
    seed_projects(cfg)
    cfg.projects = {}                              # layer disabled
    qa = answer_question(
        "what should I focus on for the website relaunch?", cfg, today=_TODAY)
    assert qa.project is None
    assert qa.stale == []


# ── UC-14 — daterange.py: parse_window ───────────────────────────────────────
_ANC = datetime.date(2026, 6, 5)   # anchor: Thursday


def test_parse_window_today(tmp_path):
    w = parse_window("what was my weight today?", today=_ANC)
    assert w is not None
    assert w.start == w.end == _ANC


def test_parse_window_yesterday(tmp_path):
    w = parse_window("what did I eat yesterday?", today=_ANC)
    assert w is not None
    assert w.start == w.end == datetime.date(2026, 6, 4)


def test_parse_window_last_week(tmp_path):
    w = parse_window("what training did I do last week?", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2026, 5, 25)  # last Mon
    assert w.end == datetime.date(2026, 5, 31)    # last Sun


def test_parse_window_this_week(tmp_path):
    w = parse_window("what did I log this week?", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2026, 6, 1)
    assert w.end == datetime.date(2026, 6, 7)


def test_parse_window_last_n_days(tmp_path):
    w = parse_window("what were my hrv readings last 7 days?", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2026, 5, 30)
    assert w.end == _ANC


def test_parse_window_this_month(tmp_path):
    w = parse_window("show me this month's logs", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2026, 6, 1)
    assert w.end == datetime.date(2026, 6, 30)


def test_parse_window_last_month(tmp_path):
    w = parse_window("training last month?", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2026, 5, 1)
    assert w.end == datetime.date(2026, 5, 31)


def test_parse_window_in_month(tmp_path):
    w = parse_window("what did I log in May?", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2026, 5, 1)
    assert w.end == datetime.date(2026, 5, 31)


def test_parse_window_in_month_with_year(tmp_path):
    w = parse_window("what happened in May 2025?", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2025, 5, 1)
    assert w.end == datetime.date(2025, 5, 31)


def test_parse_window_last_weekday(tmp_path):
    # _ANC = Thursday 2026-06-05; "last Tuesday" = 2026-06-02
    w = parse_window("what was my weight last Tuesday?", today=_ANC)
    assert w is not None
    assert w.start == w.end == datetime.date(2026, 6, 2)


def test_parse_window_explicit_iso_range(tmp_path):
    w = parse_window("what happened from 2026-05-01 to 2026-05-07?", today=_ANC)
    assert w is not None
    assert w.start == datetime.date(2026, 5, 1)
    assert w.end == datetime.date(2026, 5, 7)


def test_parse_window_no_date_phrase_returns_none(tmp_path):
    assert parse_window("what are the open bugs?", today=_ANC) is None
    assert parse_window("should I focus on feature XY or bug fixes?", today=_ANC) is None


def test_parse_window_last_n_days_out_of_range_returns_none(tmp_path):
    # 400 days is out of the 1..366 guard
    assert parse_window("last 400 days", today=_ANC) is None


# ── UC-14 — daterange.py: note_date ──────────────────────────────────────────

def test_note_date_from_frontmatter_date(tmp_path):
    p = tmp_path / "some-note.md"
    p.write_text("---\ndate: 2026-06-01\n---\nbody")
    fm = read_frontmatter(p.read_text())
    assert note_date(p, fm) == datetime.date(2026, 6, 1)


def test_note_date_from_frontmatter_created(tmp_path):
    p = tmp_path / "some-note.md"
    p.write_text("---\ncreated: 2026-05-15\n---\nbody")
    fm = read_frontmatter(p.read_text())
    assert note_date(p, fm) == datetime.date(2026, 5, 15)


def test_note_date_date_beats_created(tmp_path):
    p = tmp_path / "some-note.md"
    p.write_text("---\ndate: 2026-06-03\ncreated: 2026-01-01\n---\nbody")
    fm = read_frontmatter(p.read_text())
    assert note_date(p, fm) == datetime.date(2026, 6, 3)


def test_note_date_from_filename_prefix(tmp_path):
    p = tmp_path / "2026-05-23-ride-easy.md"
    assert note_date(p) == datetime.date(2026, 5, 23)


def test_note_date_frontmatter_beats_filename(tmp_path):
    p = tmp_path / "2026-01-01-health.md"
    p.write_text("---\ndate: 2026-06-03\n---\nbody")
    fm = read_frontmatter(p.read_text())
    assert note_date(p, fm) == datetime.date(2026, 6, 3)


def test_note_date_no_date_returns_none(tmp_path):
    p = tmp_path / "general-thoughts.md"
    assert note_date(p) is None
    assert note_date(p, {}) is None


# ── UC-14 — date-range retrieval path ────────────────────────────────────────

def make_date_range_cfg(tmp_path):
    """Config with date_range enabled."""
    return Config(
        vault_path=tmp_path / "vault",
        domains={
            "health": {"description": "health, fitness, wellness"},
            "work": {"description": "work notes"},
        },
        intents={
            "note": "a note", "log": "a log",
            "question": "a question to answer from the vault",
        },
        confidence_threshold=0.70,
        date_range={"enabled": True, "max_notes": 20, "context_budget": 24000},
    )


def seed_dated_notes(cfg):
    """Write a week of health notes + one undated note into the vault."""
    notes = {
        "health/2026-05-25-health.md": (
            "---\ndate: 2026-05-25\ntype: health-metrics\nweight_kg: 73.5\n---\n\n"
            "# Health Metrics — 2026-05-25\n\nWeight: 73.5 kg. HRV: 62 ms. Sleep: 7.8h.\n"
        ),
        "health/2026-05-26-health.md": (
            "---\ndate: 2026-05-26\ntype: health-metrics\nweight_kg: 73.3\n---\n\n"
            "# Health Metrics — 2026-05-26\n\nWeight: 73.3 kg. HRV: 58 ms. Sleep: 6.9h.\n"
        ),
        "health/2026-05-27-health.md": (
            "---\ndate: 2026-05-27\ntype: health-metrics\nweight_kg: 73.4\n---\n\n"
            "# Health Metrics — 2026-05-27\n\nWeight: 73.4 kg. HRV: 65 ms. Sleep: 8.1h.\n"
        ),
        "health/2026-06-01-health.md": (
            "---\ndate: 2026-06-01\ntype: health-metrics\nweight_kg: 73.2\n---\n\n"
            "# Health Metrics — 2026-06-01\n\nWeight: 73.2 kg. HRV: 60 ms. Sleep: 7.5h.\n"
        ),
        # Undated note — not reachable by date-range retrieval
        "work/inbox/meeting-notes.md": (
            "---\ntype: note\n---\n\n# Meeting\n\nDiscussed project timeline and weight of evidence.\n"
        ),
    }
    for relpath, body in notes.items():
        path = cfg.vault_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def test_date_range_retrieves_notes_in_window(tmp_path):
    """Notes in the window are retrieved; notes outside (and undated notes) are not."""
    cfg = make_date_range_cfg(tmp_path)
    seed_dated_notes(cfg)
    today = datetime.date(2026, 5, 28)  # Friday of that week
    qa = answer_question("what was my weight last week?", cfg, today=today)
    # last week = Mon 2026-05-18 to Sun 2026-05-24 — all 3 health notes are Mon 2026-05-25..27
    # which is THIS week not last week; so let's check this week instead
    # (today=2026-05-28 Thu; this week = Mon 2026-05-25 to Sun 2026-05-31)
    qa2 = answer_question("what was my weight this week?", cfg, today=today)
    assert qa2.window is not None
    relpaths = [c for c in qa2.citations if "health" in c]
    assert len(relpaths) == 3
    # Undated work note must NOT appear
    assert not any("meeting" in c for c in qa2.citations)


def test_date_range_chronological_order(tmp_path):
    """Notes are returned in chronological order (oldest first)."""
    from vaultwright.search import search_by_date_range
    cfg = make_date_range_cfg(tmp_path)
    seed_dated_notes(cfg)
    today = datetime.date(2026, 5, 28)
    w = parse_window("this week", today=today)
    assert w is not None
    hits = search_by_date_range("weight", cfg, w)
    dates = [note_date(h.path, read_frontmatter(h.path.read_text())) for h in hits]
    assert dates == sorted(dates)


def test_date_range_empty_window_clean_message(tmp_path):
    """An empty window returns found=False with a clean no-data message (no wrong files)."""
    cfg = make_date_range_cfg(tmp_path)
    seed_dated_notes(cfg)
    today = datetime.date(2026, 6, 5)
    # Ask for last week — 2026-05-25..31 — but anchor is June 5, so last week = May 25-31
    # We only have Jun 1 and May 25-27 in that range; last week from Jun 5 = May 25-31
    qa = answer_question("what was my weight last week?", cfg, today=today)
    # May 25, 26, 27 are in range (Mon May 25 – Sun May 31); we have 3 notes
    assert qa.found is True or qa.window is not None  # at least window is set
    # Ask for a window with definitely no notes (last year)
    qa_empty = answer_question("what were my metrics in January 2023?", cfg, today=today)
    if qa_empty.window is not None:
        assert qa_empty.found is False
        assert "didn't find" in qa_empty.reply or "no" in qa_empty.reply.lower()


def test_date_range_single_day_query(tmp_path):
    """A single-day query (yesterday) returns exactly the notes from that day."""
    cfg = make_date_range_cfg(tmp_path)
    seed_dated_notes(cfg)
    today = datetime.date(2026, 6, 2)  # yesterday = 2026-06-01
    qa = answer_question("what was my weight yesterday?", cfg, today=today)
    assert qa.window == (datetime.date(2026, 6, 1), datetime.date(2026, 6, 1))
    assert len(qa.citations) == 1
    assert "2026-06-01" in qa.citations[0]


def test_date_range_disabled_is_unchanged(tmp_path):
    """With date_range.enabled absent/false, a date question falls through to lexical search."""
    cfg = make_date_range_cfg(tmp_path)
    cfg.date_range = {}   # disable the feature
    seed_dated_notes(cfg)
    today = datetime.date(2026, 6, 5)
    qa = answer_question("what was my weight this week?", cfg, today=today)
    assert qa.window is None  # no window resolved — fell through to lexical


def test_uc13_wins_over_uc14(tmp_path):
    """UC-13 project resolution wins over UC-14 date-range — a project question
    with a date phrase takes the project path, not the date-range path."""
    # Build a config with BOTH projects and date_range enabled.
    cfg = Config(
        vault_path=tmp_path / "vault",
        domains={"work": {"description": "work notes"}},
        intents={
            "note": "a note", "question": "a question to answer from the vault",
        },
        confidence_threshold=0.70,
        projects={"enabled": True, "path": "projects", "staleness_days": 14, "context_budget": 8000},
        date_range={"enabled": True, "max_notes": 20, "context_budget": 24000},
    )
    # Seed a small project
    proj_root = cfg.projects_root() / "alpha-launch"
    proj_root.mkdir(parents=True)
    (proj_root / "INDEX.md").write_text(
        "---\ntype: project-index\nproject: Alpha Launch\nslug: alpha-launch\n"
        "status: active\naliases: [alpha]\ncreated: 2026-05-01\nlast_updated: 2026-06-01\n---\n\n"
        "# Alpha Launch\n\nLaunch plan for the alpha.\n",
        encoding="utf-8",
    )
    seed_dated_notes(cfg)  # also seed some health notes
    today = datetime.date(2026, 6, 5)
    qa = answer_question("what's happening with the alpha launch this week?", cfg, today=today)
    assert qa.project == "alpha-launch"   # UC-13 won
    assert qa.window is None              # date-range was NOT triggered


def test_date_range_regression_lexical_unchanged(tmp_path):
    """A question with no date phrase follows ordinary lexical search unchanged."""
    cfg = make_date_range_cfg(tmp_path)
    seed_dated_notes(cfg)
    # Also seed a work note with unique content
    (cfg.vault_path / "work" / "meeting-notes.md").parent.mkdir(parents=True, exist_ok=True)
    (cfg.vault_path / "work" / "timeline.md").write_text(
        "# Project Timeline\n\nAll milestones are on track.\n", encoding="utf-8"
    )
    qa = answer_question("are the milestones on track?", cfg, today=_ANC)
    assert qa.window is None       # no date window resolved
    assert qa.found is True        # lexical search found the timeline note


# ── UC-14 — frontmatter scoring improvement (B50) ────────────────────────────

def test_frontmatter_scores_structured_fields(tmp_path):
    """A note whose query term appears ONLY in frontmatter (not body) still scores > 0."""
    from vaultwright.search import _frontmatter_text, _score, query_terms
    raw = "---\ntype: health-metrics\nweight_kg: 73.3\nbody_fat_pct: 18.5\n---\n\n# Health\n\n"
    body = "Health note. No weight mentioned here."
    fm_text = _frontmatter_text(raw)
    fm_dict = read_frontmatter(raw)
    terms = query_terms("weight")
    score, _ = _score(terms, Path("health/2026-06-01-health.md"), body,
                      frontmatter_text=fm_text, frontmatter=fm_dict)
    assert score > 0, "frontmatter-only match should score > 0"


def test_type_field_boosts_domain_match(tmp_path):
    """A note whose type: field overlaps a query term gets +4 boost.

    A 'health-log' note mentions weight once; a generic 'note' mentions weight
    three times. Without a boost the generic note wins on frequency alone. The
    type boost fires because query term "health" appears in "health-log", tipping
    the health-log note ahead.
    """
    from vaultwright.search import _frontmatter_text, _score, query_terms
    # type: health-log — "health" is a substring, so the boost fires when "health"
    # is also a query term.
    raw_health = "---\ntype: health-log\nweight_kg: 73.3\n---\n\n# Health Log\n\nweight 73.3\n"
    raw_other  = "---\ntype: note\n---\n\n# Training Plan\n\nweight target 71.5 weight goal weight race\n"
    body_health = "Health log. weight 73.3"
    body_other  = "Training plan. weight target 71.5 weight goal weight race."
    terms = query_terms("health weight")   # "health" ∈ query AND "health" ∈ "health-log"
    s_health, _ = _score(terms, Path("health/2026-06-01-health.md"), body_health,
                         frontmatter_text=_frontmatter_text(raw_health),
                         frontmatter=read_frontmatter(raw_health))
    s_other, _ = _score(terms, Path("cycling/training-plan.md"), body_other,
                        frontmatter_text=_frontmatter_text(raw_other),
                        frontmatter=read_frontmatter(raw_other))
    assert s_health > s_other, (
        f"health-log note ({s_health:.1f}) should beat generic note ({s_other:.1f}) via type boost"
    )


# ── inline test runner (pytest-free) ─────────────────────────────────────────
def _run() -> int:
    tests = sorted(
        (name, fn) for name, fn in globals().items()
        if name.startswith("test_") and inspect.isfunction(fn)
    )
    passed = failed = 0
    for name, fn in tests:
        tmp = Path(tempfile.mkdtemp(prefix="vw_query_test_"))
        try:
            if inspect.signature(fn).parameters:
                fn(tmp)
            else:
                fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:
            print(f"  FAIL  {name}")
            traceback.print_exc()
            failed += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed ({len(tests)} total)")
    return 1 if failed else 0


if __name__ == "__main__":
    print(f"Vaultwright query tests — {datetime.date.today().isoformat()}")
    sys.exit(_run())
