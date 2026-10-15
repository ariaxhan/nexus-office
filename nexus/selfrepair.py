"""The self-repair loop (issue #273 -- Aria's number one rule, 2026-10-14).

"We need to fix the actual system so it repairs it always." A fault, a
repeated failure, or a quarantine triggers the system's own repair in the
same motion. Quarantine is a repair-pending state, never an end state: the
ledger will not record one without a live repair task attached (shape 2),
the launch path refuses a dead pointer into repair instead of burning
retries on it (shape 3), and no broken-state word reaches Aria except
behind a live repair or as the tier-3 escalation (shape 1, surface_guard).

THE PRE-AUTHORIZED ENVELOPE (standing grant, Aria 2026-10-14 17:11 PT):
inside the envelope, repair is pre-authorized ALWAYS -- no ask at any tier,
ever. The envelope is: correct config values and binary paths (each
correction verified: the new target exists, is executable, and for an
interpreter answers --version), clear stale state this mechanism owns,
restart the worker, repair dependencies (reinstall / re-pin to the
recorded good version), and re-run. Outside the envelope is only: money
out, contracts, her voice, irreversible acts. A fix outside the envelope
is an escalation, never an improvisation.

Proof: after a fix, the worker is re-run; "repaired" means the worker's
own completion receipt (a produced/landed flight, a jobrun proof rc=0).
Nothing else counts. A failed attempt re-opens with its evidence; the
second failure escalates to the owning coordinator (a comment on the
mechanism's issue); the third surfaces to Aria with the full history, in
the only shape allowed: what broke, what was tried, the single thing only
she can do.

Every repair closes only with the recurrence guard for its fault class
installed. For the path-rot class the guard is the launch preflight in
tower._launch and jobrun section 1c: the class cannot reach quarantine
again, because the pointer is refused before the first failure.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import surface_guard
from . import terminal
from .ledger import loads

REPAIR_PLAN_NAME = "nexus-self-repair"
ISSUE_REF = "ariaxhan/nexus-office#273"
ISSUE_NUMBER = "273"
MAX_ATTEMPTS = 3
SIGNATURE_DEAD_POINTER = "dead-pointer"

_LIVE_FLIGHT_STATES = ("queued", "running", "verifying", "verified", "landing", "resolving")
_META_CHARS = set("$`*?[]{}~")


# ---- the repair plan ------------------------------------------------------

def repair_plan(ledger):
    return ledger.plan_by_name(REPAIR_PLAN_NAME)


def ensure_repair_plan(ledger):
    """The one plan whose flights run the drain. Seeded lazily, by the first
    repair that needs it, so ledgers that never fault never grow it."""
    plan = repair_plan(ledger)
    if plan is not None:
        return plan
    pid = ledger.add_plan(
        REPAIR_PLAN_NAME, kind="script", schedule={},
        inputs={"cmd": f"{sys.executable} -m nexus repair-drain",
                "persistent_task": True},
        outputs=["repair-report.json"],
        budget={"timeout_s": 900, "max_retries": 0, "concurrency": 2})
    return ledger.plan(pid)


# ---- dead pointers (the path-rot class) -----------------------------------

_WRAPPERS = {"timeout", "nice", "nohup", "env", "stdbuf", "command"}
_INTERPRETERS = {"sh", "bash", "zsh", "dash", "node", "ruby", "perl",
                 "osascript"}
_SEPARATORS = {";", "|", "&", "&&", "||"}
_REDIRECTS = {">", ">>", "<", "2>", "2>>"}


def _judged_paths(tokens):
    """The pointer positions in one command segment: the program itself,
    and -- when the program is a shell/interpreter -- its script argument.

    Nothing else is judged. A command's other arguments are data: outputs
    it will create, inputs another step owns, values behind variables. A
    launch guard that judged data would refuse healthy work (a plan whose
    command names the file it is about to write), which is a worse failure
    than the one it prevents. The class that parks plans -- the #272
    disease -- lives exactly in the judged positions: the interpreter and
    script pointers the launch cannot proceed without."""
    judged = []
    index = 0
    while index < len(tokens):  # walk to the program, through wrappers
        token = tokens[index]
        if any(ch in token for ch in _META_CHARS):
            return judged
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token, re.S):
            index += 1  # an env assignment in program position
            continue
        if token.startswith("-"):
            index += 1  # a wrapper's flag
            continue
        base = os.path.basename(token)
        if base in _WRAPPERS:
            index += 1
            if base == "timeout":  # its duration argument is not a program
                while index < len(tokens) and (
                        tokens[index].startswith("-")
                        or re.fullmatch(r"[0-9]+[smhd]?", tokens[index])):
                    index += 1
            continue
        break
    if index >= len(tokens):
        return judged
    program = tokens[index]
    if program.startswith("/"):
        judged.append(program)
    base = os.path.basename(program)
    if base in _INTERPRETERS or re.fullmatch(r"python(\d+(\.\d+)*)?", base):
        for token in tokens[index + 1:]:  # the script is the first bare word
            if token in ("-c", "-e"):
                break  # inline code follows; there is no script pointer
            if token.startswith("-"):
                continue
            if token.startswith("/"):
                judged.append(token)
            break
    return judged


def dead_pointers(cmd):
    """The judged pointers in a shell command string that do not exist.

    A dead judged pointer is unambiguous: the launch cannot proceed, and
    proceeding anyway is how a plan burns five timeouts on a path from
    another machine's layout."""
    if not cmd:
        return []
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=";|&")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        tokens = cmd.split()
    segments, current = [], []
    for token in tokens:
        if token in _SEPARATORS:
            segments.append(current)
            current = []
        elif token in _REDIRECTS:
            continue
        else:
            current.append(token)
    segments.append(current)
    missing = []
    for segment in segments:
        for path in _judged_paths(segment):
            if len(path) > 1 and not os.path.exists(path) and path not in missing:
                missing.append(path)
    return missing


