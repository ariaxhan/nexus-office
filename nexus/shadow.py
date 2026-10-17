"""Dual-write to the Cloudflare shadow of the record layer (nexus-office#310).

The Studio ledger is authoritative in every conflict. This module only copies: every fault
record (#300) and dispatch record (#301) revision is sent to `nexus-shadow` (D1 + Queue + R2),
which judges it with its own rules and keeps parity evidence. Nobody reads the shadow
operationally, and nothing it says ever comes back.

The producer is the ledger itself: both record kinds are pure functions of ledger events
(`repair.*`/`watchdog.recovered` for faults, `dispatch.state` for dispatch), so a cursor over
event ids catches every producer, including the ones that land later, without a hook in each.

Shared event schema, pinned (nexus.shadow.record/v1):

    {schema, namespace: office.dispatch | office.fault, record_id, revision, event_id,
     record_json, sha256, transitions: [{seq, at, from, to, attempts?, studio_outcome}],
     origin, observed_at}

    revision = sha256(record_json + canonical(transitions))[:16]
    event_id = "<namespace>:<record_id>:<revision>"   (content addressed: a resend is a no-op)
    seq      = the Studio ledger event id of the step

`record_json` is the record exactly as the Studio serialises it, so D1 holds it byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request

from . import faults
from .ledger import loads

SCHEMA = "nexus.shadow.record/v1"
URL = os.environ.get("NEXUS_SHADOW_URL", "https://nexus-shadow.ariaxhan.workers.dev")
TOKEN_FILE = os.environ.get("NEXUS_SHADOW_TOKEN_FILE", os.path.expanduser("~/.nexus/shadow-token"))
#: The one ledger the Tower dual-writes from. Test and sandbox ledgers never reach the shadow.
STUDIO_LEDGER = os.path.expanduser("~/Library/Application Support/nexus/ledger.sqlite")
FAULT_KINDS = ("repair.opened", "repair.fixed", "repair.awaiting_proof", "repair.dispatched",
               "repair.attempt_failed", "repair.completed", "repair.stood_down", "repair.escalated",
               "watchdog.recovered")
BATCH = 100
MAX_EVENTS = 2000  # per push; the cursor resumes where it stopped
TIMEOUT_S = 10
UA = "nexus-office-shadow/1"  # Cloudflare refuses urllib's default agent (403, error 1010)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def studio_outcome(payload):
    """What the Studio did at one dispatch.state step, named in the shadow's outcome vocabulary."""
    src, dst = payload.get("from"), payload.get("to")
    if src is None:
        return "filed"
    if dst == "escalated":
        return "escalate:" + ((payload.get("escalation") or {}).get("kind") or "hands")
    if src == "filed" and dst == "claimed":
        return "claim_owner" if str(payload.get("dispatched_to") or "").startswith("owner:") else "dispatch"
    if src == "claimed" and dst == "claimed":
        return "redispatch" if "attempts" in payload else "launched"
    return {("claimed", "receipt"): "receipt", ("receipt", "verified"): "verify_pass",
            ("receipt", "filed"): "refile", ("verified", "closed"): "close"}.get((src, dst), f"{src}>{dst}")


def envelope(namespace, record_id, record, transitions, origin, now):
    record_json = canonical(record)
    revision = hashlib.sha256((record_json + canonical(transitions)).encode()).hexdigest()[:16]
    return {"schema": SCHEMA, "namespace": namespace, "record_id": record_id, "revision": revision,
            "event_id": f"{namespace}:{record_id}:{revision}", "record_json": record_json,
            "sha256": hashlib.sha256(record_json.encode()).hexdigest(), "transitions": transitions,
            "origin": origin, "observed_at": now}


def _dispatch_record(ledger, record_id):
    if not ledger.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dispatch_records'").fetchone():
        return None
    from . import dispatch  # #301; absent ledgers simply have no dispatch records
    return dispatch.get(ledger, record_id)


