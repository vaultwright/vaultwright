"""
bot.py — Telegram bot: capture into the vault and query back out of it.

A thin channel adapter (USE_CASES.md portability principle): it does Telegram
I/O and calls `route()` — all the logic lives in the channel-agnostic core
(`classifier` / `router` / `handlers` / `search` / `query`).

It receives messages, checks the sender against TELEGRAM_ALLOWED_IDS, and routes
each one. A capture message (note/link/task/log) is filed and the reply names
where; a question (UC-9 / UC-10) is answered from the vault instead of filed.
The UC-5 confirmation flow handles low-confidence messages: the user replies
'yes' to confirm, or sends a domain name to redirect.

Run with:  bash scripts/run.sh bot
"""
from __future__ import annotations

import logging
import os

from .classifier import Classification
from .config import load
from .env import load_env
from .router import route

log = logging.getLogger("vaultwright.bot")

_AFFIRMATIONS = {"yes", "y", "ok", "okay", "confirm", "✅", "👍"}
_DISMISSALS   = {"no", "n", "nope", "cancel", "delete", "discard", "skip", "❌", "👎"}


def _allowed_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_IDS", "").replace(" ", "")
    out: set[int] = set()
    for chunk in raw.split(","):
        if chunk:
            try:
                out.add(int(chunk))
            except ValueError:
                log.warning("Ignoring non-numeric TELEGRAM_ALLOWED_IDS entry: %r", chunk)
    return out


async def _on_start(update, context) -> None:
    await update.message.reply_text(
        "Vaultwright bot.\n"
        "• Send a note, link, task, or log entry — I'll classify it and file it "
        "into the right place in your vault.\n"
        "• Ask a question — I'll answer it from your vault and cite the notes."
    )


def _format_reply(res) -> str:
    """A query answer is sent as-is; a filing gets the ✓ confirmation prefix."""
    return res.reply if res.is_query else f"✓ {res.reply}"


async def _on_message(update, context) -> None:
    cfg = context.bot_data["cfg"]
    allowed = context.bot_data["allowed"]
    user = update.effective_user
    uid = user.id if user else 0
    text = (update.message.text or "").strip()

    # Auth (UC-1 acceptance: unauthorised users are rejected).
    if not allowed:
        await update.message.reply_text(
            f"Your Telegram ID is {uid}.\n"
            f"Add it to TELEGRAM_ALLOWED_IDS in .env, then restart the bot."
        )
        return
    if uid not in allowed:
        await update.message.reply_text(f"Unauthorised. Your Telegram ID is {uid}.")
        return
    if not text:
        return

    # UC-5 — resolve a pending confirmation, if any.
    pending = context.user_data.pop("pending", None)
    if pending:
        lowered = text.lower()
        if lowered in _AFFIRMATIONS:
            res = route(pending["text"], cfg, confirmed=True,
                        classification=pending["cls"])
            await update.message.reply_text(_format_reply(res))
            return
        if lowered in _DISMISSALS:
            await update.message.reply_text("Discarded.")
            return
        if lowered in cfg.domains:
            redirect = Classification(lowered, pending["cls"].intent, 1.0,
                                      "User redirect.")
            res = route(pending["text"], cfg, confirmed=True, classification=redirect)
            await update.message.reply_text(_format_reply(res))
            return
        # Not a confirmation — fall through and treat `text` as a fresh message.

    res = route(text, cfg)
    if res.requires_confirmation:
        context.user_data["pending"] = {"text": text, "cls": res.classification}
        await update.message.reply_text(res.reply)
    else:
        await update.message.reply_text(_format_reply(res))


def main() -> None:
    """Start the Telegram capture bot."""
    load_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set — fill in .env (see .env.example).")

    # Allow VAULTWRIGHT_CONFIG env var to point at a personal config file
    # (e.g. for dogfood setups where vault_path differs from the template).
    custom_cfg = os.getenv("VAULTWRIGHT_CONFIG")
    cfg = load(custom_cfg) if custom_cfg else load()
    from telegram.ext import (  # lazy — keeps the module importable without the dep
        Application,
        CommandHandler,
        MessageHandler,
        filters,
    )

    app = Application.builder().token(token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["allowed"] = _allowed_ids()
    app.add_handler(CommandHandler("start", _on_start))
    app.add_handler(CommandHandler("help", _on_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

    log.info(
        "Vaultwright bot starting — vault=%s domains=%s",
        cfg.vault_path, ", ".join(sorted(cfg.domains)),
    )
    app.run_polling()


if __name__ == "__main__":
    main()
