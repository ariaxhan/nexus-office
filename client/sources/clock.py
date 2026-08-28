"""The scheduled jobs, from jobctl.

A job that stopped firing looks exactly like a job with nothing to do. That is
the whole reason this source exists, and it is why the states here are kept
apart rather than folded into a health percentage:

  ok       succeeded inside the budget the job declares for itself
  stale    no successful run inside that budget. The alarm
  failing  the last run exited non-zero, and it is inside its budget
  never    no receipt at all. Usually a bad path or a permission, not a bug
  off      in launchd's persistent disabled list. A DECISION, never a fault

`off` and `stale` conflated is how a deliberate pause becomes an outage nobody
investigates, and `never` folded into `stale` sends you hunting a bug in a job
that has never once been executed.

Two files, joined on the job id:

  jobctl status --json    the health claim, derived from run receipts
  registry.jsonl          the declaration: command, schedule, budget, owner

The registry is needed because the status output does not carry the budget, and
`max_success_age_h: null` means the staleness check is switched off entirely.
A job nothing watches is itself a finding, so it is surfaced as `unwatched`.

Like runtime.py, every return carries a `state` and never a bare number: "no
root configured", "jobctl is broken", "jobctl hung" and "zero jobs" must never
render the same.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

from sources import _card

KEY = "clock"
# The human name of the fixture, fixed. A card whose title moves is a
# card the eye has to find again every time it is drawn.
TITLE = "Clock"

JOBCTL = "_meta/services/jobs/jobctl"
REGISTRY = "_meta/services/jobs/registry.jsonl"

# This runs inside a snapshot push. jobctl reads receipts off local disk and
# returns in well under a second; anything past this is a hang, and a hang has
# to be its own reported state rather than an empty room.
TIMEOUT_S = 10


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _schedule(sched: dict) -> str:
    """The schedule as a person would say it out loud."""
    kind = (sched or {}).get("kind")
    if kind == "daemon":
        return "always running"
    if kind == "interval":
        s = int((sched or {}).get("seconds") or 0)
        if s and s % 3600 == 0:
            return f"every {s // 3600}h"
        if s and s % 60 == 0:
            return f"every {s // 60}m"
        return f"every {s}s"
    if kind == "calendar":
        at = (sched or {}).get("at") or []
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        parts = []
        for t in at[:6]:
            hm = f"{int(t.get('hour') or 0):02d}:{int(t.get('minute') or 0):02d}"
            wd = t.get("weekday")
            parts.append(f"{days[int(wd) % 7]} {hm}" if wd is not None else hm)
        return "at " + ", ".join(parts) if parts else "on a calendar"
    return kind or "unknown"


def read_registry(path: pathlib.Path) -> tuple[dict, list[str]]:
    """The declarations, by id, plus every line that would not parse.

    A malformed line is reported, never skipped in silence. The registry is the
    only source of truth for what is supposed to exist, so a line nobody can
    read is a job nobody can account for.
    """
    by_id: dict[str, dict] = {}
    bad: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, [f"could not read the registry: {exc}"[:200]]

    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            bad.append(f"line {n}: {exc.msg}")
            continue
        if isinstance(row, dict) and row.get("id"):
            by_id[str(row["id"])] = row
        else:
            bad.append(f"line {n}: no id")
    return by_id, bad


def classify(row: dict, reg: dict) -> str:
    """One of ok / stale / failing / never / off, from a status row.

    jobctl's own `NO DATA` is overwritten by `STALE` whenever the job declares a
    budget, so a job that has NEVER RUN reports STALE alongside jobs that ran
    fine last week and stopped. Different cause, different fix, so never-fired
    is recovered here from the absence of any receipt at all.

    A daemon is judged by liveness, not by receipts: it legitimately carries no
    attempt, no success and no rc, so it can never be called never-fired.
    """
    state = str(row.get("state") or "").upper()
    if state == "OFF":
        return "off"

    daemon = ((reg.get("schedule") or {}).get("kind") == "daemon")
    no_receipt = (row.get("last_attempt") is None
                  and row.get("last_success") is None
                  and row.get("last_rc") is None)
    if no_receipt and not daemon and state != "OK":
        return "never"

    if state == "NO DATA":
        return "never"
    if state == "STALE":
        return "stale"
    if state == "FAILING":
        return "failing"
    if state == "OK":
        return "ok"
    return "unknown"


def _job(row: dict, reg: dict) -> dict:
    budget = reg.get("max_success_age_h")
    daemon = ((reg.get("schedule") or {}).get("kind") == "daemon")
    command = reg.get("command")
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    return {
        "id": str(row.get("job") or ""),
        "state": classify(row, reg),
        "detail": str(row.get("detail") or "")[:300],
        "schedule": _schedule(reg.get("schedule") or {}),
        # Verbatim, the way the gate shows its target. A command you can only
        # half read is a command you cannot judge.
        "command": str(command or "")[:2000],
        "owner": str(reg.get("owner") or ""),
        "note": str(reg.get("note") or "").strip()[:300],
        "last_attempt": row.get("last_attempt"),
        "last_success": row.get("last_success"),
        "last_rc": row.get("last_rc"),
        "budget_h": budget,
        # A null budget switches the staleness check off. For a daemon that is
        # correct, because liveness is what judges it. For anything else it
        # means nothing is watching, which is the finding.
        "watch": "liveness" if daemon else (f"{budget}h" if budget else "nothing"),
        "unwatched": bool(not daemon and not budget),
        "in_registry": bool(reg),
    }


def read() -> dict:
    root = _root()
    if root is None:
        return {"state": "unconfigured",
                "detail": "no OFFICE_RUNTIME_ROOT, so no job registry to read"}
    if not root.exists():
        return {"state": "missing-root", "detail": str(root)}

    jobctl = root / JOBCTL
    if not jobctl.exists():
        return {"state": "missing", "detail": f"no jobctl at {jobctl}"}

    try:
        proc = subprocess.run(
            [str(jobctl), "status", "--json"],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # Its own state, never "no jobs". A hung health check is a thing to fix,
        # and rendering it as an empty wall is exactly the false-green this
        # whole project exists to kill.
        return {"state": "timeout",
                "detail": f"jobctl status did not answer in {TIMEOUT_S}s"}
    except OSError as exc:
        return {"state": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}

    # jobctl exits 1 when anything is unhealthy, which is the NORMAL alarm case,
    # so the exit code is not an error signal here. Only unparseable stdout is.
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:200]
        return {"state": "unreadable",
                "detail": detail or "jobctl status printed nothing that parsed"}

    reg_by_id, reg_bad = read_registry(root / REGISTRY)

    jobs = [_job(r, reg_by_id.get(str(r.get("job") or "")) or {})
            for r in (data.get("jobs") or []) if isinstance(r, dict)]

    counts = {k: 0 for k in ("ok", "stale", "failing", "never", "off", "unknown")}
    for j in jobs:
        counts[j["state"]] = counts.get(j["state"], 0) + 1

    # Faults first, then the deliberately-off tail, then the quiet ones. OFF is
    # reported but never leads: it is a decision, not an alarm.
    order = {"stale": 0, "failing": 1, "never": 2, "unknown": 3, "off": 5, "ok": 6}
    jobs.sort(key=lambda j: (order.get(j["state"], 4), j["id"]))

    return {
        "state": "ok",
        "checked": len(jobs),
        "counts": counts,
        "alarm": counts["stale"] + counts["failing"] + counts["never"],
        "unwatched": sum(1 for j in jobs if j["unwatched"]),
        # Rows in the registry that would not parse, and rows jobctl reported
        # that the registry does not declare. Both are accounting holes.
        "registry_bad": reg_bad,
        "unregistered": [j["id"] for j in jobs if not j["in_registry"]],
        "jobs": jobs,
    }


# What to say when the clock could not be read at all, and how many people it
# wants. `unconfigured` is a deliberate absence, so it wants nobody; everything
# else here is something that used to work.
TROUBLE = {
    "unconfigured": ("not configured", 0),
    "missing-root": ("the vault root is not there", 1),
    "missing": ("jobctl is not installed", 1),
    "timeout": ("jobctl did not answer", 1),
    "unreadable": ("jobctl printed nothing that parsed", 1),
    "error": ("could not run jobctl", 1),
}


# Which heading a job sits under. Three, not six: a person reading the wall is
# deciding what to look at, and the difference between stale and failing belongs
# in the badge on the row rather than in a fourth heading to scan past.
GROUPS = {
    "stale": "needs a look", "failing": "needs a look",
    "never": "needs a look", "unknown": "needs a look",
    "off": "off",
    "ok": "healthy",
}
ROW_TONES = {"stale": "bad", "failing": "bad", "never": "warn",
             "unknown": "warn", "off": "dim", "ok": "ok"}


def _script(command: str, root: pathlib.Path | None) -> str:
    """The absolute file this command runs, when there really is one under the
    root, else "".

    Three conditions, all of them required, and none of them about the string
    looking plausible: the token is absolute, it resolves to a REGULAR FILE that
    exists right now, and the resolved path is inside the configured root. The
    resolve is what makes a symlink out of the vault fail this rather than pass
    it, and the containment is why `/bin/sh` is not a link even though it is
    unquestionably there.

    A link is somewhere to OPEN. Nothing in this app runs a row, and a path that
    cannot be stood behind draws as text rather than as a button that lies.
    """
    if root is None:
        return ""
    try:
        base = str(root.resolve(strict=True))
    except (OSError, RuntimeError):
        return ""
    for token in str(command or "").split():
        if not token.startswith("/"):
            continue
        candidate = pathlib.Path(token)
        try:
            real = candidate.resolve(strict=True)
            if not real.is_file():
                continue
        except (OSError, RuntimeError):
            continue
        text = str(real)
        if text == base or text.startswith(base + os.sep):
            return "file://" + text
    return ""


def rows(data: dict) -> list:
    """Every job, in the order the source already sorted them.

    Source order, not a second sort here: `read()` put the faults on top and the
    deliberately-off tail underneath, and a card that re-decided that would put
    a paused job above one that stopped firing on somebody's wall.
    """
    root = _root()
    out = []
    for job in data.get("jobs") or []:
        state = str(job.get("state") or "unknown")
        command = str(job.get("command") or "")
        # The small print, in the order it is useful: what it runs, what the
        # health check said about the last run, why nothing is watching it.
        detail = " · ".join(p for p in (
            command,
            str(job.get("detail") or ""),
            str(job.get("note") or ""),
            "nothing is watching this" if job.get("unwatched") else "",
        ) if p)
        owner = str(job.get("owner") or "")
        out.append(_card.row(
            str(job.get("id") or ""),
            str(job.get("id") or ""),
            subtitle=" · ".join(p for p in (str(job.get("schedule") or ""),
                                            owner and f"owner {owner}") if p),
            detail=detail,
            badge=state,
            tone=ROW_TONES.get(state, "warn"),
            group=GROUPS.get(state, "needs a look"),
            url=_script(command, root),
        ))
    return out


def card(data: dict) -> dict:
    """The wall clock in one line: how many jobs want a person, and why.

    `off` is counted, shown, and never leads. It is a decision, and a card that
    alarms about a deliberately paused job is a card people stop reading. The
    alarm is stale + failing + never, which is what the source already computed
    rather than a second opinion about it.
    """
    if data.get("state") != "ok":
        return _card.trouble(TITLE, data.get("state"), data.get("detail"), TROUBLE)

    counts = data.get("counts") or {}
    checked = int(data.get("checked") or 0)
    alarm = int(data.get("alarm") or 0)
    unwatched = int(data.get("unwatched") or 0)

    def n(key):
        return int(counts.get(key) or 0)

    if not checked:
        headline = "nothing is scheduled"
    elif alarm:
        headline = f"{alarm} of {checked} jobs {'needs' if alarm == 1 else 'need'} a look"
    else:
        headline = f"{checked} {_card.plural(checked, 'job')}, all fine"

    facts = [
        _card.fact("ok", _card.count(n("ok")), "ok" if n("ok") else "dim"),
        _card.fact("stale", _card.count(n("stale")), "bad" if n("stale") else "dim"),
        _card.fact("failing", _card.count(n("failing")), "bad" if n("failing") else "dim"),
        _card.fact("never ran", _card.count(n("never")), "warn" if n("never") else "dim"),
        _card.fact("off", _card.count(n("off")), "dim"),
        _card.fact("unwatched", _card.count(unwatched), "warn" if unwatched else "dim"),
    ]
    if n("unknown"):
        facts.append(_card.fact("unknown", _card.count(n("unknown")), "warn"))
    # A registry line nobody can parse and a job nobody declared are the same
    # kind of hole: something is running that the paperwork does not cover.
    holes = len(data.get("registry_bad") or []) + len(data.get("unregistered") or [])
    if holes:
        facts.append(_card.fact("unaccounted for", _card.count(holes), "warn"))

    # The freshest thing any job actually attempted. Normalised one at a time so
    # a stamp in another shape sorts last instead of winning the comparison.
    as_of = max((_card.zulu(j.get("last_attempt"))
                 for j in (data.get("jobs") or [])), default="")
    return _card.build(TITLE, headline, alarm, as_of, facts, rows(data))
