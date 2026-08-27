"""Is the issue pipeline working RIGHT NOW.

Every other pipeline surface in this office is made of receipts: things that
already happened, written down after the fact. This is the one that answers the
question a person actually asks when they hit run and see a popup: is anything
happening, what is it doing, and when does it next look.

Four facts, from four places on local disk, each of which can fail on its own:

  in flight     <pipeline>/.runtime/pid, the pid dispatch.sh writes for itself
                and removes in an EXIT trap.
  doing         _meta/logs/jobs/com.nexus.issue-dispatch.log, the job's stdout.
                Lines are "[<iso8601 utc>] <text>"; the last one is the truth.
  next look     the interval declared in _meta/services/jobs/registry.jsonl,
                plus the last attempt from `jobctl status --json`.
  allowed       _meta/state/pipeline-off, the kill switch dispatch.sh checks
                before it does anything at all.

...  and two facts about the last sweep rather than the next one:

  last full run <pipeline>/.runtime/last-success, written only when a whole
                sweep finished. A run that started and died never moves it.
  covered       how many repos the runner actually reached in the last 24
                hours, from its own receipts. A pipeline that fires on time and
                reaches nothing is green on every other field on this card.

`covered` deliberately does NOT tally outcomes. office-sync already derives that
from the same receipts file into `world.today`, and two tallies over one file
are two numbers that disagree the first time one window changes.

THE PID FILE ALONE IS NOT LIVENESS. A killed run leaves its pid behind, because
the trap never runs. So the file existing is treated as a CLAIM, and the claim is
checked twice: the process must still exist (`kill -0`), and it must still be a
dispatch.sh (pids are recycled, and a recycled pid is a false green wearing the
right number). A pid that fails either check is reported as `stale_pid`, never as
a run.

Nothing here writes. Not the pid file, not the runtime dir, not the log. This
source reads a machine it does not control and says what it sees.

Every failure mode is its own state, for the reason sections.py gives: "not
configured", "broken", "switched off" and "genuinely idle" are four different
things and must never render the same.

  unconfigured  no OFFICE_RUNTIME_ROOT
  missing-root  a root that is not there
  missing       a root with no issue pipeline installed in it
  unreadable    the runtime dir or the pid file would not read
  off           the kill switch is on, or the job is disabled in the registry.
                A DECISION, never an idle room
  ok            we could tell. `running` then says which way, and it is the only
                field allowed to say a run is alive
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import subprocess
import time

from sources import _card

KEY = "pipeline"
# The human name of the fixture, fixed. A card whose title moves is a
# card the eye has to find again every time it is drawn.
TITLE = "Pipeline"

JOB_ID = "com.nexus.issue-dispatch"
PIPELINE = "_meta/services/issue-pipeline"
RUNTIME = PIPELINE + "/.runtime"
KILL_SWITCH = "_meta/state/pipeline-off"
RECEIPTS = RUNTIME + "/receipts.jsonl"
HEARTBEAT = RUNTIME + "/last-success"
LOG = "_meta/logs/jobs/" + JOB_ID + ".log"
REGISTRY = "_meta/services/jobs/registry.jsonl"
JOBCTL = "_meta/services/jobs/jobctl"

# This runs inside a snapshot push that must not hang. Every subprocess here is
# a local disk read that answers in well under a second; anything past the cap
# is a hang, and a hang is reported as its own state rather than as an idle
# pipeline. jobctl is the slowest of the three and still returns in ~0.2s.
JOBCTL_TIMEOUT_S = 5
PS_TIMEOUT_S = 3
PMSET_TIMEOUT_S = 3

# Enough of the tail to find the last timestamped line even when the run is
# printing untimestamped continuation rows under it.
LOG_TAIL_BYTES = 16384

# Receipts are one short line per repo per sweep and the file grows forever, so
# only the tail is read. At today's rate this is weeks; the window below is one
# day, so a tail that falls short would under-report rather than invent.
RECEIPTS_TAIL_BYTES = 1_048_576

# The same rolling 24 hours office-sync uses for `world.today`, on purpose: a
# calendar day resets to zero in the middle of the afternoon here and makes the
# runner look idle twice a day.
COVER_WINDOW_S = 86400

TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s*(.*)$")


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


# How long is that, in words. One implementation, shared with every card in the
# office, so two fixtures never say the same gap two different ways.
_human = _card.human


def _epoch(iso: str | None) -> float | None:
    """An `...Z` timestamp as epoch seconds, or None if it will not parse."""
    if not iso:
        return None
    try:
        return _dt.datetime.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process, but it exists. Alive.
        return True
    except OSError:
        return False
    return True


def _command_of(pid: int) -> str | None:
    """The command line of a pid, or None if we could not ask.

    None is not "no such process": it means the question went unanswered, and
    the caller has to say so rather than pick an answer.
    """
    try:
        proc = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                              capture_output=True, text=True, timeout=PS_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def read_pid(runtime: pathlib.Path) -> dict:
    """What the pid file claims, and whether the claim survives checking.

    Returns one of:
      {"claim": "none"}                       no pid file; nobody claims a run
      {"claim": "unreadable", ...}            the file is there and would not read
      {"claim": "alive", pid, started, ...}   a real dispatch.sh is running
      {"claim": "stale", pid, why, ...}       the file is lying, and why
    """
    path = runtime / "pid"
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return {"claim": "none"}
    except OSError as exc:
        return {"claim": "unreadable", "detail": f"{type(exc).__name__}: {exc}"[:200]}

    try:
        started = path.stat().st_mtime
    except OSError:
        started = None

    if not raw.isdigit():
        return {"claim": "stale", "pid": None, "started": started,
                "why": f"the pid file does not hold a pid: {raw[:60]!r}"}

    pid = int(raw)
    if not _alive(pid):
        return {"claim": "stale", "pid": pid, "started": started,
                "why": f"pid {pid} is not running; a killed run left its pid behind"}

    cmd = _command_of(pid)
    if cmd == "":
        # ps and kill -0 disagree, which means the process died between the two.
        return {"claim": "stale", "pid": pid, "started": started,
                "why": f"pid {pid} exited while we were looking at it"}
    if cmd is None:
        # We could not ask. Report the run, and say the check did not complete,
        # rather than inventing either answer.
        return {"claim": "alive", "pid": pid, "started": started,
                "command": "", "verified": False,
                "why": "could not run ps, so the pid was not checked for recycling"}
    if "dispatch.sh" not in cmd:
        return {"claim": "stale", "pid": pid, "started": started, "command": cmd[:300],
                "why": f"pid {pid} is now some other process, not dispatch.sh"}

    return {"claim": "alive", "pid": pid, "started": started,
            "command": cmd[:300], "verified": True}


def read_log(path: pathlib.Path) -> dict:
    """The last thing the pipeline said, and when it said it."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > LOG_TAIL_BYTES:
                fh.seek(size - LOG_TAIL_BYTES)
            tail = fh.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return {"state": "missing", "doing": "", "at": None}
    except OSError as exc:
        # An unreadable log is not a quiet pipeline. Its own state, so the room
        # cannot render "nothing to say" when the truth is "cannot read".
        return {"state": "unreadable", "doing": "",
                "at": None, "detail": f"{type(exc).__name__}: {exc}"[:200]}

    lines = [ln.rstrip() for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return {"state": "empty", "doing": "", "at": None}

    # The last line is what it is doing. The last line that carries a timestamp
    # is when it last said anything: continuation rows under a timestamped line
    # have no stamp of their own.
    last = lines[-1]
    m = TS.match(last)
    doing = (m.group(2) if m else last).strip()
    at = None
    for ln in reversed(lines):
        hit = TS.match(ln)
        if hit:
            at = hit.group(1)
            break

    return {"state": "ok", "doing": doing[:400], "at": at}


def read_receipts(path: pathlib.Path, now: float) -> dict:
    """How much ground the runner covered in the last 24 hours, from receipts.

    Only the count of repos and the count of decisions. The outcome breakdown
    belongs to office-sync, which already derives it into `world.today` from
    this same file; a second tally here would be a second number to keep in step
    and the first one to go wrong.

    A line that will not parse is counted, never skipped in silence: receipts
    are the runner's own accounting and a hole in them is a finding.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > RECEIPTS_TAIL_BYTES:
                fh.seek(size - RECEIPTS_TAIL_BYTES)
                fh.readline()  # the seek probably landed mid-line; drop the half
            tail = fh.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return {"state": "missing", "repos": None, "receipts": None,
                "since": "", "unparsed": 0,
                "detail": f"the runner has written no receipts at {path}"}
    except OSError as exc:
        return {"state": "unreadable", "repos": None, "receipts": None,
                "since": "", "unparsed": 0,
                "detail": f"{type(exc).__name__}: {exc}"[:200]}

    since = _dt.datetime.fromtimestamp(
        now - COVER_WINDOW_S, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repos, seen, unparsed = set(), 0, 0
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            unparsed += 1
            continue
        if not isinstance(row, dict) or str(row.get("at") or "") < since:
            continue
        seen += 1
        repo = str(row.get("repo") or "")
        if "/" in repo:
            repos.add(repo)
    return {"state": "ok", "repos": len(repos), "receipts": seen,
            "since": since, "unparsed": unparsed, "detail": ""}


def read_registry(path: pathlib.Path) -> dict:
    """The pipeline job's own declaration: is it enabled, and how often.

    A malformed or absent registry is reported, never guessed around. The
    interval is the only thing that can turn a last attempt into a next look.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"state": "unreadable", "detail": f"{type(exc).__name__}: {exc}"[:200]}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("id") == JOB_ID:
            sched = row.get("schedule") or {}
            secs = sched.get("seconds") if sched.get("kind") == "interval" else None
            return {
                "state": "ok",
                "enabled": str(row.get("state") or "").lower() == "enabled",
                "interval_s": int(secs) if secs else None,
                "every": _human(int(secs)) if secs else "",
            }
    return {"state": "unregistered",
            "detail": f"{JOB_ID} is not declared in the job registry, so nothing schedules it"}