def dead_pointers_in_argv(argv):
    """The same judgment for a registry command array (jobrun jobs)."""
    tokens = [el for el in argv if isinstance(el, str)]
    return [path for path in _judged_paths(tokens)
            if len(path) > 1 and not os.path.exists(path)]


def _answers(path):
    """Verification for a correction target: an interpreter must answer."""
    if "python" in os.path.basename(path):
        try:
            proc = subprocess.run([path, "--version"], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0
    return True  # existence + executable is the check for anything else


def verified_replacement(path):
    """The one verified stand-in for a dead pointer, or None.

    A python-shaped basename resolves to this host's own interpreter first
    (the substrate's known-good), then the standard bin dirs. The winner
    must exist, be executable, and answer. No verified candidate, no
    correction: the repair fails honestly instead of guessing."""
    base = os.path.basename(path.rstrip("/"))
    if not base:
        return None
    candidates = []
    if re.fullmatch(r"python(\d+(\.\d+)*)?", base):
        candidates.append(sys.executable)
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
                      "/bin", "/usr/sbin", "/sbin"):
        candidates.append(os.path.join(directory, base))
    found = shutil.which(base)
    if found:
        candidates.append(found)
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK) \
                and _answers(candidate):
            return candidate
    return None


def correct_cmd(cmd, corrections):
    """Rewrite dead pointers in a command string, at token boundaries only."""
    out = cmd
    for old, new in corrections.items():
        out = re.sub(rf"(?<![\w/.-]){re.escape(old)}(?![\w/.-])", new, out)
    return out


# ---- opening repairs ------------------------------------------------------

def failure_packet(ledger, plan, reason, signature):
    """What a repair worker is handed: the failing thing, verbatim."""
    flights = ledger.flights(plan_id=plan["id"], states=("failed",), limit=1)
    last_error = None
    if flights:
        result = loads(flights[0]["result"], {}) or {}
        last_error = result.get("error")
    inputs = loads(plan["inputs"], {}) or {}
    return {"kind": "plan", "plan_id": plan["id"], "plan_name": plan["name"],
            "cmd": inputs.get("cmd"), "reason": reason, "signature": signature,
            "last_error": last_error, "dead": dead_pointers(inputs.get("cmd") or ""),
            "host": platform.node(), "python": sys.executable,
            "opened_at": time.time()}


