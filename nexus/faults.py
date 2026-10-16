"""Faults as structured machine state (issue #300, Aria 2026-10-15).

"If we see something broken, we fix it. We don't need to talk about it."

The repair record is the ONLY fault artifact. It is never written as prose
and never parsed out of prose: it is a pure function of the ledger's
repair.* events, the log the self-repair loop already keeps. One record per
fault subject (a self-repair task id, a plan id for a stopped mechanism, or
a watchdog's own fault id):

    {fault_id, signature, detected_at, source, state,
     repair: {action, at, by} | null,
     verification: {surface, result, at} | null,
     escalation: {act_needed, kind} | null}

States: detected -> repairing -> verified_fixed | escalated. Escalated is a
state, never a message: only a failed repair that needs a person's hands or
a real decision gets one, with ONE plain line naming the act. Every other
fault is silent; silence is the success signal.

Watchdogs outside the tower emit and advance records through `report()`
(`nexus faults report ...`), so their faults land in the same store.
"""

from __future__ import annotations

import json
import time
import uuid

from .ledger import loads

STATES = ("detected", "repairing", "verified_fixed", "escalated")
ESCALATION_KINDS = ("hands", "decision", "money", "irreversible", "identity")
SOURCE = "self-repair"

_REPAIR_KINDS = ("repair.fixed", "repair.awaiting_proof", "repair.dispatched")


def _action(kind, payload):
    if payload.get("action"):
        return payload["action"]
    if payload.get("corrections"):
        return "repoint " + ", ".join(f"{old} -> {new}" for old, new
                                      in sorted(payload["corrections"].items()))
    if payload.get("cleared"):
        return "clear stale state"
    if kind == "repair.awaiting_proof":
        return "re-run the worker"
    if kind == "repair.dispatched":
        return "fly the repair drain"
    return kind.split(".", 1)[1]


def record(ledger, fault_id):
    """The record for one fault, or None when the subject never faulted."""
    events = ledger.events(subject=fault_id)
    opened = [e for e in events if e["kind"] == "repair.opened"] \
        or [e for e in ledger.events(kind="repair.opened")  # pre-#300: keyed on the plan
            if (loads(e["payload"], {}) or {}).get("task_id") == fault_id]
    if not opened:
        return None
    first = loads(opened[0]["payload"], {}) or {}
    out = {"fault_id": fault_id, "signature": first.get("signature"),
           "detected_at": opened[0]["ts"], "source": first.get("source") or opened[0]["source"],
           "state": "detected", "repair": None, "verification": None, "escalation": None}
    for event in events:
        payload = loads(event["payload"], {}) or {}
        kind = event["kind"]
        if kind in _REPAIR_KINDS:
            if kind == "repair.awaiting_proof" and out["repair"] \
                    and out["repair"]["at"] == event["ts"]:
                continue  # the proof belongs to the fix just recorded
            out["repair"] ={"action": _action(kind, payload), "at": event["ts"],
                             "by": payload.get("by") or event["source"]}
            out["state"] = "repairing"
        elif kind == "repair.attempt_failed":
            out["verification"] = {"surface": payload.get("surface") or "proof run",
                                   "result": "fail", "at": event["ts"]}
            out["state"] = "repairing"
        elif kind in ("repair.completed", "repair.stood_down"):
            surface = (payload.get("surface") or payload.get("receipt")
                       or (f"flight {payload['receipt_flight']}"
                           if payload.get("receipt_flight") else None)
                       or ("plan released by a person" if kind == "repair.stood_down"
                           else "proof run"))
            out["verification"] = {"surface": surface,
                                   "result": "pass" if kind == "repair.completed" else "released",
                                   "at": event["ts"]}
            out["state"] = "verified_fixed"
        elif kind == "repair.escalated" and payload.get("act_needed"):
            # pre-#300 escalations (prose on an issue) carry no act and are
            # not records; only the structured state counts
            out["escalation"] = {"act_needed": payload["act_needed"],
                                 "kind": payload.get("kind") or "hands"}
            out["state"] = "escalated"
    return out


def records(ledger, state=None, since=None):
    """Every fault record, oldest first; optionally one state only."""
    seen, out = set(), []
    for event in ledger.events(kind="repair.opened"):
        subject = (loads(event["payload"], {}) or {}).get("task_id") or event["subject"]
        if subject in seen or (since is not None and event["ts"] < since):
            continue
        seen.add(subject)
        rec = record(ledger, subject)
        if rec is not None and (state is None or rec["state"] == state):
            out.append(rec)
    return out


def escalated(ledger):
    """What main consumes: the faults that need a person's act. Nothing else."""
    return records(ledger, state="escalated")


# ---- writers ---------------------------------------------------------------

def detect(ledger, signature, source, fault_id=None, now=None):
    """Open a record. Idempotent per fault_id: a re-detection is the same fault."""
    now = now if now is not None else time.time()
    fault_id = fault_id or f"fault_{uuid.uuid4().hex[:12]}"
    if not ledger.events(kind="repair.opened", subject=fault_id):
        ledger.event("repair.opened", fault_id,
                     {"signature": signature, "source": source}, source, now)
    return fault_id


def escalate(ledger, fault_id, act_needed, kind="hands", source=SOURCE, now=None):
    """The one escalation write: a state on the record, never a message."""
    act = " ".join(str(act_needed).split())
    if not act:
        raise ValueError("an escalation names the act")
    if kind not in ESCALATION_KINDS:
        raise ValueError(f"escalation kind must be one of {ESCALATION_KINDS}")
    now = now if now is not None else time.time()
    ledger.event("repair.escalated", fault_id,
                 {"tier": "office", "act_needed": act[:200], "kind": kind},
                 source, now)


def report(ledger, signature, source, state, fault_id=None, action=None,
           surface=None, act_needed=None, kind="hands", now=None):
    """A watchdog's whole interface: name the fault and where it is now.

    detected     -> opens the record
    repairing    -> records the repair action taken
    verified_fixed -> records the live surface the fix was verified on
    escalated    -> repair failed and a person must act: act_needed, one line
    """
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    now = now if now is not None else time.time()
    fault_id = detect(ledger, signature, source, fault_id=fault_id, now=now)
    if state == "repairing":
        ledger.event("repair.fixed", fault_id,
                     {"action": action or "repair", "by": source}, source, now)
    elif state == "verified_fixed":
        if not surface:
            raise ValueError("verified_fixed names the live surface it was verified on")
        if action:
            ledger.event("repair.fixed", fault_id,
                         {"action": action, "by": source}, source, now)
        ledger.event("repair.completed", fault_id, {"surface": surface}, source, now)
    elif state == "escalated":
        escalate(ledger, fault_id, act_needed or "", kind=kind, source=source, now=now)
    return record(ledger, fault_id)


def dumps(recs):
    return json.dumps(recs, indent=1, sort_keys=True)