def read_jobctl(root: pathlib.Path) -> dict:
    """The last attempt at this job, from the one thing that keeps receipts.

    jobctl is asked rather than its receipt file read directly, for two reasons:
    the receipts live outside the vault (outside OFFICE_RUNTIME_ROOT entirely, in
    Application Support, to dodge a TCC grant), and jobctl already owns the logic
    that turns them into a health claim. Re-deriving that here would be a second
    implementation to keep in step.
    """
    jobctl = root / JOBCTL
    if not jobctl.exists():
        return {"state": "missing", "detail": f"no jobctl at {jobctl}"}
    try:
        proc = subprocess.run([str(jobctl), "status", "--json"],
                              capture_output=True, text=True, timeout=JOBCTL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"state": "timeout",
                "detail": f"jobctl status did not answer in {JOBCTL_TIMEOUT_S}s"}
    except OSError as exc:
        return {"state": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}

    # jobctl exits 1 whenever anything at all is unhealthy, which is a normal
    # alarm and not an error here. Only unparseable stdout is.
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:200]
        return {"state": "unreadable", "detail": detail or "jobctl printed nothing that parsed"}

    for row in (data.get("jobs") or []):
        if isinstance(row, dict) and row.get("job") == JOB_ID:
            return {
                "state": "ok",
                "job_state": str(row.get("state") or "").upper(),
                "last_attempt": row.get("last_attempt"),
                "last_success": row.get("last_success"),
                "last_rc": row.get("last_rc"),
            }
    return {"state": "absent", "detail": f"jobctl does not know {JOB_ID}"}


