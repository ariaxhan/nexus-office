"""Dispatch records (nexus-office#301): every queue is rows code polls, never prose someone notices.

A record is {record_id, kind, queue, payload, state, created_at, claimed_at, dispatched_to,
receipt, verification, attempts, escalation}. States follow the #289 grammar:

    filed -> claimed -> receipt -> verified -> closed        (escalated: data, one line)

Code owns every arrow. `poll` (from the Tower tick, which runs on jobrun) dispatches a filed
record to its queue's worker; a claimed record past its bound is re-dispatched on age alone,
attempts counted on the row; a receipt is checked on the act's own surface (the record's
`verify` command), never on the worker's word. Exhausting MAX_ATTEMPTS, or a `blocked` receipt,
reaches state=escalated with {act_needed, kind} -- the #300 escalation shape.

The store refuses what it must never hold: triggers in the ledger file reject any transition
off the table, a `receipt` state with no receipt, a `verified` state with no passing
verification, a `closed` state not reached through `verified`, and every delete. A manual
`UPDATE ... SET state='verified'` aborts in sqlite itself, whoever runs it.

The table lives in the ledger file but outside its schema version: it is created on first use,
so no reader of an older nexus dies on a version it does not know.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from . import receipt as receipts
from .ledger import new_id

STATES = ("filed", "claimed", "receipt", "verified", "closed", "escalated")
MAX_ATTEMPTS = 3
FILED_BOUND_S = 600        # a filed record no worker can take
CLAIM_BOUND_S = 1800       # a dispatched worker's window to return a receipt (per-worker `bound_s`)
RECEIPT_BOUND_S = 600      # a receipt whose surface cannot be read
VERIFY_TIMEOUT_S = 60
BROKER_QUEUES = {"broker-studio": "arias-Mac-Studio", "broker-macbook": "arias-MacBook-Pro-2"}
WORKERS = os.environ.get("NEXUS_DISPATCH_WORKERS", os.path.expanduser("~/.nexus/dispatch-workers.json"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (from, to) pairs that exist. Duplicated as SQL below: the trigger is the enforcement.
TRANSITIONS = {
    ("filed", "claimed"), ("claimed", "claimed"), ("claimed", "receipt"),
    ("receipt", "verified"), ("receipt", "filed"), ("verified", "closed"),
    ("filed", "escalated"), ("claimed", "escalated"), ("receipt", "escalated"),
}
_PAIRS = " OR ".join(f"(OLD.state='{a}' AND NEW.state='{b}')" for a, b in sorted(TRANSITIONS))

SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS dispatch_records (
  record_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  queue TEXT NOT NULL,
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  state TEXT NOT NULL CHECK (state IN ({",".join(f"'{s}'" for s in STATES)})),
  created_at REAL NOT NULL,
  state_at REAL NOT NULL,
  claimed_at REAL,
  dispatched_to TEXT,
  receipt TEXT CHECK (receipt IS NULL OR json_valid(receipt)),
  verification TEXT CHECK (verification IS NULL OR json_valid(verification)),
  attempts INTEGER NOT NULL DEFAULT 0,
  escalation TEXT CHECK (escalation IS NULL OR json_valid(escalation))
)""",
    "CREATE INDEX IF NOT EXISTS dispatch_records_state ON dispatch_records(state, queue)",
    """CREATE TRIGGER IF NOT EXISTS dispatch_born_filed BEFORE INSERT ON dispatch_records
WHEN NEW.state != 'filed' OR NEW.attempts != 0 OR NEW.receipt IS NOT NULL
  OR NEW.verification IS NOT NULL OR NEW.escalation IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'a dispatch record is born filed, with nothing done'); END""",
    """CREATE TRIGGER IF NOT EXISTS dispatch_no_delete BEFORE DELETE ON dispatch_records
BEGIN SELECT RAISE(ABORT, 'dispatch records are history'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS dispatch_transition BEFORE UPDATE ON dispatch_records
WHEN NOT ({_PAIRS})
BEGIN SELECT RAISE(ABORT, 'no such dispatch transition'); END""",
    """CREATE TRIGGER IF NOT EXISTS dispatch_receipt_required BEFORE UPDATE ON dispatch_records
WHEN NEW.state IN ('receipt','verified','closed') AND (NEW.receipt IS NULL
  OR json_extract(NEW.receipt, '$.outcome') IS NULL OR json_extract(NEW.receipt, '$.verification') IS NULL)
BEGIN SELECT RAISE(ABORT, 'no state past claimed without a receipt'); END""",
    """CREATE TRIGGER IF NOT EXISTS dispatch_verification_required BEFORE UPDATE ON dispatch_records
WHEN NEW.state IN ('verified','closed') AND (NEW.verification IS NULL
  OR json_extract(NEW.verification, '$.result') != 'pass'
  OR json_extract(NEW.receipt, '$.outcome') != 'done')
BEGIN SELECT RAISE(ABORT, 'verified needs a done receipt and a passing read of the surface'); END""",
    """CREATE TRIGGER IF NOT EXISTS dispatch_escalation_required BEFORE UPDATE ON dispatch_records
WHEN NEW.state = 'escalated' AND (NEW.escalation IS NULL
  OR coalesce(json_extract(NEW.escalation, '$.act_needed'), '') = '')
BEGIN SELECT RAISE(ABORT, 'escalated needs the one act needed'); END""",
    """CREATE TRIGGER IF NOT EXISTS dispatch_attempts_monotonic BEFORE UPDATE ON dispatch_records
WHEN NEW.attempts < OLD.attempts OR NEW.record_id != OLD.record_id OR NEW.queue != OLD.queue
  OR NEW.payload != OLD.payload OR NEW.created_at != OLD.created_at
BEGIN SELECT RAISE(ABORT, 'a record''s identity and attempt count are history'); END""",
]


