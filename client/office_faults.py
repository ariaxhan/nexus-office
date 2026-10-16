"""Read Tower fault machine state for Office; never turn faults into asks."""
from contextlib import closing
import json
import sqlite3
import time

import run_board
from nexus import faults
from nexus.ledger import Ledger

DOOR_SOURCE = "watchdog:office-door-studio-shadow"


def _door_shadow(ledger):
    observations = []
    rows = ledger.conn.execute(
        "SELECT * FROM events WHERE kind = ? ORDER BY id DESC LIMIT 256",
        ("watchdog.observed",))
    for event in rows:
        try:
            payload = json.loads(event["payload"])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("source") == DOOR_SOURCE \
                and isinstance(payload.get("classification"), str):
            observations.append((event, payload))
    if not observations:
        return None
    event, payload = max(observations, key=lambda row: (row[0]["ts"], row[0]["id"]))
    fault_id = payload.get("fault_id")
    fault = faults.record(ledger, fault_id) if isinstance(fault_id, str) and fault_id else None
    return {"occurrence_id": event["subject"],
            "classification": payload["classification"], "observed_at": event["ts"],
            "stale": time.time() - event["ts"] > 7200,
            "late": payload.get("late", False), "fault": fault}


def snapshot(limit=20):
    try:
        with closing(Ledger(str(run_board.LEDGER), read_only=True)) as ledger:
            rows = faults.escalated(ledger)
            door = _door_shadow(ledger)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {"count": 0, "items": [], "door_shadow": None,
                "error": f"{type(exc).__name__}: {exc}"[:160]}
    return {"count": len(rows), "items": rows[:limit], "door_shadow": door,
            "error": None}
