"""One writer per canonical checkout: a lease file carrying the flight's baseline.

The checkout is never cloned, never switched, never stashed. A flight records
which paths were already dirty when it started; landing commits only what the
flight changed, and a crashed flight is recovered from the same record.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
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


def lane_lock(cmd, repo, flight, pid, *extra):
    """rc 0 taken/released, 3 held by another live owner. Absent tool: no lock to share."""
    if not os.path.exists(LANE_LOCK):
        return 0, ""
    env = dict(os.environ, TBS_LANE_OWNER=owner(flight), TBS_LANE_PID=str(pid))
    env.pop("TBS_LANE_RUN", None)
    proc = subprocess.run(["python3", LANE_LOCK, cmd, repo, *extra], env=env, capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stderr.strip()


def read(repo):
    try:
        with open(path(repo)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


HEARTBEAT_S = 600  # a live holder's heartbeat older than this: the holder is wedged or gone
MAX_HOLD_S = 1800  # whole-repo: no renewal for this long releases it
MAX_HOLD_WRITE_SET_S = 3600
INDEX = os.environ.get("NEXUS_LEASE_INDEX") or os.path.expanduser(
    "~/Library/Application Support/nexus/lease-repos.json")


def stamp(flight, pid, ttl_s, reason, paths, now=None):
    """The common record head. Fields absent from legacy records keep the pid/expires rules alone."""
    now = now or time.time()
    return {"flight": flight, "pid": pid, "expires": now + ttl_s, "host": socket.gethostname(),
            "started_at": now, "renewed_at": now, "heartbeat_at": now, "reason": reason, "paths": paths}


def stale(record, now=None):
    now = now or time.time()
    if not alive(record.get("pid")) or record.get("expires", 0) < now:
        return True
    if record.get("heartbeat_at") is not None and now - record["heartbeat_at"] > HEARTBEAT_S:
        return True
    hold = MAX_HOLD_WRITE_SET_S if record.get("write_set") else MAX_HOLD_S
    return record.get("renewed_at") is not None and now - record["renewed_at"] > hold


def _index_add(repo):
    with contextlib.suppress(OSError):
        repos = set(indexed())
        if repo not in repos:
            os.makedirs(os.path.dirname(INDEX), exist_ok=True)
            _write(INDEX, sorted(repos | {repo}))


def indexed():
    try:
        with open(INDEX) as f:
            return [r for r in json.load(f) if isinstance(r, str)]
    except (OSError, ValueError, TypeError):
        return []


def _write(target, data):
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, target)


def heartbeat(now=None):
    """Tower tick: renew every lease whose holder is alive and not already stale; dead ones stay for recover().

    Lock-free on purpose: a landing may hold the lanes mutex for a whole check, and a skipped beat would age a
    healthy flight. The only race is a release landing between exists() and replace(); the resurrected record
    names a finished flight and recover() closes it (no_change) or holds its bytes. Nothing is discarded."""
    from . import lanes
    now, renewed, keep = now or time.time(), 0, []
    for repo in indexed():
        if not os.path.isdir(repo):
            continue
        try:
            targets = [(path(repo), read(repo))] + [(os.path.join(lanes._dir(repo), f"{r['flight']}.json"), r)
                                                   for r in lanes.records(repo)]
        except Exception:  # noqa: BLE001 - unreadable now; keep it indexed and retry next beat
            keep.append(repo)
            continue
        targets = [(t, r) for t, r in targets if r]
        if targets:
            keep.append(repo)
        for target, record in targets:
            if "heartbeat_at" in record and not stale(record, now) and os.path.exists(target):
                with contextlib.suppress(OSError):
                    _write(target, dict(record, heartbeat_at=now, renewed_at=now))
                    renewed += 1
    if keep != indexed():
        with contextlib.suppress(OSError):
            _write(INDEX, keep)
    return renewed


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
    from . import lanes
    if lanes.records(repo):
        raise Owned("write_set_lanes:" + ",".join(r["flight"] for r in lanes.records(repo)))
    rc, err = lane_lock("check", repo, flight, pid, "--write-set")  # no session holds any file
    if rc:
        raise Owned(f"lane_lock:{err[:200]}")
    rc, err = lane_lock("acquire", repo, flight, pid)
    if rc:
        raise Owned(f"lane_lock:{err[:200]}")
    landing._git(repo, "fetch", "--quiet", "origin", branch, check=False)
    clean = not landing._git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    behind = landing._git(repo, "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}", check=False)
    if clean and behind.returncode == 0:
        landing._git(repo, "merge", "--ff-only", "--quiet", f"origin/{branch}", check=False)
    record = {**stamp(flight, pid, ttl_s, "whole_repo", None), "branch": branch,
              "head": landing._git(repo, "rev-parse", "HEAD").stdout.strip(), "baseline": dirty(repo)}
    with open(path(repo), "w") as f:
        json.dump(record, f)
    _index_add(repo)
    return record


def release(repo, flight):
    record = read(repo)
    if record and record["flight"] == flight:
        with contextlib.suppress(FileNotFoundError):
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
    held = landing.HELD_PREFIX + record["flight"]
    tip = landing.remote_tip(landing.target_key(repo, held))
    if tip:  # an earlier recovery already pushed this flight's work; re-holding can only fail non-fast-forward
        release(repo, record["flight"])
        return {"state": "HELD", "reason": "already_held", "flight": record["flight"], "sha": tip, "branch": held}
    paths, collisions = flight_paths(repo, record)
    moved = landing._git(repo, "rev-parse", "HEAD").stdout.strip() != record["head"]
    result = {"state": "CLOSED", "reason": "no_change", "flight": record["flight"]}
    if paths or moved:  # capture to a held ref, but restore nothing: the paths may be another session's bytes
        result = landing.hold(repo, record, paths, list(paths), "crashed", comment)
    if landing.terminal(repo, result):
        release(repo, record["flight"])
    return result
