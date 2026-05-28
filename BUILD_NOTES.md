# Build notes — Vaultwright (dev working copy)

This directory is the **development working copy** of the Vaultwright open-core
kit, built inside the Self-Funding project. It is **not** the published repo.

## Publishing rules

- The published repo is a **fresh git repo** under a separate neutral GitHub org
  (`vaultwright`), created with clean history at publish time — never a `git
  filter` of an existing repo. **Not** under `github.com/rkanocz` or `rasta-code`.
- Before publish: `bash scripts/sanitisation_sweep.sh` must pass, and a secret
  scan (e.g. `gitleaks`) must pass over the repo and its full history.
- Build spec: `../USE_CASES.md`. Distribution: `../DISTRIBUTION.md`.
  Sanitisation rules: `../SANITISATION_AUDIT.md`.

## Status — 2026-05-24 (Phase 2 — capture loop + query side implemented)

**Implemented + tested:** the full capture loop — `config.py`, `env.py`,
`classifier.py` (config-driven LLM classification + heuristic fallback),
`router.py` (confidence gate), `handlers/` (note / link / task / log), `bot.py`
(Telegram), `digest.py` (UC-7), `scaffold.py`. Scheduled jobs: `git_autocommit.sh`
(UC-8), `install_jobs.sh`, `launchd/*.plist.template`.

**The query side (UC-9 + UC-10):** `search.py` (grep-style lexical vault search —
no embeddings, no index) and `query.py` (grounded answer with deterministic
citations; QA mode and advice mode; honest "not found"; honest no-LLM fallback).
A `question` intent was added to `config/domains.yaml` and the classifier; the
router routes it to `query.answer_question()` instead of a write handler; the bot
answers instead of filing. All channel-agnostic and config-driven — `bot.py` stays
a thin adapter (portability principle).

39 offline tests pass — `tests/test_capture.py` (14) + `tests/test_query.py` (25);
the sanitisation sweep passes; all scripts syntax-check. Tests run pytest-free via
an inline runner (`python3 tests/test_*.py`).

**Not yet verified live (needs a real run with a bot token + API key):** the
Telegram path (`bot.py`), live LLM classification (`classify_llm`), and live LLM
answer synthesis (`query._call_llm`). These can only be verified by running the
bot — not possible in the sandbox. Routing, handlers, the gate, heuristic
classification, vault search, grounding, citation, and the no-LLM fallback *are*
covered end-to-end by the offline tests.

**Roadmap / not in MVP:** `lang_detector` (UC-11 multilingual), inbox-processing
daemon (UC-12), semantic/embedding search. The UC-1 + UC-9 demo GIF still to be
recorded. No agentic action-triggering — explicitly out of the open core.

## Status — 2026-05-25 (Phase 2 — UC-13 projects layer)

Built UC-13 from `../SPEC_projects_layer.md` (the contract). Adds a config-driven
`projects/` vault structure parallel to domains, a frontmatter contract
(`canonical` / `status` / `supersedes`), and a date-aware, project-scoped query
path. New module `projects.py` (discover / resolve / `retrieval_paths`);
`search.py` gains `read_frontmatter()`, a subtree `scope` for `search_vault()`,
and `load_note_as_hit()`; `query.py` gains `temporal_context()` (a `CONTEXT:`
date line on every prompt), project-scoped retrieval (canonical docs loaded
whole at `project_context_budget`), and a staleness note; `scaffold.py` gains
`scaffold_project()` + an argparse CLI; `templates/project/` + `scripts/new-project.sh`
ship the no-code "new project" flow; `config/domains.yaml` gets a `projects:`
block (ships enabled).

Tier 1 only (lexical, whole-document, date injection) — no embeddings, no index,
no new intent, read-only. Project resolution lives inside the existing `question`
path; the bot stays a thin adapter.

60 offline tests pass — `tests/test_capture.py` (14) + `tests/test_query.py` (46:
the original 25 + 21 UC-13 tests, generic fixtures only). The sanitisation sweep
passes.

**Spec-tension decision (recorded):** SPEC §11 #7 ("`projects.enabled: false` →
byte-for-byte today's behaviour") vs §8.1 ("temporal grounding applies to *every*
query"). Resolution: the `projects.enabled` flag gates project resolution,
retrieval, and staleness only; temporal grounding is a separate, unconditional
sub-feature. The offline-observable path (the no-LLM reply, which the tests
assert on) *is* byte-for-byte identical when disabled — the `CONTEXT:` line only
ever lands in the LLM prompt string, never in a no-LLM reply. Verified by
`test_projects_disabled_is_today_behaviour` and `test_non_project_question_unchanged`.

**Not yet verified live:** project-scoped LLM synthesis (`query._call_llm` on the
project path) — needs a real API key, Phase 3 QA. Offline, a project query runs
the honest no-LLM fallback (canonical-doc excerpts + citations + staleness note).
