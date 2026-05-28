# Vaultwright

**Build your second brain by messaging it — then ask it anything.
Local-first, open-source.**

Vaultwright turns a plain Obsidian vault into a personal AI operating system. You
message a Telegram bot from your phone; an LLM works out what each message is and
files it into the right place in your vault — automatically. Ask it a question and
it answers from your own notes, citing them. Background jobs run on a schedule.
Your notes stay plain Markdown in a git repo you own.

> **Status:** open-core, in active development. macOS-first. Bring your own LLM API key.

---

## What it does

```
Telegram message ──▶ intent classifier ──▶ router ──┬──▶ filed in your vault
                                              │      │
                              not sure? ──────┘      └──▶ a question? answered
                              bot asks you to confirm      from your vault, cited
```

- **Capture from anywhere** — message the Telegram bot; the note is classified and
  filed into the right inbox folder. No tagging, no manual sorting.
- **Ask your vault** — message a question and Vaultwright searches your notes,
  answers from them, and cites the source note(s). It also reasons over your notes
  for advice. If it finds nothing relevant, it says so — it never invents.
- **Projects** — group a time-bound effort into a `projects/` folder, then ask it
  time-relative questions ("what should I focus on this week?"). Vaultwright knows
  today's date, loads that project's own documents, and answers from them, cited.
- **Your domains, your rules** — define your own domains in one config file
  (`config/domains.yaml`). The classifier adapts. No code changes.
- **It asks when unsure** — low-confidence messages get a confirmation prompt
  instead of being silently misfiled.
- **Scheduled jobs** — a weekly digest of what you captured, and an automatic git
  backup of your vault, both via `launchd`.
- **Local-first** — plain Markdown in a git repo on your machine. The only outbound
  call is to your own LLM API key. No SaaS owns your data.

## Quick start

```bash
git clone <repo-url> vaultwright && cd vaultwright
cp .env.example .env          # add your Telegram + LLM keys
$EDITOR config/domains.yaml   # define your domains
bash scripts/setup.sh         # creates the vault skeleton + venv
```

Full walkthrough — including creating the Telegram bot and installing the scheduled
jobs — is in [`docs/SETUP.md`](docs/SETUP.md).

## Architecture

The logic is a small, channel-agnostic Python library — `classifier`, `router`,
`handlers`, `search`, `query` — all driven by `config/domains.yaml`. The Telegram
bot is a thin adapter on top. That means the capture and query capabilities are
**portable**: you can run Vaultwright standalone, or import the modules to extend a
bot you already run. Nothing in the core logic is tied to Telegram.

## Requirements

- macOS (the scheduled jobs use `launchd`; Linux/`systemd` is on the roadmap).
- Python 3.11+.
- A Telegram account (to create a bot via [@BotFather](https://t.me/botfather)).
- An LLM API key (Anthropic by default).

## Vaultwright Complete Kit

This repo is fully usable on its own. The **Complete Kit** is the finish-the-job
version — polished domain templates, an illustrated step-by-step setup guide, config
presets, and a written end-to-end walkthrough — for people who would rather not
assemble the pieces themselves.

> 🔗 *Link added at launch.*

## License

MIT — see [`LICENSE`](LICENSE).
