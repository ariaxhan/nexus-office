"""Registry configuration, durable issue intake, and synchronous product execution."""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
import math
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import time

from . import flights
from .ledger import Ledger, LedgerError, TERMINAL, default_path, loads, new_id


_deadline = ContextVar("work_deadline", default=None)
# Tower v2: `code-work` runs with lane=TOWER_LABEL and takes every ready issue in tower rows;
# any other run (nexus-work, disabled 2026-09-13) treats `tower-v2` issues as owned.
TOWER_LABEL = "tower-v2"
SENSITIVE_WINDOW = os.environ.get("NEXUS_TBS_SENSITIVE_WINDOW", os.path.expanduser(
    "~/Developer/Vaults/CodingVault/thinking-brain-school/bin/sensitive_window.py"))

_lane = ContextVar("work_lane", default=None)


def remaining(limit):
    deadline = _deadline.get()
    if deadline is None:
        return limit
    left = deadline - time.monotonic()
    if left <= 0:
        raise WorkError("command budget exhausted")
    return min(limit, left)


class WorkError(LedgerError):
    pass


class Owned(WorkError):
    pass


def registry(path):
    entries = json.loads(Path(path).read_text())["repositories"]
    result = {}
    for raw in entries:
        row = dict(raw)
        name = row["repo"].lower()
        if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", name):
            raise WorkError("invalid canonical repository")
        row["repo"] = name
        workspace = row.get("path")
        if workspace is not None and (not isinstance(workspace, str) or not workspace.strip()):
            raise WorkError(f"{name}: path must be a nonempty string or null")
        row["path"] = str(Path(workspace).expanduser().resolve()) if workspace is not None else None
        if type(row["enabled"]) is not bool:
            raise WorkError("enabled must be boolean")
        for field in ("executor", "verify"):
            argv = row[field]
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v for v in argv):
                raise WorkError(f"{name}: {field} must be argv")
        for argv in row.get("routes", {}).values():
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v for v in argv):
                raise WorkError(f"{name}: route must be argv")
        if not 1 <= float(row.get("timeout_s", 600)) <= 3600:
            raise WorkError(f"{name}: timeout must be between 1 and 3600")
        if type(row.get("max_attempts", 3)) is not int or not 1 <= row.get("max_attempts", 3) <= 10:
            raise WorkError(f"{name}: attempts must be between 1 and 10")
        if not row["provider"] or not row["account"]:
            raise WorkError(f"{name}: provider and account required")
        if name in result:
            previous = dict(result[name], path=row["path"])
            if previous != row:
                raise WorkError(f"{name}: conflicting registry entries")
            continue
        result[name] = row
    return list(result.values())


def github(repo, endpoint):
    proc = subprocess.run(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/{endpoint}"],
                          capture_output=True, text=True, timeout=remaining(60))
    if proc.returncode:
        raise WorkError(proc.stderr.strip() or "GitHub API failed")
    pages = json.loads(proc.stdout)
    return [item for page in pages for item in page]


def discover(led, entry):
    repo = entry["repo"]
    issues = github(repo, "issues?state=open&per_page=100")
    led.event("work.discovered", repo, {"count": len(issues)}, "work")
    for issue in issues:
        if "pull_request" in issue:
            continue
        capture(led, repo, issue)


