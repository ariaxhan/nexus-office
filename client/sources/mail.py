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

THE COURIERS
------------
A pigeonhole counts what has ARRIVED. Nothing above can tell you whether
anything is still arriving, and those two look identical from here: a feed whose
courier died reads as a feed with nothing new in it, forever, in green.

So the two things that carry post into this room are read directly, each from
the file it already maintains:

  mobile capture   the iCloud drop folder the iOS Shortcut writes into, its
                   `.processed` sibling, and the watcher's own ingest log.
                   Paths are $HOME-derived and match `mobile-capture-ingest.sh`;
                   OFFICE_CAPTURE_DIR / OFFICE_CAPTURE_LOG override them so a
                   test never reads the real iCloud folder.
  granola sync     `_meta/granola/state.json`: how many notes it has ever
                   fetched, when it last ran, and its last error.

`never fired` is a different state from `idle`, and keeping them apart is the
entire reason this section exists: the capture path sat silent for 31 days
looking exactly like a quiet one. A courier that is not `live` raises `needs`
and takes the headline whenever nothing more urgent is wrong.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

from sources import _card

KEY = "mail"
# The human name of the fixture, fixed. A card whose title moves is a
# card the eye has to find again every time it is drawn.
TITLE = "Mail"

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

# Where the mobile capture watcher works. Both are $HOME-derived, exactly as
# `_meta/services/mobile-capture-ingest.sh` derives them, because they live
# outside the vault and therefore outside OFFICE_RUNTIME_ROOT. The environment
# overrides exist so a test points them at a temp dir instead of reading Aria's
# real iCloud folder, which would make the tests depend on her phone.
CAPTURE_DIR = "Library/Mobile Documents/com~apple~CloudDocs/VaultCapture"
CAPTURE_LOG = "Library/Logs/mobile-capture/ingest.log"

# Bookkeeping that forces iCloud to publish the folder to the phone. Counting it
# as a queued capture would show a permanent backlog of one.
CAPTURE_IGNORE = {".gitkeep", ".DS_Store"}

# The ingest log grows forever and this runs inside a snapshot push, so only the
# tail is read. Five minutes per line, so this is days of history.
LOG_TAIL_BYTES = 16384

# The granola sync writes here, inside the vault.
GRANOLA_STATE = "_meta/granola/state.json"


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


def _capture_path(env: str, default: str) -> pathlib.Path:
    """A $HOME-derived path, or whatever the environment points it at."""
    override = os.environ.get(env, "").strip()
    return pathlib.Path(override).expanduser() if override else pathlib.Path.home() / default