def open_repair(ledger, packet, now=None):
    """Create (or find) the live repair task for a failure packet."""
    now = now if now is not None else time.time()
    ensure_repair_plan(ledger)
    subject = packet.get("plan_id") or packet.get("job_id")
    key = f"repair:{packet['kind']}:{subject}:{packet['signature']}"
    live = ledger.live_task_with_key(key)
    if live is not None:
        return live["id"]
    tid = ledger.add_task(
        title=f"Repair {packet.get('plan_name') or packet.get('job_id')}: "
              f"{packet['signature']}",
        origin="self-repair", plan_id=repair_plan(ledger)["id"],
        reason=json.dumps(packet, sort_keys=True), risk="low",
        dedupe_key=key, unique_live=True, now=now)
    if tid is None:  # a racing writer won; its task is the repair
        live = ledger.live_task_with_key(key)
        return live["id"] if live is not None else None
    ledger.event("repair.opened", subject,
                 {"task_id": tid, "signature": packet["signature"],
                  "issue": ISSUE_REF}, "self-repair", now)
    return tid


def attach_quarantine(ledger, plan, reason, signature="consecutive-failures", now=None):
    """THE transition (shape 2): quarantine and its repair are one act.

    The repair task exists first; the quarantine record then names it, in
    the same motion. There is no path from here to a bare quarantine --
    the ledger refuses one (migration v4 + quarantine_plan's own check)."""
    now = now if now is not None else time.time()
    for other in ledger.tasks():
        if other["origin"] != "self-repair":
            continue
        if other["state"] not in ("candidate", "ranked", "accepted", "running"):
            continue
        if (loads(other["reason"], {}) or {}).get("plan_id") == plan["id"]:
            # one live repair per plan: a repeat-signature repair already
            # working this plan becomes the quarantine's repair
            ledger.quarantine_plan(plan["id"], reason,
                                   repair_task_id=other["id"], now=now)
            return other["id"]
    packet = failure_packet(ledger, plan, reason, signature)
    tid = open_repair(ledger, packet, now=now)
    ledger.quarantine_plan(plan["id"], reason, repair_task_id=tid, now=now)
    return tid


def adopt(ledger, now):
    """Legacy quarantines (parked before this mechanism) get their repair.

    Runs in the tick. A quarantine the store allowed before v4 has no
    repair attached; attaching one is the same transition, applied to the
    backlog exactly once each."""
    count = 0
    for plan in ledger.plans():
        if plan["quarantined_at"] is None or plan["repair_task_id"]:
            continue
        packet = failure_packet(ledger, plan, "quarantined before self-repair",
                                "legacy-quarantine")
        tid = open_repair(ledger, packet, now=now)
        if tid:
            ledger.attach_repair(plan["id"], tid, now=now)
            count += 1
    return count


def triggers(ledger, now):
    """The second trigger: >=3 consecutive failures with one signature open
    a repair even before quarantine (the plan keeps flying; the repair
    diagnoses in parallel). One live repair per signature, by dedupe key."""
    count = 0
    for plan in ledger.plans(runnable_only=True):
        if plan["kind"] == "work" or plan["name"] == REPAIR_PLAN_NAME:
            continue
        released = ledger.events(kind="plan.unquarantined", subject=plan["id"])
        cutoff = max((e["ts"] for e in released), default=0)
        proof = proof_task_ids(ledger, plan["id"])
        history = [f for f in ledger.flights(plan_id=plan["id"], limit=6)
                   if f["state"] in ("failed", "produced", "landed")
                   and (f["created_at"] or 0) > cutoff
                   and f["task_id"] not in proof][:3]
        if len(history) < 3 or any(f["state"] != "failed" for f in history):
            continue
        codes = {((loads(f["result"], {}) or {}).get("error") or {}).get("code")
                 for f in history}
        if len(codes) != 1 or None in codes:
            continue
        signature = f"repeat:{codes.pop()}"
        key = f"repair:plan:{plan['id']}:{signature}"
        if ledger.live_task_with_key(key) is not None:
            continue
        open_repair(ledger, failure_packet(ledger, plan,
                                           "3 consecutive failures, one signature",
                                           signature), now=now)
        count += 1
    return count


