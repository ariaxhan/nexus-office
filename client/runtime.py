"""Local runtime board and a read-only gate view of validated human input."""

from __future__ import annotations

import json
import datetime
import os
import pathlib
import time
import urllib.error
import urllib.request


DEFAULT_URL = "http://127.0.0.1:8787"
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


def read_gates() -> dict:
    """Native gate view over the same validated human-input records as Needs You."""
    import human_asks
    rows=human_asks.listing()['items']
    gates=[]
    for row in rows:
        try:
            asked_at=datetime.datetime.fromisoformat(row['created_at']).timestamp()
        except ValueError:
            asked_at=0
        gates.append({'state':'pending','id':row['id'],'permission':row['gate_type'],
                      'target':row['action'],'detail':row['action'],
                      'asked_at':asked_at,'bot':None})
    return {'state':'ok','gates':gates}


def read_gate() -> dict:
    gates=read_gates()['gates']
    return gates[0] if gates else {'state':'clear'}


def answer_gate(root, question_id: str, answer: str, always: bool) -> tuple[bool,str]:
    import human_asks
    import office_tasks
    with human_asks.connect() as db:
        row=db.execute("SELECT gate_type FROM asks WHERE id=? AND state='open'",(question_id,)).fetchone()
    if row is None:
        return False,'Human-input request is no longer open'
    if row['gate_type'] not in ('inaccessible_authentication','physical_action'):
        return False,'Answer this choice in Office Needs You'
    try:
        office_tasks.answer_human_input({'id':question_id,
            'answer':'Aria completed the requested action' if answer=='allow'
                     else 'Aria declined the requested action'})
    except (OSError,ValueError,FileNotFoundError,RuntimeError) as exc:
        return False,str(exc)
    return True,'Answer recorded; Office resumed the task'


def _get(path: str):
    req = urllib.request.Request(_url() + path, method="GET")
    req.add_header("user-agent", "nexus-office-sync/1.0")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode() or "{}")


def post(path: str, body: dict, timeout: float = 20, base_url: str | None = None):
    """`timeout` is the caller's, because not every POST is a quick one: a chat
    turn is a whole agent run and holds the connection open for minutes."""
    data = json.dumps(body).encode()
    req = urllib.request.Request((base_url or _url()).rstrip("/") + path, data=data, method="POST")
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
