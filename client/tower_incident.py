"""Office-owned Tower health incident, enabled only at the TBS probe cutover.

Office polls independently of Tower every five minutes. The issue is the durable
alert; the local receipt lets a failed GitHub call remain visible and retryable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
from datetime import datetime, timezone

REPO = "ariaxhan/nexus-office"
TITLE = "Tower controller or ledger unhealthy"
MARKER = "<!-- office-tower-health/v1 -->"
STATE = Path.home() / ".local/state/nexus-office/tower-health.json"
_BUSY = threading.Lock()


def _call(args):
    done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip()[:300] or f"gh exit {done.returncode}")
    return done.stdout.strip()


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _issue(call):
    rows = json.loads(call(["issue", "list", "-R", REPO, "--state", "open", "--limit", "100",
                            "--search", '"Tower controller or ledger unhealthy" in:title',
                            "--json", "number,title,body"]))
    matches = [row for row in rows if row.get("title") == TITLE and MARKER in (row.get("body") or "")]
    if len(matches) > 1:
        raise RuntimeError("multiple Tower health issues; refusing to choose one")
    return matches[0] if matches else None


def condition(board):
    if board.get("state") != "ok":
        return "unhealthy", board.get("detail") or board.get("state") or "Tower board unreadable"
    tower = board.get("tower") or {}
    if tower.get("state") in ("down", "erroring"):
        return "unhealthy", tower.get("detail") or tower["state"]
    return "healthy", tower.get("state") or "running"


def sync(board, *, call=_call, path=STATE):
    state, detail = condition(board)
    at = datetime.now(timezone.utc).isoformat()
    receipt = {"at": at, "state": state, "detail": str(detail)[:300], "issue": None, "pending": None}
    try:
        existing = _issue(call)
        if state == "unhealthy":
            body = f"{MARKER}\n\nOffice observed Tower health: {detail}\n"
            if existing:
                receipt["issue"] = existing["number"]
            else:
                call(["issue", "create", "-R", REPO, "--title", TITLE, "--body", body])
                receipt["issue"] = "created; lookup pending"
        elif existing:
            call(["issue", "close", str(existing["number"]), "-R", REPO, "--comment",
                  f"Office verified recovery at {at}: {detail}"])
            receipt["issue"] = existing["number"]
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        receipt["pending"] = str(exc)[:300]
    _write(path, receipt)
    return receipt


def after_snapshot(snapshot):
    if os.environ.get("OFFICE_TOWER_INCIDENTS") != "1" or not _BUSY.acquire(blocking=False):
        return
    board = ((snapshot.get("automation") or {}).get("tower") or {})

    def run():
        try:
            sync(board)
        finally:
            _BUSY.release()

    threading.Thread(target=run, name="office-tower-incident", daemon=True).start()
