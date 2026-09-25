"""Read-only v1 lifecycle projection. No production writer imports this module.

Input is the proposed lifecycle event contract in the existing Nexus events table.
Unknown and contradictory timestamps stay visible as data quality, never invented.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict

SCHEMA = "office.lifecycle.v1"
WINDOW_S = 7 * 86400
PRECEDENCE = (
    "human", "safety_gate", "dependency", "external", "backoff",
    "verification", "queue", "selection", "preselection_wait", "execution_unclassified",
)
WAIT_KINDS = set(PRECEDENCE[:5])
KINDS = {
    "lifecycle.recognized", "lifecycle.selected", "lifecycle.queued",
    "lifecycle.execution_started", "lifecycle.wait_started",
    "lifecycle.wait_ended", "lifecycle.verification_started",
    "lifecycle.verification_finished", "lifecycle.attempt_finished",
    "lifecycle.verified", "lifecycle.office_published",
}
ATTEMPT_KINDS = KINDS - {"lifecycle.recognized", "lifecycle.wait_started", "lifecycle.wait_ended"}
EDGES = (
    ("actionable_to_recognized", "actionable_at", "recognized_at"),
    ("recognized_to_selected", "recognized_at", "selected_at"),
    ("selected_to_queued", "selected_at", "queued_at"),
    ("queued_to_execution", "queued_at", "execution_started_at"),
    ("execution_elapsed", "execution_started_at", "finished_at"),
    ("verification_elapsed", "verification_started_at", "verification_finished_at"),
    ("verification_to_verified", "verification_finished_at", "verified_at"),
    ("verified_to_office", "verified_at", "published_at"),
)


def read_events(connection):
    """Read only; accepts a normal Nexus SQLite connection or disposable fixture."""
    rows = connection.execute(
        "SELECT id, ts, kind, subject, payload FROM events "
        "WHERE kind LIKE 'lifecycle.%' ORDER BY id"
    )
    return [dict(id=row[0], ts=row[1], kind=row[2], subject=row[3],
                 payload=json.loads(row[4])) for row in rows]


def _stamp(event):
    value = event["payload"].get("at", event["ts"])
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _identity(event):
    payload = event["payload"]
    return payload.get("work_key") if payload.get("schema") == SCHEMA else None


def _revision_key(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _unique(events):
    seen, result, errors = {}, [], []
    for event in sorted(events, key=lambda e: e["id"]):
        payload = event.get("payload", {})
        key = payload.get("transition_key")
        kind = event.get("kind")
        if not _valid_contract(event):
            errors.append({"event_id": event.get("id"), "reason": "invalid_contract"})
            continue
        if key in seen:
            prior = seen[key]
            if (prior["kind"], prior["subject"], prior["payload"]) != (event["kind"], event["subject"], payload):
                errors.append({"event_id": event["id"], "reason": "conflicting_replay"})
            continue
        seen[key] = event
        result.append(event)
    return result, errors


def _valid_contract(event):
    payload = event.get("payload", {})
    kind = event.get("kind")
    if kind not in KINDS or not _identity(event) or not payload.get("transition_key"):
        return False
    return not (event.get("subject") != payload["work_key"] or _stamp(event) is None or
                not payload.get("task_id") or payload.get("source_revision") is None or
                (kind in ATTEMPT_KINDS and not payload.get("attempt_id")) or
                not _valid_wait_contract(kind, payload) or
                (kind.startswith("lifecycle.verification_") and not payload.get("verification_id")) or
                (kind == "lifecycle.office_published" and not payload.get("verified_event_id")))


def _valid_wait_contract(kind, payload):
    if kind not in ("lifecycle.wait_started", "lifecycle.wait_ended"):
        return True
    return bool(payload.get("wait_id") and payload.get("wait_kind") in WAIT_KINDS)


def _one(events, kind, attempt_id=None):
    matches = [e for e in events if e["kind"] == kind and
               (attempt_id is None or e["payload"].get("attempt_id") == attempt_id)]
    return matches[0] if len(matches) == 1 else None


def _lineage(events, verified):
    by_id = defaultdict(list)
    for event in events:
        if event["kind"] == "lifecycle.selected":
            by_id[event["payload"].get("attempt_id")].append(event)
    chain, errors, current, seen = [], [], verified["payload"].get("attempt_id"), set()
    if not current:
        return chain, ["missing_verified_attempt"]
    while current:
        if current in seen:
            errors.append("attempt_cycle")
            break
        seen.add(current)
        candidates = by_id.get(current, [])
        if len(candidates) != 1:
            errors.append("missing_or_duplicate_attempt")
            break
        selected = candidates[0]
        chain.append(selected)
        current = selected["payload"].get("previous_attempt_id")
    chain.reverse()
    for prior, later in zip(chain, chain[1:]):
        if (prior["payload"].get("source_revision") != later["payload"].get("source_revision") and
                later["payload"].get("resume_reason") != "source_change"):
            errors.append("unexplained_source_revision_change")
    if chain and chain[-1]["payload"].get("source_revision") != verified["payload"].get("source_revision"):
        errors.append("verified_revision_mismatch")
    return chain, errors


def _span(start, end, label, errors):
    if start is None or end is None:
        return None
    if end < start:
        errors.append("clock_invalid:" + label)
        return None
    return (start, end, label)


def _account(start, end, spans):
    """Exclusive wall partition; raw overlapping spans remain available."""
    bounds = {start, end}
    clipped = []
    for left, right, label in spans:
        left, right = max(start, left), min(end, right)
        if right > left:
            clipped.append((left, right, label))
            bounds.update((left, right))
    totals = defaultdict(float)
    overlap = 0.0
    ticks = sorted(bounds)
    for left, right in zip(ticks, ticks[1:]):
        covering = [label for a, b, label in clipped if a <= left and right <= b]
        if len(covering) > 1:
            overlap += right - left
        label = next((name for name in PRECEDENCE if name in covering), "other_unclassified")
        totals[label] += right - left
    return dict(totals), overlap


def _work_row(events, verified):
    payload = verified["payload"]
    key, revision = payload["work_key"], payload.get("source_revision")
    scoped, recognition, chain, errors, stamps = _row_start(events, verified, key, revision)
    spans, raw_waits, open_waits = [], defaultdict(float), []
    attempt_ids = [e["payload"]["attempt_id"] for e in chain]
    for selected in chain:
        _attempt_span(scoped, selected, revision, stamps, spans, errors)
    _verification_spans(scoped, attempt_ids, payload, stamps, spans, open_waits, errors)
    _wait_spans(scoped, attempt_ids, spans, raw_waits, open_waits, errors)
    stamps["published_at"] = _publication_stamp(scoped, verified["id"], errors)
    edge_values, completeness = _edge_values(stamps, recognition, errors)
    accounting, overlap, envelope = _row_accounting(stamps, errors, chain, revision, spans)
    failed, held, worker_wall = _attempt_metrics(scoped, attempt_ids)
    return dict(work_key=key, source_revision=revision, verified_event_id=verified["id"],
                attempt_ids=attempt_ids, stamps=stamps, edges=edge_values,
                completeness=completeness, elapsed_s=envelope, exclusive_s=accounting,
                unclassified_s=accounting.get("other_unclassified") if envelope is not None else None,
                raw_wait_s=dict(raw_waits), overlap_s=overlap, open_intervals=open_waits,
                errors=sorted(set(errors)), proof_ref=payload.get("proof_ref"),
                failed_attempts=failed, held_attempts=held, worker_wall_s=worker_wall)


def _publication_stamp(scoped, verified_id, errors):
    pub = [e for e in scoped if e["kind"] == "lifecycle.office_published" and
           e["payload"].get("verified_event_id") == verified_id]
    if len(pub) > 1:
        errors.append("ambiguous_publication")
    return _stamp(pub[0]) if len(pub) == 1 else None


def _attempt_metrics(scoped, attempt_ids):
    finishes = [e["payload"] for e in scoped if e["kind"] == "lifecycle.attempt_finished" and
                e["payload"].get("attempt_id") in attempt_ids]
    outcomes = [e.get("outcome") for e in finishes]
    costs = [e.get("worker_wall_s") for e in finishes]
    return (sum(x == "failed" for x in outcomes), sum(x in ("HELD", "held") for x in outcomes),
            sum(costs) if costs and all(isinstance(x, (int, float)) for x in costs) else None)


def _row_start(events, verified, key, revision):
    scoped = [e for e in events if e["payload"].get("work_key") == key]
    recognition = _one([e for e in scoped if e["payload"].get("source_revision") == revision],
                       "lifecycle.recognized")
    chain, errors = _lineage(scoped, verified)
    if sum(e["kind"] == "lifecycle.recognized" and e["payload"].get("source_revision") == revision
           for e in scoped) > 1:
        errors.append("ambiguous_recognition")
    stamps = {}
    stamps["verified_at"] = _stamp(verified)
    stamps["recognized_at"] = _stamp(recognition) if recognition else None
    stamps["actionable_at"] = recognition["payload"].get("actionable_at") if recognition else None
    if recognition is None:
        errors.append("missing_recognition")
    if not verified["payload"].get("proof_ref"):
        errors.append("missing_proof")
    return scoped, recognition, chain, errors, stamps


def _row_accounting(stamps, errors, chain, revision, spans):
    start, end = stamps["recognized_at"], stamps["verified_at"]
    if start is None or end < start or errors:
        accounting, overlap, envelope = {}, None, None
    else:
        # Recognition to first selection, and known gaps between attempts, are not active work.
        relevant = [e for e in chain if e["payload"].get("source_revision") == revision]
        if relevant:
            first = _stamp(relevant[0])
            span = _span(start, first, "preselection_wait", errors)
            if span:
                spans.append(span)
        accounting, overlap = _account(start, end, spans)
        envelope = end - start
    return accounting, overlap, envelope


def _edge_values(stamps, recognition, errors):
    edge_values, completeness = {}, {}
    for name, a, b in EDGES:
        left, right = stamps.get(a), stamps.get(b)
        if left is None or right is None:
            edge_values[name] = None
            completeness[name] = "left_censored" if a == "actionable_at" and recognition else "not_emitted"
        elif right < left:
            edge_values[name] = None
            completeness[name] = "clock_invalid"
            errors.append("clock_invalid:" + name)
        else:
            edge_values[name] = right - left
            completeness[name] = "known"
    return edge_values, completeness


def _wait_spans(scoped, attempt_ids, spans, raw_waits, open_waits, errors):
    waits = [e for e in scoped if e["kind"] == "lifecycle.wait_started" and
             (e["payload"].get("attempt_id") in attempt_ids or
              e["payload"].get("attempt_id") is None)]
    for begin in waits:
        wp = begin["payload"]
        wait_id, kind, a = wp.get("wait_id"), wp.get("wait_kind"), _stamp(begin)
        if kind not in WAIT_KINDS or not wait_id:
            errors.append("invalid_wait")
            continue
        ends = [e for e in scoped if e["kind"] == "lifecycle.wait_ended" and
                e["payload"].get("wait_id") == wait_id]
        if len(ends) > 1:
            errors.append("ambiguous_wait_end")
        b = _wait_end(wp, kind, ends)
        span = _span(a, b, kind, errors)
        if span:
            spans.append(span)
            raw_waits[kind] += b - a
        elif b is None:
            open_waits.append({"kind": kind, "id": wait_id, "started_at": a})


def _wait_end(payload, kind, ends):
    if kind == "backoff" and payload.get("eligible_at") is not None:
        return payload["eligible_at"]
    return _stamp(ends[0]) if len(ends) == 1 else None


def _verification_spans(scoped, attempt_ids, payload, stamps, spans, open_waits, errors):
    vstarts = [e for e in scoped if e["kind"] == "lifecycle.verification_started" and
               e["payload"].get("attempt_id") in attempt_ids]
    proof_finish = None
    for start_event in vstarts:
        vid = start_event["payload"].get("verification_id")
        ends = [e for e in scoped if e["kind"] == "lifecycle.verification_finished" and
                e["payload"].get("verification_id") == vid and
                e["payload"].get("attempt_id") == start_event["payload"].get("attempt_id")]
        end_event = ends[0] if len(ends) == 1 else None
        if len(ends) > 1:
            errors.append("ambiguous_verification_finish")
        a, b = _stamp(start_event), _stamp(end_event) if end_event else None
        if vid == payload.get("verification_id"):
            stamps["verification_started_at"] = a
            stamps["verification_finished_at"] = b
            proof_finish = end_event
        span = _span(a, b, "verification", errors)
        if span:
            spans.append(span)
        elif not end_event:
            open_waits.append({"kind": "verification", "id": vid, "started_at": a})
    _check_proof_finish(proof_finish, payload, errors)


def _check_proof_finish(proof_finish, payload, errors):
    if proof_finish is None or proof_finish["payload"].get("result") != "passed":
        errors.append("verified_without_passing_verification")
    elif (proof_finish["payload"].get("proof_ref") != payload.get("proof_ref") or
          proof_finish["payload"].get("exact_head") != payload.get("exact_head")):
        errors.append("proof_mismatch")


def _attempt_span(scoped, selected, revision, stamps, spans, errors):
    aid = selected["payload"]["attempt_id"]
    picked = _stamp(selected)
    queued = _one(scoped, "lifecycle.queued", aid)
    execution = _one(scoped, "lifecycle.execution_started", aid)
    finished = _one(scoped, "lifecycle.attempt_finished", aid)
    if _ambiguous_attempt(scoped, aid):
        errors.append("ambiguous_attempt_transition")
    q, x, f = (_stamp(e) if e else None for e in (queued, execution, finished))
    if stamps.get("selected_at") is None and selected["payload"].get("source_revision") == revision:
        stamps["selected_at"] = picked
    if stamps.get("queued_at") is None and selected["payload"].get("source_revision") == revision:
        stamps["queued_at"] = q
    if stamps.get("execution_started_at") is None and selected["payload"].get("source_revision") == revision:
        stamps["execution_started_at"] = x
    if f is not None:
        stamps["finished_at"] = f
    for a, b, label in ((picked, q, "selection"), (q, x, "queue"),
                        (x, f, "execution_unclassified")):
        span = _span(a, b, label, errors)
        if span:
            spans.append(span)


def _ambiguous_attempt(scoped, aid):
    return any(sum(e["kind"] == kind and e["payload"].get("attempt_id") == aid for e in scoped) > 1
               for kind in ("lifecycle.queued", "lifecycle.execution_started", "lifecycle.attempt_finished"))


def project(events, *, as_of, window_s=WINDOW_S):
    """Return completed and open cohorts plus data quality; never writes to input."""
    clean, errors = _unique(events)
    grouped = defaultdict(list)
    for event in clean:
        if _stamp(event) <= as_of:
            grouped[event["payload"]["work_key"]].append(event)
    completed, invalid_results, backlog = [], [], []
    for key, rows in grouped.items():
        verified = [e for e in rows if e["kind"] == "lifecycle.verified"]
        valid_revisions = set()
        for event in verified:
            row = _work_row(rows, event)
            if not row["errors"]:
                valid_revisions.add(_revision_key(row["source_revision"]))
            if as_of - window_s <= _stamp(event) <= as_of:
                (invalid_results if row["errors"] else completed).append(row)
        recognized = [e for e in rows if e["kind"] == "lifecycle.recognized" and _stamp(e) <= as_of]
        latest = max(recognized, key=_stamp, default=None)
        if latest and _revision_key(latest["payload"].get("source_revision")) not in valid_revisions:
            backlog.append(_backlog_row(key, rows, latest, as_of))
    return dict(completed=completed, invalid_results=invalid_results,
                open_backlog=backlog, data_quality=errors)


def _backlog_row(key, rows, latest, as_of):
    revision = latest["payload"].get("source_revision")
    related = [e for e in rows if _revision_key(e["payload"].get("source_revision")) == _revision_key(revision)]
    attempts = [e for e in related if e["kind"] == "lifecycle.selected"]
    waits = [e for e in rows if e["kind"] == "lifecycle.wait_started" and
             (e["payload"].get("source_revision") == revision or e["payload"].get("attempt_id") is None)]
    actionable = latest["payload"].get("actionable_at")
    base = actionable if actionable is not None else _stamp(latest)
    eligible_at = _eligible_at(waits, as_of)
    pending = _pending_after_eligibility(eligible_at, attempts)
    return dict(work_key=key, source_revision=revision, age_s=as_of - base,
                age_basis="actionable" if actionable is not None else "recognized",
                latest_attempt_id=max(attempts, key=_stamp)["payload"].get("attempt_id") if attempts else None,
                latest_outcome=max((e for e in related if e["kind"] == "lifecycle.attempt_finished"),
                                   key=_stamp, default={"payload": {}})["payload"].get("outcome"),
                open_waits=_open_waits(rows, waits, as_of), eligible_at=eligible_at,
                post_eligibility_age_s=as_of - eligible_at if pending else None)


def _pending_after_eligibility(eligible_at, attempts):
    return eligible_at is not None and not any(_stamp(e) >= eligible_at for e in attempts)


def _eligible_at(waits, as_of):
    return max((e["payload"]["eligible_at"] for e in waits if
                isinstance(e["payload"].get("eligible_at"), (int, float)) and
                e["payload"]["eligible_at"] <= as_of), default=None)


def _open_waits(rows, waits, as_of):
    open_waits = []
    for wait in waits:
        wid = wait["payload"].get("wait_id")
        ended = any(e["kind"] == "lifecycle.wait_ended" and e["payload"].get("wait_id") == wid
                    for e in rows)
        eligible = wait["payload"].get("eligible_at")
        if not ended and not (wait["payload"].get("wait_kind") == "backoff" and
                              eligible is not None and eligible <= as_of):
            open_waits.append({"wait_id": wid, "kind": wait["payload"].get("wait_kind"),
                               "age_s": as_of - _stamp(wait), "eligible_at": eligible})
    return open_waits


def percentile(values, percentage):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(percentage * len(ordered) / 100) - 1)]


def summarize(rows, edge):
    applicable = [r for r in rows if r["completeness"].get(edge) != "not_applicable"]
    values = [r["edges"][edge] for r in applicable if r["edges"].get(edge) is not None]
    missing = defaultdict(int)
    for row in applicable:
        if row["edges"].get(edge) is None:
            missing[row["completeness"][edge]] += 1
    return dict(count=len(values), applicable=len(applicable), completeness=(len(values) / len(applicable) if applicable else None),
                p50=percentile(values, 50), p90=percentile(values, 90),
                p99=percentile(values, 99), worst=max(values, default=None),
                total_s=sum(values), missing=dict(missing))