def _power() -> str:
    """ac / battery / unknown.

    dispatch defers the whole run on battery, and a run that defers every hour
    looks exactly like an idle one from the outside. That is the false-green this
    field exists to break.
    """
    try:
        proc = subprocess.run(["pmset", "-g", "ps"], capture_output=True,
                              text=True, timeout=PMSET_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    return "ac" if "AC Power" in (proc.stdout or "") else "battery"


def _cannot(state: str, detail: str) -> dict:
    """A source that could not tell, in the same shape as one that could.

    Every field the room reads is present in every return, so a consumer never
    has to guess whether an absent key means false or means nobody looked.
    """
    return {"state": state, "detail": detail, "running": False,
            "running_for": None, "doing": "", "next_in": None,
            "heartbeat": None, "covered": None}


def read() -> dict:
    now = time.time()

    root = _root()
    if root is None:
        return _cannot("unconfigured",
                       "no OFFICE_RUNTIME_ROOT, so there is no pipeline to look at")
    if not root.exists():
        return _cannot("missing-root", str(root))

    pipeline_dir = root / PIPELINE
    if not pipeline_dir.exists():
        return _cannot("missing", f"no issue pipeline installed at {pipeline_dir}")

    runtime = root / RUNTIME
    pid = read_pid(runtime)
    if pid["claim"] == "unreadable":
        return _cannot("unreadable",
                       f"the pid file would not read: {pid.get('detail', '')}")

    log = read_log(root / LOG)
    reg = read_registry(root / REGISTRY)
    kill = (root / KILL_SWITCH)
    switched_off = kill.exists()

    running = pid["claim"] == "alive"
    started = pid.get("started")
    stale = pid if pid["claim"] == "stale" else None

    # Only asked when nothing is running: on battery is an explanation for an
    # idle pipeline, and it costs a subprocess nobody needs mid-run.
    power = "unknown" if running else _power()

    # When it next looks. jobctl's last attempt is preferred; the pipeline's own
    # heartbeat is the fallback, and it is a weaker signal because it records the
    # last SUCCESS, so a run that failed does not move it.
    job = read_jobctl(root)
    interval = reg.get("interval_s")
    last_attempt = job.get("last_attempt")
    # When a whole sweep last FINISHED. dispatch.sh writes this at the end, so a
    # run that started and died never moves it, which is exactly what makes it
    # worth reading beside `last_attempt`.
    try:
        heartbeat = (root / HEARTBEAT).read_text(encoding="utf-8").strip()[:40]
    except OSError:
        heartbeat = ""

    next_source = "jobctl"
    base = _epoch(last_attempt)
    if base is None:
        base = _epoch(heartbeat)
        next_source = "heartbeat" if base is not None else "jobctl"
    if base is None:
        next_source = "unknown"

    cover = read_receipts(root / RECEIPTS, now)

    next_at, next_in, overdue, late_by = None, None, False, None
    if base is not None and interval:
        due = base + interval
        next_at = _dt.datetime.fromtimestamp(due, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if due > now:
            next_in = _human(due - now)
        else:
            # Past due is not "in 0 minutes". Saying so out loud is the only way
            # a scheduler that quietly stopped firing is ever noticed.
            overdue = True
            next_in = "any moment now"
            late_by = _human(now - due)

    # The state. Running wins over everything, because a run in flight is the
    # most urgent true thing even if the switch was flipped a second ago.
    if running:
        state = "ok"
        detail = f"a run is in flight (pid {pid.get('pid')})"
    elif switched_off:
        state = "off"
        detail = f"it is switched off by the kill switch at {kill}"
    elif reg.get("state") == "unregistered":
        state = "off"
        detail = reg.get("detail", "")
    elif reg.get("state") == "ok" and not reg.get("enabled"):
        state = "off"
        detail = f"{JOB_ID} is disabled in the job registry, so it will not fire"
    elif job.get("job_state") == "OFF":
        state = "off"
        detail = f"{JOB_ID} is in launchd's disabled list, so it will not fire"
    else:
        state = "ok"
        detail = (f"nothing running; next look {next_in}" if next_in
                  else "nothing running, and the next look could not be worked out")

    return {
        "state": state,
        "detail": detail,

        # The panel reads these five.
        "running": running,
        "running_for": _human(now - started) if (running and started) else None,
        "doing": log["doing"] if running else "",
        "next_in": next_in,

        # ... and everything else the room is entitled to know.
        "pid": pid.get("pid"),
        "pid_verified": pid.get("verified"),
        "pid_note": pid.get("why", ""),
        # A pid file naming a process that is not a live dispatch.sh. Present
        # ONLY here, never as `running`, which is the whole point.
        "stale_pid": ({"pid": stale.get("pid"), "why": stale.get("why"),
                       "since": stale.get("started")} if stale else None),

        # Idle or not, this is the last thing it actually said.
        "last_said": log["doing"],
        "last_said_at": log["at"],
        "last_said_age_s": (round(now - (_epoch(log["at"]) or now)) if log["at"] else None),
        "log_state": log["state"],
        "log_detail": log.get("detail", ""),

        "next_at": next_at,
        "next_source": next_source,
        "overdue": overdue,
        "late_by": late_by,
        "every": reg.get("every", ""),
        "schedule_state": reg["state"],
        "enabled": reg.get("enabled"),
        "kill_switch": switched_off,

        # The last sweep that actually finished, and what it reached. A pipeline
        # that fires on time and covers nothing is green on every field above.
        "heartbeat": heartbeat or None,
        "heartbeat_age_s": (round(now - (_epoch(heartbeat) or now))
                            if _epoch(heartbeat) is not None else None),
        "covered": cover,

        "job_state": job.get("job_state"),
        "last_attempt": last_attempt,
        "last_success": job.get("last_success"),
        "last_rc": job.get("last_rc"),
        "jobctl_state": job["state"],
        "jobctl_detail": job.get("detail", ""),

        "power": power,
        # On battery dispatch exits before doing anything, and the log says so.
        # Without this an hourly deferral is indistinguishable from an idle hour.
        "deferring": bool(power == "battery" and "battery" in (log["doing"] or "").lower()),
    }


# Only the four states `_cannot` can return. `ok` and `off` come out of the full
# read, which fills in every field the card below reads, and `off` is a decision
# that wants nobody rather than a fault that does.
TROUBLE = {
    "unconfigured": 0,
    "missing-root": 1,
    "missing": 1,
    "unreadable": 1,
}


def card(data: dict) -> dict:
    """Is the pipeline working right now, in one line.

    `detail` is already the sentence this source exists to produce, so the card
    does not write a second one. What it adds is `needs`: a pid file that is
    lying, or a log nobody can read, is a thing a person has to go and look at,
    and neither of those is visible in `running`.

    It also adds the last finished sweep and how many repos it reached. A runner
    that fires on schedule and covers nothing reads as perfectly healthy on
    `running`, `next look` and `last said` all at once.
    """
    state = data.get("state")
    detail = str(data.get("detail") or "")
    if state not in ("ok", "off"):
        needs = TROUBLE.get(state, 1)
        facts = [_card.fact("state", str(state), "bad" if needs else "dim")]
        if detail:
            facts.append(_card.fact("detail", detail, "dim"))
        return _card.build(TITLE, detail or str(state), needs, "", facts)

    stale = data.get("stale_pid")
    log_state = data.get("log_state")
    running = bool(data.get("running"))
    running_for = data.get("running_for")

    facts = [_card.fact("running",
                        (f"yes, for {running_for}" if running_for else "yes") if running else "no",
                        "ok" if running else "dim")]
    if data.get("doing"):
        facts.append(_card.fact("doing", data["doing"]))

    if data.get("overdue"):
        # Past due is never "in 0 minutes". A scheduler that quietly stopped
        # firing is only ever noticed because a card said this out loud.
        facts.append(_card.fact("next look",
                                f"overdue by {data.get('late_by') or 'a while'}", "warn"))
    elif data.get("next_in"):
        facts.append(_card.fact("next look", data["next_in"]))
    else:
        facts.append(_card.fact("next look", "could not be worked out", "warn"))

    # One row for the last sweep that finished and what it reached, because on
    # their own each is easy to explain away: a fresh heartbeat over zero repos
    # is a runner that woke up and did nothing, and neither number says that.
    cover = data.get("covered") or {}
    hb_age = data.get("heartbeat_age_s")
    reached = cover.get("repos")
    when = f"{_human(hb_age)} ago" if hb_age is not None else "never"
    if cover.get("state") != "ok":
        facts.append(_card.fact("last full run",
                                f"{when}, receipts {cover.get('state') or 'unknown'}", "bad"))
    else:
        facts.append(_card.fact(
            "last full run", f"{when}, {reached} {_card.plural(reached, 'repo')} in 24h",
            "warn" if (hb_age is None or not reached) else "dim"))

    age = data.get("last_said_age_s")
    if log_state == "ok":
        facts.append(_card.fact("last said",
                                f"{_human(age)} ago" if age is not None else "no timestamp on it",
                                "dim"))
    else:
        facts.append(_card.fact("last said", f"the log is {log_state}", "bad"))

    pid = data.get("pid")
    if pid is None:
        facts.append(_card.fact("pid", "nobody claims a run", "dim"))
    elif data.get("pid_verified"):
        facts.append(_card.fact("pid", f"{pid}, checked against ps", "ok"))
    else:
        facts.append(_card.fact("pid", f"{pid}, not checked for recycling", "warn"))

    if stale:
        facts.append(_card.fact("stale pid", stale.get("why") or stale.get("pid"), "bad"))
    if data.get("kill_switch"):
        facts.append(_card.fact("kill switch", "on", "warn"))

    # A missing log is a break only when the pipeline is supposed to be running:
    # one switched off on purpose has no log to speak of, and no alarm to raise.
    broken = (bool(stale)
              or (data.get("state") == "ok" and log_state != "ok")
              # Receipts nobody can read is a hole in the runner's own
              # accounting, and it is invisible in every field above.
              or (data.get("covered") or {}).get("state") == "unreadable")
    return _card.build(TITLE, detail, 1 if broken else 0,
                       _card.zulu(data.get("last_said_at")), facts)
