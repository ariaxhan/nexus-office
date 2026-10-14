"""The Wall of Failure: one plain line per failure, up until fixed AND verified.

Aria's wall (2026-10-13), with her rules, which this file exists to keep:

  * every failure gets a plain-language line the day it happens
  * a line comes down ONLY when the failure is fixed AND the fix is verified
    — never when it is merely explained
  * a repeat is marked REPEAT

The data is an append-only JSONL file, one record per state change, the last
record for an id carrying the line's current state:

    {"id": "wf-001", "date": "2026-10-13",
     "line": "The short plain statement of what happened.",
     "detail": "The slightly longer plain version, or nothing.",
     "repeat": false, "status": "open", "source": "where the record lives"}

Statuses, kept apart because they are not the same thing:

  open      up. Not fixed.
  watched   up. A rule or mechanism exists; not yet proven. Still up.
  fixed     DOWN — off the wall. Only reachable with `fixed_on` (a date) and
            `verification` (what was checked, in words) on the record.

The enforcement lives HERE, in the fold, not in whoever writes the file: a
record that claims `fixed` without a verification note does not take the
line down. It stays up, and the attempt is named in the section's problems,
because a retirement nobody verified is exactly the failure this wall
exists to catch. `scripts/wall.py` is the low-friction way in (it refuses
to write an unverified retirement at all); this fold is the backstop for
everything that writes the file any other way.

The file lives outside the release tree, in the vault, so a release never
carries it and a lane can add a line the day a failure happens without a
deploy: `<runtime root>/_meta/wall-of-failure.jsonl`, overridable with
OFFICE_FAILURE_WALL (the tests use that). Every return carries a `state`:
no root, no file, and an unreadable file must never render the same as a
wall with nothing up.
"""

from __future__ import annotations

import json
import os
import pathlib

from sources import _card

KEY = "failure_wall"
TITLE = "Wall of Failure"

WALL_REL = pathlib.Path("_meta") / "wall-of-failure.jsonl"
STATUSES = ("open", "watched", "fixed")

# Worst first: a repeat leads, then what is simply open, then what is
# watched. Inside a group, the oldest line leads — it has been up longest.
ORDER = {"open": 1, "watched": 2}
TONE = {"open": "bad", "watched": "warn"}


def wall_path() -> pathlib.Path | None:
    """Where the wall lives, or None when nothing says where that is."""
    v = os.environ.get("OFFICE_FAILURE_WALL", "").strip()
    if v:
        return pathlib.Path(v).expanduser()
    root = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    if root:
        return pathlib.Path(root).expanduser() / WALL_REL
    return None


def _clean(rec) -> dict | None:
    """One record, normalised, or None when it cannot carry a line at all.

    A junk record is skipped, never fatal: one bad line in the file must
    not cost the whole wall. A `fixed` claim that cannot prove itself is
    NOT skipped — it is coerced back up and reported, by `fold`.
    """
    if not isinstance(rec, dict):
        return None
    rid = str(rec.get("id") or "").strip()
    line = str(rec.get("line") or "").strip()
    date = str(rec.get("date") or "").strip()
    if not rid or not line or not date:
        return None
    status = str(rec.get("status") or "open").strip().lower()
    if status not in STATUSES:
        status = "open"
    return {
        "id": rid,
        "date": date,
        "line": line,
        "detail": str(rec.get("detail") or "").strip(),
        "repeat": bool(rec.get("repeat")),
        "status": status,
        "source": str(rec.get("source") or "").strip(),
        "note": str(rec.get("note") or "").strip(),
        "fixed_on": str(rec.get("fixed_on") or "").strip(),
        "verification": str(rec.get("verification") or "").strip(),
    }


