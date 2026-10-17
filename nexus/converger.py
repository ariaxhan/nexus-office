"""Registry truth as structured fields, kept true by a converger (#305, pd-12).

A job's cadence lived in three places (the vault SOURCE registry, the copy
jobctl deploys beside jobrun, the plist launchd loads) and its exceptions in
a fourth: prose in the row's note. The note is now a field:

    cadence_s   the seconds between runs the job is meant to keep
    migration   {from, to, at, ref, reason}: a schedule-kind change, as data

and the converger reads all four and makes them agree. Each disagreement is
a #300 repair record, never text:

    plist / deployed differ from the source  -> inside the envelope: `jobctl
        sync` regenerates both from the source; re-read; verified_fixed on
        the plist path, or escalated (technical) when sync fails
    cadence_s contradicts the source schedule -> escalated (decision): only a
        person knows which of the two was meant

A divergence that clears on its own becomes `recovered`. Silence is clean.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import time
from pathlib import Path

from . import faults

SOURCE = "converger:job-registry"
HOME = Path.home()
VAULT = Path(os.environ.get("VAULT_ROOT", HOME / "Developer" / "Vaults"))
RUNTIME_DIR = Path(os.environ.get(
    "JOBCTL_RUNTIME_DIR", HOME / "Library" / "Application Support" / "nexus-jobs"))
SOURCE_REGISTRY = VAULT / "_meta" / "services" / "jobs" / "registry.jsonl"
DEPLOYED_REGISTRY = RUNTIME_DIR / "registry.jsonl"
LAUNCH_AGENTS = HOME / "Library" / "LaunchAgents"
DEFAULT_HOST = os.environ.get("JOBCTL_DEFAULT_HOST", "arias-Mac-Studio")
WEEK_MIN = 7 * 24 * 60


# ---- schedules -------------------------------------------------------------

def _week_minutes(entry):
    """Every minute of a week a launchd calendar entry fires (missing = any)."""
    days = [entry["weekday"] % 7] if "weekday" in entry else range(7)
    hours = [entry["hour"]] if "hour" in entry else range(24)
    minutes = [entry["minute"]] if "minute" in entry else range(60)
    return {(d * 24 + h) * 60 + m for d in days for h in hours for m in minutes}


def fires(schedule):
    """A schedule's comparable identity: two schedules agree iff this does."""
    kind = (schedule or {}).get("kind")
    if kind == "interval":
        return ("interval", int(schedule["seconds"]))
    if kind == "calendar":
        week = set()
        for entry in schedule.get("at") or []:
            week |= _week_minutes(entry)
        return ("calendar", frozenset(week))
    return (kind,)


def cadence(schedule):
    """Seconds between runs, or None when there is no single one."""
    shape = fires(schedule)
    if shape[0] == "interval":
        return shape[1]
    if shape[0] != "calendar" or not shape[1]:
        return None
    week = sorted(shape[1])
    gaps = {b - a for a, b in zip(week, week[1:] + [week[0] + WEEK_MIN])}
    return gaps.pop() * 60 if len(gaps) == 1 else None


def plist_schedule(path):
    """The schedule a plist on disk actually gives launchd, or None if absent."""
    try:
        with open(path, "rb") as f:
            d = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        return None
    if "StartInterval" in d:
        return {"kind": "interval", "seconds": int(d["StartInterval"])}
    if "StartCalendarInterval" in d:
        cal = d["StartCalendarInterval"]
        cal = cal if isinstance(cal, list) else [cal]
        return {"kind": "calendar", "at": [{k.lower(): v for k, v in e.items()} for e in cal]}
    if d.get("KeepAlive"):
        return {"kind": "daemon"}
    return {"kind": None}


# ---- registry --------------------------------------------------------------

def read_registry(path):
    rows = []
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return rows
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#"):
            rows.append(json.loads(line))
    return rows


def _this_host():
    if os.environ.get("JOBCTL_HOST"):
        return os.environ["JOBCTL_HOST"]
    return subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"],
                          capture_output=True, text=True).stdout.strip()


def _scheduled(rows, host):
    return [r for r in rows if r.get("state") == "enabled"
            and (r.get("host") or DEFAULT_HOST) == host
            and (r.get("schedule") or {}).get("kind") in ("interval", "calendar")]


def observe(source_path, deployed_path, agents_dir, host):
    """{job: (row, {surface: divergent?}, cadences)} for every scheduled job here."""
    deployed = {r["id"]: r for r in read_registry(deployed_path)}
    out = {}
    for r in _scheduled(read_registry(source_path), host):
        want = fires(r["schedule"])
        dep = (deployed.get(r["id"]) or {}).get("schedule")
        plist = plist_schedule(Path(agents_dir) / f"{r['id']}.plist")
        cad = {"field_s": r.get("cadence_s"), "source_s": cadence(r["schedule"]),
               "deployed_s": cadence(dep) if dep else None,
               "plist_s": cadence(plist) if plist else None}
        # absent = an install still pending jobctl's own try gate, not drift
        diverged = {"deployed": dep is not None and fires(dep) != want,
                    "plist": plist is not None and fires(plist) != want,
                    "field": r.get("cadence_s") is not None
                    and r["cadence_s"] != cad["source_s"]}
        out[r["id"]] = (r, diverged, cad)
    return out


