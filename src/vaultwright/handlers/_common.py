"""
_common.py — shared helpers for Vaultwright handlers.

write_note()  — drop a timestamped Markdown note into an inbox folder.
append_line() — append a line to a Markdown file (optionally under a date heading).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class HandlerResult:
    """Outcome of one handler run."""

    success: bool
    path: Optional[Path]
    reply: str


def slugify(text: str, maxlen: int = 50) -> str:
    """Make a filesystem-friendly slug from arbitrary text."""
    out = "".join(c if c.isalnum() or c in " -_" else " " for c in text)
    out = "-".join(out.split()).lower()
    return out[:maxlen].rstrip("-") or "note"


def _frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is not None:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def write_note(
    inbox: Path,
    text: str,
    *,
    intent: str,
    domain: str,
    confidence: float,
    title: Optional[str] = None,
) -> Path:
    """Write a timestamped Markdown note into `inbox`. Returns the file path."""
    inbox.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    title = (title or text.split("\n", 1)[0]).strip()
    path = inbox / f"{stamp}-{slugify(title)}.md"
    front = _frontmatter(
        {
            "type": intent,
            "domain": domain,
            "source": "telegram",
            "captured": now.isoformat(timespec="seconds"),
            "confidence": round(confidence, 2),
        }
    )
    path.write_text(f"{front}\n\n{text.strip()}\n", encoding="utf-8")
    return path


def append_line(target: Path, line: str, *, date_heading: bool = False) -> Path:
    """Append `line` to a Markdown file, creating it if needed.

    With date_heading=True, ensures a `## YYYY-MM-DD` heading for today exists
    and appends the line beneath it (entries arrive in time order, so today's
    heading is always last).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""

    if date_heading:
        heading = f"## {datetime.date.today().isoformat()}"
        if heading not in existing:
            prefix = (existing.rstrip() + "\n\n") if existing.strip() else ""
            existing = f"{prefix}{heading}\n"
        body = existing.rstrip() + "\n" + line + "\n"
    else:
        prefix = (existing.rstrip() + "\n") if existing.strip() else ""
        body = prefix + line + "\n"

    target.write_text(body, encoding="utf-8")
    return target
