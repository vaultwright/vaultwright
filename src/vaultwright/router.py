"""
router.py — classify a message, then file it, answer it, or ask for confirmation.

Two paths share one entry point:
- capture intents (note/link/task/log) → a handler writes the note into the vault.
- the query intent (question)          → `query.answer_question` reads an answer
                                          back out of the vault (USE_CASES UC-9/10).

The confidence gate (USE_CASES.md UC-5) lives here: when a Classification's
confidence is below cfg.confidence_threshold, route() returns a result that asks
the bot to request confirmation instead of acting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .classifier import Classification, classify
from .config import Config
from .handlers import dispatch
from .query import answer_question

QUERY_INTENT = "question"


@dataclass
class RouteResult:
    """Outcome of routing one message."""

    classification: Classification
    requires_confirmation: bool
    reply: str
    written_path: Optional[str] = None
    is_query: bool = False          # True when reply is a query answer, not a filing


def route(
    text: str,
    cfg: Config,
    *,
    confirmed: bool = False,
    classification: Optional[Classification] = None,
) -> RouteResult:
    """Classify `text` and either file it, answer it, or ask the user to confirm.

    Pass `classification` to reuse an existing one (e.g. on a confirmation reply)
    instead of paying for a second LLM call.
    """
    cls = classification or classify(text, cfg)
    is_question = cls.intent == QUERY_INTENT

    # UC-5 confidence gate — applies to capture and query alike. A low-confidence
    # 'question' call means the classifier is not sure it *is* a question, so we
    # still confirm before acting.
    if not confirmed and cls.confidence < cfg.confidence_threshold:
        pct = int(cls.confidence * 100)
        if is_question:
            reply = (
                f"This looks like a question ({pct}% sure) — reply 'yes' and I'll "
                f"answer it from your vault, or rephrase it and send again."
            )
        else:
            reply = (
                f"I think this is a {cls.intent} in '{cls.domain}' ({pct}% sure).\n"
                f"Reply 'yes' to confirm, or send a domain name to redirect "
                f"({', '.join(sorted(cfg.domains))})."
            )
        return RouteResult(cls, requires_confirmation=True, reply=reply)

    # Query path (UC-9 / UC-10) — read-only: answer from the vault, write nothing.
    if is_question:
        qa = answer_question(text, cfg)
        return RouteResult(
            cls,
            requires_confirmation=False,
            reply=qa.reply,
            written_path=None,
            is_query=True,
        )

    # Capture path — a handler writes the note into the vault.
    result = dispatch(text, cls, cfg)
    return RouteResult(
        cls,
        requires_confirmation=False,
        reply=result.reply,
        written_path=str(result.path) if result.path else None,
    )
