"""
Offline tests for the Vaultwright capture loop.

Covers everything that does not need a live LLM or live Telegram: config,
handlers, the router confidence gate, the classifier heuristic + JSON parsing,
and the digest.

pytest is not required. Run directly:

    PYTHONPATH=src python3 tests/test_capture.py

The bottom-of-file runner gives each test a fresh temp directory. The test
functions also work unchanged under pytest (the `tmp_path` fixture).
"""
import datetime
import inspect
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vaultwright import digest
from vaultwright.classifier import Classification, _parse_json, classify_heuristic
from vaultwright.config import Config
from vaultwright.handlers import dispatch
from vaultwright.handlers._common import append_line, slugify, write_note
from vaultwright.router import route


def make_cfg(tmp_path):
    return Config(
        vault_path=tmp_path / "vault",
        domains={
            "work": {"description": "work projects and meetings"},
            "reading": {"description": "articles links videos to read"},
            "personal": {"description": "personal thoughts and journal"},
        },
        intents={"note": "a note", "link": "a link", "task": "a task", "log": "a log"},
        confidence_threshold=0.70,
    )


# ── helpers ──────────────────────────────────────────────────────────────────
def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("   ") == "note"


def test_write_note(tmp_path):
    cfg = make_cfg(tmp_path)
    path = write_note(cfg.inbox("work"), "buy milk", intent="note",
                      domain="work", confidence=0.9)
    assert path.exists()
    body = path.read_text()
    assert "buy milk" in body
    assert "type: note" in body and "domain: work" in body


def test_append_line_plain(tmp_path):
    f = tmp_path / "tasks.md"
    append_line(f, "- [ ] one")
    append_line(f, "- [ ] two")
    assert f.read_text().count("- [ ]") == 2


def test_append_line_date_heading(tmp_path):
    f = tmp_path / "log.md"
    append_line(f, "- 10:00 first", date_heading=True)
    append_line(f, "- 10:05 second", date_heading=True)
    today = datetime.date.today().isoformat()
    text = f.read_text()
    assert text.count(f"## {today}") == 1
    assert "- 10:00 first" in text and "- 10:05 second" in text


# ── handlers (UC-1..UC-4) ────────────────────────────────────────────────────
def test_handle_note(tmp_path):
    cfg = make_cfg(tmp_path)
    res = dispatch("a plain thought", Classification("work", "note", 0.9), cfg)
    assert res.success and res.path.exists()
    assert res.path.parent == cfg.inbox("work")


def test_handle_link_preserves_url(tmp_path):
    cfg = make_cfg(tmp_path)
    res = dispatch("read this https://example.com/article",
                   Classification("reading", "link", 0.9), cfg)
    assert res.success
    assert "https://example.com/article" in res.path.read_text()


def test_handle_task(tmp_path):
    cfg = make_cfg(tmp_path)
    res = dispatch("file the taxes", Classification("work", "task", 0.9), cfg)
    assert res.path.name == "tasks.md"
    assert "- [ ] file the taxes" in res.path.read_text()


def test_handle_log(tmp_path):
    cfg = make_cfg(tmp_path)
    res = dispatch("felt strong today", Classification("personal", "log", 0.9), cfg)
    assert res.path.name == "log.md"
    assert "felt strong today" in res.path.read_text()


# ── classifier helpers ───────────────────────────────────────────────────────
def test_classify_heuristic(tmp_path):
    cfg = make_cfg(tmp_path)
    assert classify_heuristic("check https://x.com", cfg).intent == "link"
    assert classify_heuristic("todo: call the bank", cfg).intent == "task"
    assert classify_heuristic("just a normal note", cfg).intent == "note"


def test_parse_json():
    assert _parse_json('{"a": 1}') == {"a": 1}
    assert _parse_json('here you go: {"a": 2} done')["a"] == 2


# ── router confidence gate (UC-5) ────────────────────────────────────────────
def test_router_low_confidence_asks(tmp_path):
    cfg = make_cfg(tmp_path)
    res = route("ambiguous", cfg, classification=Classification("work", "note", 0.40))
    assert res.requires_confirmation and res.written_path is None


def test_router_high_confidence_files(tmp_path):
    cfg = make_cfg(tmp_path)
    res = route("clear note", cfg, classification=Classification("work", "note", 0.95))
    assert not res.requires_confirmation and res.written_path


def test_router_confirm_files(tmp_path):
    cfg = make_cfg(tmp_path)
    low = Classification("work", "note", 0.40)
    res = route("ambiguous", cfg, confirmed=True, classification=low)
    assert not res.requires_confirmation and res.written_path


# ── digest (UC-7) ────────────────────────────────────────────────────────────
def test_digest(tmp_path):
    cfg = make_cfg(tmp_path)
    write_note(cfg.inbox("work"), "x", intent="note", domain="work", confidence=0.9)
    body, per_domain = digest.build_digest(cfg)
    assert "Weekly digest" in body
    assert per_domain.get("work")


# ── inline test runner (pytest-free) ─────────────────────────────────────────
def _run() -> int:
    tests = sorted(
        (name, fn) for name, fn in globals().items()
        if name.startswith("test_") and inspect.isfunction(fn)
    )
    passed = failed = 0
    for name, fn in tests:
        tmp = Path(tempfile.mkdtemp(prefix="vw_capture_test_"))
        try:
            if inspect.signature(fn).parameters:
                fn(tmp)
            else:
                fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:
            print(f"  FAIL  {name}")
            traceback.print_exc()
            failed += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed ({len(tests)} total)")
    return 1 if failed else 0


if __name__ == "__main__":
    print(f"Vaultwright capture tests — {datetime.date.today().isoformat()}")
    sys.exit(_run())