def collect(ledger, after_id, now=None, origin=None, limit=MAX_EVENTS):
    """(envelopes, last event id seen): the current revision of every record touched after the cursor."""
    now = time.time() if now is None else now
    origin = origin or socket.gethostname().split(".")[0]
    rows = ledger.conn.execute(
        f"SELECT id, kind, subject, payload FROM events WHERE id > ? AND kind IN"
        f" ({','.join('?' * (len(FAULT_KINDS) + 1))}) ORDER BY id LIMIT ?",
        (after_id, "dispatch.state", *FAULT_KINDS, limit)).fetchall()
    last = rows[-1]["id"] if rows else after_id
    out = []
    for ns, subject in _touched(rows):
        record, steps = _revision(ledger, ns, subject)
        if record is not None:
            out.append(envelope(ns, subject, record, steps, origin, now))
    return out, last


def _touched(rows):
    """(namespace, record id) of each record the rows moved, first-touched order, once each."""
    touched = {}
    for row in rows:
        ns = "office.dispatch" if row["kind"] == "dispatch.state" else "office.fault"
        subject = row["subject"]
        if ns == "office.fault" and row["kind"] == "repair.opened":
            subject = (loads(row["payload"], {}) or {}).get("task_id") or subject
        if subject:
            touched[(ns, subject)] = True
    return list(touched)


def _revision(ledger, ns, subject):
    """(record, transitions) as the Studio holds them now, or (None, None)."""
    if ns == "office.dispatch":
        record = _dispatch_record(ledger, subject)
        if record is None:
            return None, None
        return record, [dict(_step(e), seq=e["id"], at=e["ts"])
                        for e in ledger.events(kind="dispatch.state", subject=subject)]
    record = faults.record(ledger, subject)
    if record is None:
        return None, None
    seq = max(e["id"] for e in ledger.events(subject=subject))
    return record, [{"seq": seq, "to": record["state"], "studio_outcome": "allow"}]


def _step(event):
    payload = loads(event["payload"], {}) or {}
    step = {"from": payload.get("from"), "to": payload.get("to"), "studio_outcome": studio_outcome(payload)}
    if "attempts" in payload:
        step["attempts"] = payload["attempts"]
    return step


def _cursor_path(ledger):
    return os.path.join(os.path.dirname(os.path.abspath(ledger.path)), "shadow-cursor.json")


def _token():
    try:
        with open(TOKEN_FILE) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def post(records, token, url=URL):
    body = json.dumps({"records": records}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/ingest", data=body, method="POST", headers={
        "authorization": f"Bearer {token}", "content-type": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 400:  # every envelope refused: report them, never wedge the cursor on them
            return json.loads(exc.read() or b"{}")
        raise


def push(ledger, now=None, send=post, token=None, from_id=None):
    """Send every record revision since the cursor. The cursor moves only past what landed.

    A first push starts at the ledger's newest event: the shadow copies records from their
    creation on, it does not backfill history (pass from_id=0 for that, by hand).
    """
    token = token or _token()
    if not token:
        return {"skipped": "no shadow token"}
    path = _cursor_path(ledger)
    try:
        with open(path) as fh:
            after = int(json.load(fh)["after_id"])
    except (OSError, ValueError, KeyError):
        after = None
    if from_id is not None:
        after = from_id
    if after is None:
        newest = ledger.conn.execute("SELECT coalesce(max(id), 0) FROM events").fetchone()[0]
        _save(path, newest)
        return {"started_at": newest, "sent": 0}
    envelopes, last = collect(ledger, after, now=now)
    report = {"sent": 0, "fresh": 0, "refused": []}
    for i in range(0, len(envelopes), BATCH):
        res = send(envelopes[i:i + BATCH], token)
        report["sent"] += len(res.get("accepted", []))
        report["fresh"] += sum(1 for a in res.get("accepted", []) if a.get("fresh"))
        report["refused"] += res.get("refused", [])
    _save(path, last)
    report["after_id"] = last
    return report


def _save(path, after_id):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"after_id": after_id}, fh)
    os.replace(tmp, path)


def tick(ledger, now=None):
    """The Tower's call: the Studio ledger only, and never an error that stops the fleet."""
    if os.path.abspath(ledger.path) != STUDIO_LEDGER:
        return None
    try:
        return push(ledger, now=now)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"error": repr(exc)[-200:]}


def readout(token=None, url=URL, record_id=None):
    token = token or _token()
    q = f"?record_id={record_id}" if record_id else ""
    req = urllib.request.Request(url.rstrip("/") + "/parity" + q, headers={"authorization": f"Bearer {token}", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        return json.loads(res.read())
