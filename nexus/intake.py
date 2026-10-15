"""The intake state machine (nexus-office#289): FILED -> CLAIMED -> RECEIPT -> VERIFIED -> CLOSED.

Not a tracker. Every state is read off records that already exist, per the canonical
pair (issues = truth for work, the ledger = truth for what happened):

    FILED     the item's task generation, from `work.capture` (GitHub open; queue item = `ready`)
    CLAIMED   the first flight on that generation: the same Tower claim the Watch classifier
              reads (client/office_work.py tower_activity, a board row in state 'working')
    RECEIPT   `work.receipt` -- the receipt the verifier accepted, never the lane's word
    VERIFIED  `lifecycle.verified` for the generation (the verifier's read)
    CLOSED    `work.closed`, or GitHub reporting the issue closed

Bounds: FILED 2 h for a queue item, CLAIMED the lane's bound (24 h unless the issue body
sets `Lane bound: N h`), RECEIPT 1 h, VERIFIED none (the verification act closes). A state
that outlives its bound -- still sitting in it, or left late -- raises one fault ON THE
MECHANISM: an issue filed on the item's own repo, deduped per (generation, state) in the
ledger. Never a chat note, never a report line.

`sweep` runs from the Tower tick, throttled; `nexus intake` runs the same sweep by hand.
"""

from __future__ import annotations

import datetime
import re
import subprocess
import time

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
FAULT_PREFIX = "[intake-fault]"
HOLD = {"hold", "on-hold", "blocked", "blocked-needs-look", "cancelled", "canceled"}
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
    for row in ledger.conn.execute("SELECT ts, payload FROM events WHERE kind='lifecycle.verified'"
                                   " AND subject=? ORDER BY id", (key,)):
        if loads(row[1], {}).get("task_id") == task_id:
            return row[0]
    return None


def item(ledger, task):
    """One generation's walk: entry times per state, the current state, and its bounds."""
    issue = _issue(ledger, task["id"])
    labels = {str(label.get("name", "")).lower() for label in issue.get("labels", [])}
    created = _parse(issue.get("created_at")) or task["created_at"]
    flight = ledger.conn.execute("SELECT MIN(created_at) FROM flights WHERE task_id=?",
                                 (task["id"],)).fetchone()[0]
    receipt = _first(ledger, "work.receipt", task["id"])
    closed = _first(ledger, "work.closed", task["id"])
    verified = _verified(ledger, task["dedupe_key"], task["id"])
    if closed is None and issue.get("state") == "closed":
        closed = _observed(ledger, task["id"])
    entered = {"FILED": max(created, task["created_at"]), "CLAIMED": flight, "RECEIPT": receipt,
               "VERIFIED": verified if verified is not None else (closed if receipt else None),
               "CLOSED": closed}
    state = next(s for s in reversed(STATES) if entered[s] is not None)
    queued = "ready" in labels and not labels & HOLD
    bounds = {"FILED": FILED_BOUND_S if queued else None, "CLAIMED": lane_bound(issue.get("body")),
              "RECEIPT": RECEIPT_BOUND_S, "VERIFIED": None, "CLOSED": None}
    return {"key": task["dedupe_key"], "task": task["id"], "number": issue.get("number"),
            "title": issue.get("title") or task["title"], "state": state, "entered": entered,
            "bounds": bounds, "queued": queued}


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
        if dwell > bound:
            out.append((state, dwell, bound))
    return out


def walks(ledger, now=None):
    """Every judged generation: the newest per dedupe key, recognized since activation, still seen."""
    now = now if now is not None else time.time()
    rows = ledger.conn.execute(
        "SELECT * FROM tasks t WHERE origin='github-work' AND created_at >= ? AND rowid = ("
        " SELECT rowid FROM tasks u WHERE u.dedupe_key=t.dedupe_key"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1)", (SINCE,)).fetchall()
    out = []
    for task in rows:
        walk = item(ledger, task)
        if str(walk["title"]).startswith(FAULT_PREFIX):
            continue
        seen = _observed(ledger, task["id"])
        if walk["state"] != "CLOSED" and (seen is None or now - seen > OBSERVED_WITHIN_S):
            continue
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
        f"- entered {state}: {_iso(walk['entered'][state])}",
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


def file_fault(ledger, walk, state, dwell, bound, gh=_gh):
    """File one fault issue for (generation, state), at most once. Returns its URL or None."""
    mark = marker(walk, state)
    if ledger.events(kind="intake.fault", subject=mark):
        return None
    repo = walk["key"].removeprefix("github:").split("#")[0]
    url = None
    if ledger.events(kind="intake.fault_filing", subject=mark):
        url = _already_filed(repo, mark) if gh is _gh else None
    if url is None:
        ledger.event("intake.fault_filing", mark, {"repo": repo, "state": state}, "intake")
        title, body = fault_text(walk, state, dwell, bound)
        url = gh(["issue", "create", "-R", repo, "--title", title, "--body", body])
    ledger.event("intake.fault", mark, {"item": walk["key"], "task": walk["task"], "state": state,
                                         "dwell_s": round(dwell), "bound_s": bound,
                                         "overrun_s": round(dwell - bound), "issue": url}, "intake")
    return url


def record_transition(ledger, walk):
    """`intake.state` once per change: the walk itself, as receipts."""
    row = ledger.conn.execute("SELECT payload FROM events WHERE kind='intake.state' AND subject=?"
                              " ORDER BY id DESC LIMIT 1", (walk["key"],)).fetchone()
    last = loads(row[0], {}) if row else {}
    if last.get("state") == walk["state"] and last.get("task") == walk["task"]:
        return False
    ledger.event("intake.state", walk["key"], {
        "task": walk["task"], "state": walk["state"], "from": last.get("state"),
        "at": walk["entered"][walk["state"]]}, "intake")
    return True


_last_sweep = [0.0]


def sweep(ledger, now=None, gh=_gh, force=False):
    """Record transitions, fault overruns. Throttled to SWEEP_EVERY_S unless forced."""
    now = now if now is not None else time.time()
    if not force and now - _last_sweep[0] < SWEEP_EVERY_S:
        return None
    _last_sweep[0] = now
    report = {"items": 0, "transitions": 0, "faults": [], "errors": []}
    for walk in walks(ledger, now):
        report["items"] += 1
        report["transitions"] += record_transition(ledger, walk)
        for state, dwell, bound in overruns(walk, now):
            if len(report["faults"]) >= MAX_FILINGS:
                break
            try:
                url = file_fault(ledger, walk, state, dwell, bound, gh=gh)
            except Exception as exc:  # noqa: BLE001 - next sweep retries; the filing claim stays
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
        over = (now - since - bound) if bound is not None and state != "CLOSED" else None
        out[(repo, walk["number"])] = {"state": state, "since": since, "bound_s": bound,
                                       "overrun_s": round(over) if over and over > 0 else None}
    return out