def _last_ingest(path: pathlib.Path) -> str:
    """When the capture watcher last completed a run, from its own log.

    Only the tail is read: the log grows forever and this runs inside a snapshot
    push. Lines are "<iso8601 utc> <text>"; the stamp is the first token.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > LOG_TAIL_BYTES:
                fh.seek(size - LOG_TAIL_BYTES)
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed(tail.splitlines()):
        if " OK " in line or "run complete" in line:
            bits = line.split()
            return bits[0] if bits else ""
    return ""


def _courier(key, label, state, delivered=None, waiting=None,
             last_run="", error="", why="") -> dict:
    """One courier, in one shape, whichever feed it carries.

    `delivered` is how many items it has ever brought in and `waiting` is how
    many are sitting in front of it right now. `waiting` is None when the queue
    cannot be counted at all, which is never the same as zero.
    """
    return {"key": key, "label": label, "state": state,
            "delivered": delivered, "waiting": waiting,
            "last_run": last_run or "", "error": str(error or "")[:200], "why": why}


def read_capture() -> dict:
    """The mobile capture watcher: what is queued, what it has ever moved, when.

    `never fired` is its own state and never folded into `live`. A drop folder
    that has existed for a month and processed nothing means the iOS Shortcut
    was never installed, which is a fix nobody looks for while the room says the
    mailroom is clear.
    """
    drop = _capture_path("OFFICE_CAPTURE_DIR", CAPTURE_DIR)
    label = "capture watcher"
    try:
        entries = list(drop.iterdir())
    except FileNotFoundError:
        return _courier("capture", label, "missing",
                        why=f"there is no drop folder at {drop}, so nothing can arrive")
    except OSError as exc:
        return _courier("capture", label, "unreadable",
                        error=f"{type(exc).__name__}: {exc}",
                        why="the drop folder would not read, so the queue is unknown")

    queued = sum(1 for f in entries
                 if f.is_file() and f.name not in CAPTURE_IGNORE)
    processed = drop / ".processed"
    try:
        ever = sum(1 for f in processed.iterdir() if f.is_file())
    except OSError:
        ever = 0
    last = _last_ingest(_capture_path("OFFICE_CAPTURE_LOG", CAPTURE_LOG))

    if not ever:
        return _courier("capture", label, "never-fired", 0, queued, last,
                        why="nothing has ever arrived; the iOS Shortcut is missing")
    return _courier("capture", label, "live", ever, queued, last,
                    why=f"{queued} in the drop folder, {ever} moved in so far")


def read_granola(root: pathlib.Path) -> dict:
    """The granola sync: how many notes it holds, when it ran, what it said.

    Its last error is carried verbatim. A sync that has been failing for a day
    still reports a perfectly healthy note count, and that count is exactly what
    makes the failure invisible.
    """
    label = "granola sync"
    path = root / GRANOLA_STATE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _courier("granola", label, "missing",
                        why=f"no sync state at {path}, so nothing is fetching meetings")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _courier("granola", label, "unreadable",
                        error=f"{type(exc).__name__}: {exc}",
                        why="the sync state would not read")

    # `synced` is a MAP of meeting id -> metadata in some versions and a count in
    # others. Both mean the same thing and neither is printed raw.
    synced = raw.get("synced")
    delivered = len(synced) if isinstance(synced, (dict, list)) else synced
    try:
        delivered = int(delivered or 0)
    except (TypeError, ValueError):
        delivered = 0

    last = str(raw.get("last_run") or "")
    error = raw.get("last_error")
    if error:
        return _courier("granola", label, "failing", delivered, None, last, error,
                        why="the last sync ended in an error, so the notes are only as new as that")
    if not last:
        return _courier("granola", label, "never-fired", delivered, None, "",
                        why="the sync has never completed a run")
    # Waiting is None on purpose: what a live API is holding cannot be counted
    # from disk, and zero would be a lie the room draws as an empty shelf.
    return _courier("granola", label, "live", delivered, None, last,
                    why=f"{delivered} notes fetched so far")


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
        # What is still ARRIVING. A pigeonhole cannot tell you that, and a dead
        # courier reads as a quiet feed for as long as nobody asks.
        "couriers": [read_capture(), read_granola(root)],
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


TROUBLE = {
    "unconfigured": ("not configured", 0),
    "missing": ("intake is not installed", 1),
    "timeout": ("intake did not answer", 1),
    "unreadable": ("intake printed something that was not JSON", 1),
    "error": ("intake would not run", 1),
}

# Held back for three different reasons, and they are never added up. Declined
# is a decision, blocked is a gate, rate limited is a failure that LOOKS like an
# absence, and no transcript is a meeting nobody wrote down.
HELD = (("declined", "declined"), ("blocked", "blocked"),
        ("rate_limited", "rate limited"), ("no_transcript", "no transcript"))


def card(data: dict) -> dict:
    """What is sitting in the mailroom, and whether the count can be trusted.

    Staleness outranks every number below it. If the last run does not cover
    what is on disk then the counts describe a moment that has passed, so the
    headline says so where the count would have gone.

    A stopped courier comes next, ahead of the waiting count, because a feed
    with nothing waiting on it is either clear or dead and only one of those is
    good news.
    """
    if data.get("state") != "ok":
        return _card.trouble(TITLE, data.get("state"), data.get("detail"), TROUBLE)

    holes = data.get("pigeonholes") or []
    waiting = sum(int(h.get("waiting") or 0) for h in holes)
    stale = bool(data.get("stale"))
    couriers = data.get("couriers") or []
    stopped = [c for c in couriers if c.get("state") != "live"]

    if stale:
        headline = "behind: " + (data.get("stale_reason")
                                 or "the last run does not cover what is on disk")
    elif stopped:
        # Outranks the waiting count, and especially outranks "nothing waiting":
        # a feed nothing is arriving on is empty for the worst possible reason,
        # and that is the exact green this section was built to refuse.
        first = stopped[0]
        headline = f"{first.get('label')} stopped: {first.get('why') or first.get('state')}"
    elif waiting:
        headline = f"{_card.count(waiting)} waiting to be filed"
    else:
        headline = "nothing waiting to be filed"

    facts = []
    # A courier that is not delivering goes above the counts it explains.
    for c in stopped:
        facts.append(_card.fact(c.get("label") or c.get("key") or "courier",
                                c.get("why") or c.get("state"), "bad"))

    for hole in holes:
        n = hole.get("waiting")
        filed = _card.count(hole.get("filed") or 0)
        if n is None:
            # Email lives on a server. Unknown, never zero, and drawn as such.
            value, tone = f"unknown, {filed} filed", "dim"
        else:
            n = int(n)
            value = f"{_card.count(n)} waiting, {filed} filed"
            tone = "warn" if n else "ok"
        facts.append(_card.fact(hole.get("label") or hole.get("key") or "feed", value, tone))

    last_run = data.get("last_run")
    facts.append(_card.fact("last run", _card.ago(last_run) or "never",
                            "warn" if stale else "dim"))

    # When every courier is working, one row says so with the freshness of each,
    # because "the post is still coming" is a fact and not the absence of one.
    if couriers and not stopped:
        parts = [f"{c.get('label', '').split()[0]} {_card.ago(c.get('last_run')) or 'unknown'}"
                 for c in couriers]
        facts.append(_card.fact("arriving", ", ".join(parts), "dim"))

    held = data.get("held") or {}
    counted = {**held, "blocked": sum(int(v or 0) for v in (held.get("blocked") or {}).values())}
    parts = [f"{_card.count(int(counted.get(key) or 0))} {label}"
             for key, label in HELD if int(counted.get(key) or 0)]
    if parts:
        facts.append(_card.fact("held back", ", ".join(parts),
                                "warn" if int(counted.get("rate_limited") or 0) else "dim"))

    # A stopped courier is one thing a person has to go and restart, and it is
    # never visible in the waiting count it silently holds at zero.
    return _card.build(TITLE, headline, waiting + len(stopped),
                       _card.zulu(last_run), facts)