def fold(records) -> tuple[list[dict], list[str]]:
    """Records in file order -> (current lines, problems).

    The last clean record per id wins. A winning record that claims
    `fixed` without BOTH a `fixed_on` date and a `verification` note is
    coerced to `open` and named in problems: the line stays up.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    problems: list[str] = []
    for rec in records:
        line = _clean(rec)
        if line is None:
            continue
        if line["id"] not in by_id:
            order.append(line["id"])
        by_id[line["id"]] = line
    lines = []
    for rid in order:
        line = by_id[rid]
        if line["status"] == "fixed" and not (line["fixed_on"] and line["verification"]):
            problems.append(
                f"{rid} claims fixed without a verification note — still up")
            line = {**line, "status": "open"}
        lines.append(line)
    return lines, problems


def load(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    records = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue  # a torn line is not a line; the fold skips the rest
    return fold(records)


def read() -> dict:
    path = wall_path()
    if path is None:
        return {"state": "unconfigured",
                "detail": "no OFFICE_FAILURE_WALL and no OFFICE_RUNTIME_ROOT, "
                          "so no wall file to read"}
    if not path.exists():
        return {"state": "missing", "detail": str(path)}
    try:
        lines, problems = load(path)
    except OSError as exc:
        return {"state": "unreadable",
                "detail": f"{type(exc).__name__}: {exc}"[:200]}
    up = [ln for ln in lines if ln["status"] != "fixed"]
    down = [ln for ln in lines if ln["status"] == "fixed"]
    return {
        "state": "ok",
        "path": str(path),
        "lines": lines,
        "up": up,
        "down": down,
        "problems": problems,
        "counts": {
            "up": len(up),
            "open": sum(1 for ln in up if ln["status"] == "open"),
            "watched": sum(1 for ln in up if ln["status"] == "watched"),
            "repeats": sum(1 for ln in up if ln["repeat"]),
            "down": len(down),
        },
        # The card contract wants a timestamp or nothing; the freshness of
        # this data is the newest day it carries a record for.
        "as_of": _card.zulu(f"{max((ln['date'] for ln in lines), default='')}T00:00:00Z"),
    }


TROUBLE = {
    "unconfigured": ("not configured", 0),
    "missing": ("the wall file is not there — failures are going unrecorded", 1),
    "unreadable": ("could not read the wall file", 1),
}


def card(data: dict) -> dict:
    if data.get("state") != "ok":
        return _card.trouble(TITLE, data.get("state"), data.get("detail"), TROUBLE)

    counts = data["counts"]
    up = sorted(
        data["up"],
        key=lambda ln: (0 if ln["repeat"] else 1,
                        ORDER.get(ln["status"], 3), ln["date"], ln["id"]),
    )
    facts = [
        _card.fact("Still up", str(counts["up"]), "bad" if counts["up"] else "ok"),
        _card.fact("Open", str(counts["open"]), "bad" if counts["open"] else "dim"),
        _card.fact("Watched", str(counts["watched"]),
                   "warn" if counts["watched"] else "dim"),
        _card.fact("Repeats", str(counts["repeats"]),
                   "bad" if counts["repeats"] else "dim"),
        _card.fact("Down (fixed + verified)", str(counts["down"]), "dim"),
    ]
    if data.get("problems"):
        facts.append(_card.fact("Unproven retirements", str(len(data["problems"])),
                                "bad"))

    if not up:
        headline = "nothing up — every line is fixed and verified"
    else:
        worst = up[0]
        headline = (f"{counts['up']} {_card.plural(counts['up'], 'failure')} "
                    f"still up: {worst['line']}")

    rows = []
    for ln in up:
        badge = "REPEAT" if ln["repeat"] else ln["status"].upper()
        subtitle = ln["date"] + (f" · {ln['source']}" if ln["source"] else "")
        detail = ln["detail"] or ln["note"]
        rows.append(_card.row(ln["id"], ln["line"], subtitle, detail, badge,
                              TONE.get(ln["status"], "warn"), "Still up"))
    return _card.build(TITLE, headline, counts["up"], data.get("as_of", ""),
                       facts, rows)