# ---- the converger ---------------------------------------------------------

def _signature(job, surface):
    return f"cadence-divergence:{job}:{surface}"


def _open(ledger):
    """This converger's live records by signature (escalated counts as live)."""
    return {rec["signature"]: rec for rec in faults.records(ledger)
            if rec["source"] == SOURCE and rec["state"] in ("detected", "repairing", "escalated")}


def jobctl_sync():
    return subprocess.run([str(RUNTIME_DIR / "jobctl"), "sync"],
                          capture_output=True, text=True).returncode


def _detect(ledger, seen, live, now):
    """Open a record per new divergence; a contradicted field is a decision."""
    drift = {}
    for job, (_, diverged, _) in seen.items():
        for surface in (s for s, bad in diverged.items() if bad):
            sig = _signature(job, surface)
            if sig not in live:
                live[sig] = faults.report(ledger, sig, SOURCE, "detected", now=now)
            if live[sig]["state"] == "escalated":
                continue
            if surface == "field":
                faults.report(ledger, sig, SOURCE, "escalated", fault_id=live[sig]["fault_id"],
                              act_needed=f"choose {job} cadence: set cadence_s or its schedule "
                                         "in the source registry so they agree",
                              kind="decision", now=now)
            else:
                drift[sig] = job
    return drift


def _repair(ledger, drift, live, sync, observe_again, agents_dir, deployed_path, now):
    """The envelope: one `jobctl sync`, then verify each surface on disk."""
    for sig in drift:
        faults.report(ledger, sig, SOURCE, "repairing",
                      fault_id=live[sig]["fault_id"], action="jobctl sync", now=now)
    rc = sync()
    after = observe_again()
    for sig, job in drift.items():
        surface, fid = sig.rsplit(":", 1)[1], live[sig]["fault_id"]
        if rc == 0 and job in after and not after[job][1][surface]:
            where = Path(agents_dir) / f"{job}.plist" if surface == "plist" else Path(deployed_path)
            faults.report(ledger, sig, SOURCE, "verified_fixed", fault_id=fid,
                          surface=str(where), now=now)
        else:
            faults.report(ledger, sig, SOURCE, "escalated", fault_id=fid,
                          act_needed=f"jobctl sync (rc={rc}) left {job} {surface} "
                                     "disagreeing with the source registry",
                          kind="technical", now=now)
    return after


def converge(ledger, source_path=SOURCE_REGISTRY, deployed_path=DEPLOYED_REGISTRY,
             agents_dir=LAUNCH_AGENTS, host=None, sync=jobctl_sync, now=None):
    """One reconciliation pass. `sync=None` is check-only: detect, never repair."""
    host = host or _this_host()
    now = now if now is not None else time.time()
    look = lambda: observe(source_path, deployed_path, agents_dir, host)  # noqa: E731
    seen, live = look(), _open(ledger)
    drift = _detect(ledger, seen, live, now)
    if drift and sync is not None:
        seen = _repair(ledger, drift, live, sync, look, agents_dir, deployed_path, now)

    still = {_signature(j, s) for j, (_, d, _) in seen.items() for s, bad in d.items() if bad}
    for sig, rec in _open(ledger).items():
        if sig not in still and rec["state"] in ("detected", "escalated"):
            ledger.event("watchdog.recovered", rec["fault_id"],
                         {"surface": "converger pass"}, SOURCE, now)
    return {"at": now, "host": host, "clean": not still, "divergent": sorted(still),
            "jobs": {j: cad for j, (_, _, cad) in sorted(seen.items())}}


# ---- note prose -> fields --------------------------------------------------

_CONVERSION = re.compile(
    r"\s*\[(\d{4}-\d\d-\d\d): interval->calendar conversion - launchd StartInterval dead "
    r"session-wide on this host \(([\w-]+#\d+)\); cadence preserved exactly\.\]")


def migrate_rows(rows, before):
    """Lift the interval->calendar prose into cadence_s + migration.

    `before` maps job id -> its pre-conversion schedule. The prose claims the
    cadence was preserved; it moves into fields only where that is true, so a
    false claim stays visible instead of becoming a field."""
    out, moved = [], []
    for r in rows:
        m = _CONVERSION.search(r.get("note") or "")
        old = before.get(r["id"]) or {}
        if m and old.get("kind") == "interval" and cadence(r["schedule"]) == old.get("seconds"):
            r = dict(r, note=_CONVERSION.sub("", r["note"]).strip(),
                     cadence_s=old["seconds"],
                     migration={"from": "interval", "to": "calendar", "at": m.group(1),
                                "ref": m.group(2), "reason": "launchd-startinterval-dead"})
            moved.append(r["id"])
        out.append(r)
    return out, moved


def migrate_file(path, before_path, write=False):
    """Rewrite a registry in place, comments and row order kept. Returns moved ids."""
    before = {r["id"]: r.get("schedule") for r in read_registry(before_path)}
    lines = Path(path).read_text().splitlines(keepends=True)
    moved, out = [], []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            out.append(line)
            continue
        [row], ids = migrate_rows([json.loads(line)], before)
        moved += ids
        out.append(json.dumps(row) + "\n" if ids else line)
    if write and moved:
        tmp = Path(str(path) + ".converger-tmp")
        tmp.write_text("".join(out))
        os.replace(tmp, path)
    return moved