def attempts(ledger, task_id):
    return len(ledger.events(kind="repair.attempt_failed", subject=task_id))


def proof_task_ids(ledger, plan_id):
    """The repair loop's proof tasks on a plan. Proof flights are the
    mechanism re-running the worker; they are accounted by the repair
    lifecycle (attempts), never by the plan's own failure history -- a
    proof that fails must not extend the plan's failure streaks, or the
    mechanism's own re-runs would bury the plan they are repairing."""
    return {t["id"] for t in ledger.tasks()
            if t["plan_id"] == plan_id
            and (t["dedupe_key"] or "").startswith("repair-proof:")}


# ---- the drain: repair workers, flown as flights of the repair plan --------

def _fail(ledger, task, why, now):
    n = attempts(ledger, task["id"]) + 1
    ledger.event("repair.attempt_failed", task["id"],
                 {"attempt": n, "why": str(why)[:400]}, "self-repair", now)
    ledger.event("repair.retry_due", task["id"], {"attempt": n}, "self-repair", now)
    return "failed", str(why)[:400]


def _schedule_proof(ledger, task, plan, now):
    """The proof is a re-run of the worker itself, on the normal machinery."""
    if plan["quarantined_at"] is not None:
        ledger.unquarantine_plan(plan["id"], now=now)
    key = f"repair-proof:{task['id']}:{attempts(ledger, task['id'])}"
    proof_task = ledger.add_scheduled_task(plan, key, now)
    ledger.event("repair.awaiting_proof", task["id"],
                 {"proof_key": key, "proof_task_id": proof_task}, "self-repair", now)
    return "fixed", f"proof scheduled: {key}"


def _drain_plan(ledger, task, packet, now):
    plan = ledger.plan(packet.get("plan_id") or "")
    if plan is None:
        return _fail(ledger, task, "target plan no longer exists", now)
    if _person_released(ledger, plan["id"], task):
        return "skipped", "plan released after this repair opened; standing down"
    awaiting = ledger.events(kind="repair.awaiting_proof", subject=task["id"])
    if awaiting:
        payload = loads(awaiting[-1]["payload"], {}) or {}
        proof_task = ledger.task(payload.get("proof_task_id") or "")
        proof_flights = (ledger.flights(task_id=proof_task["id"])
                         if proof_task else [])
        live = proof_task is not None and proof_task["state"] in (
            "candidate", "ranked", "accepted", "running")
        flying = any(f["state"] in ("queued", "running") for f in proof_flights)
        if live or flying:
            return "skipped", "proof already scheduled"
        # the recorded proof settled without completing the repair (it
        # failed): this pass is a retry -- diagnose again, proof again
    inputs = loads(plan["inputs"], {}) or {}
    cmd = inputs.get("cmd") or ""
    missing = dead_pointers(cmd)
    if missing:
        corrections = {}
        for path in missing:
            replacement = verified_replacement(path)
            if replacement is None:
                return _fail(ledger, task,
                             f"no verified replacement for dead path {path}", now)
            corrections[path] = replacement
        inputs["cmd"] = correct_cmd(cmd, corrections)
        ledger.set_plan_inputs(plan["id"], inputs, now=now)
        ledger.event("repair.fixed", task["id"],
                     {"corrections": corrections, "signature": packet.get("signature")},
                     "self-repair", now)
        return _schedule_proof(ledger, task, plan, now)
    if packet.get("signature") == "stale-state":
        owned = [p for p in packet.get("stale_paths", [])
                 if os.path.basename(p).startswith("nexus-repair-")]
        for path in owned:
            with contextlib.suppress(OSError):
                os.remove(path)
        if owned:
            ledger.event("repair.fixed", task["id"],
                         {"cleared": owned, "signature": "stale-state"},
                         "self-repair", now)
    # Nothing to correct: the fault may already be gone (a healed volume, a
    # manual fix). The proof re-run decides -- repaired means the receipt.
    return _schedule_proof(ledger, task, plan, now)


