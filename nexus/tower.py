"""Tower: the one process that decides. It never does work itself.

`tick(ledger, now)` is the whole controller and is a plain function so a test can
run it, kill it, run two of them at once, and run it again. Everything it knows
comes from the ledger; nothing it learns lives anywhere else. That is what makes
`kill -9` at any instruction survivable: a restart re-derives the world.

Tick order is load bearing:

    expire leases -> budgets -> reap finished -> reconcile vanished ->
    reconcile applying landings -> quarantine -> accept tasks -> schedule -> launch

Reaping before reconciling is why a flight that finished and exited between two
ticks is read as produced rather than declared vanished.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import time

from . import flights as fl
from .ledger import Ledger, loads

DEFAULT_CONCURRENCY = 4
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_MAX_RETRIES = 2
#: a flight that is `running` with no pid yet has this long to have one recorded
#: before it is treated as never having started.
PID_GRACE_S = 5.0
LEASE_SLACK_S = 60.0


def flights_root(ledger: Ledger) -> str:
    env = os.environ.get("NEXUS_FLIGHTS")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(ledger.path)), "flights")


def _budget(plan):
    budget = loads(plan["budget"], {}) or {}
    return (
        float(budget.get("timeout_s", DEFAULT_TIMEOUT_S)),
        int(budget.get("max_retries", DEFAULT_MAX_RETRIES)),
        int(budget.get("concurrency", DEFAULT_CONCURRENCY)),
    )


def is_paused(ledger: Ledger) -> bool:
    row = ledger.last_event(("tower.paused", "tower.resumed"))
    return bool(row) and row["kind"] == "tower.paused"


def pause(ledger: Ledger, reason="operator"):
    ledger.event("tower.paused", None, {"reason": reason}, "click")


def resume(ledger: Ledger, reason="operator"):
    ledger.event("tower.resumed", None, {"reason": reason}, "click")


# ---- the tick --------------------------------------------------------------


def tick(ledger: Ledger, now=None, root=None, landing_probe=None):
    """One pass of the controller. Safe to run twice at once; safe to kill."""
    now = now if now is not None else time.time()
    root = root or flights_root(ledger)
    report = {
        "expired": 0, "timed_out": 0, "produced": 0, "failed": 0, "vanished": 0,
        "launched": 0, "scheduled": 0, "accepted": 0, "rejected": 0,
        "quarantined": 0, "retried": 0, "reconciled_landings": 0,
    }

    report["expired"] = len(ledger.expire_leases(now))
    report["timed_out"] = _enforce_budgets(ledger, now)
    reaped = _reap(ledger, now, root)
    report["produced"] += reaped["produced"]
    report["failed"] += reaped["failed"]
    report["vanished"] = _reconcile_vanished(ledger, now, root)
    report["failed"] += report["timed_out"] + report["vanished"]
    report["retried"] = _retry_exhausted(ledger, now)
    report["reconciled_landings"] = _reconcile_landings(ledger, now, landing_probe)
    report["quarantined"] = _quarantine(ledger, now)

    if is_paused(ledger):
        report["paused"] = True
        return report

    report["scheduled"] = _schedule(ledger, now)
    accepted, rejected = accept_tasks(ledger, now)
    report["accepted"], report["rejected"] = accepted, rejected
    report["launched"] = _launch(ledger, now, root)
    return report


# ---- budgets ---------------------------------------------------------------


def _enforce_budgets(ledger, now):
    killed = 0
    for flight in ledger.flights(states=("running",)):
        plan = ledger.plan(flight["plan_id"])
        timeout_s, _, _ = _budget(plan)
        started = flight["started_at"] or flight["created_at"]
        if started + timeout_s > now:
            continue
        fl.kill(flight["pid"])
        if ledger.fail(flight["id"], "timeout", f"over {timeout_s}s budget",
                       expect="running", now=now):
            _finish_failed(ledger, flight, now)
            killed += 1
    return killed


# ---- reaping ---------------------------------------------------------------


def _reap(ledger, now, root):
    """Read `result.json`, never the log. The runner decides, the child does not."""
    out = {"produced": 0, "failed": 0}
    for flight in ledger.flights(states=("running",)):
        workspace = flight["workspace"] or fl.workspace_path(root, flight["id"])
        result, error = fl.read_result(workspace)
        if error == "missing_result":
            continue
        if error is not None:
            if ledger.fail(flight["id"], error, f"unreadable {fl.RESULT_NAME}",
                           expect="running", now=now):
                _finish_failed(ledger, flight, now)
                out["failed"] += 1
            continue
        if result.get("ok"):
            if ledger.set_state(flight["id"], "produced", expect="running", now=now,
                                result=result, ended_at=now):
                for artifact in result.get("artifacts", []):
                    ledger.add_artifact(flight["id"], artifact.get("kind", "file"),
                                        os.path.join(workspace, artifact.get("ref", "")),
                                        artifact.get("sha"), now=now)
                ledger.release_leases(flight["id"], now=now)
                if flight["task_id"]:
                    ledger.set_task_state(flight["task_id"], "done", decided_by="tower policy",
                                          expect="running", now=now)
                out["produced"] += 1
            continue
        err = result.get("error") or {}
        code = err.get("code") or "unknown"
        if ledger.fail(flight["id"], code, str(err.get("detail", "")), expect="running",
                       now=now, cost=result.get("cost")):
            _finish_failed(ledger, flight, now)
            out["failed"] += 1
    return out


def _reconcile_vanished(ledger, now, root):
    """Narrow: only a row whose process is provably gone with nothing to show."""
    gone = 0
    for flight in ledger.flights(states=("running",)):
        if fl.alive(flight["pid"]):
            continue
        if flight["pid"] is None and (flight["started_at"] or now) + PID_GRACE_S > now:
            continue
        workspace = flight["workspace"] or fl.workspace_path(root, flight["id"])
        if os.path.exists(fl.result_path(workspace)):
            continue  # the next reap owns it
        if ledger.fail(flight["id"], "vanished", "process gone with no result",
                       expect="running", now=now):
            _finish_failed(ledger, flight, now)
            gone += 1
    return gone


def _finish_failed(ledger, flight, now):
    """A failed flight leaves nothing behind: its process, its leases, its workspace."""
    fl.kill(flight["pid"])
    ledger.release_leases(flight["id"], now=now)
    workspace = flight["workspace"]
    if workspace and os.path.isdir(workspace):
        shutil.rmtree(workspace, ignore_errors=True)


def _retry_exhausted(ledger, now):
    """Retry per budget; when the budget is spent the task is abandoned, not looped."""
    made = 0
    for flight in ledger.flights(states=("failed",), limit=200):
        task_id = flight["task_id"]
        if not task_id:
            continue
        task = ledger.task(task_id)
        if task is None or task["state"] != "running":
            continue
        plan = ledger.plan(flight["plan_id"])
        _, max_retries, _ = _budget(plan)
        if flight["attempt"] > max_retries or plan["quarantined_at"] is not None:
            ledger.set_task_state(task_id, "abandoned", decided_by="tower policy",
                                  expect="running", reason="retries exhausted", now=now)
            continue
        new = ledger.create_flight(flight["plan_id"], task_id=task_id,
                                   attempt=flight["attempt"] + 1, now=now,
                                   unique_for_task=True)
        if new:
            made += 1
    return made


def _reconcile_landings(ledger, now, probe=None):
    """`applying` is the one landing state that outlives a crash mid-push.

    Without a probe (step 3 supplies the GitHub one) tower only records that the
    row needs reconciling. It never guesses that a push happened.
    """
    rows = ledger.landings(states=("applying",))
    if not rows:
        return 0
    done = 0
    for row in rows:
        if probe is None:
            ledger.event("landing.needs_reconcile", row["flight_id"],
                         {"landing": row["id"], "expected_sha": row["expected_sha"]},
                         "tower", now)
            continue
        tip = probe(row["target"])
        if tip and tip == row["expected_sha"]:
            ledger.apply_landing(row["id"], tip, now=now)
            done += 1
    return done


def _quarantine(ledger, now):
    """N consecutive failures and the plan stops, loudly, instead of failing forever."""
    count = 0
    for plan in ledger.plans(runnable_only=True):
        _, max_retries, _ = _budget(plan)
        limit = max(1, max_retries)
        recent = ledger.flights(plan_id=plan["id"], limit=limit)
        terminal = [f for f in recent if f["state"] in ("failed", "produced", "landed")]
        if len(terminal) < limit:
            continue
        if all(f["state"] == "failed" for f in terminal[:limit]):
            ledger.quarantine_plan(plan["id"], f"{limit} consecutive failures", now=now)
            count += 1
    return count


# ---- scheduling ------------------------------------------------------------


def due(ledger, plan, now):
    """Is this plan due? Returns (due, dedupe_key, trigger_event_id).

    The dedupe key names the OCCASION, not the plan, so a tick that runs twice
    for the same second creates one task and rejects the other as a duplicate.
    """
    schedule = loads(plan["schedule"], {}) or {}
    if "every" in schedule:
        every = float(schedule["every"])
        if every <= 0:
            return False, None, None
        last = ledger.conn.execute(
            "SELECT MAX(created_at) AS t FROM tasks WHERE plan_id=?", (plan["id"],)
        ).fetchone()["t"]
        if last is not None and now - last < every:
            return False, None, None
        return True, f"plan:{plan['id']}:every:{int(now // every)}", None
    if "at" in schedule:
        hour, _, minute = str(schedule["at"]).partition(":")
        local = datetime.datetime.fromtimestamp(now)
        target = local.replace(hour=int(hour), minute=int(minute or 0), second=0, microsecond=0)
        if local < target:
            return False, None, None
        return True, f"plan:{plan['id']}:at:{target.date()}T{schedule['at']}", None
    if "on" in schedule:
        seen = ledger.conn.execute(
            "SELECT MAX(json_extract(payload,'$.trigger_event_id')) AS t FROM events"
            " WHERE kind='plan.triggered' AND subject=?", (plan["id"],)
        ).fetchone()["t"]
        rows = ledger.events(kind=schedule["on"], after_id=int(seen or 0))
        if not rows:
            return False, None, None
        event = rows[0]
        return True, f"plan:{plan['id']}:event:{event['id']}", event["id"]
    return False, None, None


def _schedule(ledger, now):
    """A due plan produces a TASK. Flights only ever come from tasks."""
    made = 0
    for plan in ledger.plans(runnable_only=True):
        ready, key, trigger = due(ledger, plan, now)
        if not ready:
            continue
        if ledger.live_task_with_key(key) is not None:
            continue
        ledger.add_task(
            title=f"run {plan['name']}", origin="plan", plan_id=plan["id"],
            objective_id=plan["objective_id"], reason="schedule due",
            risk="low", dedupe_key=key, now=now)
        if trigger is not None:
            ledger.event("plan.triggered", plan["id"], {"trigger_event_id": trigger},
                         "tower", now)
        made += 1
    return made


# ---- task acceptance -------------------------------------------------------


def accept_tasks(ledger, now=None):
    """Policy, not enthusiasm: accept what the plan allows and nothing twice."""
    now = now if now is not None else time.time()
    accepted = rejected = 0
    for task in ledger.tasks(states=("candidate", "ranked")):
        plan = ledger.plan(task["plan_id"]) if task["plan_id"] else None
        policy = (loads(plan["resolution_policy"], {}) if plan else {}) or {}
        if plan is None or not plan["enabled"] or plan["quarantined_at"] is not None \
                or policy.get("may_accept", True) is False:
            if ledger.set_task_state(task["id"], "rejected_policy", decided_by="tower policy",
                                     expect=task["state"], reason="plan policy", now=now):
                rejected += 1
            continue
        if ledger.live_task_with_key(task["dedupe_key"], exclude=task["id"]) is not None:
            if ledger.set_task_state(task["id"], "rejected_duplicate",
                                     decided_by="tower policy", expect=task["state"],
                                     reason=task["dedupe_key"], now=now):
                rejected += 1
            continue
        if ledger.set_task_state(task["id"], "accepted", decided_by="tower policy",
                                 expect=task["state"], now=now):
            accepted += 1

    # accepted-with-no-flight is the state a crash between the two writes leaves.
    for task in ledger.tasks(states=("accepted",)):
        if not task["plan_id"]:
            continue
        flight = ledger.create_flight(task["plan_id"], task_id=task["id"], now=now,
                                      unique_for_task=True)
        if flight or ledger.flights(task_id=task["id"]):
            ledger.set_task_state(task["id"], "running", decided_by="tower policy",
                                  expect="accepted", now=now)
    return accepted, rejected


# ---- launching -------------------------------------------------------------


def _launch(ledger, now, root):
    launched = 0
    running = len(ledger.flights(states=("running",)))
    queued = sorted(ledger.flights(states=("queued",)), key=lambda r: r["created_at"])
    for flight in queued:
        plan = ledger.plan(flight["plan_id"])
        timeout_s, _, concurrency = _budget(plan)
        if running >= concurrency:
            break
        resources = loads(plan["resources"], []) or []
        if resources and not ledger.acquire_leases(
                flight["id"], resources, now + timeout_s + LEASE_SLACK_S, now=now):
            continue  # someone else holds the target; try again next tick
        workspace = fl.workspace_path(root, flight["id"])
        os.makedirs(workspace, exist_ok=True)
        if not ledger.set_state(flight["id"], "running", expect="queued", now=now,
                                workspace=workspace, started_at=now):
            ledger.release_leases(flight["id"], now=now)
            continue
        if flight["task_id"]:
            ledger.deliver_messages(flight["task_id"], flight["id"], now=now)
        pid = _spawn(ledger, plan, flight["id"], workspace, timeout_s)
        if pid is None:
            ledger.fail(flight["id"], "spawn_failed", "could not start the runner",
                        expect="running", now=now)
            _finish_failed(ledger, ledger.flight(flight["id"]), now)
            continue
        ledger.set_pid(flight["id"], pid, now=now)
        running += 1
        launched += 1
    return launched


def _spawn(ledger, plan, flight_id, workspace, timeout_s):
    """Detached, in its own session, so tower dying does not take the work with it."""
    inputs = loads(plan["inputs"], {}) or {}
    cmd = inputs.get("cmd")
    if plan["kind"] != "script" or not cmd:
        return None
    outputs = loads(plan["outputs"], []) or []
    argv = [sys.executable, "-m", "nexus", "flight-run", "--workspace", workspace,
            "--cmd", cmd, "--timeout", str(timeout_s)]
    for name in outputs:
        argv += ["--output", name]
    env = dict(os.environ)
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = package_parent + os.pathsep + env.get("PYTHONPATH", "")
    try:
        with open(os.path.join(workspace, fl.LOG_NAME), "ab") as log:
            proc = subprocess.Popen(argv, cwd=package_parent, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, start_new_session=True, env=env)
    except OSError:
        return None
    return proc.pid


# ---- run loop and views ----------------------------------------------------


def run(ledger, interval=5.0, iterations=None, root=None):
    count = 0
    while iterations is None or count < iterations:
        try:
            tick(ledger, root=root)
        except Exception as exc:  # a bad tick must never end the tower
            ledger.event("tower.tick_error", None, {"error": repr(exc)}, "tower")
        count += 1
        if iterations is None or count < iterations:
            time.sleep(interval)
    return count


def status(ledger, limit=15):
    """What the Office draws, as text: one read of the ledger, no other source."""
    lines = []
    lines.append(f"ledger  {ledger.path}  v{ledger.user_version()}"
                 f"{'  PAUSED' if is_paused(ledger) else ''}")
    problems = ledger.integrity_check()
    lines.append("integrity  " + ("ok" if not problems else f"{len(problems)} problems"))
    for problem in problems[:5]:
        lines.append(f"  ! {problem}")

    lines.append("")
    lines.append(f"{'PLAN':24} {'KIND':8} {'SCHEDULE':16} STATE")
    for plan in ledger.plans():
        state = "quarantined" if plan["quarantined_at"] else (
            "enabled" if plan["enabled"] else "disabled")
        lines.append(f"{plan['name'][:24]:24} {plan['kind'][:8]:8} "
                     f"{json.dumps(loads(plan['schedule'], {}))[:16]:16} {state}")

    lines.append("")
    lines.append(f"{'FLIGHT':20} {'PLAN':18} {'STATE':10} {'ATT':3} ERROR")
    for flight in ledger.flights(limit=limit):
        plan = ledger.plan(flight["plan_id"])
        result = loads(flight["result"], {}) or {}
        error = ((result.get("error") or {}).get("code") or "") if result else ""
        lines.append(f"{flight['id'][:20]:20} {plan['name'][:18]:18} "
                     f"{flight['state']:10} {flight['attempt']:<3} {error}")

    open_gates = ledger.open_gates()
    if open_gates:
        lines.append("")
        lines.append("NEEDS YOU")
        for gate in open_gates:
            lines.append(f"  {gate['id']}  {gate['question']}")
    return "\n".join(lines)
