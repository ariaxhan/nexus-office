"""Buzz: the one way this machine says something out loud.

The office is a room you have to be looking at. Buzz is the tap on the shoulder
for the few things worth interrupting a human over: a raised hand, a PR that
landed, a lane that refused to act. Everything else stays in the room.

This is a workflow webhook, not Aria's Nostr key. A plain HTTPS POST with a
per-workflow secret, which is exactly why the door may hold it: the door is a
machine that should never carry an identity. The identity path is `buzz say`,
and it is deliberately not reachable from here.

Three things about this contract have already cost a day between them, so they
are stated rather than implied:

  the field is `board`   `trigger.text` is a built-in the relay's executor
                         registers AFTER webhook fields, so a body field called
                         `text` is silently clobbered and the message posts
                         EMPTY, with no error. `board` is the field.

  202 or it did not      The relay answers 202 Accepted on success. Anything
                         else, 200 included, means the post did not land, and
                         `hook-post.sh` has always treated it that way.

  unset means quiet      No secret, or no URL, and `notify` returns False and
                         says so once. It never raises. A notifier that can take
                         the door down with it is worse than no notifier.

The throttle is the other half of failing quietly. A GitHub redelivery storm can
replay the same event many times in a minute, and the room must not turn that
into many taps on the shoulder. The same `kind` for the same `subject` fires at
most once per OFFICE_BUZZ_MIN_S seconds, in memory, per process.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# The live workflow: relay + `/hooks/<workflow-id>`, bound to #queue. Overridable
# whole, by OFFICE_BUZZ_HOOK_URL, so a second tailnet or a test relay needs no
# code change.
RELAY = "https://thinking-brain-school.communities.buzz.xyz"
WORKFLOW = "7a1c89ec-54ec-488f-9089-c0caf21aee6d"
DEFAULT_URL = f"{RELAY}/hooks/{WORKFLOW}"

# 20 s, the same ceiling hook-post.sh uses. Long enough for a cold relay, short
# enough that a hung post cannot hold a door thread all day.
TIMEOUT = 20
ACCEPTED = 202
DEFAULT_MIN_S = 300

_LOCK = threading.Lock()
_LAST: dict[tuple[str, str], float] = {}
_SAID: set[str] = set()


def _log(msg: str) -> None:
    print(f"[buzz] {msg}", file=sys.stderr, flush=True)


def _once(msg: str) -> None:
    """Say this at most once per process. The unconfigured case is a standing
    condition, not an event: repeating it every webhook would bury the log."""
    with _LOCK:
        if msg in _SAID:
            return
        _SAID.add(msg)
    _log(msg)


def _url() -> str:
    """Blank means unset, not "off": an env var that got exported empty by a
    wrapper must not silently disable the only channel this room has."""
    return (os.environ.get("OFFICE_BUZZ_HOOK_URL") or "").strip() or DEFAULT_URL.strip()


def _secret() -> str:
    """The secret comes from the environment, never from the keychain. The
    wrapper exports it; this file runs in whatever the door runs in and must
    work on a machine with no keychain at all."""
    return (os.environ.get("OFFICE_BUZZ_SECRET") or "").strip()


def _min_s() -> float:
    raw = (os.environ.get("OFFICE_BUZZ_MIN_S") or "").strip()
    try:
        return max(0.0, float(raw)) if raw else float(DEFAULT_MIN_S)
    except ValueError:
        return float(DEFAULT_MIN_S)


def _throttled(kind: str, subject: str) -> bool:
    """True when this kind fired for this subject too recently.

    The clock is marked on the ATTEMPT, not on success. A relay that is failing
    under a redelivery storm is the exact moment you least want to retry sixty
    times, and a caller that wants a failed post again can say so by passing a
    different subject."""
    window = _min_s()
    now = time.monotonic()
    key = (kind, subject)
    with _LOCK:
        seen = _LAST.get(key)
        if window and seen is not None and now - seen < window:
            return True
        _LAST[key] = now
    return False


def _post(url: str, secret: str, text: str) -> bool:
    body = json.dumps({"board": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-webhook-secret", secret)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        _log(f"the relay refused the post (HTTP {exc.code})")
        return False
    except Exception as exc:
        # Never the exception's repr: a urllib error can carry the request, and
        # the request carries the secret header.
        _log(f"the relay is unreachable ({type(exc).__name__})")
        return False
    if code != ACCEPTED:
        _log(f"the relay did not accept the post (HTTP {code})")
        return False
    return True


def notify(kind: str, text: str, subject: str = "") -> bool:
    """Post one line into #queue. True only when the relay accepted it.

    `kind` and `subject` are the throttle key, never part of the message: kind is
    what happened ("gate", "landed", "refused"), subject is what it happened to
    (a repo, a gate id). Two different repos landing a PR are two taps; the same
    repo replayed six times by a redelivery is one."""
    url, secret = _url(), _secret()
    if not url or not secret:
        _once("buzz not configured")
        return False
    line = " ".join((text or "").split())
    if not line:
        return False
    if _throttled(kind, subject):
        return False
    return _post(url, secret, line)


# ── what the taps say ────────────────────────────────────────────────────────
#
# Pure functions, so the wording is testable without a relay and reviewable
# without a run. Every one stays under 200 characters, because a notification
# nobody can read at a glance is a notification nobody reads.

CAP = 200


def _trim(value, limit: int) -> str:
    """One line, at most `limit` characters, ellipsis included in the count."""
    s = " ".join(str(value or "").split())
    return s if len(s) <= limit else s[: max(1, limit - 3)].rstrip() + "..."


def gate_raised(gate: dict) -> str:
    """A blocked agent, named by the bot that asked. The target is the literal
    thing being permitted, trimmed only here: the room still shows it whole."""
    gate = gate or {}
    who = _trim(gate.get("bot"), 40) or "an agent"
    what = _trim(gate.get("permission"), 40) or "permission"
    target = _trim(gate.get("target"), 80) or "something it did not name"
    return f"a gate is raised: {who} wants {what} on {target}"[:CAP]


def pr_landed(repo, number, title) -> str:
    return f"landed: {_trim(repo, 60)} #{_trim(number, 12)} {_trim(title, 80)}".strip()[:CAP]


def lane_refused(repo, issue, detail) -> str:
    where = _trim(repo, 60)
    issue = _trim(issue, 12)
    if issue:
        where = f"{where} #{issue}"
    return f"a lane refused on {where}: {_trim(detail, 80) or 'no reason given'}"[:CAP]


# ── the watcher ──────────────────────────────────────────────────────────────
# Three things are worth a ping on the board: a hand raised, a PR landed, a
# lane refused. Gates come from the runtime floor; the other two come from the
# pipeline's own receipts, tailed by byte offset so a restart never re-announces
# the past (the first read only takes a position).

def new_gate_ids(seen: set, gates: list) -> list:
    """The ids on the floor now that were not there before, oldest first."""
    return [str(g.get("id") or "") for g in gates
            if g.get("id") and str(g["id"]) not in seen]


def new_receipts(path, offset: int):
    """(rows, new_offset): the receipt lines appended since offset. A file that
    shrank (rotated) is read from the top again."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:
        offset = 0
    rows = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    for line in chunk.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, offset + len(chunk)


