"""what intake has caught and not yet filed.

Intake is the mouth of the pipeline: Granola notes, mobile captures and email go
in, GitHub issues come out. It is also the one stage with no representation in
the room, and the one where things go missing quietly, because "captured" and
"filed" are two different states and only the second one is visible anywhere
else.

WHICH INTERFACE THIS USES, AND WHY
----------------------------------
`intake.py --summary --json`, run as a subprocess with a hard timeout.

It advertises itself as "cache-only status: no model calls, no network, no
writes", and that was measured before this file was written rather than taken on
trust: 0.08s wall clock on the real vault, cold. That is well inside what a
snapshot push can afford, so the advertised interface is the one used. A plain
run of intake (no --summary) reaches the Granola API and can take minutes, so it
is never invoked here, and neither is --file, which creates real GitHub issues.

Two small files are read directly alongside it, because --summary drops facts
this room needs:

  cache/last-run.json   `rate_limited` and `no_transcript`. Intake's own
                        docstring insists rate_limited is NOT an absence, and
                        summarise() does not carry it through. A mailroom that
                        cannot tell "nothing came" from "Granola said 429" is
                        the false-green this project exists to kill.
  state.json            the exclusion list itself. --summary reports a COUNT
                        from the last run, which is 0 whenever the last run
                        happened to exclude nothing, even while three meetings
                        stand permanently excluded. The names are the data;
                        deliberately skipped must never look like dropped.

Both are cheap local reads and both fail soft: a missing or torn file costs the
detail, never the snapshot.

STALENESS DOMINATES
-------------------
`stale` and `stale_reason` come straight from intake and are the headline. Every
count below them is subordinate: if the summary does not cover what is on disk,
the numbers describe a moment that has passed, and the room must render the
staleness where the count would have gone.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

KEY = "mail"

SERVICE = "_meta/services/intake"

# Measured at 0.08s. Ten seconds is not a budget, it is a tripwire: if intake
# ever starts reaching the network from --summary, this fires instead of hanging
# the push, and a timeout reports as its OWN state rather than as an empty room.
TIMEOUT = 10

# The three feeds, in the order they hang on the wall. `on_disk` can count the
# first two; email lives on a server and can never be counted from disk, so it
# is unknown rather than zero, and the room draws those differently.
FEEDS = [
    ("granola", "granola", True),
    ("capture", "mobile capture", True),
    ("email", "email", False),
]


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _json_file(path: pathlib.Path) -> dict:
    """A local JSON file, or {}. Never raises: these are extra detail, not the
    snapshot, and one torn cache file must not cost the whole mailroom."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _summary(script: pathlib.Path) -> dict:
    """Run intake's own status interface. Returns {"error": ...} rather than
    raising, so every failure mode gets a name in the room."""
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--summary", "--json"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=str(script.parent),
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "detail": f"intake --summary did not answer in {TIMEOUT}s"}
    except OSError as exc:
        return {"error": "error", "detail": f"could not run intake: {exc}"[:200]}

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"error": "error",
                "detail": f"intake --summary exited {proc.returncode}: "
                          f"{detail[-1] if detail else 'no output'}"[:300]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "unreadable",
                "detail": "intake --summary printed something that was not JSON"}


def _excluded(state: dict) -> list:
    """The operator's exclusion list, as names rather than a number.

    Stored as "<id>  <title>" strings. Split on the first run of whitespace: the
    id is one token, everything after it is what a person would recognise.
    """
    out = []
    for raw in (state.get("exclude") or {}).get("meetings", []):
        text = str(raw).strip()
        if not text:
            continue
        bits = text.split(None, 1)
        out.append({"id": bits[0], "title": bits[1].strip() if len(bits) > 1 else ""})
    for pattern in (state.get("exclude") or {}).get("title_patterns", []):
        if str(pattern).strip():
            out.append({"id": "", "title": f"any title matching /{pattern}/"})
    return out


def _pigeonholes(summary: dict, snap: dict, state: dict) -> list:
    """One per feed: what is sitting in it, and why it has not moved.

    `waiting` is what exists on disk minus what the last run covered. Intake
    counts a deliberately excluded item as covered, on purpose, so an exclusion
    does not make the room look permanently behind.
    """
    on_disk = summary.get("on_disk") or {}
    covered = snap.get("covered") or {}
    ran = set(summary.get("sources") or [])

    filed_by_source = {}
    for record in (state.get("filed") or {}).values():
        src = str((record or {}).get("source") or "")
        filed_by_source[src] = filed_by_source.get(src, 0) + 1

    holes = []
    for key, label, countable in FEEDS:
        seen = int(covered.get(key, 0) or 0)
        hole = {
            "key": key,
            "label": label,
            "in_last_run": key in ran,
            "on_disk": int(on_disk[key]) if countable and key in on_disk else None,
            "covered": seen,
            "filed": filed_by_source.get(key, 0),
            "waiting": None,
            "why": "",
        }
        if hole["on_disk"] is None:
            hole["why"] = ("email is a live mailbox, so nothing about it can be counted "
                           "from disk. Unknown, not zero.")
        else:
            hole["waiting"] = max(hole["on_disk"] - seen, 0)
            if not hole["in_last_run"]:
                hole["why"] = "this feed was not in the last run, so none of it has been looked at"
            elif hole["waiting"]:
                hole["why"] = f"{hole['waiting']} of {hole['on_disk']} on disk are not in the last run's decisions"
            else:
                hole["why"] = "everything on disk was covered by the last run"
        holes.append(hole)
    return holes


def read() -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured",
                "detail": "OFFICE_RUNTIME_ROOT is not set, so there is no vault to read intake from"}
    service = root / SERVICE
    script = service / "intake.py"
    if not script.exists():
        return {"state": "missing", "detail": f"no intake at {script}"}

    summary = _summary(script)
    if summary.get("error"):
        # A timeout is its own state and says so. It is emphatically not an
        # empty mailroom: it means nobody could answer the question.
        return {"state": summary["error"], "detail": summary.get("detail", "")}

    snap = _json_file(service / "cache" / "last-run.json")
    state = _json_file(pathlib.Path(os.environ.get("INTAKE_STATE") or (service / "state.json")))

    stale = bool(summary.get("stale"))
    return {
        "state": "ok",
        # The headline. Everything below is subordinate to these two.
        "stale": stale,
        "stale_reason": str(summary.get("stale_reason") or "")[:400] if stale else "",
        "last_run": summary.get("last_run"),
        "watermark": summary.get("watermark"),
        # The last run's own honesty about itself: a --file run and a dry run
        # decide the same things and do very different amounts about it.
        "dry_run": snap.get("dry_run"),
        "pigeonholes": _pigeonholes(summary, snap, state),
        "counts": {
            "items": int(summary.get("items") or 0),
            "would_file": int(summary.get("would_file") or 0),
            "filed": int(summary.get("filed") or 0),
            "cached": int(summary.get("cached") or 0),
        },
        # Three different things, kept apart on purpose. Declined is a decision,
        # blocked is a gate, rate_limited is a failure that LOOKS like an
        # absence. Adding them up would erase the only distinction that matters.
        "held": {
            "declined": int(summary.get("declined") or 0),
            "blocked": {str(k): int(v) for k, v in (summary.get("blocked") or {}).items()},
            "rate_limited": int(snap.get("rate_limited") or 0),
            "no_transcript": int(snap.get("no_transcript") or 0),
        },
        # Deliberate, named, and never folded into the numbers above.
        "excluded": _excluded(state),
    }
