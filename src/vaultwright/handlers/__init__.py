"""
handlers/ — one handler per intent.

A handler takes the message text, its Classification, and the Config, and writes
the note into the right place in the vault. `dispatch()` selects the handler.

Intents (from config/domains.yaml): note, link, task, log.
- note → its own timestamped Markdown file in <domain>/inbox/
- link → its own file, with the URL preserved (UC-2)
- task → a checklist line appended to <domain>/inbox/tasks.md (UC-3)
- log  → a dated line appended to <domain>/inbox/log.md (UC-4)
"""
from __future__ import annotations

import datetime
import re

from ..classifier import Classification
from ..config import Config
from ._common import HandlerResult, append_line, write_note

_URL_RE = re.compile(r"https?://\S+")


def handle_note(text: str, cls: Classification, cfg: Config) -> HandlerResult:
    path = write_note(
        cfg.inbox(cls.domain), text,
        intent="note", domain=cls.domain, confidence=cls.confidence,
    )
    return HandlerResult(True, path, f"note → {cls.domain}/inbox/{path.name}")


def handle_link(text: str, cls: Classification, cfg: Config) -> HandlerResult:
    match = _URL_RE.search(text)
    body = text if not match else f"{text}\n\nURL: {match.group(0)}"
    path = write_note(
        cfg.inbox(cls.domain), body,
        intent="link", domain=cls.domain, confidence=cls.confidence,
    )
    return HandlerResult(True, path, f"link → {cls.domain}/inbox/{path.name}")


def handle_task(text: str, cls: Classification, cfg: Config) -> HandlerResult:
    target = cfg.inbox(cls.domain) / "tasks.md"
    append_line(target, f"- [ ] {text.strip()}")
    return HandlerResult(True, target, f"task → {cls.domain}/inbox/tasks.md")


def handle_log(text: str, cls: Classification, cfg: Config) -> HandlerResult:
    target = cfg.inbox(cls.domain) / "log.md"
    stamp = datetime.datetime.now().strftime("%H:%M")
    append_line(target, f"- {stamp} {text.strip()}", date_heading=True)
    return HandlerResult(True, target, f"log → {cls.domain}/inbox/log.md")


REGISTRY = {
    "note": handle_note,
    "link": handle_link,
    "task": handle_task,
    "log": handle_log,
}


def dispatch(text: str, cls: Classification, cfg: Config) -> HandlerResult:
    """Route a classified message to its handler (note handler is the fallback)."""
    handler = REGISTRY.get(cls.intent, handle_note)
    return handler(text, cls, cfg)