class DispatchError(ValueError):
    pass


def ensure(led):
    for statement in SCHEMA:
        led.conn.execute(statement)


def _row(row):
    if row is None:
        return None
    out = dict(row)
    for key in ("payload", "receipt", "verification", "escalation"):
        out[key] = json.loads(out[key]) if out[key] else None
    return out


def get(led, record_id):
    ensure(led)
    return _row(led.conn.execute("SELECT * FROM dispatch_records WHERE record_id=?", (record_id,)).fetchone())


def records(led, states=None, queue=None):
    ensure(led)
    sql, args = "SELECT * FROM dispatch_records WHERE 1=1", []
    if states:
        sql += f" AND state IN ({','.join('?' * len(states))})"
        args += list(states)
    if queue:
        sql += " AND queue=?"
        args.append(queue)
    return [_row(r) for r in led.conn.execute(sql + " ORDER BY created_at, record_id", args)]


def file(led, queue, kind, payload, now=None):
    """Write one record, state filed. The only way work enters a queue."""
    if not isinstance(payload, dict):
        raise DispatchError("payload must be a mapping")
    if not queue or not kind:
        raise DispatchError("queue and kind are required")
    ensure(led)
    now = time.time() if now is None else now
    record_id = new_id("dsp")
    with led.tx() as c:
        c.execute("INSERT INTO dispatch_records (record_id, kind, queue, payload, state, created_at, state_at)"
                  " VALUES (?,?,?,?,?,?,?)", (record_id, kind, queue, json.dumps(payload, sort_keys=True),
                                               "filed", now, now))
        led._event("dispatch.state", record_id, {"from": None, "to": "filed", "queue": queue}, "dispatch", now)
    return record_id


def _move(led, record, to, now, source="dispatch", **fields):
    """Compare-and-set one transition, with its event. False if the row moved under us."""
    sets = {"state": to, "state_at": now, **fields}
    for key in ("receipt", "verification", "escalation"):
        if key in sets and sets[key] is not None:
            sets[key] = json.dumps(sets[key], sort_keys=True)
    cols = ", ".join(f"{k}=?" for k in sets)
    with led.tx() as c:
        cur = c.execute(f"UPDATE dispatch_records SET {cols} WHERE record_id=? AND state=? AND attempts=?",
                        (*sets.values(), record["record_id"], record["state"], record["attempts"]))
        if cur.rowcount != 1:
            return False
        detail = {k: v for k, v in fields.items() if k in ("attempts", "dispatched_to", "escalation", "verification")}
        led._event("dispatch.state", record["record_id"], {"from": record["state"], "to": to, **detail}, source, now)
    return True


