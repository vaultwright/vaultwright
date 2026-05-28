"""
digest.py — weekly digest job (USE_CASES.md UC-7).

Compiles the past week's inbox activity into a dated digest note in
<vault>/digests/ and sends a summary via the Telegram bot. Invoked by a launchd
job (launchd/com.vaultwright.digest.plist.template).

If the Telegram send fails, the digest note is still written — no silent total
failure (UC-7 acceptance criteria).

Run with:  bash scripts/run.sh digest
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.request

from .config import Config, load
from .env import load_env


def build_digest(cfg: Config, days: int = 7) -> tuple[str, dict]:
    """Return (markdown_body, {domain: [filenames]}) for the last `days` days."""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    per_domain: dict[str, list[str]] = {}

    for domain in sorted(cfg.domains):
        inbox = cfg.inbox(domain)
        if not inbox.exists():
            continue
        recent = [
            p for p in inbox.glob("*.md")
            if datetime.datetime.fromtimestamp(p.stat().st_mtime) >= cutoff
        ]
        if recent:
            per_domain[domain] = sorted(p.name for p in recent)

    total = sum(len(v) for v in per_domain.values())
    today = datetime.date.today().isoformat()
    lines = [
        f"# Weekly digest — {today}",
        "",
        f"{total} item(s) captured in the last {days} days.",
        "",
    ]
    for domain, files in per_domain.items():
        lines.append(f"## {domain} ({len(files)})")
        lines.extend(f"- {name}" for name in files)
        lines.append("")
    if not per_domain:
        lines.append("_No captures this week._")
    return "\n".join(lines), per_domain


def send_telegram(text: str, parse_mode: str | None = None) -> bool:
    """Best-effort Telegram send to every allowed ID. Returns True if any sent.

    Pass ``parse_mode="HTML"`` when ``text`` contains HTML tags (e.g. an
    ``<a href>`` link). Telegram requires explicit parse_mode to render HTML;
    without it the tags are sent as literal characters.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    ids = os.getenv("TELEGRAM_ALLOWED_IDS", "").replace(" ", "")
    if not token or not ids:
        return False
    sent = False
    for chat_id in (x for x in ids.split(",") if x):
        try:
            body: dict = {"chat_id": chat_id, "text": text}
            if parse_mode:
                body["parse_mode"] = parse_mode
            payload = json.dumps(body).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            sent = True
        except Exception:
            pass
    return sent


def _format_summary(total: int, per_domain: dict, note_name: str, today: str) -> str:
    """Build the Telegram summary message.

    Telegram Bot API only linkifies http/https/tg/tel schemes — obsidian://
    links are silently stripped regardless of parse mode. So we send a rich
    plain-text summary instead: per-domain breakdown + the note filename so
    the user can open it via Obsidian's Quick Switcher (Cmd+O).
    """
    lines = [f"📋 Vaultwright digest — {today} — {total} item(s)"]
    if per_domain:
        breakdown = "  ".join(f"{d}: {len(v)}" for d, v in sorted(per_domain.items()))
        lines.append(breakdown)
    lines.append(f"Note: {note_name}")
    return "\n".join(lines)


def main() -> None:
    """Build the weekly digest, write the note, send the Telegram summary."""
    load_env()
    cfg = load()
    today = datetime.date.today().isoformat()
    body, per_domain = build_digest(cfg)

    digests = cfg.digests_dir()
    digests.mkdir(parents=True, exist_ok=True)
    out = digests / f"{today}-digest.md"
    out.write_text(body + "\n", encoding="utf-8")
    print(f"digest written: {out}")

    total = sum(len(v) for v in per_domain.values())
    summary = _format_summary(total, per_domain, out.name, today)
    if send_telegram(summary):
        print("telegram summary sent")
    else:
        print("telegram summary not sent (no token/IDs or send failed) — note still written")


if __name__ == "__main__":
    main()
