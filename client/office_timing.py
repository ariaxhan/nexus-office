"""Observe first successful Office API exposure of a verified Nexus work flight.

The System Runs API reads the live ledger, not the periodic world snapshot. This
records the first response containing each verified flight; it does not claim
that a phone rendered it or Aria saw it. Failure cannot change the response.
"""

from __future__ import annotations

import json
import sqlite3
import time

import run_board


def observe_runs(body, server_revision):
    ids = [row["id"] for row in body.get("items", [])
           if row.get("plan") == "github-work" and row.get("state") == "landed"]
    if not ids:
        return 0
    try:
        with sqlite3.connect(f"file:{run_board.LEDGER}?mode=rw", uri=True, timeout=0.05) as db:
            marks = ",".join("?" * len(ids))
            verified = db.execute(
                "SELECT id, payload FROM events WHERE kind='lifecycle.verified' "
                f"AND json_extract(payload,'$.attempt_id') IN ({marks})", ids).fetchall()
            if not verified:
                return 0
            published = {json.loads(row[0]).get("verified_event_id") for row in db.execute(
                "SELECT payload FROM events WHERE kind='lifecycle.office_published'")}
            count = 0
            for event_id, raw in verified:
                if event_id in published:
                    continue
                source = json.loads(raw)
                at = time.time()
                payload = {key: source.get(key) for key in
                           ("schema", "work_key", "task_id", "source_revision", "attempt_id")}
                payload.update(transition_key=f"office_published:{event_id}", at=at,
                               cause_event_id=event_id, verified_event_id=event_id,
                               flight_id=source.get("attempt_id"), published_at=at,
                               office_revision=server_revision,
                               surface="system_runs_response", api_item_key=source.get("attempt_id"))
                db.execute("INSERT INTO events(ts,kind,subject,payload,source) VALUES (?,?,?,?,?)",
                           (at, "lifecycle.office_published", source["work_key"],
                            json.dumps(payload, sort_keys=True), "office"))
                count += 1
            db.commit()
            return count
    except Exception:  # a read response remains successful if observation cannot write
        return 0
