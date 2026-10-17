"""Stuck-set records (nexus-office#303): the pile alarm's output, consumed by the intake machine.

The pile alarm (client/pile_alarm.py) no longer files text. Each run hands its stuck set here
as records {repo, issue, filed_at, age_s, state, owner}; the ledger keeps them:

    intake.stuck       an item entered the stuck set (once per entry, not once per run)
    intake.unstuck     it left the set (claimed, received, closed)
    intake.redispatch  its owner was handed a Tower task to move it

An aged record (past the FILED bound, 2 h: no claim on an eligible item) re-dispatches its
owner: one live task per item on the owner's `<id>-work` plan, again at most once per lane
bound (24 h) while it stays stuck; at most MAX_DISPATCH per pass and MAX_LIVE_PER_OWNER live
per owner. An item with no owner plan is recorded and not dispatched. No GitHub write here.

`intake.sweep` calls `redispatch` on every Tower tick; `ingest` calls it after each alarm run.
"""

from __future__ import annotations

import json
import time

from .ledger import loads

FIELDS = ("repo", "issue", "filed_at", "age_s", "state", "owner")
AGED_S = 2 * 3600  # intake.FILED_BOUND_S
REDISPATCH_S = 24 * 3600  # intake.LANE_BOUND_S
MAX_DISPATCH = 3
MAX_LIVE_PER_OWNER = 3  # a 100-item backlog reaches an owner as a few tasks at a time, never a flood


def key(repo, issue):
    return f"github:{repo.lower()}#{int(issue)}"


def current(ledger):
    """{subject: latest record} for every item whose last stuck event is an entry, not an exit."""
    out = {}
    for kind, subject, payload in ledger.conn.execute(
            "SELECT kind, subject, payload FROM events WHERE kind IN ('intake.stuck','intake.unstuck')"
            " ORDER BY id"):
        if kind == "intake.stuck":
            out[subject] = loads(payload, {})
        else:
            out.pop(subject, None)
    return out


def ingest(ledger, records, now=None, dispatch=None):
    """Record the alarm's stuck set as entries/exits, then re-dispatch aged owners."""
    now = time.time() if now is None else now
    seen = {key(r["repo"], r["issue"]): {f: r.get(f) for f in FIELDS} for r in records}
    report = {"entered": 0, "left": 0}
    with ledger.tx():
        held = current(ledger)
        for subject, record in seen.items():
            if subject not in held:
                ledger._event("intake.stuck", subject, record, "pile-alarm", now)
                report["entered"] += 1
        for subject in held.keys() - seen.keys():
            ledger._event("intake.unstuck", subject, {}, "pile-alarm", now)
            report["left"] += 1
    report.update(redispatch(ledger, now, dispatch, ages={s: r.get("age_s") for s, r in seen.items()}))
    return report


def _last_dispatch(ledger, subject):
    row = ledger.conn.execute("SELECT MAX(ts) FROM events WHERE kind='intake.redispatch' AND subject=?",
                              (subject,)).fetchone()
    return row[0] if row else None


def _owner_live(ledger, owner):
    from .ledger import TASK_LIVE
    marks = ",".join("?" * len(TASK_LIVE))
    return ledger.conn.execute(f"SELECT COUNT(*) FROM tasks t JOIN plans p ON p.id=t.plan_id WHERE p.name=?"
                               f" AND t.origin='intake-stuck' AND t.state IN ({marks})",
                               (owner, *TASK_LIVE)).fetchone()[0]


def _tower_task(ledger, record, now):
    plan = ledger.plan_by_name(record["owner"])
    if plan is None:
        return None
    repo, issue = record["repo"], record["issue"]
    dedupe = f"stuck:{key(repo, issue)}"
    live = ledger.live_task_with_key(dedupe)
    if live is not None:  # the owner already holds a live task for this item
        return live["id"]
    return ledger.add_task(
        title=f"Stuck: {repo}#{issue} {record.get('state')} for {int(record['age_s'] // 3600)} h",
        origin="intake-stuck", plan_id=plan["id"], dedupe_key=dedupe,
        unique_live=True, risk="low", now=now,
        reason=json.dumps({f: record.get(f) for f in FIELDS}, sort_keys=True))


def _due(ledger, subject, record, now, ages, check_owner):
    """The record with its current age when its owner is owed a task now, else None (or 'unowned')."""
    entered = ledger.conn.execute("SELECT MAX(ts) FROM events WHERE kind='intake.stuck' AND subject=?",
                                  (subject,)).fetchone()[0]
    record = dict(record, age_s=(ages or {}).get(subject) or (record.get("age_s") or 0) + now - entered)
    if record["age_s"] < AGED_S:
        return None
    if not record.get("owner"):
        return "unowned"
    if check_owner and _owner_live(ledger, record["owner"]) >= MAX_LIVE_PER_OWNER:
        return None
    last = _last_dispatch(ledger, subject)
    return None if last is not None and last >= entered and now - last < REDISPATCH_S else record


def redispatch(ledger, now=None, dispatch=None, ages=None):
    """Hand each aged, owned, held record's owner a task; bounded per pass, per owner and per item."""
    now = time.time() if now is None else now
    dispatch = dispatch or _tower_task
    out = {"redispatched": [], "unowned": 0, "errors": []}
    for subject, held in sorted(current(ledger).items()):
        if len(out["redispatched"]) >= MAX_DISPATCH:
            break
        record = _due(ledger, subject, held, now, ages, dispatch is _tower_task)
        if record == "unowned":
            out["unowned"] += 1
        if not isinstance(record, dict):
            continue
        try:
            task = dispatch(ledger, record, now)
        except Exception as exc:  # noqa: BLE001 - one owner's failure is a report line; next pass retries
            out["errors"].append(f"{subject}: {exc}"[:200])
            continue
        ledger.event("intake.redispatch", subject, {"owner": record["owner"], "task": task,
                                                    "age_s": round(record["age_s"])}, "intake", now)
        out["redispatched"].append({"item": subject, "owner": record["owner"], "task": task})
    return out