def capture(led, repo, issue):
    key = f"github:{repo}#{int(issue['number'])}"
    with led.tx() as c:
        task = c.execute("SELECT id FROM tasks WHERE dedupe_key=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (key,)).fetchone()
        if task is None:
            tid = new_id("task")
            c.execute("INSERT INTO tasks(id,origin,title,state,dedupe_key,created_at)"
                      " VALUES (?,'github-work',?,'accepted',?,?)",
                      (tid, issue["title"], key, time.time()))
            led._event("task.state", tid, {"to": "accepted", "dedupe_key": key}, "work")
        else:
            tid = task["id"]
        led._event("work.issue", tid, issue, "work")
    return tid


def latest(led, kind, subject):
    row = led.conn.execute("SELECT payload FROM events WHERE kind=? AND subject=?"
                           " ORDER BY id DESC LIMIT 1", (kind, subject)).fetchone()
    return loads(row[0], {}) if row else {}


def plan(led):
    with led.tx() as c:
        row = c.execute("SELECT id FROM plans WHERE name='github-work'").fetchone()
        if row:
            return row[0]
        pid = new_id("plan")
        c.execute("INSERT INTO plans(id,name,kind,created_at) VALUES (?,'github-work','work',?)",
                  (pid, time.time()))
        led._event("plan.added", pid, {"name": "github-work", "kind": "work"}, "work")
        return pid


def claim(led, repo, number, owner_pid, *, runner=False):
    if owner_pid <= 0 or not flights.alive(owner_pid):
        raise WorkError("owner process must be alive")
    repo = repo.lower()
    key = f"github:{repo}#{number}"
    pid = plan(led)
    with led.tx() as c:
        task = c.execute("SELECT * FROM tasks WHERE dedupe_key=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (key,)).fetchone()
        if task is None or task["state"] == "done":
            raise WorkError("issue must be captured and unfinished")
        active = c.execute("SELECT * FROM flights WHERE task_id=? AND state NOT IN"
                           " ('landed','failed','cancelled')", (task["id"],)).fetchone()
        if active:
            raise Owned(f"claim retained by {active['id']}; reconcile owner first")
        held = c.execute("SELECT holder_flight FROM leases WHERE resource=?", (key,)).fetchone()
        if held:
            raise Owned(f"resource retained by {held[0]}")
        if task["state"] in ("abandoned", "rejected_duplicate", "rejected_policy"):
            if runner or task["state"] != "abandoned":
                raise WorkError("decided issue requires explicit recovery claim")
            previous = task["id"]
            tid = new_id("task")
            c.execute("INSERT INTO tasks(id,origin,title,state,dedupe_key,created_at)"
                      " VALUES (?,'github-work',?,'accepted',?,?)",
                      (tid, task["title"], key, time.time()))
            led._event("work.generation", tid, {"previous_task": previous, "dedupe_key": key,
                                               "reason": "explicit recovery claim"}, "work")
            led._event("work.issue", tid, latest(led, "work.issue", previous), "work")
            task = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        fid = new_id("flt")
        attempt = c.execute("SELECT count(*)+1 FROM flights WHERE task_id IN"
                            " (SELECT id FROM tasks WHERE dedupe_key=?)", (key,)).fetchone()[0]
        c.execute("INSERT INTO flights(id,task_id,plan_id,state,pid,created_at,started_at,attempt)"
                  " VALUES (?,?,?,'running',?,?,?,?)", (fid, task["id"], pid, owner_pid,
                                                         time.time(), time.time(), attempt))
        c.execute("INSERT INTO leases(resource,holder_flight,until) VALUES (?,?,?)",
                  (key, fid, float('inf')))
        c.execute("UPDATE tasks SET state='running',plan_id=? WHERE id=?", (pid, task["id"]))
        led._event("task.state", task["id"], {"to": "running"}, "work")
        led._event("flight.state", fid, {"to": "running", "pid": owner_pid}, "work")
        if runner:
            led._event("work.runner", fid, {}, "work")
    return fid


def stop(led, flight):
    """Stop recorded execution sessions; a direct claim never owns its desktop PID."""
    fid = flight["id"]
    process = latest(led, "work.process", fid).get("pid")
    runner = bool(led.events(kind="work.runner", subject=fid))
    owner = flight["pid"]
    if latest(led, "work.executing", fid) and not process:
        return False
    if runner and owner and owner != os.getpid() and flights.alive(owner):
        try:
            os.kill(owner, signal.SIGSTOP)
        except ProcessLookupError:
            pass
        except OSError:
            return False
    if process and not flights._kill_owned_session(process):
        return False
    if runner and owner and owner != os.getpid():
        try:
            os.kill(owner, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        deadline = time.monotonic() + flights.SESSION_KILL_S
        while flights._live_pids([owner]) != []:
            if time.monotonic() >= deadline:
                return False
            time.sleep(flights.KILL_POLL_S)
    led.event("work.teardown", fid, {"session": process, "runner": runner, "ok": True}, "work")
    return True


def release(led, fid, owner_pid):
    row = led.flight(fid)
    if row is None or row["pid"] != owner_pid:
        raise WorkError("claim owner mismatch")
    if led.plan(row["plan_id"])["kind"] != "work" or row["workspace"]:
        raise WorkError("client workspace release requires product reconciliation")
    if latest(led, "work.executing", fid):
        raise WorkError("execution requires reconciliation before release")
    led.set_state(fid, "cancelled", expect="running", source="work")


def issue_now(led, entry, task):
    number = latest(led, "work.issue", task["id"])["number"]
    proc = subprocess.run(["gh", "api", f"repos/{entry['repo']}/issues/{number}"],
                          capture_output=True, text=True, timeout=remaining(60))
    if proc.returncode:
        raise WorkError(proc.stderr.strip() or "issue lookup failed")
    issue = json.loads(proc.stdout)
    if not isinstance(issue, dict) or issue.get("number") != number or issue.get("state") not in ("open", "closed"):
        raise WorkError("invalid authoritative issue")
    capture(led, entry["repo"], issue)
    return issue


def eligibility(issue):
    if issue["state"] == "closed":
        return "closed"
    labels = {label["name"].lower() for label in issue.get("labels", [])}
    if TOWER_LABEL in labels and _lane.get() != TOWER_LABEL:
        return "owned"
    if labels.intersection({"direct", "claimed", "in-progress", "in progress"}):
        return "owned"
    if labels.intersection({"hold", "on-hold", "blocked", "blocked-needs-look", "cancelled", "canceled"}):
        return "held"
    return "ready" if "ready" in labels else "resume" if labels.intersection({"in-pr", "in pr"}) else "ineligible"


def context(led, entry, task, issue=None):
    issue = issue if issue is not None else issue_now(led, entry, task)
    # Issue timeline is authoritative and bounded to this obligation, not repo history.
    events = github(entry["repo"], f"issues/{issue['number']}/timeline?per_page=100")
    matches = {}
    for event in events:
        source = event.get("source", {}).get("issue", {})
        if source.get("pull_request"):
            matches[source["html_url"]] = source
    return {"repo": entry["repo"], "issue": issue, "pull_requests": list(matches.values()),
            "task": task["id"], "provider": entry["provider"], "account": entry["account"],
            "idempotency_key": task["dedupe_key"], "mode": "resume" if matches or eligibility(issue) == "resume" else "build",
            "item_attempts": conveyor_attempts(led, task["dedupe_key"].removeprefix("github:"))}


def adapter(argv, entry, payload, log, started=None):
    """One bounded process session; JSON input, retained output and diagnostics."""
    if not workspace_available(entry):
        raise WorkError("checkout unavailable; hydrate explicitly through vaults repos")
    timeout = min(3600, max(1, float(entry.get("timeout_s", 600))))
    env = dict(os.environ, NEXUS_WORK_PROVIDER=entry["provider"], NEXUS_WORK_ACCOUNT=entry["account"])
    timeout = remaining(timeout)
    with open(log, "ab") as handle:
        proc = subprocess.Popen(argv, cwd=entry["path"], env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=handle, start_new_session=True)
        try:
            if started:
                started(proc.pid)
            output, _ = proc.communicate(json.dumps(payload).encode(), timeout=remaining(timeout))
        except BaseException:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        finally:
            # A returned parent cannot leave delivery running in its process group.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass  # macOS answers EPERM for a group holding only zombies.
        handle.write(output)
        if proc.returncode:
            raise WorkError(f"adapter exit {proc.returncode}")
    return output


def proof(entry, payload, log):
    result = json.loads(adapter(entry["verify"], entry, payload, log))
    if not isinstance(result, dict) or result.get("state") not in ("absent", "pending", "delivered"):
        raise WorkError("unknown verification result")
    if result["state"] == "delivered":
        if result.get("verified") is not True or not isinstance(result.get("evidence"), list) or not result["evidence"]:
            raise WorkError("delivery proof missing")
        if result.get("idempotency_key") != payload["idempotency_key"]:
            raise WorkError("delivery proof identity mismatch")
    return result


def finish(led, fid, payload, result):
    led.event("work.proof", fid, result, "work")
    item_attempt(led, fid, "succeeded", evidence=result["evidence"])
    row = led.flight(fid)
    for state in ("produced", "verified"):
        led.set_state(fid, state, source="work")
    landing = led.create_landing(fid, payload["idempotency_key"], state="verified")
    led.apply_landing(landing, json.dumps(result["evidence"], sort_keys=True))
    led.set_task_state(row["task_id"], "done", decided_by="work proof")
    close_issue(led, payload)


def close_issue(led, payload):
    issue = issue_now(led, {"repo": payload["repo"]}, {"id": payload["task"]})
    if issue["state"] == "closed":
        led.event("work.closed", payload["task"], {"repo": payload["repo"]}, "work")
        return
    if eligibility(issue) not in ("ready", "resume"):
        raise WorkError("issue closure held by current eligibility")
    proc = subprocess.run(["gh", "api", "--method", "PATCH",
                           f"repos/{payload['repo']}/issues/{payload['issue']['number']}",
                           "-f", "state=closed"], capture_output=True, text=True, timeout=remaining(60))
    if proc.returncode:
        raise WorkError(proc.stderr.strip() or "issue closure failed")
    if json.loads(proc.stdout).get("state") != "closed":
        raise WorkError("issue closure not confirmed")
    led.event("work.closed", payload["task"], {"repo": payload["repo"]}, "work")


def item_attempt(led, fid, status, retry_at=0, evidence=None):
    row = led.flight(fid)
    task = led.conn.execute("SELECT dedupe_key FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
    led.event("work.item_attempt", task[0].removeprefix("github:"),
              {"step": "nexus-work", "status": status, "attempt": row["attempt"],
               "retry_at": retry_at, "evidence": evidence or []}, "work")


def fail(led, fid, exc):
    row = led.flight(fid)
    # Back off on consecutive failures; review and pending passes are progress, not failures.
    streak = 1
    for prior in sorted(led.flights(task_id=row["task_id"]), key=lambda r: r["created_at"], reverse=True):
        if prior["id"] == fid:
            continue
        if prior["state"] != "failed":
            break
        streak += 1
    delay = min(86400, 60 * 2 ** min(streak, 10))
    item_attempt(led, fid, "failed", time.time() + delay, [str(exc)])
    led.event("work.failure", row["task_id"], {"flight": fid, "error": str(exc),
              "next_retry": time.time() + delay, "attempt": row["attempt"]}, "work")
    if row["state"] not in TERMINAL:
        led.fail(fid, "work_failed", str(exc), expect=row["state"])


def pending(led, fid, result):
    now = time.time()
    retry = result.get("retry_at", now + 60)
    if not isinstance(retry, (int, float)) or not math.isfinite(retry):
        retry = now + 60
    retry = min(now + 86400, max(now + 1, retry))
    tid = led.flight(fid)["task_id"]
    rank = priority_rank({l["name"].lower() for l in latest(led, "work.issue", tid).get("labels", [])})
    led.event("work.pending", tid, dict(result, next_retry=retry, rank=rank), "work")
    item_attempt(led, fid, "pending", retry, result.get("evidence"))
    led.set_state(fid, "cancelled", expect="running", source="work")
    return "pending"


def select_executor(entry, issue):
    routes = entry.get("routes", {})
    labels = {r["name"] for r in issue.get("labels", [])}
    selected = labels.intersection(routes)
    if len(selected) > 1:
        raise WorkError("conflicting executor routes")
    return routes[next(iter(selected))] if selected else entry["executor"]


def source_evidence(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, dict)
                    and all(isinstance(item.get(key), str) and item[key].strip()
                            for key in ('url', 'head')) for item in value))


def source_continuation(result, payload):
    """Only an authoritative verifier can attest a resumable source review stage."""
    return (result['state'] == 'absent' and result.get('retry_safe') is True
            and result.get('idempotency_key') == payload['idempotency_key']
            and result.get('resume_kind') in ('review', 'repair')
            and source_evidence(result.get('evidence')))


def execute(led, entry, task):
    previous = led.flights(task_id=task["id"])
    uncertain = any(row["state"] != "cancelled" or latest(led, "work.executing", row["id"]) for row in previous)
    for row in previous:
        if row["state"] in TERMINAL:
            continue
        process = latest(led, "work.process", row["id"])
        if flights.alive(process.get("pid")):
            return "owned"
        if flights.alive(row["pid"]):
            return "owned"
        if not led.events(kind="work.runner", subject=row["id"]) and not latest(led, "work.executing", row["id"]):
            return "owned"  # A dead direct session is not product release authorization.
        # Delivery may have happened. Never replay until authoritative reconciliation.
        led.fail(row["id"], "owner_exited", "owner process terminated")
    fid = claim(led, entry["repo"], latest(led, "work.issue", task["id"])["number"], os.getpid(), runner=True)
    log = Path(led.path).resolve().parent / "logs" / f"{fid}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch()
    led.add_artifact(fid, "log", str(log))
    try:
        payload = context(led, entry, task)
        state = eligibility(payload["issue"])
        if state not in ("ready", "resume") and not uncertain:
            led.set_state(fid, "cancelled", expect="running", source="work")
            return state
        result = proof(entry, payload, log)
        if result["state"] == "delivered":
            finish(led, fid, payload, result)
            return "done"
        if state not in ("ready", "resume"):
            led.set_state(fid, "cancelled", expect="running", source="work")
            return state
        if result["state"] == "pending":
            return pending(led, fid, result)
        if (uncertain or payload["item_attempts"] or payload["mode"] == "resume") and result.get("retry_safe") is not True:
            raise WorkError("prior execution requires authoritative retry clearance")
        attempts = sum(bool(latest(led, "work.executing", r["id"])) for r in previous)
        if attempts >= min(10, entry.get("max_attempts", 3)):
            raise WorkError("executor attempts exhausted")
        payload["issue"] = issue_now(led, entry, task)
        state = eligibility(payload["issue"])
        if state not in ("ready", "resume"):
            led.set_state(fid, "cancelled", expect="running", source="work")
            return state
        argv = select_executor(entry, payload["issue"])
        led.event("work.executing", fid, payload, "work")
        item_attempt(led, fid, "started")
        adapter(argv, entry, payload, log,
                lambda pid: led.event("work.process", fid, {"pid": pid}, "work"))
        result = proof(entry, payload, log)
        if source_continuation(result, payload):
            result = dict(result, state='pending',
                          reason=result.get('reason') or f"Verified source awaits {result['resume_kind']}")
        if result["state"] == "pending":
            return pending(led, fid, result)
        if result["state"] != "delivered":
            raise WorkError("requested outcome not proven")
        finish(led, fid, payload, result)
        return "done"
    except (OSError, ValueError, KeyError, TypeError, LedgerError, subprocess.SubprocessError) as exc:
        fail(led, fid, exc)
        return "failed"


def needs_reconciliation(led, task, state):
    if task["state"] == "done":
        return True
    return state == "closed" and any(
        latest(led, "work.executing", row["id"]) for row in led.flights(task_id=task["id"]))


def selection_queue(led, entry):
    """Fresh intake dispositions do not spend the bounded actionable-work slots."""
    name = entry["repo"]
    queue, report = [], []
    for task in led.tasks():
        if not str(task["dedupe_key"]).startswith(f"github:{name}#"):
            continue
        newest = led.conn.execute("SELECT id FROM tasks WHERE dedupe_key=?"
                                  " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                  (task["dedupe_key"],)).fetchone()
        if newest[0] != task["id"]:
            continue
        if task["state"] == "done" and latest(led, "work.closed", task["id"]):
            continue
        issue = latest(led, "work.issue", task["id"])
        state = eligibility(issue)
        if state in ("ready", "resume") or needs_reconciliation(led, task, state):
            queue.append(task)
            continue
        if state == "closed" and latest(led, "work.disposition", task["id"]).get("state") == "closed":
            continue
        reason = f"{state}: current intake labels " + ", ".join(l["name"] for l in issue.get("labels", []))
        disposition(led, task, state, reason)
        report.append(dict(repo=name, task=task["id"], state=state))
    return sorted(queue, key=lambda task: selection_priority(led, task)), report


def selection_priority(led, task):
    """Fresh work first, then age with a one-day bonus per signal.

    A task whose last disposition was "pending" is waiting on something outside the tower (a
    review, a merge, a proof). It has had its slot; it sorts behind every task that has not,
    so ten stalled in-PR items cannot hold a repository's slot against a new ready issue
    (Aria, 2026-09-11, tbs-www#310). Within the unstalled, `ready` work precedes `in pr`
    resumption: an open PR is waiting on review or merge, a ready issue is waiting on us.
    Stalled and resuming items still run once fresh work is exhausted, or from the second
    slot when max_items allows. Priority is strict and comes first: p0 > p1 > p2 > unlabeled,
    ahead of stall, resumption and age (a stalled item is skipped as backoff, so it never holds
    a slot)."""
    issue = latest(led, "work.issue", task["id"])
    labels = {label["name"].lower() for label in issue.get("labels", [])}
    bonus = 86400 * (bool(led.flights(task_id=task["id"])) +
                     bool(labels & {"urgent", "in-pr", "in pr"}))
    # Stalled only while the recorded retry deadline is still ahead; past it the item is as
    # fresh as any other and competes on age again.
    stalled = (latest(led, "work.disposition", task["id"]).get("state") == "pending"
               and next_retry(led, task) > time.time())
    resuming = eligibility(issue) == "resume"      # in PR: waiting on review or merge, not on us
    return (priority_rank(labels), stalled, resuming, task["created_at"] - bonus)


PRIORITY_LABELS = ("p0", "p1", "p2")


def is_p0(led, task):
    return priority_rank({l["name"].lower() for l in latest(led, "work.issue", task["id"]).get("labels", [])}) == 0


def priority_rank(labels):
    """0 for p0, 1 for p1, 2 for p2, 3 unlabeled; the highest label present wins."""
    return next((rank for rank, name in enumerate(PRIORITY_LABELS) if name in labels), len(PRIORITY_LABELS))


def tower_row(entry):
    """Risk rules present, or a personal repo. Client repos without risk data never reach an auto-merge."""
    checkout = entry.get("canonical_path") or entry.get("path")
    return bool(checkout and os.path.isdir(checkout)) and (
        bool(entry.get("risk")) or (entry.get("delivery") or {}).get("kind") == "product-proof")


def eligible(led, entries, repo):
    """Enabled repositories, or none: `nexus plans disable github-work` stops every one at once."""
    entries = [e for e in entries if e["enabled"] and (not repo or e["repo"] == repo.lower())]
    if _lane.get() == TOWER_LABEL:
        switch = led.plan_by_name("code-work")  # its own switch; github-work does not gate it
        if switch and switch["enabled"]:
            return [e for e in entries if tower_row(e)]
    elif led.plan(plan(led))["enabled"]:
        return entries
    led.event("work.disabled", "github-work", {"repositories": len(entries)}, "work")
    return []


def run(led, entries, repo=None, *, budget_s=300, max_items=20, lane=None, issue=None, registry_path=None,
        parallel=None):
    token = _lane.set(lane)
    try:
        cut = cut_idle(led, [e for e in entries if e["enabled"]]) if lane == TOWER_LABEL and issue is None else []
        if lane == TOWER_LABEL and issue is None and repo is None and registry_path:
            waved = dispatch_wave(led, entries, registry_path, budget_s, parallel or lanes_caps(registry_path))
            if waved is not None:
                return cut + waved
        return cut + _run(led, entries, repo, budget_s=budget_s, max_items=max_items, issue=issue)
    finally:
        _lane.reset(token)


_registry = ContextVar("work_registry", default=None)


def lanes_caps(registry_path):
    from . import lanes
    try:
        return lanes.caps(json.loads(Path(registry_path).read_text()).get("tower"))
    except (OSError, ValueError, TypeError):
        return lanes.PER_REPO, lanes.GLOBAL


def wave_candidates(led, entries, caps):
    """The first execution-plan wave with runnable items, cut to the caps and to disjoint write sets.

    Waves gate in plan order; within a wave, runnable items are taken p0 > p1 > p2 > unlabeled
    (plan order breaks ties) before the caps and write-set cuts apply. A runnable p0 in a later
    wave preempts that order at the next tick: dependencies are already enforced by the contract gate."""
    from . import lanes
    waves = lanes.read_plan()
    if not waves:
        return None
    rows = {e["repo"]: e for e in eligible(led, entries, None)}
    per_repo, global_cap = caps
    discovered = set()
    runnable_waves = []
    for wave in waves:
        runnable = []
        for item in wave:
            entry = rows.get(item["repo"])
            if not entry:
                continue
            if item["repo"] not in discovered:
                discover(led, entry)
                discovered.add(item["repo"])
            task = led.conn.execute("SELECT * FROM tasks WHERE dedupe_key=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                    (f"github:{item['repo']}#{item['number']}",)).fetchone()
            if not task or task["state"] == "done" or next_retry(led, task) > time.time():
                continue
            issue = latest(led, "work.issue", task["id"])
            if eligibility(issue) not in ("ready", "resume") or tower_gate(entry, issue)[1]:
                continue
            runnable.append((priority_rank({l["name"].lower() for l in issue.get("labels", [])}), item))
        runnable_waves.append(runnable)
    first = next((r for r in runnable_waves if r), [])
    preempt = [pair for r in runnable_waves if r is not first for pair in r if pair[0] == 0]
    picked = []
    for _, item in sorted(first + preempt, key=lambda pair: pair[0]):
        if len(picked) >= global_cap:
            break
        if sum(p["repo"] == item["repo"] for p in picked) >= per_repo:
            continue
        if any(p["repo"] == item["repo"] and lanes.overlaps(p["write_set"], item["write_set"]) for p in picked):
            continue
        picked.append(item)
    return picked or None


def window_open():
    """bin/sensitive_window.py allows a sensitive change now (exit 0)."""
    proc = subprocess.run(["python3", SENSITIVE_WINDOW, "check", "--label", "sensitive"],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def contract_required(entry):
    """TBS repositories are triaged: their issues run only with a valid tbs-contract block."""
    return entry["repo"].startswith("thinking-brain-school/")


def tower_gate(entry, issue):
    """THE eligibility gate for every Tower path (wave, fallback, retry, review resume): (contract, reason|None)."""
    from . import contract
    return contract.gate(issue, required=contract_required(entry), window_open=window_open)


def reopen(led, task_id, reason):
    """A done task that was never proven done gets a fresh generation; history stays."""
    with led.tx() as c:
        task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        newest = c.execute("SELECT id FROM tasks WHERE dedupe_key=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                           (task["dedupe_key"],)).fetchone()[0]
        if newest != task_id or task["state"] not in ("done", "abandoned"):
            return None
        tid = new_id("task")
        c.execute("INSERT INTO tasks(id,origin,title,state,dedupe_key,created_at) VALUES (?,?,?,'accepted',?,?)",
                  (tid, task["origin"], task["title"], task["dedupe_key"], time.time()))
        led._event("work.generation", tid, {"previous_task": task_id, "dedupe_key": task["dedupe_key"],
                                            "reason": reason}, "work")
        led._event("work.issue", tid, latest(led, "work.issue", task_id), "work")
    return tid


def dispatch_wave(led, entries, registry_path, budget_s, caps):
    """Run one wave's file-disjoint lanes at once, one child `work run --issue` each. None: fall back."""
    picked = wave_candidates(led, entries, caps)
    if not picked:
        return None
    base = [sys.executable, "-m", "nexus", "--ledger", str(led.path), "work", "run", "--registry", str(registry_path),
            "--lane", TOWER_LABEL, "--max-items", "1", "--budget-s", str(int(budget_s))]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    children = []
    for item in picked:
        proc = subprocess.Popen(base + ["--repo", item["repo"], "--issue", str(item["number"])], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        led.event("work.wave_lane", item["repo"], dict(item, pid=proc.pid, started=time.time()), "work")
        children.append((item, proc))
    report = []
    for item, proc in children:
        try:
            out, err = proc.communicate(timeout=max(1, budget_s))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        try:
            rows = json.loads(out or "[]")
        except ValueError:
            rows = [dict(repo=item["repo"], state="failed", error=(err or "")[-300:])]
        led.event("work.wave_lane_done", item["repo"], dict(item, pid=proc.pid, rc=proc.returncode,
                                                          ended=time.time(), report=rows), "work")
        report.extend(rows)
    return report


MIN_FLIGHT_S = 300   # an executor started with less budget than this only times out (25s runs, 2026-09-14)
WAIT_S = 120         # a lane lock, a leased path or an unpushable hold: retried after this, never an attempt


def open_pr(repo, number):
    """URL of the open Tower PR for this issue (head aria/issue-<n>), or None."""
    proc = subprocess.run(["gh", "pr", "list", "-R", repo, "--head", f"aria/issue-{number}", "--state", "open",
                           "--json", "url", "--jq", ".[0].url // empty"],
                          capture_output=True, text=True, timeout=remaining(60))
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def wait(led, fid, why):
    """A contention wait: bounded retry_at, recorded as pending, never counted as a failed attempt."""
    return pending(led, fid, {"reason": f"wait: {why}"[:300], "retry_at": time.time() + WAIT_S, "evidence": []})


IDLE_S = 1200        # a tower lane whose checkout fingerprint has not moved for this long is idle: cut off
MAX_CUTS = 2         # after this many cut-offs the issue is blocked-needs-look and skipped (Aria, 2026-09-14)


def progress(path, fid):
    """Fingerprint of what a flight has done in its checkout (HEAD + its changed bytes); None: no lease."""
    from . import landing, lanes, lease
    rec = next((r for r in [lease.read(path)] + lanes.records(path) if r and r.get("flight") == fid), None)
    if rec is None:
        return None
    head = landing._git(path, "rev-parse", "HEAD").stdout.strip()
    changed, _ = lease.flight_paths(path, rec)
    return json.dumps([head, {p: lease.digest(path, p) for p in changed}], sort_keys=True)


def cut_idle(led, entries, now=None):
    """Each tick: a running tower lane with no progress since IDLE_S is stopped, its bytes held, the issue
    flagged once and requeued; the MAX_CUTS-th cut blocks the issue so no loop re-flies it."""
    from . import lanes, lease
    now, paths, cut = now or time.time(), {e["repo"]: e.get("canonical_path") or e["path"] for e in entries}, []
    for flight in led.flights(states=("running",)) if paths else []:
        run = latest(led, "work.executing", flight["id"])
        path = paths.get(run.get("repo"))
        if run.get("lane") != TOWER_LABEL or not path:
            continue
        fp, seen = progress(path, flight["id"]), latest(led, "work.progress", flight["id"])
        if fp is None:
            continue
        if seen.get("fp") != fp:
            led.event("work.progress", flight["id"], {"fp": fp, "at": now}, "work")
            continue
        if now - seen["at"] < IDLE_S or not stop(led, flight):
            continue
        with contextlib.suppress(Exception):  # preserve first; a failed hold leaves the stale lease for recovery
            lease.recover(path)
            lanes.recover(path)
        key = led.conn.execute("SELECT dedupe_key FROM tasks WHERE id=?", (flight["task_id"],)).fetchone()[0]
        cuts = 1 + sum(1 for e in led.events(kind="work.cut") if loads(e["payload"], {}).get("key") == key)
        led.event("work.cut", flight["task_id"], {"key": key, "flight": flight["id"], "cuts": cuts}, "work")
        number, blocked = run["issue"], cuts >= MAX_CUTS
        body = (f"Nexus flight {flight['id']}: idle {int(now - seen['at'])}s, cut off ({cuts}/{MAX_CUTS}). "
                + ("Blocked: needs a look; Tower skips it." if blocked else "Requeued for a fresh lane."))
        subprocess.run(["gh", "issue", "comment", str(number), "-R", run["repo"], "--body", body],
                       capture_output=True, text=True, timeout=60)
        if blocked:
            subprocess.run(["gh", "issue", "edit", str(number), "-R", run["repo"], "--remove-label", "ready",
                            "--add-label", "blocked-needs-look"], capture_output=True, text=True, timeout=60)
        pending(led, flight["id"], {"reason": f"cut: idle ({cuts}/{MAX_CUTS})", "evidence": [],
                                    "retry_at": now + (86400 if blocked else 1)})
        cut.append(dict(repo=run["repo"], issue=number, state="blocked" if blocked else "requeued"))
    return cut


def tower_execute(led, entry, task):
    """Tower v2: lease the canonical checkout, run, land in place, prove a terminal state."""
    from . import executor
    entry = dict(entry, path=entry.get("canonical_path") or entry["path"])
    deadline = _deadline.get()
    if deadline is not None and deadline - time.monotonic() < MIN_FLIGHT_S:
        return "backoff"  # no claim, no flight: the next tick has a full budget
    issue = issue_now(led, entry, task)
    repo, number = entry["repo"], issue["number"]
    pr_url = open_pr(repo, number)
    if pr_url:  # the build already produced a PR: review its head, never rebuild and re-push it
        return tower_review(led, entry, task, pr_url)
    fid = claim(led, entry["repo"], issue["number"], os.getpid(), runner=True)

    def gh(*args):
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=remaining(120))
        if proc.returncode:
            raise WorkError(proc.stderr.strip() or "gh failed")
        return proc.stdout.strip()

    def pr_create(head, base, body):
        try:
            return gh("pr", "create", "-R", repo, "--head", head, "--base", base,
                      "--title", issue.get("title", f"#{number}"), "--body", body)
        except WorkError:
            existing = open_pr(repo, number)  # the branch moved under an open PR: the PR is the same obligation
            if existing:
                return existing
            raise

    from . import lanes
    found, why = tower_gate(entry, issue)
    if why:  # re-checked at the moment of flight: a dependency may have reopened
        led.event("work.gated", fid, {"repo": repo, "issue": number, "reason": why}, "work")
        return pending(led, fid, {"reason": f"gate: {why}", "retry_at": time.time() + 1800, "evidence": []})
    write_set = (found or {}).get("write_set") or lanes.write_set_for(repo, number)
    if (found or {}).get("route") == "antigravity":
        issue = dict(issue, labels=list(issue.get("labels", [])) + [{"name": "route-antigravity"}])
    per_repo = lanes_caps(_registry.get())[0] if _registry.get() else lanes.PER_REPO
    try:
        led.event("work.executing", fid, {"repo": repo, "issue": number, "lane": TOWER_LABEL, "open_deps": [],
                                          "write_set": write_set, "started": time.time()}, "work")
        result = executor.fly(
            entry, issue, fid, timeout_s=remaining(float(entry.get("timeout_s", 900))),
            pr_create=pr_create,
            comment=lambda body: gh("issue", "comment", str(number), "-R", repo, "--body", body),
            write_set=write_set, per_repo=per_repo)
        led.event("work.lane", fid, {"repo": repo, "issue": number, "write_set": write_set, "state": result["state"],
                                     "sha": result.get("sha"), "ended": time.time()}, "work")
        state = _settle(led, fid, entry, task, issue, result, found)
        if result.get("reason") == "in_review":
            return tower_review(led, entry, task, result["pr_url"])
        return state
    except Exception as exc:  # noqa: BLE001 - every failure is recorded; the lease stays for recovery
        from . import landing, lease
        if isinstance(exc, lease.Owned):  # another lane or a person holds these paths: a wait, not a failure
            return wait(led, fid, str(exc))
        if isinstance(exc, landing.LandingError) and exc.code == "held_push_failed":
            return wait(led, fid, str(exc))  # the lease keeps the bytes; recovery pushes them next tick
        fail(led, fid, exc)
        return "failed"


def _settle(led, fid, entry, task, issue, result, contract_=None):
    from . import tower
    repo, number = entry["repo"], issue["number"]
    tower.land_write_flight(led, fid, entry["path"], result)
    if result["state"] == "HELD":
        if result.get("reason") != "in_review" and not result.get("requeue"):  # parked for a person; never re-flown on the next tick
            subprocess.run(["gh", "issue", "edit", str(number), "-R", repo, "--remove-label", "ready",
                            "--add-label", "hold"], capture_output=True, text=True, timeout=remaining(60))
        retry_s = result.get("retry_s") or (60 if result.get("reason") == "in_review" or result.get("requeue") else 3600)
        return pending(led, fid, {"reason": result.get("reason"), "retry_at": time.time() + retry_s,
                                  "hold": result.get("hold"), "pr_url"
: result.get("pr_url"),
                                  "evidence": [result.get("pr_url") or result.get("comment_url")]})
    from . import contract
    done, why = contract.done_receipt(result, contract_, entry["path"])
    if not done:  # an executor exit is never proof: no_change, a failed check or no commit stays open
        url = subprocess.run(["gh", "issue", "comment", str(number), "-R", repo, "--body",
                              f"Nexus flight {fid}: not done. {why}. Left open for the next attempt."],
                             capture_output=True, text=True, timeout=remaining(60)).stdout.strip()
        return pending(led, fid, {"reason": f"not_done: {why}", "retry_at": time.time() + 3600,
                                  "evidence": [url] if url else []})
    led.event("work.receipt", task["id"], {"flight": fid, "sha": result["sha"], "receipt": why}, "work")
    led.set_task_state(led.flight(fid)["task_id"], "done", decided_by="tower receipt: " + why)
    close_issue(led, {"repo": repo, "task": task["id"], "issue": issue})
    return "done"


def _sensitive_hold(entry, pr):
    """TBS sensitive changes merge only 01:00-08:00 KST; the gate's refusal (exit 3) is the hold message."""
    if not entry["repo"].lower().startswith("thinking-brain-school/"):
        return None
    labels = [arg for l in pr.get("labels") or [] for arg in ("--label", l["name"])]
    files = [f["path"] for f in pr.get("files") or []]
    proc = subprocess.run(["python3", SENSITIVE_WINDOW, "check", "--repo", entry["path"], *labels, *files],
                          capture_output=True, text=True, timeout=remaining(60))
    if proc.returncode == 3:
        return proc.stderr.strip() or "sensitive change outside the 01:00-08:00 KST window"
    if proc.returncode:
        raise WorkError("sensitive window check failed: " + proc.stderr.strip()[:200])
    return None


def review_verdict(led, pr_url, head):
    """The last recorded reviewer verdict for exactly this PR head, or None."""
    for row in led.conn.execute("SELECT payload FROM events WHERE kind='work.review' ORDER BY id DESC"):
        payload = loads(row[0], {})
        if payload.get("pr") == pr_url and head and payload.get("head") == head:
            return payload
    return None


def tower_review(led, entry, task, pr_url):

    """A separate flight, different identity: PASS merges (LANDED), FAIL comments (HELD)."""
    from . import executor
    entry = dict(entry, path=entry.get("canonical_path") or entry["path"])
    issue = issue_now(led, entry, task)
    fid = claim(led, entry["repo"], issue["number"], os.getpid(), runner=True)
    repo = entry["repo"]
    try:
        gh = lambda *a: subprocess.run(["gh", *a], capture_output=True, text=True, timeout=remaining(120))  # noqa: E731
        pr = json.loads(gh("pr", "view", pr_url, "--json", "headRefName,headRefOid,baseRefName,labels,files").stdout)
        cached = review_verdict(led, pr_url, pr["headRefOid"])
        if cached:  # the same head was already judged: never restart review on an unchanged head
            verdict, why = cached["verdict"], cached["reason"]
        else:
            led.event("work.executing", fid, {"repo": repo, "issue": issue["number"], "lane": TOWER_LABEL,
                                              "review": pr_url, "head": pr["headRefOid"]}, "work")
            verdict, why = executor.review(entry, pr_url, fid, timeout_s=remaining(900))
        led.event("work.review", fid, {"pr": pr_url, "head": pr["headRefOid"], "verdict": verdict, "reason": why,
                                       "cached": bool(cached)}, "work")
        held = verdict == "PASS" and _sensitive_hold(entry, pr)
        if held:  # outside the TBS KST window: stay in review, re-flown after the window opens
            url = gh("pr", "comment", pr_url, "--body", f"Nexus review flight {fid}: HELD. {held}").stdout.strip()
            return _settle(led, fid, entry, task, issue,
                           {"state": "HELD", "flight": fid, "reason": "in_review", "retry_s": 3600, "hold": held[:300],
                            "pr_url": pr_url, "sha": pr["headRefOid"], "branch": pr["headRefName"],
                            "comment_url": url or pr_url})
        if verdict == "PASS":

            merged = gh("pr", "merge", pr_url, "--squash", "--delete-branch")
            sha = json.loads(gh("pr", "view", pr_url, "--json", "mergeCommit").stdout or "{}").get(
                "mergeCommit") or {}
            if merged.returncode == 0 and sha.get("oid"):
                return _settle(led, fid, entry, task, issue,
                               {"state": "LANDED", "flight": fid, "sha": sha["oid"], "branch": pr["baseRefName"]},
                               tower_gate(entry, issue)[0])
            why = "merge failed: " + (merged.stderr or "").strip()[:200]
        url = gh("pr", "comment", pr_url, "--body", f"Nexus review flight {fid}: HELD. {why}").stdout.strip()
        return _settle(led, fid, entry, task, issue,
                       {"state": "HELD", "flight": fid, "reason": f"review_fail: {why}"[:300],
                        "sha": pr["headRefOid"], "branch": pr["headRefName"], "comment_url": url or pr_url})
    except Exception as exc:  # noqa: BLE001
        fail(led, fid, exc)
        return "failed"


def _run(led, entries, repo=None, *, budget_s=300, max_items=20, issue=None):
    """One pass over every enabled repository; max_items bounds the work taken PER repository.

    2026-09-11: the bound used to be per run. With ~90 registered repositories and one item
    per 15-minute tick, a repository whose issue merely reported "pending" consumed the whole
    tick and tbs-www waited a day for its turn. Now each repository gets its own slots every
    tick, so a queue in one repository never starves another."""
    if not math.isfinite(budget_s) or not 1 <= budget_s <= 3600 or not 1 <= max_items <= 100:
        raise WorkError("budget must be 1..3600 seconds; max_items must be 1..100")
    if repo and repo.lower() not in {e["repo"] for e in entries}:
        raise WorkError(f"unknown repository: {repo}")
    entries = eligible(led, entries, repo)
    report = []
    # Least recently serviced repositories first, using existing ledger receipts.
    entries.sort(key=lambda e: (not has_p0(led, e["repo"]), latest(led, "work.serviced", e["repo"]).get("at", 0)))
    deadline = time.monotonic() + budget_s
    passive = ("held", "owned", "ineligible", "closed", "backoff", "blocked", "reopened")
    for index, entry in enumerate(entries):
        if time.monotonic() >= deadline:
            break
        # A tower flight needs the whole budget; queue fairness comes from least-recently-serviced order.
        share = 1 if _lane.get() == TOWER_LABEL else len(entries) - index
        token = _deadline.set(min(deadline, time.monotonic() + (deadline - time.monotonic()) / share))
        try:
            name = entry["repo"]
            discover(led, entry)
            queue, dispositions = selection_queue(led, entry)
            report.extend(dispositions)
            if issue is not None:  # one lane of a dispatched wave
                queue = [t for t in queue if t["dedupe_key"] == f"github:{name}#{int(issue)}"]
            taken = 0
            while queue and taken < max_items and time.monotonic() < deadline:
                while (queue and eligibility(latest(led, "work.issue", queue[0]["id"])) in ("ready", "resume")
                       and next_retry(led, queue[0]) > time.time()):
                    task = queue.pop(0)
                    detail = latest(led, "work.pending", task["id"])
                    disposition(led, task, "backoff", detail.get("reason", "waiting for next check"),
                                evidence=detail.get("evidence", []), next_retry=next_retry(led, task))
                    report.append(dict(repo=name, task=task["id"], state="backoff"))
                if not queue:
                    break
                task = queue.pop(0)
                state = run_task(led, entry, task)
                if state not in passive:
                    taken += 1
                report.append(dict(repo=name, task=task["id"], state=state))
        except (OSError, ValueError, KeyError, TypeError, LedgerError, subprocess.SubprocessError) as exc:
            led.event("work.discovery_failed", entry["repo"], {"error": str(exc)}, "work")
            report.append(dict(repo=entry["repo"], state="failed", error=str(exc)))
        finally:
            led.event("work.serviced", entry["repo"], {"at": time.time()}, "work")
            _deadline.reset(token)
    return report


def has_p0(led, repo):
    """A captured, unfinished, ready p0 issue in this repository: it preempts queue order at the next tick."""
    for task in led.tasks():
        if str(task["dedupe_key"]).lower().startswith(f"github:{repo}#") and task["state"] != "done":
            issue = latest(led, "work.issue", task["id"])
            if eligibility(issue) == "ready" and is_p0(led, task):
                return True
    return False


def next_retry(led, task):
    if task["state"] == "done":
        return 0
    row = led.conn.execute("SELECT kind, payload FROM events WHERE subject=? AND kind IN "
                           "('work.failure','work.pending') ORDER BY id DESC LIMIT 1", (task["id"],)).fetchone()
    rows = [loads(row[1], {})] if row else []
    if row and row[0] == "work.pending" and rows[0].get("rank", 0) != 0 and is_p0(led, task):
        rows = []  # a p0 preempts at the next tick a wait recorded before it was p0; failure backoff still holds
    return max([0] + [r.get("next_retry", 0) for r in rows] +
               [conveyor_backoff(led, task["dedupe_key"].removeprefix("github:"))])


def disposition(led, task, state, reason, **details):
    led.event("work.disposition", task["id"], dict(state=state, reason=reason, **details), "work")


def run_task(led, entry, task):
    state = _run_task(led, entry, task)
    detail = latest(led, "work.pending", task["id"]) if state == "pending" else {}
    if state == "failed":
        detail = latest(led, "work.failure", task["id"])
    issue = latest(led, "work.issue", task["id"])
    reason = state
    if state in ("held", "owned", "ineligible"):
        reason = f"{state}: current issue labels " + ", ".join(l["name"] for l in issue.get("labels", []))
    disposition(led, task, state, detail.get("reason", detail.get("error", reason)),
                evidence=detail.get("evidence", []), next_retry=next_retry(led, task))
    return state


def conveyor_attempts(led, subject):
    rows = led.conn.execute("SELECT payload FROM events WHERE kind='work.item_attempt'"
                            " AND source='conveyor' AND lower(subject)=? ORDER BY id", (subject.lower(),))
    steps = {}
    for row in rows:
        payload = loads(row[0], {})
        steps[payload.get("step")] = payload
    return list(steps.values())


def conveyor_backoff(led, subject):
    """Latest retry_at the conveyor recorded for work on this item. Its triage step is not
    work on the item: a triage that stands down under repository backpressure records a
    failed attempt with a 30-minute retry, and honouring that here kept a ready issue out of
    the tower for as long as the repository had parked PRs (tbs-www#310, 2026-09-11)."""
    return max([0.0] + [float(row.get("retry_at") or 0) for row in conveyor_attempts(led, subject)
                        if row.get("step") != "triage"])


def _run_task(led, entry, task):
    try:
        if task["state"] == "done":
            if _lane.get() == TOWER_LABEL and not latest(led, "work.receipt", task["id"]):
                tid = reopen(led, task["id"], "done without receipt")  # never close an unproven issue
                return "reopened" if tid else "done"
            if not latest(led, "work.closed", task["id"]):
                close_issue(led, context(led, entry, task))
            return "done"
        current = issue_now(led, entry, task)
        state = eligibility(current)
        reconcile = state == "closed" and _lane.get() != TOWER_LABEL and any(
            latest(led, "work.executing", row["id"]) for row in led.flights(task_id=task["id"]))
        if state not in ("ready", "resume") and not reconcile:
            return state
        if conveyor_backoff(led, task["dedupe_key"].removeprefix("github:")) > time.time():
            return "backoff"
        if next_retry(led, task) > time.time():
            return "backoff"
        if _lane.get() == TOWER_LABEL:
            why = tower_gate(entry, current)[1]
            if why:
                led.event("work.pending", task["id"], {"reason": f"gate: {why}", "next_retry": time.time() + 1800}, "work")
                return "blocked"
            waiting = latest(led, "work.pending", task["id"])
            if waiting.get("reason") == "in_review" and waiting.get("pr_url"):
                return tower_review(led, entry, task, waiting["pr_url"])
            return tower_execute(led, entry, task)
        return execute(led, entry, task)
    except Owned:
        return "owned"
    except (OSError, ValueError, KeyError, TypeError, LedgerError, sqlite3.Error, subprocess.SubprocessError) as exc:
        led.event("work.item_failed", task["id"], {"error": str(exc)}, "work")
        return "failed"


def workspace_available(entry):
    """An unmapped path never means the caller's current directory."""
    path = entry.get("path")
    if not path or not Path(path).is_dir():
        return False
    # Inventory-backed repositories must contain their own Git metadata; an
    # offloaded directory inside the vault is not the parent vault checkout.
    return "availability" not in entry or (Path(path) / ".git").exists()


def status(led, entries):
    return {"repositories": [dict(repo=e["repo"], path=e["path"], enabled=e["enabled"],
                                  available=workspace_available(e)) for e in entries],
            "tasks": [dict(t, disposition=latest(led, "work.disposition", t["id"]), failure=latest(led, "work.failure", t["id"]))
                      for t in led.tasks() if t["origin"] == "github-work"],
            "attempts": [dict(f) for f in led.flights()
                         if led.plan(f["plan_id"])["kind"] == "work"],
            "item_attempts": [dict(e) for e in led.events(kind="work.item_attempt")],
            "gaps": [dict(e) for e in led.events() if e["kind"] in
                     ("work.discovery_failed", "work.item_failed", "work.failure")]}


def read_status(path, entries):
    path = Path(path or default_path()).expanduser().resolve()
    coverage = [dict(repo=e["repo"], path=e["path"], enabled=e["enabled"],
                     available=workspace_available(e)) for e in entries]
    if not path.exists():
        return {"repositories": coverage, "tasks": [], "attempts": [],
                "item_attempts": [], "gaps": [{"kind": "work.no_ledger"}]}
    led = Ledger.__new__(Ledger)
    led.path = str(path)
    led.conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    led.conn.row_factory = sqlite3.Row
    try:
        return status(led, entries)
    finally:
        led.close()
