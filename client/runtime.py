"""The local agent runtime, as a source the office can read.

The office already knows what work EXISTS, from the issue pipeline. This is the
half that knows what is happening RIGHT NOW: which agent is mid-run, what it is
blocked on, what it has cost. That lives on this machine and never leaves it, so
the adapter runs here beside everything else that holds credentials.

Two channels, deliberately separate, because they fail independently:

  the gate    FILES at <root>/_meta/state/pending-question[.<bot>].json
              Present whenever an agent is blocked, whether or not any dashboard
              is running. This is the channel that matters, so it is the one that
              does not depend on a server being up.

              One file per asking bot, because two bots run on two threads and
              can raise a hand in the same second; on one shared file the second
              write erased the first and that question was answered by nobody,
              forever. So every read here walks the whole set, and every answer
              is matched by id across the whole set. Reading only the unnamed
              file would show one raised hand and hide the other, which is the
              one thing this room is not allowed to do.

  the board   HTTP GET <url>/api/state
              Commissions, runs, cost. Only available while the dashboard serves.

Each reports its own reachability. "The dashboard is down" and "nothing is
happening" must never render the same, which is the whole reason this file
returns a status for each channel instead of an empty dict for both.
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import private_state as private

DEFAULT_URL = "http://127.0.0.1:8787"
STATE = "_meta/state"
# The unnamed gate: one nobody attached a bot to. Named ones sit beside it as
# `pending-question.<bot>.json`. These three constants are the harness's file
# layout restated, and they are the whole coupling: the office reads the same
# directory the harness writes, so a gate is visible with nothing else running.
PENDING_NAME = "pending-question.json"
PENDING_PREFIX = "pending-question."
PENDING_SUFFIX = ".json"
PENDING_GLOB = PENDING_PREFIX + "*" + PENDING_SUFFIX
PENDING_LOCK_NAME = "pending-question.lock"
# Kept for anyone who imported it: the unnamed gate, relative to the root.
PENDING = STATE + "/" + PENDING_NAME

# The runtime is a local dev server. If it does not answer in a couple of seconds
# it is not running, and a snapshot push must not hang waiting to find out.
TIMEOUT = 3


def _root():
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _url():
    return os.environ.get("OFFICE_RUNTIME_URL", DEFAULT_URL).rstrip("/")


def configured() -> bool:
    return bool(_root() or os.environ.get("OFFICE_RUNTIME_URL"))


def _bot_of(path: pathlib.Path) -> str:
    """The bot a pending file is named for; "" for the one nobody named."""
    if path.name == PENDING_NAME:
        return ""
    return path.name[len(PENDING_PREFIX):-len(PENDING_SUFFIX)]


def _asked_at(record: dict) -> float:
    """When the hand went up. A record with no usable time sorts FIRST, not last,
    which is the harness's own rule: a gate whose age we cannot read is not a gate
    we get to put at the back of the queue."""
    try:
        return float(record.get("asked_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pending_files(root: pathlib.Path) -> tuple[list[tuple[pathlib.Path, dict]], bool]:
    """(every readable pending file with its record, oldest first), (something torn).

    A file that is missing, half written or not an object is skipped rather than
    raised on, because one bot's mangled file must never hide another bot's raised
    hand. The second value says whether anything WAS skipped, so a caller can say
    "unreadable" out loud instead of reporting a quiet all-clear.
    """
    state = pathlib.Path(root) / STATE
    try:
        named = sorted(state.glob(PENDING_GLOB))
    except OSError:
        named = []
    found: list[tuple[pathlib.Path, dict]] = []
    torn = False
    for path in [state / PENDING_NAME, *named]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            # The runtime writes these whole, but a read can catch one mid-write.
            # A torn gate is not the absence of a gate and must never read as one.
            torn = True
            continue
        if isinstance(data, dict):
            found.append((path, data))
        else:
            torn = True
    found.sort(key=lambda pair: _asked_at(pair[1]))
    return found, torn


@contextmanager
def _pending_lock(root: pathlib.Path):
    """Share the Harness lock for gate replacement, answering, and cleanup."""
    root = pathlib.Path(root)
    lock = root / STATE / PENDING_LOCK_NAME
    private.append_text(lock, "", anchor=root)
    flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock, flags)
    os.chmod(lock, private.FILE_MODE, follow_symlinks=False)
    with os.fdopen(fd, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _shape(path: pathlib.Path, data: dict) -> dict:
    """One pending record in the shape the room renders. The ONE definition of it:
    the single gate and the list are the same object seen twice, so they can never
    disagree about what a gate looks like."""
    asked = _asked_at(data)
    return {
        "state": "pending",
        "id": str(data.get("id") or ""),
        "permission": str(data.get("permission") or ""),
        # The literal thing being asked about. Never summarised, never truncated
        # into ambiguity: a gate you approve without seeing the exact target is
        # not a gate.
        "target": str(data.get("target") or "")[:4000],
        "detail": str(data.get("detail") or "")[:4000],
        "asked_at": asked,
        "waiting_s": round(time.time() - asked) if asked else None,
        # Which bot asked. The record says, and the file name says when it does
        # not. Absent means the shared session.
        "bot": str(data.get("bot") or _bot_of(path)) or None,
    }


def _gate_root() -> tuple[pathlib.Path | None, dict | None]:
    """(root, the reason there is nothing to read). Exactly one is None."""
    root = _root()
    if root is None:
        return None, {"state": "unconfigured"}
    if not root.exists():
        return None, {"state": "missing-root", "detail": str(root)}
    return root, None


def read_gate() -> dict:
    """The oldest pending permission question, read straight off disk.

    Returns a status dict, never a bare None, because the caller has to be able to
    tell "no root configured" from "configured and nothing pending". The first is
    a setup gap; the second is good news.

    Oldest across every bot, not just the unnamed file: since the harness gave each
    bot its own file, reading one name would answer "clear" while a named bot stood
    there with its hand up.
    """
    root, why = _gate_root()
    if why is not None:
        return why

    files, torn = _pending_files(root)
    for path, data in files:
        if not data.get("answer"):
            return _shape(path, data)
    if torn:
        return {"state": "unreadable", "detail": "a gate file did not parse"}
    return {"state": "clear"}


def read_gates() -> dict:
    """Every raised hand at once: {"state", "gates"}, oldest first.

    `state` is "ok" when the floor was read cleanly, and otherwise the same word
    `read_gate` would have used for the same trouble, so the two endpoints can
    never tell different stories about whether the gate channel is working.

    `gates` is still filled in when something was torn. A neighbour's half-written
    file is a reason to say so, never a reason to drop a hand that IS readable.
    """
    root, why = _gate_root()
    if why is not None:
        return {**why, "gates": []}

    files, torn = _pending_files(root)
    gates = [_shape(path, data) for path, data in files if not data.get("answer")]
    if torn:
        return {"state": "unreadable", "detail": "a gate file did not parse", "gates": gates}
    return {"state": "ok", "gates": gates}


def answer_gate(root: pathlib.Path, question_id: str, answer: str, always: bool) -> tuple[bool, str]:
    """Answer the pending question, but ONLY if it is still the same question.

    The id is searched across every bot's file, so the answer lands on the question
    that carries it and on no other. The runtime's own waiting loop compares ids
    before accepting an answer, so a mismatched write is already harmless there.
    This checks first anyway, so the office can say "that question is gone" out
    loud instead of silently writing into a file nobody is reading any more.
    """
    root = pathlib.Path(root)
    try:
        with _pending_lock(root):
            files, torn = _pending_files(root)
            target = next(
                ((p, d) for p, d in files if str(d.get("id") or "") == question_id),
                None,
            )
            if target is None:
                if torn:
                    return False, "could not read the gate: a gate file did not parse"
                if not files:
                    return False, "that question is gone; the agent stopped waiting"
                return False, "the agent has moved on; this answer was for an older question"

            path, data = target
            if data.get("answer"):
                return False, "that question was already answered somewhere else"
            if path.is_symlink():
                return False, "could not write the answer: gate path is a symlink"

            # Re-read at the commit point. The shared lock serializes first-party
            # writers; the id check also refuses an uncoordinated replacement.
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False, "could not read the gate at the answer commit point"
            if not isinstance(current, dict) or str(current.get("id") or "") != question_id:
                return False, "the agent has moved on; this answer was for an older question"
            if current.get("answer"):
                return False, "that question was already answered somewhere else"

            current["answer"] = answer
            current["always"] = bool(always)
            private.atomic_write_text(path, json.dumps(current), anchor=root)
    except OSError as exc:
        return False, f"could not write the answer: {exc}"
    return True, f"{answer}" + (" (always)" if always else "") + f" for {current.get('permission')}"


def _get(path: str):
    req = urllib.request.Request(_url() + path, method="GET")
    req.add_header("user-agent", "nexus-office-sync/1.0")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode() or "{}")


def post(path: str, body: dict, timeout: float = 20):
    """`timeout` is the caller's, because not every POST is a quick one: a chat
    turn is a whole agent run and holds the connection open for minutes."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(_url() + path, data=data, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", "nexus-office-sync/1.0")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def read_board() -> dict:
    """Commissions, runs and cost from the dashboard, or an honest reason why not."""
    if not configured():
        return {"state": "unconfigured"}
    try:
        state = _get("/api/state")
    except urllib.error.URLError as exc:
        # Not running is the NORMAL case: the dashboard is a foreground dev server
        # that is usually closed. It is still a state the room has to show, so it
        # is reported rather than swallowed.
        return {"state": "down", "detail": str(getattr(exc, "reason", exc))[:160]}
    except Exception as exc:
        return {"state": "error", "detail": str(exc)[:160]}

    metrics = state.get("metrics") or {}
    return {
        "state": "up",
        "root": state.get("root") or "",
        "runs": state.get("runs") or [],
        "active": state.get("active") or [],
        "complete": (state.get("complete") or [])[:12],
        "archived_count": len(state.get("archived") or []),
        "metrics": {
            "active": metrics.get("active"),
            "complete": metrics.get("complete"),
            "archived": metrics.get("archived"),
            "runs": metrics.get("runs"),
            "total_cost": metrics.get("total_cost"),
            "average_cache_read_ratio": metrics.get("average_cache_read_ratio"),
        },
    }


def snapshot() -> dict:
    """Everything the room should know about the runtime, in one object."""
    return {"gate": read_gate(), "board": read_board(), "url": _url(),
            "root": str(_root()) if _root() else ""}
