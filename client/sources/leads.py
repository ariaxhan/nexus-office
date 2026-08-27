"""the gig desk: consulting work that might exist, and what it is waiting on.

Every other source in this room reports on a machine. This one reports on a
pipeline of people, which fails differently: a lead does not break, it goes
quiet, and quiet looks exactly like nothing happening. So the headline here is
never a count of leads. It is the count of leads WAITING ON ARIA, because that
is the only number that changes what she does in the next ten minutes.

THREE FILES, KEPT APART ON PURPOSE
----------------------------------

  the ledger    <root>/_meta/leads/leads.jsonl
                One JSON object per line, append-only, written by the scout and
                the qualifier. This is the measured half: it says what was
                actually found and what stage each thing reached.

  the map       <root>/_meta/leads/sources.json
                The scout's own claim about WHERE Aria's work comes from,
                rediscovered rather than configured. This is the stated half. It
                carries a confidence and it is never allowed to become a number
                on the board without one.

  the voice     <root>/_meta/leads/voice.md
                What Aria sounds like, established from her own writing. A pitch
                bot drafting without this is a bot writing in nobody's voice, so
                its absence is reported as loudly as an empty ledger.

The map is a CLAIM and the ledger is a RECEIPT, and this file refuses to fold
them together. `claimed_confidence` comes off the map untouched; `found` and
`converted` are counted from the ledger. When they disagree, the room shows both
and says which is which. Flattening a claim and a measurement into one number is
the lie-with-a-decimal-point this project keeps saying it will not tell.

STAGES
------
new → qualified → drafted → sent → replied → dead

`dead` is a decision and is never a failure state; a lead that went nowhere for
a stated reason is finished work. The order the board reads them in is NOT the
order above: it is the order of who is blocked, exactly as StateRules does it.

  drafted   a message is written and only Aria can send it
  replied   a human answered and is now waiting on her
  qualified worth pitching, nothing written yet
  new       found, not yet judged
  sent      out, waiting on them, nothing for her to do
  dead      closed, with a reason

STATES
------
unconfigured  no OFFICE_RUNTIME_ROOT
missing-root  a root that is not there
missing       a root with no gig desk in it yet
unreadable    the ledger would not parse at all
ok            we could tell. Torn lines are counted and reported, never dropped
              silently, for the same reason cost.py never drops an undated row.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

KEY = "leads"

DIR = "_meta/leads"
LEDGER = DIR + "/leads.jsonl"
MAP = DIR + "/sources.json"
VOICE = DIR + "/voice.md"

# The order the board reads stages in: who is blocked, not what happened first.
# Anything not in here is an unknown stage and is reported under its own name
# rather than coerced into one of these.
BLOCKING = ("drafted", "replied")
STAGE_ORDER = ("drafted", "replied", "qualified", "new", "sent", "dead")

# A lead nobody has touched in this long is quiet, which is the failure mode
# this desk exists to make visible. Not dead, not fine: quiet.
QUIET_DAYS = 10

CONFIDENCES = ("low", "medium", "high")


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _parse_time(value) -> datetime.datetime | None:
    """An ISO8601 timestamp, or None. Never raises: an undated row still has a
    real lead in it, and dropping it would hide work rather than report it."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone(datetime.timezone.utc)


def _read_rows(path: pathlib.Path) -> tuple[list, int]:
    """(rows, torn). Torn lines are counted, never silently dropped."""
    rows, torn = [], 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                torn += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                torn += 1
    return rows, torn


def _lead(row: dict, now: datetime.datetime) -> dict:
    """One ledger row as the board reads it. Unknown fields keep their own name."""
    stage = str(row.get("stage") or "new").strip().lower() or "new"
    touched = _parse_time(row.get("last_touch")) or _parse_time(row.get("found_at"))
    quiet_days = (now - touched).days if touched else None

    rate = row.get("rate")
    if not isinstance(rate, dict):
        rate = {}
    signal = str(rate.get("signal") or "").strip()
    confidence = str(rate.get("confidence") or "").strip().lower()

    return {
        "id": str(row.get("id") or "")[:120],
        "who": str(row.get("who") or "")[:200],
        "org": str(row.get("org") or "")[:200],
        # What they said they need, in their words where possible. Never
        # summarised into ambiguity: this is what a pitch has to answer.
        "need": str(row.get("need") or "")[:1000],
        "where": str(row.get("where") or "")[:80],
        "link": str(row.get("link") or "")[:500],
        "stage": stage,
        "known_stage": stage in STAGE_ORDER,
        "blocked_on_aria": stage in BLOCKING,
        "found_at": row.get("found_at"),
        "last_touch": row.get("last_touch"),
        "dated": touched is not None,
        "quiet_days": quiet_days,
        # Quiet is not dead and not fine. A sent lead nobody answered is the
        # single thing this desk exists to stop losing.
        "quiet": bool(stage == "sent" and quiet_days is not None and quiet_days >= QUIET_DAYS),
        # A rate is a SIGNAL, never a price. It carries the confidence it was
        # written with, and a signal with no confidence is reported as having
        # none rather than being assumed solid.
        "rate_signal": signal,
        "rate_confidence": confidence if confidence in CONFIDENCES else "",
        # Why it is at this stage. On a dead lead this is the whole value of
        # the row: it stops the scout finding the same thing again next week.
        "why": str(row.get("why") or "")[:600],
        "has_draft": bool(str(row.get("draft") or "").strip()),
    }


