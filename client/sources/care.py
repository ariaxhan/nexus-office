"""The care desk: is hello@ being read, and what did the last sweep do.

The care desk itself is the tbs-care desk in the roster: every hello@ thread
becomes one issue there with a drafted reply under it, and a person reviews
the draft on the issue. That desk already shows the queue. What it cannot show
is the thing this card exists for: whether the sweep that FEEDS it is alive.
A mailbox whose reader died looks exactly like a quiet mailbox, forever, in
green, and that is the false-green this project exists to kill.

WHICH FILE THIS READS, AND WHY
------------------------------
`_meta/services/intake/cache/care-last-run.json`, written by every care run of
intake (`intake.py --source care`), dry or filing. Intake keeps this snapshot
apart from the shared one because the care desk runs on its own cadence, and a
care run overwriting the mailroom's snapshot would make the Mail card call
granola stale every quarter hour. One local JSON read; no subprocess, no
network, no model. Fails soft: a torn file is a state with a name, never a
blank fixture.

STATES, IN ORDER
----------------
  never     no snapshot: the desk has not run once. Not "quiet".
  dark      the last run could not reach the mailbox. Nothing was read.
  stale     the last run is older than STALE_AFTER_S. The counts are history.
  error     the last run hit errors filing or drafting.
  dry       the last run decided everything and filed nothing (a rehearsal).
  ok        read, drafted, filed.

`needs` is 1 for never, dark, stale and error: those are the desk being blind,
which a person has to fix. It is 0 for dry and ok, because the threads that
want a person are counted on the tbs-care desk, not twice.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time

from sources import _card

KEY = "care"
TITLE = "Care"

SNAPSHOT = "_meta/services/intake/cache/care-last-run.json"
MAILBOX = "hello@thinkingbrainschool.com"

# The job runs every 15 minutes. Three misses is a stopped job, not a slow one.
STALE_AFTER_S = 45 * 60

_UNANSWERED_RE = re.compile(r"(\d+) unanswered")
_ANSWERED_RE = re.compile(r"(\d+) answered")
_NOISE_RE = re.compile(r"(\d+) noise")
_READ_RE = re.compile(r"(\d+) messages read")

TROUBLE = {
    "unconfigured": ("No vault to read the care desk from", 0),
    "never": ("The care desk has never run", 1),
    "dark": ("hello@ could not be read", 1),
    "unreadable": ("The care snapshot is torn", 1),
}


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _num(pattern: re.Pattern, notes: list) -> int | None:
    for note in notes:
        m = pattern.search(str(note))
        if m:
            return int(m.group(1))
    return None


def read(now: float | None = None) -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured",
                "detail": "OFFICE_RUNTIME_ROOT is not set, so there is no snapshot to read"}
    path = root / SNAPSHOT
    if not path.exists():
        return {"state": "never", "detail": f"no {SNAPSHOT}; run intake.py --source care once"}
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"state": "unreadable", "detail": str(exc)[:160]}

    notes = [str(n) for n in (snap.get("notes") or [])]
    dark = next((n for n in notes if "unreachable" in n), "")
    at = _card.zulu(snap.get("at"))
    age = None
    if at:
        import datetime
        then = datetime.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc).timestamp()
        age = (time.time() if now is None else now) - then

    out = {
        "state": "ok",
        "mailbox": MAILBOX,
        "as_of": at,
        "age_s": None if age is None else max(0, int(age)),
        "stale": age is None or age > STALE_AFTER_S,
        "dry_run": bool(snap.get("dry_run")),
        "read": _num(_READ_RE, notes),
        "unanswered": _num(_UNANSWERED_RE, notes),
        "answered": _num(_ANSWERED_RE, notes),
        "noise": _num(_NOISE_RE, notes),
        "filed": int(snap.get("filed") or 0),
        "would_file": int(snap.get("would_file") or 0),
        "followup": int(snap.get("followup") or 0),
        "duplicates": int(snap.get("skipped_duplicate") or 0),
        "errors": int(snap.get("error") or 0),
        "blocked": dict(snap.get("blocked") or {}),
        "threads": list(snap.get("threads") or [])[:20],
    }
    if dark:
        out["state"] = "dark"
        out["detail"] = dark.split(":", 2)[-1].strip()[:160]
    return out


def card(data: dict) -> dict:
    state = data.get("state")
    if state in TROUBLE:
        return _card.trouble(TITLE, state, data.get("detail"), TROUBLE)

    as_of = data.get("as_of") or ""
    when = _card.ago(as_of) or "unknown"
    filed = data["filed"] + data["followup"]
    unanswered = data.get("unanswered")
    blocked = sum(int(v or 0) for v in (data.get("blocked") or {}).values())

    facts = []
    if unanswered is not None:
        facts.append(_card.fact("threads waiting", str(unanswered), "warn" if unanswered else "ok"))
    if data.get("dry_run"):
        facts.append(_card.fact("would file", str(data["would_file"]), "dim"))
    else:
        facts.append(_card.fact("filed last sweep",
                                f"{data['filed']} new, {data['followup']} follow-up", "ok" if filed else "dim"))
    if data.get("duplicates"):
        facts.append(_card.fact("already on an issue", str(data["duplicates"]), "dim"))
    if blocked:
        facts.append(_card.fact("held back", ", ".join(f"{k} {v}" for k, v in data["blocked"].items()), "bad"))
    if data.get("errors"):
        facts.append(_card.fact("errors", str(data["errors"]), "bad"))
    if data.get("read") is not None:
        facts.append(_card.fact("inbox read", f"{data['read']} messages, {data.get('noise') or 0} robots", "dim"))
    facts.append(_card.fact("last sweep", when, "bad" if data.get("stale") else "dim"))

    rows = []
    for t in data.get("threads") or []:
        outcome = str(t.get("outcome") or "")
        tone = {"filed": "ok", "followup": "ok", "would_file": "dim", "would_followup": "dim",
                "skip_duplicate": "dim", "error": "bad", "not_opted_in": "bad",
                "blocked_on_identity": "bad"}.get(outcome, "")
        rows.append(_card.row(f"care-{t.get('title', '')[:40]}", t.get("title", ""),
                              t.get("date", ""), t.get("detail", ""), outcome.replace("_", " "), tone))

    if data.get("stale"):
        headline = f"The care sweep has not run since {when}; the mailbox is unread"
        return _card.build(TITLE, headline, 1, as_of, facts, rows)
    if data.get("errors"):
        headline = f"{data['errors']} thread(s) could not be drafted or filed"
        return _card.build(TITLE, headline, 1, as_of, facts, rows)
    if blocked:
        headline = f"{blocked} thread(s) held back; nothing reached tbs-care"
        return _card.build(TITLE, headline, 1, as_of, facts, rows)
    if data.get("dry_run"):
        headline = f"Rehearsal only: {data['would_file']} thread(s) would be filed, none were"
        return _card.build(TITLE, headline, 0, as_of, facts, rows)
    if unanswered:
        headline = f"{unanswered} {_card.plural(unanswered, 'thread')} waiting; {filed} reached tbs-care {when}"
    else:
        headline = f"hello@ is clear; last read {when}"
    return _card.build(TITLE, headline, 0, as_of, facts, rows)
