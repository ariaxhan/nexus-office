"""One writer per canonical checkout: a lease file carrying the flight's baseline.

The checkout is never cloned, never switched, never stashed. A flight records
which paths were already dirty when it started; landing commits only what the
flight changed, and a crashed flight is recovered from the same record.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

from . import landing

# The one writer lock Vaults-wide: `tbs lock` (repo@branch). Humans and flights see each other.
LANE_LOCK = os.environ.get("NEXUS_LANE_LOCK", os.path.expanduser(
    "~/Developer/Vaults/CodingVault/thinking-brain-school/bin/tbs-lane-lock.py"))

MID_OPS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "BISECT_LOG")


class Owned(Exception):
    """exit 75: someone else holds the checkout, or it is not safe to start."""


def _gitdir(repo):
    return landing._git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()


def path(repo):
    return os.path.join(_gitdir(repo), "nexus-lease.json")


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def digest(repo, rel):
    full = os.path.join(repo, rel)
    if not os.path.lexists(full):
        return None
    if os.path.islink(full):
        return "link:" + os.readlink(full)
    with open(full, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def dirty(repo):
    """{path: content digest or None when deleted} for every changed or untracked path."""
    out = landing._git(repo, "status", "--porcelain", "-z", "--untracked-files=all").stdout
    items, paths, i = out.split("\0"), [], 0
    while i < len(items):
        item = items[i]
        if item:
            paths.append(item[3:])
            if item[0] in "RC":
                i += 1
                paths.append(items[i])
        i += 1
    return {p: digest(repo, p) for p in paths}


def owner(flight):
    return f"nexus:{flight}"


def lane_lock(cmd, repo, flight, pid):
    """rc 0 taken/released, 3 held by another live owner. Absent tool: no lock to share."""
    if not os.path.exists(LANE_LOCK):
        return 0, ""
    env = dict(os.environ, TBS_LANE_OWNER=owner(flight), TBS_LANE_PID=str(pid))
    env.pop("TBS_LANE_RUN", None)
    proc = subprocess.run(["python3", LANE_LOCK, cmd, repo], env=env, capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stderr.strip()


def read(repo):
    try:
        with open(path(repo)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def stale(record, now=None):
    return not alive(record.get("pid")) or record.get("expires", 0) < (now or time.time())


def acquire(repo, branch, flight, pid, ttl_s):
    """Refuse unless on `branch` and not mid-operation; fast-forward only a clean tree."""
    if landing._git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != branch:
        raise Owned("other_branch")
    gd = _gitdir(repo)
    if any(os.path.exists(os.path.join(gd, m)) for m in MID_OPS):
        raise Owned("mid_operation")
    existing = read(repo)
    if existing:
        raise Owned(f"stale:{existing['flight']}" if stale(existing) else f"owned:{existing['flight']}")
    rc, err = lane_lock("acquire", repo, flight, pid)
    if rc:
        raise Owned(f"lane_lock:{err[:200]}")
    landing._git(repo, "fetch", "--quiet", "origin", branch, check=False)
    clean = not landing._git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    behind = landing._git(repo, "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}", check=False)
    if clean and behind.returncode == 0:
        landing._git(repo, "merge", "--ff-only", "--quiet", f"origin/{branch}", check=False)
    record = {"flight": flight, "pid": pid, "expires": time.time() + ttl_s, "branch": branch,
              "head": landing._git(repo, "rev-parse", "HEAD").stdout.strip(), "baseline": dirty(repo)}
    with open(path(repo), "w") as f:
        json.dump(record, f)
    return record


def release(repo, flight):
    record = read(repo)
    if record and record["flight"] == flight:
        os.remove(path(repo))
        lane_lock("release", repo, flight, record["pid"])


def flight_paths(repo, record):
    """(paths the flight changed, those of them that were already dirty at baseline)."""
    now, base = dirty(repo), record["baseline"]
    missing = object()
    changed = sorted(p for p in set(now) | set(base) if now.get(p, missing) != base.get(p, missing))
    return changed, [p for p in changed if p in base]


def recover(repo, comment=None):
    """A stale lease is driven to HELD before any new flight: nothing stays local-only."""
    record = read(repo)
    if not record or not stale(record):
        return None
    paths, collisions = flight_paths(repo, record)
    # A write after the lease expired is not the flight's: a person edited an unlocked tree. Left as is.
    paths = [p for p in paths if not (os.path.lexists(os.path.join(repo, p))
                                      and os.lstat(os.path.join(repo, p)).st_mtime > record.get("expires", 0))]
    moved = landing._git(repo, "rev-parse", "HEAD").stdout.strip() != record["head"]
    result = {"state": "CLOSED", "reason": "no_change", "flight": record["flight"]}
    if paths or moved:
        result = landing.hold(repo, record, paths, collisions, "crashed", comment)
    if landing.terminal(repo, result):
        release(repo, record["flight"])
    return result
