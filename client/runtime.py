"""The local agent runtime, as a source the office can read.

The office already knows what work EXISTS, from the issue pipeline. This is the
half that knows what is happening RIGHT NOW: which agent is mid-run, what it is
blocked on, what it has cost. That lives on this machine and never leaves it, so
the adapter runs here beside everything else that holds credentials.

Two channels, deliberately separate, because they fail independently:

  the gate    a FILE at <root>/_meta/state/pending-question.json
              Present whenever an agent is blocked, whether or not any dashboard
              is running. This is the channel that matters, so it is the one that
              does not depend on a server being up.

  the board   HTTP GET <url>/api/state
              Commissions, runs, cost. Only available while the dashboard serves.

Each reports its own reachability. "The dashboard is down" and "nothing is
happening" must never render the same, which is the whole reason this file
returns a status for each channel instead of an empty dict for both.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8787"
PENDING = "_meta/state/pending-question.json"

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


def read_gate() -> dict:
    """The pending permission question, read straight off disk.

    Returns a status dict, never a bare None, because the caller has to be able to
    tell "no root configured" from "configured and nothing pending". The first is
    a setup gap; the second is good news.
    """
    root = _root()
    if root is None:
        return {"state": "unconfigured"}
    if not root.exists():
        return {"state": "missing-root", "detail": str(root)}

    path = root / PENDING
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"state": "clear"}
    except OSError as exc:
        return {"state": "error", "detail": str(exc)[:200]}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # The runtime writes this file whole, but it can be caught mid-write.
        # A torn read is not an absence of a gate, and must not read as one.
        return {"state": "unreadable", "detail": "the gate file did not parse"}

    if data.get("answer"):
        return {"state": "clear"}

    asked = float(data.get("asked_at") or 0)
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
        # Which bot asked, when the runtime says. Absent means the shared session.
        "bot": str(data.get("bot") or "") or None,
    }


def answer_gate(root: pathlib.Path, question_id: str, answer: str, always: bool) -> tuple[bool, str]:
    """Answer the pending question, but ONLY if it is still the same question.

    The runtime's own waiting loop compares ids before accepting an answer, so a
    mismatched write is already harmless there. This checks first anyway, so the
    office can say "that question is gone" out loud instead of silently writing
    into a file nobody is reading any more.
    """
    path = pathlib.Path(root) / PENDING
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "that question is gone; the agent stopped waiting"
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"could not read the gate: {exc}"

    if data.get("answer"):
        return False, "that question was already answered somewhere else"
    if str(data.get("id") or "") != question_id:
        return False, "the agent has moved on; this answer was for an older question"

    data["answer"] = answer
    data["always"] = bool(always)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return False, f"could not write the answer: {exc}"
    return True, f"{answer}" + (" (always)" if always else "") + f" for {data.get('permission')}"


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
