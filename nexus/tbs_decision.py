"""The one TBS judgment that crosses into Tower: `tbs.coordinator-decision/v1` (consumer side).

TBS owns the snapshot, the policy and the verifiers; this file owns only the boundary. A decision is
parsed strictly and either becomes one ordinary candidate task (Tower then does acceptance, flights,
leases, retry and terminal proof unchanged) or is recorded as a `tbs.decision` event and nothing else.
A decision can never declare success, pick a retry count or touch lifecycle state.

Canonical fixture (the TBS producer tests read it from here): tests/fixtures/tbs/coordinator-decision.v1.json
Handoff: thinking-brain-school findings/tbs-nexus-central-worker-handoff.md
"""

from __future__ import annotations

import re

SCHEMA = "tbs.coordinator-decision/v1"
DECISIONS = ("execute", "wait", "escalate", "idle")
CLASSES = ("maintenance", "defect", "audit", "care", "release")  # lessons and literature stay out
RISKS = ("low", "medium", "high", "sensitive")
TOP = {"schema", "snapshot_id", "decided_at", "decision", "outcome", "wait", "escalation"}
OUTCOME = {"key", "class", "title", "reason", "impact", "risk", "objective", "output", "check",
           "resources", "executor", "authority"}
REQUIRED = OUTCOME - {"reason", "impact"}
WAIT = {"reason", "wake_at", "wake_on", "evidence"}
ESCALATION = {"question", "options", "evidence"}
AUTHORITY = {"mode", "source"}
SNAPSHOT_ID = re.compile(r"sha256:[0-9a-f]{64}")
KEY = re.compile(r"[a-z][a-z0-9_-]*(:[A-Za-z0-9._-]+){1,6}")
TEXT, ITEMS = 600, 8


class Refused(ValueError):
    """A decision Tower will not act on. The reason is recorded; no task exists."""


def _text(value, name, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > TEXT:
        raise Refused(f"{name}: a nonempty string of at most {TEXT} characters")
    return value


def _only(obj, allowed, name):
    if not isinstance(obj, dict):
        raise Refused(f"{name}: must be an object")
    unknown = set(obj) - allowed
    if unknown:
        raise Refused(f"{name}: unknown field(s) {sorted(unknown)}")
    return obj


def _list(value, name, required=True):
    if value is None and not required:
        return []
    if (not isinstance(value, list) or (required and not value) or len(value) > ITEMS
            or not all(isinstance(v, str) and v and len(v) <= TEXT for v in value)):
        raise Refused(f"{name}: a list of 1..{ITEMS} nonempty strings")
    return value


def parse(decision, snapshot_id):
    """The validated decision, or Refused. `snapshot_id` is the hash of the packet the model saw."""
    d = _only(decision, TOP, "decision")
    if d.get("schema") != SCHEMA:
        raise Refused(f"schema: expected {SCHEMA}")
    if not SNAPSHOT_ID.fullmatch(str(d.get("snapshot_id", ""))) or d["snapshot_id"] != snapshot_id:
        raise Refused("snapshot_id: does not match the packet this decision was made from")
    _text(d.get("decided_at"), "decided_at")
    kind = d.get("decision")
    if kind not in DECISIONS:
        raise Refused(f"decision: one of {DECISIONS}")
    present = {k for k in ("outcome", "wait", "escalation") if d.get(k) is not None}
    expected = {"execute": {"outcome"}, "wait": {"wait"}, "escalate": {"escalation"}, "idle": set()}[kind]
    if present != expected:
        raise Refused(f"decision {kind}: carries exactly {sorted(expected) or 'no payload'}, got {sorted(present)}")
    if kind == "execute":
        o = _only(d["outcome"], OUTCOME, "outcome")
        missing = REQUIRED - {k for k, v in o.items() if v not in (None, "", [])}
        if missing:
            raise Refused(f"outcome: missing {sorted(missing)}")
        if not KEY.fullmatch(str(o["key"])) or len(o["key"]) > 160:
            raise Refused("outcome.key: a stable namespaced key like care:thread:<id>:answer")
        if o["class"] not in CLASSES:
            raise Refused(f"outcome.class: one of {CLASSES}")
        if o["risk"] not in RISKS:
            raise Refused(f"outcome.risk: one of {RISKS}")
        for f in ("title", "objective", "output", "check", "executor"):
            _text(o[f], f"outcome.{f}")
        for f in ("reason", "impact"):
            _text(o.get(f), f"outcome.{f}", required=False)
        _list(o["resources"], "outcome.resources")
        a = _only(o["authority"], AUTHORITY, "outcome.authority")
        _text(a.get("mode"), "outcome.authority.mode")
        _text(a.get("source"), "outcome.authority.source")
    elif kind == "wait":
        w = _only(d["wait"], WAIT, "wait")
        _text(w.get("reason"), "wait.reason")
        if not (w.get("wake_at") or w.get("wake_on")):
            raise Refused("wait: needs wake_at or wake_on")
        _text(w.get("wake_at"), "wait.wake_at", required=False)
        _text(w.get("wake_on"), "wait.wake_on", required=False)
        _list(w.get("evidence"), "wait.evidence")
    elif kind == "escalate":
        e = _only(d["escalation"], ESCALATION, "escalation")
        _text(e.get("question"), "escalation.question")
        _list(e.get("options"), "escalation.options")
        _list(e.get("evidence"), "escalation.evidence", required=False)
    return d


def accept(led, decision, snapshot_id, plan_id=None, now=None):
    """{"decision", "task", "duplicate_of", "refused"}. At most one live task per outcome key, ever."""
    try:
        d = parse(decision, snapshot_id)
    except Refused as exc:
        led.event("tbs.decision", plan_id, {"refused": str(exc), "snapshot_id": snapshot_id}, "tbs", now)
        return {"decision": None, "task": None, "duplicate_of": None, "refused": str(exc)}
    kind, task = d["decision"], None
    duplicate = None
    if kind == "execute":
        o = d["outcome"]
        key = "tbs:" + o["key"]
        task = led.add_task(o["title"], "tbs", plan_id=plan_id, reason=o.get("reason"), impact=o.get("impact"),
                            risk=o["risk"], dedupe_key=key, now=now, objective=o["objective"],
                            autonomy=o["authority"]["mode"], output=o["output"], check=o["check"],
                            unique_live=True)
        if task is None:
            duplicate = led.live_task_with_key(key)["id"]
    led.event("tbs.decision", task or plan_id,
              {"decision": d, "snapshot_id": snapshot_id, "duplicate_of": duplicate}, "tbs", now)
    return {"decision": kind, "task": task, "duplicate_of": duplicate, "refused": None}
