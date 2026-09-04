"""A calm, read-only view of Nexus automation runs.

The ledger is the authority for state; each flight's own log and lane outputs say what
happened. Repeated schedules are grouped so a five-minute probe occupies one row, not 288.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import time
from datetime import datetime, timezone

ROOT = pathlib.Path.home() / "Library" / "Application Support" / "nexus"
LEDGER = pathlib.Path(os.environ.get("OFFICE_NEXUS_LEDGER", ROOT / "ledger.sqlite"))
FLIGHTS = pathlib.Path(os.environ.get("OFFICE_NEXUS_FLIGHTS", ROOT / "flights"))
RECENT_SECONDS = 24 * 3600
MAX_FAMILIES = 16
MAX_RUNS = 12
MAX_TEXT = 12_000
ACTIVE = ("queued", "running", "verifying", "verified", "landing", "resolving")
SUCCESS = ("produced", "landed")


def _iso(stamp) -> str:
    if not stamp:
        return ""
    return datetime.fromtimestamp(float(stamp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: pathlib.Path, limit: int = MAX_TEXT) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def _line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if not line.startswith(("[", "Recorded:")):
            return line[:240]
    return (lines[-1] if lines else "")[:240]


def _transcript(flight_id: str, title: str, objective: str) -> list[dict]:
    root = FLIGHTS / flight_id
    blocks = []
    request = objective.strip() or title.strip()
    if request:
        blocks.append({"speaker": "requested", "text": request[:4_000]})
    log = _read(root / "log")
    if log:
        blocks.append({"speaker": "run", "text": log})
    lanes = root / "lanes"
    if lanes.is_dir():
        for path in sorted(lanes.glob("*.out"))[:24]:
            text = _read(path)
            if text:
                blocks.append({"speaker": path.stem.replace("__", " #"), "text": text})
    result = _read(root / "result.json", 4_000)
    if result:
        try:
            parsed = json.loads(result)
            error = parsed.get("error")
            if error:
                blocks.append({"speaker": "error", "text": json.dumps(error, ensure_ascii=False)})
        except (TypeError, ValueError):
            pass
    return blocks


def _run(row: sqlite3.Row) -> dict:
    started, ended = row["started_at"], row["ended_at"]
    seconds = max(0, int((ended or time.time()) - (started or row["created_at"])))
    log = _read(FLIGHTS / row["id"] / "log", 8_000)
    lane_lines = []
    lanes = FLIGHTS / row["id"] / "lanes"
    if lanes.is_dir():
        for path in sorted(lanes.glob("*.out"))[:24]:
            line = _line(_read(path, 4_000))
            if line:
                lane_lines.append(line)
    summary = _line(log) or (lane_lines[-1] if lane_lines else "")
    return {
        "id": row["id"], "state": row["state"], "title": row["title"],
        "started_at": _iso(started or row["created_at"]), "ended_at": _iso(ended),
        "duration_s": seconds, "summary": summary,
        "transcript": _transcript(row["id"], row["title"], row["objective"] or ""),
    }


def _empty(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail, "done": 0, "active": 0,
            "open": 0, "needs": 0, "families": []}


def read(now: float | None = None) -> dict:
    now = now or time.time()
    if not LEDGER.exists():
        return _empty("missing", "no Nexus run ledger is installed")
    try:
        uri = f"file:{LEDGER}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT f.*, t.title, COALESCE(t.objective,'') objective, p.name plan, "
                "p.enabled FROM flights f JOIN tasks t ON t.id=f.task_id "
                "JOIN plans p ON p.id=f.plan_id WHERE f.created_at>=? "
                "ORDER BY f.created_at DESC", (now - RECENT_SECONDS,)).fetchall()
            open_by_plan = dict(db.execute(
                "SELECT p.name, count(*) FROM tasks t JOIN plans p ON p.id=t.plan_id "
                "WHERE t.state IN ('candidate','accepted','running') GROUP BY p.name").fetchall())
    except (OSError, sqlite3.Error) as exc:
        return _empty("unreadable", f"run ledger unreadable: {type(exc).__name__}")

    grouped = {}
    for row in rows:
        grouped.setdefault(row["plan"], []).append(row)
    families = []
    for plan, plan_rows in grouped.items():
        runs = [_run(row) for row in plan_rows[:MAX_RUNS]]
        states = [row["state"] for row in plan_rows]
        latest = runs[0]
        failures = sum(state == "failed" for state in states)
        families.append({
            "id": plan, "name": plan, "state": latest["state"],
            "enabled": bool(plan_rows[0]["enabled"]), "last_at": latest["ended_at"] or latest["started_at"],
            "summary": latest["summary"], "runs": runs, "count": len(plan_rows),
            "done": sum(state in SUCCESS for state in states),
            "failed": failures, "active": sum(state in ACTIVE for state in states),
            "open": int(open_by_plan.get(plan, 0)),
            # A recovered failure is history. Only a currently enabled plan whose latest
            # run failed is an exception; disabled experiments never summon a person.
            "needs": bool(plan_rows[0]["enabled"] and latest["state"] == "failed"),
        })
    families.sort(key=lambda row: row["last_at"], reverse=True)
    families.sort(key=lambda row: (not row["needs"], not bool(row["active"])))
    families = families[:MAX_FAMILIES]
    return {
        "state": "ok", "detail": "", "families": families,
        "done": sum(bool(row["done"]) for row in families),
        "active": sum(row["active"] for row in families),
        "open": sum(row["open"] for row in families),
        "needs": sum(1 for row in families if row["needs"]),
    }