def submit_receipt(led, record_id, receipt, now=None):
    """The worker's one write: a grammar-valid receipt (#291) moves claimed -> receipt."""
    canonical = receipts.validate(receipt)
    record = get(led, record_id)
    if record is None:
        raise DispatchError(f"no record {record_id}")
    if record["state"] != "claimed":
        raise DispatchError(f"{record_id} is {record['state']}; a receipt answers a claimed record")
    now = time.time() if now is None else now
    if not _move(led, record, "receipt", now, source="worker", receipt=canonical):
        raise DispatchError(f"{record_id} moved while the receipt was written")
    return canonical


# ---- workers -------------------------------------------------------------


def holder_prompt(record):
    p = record["payload"]
    receipt_cmd = (f"cd {ROOT} && {sys.executable} -m nexus dispatch receipt {record['record_id']} "
                   "--outcome done|failed|blocked --artifact '<pointer>' --verification '<surface>' "
                   "--evidence '<bounded fact>' --next 'none|<owner>: <what>'")
    return (f"You are the control holder for queue {record['queue']} (dispatch record {record['record_id']}, "
            f"attempt {record['attempts']}). Open ONE computer-control session for this device, perform the ACT "
            "exactly as written, improvising nothing, then end the session.\n\n"
            f"REQUESTER: {p.get('requester')}\nACT: {p.get('act')}\nSHELL CHECK: {p.get('shell_check')}\n"
            f"COMPLETION SURFACE: {p.get('surface')}\n\n"
            "If the act turns out to be shell-able, do not execute it over control: return outcome blocked with "
            "next naming the shell route. Your only report is the receipt, written with:\n" + receipt_cmd +
            "\nNo chat message, no issue comment. Code reads the surface after your receipt; your word is not "
            "the verification.")


def default_workers(host):
    """The broker holder lane for the queue whose device this host is. Pure."""
    router = os.path.expanduser("~/Developer/Vaults/_meta/services/tbs/account-router.sh")
    return {queue: {"argv": [router, "run", "code-judgment", "--", "-p", "{prompt}",
                             "--dangerously-skip-permissions"], "bound_s": CLAIM_BOUND_S}
            for queue, owner in BROKER_QUEUES.items() if owner == host}


def load_workers(path=None, host=None):
    from .executor import local_host_name
    workers = default_workers(host if host is not None else local_host_name())
    path = path or WORKERS
    if os.path.exists(path):
        with open(path) as fh:
            workers.update(json.load(fh))
    return workers


def spawn(argv, env, log):
    """Launch the worker detached; it outlives the tick and reports by receipt alone."""
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "ab") as out:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env,
                                cwd=ROOT, start_new_session=True)
    return proc.pid


def _dispatch(led, record, worker, now, launch):
    attempts = record["attempts"] + 1
    if not _move(led, record, "claimed", now, attempts=attempts, claimed_at=now, dispatched_to=None):
        return None
    record = get(led, record["record_id"])
    argv = [a.replace("{prompt}", holder_prompt(record)).replace("{record_id}", record["record_id"])
            for a in worker["argv"]]
    env = dict(os.environ, NEXUS_DISPATCH_RECORD=json.dumps(record, sort_keys=True),
               NEXUS_DISPATCH_RECORD_ID=record["record_id"], PYTHONPATH=ROOT)
    log = os.path.join(os.path.dirname(os.path.abspath(led.path)), "dispatch-logs", f"{record['record_id']}.log")
    try:
        target = f"{worker.get('name') or record['queue']}:pid={launch(argv, env, log)}"
    except (OSError, subprocess.SubprocessError) as exc:
        target = f"launch-failed:{type(exc).__name__}"  # stays claimed; age re-dispatches it
    _move(led, record, "claimed", now, dispatched_to=target)
    return target


def _escalate(led, record, now, act, kind):
    return _move(led, record, "escalated", now, escalation={"act_needed": " ".join(act.split())[:300], "kind": kind})


