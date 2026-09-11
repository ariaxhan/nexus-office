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
import time

from . import flights
from .ledger import Ledger, LedgerError, TERMINAL, default_path, loads, new_id


_deadline = ContextVar("work_deadline", default=None)


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
    if labels.intersection({"direct", "claimed", "in-progress", "in progress"}):
        return "owned"
    if labels.intersection({"hold", "on-hold", "blocked", "cancelled", "canceled"}):
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
    led.event("work.pending", tid, dict(result, next_retry=retry), "work")
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
    issue = latest(led, "work.issue", task["id"])
    labels = {label["name"].lower() for label in issue.get("labels", [])}
    bonus = 86400 * (bool(led.flights(task_id=task["id"])) +
                     bool(labels & {"urgent", "p0", "p1", "in-pr", "in pr"}))
    return task["created_at"] - bonus


def eligible(led, entries, repo):
    """Enabled repositories, or none: `nexus plans disable github-work` stops every one at once."""
    entries = [e for e in entries if e["enabled"] and (not repo or e["repo"] == repo.lower())]
    if led.plan(plan(led))["enabled"]:
        return entries
    led.event("work.disabled", "github-work", {"repositories": len(entries)}, "work")
    return []


def run(led, entries, repo=None, *, budget_s=300, max_items=20):
    if not math.isfinite(budget_s) or not 1 <= budget_s <= 3600 or not 1 <= max_items <= 100:
        raise WorkError("budget must be 1..3600 seconds; max_items must be 1..100")
    if repo and repo.lower() not in {e["repo"] for e in entries}:
        raise WorkError(f"unknown repository: {repo}")
    entries = eligible(led, entries, repo)
    # Least recently serviced repositories first, using existing ledger receipts.
    entries.sort(key=lambda e: latest(led, "work.serviced", e["repo"]).get("at", 0))
    deadline = time.monotonic() + budget_s
    report, queues = [], {}
    count = 0
    while entries and count < max_items and time.monotonic() < deadline:
        for index, entry in enumerate(entries[:]):
            if count >= max_items or time.monotonic() >= deadline:
                break
            token = _deadline.set(min(deadline, time.monotonic() +
                                      (deadline - time.monotonic()) / min(len(entries) - index, max_items - count)))
            try:
                name = entry["repo"]
                if name not in queues:
                    discover(led, entry)
                    queues[name], passive = selection_queue(led, entry)
                    report.extend(passive)
                queue = queues[name]
                while (queue and eligibility(latest(led, "work.issue", queue[0]["id"])) in ("ready", "resume")
                       and next_retry(led, queue[0]) > time.time()):
                    task = queue.pop(0)
                    detail = latest(led, "work.pending", task["id"])
                    disposition(led, task, "backoff", detail.get("reason", "waiting for next check"),
                                evidence=detail.get("evidence", []), next_retry=next_retry(led, task))
                    report.append(dict(repo=name, task=task["id"], state="backoff"))
                if not queue:
                    continue
                task = queue.pop(0)
                state = run_task(led, entry, task)
                if state not in ("held", "owned", "ineligible", "closed", "backoff"):
                    count += 1
                report.append(dict(repo=name, task=task["id"], state=state))
            except (OSError, ValueError, KeyError, TypeError, LedgerError, subprocess.SubprocessError) as exc:
                led.event("work.discovery_failed", entry["repo"], {"error": str(exc)}, "work")
                report.append(dict(repo=entry["repo"], state="failed", error=str(exc)))
                queues[entry["repo"]] = []
            finally:
                led.event("work.serviced", entry["repo"], {"at": time.time()}, "work")
                _deadline.reset(token)
        entries = [e for e in entries if queues.get(e["repo"])]
    return report


def next_retry(led, task):
    if task["state"] == "done":
        return 0
    row = led.conn.execute("SELECT payload FROM events WHERE subject=? AND kind IN "
                           "('work.failure','work.pending') ORDER BY id DESC LIMIT 1", (task["id"],)).fetchone()
    rows = [loads(row[0], {})] if row else []
    return max([0] + [r.get("next_retry", 0) for r in rows] +
               [float(r.get("retry_at") or 0) for r in conveyor_attempts(led, task["dedupe_key"].removeprefix("github:"))])


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


def _run_task(led, entry, task):
    try:
        if task["state"] == "done":
            if not latest(led, "work.closed", task["id"]):
                close_issue(led, context(led, entry, task))
            return "done"
        state = eligibility(issue_now(led, entry, task))
        reconcile = state == "closed" and any(
            latest(led, "work.executing", row["id"]) for row in led.flights(task_id=task["id"]))
        if state not in ("ready", "resume") and not reconcile:
            return state
        conveyor = conveyor_attempts(led, task["dedupe_key"].removeprefix("github:"))
        if any(float(row.get("retry_at") or 0) > time.time() for row in conveyor):
            return "backoff"
        if next_retry(led, task) > time.time():
            return "backoff"
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
