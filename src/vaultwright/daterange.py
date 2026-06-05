"""
daterange.py — date-window parsing and note-date extraction (USE_CASES UC-14).

Two public functions:

  parse_window(question, today) -> DateWindow | None
      Detect a temporal expression in a question and resolve it to an inclusive
      (start, end) date range. Returns None when no clear date phrase is present
      so the caller falls through to ordinary lexical search.

  note_date(path, frontmatter) -> datetime.date | None
      Resolve the date a note belongs to from frontmatter (`date:` / `created:`)
      or a leading YYYY-MM-DD filename prefix. Returns None for notes with no
      date signal — they are not reachable by date-range retrieval but remain
      findable by lexical search.

Both functions are pure + injectable (`today` argument) for deterministic tests.
Neither touches the filesystem beyond what the caller passes in.
"""
from __future__ import annotations

import calendar
import datetime
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DateWindow:
    """An inclusive date range resolved from a natural-language expression."""

    start: datetime.date
    end: datetime.date
    label: str   # human-readable: e.g. "last week (2026-05-18 to 2026-05-24)"

    def contains(self, d: datetime.date) -> bool:
        return self.start <= d <= self.end


# ── internal helpers ──────────────────────────────────────────────────────────

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # 3-letter abbreviations
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_DATE_PREFIX_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _iso_week_bounds(day: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Monday and Sunday of the ISO week containing `day`."""
    monday = day - datetime.timedelta(days=day.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def _month_bounds(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last)


# ── parse_window ─────────────────────────────────────────────────────────────

def parse_window(
    question: str,
    today: datetime.date | None = None,
) -> DateWindow | None:
    """Return the date window the question refers to, or None.

    Conservative by design: fires only on an unambiguous date phrase. A false
    positive (treating a non-date question as date-scoped) is worse than a
    miss — the miss falls through to ordinary lexical search. Pattern priority
    is fixed: earlier patterns are checked first; the first match wins.

    Recognised phrases (case-insensitive):
      today · yesterday
      this week · last week
      this month · last month
      this year
      last N days / past N days  (N = 1..366)
      in <Month> [<Year>]        e.g. "in May", "in June 2025"
      last/past <Month>          e.g. "last May", "past June"
      <ISO> to <ISO>             explicit inclusive range (2026-05-01 to 2026-05-07)
      last <weekday>             e.g. "last Monday", "last Tuesday"
    """
    today = today or datetime.date.today()
    t = question.lower()

    # ── today ────────────────────────────────────────────────────────────────
    if re.search(r"\btoday\b", t):
        return DateWindow(today, today, f"today ({today.isoformat()})")

    # ── yesterday ────────────────────────────────────────────────────────────
    if re.search(r"\byesterday\b", t):
        d = today - datetime.timedelta(days=1)
        return DateWindow(d, d, f"yesterday ({d.isoformat()})")

    # ── explicit ISO range  "2026-05-01 to 2026-05-07" ───────────────────────
    m = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*(?:to|through|–|-)\s*(\d{4}-\d{2}-\d{2})", t
    )
    if m:
        try:
            s = datetime.date.fromisoformat(m.group(1))
            e = datetime.date.fromisoformat(m.group(2))
            if s <= e:
                return DateWindow(
                    s, e,
                    f"{m.group(1)} to {m.group(2)}",
                )
        except ValueError:
            pass

    # ── last N days / past N days ─────────────────────────────────────────────
    m = re.search(r"\b(?:last|past)\s+(\d+)\s+days?\b", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 366:
            s = today - datetime.timedelta(days=n - 1)
            return DateWindow(
                s, today,
                f"last {n} days ({s.isoformat()} to {today.isoformat()})",
            )

    # ── this week / last week ─────────────────────────────────────────────────
    if re.search(r"\bthis\s+week\b", t):
        monday, sunday = _iso_week_bounds(today)
        return DateWindow(
            monday, sunday,
            f"this week ({monday.isoformat()} to {sunday.isoformat()})",
        )
    if re.search(r"\blast\s+week\b", t):
        last_mon = today - datetime.timedelta(days=today.weekday() + 7)
        last_sun = last_mon + datetime.timedelta(days=6)
        return DateWindow(
            last_mon, last_sun,
            f"last week ({last_mon.isoformat()} to {last_sun.isoformat()})",
        )

    # ── this month / last month ───────────────────────────────────────────────
    if re.search(r"\bthis\s+month\b", t):
        s, e = _month_bounds(today.year, today.month)
        return DateWindow(s, e, f"this month ({s.isoformat()} to {e.isoformat()})")
    if re.search(r"\blast\s+month\b", t):
        first_of_this = datetime.date(today.year, today.month, 1)
        last_month_last = first_of_this - datetime.timedelta(days=1)
        s, e = _month_bounds(last_month_last.year, last_month_last.month)
        return DateWindow(s, e, f"last month ({s.isoformat()} to {e.isoformat()})")

    # ── this year ─────────────────────────────────────────────────────────────
    if re.search(r"\bthis\s+year\b", t):
        s = datetime.date(today.year, 1, 1)
        e = datetime.date(today.year, 12, 31)
        return DateWindow(s, e, f"this year ({today.year})")

    # ── in <Month> [<Year>] ───────────────────────────────────────────────────
    month_pattern = "|".join(_MONTH_NAMES)
    m = re.search(
        rf"\bin\s+({month_pattern})\s*(\d{{4}})?\b", t
    )
    if m:
        month_num = _MONTH_NAMES[m.group(1)]
        year = int(m.group(2)) if m.group(2) else today.year
        s, e = _month_bounds(year, month_num)
        label = m.group(1).capitalize() + (f" {year}" if m.group(2) else "")
        return DateWindow(s, e, f"in {label} ({s.isoformat()} to {e.isoformat()})")

    # ── last/past <Month> ─────────────────────────────────────────────────────
    m = re.search(
        rf"\b(?:last|past)\s+({month_pattern})\b", t
    )
    if m:
        month_num = _MONTH_NAMES[m.group(1)]
        # Use the most recent occurrence of that month (may be current year or last)
        year = today.year
        if month_num > today.month:
            year -= 1
        s, e = _month_bounds(year, month_num)
        label = m.group(1).capitalize()
        return DateWindow(s, e, f"last {label} ({s.isoformat()} to {e.isoformat()})")

    # ── last <weekday>  e.g. "last Monday" ───────────────────────────────────
    weekday_pattern = "|".join(_WEEKDAY_NAMES)
    m = re.search(rf"\blast\s+({weekday_pattern})\b", t)
    if m:
        target_wd = _WEEKDAY_NAMES[m.group(1)]
        delta = (today.weekday() - target_wd) % 7
        if delta == 0:
            delta = 7   # "last Monday" when today IS Monday → 7 days ago
        d = today - datetime.timedelta(days=delta)
        return DateWindow(d, d, f"last {m.group(1).capitalize()} ({d.isoformat()})")

    # ── bare weekday name (most recent past occurrence) ───────────────────────
    # Only fire when a weekday is the clear temporal anchor, not as a random word.
    # Require it to be adjacent to a health/metric/query verb to avoid false hits
    # on e.g. "I have a meeting on Friday".
    m = re.search(
        rf"\b({weekday_pattern})\b", t
    )
    if m:
        # Only resolve if there's also a clear date-query signal nearby
        if re.search(
            r"\b(what|when|how much|weigh|weight|hrv|sleep|rhr|log|did i|was my|were my)\b", t
        ):
            target_wd = _WEEKDAY_NAMES[m.group(1)]
            delta = (today.weekday() - target_wd) % 7
            if delta == 0:
                delta = 7
            d = today - datetime.timedelta(days=delta)
            return DateWindow(d, d, f"{m.group(1).capitalize()} ({d.isoformat()})")

    return None


# ── note_date ─────────────────────────────────────────────────────────────────

def note_date(
    path: Path,
    frontmatter: dict | None = None,
) -> datetime.date | None:
    """The date a note belongs to, or None when it is not date-addressable.

    Priority:
      1. frontmatter `date:` field (ISO date string or datetime.date)
      2. frontmatter `created:` field (same formats)
      3. leading YYYY-MM-DD prefix in the filename

    A note with no resolvable date is excluded from date-range retrieval; it is
    still reachable by ordinary lexical search.
    """
    if frontmatter:
        for key in ("date", "created"):
            val = frontmatter.get(key)
            if val is None:
                continue
            if isinstance(val, datetime.date):
                # yaml.safe_load may return a datetime.date or datetime.datetime
                return val.date() if isinstance(val, datetime.datetime) else val
            s = str(val).strip()[:10]
            try:
                return datetime.date.fromisoformat(s)
            except ValueError:
                pass

    # Filename prefix: 2026-05-23-...md  or  2026-05-23 — Race.md
    m = _DATE_PREFIX_RE.match(path.stem)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None
