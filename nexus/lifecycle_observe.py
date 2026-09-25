"""Best-effort, low-volume timing observations for one GitHub/Tower issue lane.

Never changes selection, proof, retry or gate decisions. A failed observation is
missing telemetry, not a failed work flight.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

SCHEMA = "office.lifecycle.v1"


def revision(issue):
    eligibility = {
        "state": issue.get("state"),
        "labels": sorted(str(label.get("name", "")).lower() for label in issue.get("labels", [])),
        "assignee": (issue.get("assignee") or {}).get("login"),
        "body": issue.get("body") or "",  # dependency directives can live here
    }
    digest = hashlib.sha256(json.dumps(eligibility, sort_keys=True).encode()).hexdigest()
    return {"github_updated_at": issue.get("updated_at"), "eligibility_digest": digest}


def _key(repo, issue):
    return f"github:{repo.lower()}#{int(issue['number'])}"


def _write(ledger, kind, work_key, task_id, source_revision, attempt_id=None,
           *, at=None, transition_key=None, **extra):
    at = time.time() if at is None else at
    payload = dict(schema=SCHEMA, work_key=work_key, task_id=task_id,
                   source_revision=source_revision, attempt_id=attempt_id,
                   transition_key=transition_key or f"{kind}:{attempt_id}",
                   at=at, cause_event_id=None, missing_reason="cause_not_linked", **extra)
    try:
        ledger.event(kind, work_key, payload, "lifecycle", at)
        return ledger.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception:  # telemetry must not change the work result
        return None


def recognized(ledger, repo, task_id, issue, eligibility):
    try:
        if eligibility(issue) not in ("ready", "resume"):
            return
        key, source = _key(repo, issue), revision(issue)
        rows = ledger.conn.execute(
            "SELECT payload FROM events WHERE kind='lifecycle.recognized' AND subject=? ORDER BY id DESC LIMIT 1",
            (key,)).fetchall()
        if rows and json.loads(rows[0][0]).get("source_revision") == source:
            return
    except Exception:  # telemetry must not change the capture result
        return
    stamp = time.time()
    _write(ledger, "lifecycle.recognized", key, task_id, source,
           at=stamp, transition_key=f"recognized:{key}:{source['eligibility_digest']}:{source['github_updated_at']}",
           recognized_at=stamp, actionable_at=None, actionable_status="left_censored",
           eligibility="ready")


def selected_and_queued(ledger, fid, key, task, latest, selected_at):
    try:
        task_id = task["id"]
        source = revision(latest(ledger, "work.issue", task_id))
        flight = ledger.flight(fid)
        prior = ledger.conn.execute(
            "SELECT f.id FROM flights f JOIN tasks t ON t.id=f.task_id WHERE t.dedupe_key=? AND f.id<>? "
            "ORDER BY f.created_at DESC,f.rowid DESC LIMIT 1", (key, fid)).fetchone()
        prior_fid = prior[0] if prior else None
    except Exception:  # telemetry must not change the claim result
        return
    previous = None
    previous_source = None
    if prior_fid:
        try:
            row = ledger.conn.execute(
                "SELECT payload FROM events WHERE kind='lifecycle.selected' "
                "AND json_extract(payload,'$.attempt_id')=? ORDER BY id DESC LIMIT 1",
                (prior_fid,)).fetchone()
            if row:
                previous = prior_fid
                previous_source = json.loads(row[0]).get("source_revision")
        except (sqlite3.Error, ValueError, TypeError):
            pass
    reason = "source_change" if previous and previous_source != source else (
        "retry" if previous else "first_or_prior_uninstrumented")
    _write(ledger, "lifecycle.selected", key, task_id, source, fid,
           at=selected_at, transition_key=f"selected:{fid}", previous_attempt_id=previous,
           resume_reason=reason, selected_at=selected_at, eligible_since=None,
           eligible_since_status="source_unavailable")
    _write(ledger, "lifecycle.queued", key, task_id, source, fid,
           at=flight["created_at"], transition_key=f"queued:{fid}",
           queued_at=flight["created_at"], flight_id=fid)


def for_flight(ledger, fid, kind, *, at=None, transition_key=None, **extra):
    """Reuse the selected revision; never substitute a later issue snapshot."""
    try:
        row = ledger.conn.execute(
            "SELECT payload FROM events WHERE kind='lifecycle.selected' "
            "AND json_extract(payload,'$.attempt_id')=? ORDER BY id DESC LIMIT 1", (fid,)).fetchone()
        if row is None:
            return None
        selected = json.loads(row[0])
        extra = {name: value() if callable(value) else value for name, value in extra.items()}
        return _write(ledger, kind, selected["work_key"], selected["task_id"],
                      selected["source_revision"], fid, at=at,
                      transition_key=transition_key or f"{kind}:{fid}", **extra)
    except Exception:  # telemetry must not change the flight result
        return None


def latest_receipt_id(ledger, task_id):
    try:
        row = ledger.conn.execute(
            "SELECT id FROM events WHERE kind='work.receipt' AND subject=? ORDER BY id DESC LIMIT 1",
            (task_id,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def gate_state(ledger, task, issue, reason):
    """Track a source-owned gate; a cleared gate closes its exact open wait."""
    try:
        key = task["dedupe_key"]
        starts = ledger.conn.execute(
            "SELECT payload FROM events WHERE kind='lifecycle.wait_started' AND subject=? "
            "AND json_extract(payload,'$.gate_origin')='tower_gate' ORDER BY id", (key,)).fetchall()
        ended = {json.loads(row[0])["wait_id"] for row in ledger.conn.execute(
            "SELECT payload FROM events WHERE kind='lifecycle.wait_ended' AND subject=? "
            "AND json_extract(payload,'$.gate_origin')='tower_gate'", (key,))}
        open_waits = [json.loads(row[0]) for row in starts if json.loads(row[0])["wait_id"] not in ended]
        for old in open_waits:
            if old.get("reason_code") == reason:
                return
            _write(ledger, "lifecycle.wait_ended", key, task["id"], revision(issue),
                   transition_key=f"gate_end:{old['wait_id']}", wait_id=old["wait_id"],
                   wait_kind=old["wait_kind"], gate_origin="tower_gate",
                   reason_code="source_or_gate_changed")
        if not reason:
            return
        kind = ("dependency" if reason.startswith("blocked by open dependency") else
                "external" if reason.startswith("route ") else "safety_gate")
        source = revision(issue)
        wait_id = f"gate:{task['id']}:{source['eligibility_digest']}"
        _write(ledger, "lifecycle.wait_started", key, task["id"], source,
               transition_key=f"gate_start:{wait_id}", wait_id=wait_id,
               wait_kind=kind, gate_origin="tower_gate", reason_code=reason[:160])
    except Exception:  # gate behavior never depends on telemetry
        return