def _by_stage(leads: list) -> list:
    """Counts in blocked-first order, with unknown stages named at the end."""
    seen = {}
    for lead in leads:
        seen[lead["stage"]] = seen.get(lead["stage"], 0) + 1
    out = [{"stage": s, "count": seen.pop(s, 0)} for s in STAGE_ORDER]
    for stage in sorted(seen):
        out.append({"stage": stage, "count": seen[stage], "unknown": True})
    return out


def _map(path: pathlib.Path, leads: list) -> dict:
    """Where the work comes from: the scout's claim beside the ledger's count.

    The scout rediscovers this rather than being told it, so the claim can be
    wrong, and the only honest way to show it is next to what actually happened.
    `found` and `converted` are counted here from the ledger; `claimed_*` fields
    are copied off the map untouched and are never arithmetic.
    """
    found, converted = {}, {}
    for lead in leads:
        key = lead["where"] or "unknown"
        found[key] = found.get(key, 0) + 1
        if lead["stage"] == "replied":
            converted[key] = converted.get(key, 0) + 1

    claim, state = {}, "missing"
    learned_at = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError, ValueError):
        state = "unreadable"
    else:
        state = "ok"
        learned_at = data.get("learned_at")
        for row in data.get("sources") or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            if key:
                claim[key] = row

    rows = []
    for key in sorted(set(found) | set(claim)):
        row = claim.get(key) or {}
        confidence = str(row.get("confidence") or "").strip().lower()
        rows.append({
            "key": key,
            "label": str(row.get("label") or key)[:120],
            # measured, from the ledger
            "found": found.get(key, 0),
            "converted": converted.get(key, 0),
            # stated, from the scout
            "claimed": key in claim,
            "claimed_confidence": confidence if confidence in CONFIDENCES else "",
            "why": str(row.get("why") or "")[:400],
        })
    rows.sort(key=lambda r: (-r["converted"], -r["found"], r["key"]))
    return {"state": state, "learned_at": learned_at, "sources": rows}


def _voice(path: pathlib.Path) -> dict:
    """Whether Aria's voice has been established, and how recently.

    Reported as its own state because a pitch drafted with no voice profile is
    not a slightly worse draft, it is a draft in nobody's voice, and that is a
    thing she has to be told before she reads one.
    """
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"state": "missing",
                "detail": "no voice profile yet, so nothing should be drafting in her voice"}
    except OSError as exc:
        return {"state": "error", "detail": str(exc)[:200]}
    if not text.strip():
        return {"state": "empty", "detail": "the voice profile is there but says nothing"}
    return {
        "state": "ok",
        "updated": datetime.datetime.fromtimestamp(
            stat.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "words": len(text.split()),
    }


def read() -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured",
                "detail": "OFFICE_RUNTIME_ROOT is not set, so there is no gig desk to read"}
    if not root.exists():
        return {"state": "missing-root", "detail": str(root)}

    ledger = root / LEDGER
    if not (root / DIR).exists():
        return {"state": "missing",
                "detail": f"no gig desk at {root / DIR}; the scout has never run"}

    torn = 0
    try:
        rows, torn = _read_rows(ledger)
    except FileNotFoundError:
        rows = []
    except OSError as exc:
        return {"state": "unreadable", "detail": str(exc)[:200]}

    now = datetime.datetime.now(datetime.timezone.utc)
    leads = [_lead(row, now) for row in rows]
    # Blocked first, then longest quiet, then newest. The top of this list is
    # the answer to "what do I do now", which is the only question the desk has.
    rank = {stage: i for i, stage in enumerate(STAGE_ORDER)}
    leads.sort(key=lambda l: (rank.get(l["stage"], len(STAGE_ORDER)),
                              -(l["quiet_days"] or 0),
                              l["id"]))

    waiting = [l for l in leads if l["blocked_on_aria"]]
    return {
        "state": "ok",
        # The headline, and deliberately not len(leads). A hundred leads nobody
        # has to touch is a quiet desk; two drafts waiting is a busy one.
        "waiting_on_aria": len(waiting),
        "quiet": len([l for l in leads if l["quiet"]]),
        "total": len(leads),
        # Counted, not dropped. A torn line is a lead we lost, and the desk says
        # so rather than reporting a smaller number with a straight face.
        "torn_lines": torn,
        "undated": len([l for l in leads if not l["dated"]]),
        "by_stage": _by_stage(leads),
        "leads": leads[:40],
        "truncated": max(len(leads) - 40, 0),
        "map": _map(root / MAP, leads),
        "voice": _voice(root / VOICE),
    }