def announce_receipt(row: dict) -> bool:
    outcome = str(row.get("outcome") or "")
    repo = str(row.get("repo") or "")
    if outcome == "terminal" and row.get("live_outcome") == "PASS" and row.get("notification_accepted") is True:
        return notify("delivery_terminal", pr_landed(repo, row.get("issue") or "", row.get("detail") or ""),
                      subject=f"{repo}#{row.get('issue') or ''}")
    if outcome == "refused":
        return notify("lane_refused", lane_refused(repo, row.get("issue") or "", row.get("detail") or ""),
                      subject=f"{repo}#{row.get('issue') or ''}")
    return False


def watch(read_gates, receipts_path, every: float = 15.0) -> None:
    """Runs forever on a daemon thread. read_gates() returns {"gates": [...]}."""
    seen: set = set()
    first = True
    offset = 0
    if receipts_path:
        try:
            offset = os.path.getsize(receipts_path)
        except OSError:
            offset = 0
    while True:
        try:
            gates = (read_gates() or {}).get("gates") or []
            fresh = new_gate_ids(seen, gates)
            seen = {str(g.get("id")) for g in gates if g.get("id")}
            if not first:
                for gid in fresh:
                    gate = next(g for g in gates if str(g.get("id")) == gid)
                    notify("gate_raised", gate_raised(gate), subject=gid)
            if receipts_path:
                rows, offset = new_receipts(receipts_path, offset)
                for row in rows:
                    announce_receipt(row)
        except Exception as exc:  # noqa: BLE001 - the board is a courtesy, never a crash
            _log(f"buzz watch: {type(exc).__name__}")
        first = False
        time.sleep(every)
