"""whether the mailbox on the public path is alive.

`/webhook` is the one door open to the internet, and it is the one part of this
office with no natural way to notice it has stopped. A hook that GitHub
unregistered, a Funnel that fell over, a secret that got rotated on one side
only: all three look identical from in here, which is a quiet room. And a quiet
room is exactly what a working Sunday looks like too.

So this fixture answers the only question that separates them: **when did
anything last arrive.** Silence with a delivery four minutes ago is a quiet
morning. Silence with the last delivery three days ago, on a repo that has had
commits since, is a broken hook.

It reads the same three files the receiver writes, and nothing else. No network,
no GitHub, no subprocess: the mailbox's own record is the measurement.

  webhook-seen.json     how many deliveries have ever been handled
  webhook-events.jsonl  what arrived, last 5000
  webhook-runs.jsonl    what the trigger ran, and what it exited with

`queued` is the one fact with no file behind it. A debounce window is by
definition the thing that has not happened yet, so it is read off the Trigger
this process is running, and is an empty list in any process that is not serving.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
import time

from sources import _card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import webhook as wh  # noqa: E402  (needs the path above)

KEY = "webhook"
TITLE = "Webhooks"

# A run of bad signatures in a row is the shape of a rotated secret or somebody
# knocking. One is noise; three in a row with nothing valid between them is a
# thing to go and look at.
BAD_SIG_RUN = 3


def _state_dir() -> pathlib.Path:
    """Read at call time, never at import: a test points the state somewhere
    disposable by assigning `webhook.STATE`, and an import-time copy would keep
    reading the real one."""
    return pathlib.Path(wh.STATE)


def _rows(path: pathlib.Path, n: int = 200) -> list:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _epoch(stamp) -> float | None:
    s = str(stamp or "").strip()
    if not s:
        return None
    try:
        dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


def read(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    # Asked of the module that does the verifying, never of the environment:
    # a card that reads the config separately from the door is a card that can
    # say "configured" about a door answering 503.
    configured = bool(wh.SECRET)
    state_dir = _state_dir()

    events = _rows(state_dir / wh.EVENTS_FILE)
    runs = _rows(state_dir / wh.RUNS_FILE)

    day = now - 24 * 3600
    events_today = sum(1 for e in events if (_epoch(e.get("at")) or 0) >= day)
    runs_today = sum(1 for r in runs if (_epoch(r.get("at")) or 0) >= day)

    last = events[-1] if events else {}
    last_at = last.get("at") or ""
    last_age = None if _epoch(last_at) is None else max(0.0, now - _epoch(last_at))

    last_run = runs[-1] if runs else {}
    last_run_at = last_run.get("at") or ""
    last_run_age = None if _epoch(last_run_at) is None else max(0.0, now - _epoch(last_run_at))

    # Not "configured" as a synonym for "working": a secret set and nothing ever
    # delivered is its own state, and it is the one a person has to act on
    # (register the hook), so it must not read the same as a quiet morning.
    if not configured:
        state = "unconfigured"
    elif not events:
        state = "silent"
    else:
        state = "ok"

    trig = getattr(wh, "RUNNING_TRIGGER", None)
    try:
        queued = trig.queued() if trig is not None else []
    except Exception:  # noqa: BLE001 - a queue reading wrong is not a dead section
        queued = []

    bad_run = _bad_signature_run(state_dir)

    return {
        "state": state,
        "detail": "" if configured else "no OFFICE_WEBHOOK_SECRET, so /webhook answers 503",
        "configured": configured,
        "events_today": events_today,
        "events_total": len(events),
        "last_at": last_at,
        "last_age_s": last_age,
        "last_event": (f"{last.get('event', '')}.{last.get('action', '')}".strip(".")
                       if last else ""),
        "last_repo": last.get("repo") or "",
        "runs_today": runs_today,
        "last_run_at": last_run_at,
        "last_run_age_s": last_run_age,
        "last_run_rc": last_run.get("rc"),
        "last_run_repo": last_run.get("repo") or "",
        "queued": list(queued),
        "bad_signature_run": bad_run,
    }


def _bad_signature_run(state_dir: pathlib.Path) -> int:
    """How many bad signatures have arrived with nothing valid since.

    The receiver does not log a refusal to the events file on purpose: an
    unsigned poster must not be able to fill a disk by posting. So this is read
    from the counter the door keeps in memory, and is 0 in any process that is
    not serving.
    """
    n = getattr(wh, "BAD_SIGNATURES", 0)
    try:
        return max(0, int(n))
    except (TypeError, ValueError):
        return 0


def card(data: dict, now: float | None = None) -> dict:
    """One line: is anything arriving, and how long ago was the last one."""
    state = data.get("state")
    configured = bool(data.get("configured"))

    if not configured:
        return _card.build(TITLE, "not configured", 0, "", [
            _card.fact("state", "no secret set", "dim"),
            _card.fact("detail", str(data.get("detail") or ""), "dim"),
        ])

    today = int(data.get("events_today") or 0)
    age = data.get("last_age_s")
    when = f"last {_card.human(age)} ago" if age is not None else "nothing has ever arrived"
    headline = f"{today} {_card.plural(today, 'event')} today, {when}"

    bad = int(data.get("bad_signature_run") or 0)
    # A run of refusals is the one thing here that genuinely wants a person: it
    # means the secret on one side is not the secret on the other, and every
    # delivery is being dropped while the room looks merely quiet.
    needs = 1 if bad > BAD_SIG_RUN else 0
    if state == "silent":
        needs = max(needs, 1)
        headline = "configured, but nothing has ever arrived"

    rc = data.get("last_run_rc")
    run_age = data.get("last_run_age_s")
    if run_age is None:
        run_row = _card.fact("last run", "none yet", "dim")
    elif rc is None:
        run_row = _card.fact("last run", f"{_card.human(run_age)} ago, no checkout to run", "warn")
    else:
        run_row = _card.fact("last run", f"{_card.human(run_age)} ago, exit {rc}",
                             "ok" if rc == 0 else "bad")

    queued = list(data.get("queued") or [])
    facts = [
        _card.fact("events today", _card.count(today), "dim" if today else "warn"),
        _card.fact("last event",
                   f"{data.get('last_event') or 'none'} {data.get('last_repo') or ''}".strip()
                   if age is not None else "never", "dim"),
        _card.fact("runs today", _card.count(int(data.get("runs_today") or 0)), "dim"),
        run_row,
        _card.fact("queued", ", ".join(queued) if queued else "nothing waiting",
                   "warn" if queued else "dim"),
    ]
    if bad:
        facts.append(_card.fact("refused signatures", f"{bad} in a row",
                                "bad" if bad > BAD_SIG_RUN else "warn"))
    return _card.build(TITLE, headline, needs, _card.zulu(data.get("last_at")), facts)
