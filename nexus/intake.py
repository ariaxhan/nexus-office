"""The intake state machine (nexus-office#289): FILED -> CLAIMED -> RECEIPT -> VERIFIED -> CLOSED.

Not a tracker. Every state is read off records that already exist, per the canonical
pair (issues = truth for work, the ledger = truth for what happened):

    FILED     the item's task generation, from `work.capture`; its queue time is read off the
              recorded `work.issue` revisions, so a label change never rewrites history
    CLAIMED   the first claim on either surface the Watch classifier reads: a Tower flight
              (tower_activity) or a coordinator receipt (nexus/coordinator.py, `intake.claim`)
    RECEIPT   `work.receipt` -- the receipt the verifier accepted, never the lane's word
    VERIFIED  the verifier's read: a passed `lifecycle.verification_finished`, else `lifecycle.verified`
    CLOSED    `work.closed`, or GitHub reporting the issue closed

Every evidenced transition is recorded once as `intake.state`, including ones that happened
between sweeps (settlement writes receipt, close and verified in one execution).

Bounds: FILED 2 h of accumulated time eligible as a `ready` queue item, CLAIMED the lane's bound (24 h unless the issue body
sets `Lane bound: N h`), RECEIPT 1 h, VERIFIED none (the verification act closes). A state
that outlives its bound -- still sitting in it, or left late -- raises one fault ON THE
MECHANISM: an issue filed on the item's own repo, deduped per (generation, state) in the
ledger. Never a chat note, never a report line.

`sweep` runs from the Tower tick, throttled; `nexus intake` runs the same sweep by hand.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import time

from . import coordinator, flights, work
from .ledger import loads

STATES = ("FILED", "CLAIMED", "RECEIPT", "VERIFIED", "CLOSED")
FILED_BOUND_S = 2 * 3600
LANE_BOUND_S = 24 * 3600
RECEIPT_BOUND_S = 3600
#: activation: generations recognized before this predate the machine and are never judged
#: by it (the ledger holds hundreds of years-old accepted tasks; judging them would be a storm).
SINCE = datetime.datetime(2026, 10, 15, 19, 0, tzinfo=datetime.timezone.utc).timestamp()
#: a repo no longer discovered leaves its tasks frozen; a frozen snapshot is not an open item.
OBSERVED_WITHIN_S = 6 * 3600
SWEEP_EVERY_S = 60
MAX_FILINGS = 3  # per sweep: a backlog becomes faults over minutes, never a burst
FILING_LEASE_S = 300  # one caller owns a fault identity while it files; a dead or late owner frees it
FAULT_PREFIX = "[intake-fault]"
_BOUND = re.compile(r"(?im)^\W*lane bound\W*:?\W*(\d+(?:\.\d+)?)\s*(m|min|h|hours?|d|days?)\b")


def lane_bound(body):
    """The commission's own CLAIMED -> RECEIPT bound, or the 24 h default."""
    match = _BOUND.search(body or "")
    if not match:
        return LANE_BOUND_S
    unit = match.group(2)[0].lower()
    return float(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[unit]


def _parse(stamp):
    try:
        return datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _first(ledger, kind, subject):
    row = ledger.conn.execute("SELECT ts FROM events WHERE kind=? AND subject=? ORDER BY id LIMIT 1",
                              (kind, subject)).fetchone()
    return row[0] if row else None


def _issue(ledger, task_id):
    row = ledger.conn.execute("SELECT payload FROM events WHERE kind='work.issue' AND subject=?"
                              " ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    return loads(row[0], {}) if row else {}


def _observed(ledger, task_id):
    row = ledger.conn.execute("SELECT MAX(ts) FROM events WHERE kind IN ('work.issue','work.observed')"
                              " AND subject=?", (task_id,)).fetchone()
    return row[0] if row else None


def _verified(ledger, key, task_id):
    """The verifier's read: a passed verification, else the settlement's `lifecycle.verified`."""
    found = {}
    for row in ledger.conn.execute("SELECT kind, ts, payload FROM events WHERE subject=? AND kind IN"
                                   " ('lifecycle.verification_finished','lifecycle.verified') ORDER BY id",
                                   (key,)):
        payload = loads(row[2], {})
        if payload.get("task_id") != task_id or row[0] in found:
            continue
        if row[0] == "lifecycle.verified" or payload.get("result") == "passed":
            found[row[0]] = row[1]
    return found.get("lifecycle.verification_finished", found.get("lifecycle.verified"))


def _claimed(ledger, task):
    """The first claim on either surface the Watch reads: a Tower flight or a coordinator receipt."""
    flight = ledger.conn.execute("SELECT MIN(created_at) FROM flights WHERE task_id=?",
                                 (task["id"],)).fetchone()[0]
    receipt = ledger.conn.execute("SELECT MIN(ts) FROM events WHERE kind='intake.claim' AND subject=?"
                                  " AND ts >= ?", (task["dedupe_key"], task["created_at"])).fetchone()[0]
    return min((t for t in (flight, receipt) if t is not None), default=None)


def _queue_ready(revision):
    """Was this revision eligible as a `ready` queue item -- judged as its owning lane?

    `work.eligibility` is lane-relative by design: a tower-v2 item reads 'owned' to
    every lane but the tower lane. The judge is not a lane; it measures the owning
    lane's queue, so it asks with the owner's lane context set. Judged from any
    other chair (the Tower tick sets no lane), every tower-v2 item accrues zero
    queue time and its FILED bound can never fire.
    """
    from . import work

    token = None
    try:
        labels = {label["name"].lower() for label in revision.get("labels", [])}
        lane = work.TOWER_LABEL if work.TOWER_LABEL in labels else work._lane.get()
        token = work._lane.set(lane)
        return work.eligibility(revision) == "ready"
    except (KeyError, TypeError, AttributeError):
        return False
    finally:
        if token is not None:
            work._lane.reset(token)


def _queued(ledger, task, until):
    """(first entry, seconds) this generation spent eligible as a `ready` queue item before `until`.

    Read off every recorded `work.issue` revision in order, so an interval that ended stays
    counted after its labels change, and labels added late start the clock late.
    """
    first, total, start = None, 0.0, None
    rows = ledger.conn.execute("SELECT ts, payload FROM events WHERE kind='work.issue' AND subject=?"
                               " ORDER BY id", (task["id"],)).fetchall()
    for ts, payload in rows:
        ts = min(max(ts, task["created_at"]), until)
        ready = _queue_ready(loads(payload, {}))
        if ready and start is None:
            start = ts
            first = ts if first is None else first
        elif not ready and start is not None:
            total, start = total + ts - start, None
    if start is not None:
        total += until - start
    return first, total


def item(ledger, task, now=None):
    """One generation's walk: entry times per state, the current state, and its bounds."""
    now = now if now is not None else time.time()
    issue = _issue(ledger, task["id"])
    created = _parse(issue.get("created_at")) or task["created_at"]
    claimed = _claimed(ledger, task)
    receipt = _first(ledger, "work.receipt", task["id"])
    closed = _first(ledger, "work.closed", task["id"])
    verified = _verified(ledger, task["dedupe_key"], task["id"])
    if closed is None and issue.get("state") == "closed":
        closed = _observed(ledger, task["id"])
    entered = {"FILED": max(created, task["created_at"]), "CLAIMED": claimed, "RECEIPT": receipt,
               "VERIFIED": verified if verified is not None else (closed if receipt else None),
               "CLOSED": closed}
    if entered["RECEIPT"] is None:  # verification without a receipt is not this machine's walk
        entered["VERIFIED"] = None
    prior = None
    for state in STATES:  # evidence is ordered by the walk: a state is never entered before its predecessor
        if entered[state] is not None:
            entered[state] = prior = max(entered[state], prior) if prior is not None else entered[state]
    state = next(s for s in reversed(STATES) if entered[s] is not None)
    left_filed = min((entered[s] for s in STATES[1:] if entered[s] is not None), default=now)
    queue_since, queued_s = _queued(ledger, task, left_filed)
    bounds = {"FILED": FILED_BOUND_S if queue_since is not None else None,
              "CLAIMED": lane_bound(issue.get("body")),
              "RECEIPT": RECEIPT_BOUND_S, "VERIFIED": None, "CLOSED": None}
    return {"key": task["dedupe_key"], "task": task["id"], "number": issue.get("number"),
            "title": issue.get("title") or task["title"], "state": state, "entered": entered,
            "bounds": bounds, "queue_since": queue_since, "queued_s": queued_s}


def overruns(walk, now):
    """[(state, dwell, bound)] for every state that outlived its bound, open or already left."""
    out, entered = [], walk["entered"]
    for index, state in enumerate(STATES):
        start, bound = entered[state], walk["bounds"][state]
        if start is None or bound is None:
            continue
        later = [entered[s] for s in STATES[index + 1:] if entered[s] is not None]
        if state != walk["state"] and not later:
            continue
        dwell = (min(later) if later else now) - start
        if state == "FILED":  # only time eligible in the queue counts, as recorded when it was
            dwell = walk["queued_s"]
        if dwell > bound:
            out.append((state, dwell, bound))
    return out


def _coordinator_walk(ledger, key, number, now):
    """An item only the coordinator holds (no Tower capture): CLAIMED from its first recorded receipt."""
    since = ledger.conn.execute("SELECT MIN(ts) FROM events WHERE kind='intake.claim' AND subject=?",
                                (key,)).fetchone()[0]
    if since is None or since < SINCE:
        return None
    entered = dict.fromkeys(STATES)
    entered["CLAIMED"] = since
    return {"key": key, "task": "coordinator", "number": number, "title": f"coordinator claim {key}",
            "state": "CLAIMED", "entered": entered, "queue_since": None, "queued_s": 0.0,
            "bounds": {"FILED": None, "CLAIMED": LANE_BOUND_S, "RECEIPT": RECEIPT_BOUND_S,
                       "VERIFIED": None, "CLOSED": None}}


def walks(ledger, now=None, claims=None):
    """Every judged generation: the newest per dedupe key, recognized since activation, still seen.

    `claims` (the coordinator's current receipts) adds the items it runs that Tower never captured,
    for as long as the doc still shows a worker running on them.
    """
    now = now if now is not None else time.time()
    rows = ledger.conn.execute(
        "SELECT * FROM tasks t WHERE origin='github-work' AND created_at >= ? AND rowid = ("
        " SELECT rowid FROM tasks u WHERE u.dedupe_key=t.dedupe_key"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1)", (SINCE,)).fetchall()
    out = []
    for task in rows:
        repo = str(task["dedupe_key"]).removeprefix("github:").split("#", 1)[0]
        if work.tower_excluded_repo(repo):
            continue
        walk = item(ledger, task, now)
        if str(walk["title"]).startswith(FAULT_PREFIX):
            continue
        seen = _observed(ledger, task["id"])
        if walk["state"] != "CLOSED" and (seen is None or now - seen > OBSERVED_WITHIN_S):
            continue
        out.append(walk)
    keys = {walk["key"] for walk in out} | {row[0] for row in ledger.conn.execute(
        "SELECT DISTINCT dedupe_key FROM tasks WHERE origin='github-work'")}
    for (repo, number), claim in (claims or {}).items():
        if work.tower_excluded_repo(repo):
            continue
        key = f"github:{repo.lower()}#{int(number)}"
        if claim.get("running") and key not in keys:
            walk = _coordinator_walk(ledger, key, int(number), now)
            if walk:
                out.append(walk)
    return out


def _span(seconds):
    minutes = int(seconds // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fault_text(walk, state, dwell, bound):
    repo_ref = walk["key"].removeprefix("github:")
    nxt = STATES[STATES.index(state) + 1]
    title = (f"{FAULT_PREFIX} {repo_ref} sat in {state} {_span(dwell)} "
             f"(bound {_span(bound)}, overrun {_span(dwell - bound)})")
    left = walk["entered"].get(nxt)
    body = "\n".join([
        f"The intake state machine (nexus-office#289) faulted on **{repo_ref}**.",
        "",
        f"- item: {repo_ref} — {walk['title']}",
        f"- state: **{state}** (next: {nxt})",
        f"- entered {state}: {_iso(walk['entered'][state])}"
        + (f" (queued as `ready` from {_iso(walk['queue_since'])})" if state == "FILED" else ""),
        f"- left {state}: {_iso(left) if left else 'not yet (still ' + state + ')'}",
        f"- bound: {_span(bound)}; dwell: {_span(dwell)}; **overrun: {_span(dwell - bound)}**",
        f"- ledger task: `{walk['task']}`",
        "",
        "This is a fault on the mechanism that should have moved the item, not on the item. "
        "Filed automatically by `nexus/intake.py`; one per item and state.",
        "",
        f"<!-- {marker(walk, state)} -->",
    ])
    return title, body


def marker(walk, state):
    return f"intake-fault:{walk['key']}:{state}:{walk['task']}"


def _gh(args, timeout=30):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip()[:300] or f"gh exit {proc.returncode}")
    return proc.stdout.strip()


def _already_filed(repo, mark):
    """Only after a crash between filing and recording: is the fault already on GitHub?"""
    out = _gh(["api", "-X", "GET", "search/issues", "-f", f"q=repo:{repo} in:body \"{mark}\"",
               "--jq", "[.items[].html_url][0] // empty"])
    return out or None


def _own_filing(ledger, mark, repo, state, now):
    """Atomically take the filing claim on one fault identity, across processes.

    None: already filed, or another live caller holds the claim. Otherwise whether an earlier
    attempt may have reached GitHub (its owner died, ran late, or errored mid-create).
    """
    with ledger.tx() as c:
        if c.execute("SELECT 1 FROM events WHERE kind='intake.fault' AND subject=?", (mark,)).fetchone():
            return None
        row = c.execute("SELECT payload FROM events WHERE kind='intake.fault_filing' AND subject=?"
                        " ORDER BY id DESC LIMIT 1", (mark,)).fetchone()
        last = loads(row[0], {}) if row else None
        if (last and not last.get("released") and last.get("until", 0) > now
                and flights.alive(last.get("pid"))):
            return None
        ledger._event("intake.fault_filing", mark, {"repo": repo, "state": state, "pid": os.getpid(),
                                                    "until": now + FILING_LEASE_S}, "intake")
        return {"attempted": last is not None}


def file_fault(ledger, walk, state, dwell, bound, gh=_gh, now=None):
    """File one fault issue for (generation, state), at most once. Returns its URL or None."""
    mark = marker(walk, state)
    repo = walk["key"].removeprefix("github:").split("#")[0]
    owned = _own_filing(ledger, mark, repo, state, now if now is not None else time.time())
    if owned is None:
        return None
    try:
        url = _already_filed(repo, mark) if owned["attempted"] and gh is _gh else None
        if url is None:
            title, body = fault_text(walk, state, dwell, bound)
            url = gh(["issue", "create", "-R", repo, "--title", title, "--body", body])
    except BaseException:
        ledger.event("intake.fault_filing", mark, {"repo": repo, "state": state, "released": True}, "intake")
        raise
    ledger.event("intake.fault", mark, {"item": walk["key"], "task": walk["task"], "state": state,
                                         "dwell_s": round(dwell), "bound_s": bound,
                                         "overrun_s": round(dwell - bound), "issue": url}, "intake")
    return url


def record_transitions(ledger, walk):
    """`intake.state` once per evidenced (generation, state), in walk order, even if a sweep missed it."""
    seen = set()
    for (payload,) in ledger.conn.execute("SELECT payload FROM events WHERE kind='intake.state'"
                                          " AND subject=?", (walk["key"],)):
        last = loads(payload, {})
        seen.add((last.get("task"), last.get("state")))
    written, previous = 0, None
    with ledger.tx():
        for state in STATES:
            at = walk["entered"][state]
            if at is None:
                continue
            if (walk["task"], state) not in seen:
                ledger._event("intake.state", walk["key"], {"task": walk["task"], "state": state,
                                                             "from": previous, "at": at}, "intake")
                written += 1
            previous = state
    return written


def sync_claims(ledger, published):
    """Record each coordinator claim once, as `intake.claim` at the receipt's own stamp.

    The coordinator doc is rewritten in place; the ledger keeps the claim after the doc moves on.
    `published` is {(repo, number): {'activity': epoch, 'running': bool}} (nexus/coordinator.py).
    """
    count = 0
    for (repo, number), claim in published.items():
        if not claim.get("activity"):
            continue
        key = f"github:{repo.lower()}#{int(number)}"
        if ledger.conn.execute("SELECT 1 FROM events WHERE kind='intake.claim' AND subject=? AND ts >="
                               " COALESCE((SELECT MAX(created_at) FROM tasks WHERE dedupe_key=?), 0)",
                               (key, key)).fetchone():
            continue
        ledger.event("intake.claim", key, {"source": "coordinator", "running": bool(claim.get("running"))},
                     "intake", claim["activity"])
        count += 1
    return count


def coordinator_claims(now=None):
    """Every claim a registered checkout's coordinator doc publishes (the Watch's doc_claims)."""
    registry = os.environ.get("NEXUS_WORK_REGISTRY")
    if not registry:
        return {}
    out = {}
    try:
        entries = work.registry(registry)
    except (OSError, ValueError, work.WorkError):
        return {}
    for entry in entries:
        if entry["enabled"] and not work.tower_excluded_repo(entry["repo"]) and entry.get("path"):
            out.update(coordinator.published(entry["path"]))
    return out


_last_sweep = [0.0]


def sweep(ledger, now=None, gh=_gh, force=False, claims=None):
    """Sync coordinator claims, record transitions, fault overruns. Throttled unless forced."""
    now = now if now is not None else time.time()
    if not force and now - _last_sweep[0] < SWEEP_EVERY_S:
        return None
    _last_sweep[0] = now
    claims = coordinator_claims() if claims is None else claims
    report = {"items": 0, "transitions": 0, "faults": [], "errors": [], "claims": sync_claims(ledger, claims)}
    for walk in walks(ledger, now, claims):
        report["items"] += 1
        report["transitions"] += record_transitions(ledger, walk)
        for state, dwell, bound in overruns(walk, now):
            if len(report["faults"]) >= MAX_FILINGS:
                break
            try:
                url = file_fault(ledger, walk, state, dwell, bound, gh=gh, now=now)
            except Exception as exc:  # noqa: BLE001 - next sweep retries; the filing claim was released
                report["errors"].append(f"{walk['key']}:{state}: {exc}"[:200])
                continue
            if url:
                report["faults"].append(url)
    return report


def states(ledger, now=None):
    """{(repo, number): {state, since, bound_s, overrun_s}} for surfaces (the Watch tab)."""
    now = now if now is not None else time.time()
    out = {}
    for walk in walks(ledger, now):
        repo = walk["key"].removeprefix("github:").split("#")[0]
        state = walk["state"]
        since, bound = walk["entered"][state], walk["bounds"][state]
        dwell = walk["queued_s"] if state == "FILED" else now - since
        over = (dwell - bound) if bound is not None and state != "CLOSED" else None
        out[(repo, walk["number"])] = {"state": state, "since": since, "bound_s": bound,
                                       "overrun_s": round(over) if over and over > 0 else None}
    return out
