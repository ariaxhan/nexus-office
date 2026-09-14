"""Parallel Tower lanes in ONE canonical checkout on main: write-set leases, no worktrees, no branches.

A lane with a write_set (triage's execution plan) leases only those paths. Disjoint lanes run at
once in the same checkout; overlapping lanes serialize. A lane without a write_set is a whole-repo
lane (lease.acquire: nexus-lease.json + `tbs lock`, unchanged). A person holding `tbs lock` on the
repository blocks every lane. Landing commits only the lane's own paths through a temporary index,
so no lane can capture or revert another lane's or a person's files.
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import json
import os
import time
from datetime import datetime

from . import landing, lease

PLAN = os.environ.get("NEXUS_EXECUTION_PLAN", os.path.expanduser(
    "~/Developer/Vaults/CodingVault/thinking-brain-school/_meta/state/execution-plan.json"))
PLAN_MAX_AGE_S = 3 * 3600
PER_REPO, GLOBAL = 3, 3
NOTES = os.environ.get("NEXUS_LEASE_NOTES", os.path.expanduser(
    "~/Developer/Vaults/CodingVault/thinking-brain-school/_meta/state/lease-notes.jsonl"))


def caps(tower_config):
    par = (tower_config or {}).get("parallel") or {}
    return int(par.get("per_repo", PER_REPO)), int(par.get("global", GLOBAL))


# ---- execution plan ---------------------------------------------------------


def _stamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def read_plan(path=None, now=None):
    """Waves of [{repo, number, write_set|None}] in priority order; None when missing, invalid or >3h old."""
    path = path or PLAN
    try:
        with open(path) as f:
            data = json.load(f)
        made = _stamp(data.get("generated_at"))
        made = os.path.getmtime(path) if made is None else made
    except (OSError, ValueError, AttributeError):
        return None
    if (now or time.time()) - made > PLAN_MAX_AGE_S:
        return None
    waves = []
    for wave in data.get("waves") or []:
        items = wave if isinstance(wave, list) else (wave.get("issues") or wave.get("items") or [])
        rows = []
        for item in items:
            number = item.get("number", item.get("issue")) if isinstance(item, dict) else None
            if not item or not item.get("repo") or not str(number).isdigit():
                continue
            ws = item.get("write_set")
            clean = sorted({str(p).strip().strip("/") for p in ws} - {""}) if isinstance(ws, list) else []
            rows.append({"repo": item["repo"].lower(), "number": int(number), "write_set": clean or None})
        if rows:
            waves.append(rows)
    return waves


def write_set_for(repo, number, path=None, now=None):
    for wave in read_plan(path, now) or []:
        for item in wave:
            if item["repo"] == repo.lower() and item["number"] == int(number):
                return item["write_set"]
    return None


# ---- path sets --------------------------------------------------------------


def _covers(pattern, p):
    return p == pattern or p.startswith(pattern + "/") or fnmatch.fnmatchcase(p, pattern)


def overlaps(a, b):
    """None is the whole repository and overlaps everything."""
    if a is None or b is None:
        return True
    return any(_covers(x, y) or _covers(y, x) for x in a for y in b)


def inside(write_set, p):
    return write_set is None or any(_covers(w, p) for w in write_set)


# ---- lease ------------------------------------------------------------------


def _dir(repo):
    return os.path.join(lease._gitdir(repo), "nexus-leases")


@contextlib.contextmanager
def mutex(repo):
    """Serializes lease changes AND landings in one repository."""
    d = _dir(repo)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ".mutex"), "a") as m:
        fcntl.flock(m, fcntl.LOCK_EX)
        yield d


def records(repo):
    d, out = _dir(repo), []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.endswith(".json"):
            with contextlib.suppress(OSError, ValueError), open(os.path.join(d, name)) as f:
                out.append(json.load(f))
    return out


def live(repo):
    return [r for r in records(repo) if not lease.stale(r)]


def acquire(repo, branch, flight, pid, ttl_s, write_set, per_repo=PER_REPO):
    """Lease `write_set` in the canonical checkout. Owned when it cannot run now; nothing is switched."""
    if landing._git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != branch:
        raise lease.Owned("other_branch")
    if any(os.path.exists(os.path.join(lease._gitdir(repo), m)) for m in lease.MID_OPS):
        raise lease.Owned("mid_operation")
    whole = lease.read(repo)
    if whole:
        raise lease.Owned(f"owned:{whole['flight']}")
    rc, err = lease.lane_lock("check", repo, flight, pid, "--write-set", *write_set)  # whole-repo locks and session files
    if rc:
        raise lease.Owned(f"lane_lock:{err[:200]}")
    with mutex(repo) as d:
        others = live(repo)
        if len(others) >= per_repo:
            raise lease.Owned(f"per_repo_cap:{per_repo}")
        hit = next((r for r in others if overlaps(r["write_set"], write_set)), None)
        if hit:
            raise lease.Owned(f"write_set:{hit['flight']}")
        baseline = lease.dirty(repo)
        dirty_inside = sorted(p for p in baseline if inside(write_set, p))
        if dirty_inside:  # a person's or crashed lane's bytes already sit in these paths
            raise lease.Owned("write_set_dirty:" + ",".join(dirty_inside[:5]))
        record = {"flight": flight, "pid": pid, "expires": time.time() + ttl_s, "branch": branch,
                  "head": landing._git(repo, "rev-parse", "HEAD").stdout.strip(),
                  "write_set": write_set, "baseline": baseline}
        with open(os.path.join(d, f"{flight}.json"), "w") as f:
            json.dump(record, f)
    return record


def read(repo, flight):
    return next((r for r in records(repo) if r["flight"] == flight), None)


def release(repo, flight):
    with contextlib.suppress(OSError):
        os.remove(os.path.join(_dir(repo), f"{flight}.json"))


def flight_paths(repo, record):
    """(own changed paths inside the write set, strays changed outside every live lane's write set)."""
    changed, _ = lease.flight_paths(repo, record)
    mine = [p for p in changed if inside(record["write_set"], p)]
    others = [r["write_set"] for r in live(repo) if r["flight"] != record["flight"]]
    strays = [p for p in changed if p not in mine and not any(inside(ws, p) for ws in others)]
    return mine, strays


def recover(repo, comment=None):
    """A stale lane is held from its OWN write-set paths only; nothing outside them is committed or reverted."""
    results = []
    for record in records(repo):
        if not lease.stale(record):
            continue
        mine, _ = flight_paths(repo, record)
        result = {"state": "CLOSED", "reason": "no_change", "flight": record["flight"]}
        if mine:
            result = landing.hold(repo, record, mine, [], "crashed", comment)
        if landing.terminal(repo, result):
            release(repo, record["flight"])
        results.append(result)
    return results


def note(repo, number, record, paths, reason="needs paths outside write_set"):
    """One append-only lease-note; triage folds it into the next execution plan. No chat."""
    row = {"at": time.time(), "repo": repo, "issue": number, "flight": record["flight"],
           "write_set": record["write_set"], "paths": sorted(paths), "reason": reason}
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(NOTES), exist_ok=True)
        with open(NOTES, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


# ---- landing ----------------------------------------------------------------


def catch_up(repo, record, branch):
    """Bring the shared checkout to origin for paths nobody has dirty. None, or a requeue reason."""
    landing._git(repo, "fetch", "--quiet", "origin", branch)
    head = landing._git(repo, "rev-parse", "HEAD").stdout.strip()
    tip = landing._git(repo, "rev-parse", f"origin/{branch}").stdout.strip()
    if head == tip:
        return None
    if landing._git(repo, "merge-base", "--is-ancestor", head, tip, check=False).returncode:
        return "not_fast_forward"
    moved = landing._git(repo, "diff", "--name-only", "--no-renames", head, tip).stdout.split("\n")
    moved = [p for p in moved if p]
    if overlaps(record["write_set"], moved):
        return "rebase_overlap"
    if set(moved) & set(lease.dirty(repo)):
        return "moved_paths_dirty"
    landing._locked(repo, "update-ref", f"refs/heads/{branch}", tip, head)
    landing.restore(repo, moved, tip)  # clean files only: worktree and index follow the ref
    return None


def land(entry, issue, record, proc, forced, pr_create, comment, run, classify, lines):
    """Enforce the write set, catch up to origin, re-check, commit only own paths, push."""
    repo, branch = entry["path"], record["branch"]
    mine, strays = flight_paths(repo, record)
    if strays:  # refused at commit; never committed, never reverted: they may be a person's bytes
        note(entry["repo"], issue["number"], record, strays)
        held = landing.hold(repo, record, mine, [], "out_of_write_set", comment) if mine else {
            "state": "CLOSED", "flight": record["flight"], "reason": "no_change"}
        return dict(held, requeue="plan", strays=strays[:20])
    if not mine:
        return {"state": "CLOSED", "flight": record["flight"], "reason": "no_change"}
    if proc.returncode:
        return landing.hold(repo, record, mine, [], f"exit_{proc.returncode}", comment)
    labels = [l["name"] for l in issue.get("labels", [])]
    message = f"{entry['repo'].split('/')[-1]}: #{issue['number']} {issue.get('title', '')}".strip()
    for _ in range(3):
        with mutex(repo):
            why = catch_up(repo, record, branch)
            if why:
                return dict(landing.hold(repo, record, mine, [], why, comment), requeue="plan")
            if entry.get("check") and run(entry["check"], cwd=repo, capture_output=True, text=True,
                                          timeout=1800).returncode:
                return landing.hold(repo, record, mine, [], "check_failed", comment)
            mode = forced or classify(entry.get("risk"), labels, mine, lines(repo, mine), entry.get("first_road", False))
            head = landing._git(repo, "rev-parse", "HEAD").stdout.strip()
            if mode != "direct":
                return _review(repo, record, mine, head, message, issue, pr_create, comment, mode)
            sha = landing.commit_paths(repo, head, mine, f"{message}\n\nNexus-Flight: {record['flight']}")
            if landing.push_ref(repo, sha, branch):
                landing._locked(repo, "update-ref", f"refs/heads/{branch}", sha, head)
                landing._locked(repo, "update-index", "--add", "--remove", "--", *mine)
                return {"state": "LANDED", "flight": record["flight"], "sha": sha, "branch": branch, "paths": mine}
    return landing.hold(repo, record, mine, [], "push_rejected", comment)


def _review(repo, record, mine, head, message, issue, pr_create, comment, mode):
    sha = landing.commit_paths(repo, head, mine, f"{message}\n\nNexus-Flight: {record['flight']}")
    pr_branch = f"aria/issue-{issue['number']}"
    if not landing.push_ref(repo, sha, pr_branch):
        return landing.hold(repo, record, mine, [], "branch_push_rejected", comment)
    reason = "in_review" if mode == "review" else "human:risk"
    url = pr_create(pr_branch, record["branch"], f"{message}\n\nCloses #{issue['number']}")
    if reason != "in_review" and comment:
        comment(f"needs Aria: {reason}. PR {url}")
    landing.restore(repo, mine, head)
    return {"state": "HELD", "flight": record["flight"], "reason": reason, "sha": sha, "branch": pr_branch,
            "pr_url": url, "paths": mine}
