"""
classifier.py — LLM intent classifier (config-driven).

Takes a captured message and returns a Classification — (domain, intent,
confidence). Domains and intents come entirely from config/domains.yaml, so the
classifier always reflects the user's own setup (USE_CASES.md UC-6).

The LLM call uses a cheap, fast model. If it fails (no key, timeout, bad JSON)
a local heuristic fallback runs so a capture is never lost.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .config import Config

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
URL_RE = re.compile(r"https?://\S+")


@dataclass
class Classification:
    """Result of classifying one captured message."""

    domain: str
    intent: str
    confidence: float          # 0.0–1.0
    rationale: str = ""
    source: str = "llm"        # "llm" | "heuristic"


# ── Prompt ───────────────────────────────────────────────────────────────────
def _build_prompt(text: str, cfg: Config) -> tuple[str, str]:
    domain_lines = "\n".join(
        f"- {name}: {meta.get('description', '')}"
        for name, meta in cfg.domains.items()
    )
    intent_lines = "\n".join(f"- {name}: {desc}" for name, desc in cfg.intents.items())
    system = f"""You classify a personal note into exactly one domain and one intent.

DOMAINS:
{domain_lines}

INTENTS:
{intent_lines}

Respond with ONLY a JSON object, no prose:
{{"domain": "<one domain key>", "intent": "<one intent key>", "confidence": <0.0-1.0>, "rationale": "<one short sentence>"}}

Rules:
- confidence reflects certainty: use 0.85+ only when unambiguous, 0.50-0.70 when unsure.
- if the message ASKS for information or advice — it wants an answer back rather
  than recording something — intent = question (only if 'question' is listed above).
- if the message is mainly a URL/link to read later, intent = link.
- if it is something to do or be reminded of, intent = task.
- if it is a short dated log or journal entry, intent = log.
- otherwise intent = note.
- for a question the domain is only a hint; still pick the most likely one.
- pick the single best domain; when genuinely unsure, lower the confidence."""
    user = f"Message:\n---\n{text}\n---"
    return system, user


def _parse_json(raw: str) -> dict:
    """Extract a JSON object from an LLM response, tolerating surrounding prose."""
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"no JSON in LLM response: {raw[:200]}")


def _validate(parsed: dict, cfg: Config, source: str) -> Classification:
    """Coerce an LLM/heuristic result onto the configured domains and intents."""
    domain = parsed.get("domain")
    if domain not in cfg.domains:
        domain = next(iter(cfg.domains))
    intent = parsed.get("intent")
    if intent not in cfg.intents:
        intent = "note" if "note" in cfg.intents else next(iter(cfg.intents))
    try:
        conf = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = min(1.0, max(0.0, conf))
    return Classification(
        domain=domain,
        intent=intent,
        confidence=conf,
        rationale=str(parsed.get("rationale", "")),
        source=source,
    )


# ── Classifiers ──────────────────────────────────────────────────────────────
def _llm_call(system: str, user: str) -> str:
    """Delegate to shared.llm if available; fall back to direct anthropic call."""
    try:
        from shared.llm import call as llm_call  # _shared/ on PYTHONPATH (dogfood)
        return llm_call(system=system, user=user, max_tokens=200, temperature=0, timeout=10.0)
    except ImportError:
        pass
    # Standalone path — open-core install without _shared/.
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic  # lazy

    model = os.getenv("VAULTWRIGHT_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic(api_key=api_key, timeout=10.0)
    resp = client.messages.create(
        model=model,
        max_tokens=200,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


def classify_llm(text: str, cfg: Config) -> Classification:
    """Classify via the LLM. Raises on any failure (caller falls back)."""
    system, user = _build_prompt(text, cfg)
    raw = _llm_call(system, user)
    return _validate(_parse_json(raw), cfg, source="llm")


def classify_heuristic(text: str, cfg: Config) -> Classification:
    """Local fallback — no API. Intent by simple rules, domain by keyword overlap."""
    lowered = text.strip().lower()

    if URL_RE.search(text):
        intent = "link"
    elif re.search(r"\b(todo|to-do|remind me|don'?t forget|need to|task:)\b", lowered):
        intent = "task"
    elif lowered.endswith("?"):
        # Ending in '?' is the one unambiguous offline signal for a question.
        # Questions phrased without '?' are left to the LLM classifier — the
        # heuristic stays conservative so imperatives ("do laundry") are never
        # mistaken for questions and lost instead of captured.
        intent = "question"
    elif re.search(r"\b(log:|today i|felt|feeling)\b", lowered):
        intent = "log"
    else:
        intent = "note"
    if intent not in cfg.intents:
        # 'question' may not be configured — fall back to a safe capture intent.
        intent = "note" if "note" in cfg.intents else next(iter(cfg.intents))

    msg_words = set(re.findall(r"\w+", lowered))
    best, best_score = None, 0
    for name, meta in cfg.domains.items():
        text_blob = f"{name} {meta.get('description', '')}".lower()
        score = len(set(re.findall(r"\w+", text_blob)) & msg_words)
        if score > best_score:
            best, best_score = name, score
    domain = best or next(iter(cfg.domains))
    # A clear interrogative is a high-confidence intent call even offline; the
    # domain barely matters for a question (search is vault-wide), so don't let a
    # weak domain match drag a question into the UC-5 confirmation gate.
    if intent == "question":
        confidence = 0.78
    else:
        confidence = 0.55 if best_score else 0.40
    return Classification(
        domain=domain,
        intent=intent,
        confidence=confidence,
        rationale="Heuristic fallback (no LLM).",
        source="heuristic",
    )


def classify(text: str, cfg: Config) -> Classification:
    """Classify a message. Tries the LLM, falls back to the heuristic."""
    if not text or not text.strip():
        return Classification(
            next(iter(cfg.domains)), "note", 0.0, "Empty input.", source="heuristic"
        )
    try:
        return classify_llm(text, cfg)
    except Exception:
        return classify_heuristic(text, cfg)
