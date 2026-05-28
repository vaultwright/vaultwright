---
type: project-index
project: "{{PROJECT}}"
slug: {{SLUG}}
status: active
aliases: [{{ALIASES}}]
created: {{CREATED}}
last_updated: {{LAST_UPDATED}}
---

# {{PROJECT}}

> Project hub — identity, status, and links. Keep this short; it is loaded
> whole on every project query.

## What this is

One or two sentences: the goal of this project and why it exists.

## Status

The current phase in a line. The living detail belongs in `STATE.md`.

## Documents

- `STATE.md` — what is true right now (kept small, always current).
- A canonical doc — the authoritative plan/roadmap. Create one from
  `templates/project/DOC.md` and give it a real name (e.g. `plan.md`).
- Supporting notes — ordinary notes in this folder; reachable through search.

## Aliases

The `aliases:` field above is how a question reaches this project. Add every
natural phrasing you would actually type — the more, the better the resolver
works. Avoid single generic words (e.g. "plan", "project"); they are ignored.