def _drain_job(ledger, task, packet, now):
    """A jobrun job: correct its registry command (vault source), sync, and
    prove by running the job itself. The registry is config; correcting a
    verified binary path inside it is inside the envelope."""
    registry_path, job_id = packet.get("registry"), packet.get("job_id")
    jobctl = packet.get("jobctl")
    try:
        with open(registry_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        return _fail(ledger, task, f"registry unreadable: {exc}", now)
    target = None
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("id") == job_id:
            target = (index, row)
            break
    if target is None:
        return _fail(ledger, task, f"job {job_id} not in registry", now)
    index, row = target
    command = row.get("command") or []
    missing = dead_pointers_in_argv(command)
    if missing:
        corrections = {}
        for path in missing:
            replacement = verified_replacement(path)
            if replacement is None:
                return _fail(ledger, task,
                             f"no verified replacement for dead path {path}", now)
            corrections[path] = replacement
        line = lines[index]
        for old, new in corrections.items():
            line = line.replace(json.dumps(old), json.dumps(new))
        lines[index] = line
        tmp = registry_path + ".tmp-repair"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(tmp, registry_path)
        ledger.event("repair.fixed", task["id"],
                     {"corrections": corrections, "registry": registry_path},
                     "self-repair", now)
        sync = subprocess.run([jobctl, "sync"], capture_output=True, text=True,
                              timeout=120)
        if sync.returncode != 0:
            return _fail(ledger, task,
                         f"jobctl sync rc={sync.returncode}: {sync.stderr[-200:]}", now)
    try:
        proof = subprocess.run([jobctl, "try", job_id], capture_output=True,
                               text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return _fail(ledger, task, "proof run (jobctl try) exceeded 300s", now)
    if proof.returncode != 0:
        return _fail(ledger, task,
                     f"proof run rc={proof.returncode}: {(proof.stderr or proof.stdout)[-200:]}",
                     now)
    ledger.set_task_state(task["id"], "done", decided_by="self-repair",
                          expect="running", reason="job proof run rc=0", now=now,
                          evidence={"kind": "delivered",
                                    "result": {"verified": True,
                                               "evidence": [f"jobctl try {job_id} rc=0"]}})
    ledger.event("repair.completed", task["id"],
                 {"receipt": f"jobctl try {job_id} rc=0", "issue": ISSUE_REF},
                 "self-repair", now)
    return "fixed", f"job {job_id} proof rc=0"


def _own_task_id(ledger):
    """The drain flies as a flight; its workspace names the flight, the
    flight names the task. Outside a flight (an operator's CLI run) there
    is no own task and the drain sweeps what is open."""
    flight = ledger.flight(os.path.basename(os.getcwd()))
    return flight["task_id"] if flight else None


def drain(ledger, now=None, only_task_id=None):
    """One drain pass: work the open repair tasks. Runs inside the repair
    plan's flights (one task per flight) and from `nexus repair-drain`."""
    now = now if now is not None else time.time()
    if only_task_id is None:
        only_task_id = _own_task_id(ledger)
    report = {"fixed": [], "failed": [], "skipped": []}
    tasks = [t for t in ledger.tasks(states=("accepted", "running"))
             if t["origin"] == "self-repair"]
    for task in tasks:
        if only_task_id and task["id"] != only_task_id:
            continue
        packet = loads(task["reason"], {}) or {}
        try:
            if packet.get("kind") == "job":
                status, detail = _drain_job(ledger, task, packet, now)
            else:
                status, detail = _drain_plan(ledger, task, packet, now)
        except Exception as exc:  # noqa: BLE001 - a drain error is an attempt
            status, detail = _fail(ledger, task, f"drain error: {exc!r}", now)
        report[status].append({"task": task["id"], "detail": detail})
    with contextlib.suppress(OSError):
        with open(os.path.join(os.getcwd(), "repair-report.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(report, handle, indent=1, sort_keys=True)
    return report


# ---- verification + escalation: the tick-side lifecycle owner --------------

def _person_released(ledger, plan_id, task):
    """The release ts that stands a repair down, or 0.

    The release that outranks the loop is a PERSON's (`nexus plans
    release`). The mechanism's own unquarantine-for-proof (in
    _schedule_proof, so the worker can re-run) is not one: when that
    proof fails, the plan is re-quarantined in the same motion, so on
    the plan's own timeline the release is superseded. A release counts
    only when the newest plan.unquarantined postdates both this
    repair's opening and the newest plan.quarantined."""
    released = ledger.events(kind="plan.unquarantined", subject=plan_id)
    if not released:
        return 0
    release_ts = max(e["ts"] for e in released)
    if release_ts <= (task["created_at"] or 0):
        return 0
    quarantined = ledger.events(kind="plan.quarantined", subject=plan_id)
    if quarantined and max(e["ts"] for e in quarantined) >= release_ts:
        return 0
    return release_ts


def verify(ledger, now=None):
    """Resolve proofs, count attempts, escalate, and re-fly failed repairs.

    Runs in the tower tick (never inside a flight), which is what makes a
    crashed drain flight visible: a repair task running with a dead latest
    flight and no recorded attempt gets the attempt recorded here."""
    now = now if now is not None else time.time()
    out = {"completed": 0, "retried": 0, "escalated": 0, "proofs_failed": 0}
    for task in ledger.tasks(states=("running",)):
        if task["origin"] != "self-repair":
            continue
        tid = task["id"]
        events = ledger.events(subject=tid)
        awaiting = [e for e in events if e["kind"] == "repair.awaiting_proof"]
        if awaiting:
            payload = loads(awaiting[-1]["payload"], {}) or {}
            proof_flights = (ledger.flights(task_id=payload.get("proof_task_id"))
                             if payload.get("proof_task_id") else [])
            if not proof_flights:
                continue  # the proof has not flown yet
            latest = proof_flights[0]
            if latest["state"] in ("produced", "landed"):
                result = loads(latest["result"], {}) or {}
                evidence = {"kind": "outputs", "result": result}
                try:
                    terminal.require("task", "done", evidence)
                except terminal.IllegalTransition as exc:
                    # The proof flight settled, but its result is not a
                    # proof: the terminal guard refuses it as completion
                    # evidence (no declared outputs, an unproven outcome).
                    # That is a FAILED proof, never a completion -- it
                    # takes the failed-proof path below, and the guard's
                    # refusal never escapes verify(). (First live case,
                    # 2026-10-15: the tbs-finish repair's proof produced
                    # {"ok": true, "artifacts": []}; the refusal escaped
                    # and wedged every tick before discover/launch.)
                    proof_error = f"proof run produced no proof: {exc}"
                else:
                    ledger.set_task_state(
                        tid, "done", decided_by="self-repair", expect="running",
                        reason="proof run completed", now=now,
                        evidence=evidence)
                    ledger.event("repair.completed", tid,
                                 {"receipt_flight": latest["id"], "issue": ISSUE_REF},
                                 "self-repair", now)
                    out["completed"] += 1
                    continue
            elif latest["state"] in ("failed", "cancelled"):
                result = loads(latest["result"], {}) or {}
                proof_error = f"proof run failed: {result.get('error')}"
            else:
                continue
            # A settled proof that did not prove the repair: count the
            # attempt with the failure named, re-quarantine a
            # quarantine-born repair's plan, and let the ladder below
            # re-fly the repair on the next pass.
            handled = any(e["kind"] == "repair.attempt_failed"
                          and e["ts"] >= (latest["ended_at"] or 0)
                          for e in events)
            if not handled:
                _fail(ledger, ledger.task(tid), proof_error, now)
                packet = loads(task["reason"], {}) or {}
                plan = ledger.plan(packet.get("plan_id") or "")
                if plan is not None and plan["quarantined_at"] is None \
                        and not str(packet.get("signature") or "").startswith("repeat:"):
                    # a quarantine-born repair re-quarantines on a failed
                    # proof; a repeat-signature repair (the plan never
                    # stopped) must not stop it unilaterally -- the
                    # threshold in the tick owns that decision
                    ledger.quarantine_plan(plan["id"],
                                           "proof run failed after repair",
                                           repair_task_id=tid, now=now)
                out["proofs_failed"] += 1
                continue
            # already counted: fall to the retry logic below
        packet = loads(task["reason"], {}) or {}
        if packet.get("plan_id"):
            release_ts = _person_released(ledger, packet["plan_id"], task)
            if release_ts:
                # a person released the plan after this repair opened:
                # their decision outranks the loop, and the repair closes
                # as stood down -- never as a failure, never silently
                ledger.event("repair.stood_down", tid,
                             {"why": "plan released after this repair opened",
                              "released_at": release_ts}, "self-repair", now)
                ledger.set_task_state(
                    tid, "done", decided_by="self-repair", expect="running",
                    reason="stood down: plan released", now=now,
                    evidence={"kind": "delivered",
                              "result": {"verified": True,
                                         "evidence": ["plan.unquarantined after "
                                                      "the repair opened"]}})
                out["stood_down"] = out.get("stood_down", 0) + 1
                continue
        n = attempts(ledger, tid)
        if n >= MAX_ATTEMPTS:
            if not any(e["kind"] == "repair.escalated"
                       and (loads(e["payload"], {}) or {}).get("tier") == "human"
                       for e in events):
                escalate_human(ledger, ledger.task(tid), now)
                out["escalated"] += 1
            ledger.set_task_state(tid, "abandoned", decided_by="self-repair",
                                  expect="running",
                                  reason="repair attempts exhausted", now=now)
            continue
        if n >= 2 and not any(e["kind"] == "repair.escalated"
                              and (loads(e["payload"], {}) or {}).get("tier") == "coordinator"
                              for e in events):
            escalate_coordinator(ledger, ledger.task(tid), now)
            out["escalated"] += 1
        retry_due = any(e["kind"] == "repair.retry_due" for e in events)
        flights = ledger.flights(task_id=tid)
        if flights and flights[0]["state"] in ("failed", "cancelled") \
                and not retry_due:
            _fail(ledger, ledger.task(tid),
                  f"repair flight {flights[0]['state']} before recording an attempt",
                  now)
            retry_due = True
        live = [f for f in flights if f["state"] in _LIVE_FLIGHT_STATES]
        if retry_due and not live:
            made = ledger.create_flight(task["plan_id"], task_id=tid,
                                         attempt=n + 1, now=now,
                                         unique_for_task=True)
            if made:
                out["retried"] += 1
    return out


def _gh_comment(body):
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    try:
        proc = subprocess.run([gh, "issue", "comment", ISSUE_NUMBER,
                               "--repo", "ariaxhan/nexus-office", "--body", body],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc!r}"[:120]
    return "posted" if proc.returncode == 0 else f"rc={proc.returncode}"


def escalate_coordinator(ledger, task, now):
    """Tier 2: the owning coordinator gets the failure, on the issue where
    repair work is tracked. Recorded either way; silence is the defect."""
    packet = loads(task["reason"], {}) or {}
    body = (f"Self-repair escalation (tier 2): repair task {task['id']} for "
            f"`{packet.get('plan_name') or packet.get('job_id')}` has failed "
            f"{attempts(ledger, task['id'])} attempts inside the envelope. "
            f"Packet: {task['reason']}")
    outcome = _gh_comment(body)
    ledger.event("repair.escalated", task["id"],
                 {"tier": "coordinator", "issue": ISSUE_REF, "gh": outcome},
                 "self-repair", now)


def _human_asks():
    client = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "client")
    if client not in sys.path:
        sys.path.insert(0, client)
    import human_asks
    return human_asks


def _tried(ledger, task_id):
    rows = ledger.events(kind="repair.attempt_failed", subject=task_id)
    return "; ".join(f"attempt {i + 1}: {(loads(e['payload'], {}) or {}).get('why', '?')}"
                     for i, e in enumerate(rows)) or "no attempts recorded"


def escalate_human(ledger, task, now):
    """Tier 3 -- the ONLY broken-state shape that may reach Aria: what
    broke, what was tried, the single thing only she can do. Composed
    through the surface guard, with the repair reference attached."""
    packet = loads(task["reason"], {}) or {}
    name = packet.get("plan_name") or packet.get("job_id") or "a worker"
    dead = packet.get("dead") or []
    single_act = (f"restore or repoint the dead path {dead[0]}" if dead else
                  "correct the worker's command by hand, or retire it")
    text = (f"{name} is stopped and its repair has failed {MAX_ATTEMPTS} times. "
            f"What broke: {packet.get('reason')}. "
            f"What was tried: {_tried(ledger, task['id'])}. "
            f"The single thing only you can do: {single_act}.")
    ref = {"issue": ISSUE_REF, "task_id": task["id"], "escalated": True,
           "attempts": MAX_ATTEMPTS, "single_act": single_act,
           "ledger_path": ledger.path}
    surface_guard.assert_sendable(text, repair_ref=ref, ledger_path=ledger.path)
    outcome = {}
    try:
        asks = _human_asks()
        with contextlib.closing(asks.connect()) as db:
            outcome["ask"] = asks.request_human_input(
                db, identifier=f"nexus-repair:{task['id']}",
                execution_ref=f"nexus-repair:{task['id']}",
                gate_type="new_judgment", action=text,
                why_agent_cannot_do_it="The pre-authorized repair envelope was "
                                       "exhausted: three verified attempts failed.",
                authorization_gap="Only a person can restore a path the envelope "
                                  "cannot correct, or retire the worker.",
                resume_after_answer="The repair task record carries the full "
                                    "history; release or retire the worker there.")
    except Exception as exc:  # noqa: BLE001 - the escalation is recorded regardless
        outcome["ask_error"] = repr(exc)[:200]
    ledger.event("repair.escalated", task["id"],
                 {"tier": "human", "issue": ISSUE_REF, **outcome},
                 "self-repair", now)


def escalate_mechanism(ledger, plan, why, now):
    """The mechanism itself is stopped (its plan quarantined, or a repair
    abandoned unescalated). Same tier-3 shape, fired by the tower's
    safety-net pass; recorded as an event whatever the ask's outcome."""
    text = (f"The self-repair mechanism is stopped. What broke: {why}. "
            f"What was tried: automatic repair is the thing that is down. "
            f"The single thing only you can do: release plan {plan['name']} "
            f"(`nexus plans release {plan['name']}`) or retire it.")
    outcome = {}
    try:
        asks = _human_asks()
        with contextlib.closing(asks.connect()) as db:
            outcome["ask"] = asks.request_human_input(
                db, identifier=f"nexus-repair-mechanism:{plan['id']}:{int(plan['quarantined_at'] or 0)}",
                execution_ref="nexus-self-repair",
                gate_type="new_judgment", action=text,
                why_agent_cannot_do_it="The repairer itself is stopped; nothing "
                                       "below a person can restart it.",
                authorization_gap="A stopped repair plan is only released by a person.",
                resume_after_answer="Releasing the plan restarts the repair loop.")
    except Exception as exc:  # noqa: BLE001
        outcome["ask_error"] = repr(exc)[:200]
    ledger.event("repair.escalated", plan["id"],
                 {"tier": "human", "mechanism": True, "issue": ISSUE_REF, **outcome},
                 "self-repair", now)
