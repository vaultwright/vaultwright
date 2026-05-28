# Vaultwright — Setup Guide

From a clean machine to a working personal AI-OS. Budget ~20–30 minutes.

## Prerequisites

- **macOS** — the scheduled jobs use `launchd`. (Linux/`systemd` is on the roadmap.)
- **Python 3.11+** — check with `python3 --version`.
- **A Telegram account** — to create your capture bot.
- **An LLM API key** — Anthropic by default ([console.anthropic.com](https://console.anthropic.com)).
- **An Obsidian vault** — or just an empty folder; Vaultwright will populate it.

---

## Step 1 — Clone the repo

```bash
git clone <repo-url> vaultwright
cd vaultwright
```

## Step 2 — Define your domains

Open `config/domains.yaml`. This file *is* your system — the classifier, the
router, and the vault folders all derive from it.

- Set `vault_path` to where your Obsidian vault lives (e.g. `~/Documents/vault`).
- Edit the `domains:` list — add, rename, or remove domains. Give each a clear
  `description`; the classifier uses it to decide where messages go, so accuracy
  here directly improves auto-filing.

You can come back and change this any time — re-running setup picks up new domains.

## Step 3 — Create your Telegram bot

1. In Telegram, open a chat with [@BotFather](https://t.me/botfather).
2. Send `/newbot` and follow the prompts (name + username).
3. BotFather replies with a **bot token** — copy it.

## Step 4 — Fill in your secrets

```bash
cp .env.example .env
```

Edit `.env`:

- `TELEGRAM_BOT_TOKEN` — the token from BotFather.
- `ANTHROPIC_API_KEY` — your LLM API key.
- `TELEGRAM_ALLOWED_IDS` — leave blank for now; you will get your ID in Step 6.

`.env` is gitignored — it is never committed.

## Step 5 — Run setup

```bash
bash scripts/setup.sh
```

This creates the Python virtual environment, installs dependencies, and builds
your vault skeleton (a folder + `inbox/` for each domain, plus `digests/`).

## Step 6 — Start the bot and capture your first note

```bash
bash scripts/run.sh bot
```

Open Telegram and message your bot. The first time, it will reply with your
**Telegram user ID**. Stop the bot (`Ctrl-C`), add that ID to
`TELEGRAM_ALLOWED_IDS` in `.env`, and start it again.

Now message it a real thought — e.g. *"check out this article https://example.com"*.
Within a few seconds it classifies the message, files a Markdown note into the
right domain's `inbox/`, and replies telling you where. Open your vault in Obsidian
and you will see the note. **That is the core loop.**

You can also **ask it questions**. Once you have captured a few notes, message a
question — e.g. *"what links did I save this week?"* or *"what should I follow up
on?"*. Instead of filing the message, Vaultwright searches your vault, answers from
the matching notes, and cites them. If nothing relevant is found, it tells you so
rather than guessing.

## Step 7 — Install the scheduled jobs

```bash
bash scripts/install_jobs.sh
```

This installs two `launchd` jobs:

- **Weekly digest** — a summary of what you captured, as a digest note and a
  Telegram message.
- **Vault backup** — commits your vault to git on a schedule, so your knowledge
  base is always versioned.

## Step 8 — Add a project (optional)

A **project** is a folder in your vault for a time-bound, multi-document effort
— a plan, a roadmap, a renovation, a launch. You can ask it questions like
*"what should I focus on this week for the website relaunch?"* and Vaultwright
answers from that project's own documents, with today's real date in mind.

Create one with:

```bash
bash scripts/new-project.sh q3-website-relaunch "Q3 Website Relaunch"
```

This creates `projects/q3-website-relaunch/` in your vault with two files:

- `INDEX.md` — the project hub. Edit its `aliases:` line to list the words you
  would naturally use to refer to the project; that is how a question reaches
  it. Avoid single generic words like "plan" — they are ignored.
- `STATE.md` — a small "what's true right now" snapshot. Keep it short and
  update its `last_updated` date whenever you touch it.

For a long, authoritative document — a plan with a week-by-week table — copy
`templates/project/DOC.md` into the project folder under a real name (e.g.
`plan.md`). It carries `canonical: true`, so the query path loads it **whole**
when a question resolves to the project. Structure time-bound work under dated
headings so a "this week" question can land on the right section. If a new doc
replaces an old one, list the old filename in the new doc's `supersedes:` field
so the stale version drops out of answers.

The projects layer is controlled by the `projects:` block in
`config/domains.yaml` — it ships enabled. A canonical document older than
`staleness_days` (14 by default) earns a soft "may be out of date" note on
answers, a nudge to keep it current. Remove the `projects:` block to turn the
layer off entirely.

---

## Troubleshooting

- **Bot doesn't respond** — check `TELEGRAM_BOT_TOKEN` in `.env`; make sure the bot
  process is running (`bash scripts/run.sh bot`).
- **"Unauthorised" reply** — your Telegram ID is not in `TELEGRAM_ALLOWED_IDS`.
  Re-do the end of Step 6.
- **Notes not appearing** — confirm `vault_path` in `config/domains.yaml` points at
  the right folder, and re-run `bash scripts/setup.sh`.
- **Classification seems off** — improve the `description` fields in
  `config/domains.yaml`; they are what the classifier reads.
- **A project question isn't recognised** — check the project's `INDEX.md` has
  `aliases:` covering how you phrase it, and that the `projects:` block in
  `config/domains.yaml` is present with `enabled: true`.