def _verify(record, run):
    argv = record["payload"].get("verify")
    out = run(argv, capture_output=True, text=True, timeout=VERIFY_TIMEOUT_S)
    return {"surface": " ".join(argv), "result": "pass" if out.returncode == 0 else "fail",
            "rc": out.returncode, "said": ((out.stdout or "") + (out.stderr or ""))[-300:], "at": time.time()}


def poll(led, now=None, workers=None, launch=spawn, run=subprocess.run):
    """One pass: every open record advanced by code, or left exactly where its bound allows."""
    ensure(led)
    now = time.time() if now is None else now
    report = {"dispatched": 0, "redispatched": 0, "verified": 0, "closed": 0, "refiled": 0, "escalated": 0}
    open_records = records(led, states=("filed", "claimed", "receipt", "verified"))
    if not open_records:
        return report
    workers = load_workers() if workers is None else workers
    for record in open_records:
        worker = workers.get(record["queue"])
        age = now - record["state_at"]
        state = record["state"]
        if state == "filed":
            if record["attempts"] >= MAX_ATTEMPTS:
                report["escalated"] += _escalate(led, record, now, _act(record, "no verified completion"), "hands")
            elif worker:
                report["dispatched"] += _dispatch(led, record, worker, now, launch) is not None
            elif age > FILED_BOUND_S:
                report["escalated"] += _escalate(
                    led, record, now, f"register a worker for queue {record['queue']} on its device", "config")
        elif state == "claimed":
            bound = float((worker or {}).get("bound_s", CLAIM_BOUND_S))
            if now - (record["claimed_at"] or record["state_at"]) <= bound:
                continue
            if record["attempts"] >= MAX_ATTEMPTS or not worker:
                report["escalated"] += _escalate(led, record, now, _act(record, "no receipt"), "hands")
            else:
                report["redispatched"] += _dispatch(led, record, worker, now, launch) is not None
        elif state == "receipt":
            outcome = record["receipt"]["outcome"]
            if outcome == "blocked":
                nxt = record["receipt"]["next"]
                report["escalated"] += _escalate(led, record, now, nxt if nxt != "none" else _act(record, "blocked"),
                                                 "decision")
                continue
            if outcome != "done":
                report["refiled"] += _move(led, record, "filed", now, receipt=None)
                continue
            try:
                verification = _verify(record, run)
            except (OSError, subprocess.SubprocessError, TypeError) as exc:
                if age > RECEIPT_BOUND_S:
                    report["refiled"] += _move(led, record, "filed", now, receipt=None)
                led.event("dispatch.unverifiable", record["record_id"], {"error": repr(exc)[-300:]}, "dispatch")
                continue
            if verification["result"] == "pass":
                if _move(led, record, "verified", now, verification=verification):
                    report["verified"] += 1
                    record = get(led, record["record_id"])
                    report["closed"] += _move(led, record, "closed", now)
            else:
                led.event("dispatch.verification_failed", record["record_id"], verification, "dispatch")
                report["refiled"] += _move(led, record, "filed", now, receipt=None)
        elif state == "verified":
            report["closed"] += _move(led, record, "closed", now)
    return report


def _act(record, why):
    p = record["payload"]
    act = p.get("act") or record["kind"]
    return f"{act} -- by hand on {record['queue']} ({why} after {record['attempts']} dispatches)"


# ---- the broker adapter (#263 / #264) -------------------------------------


def file_control_request(led, queue, requester, act, shell_check, surface, verify, now=None):
    """A control request as a record. `verify` is the argv code runs to read the act's surface."""
    if queue not in BROKER_QUEUES:
        raise DispatchError(f"queue must be one of {', '.join(BROKER_QUEUES)}")
    fields = {"requester": requester, "act": act, "shell_check": shell_check, "surface": surface}
    empty = [k for k, v in fields.items() if not (isinstance(v, str) and v.strip())]
    if empty:
        raise DispatchError(f"required: {', '.join(empty)}")
    if not (isinstance(verify, list) and verify and all(isinstance(a, str) and a for a in verify)):
        raise DispatchError("verify must be a non-empty argv: the command that reads the completion surface")
    return file(led, queue, "control-request", {**fields, "verify": verify}, now=now)
