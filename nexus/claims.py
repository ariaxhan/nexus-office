#!/usr/bin/env python3
"""Claim-before-edit registry (#311): one claim record per (file, lane), shared by every worktree.

Adopted from the TBS reference (thinking-brain-school bin/lane-claim.py, 50c7ceb6b, TBS #769):
same store (<git-common-dir>/lane-claims/<sha256(file\\0lane)>.json), same record keys, same
release ledger (releases.jsonl), same exit codes, so either CLI reads the other's claims.
Not used unchanged because its unclaimed-file rule is hardwired to TBS branch prefixes and
TBS source dirs; Nexus lanes run on main in one checkout and are named by work order.
Differences: `work_order` on every record; a lane is identified by --lane, $TBS_LANE_ID, or a
lane branch; any path counts as source; --root lets a non-git tree keep claims in <root>/.lane-claims.

Record: {file, lane_id, work_order, issue, claimed_at, heartbeat_at, expires_at}. A claim expires
when its heartbeat lapses (expires_at = heartbeat_at + ttl) and is released on the next read.

  claims.py claim <paths> --lane L --work-order W [--ttl S]   exit 3 if another live lane holds one
  claims.py check <paths> [--landing]   exit 3: another lane's claim; with --landing an identified
                                         lane must also hold a claim on every path
  claims.py heartbeat|release [<paths>|--all] --lane L
  claims.py list [<paths>]
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

DEFAULT_TTL = int(os.environ.get("TBS_CLAIM_TTL", "14400"))
LANE_BRANCHES = ("prose-build-", "lane/", "aria/issue-", "aria/held/")


class Refused(Exception):
    """A claim or landing was refused; .rows are the claims that refused it."""

    def __init__(self, message, rows=()):
        super().__init__(message)
        self.rows = list(rows)


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                   stderr=subprocess.DEVNULL).strip()


def toplevel(root):
    try:
        return Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    except (subprocess.CalledProcessError, OSError):
        return Path(root).resolve()


def store(root):
    try:
        return Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "lane-claims"
    except (subprocess.CalledProcessError, OSError):
        return Path(root).resolve() / ".lane-claims"


def branch_of(root):
    try:
        return _git(root, "branch", "--show-current")
    except (subprocess.CalledProcessError, OSError):
        return ""


def normalize(root, path):
    top = toplevel(root)
    target = (top / path).resolve()
    try:
        rel = target.relative_to(top).as_posix()
    except ValueError:
        raise ValueError(f"outside worktree: {path}")
    if rel in ("", ".") or rel.startswith((".git/", ".lane-claims/")):
        raise ValueError(f"not a source path: {path}")
    return rel


def _record_path(home, path, lane):
    return home / (hashlib.sha256((path + "\0" + lane).encode()).hexdigest() + ".json")


def _load(home):
    rows = []
    for file in sorted(home.glob("*.json")):
        try:
            row = json.loads(file.read_text())
        except (OSError, ValueError):
            raise RuntimeError(f"corrupt claim: {file}")
        if isinstance(row, dict) and "file" in row and "lane_id" in row:
            rows.append((file, row))
    return rows


def _release(home, file, row, reason, now, events):
    file.unlink()
    event = dict(row, released_at=now, release_reason=reason)
    with (home / "releases.jsonl").open("a") as out:
        out.write(json.dumps(event, sort_keys=True) + "\n")
    events.append(event)


@contextlib.contextmanager
def _registry(root):
    """(home, live rows, events) under the registry mutex; lapsed claims are released first."""
    home = store(root)
    home.mkdir(parents=True, exist_ok=True)
    with (home / ".mutex").open("a") as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        now, events, rows = time.time(), [], []
        for file, row in _load(home):
            if row.get("expires_at", 0) <= now:
                _release(home, file, row, "expired", now, events)
            else:
                rows.append((file, row))
        yield home, rows, events, now


def claim(root, paths, lane, work_order, ttl=DEFAULT_TTL):
    """Claim every path for `lane`, all or nothing; Refused names the other lanes' live claims."""
    paths = sorted({normalize(root, p) for p in paths})
    with _registry(root) as (home, rows, _, now):
        conflicts = [row for _, row in rows if row["file"] in paths and row["lane_id"] != lane]
        if conflicts:
            raise Refused("claimed by another live lane", conflicts)
        out = []
        for path in paths:
            row = next((r for _, r in rows if r["file"] == path and r["lane_id"] == lane), None) or {
                "file": path, "lane_id": lane, "work_order": str(work_order), "issue": _issue(work_order),
                "claimed_at": now}
            row.update(heartbeat_at=now, expires_at=now + ttl)
            _record_path(home, path, lane).write_text(json.dumps(row, sort_keys=True) + "\n")
            out.append(row)
        return out


def check(root, paths, lane, landing=False):
    """[(path, reason, rows)] refusals: another lane's live claim, or (landing) no claim of this lane."""
    paths = sorted({normalize(root, p) for p in paths})
    with _registry(root) as (_, rows, _, _):
        refused = []
        for path in paths:
            matches = [row for _, row in rows if row["file"] == path]
            if any(row["lane_id"] != lane for row in matches):
                refused.append((path, "claimed by another live lane", matches))
            elif landing and lane and not matches:
                refused.append((path, f"no claim for lane {lane}", matches))
        return refused


def touch(root, lane, paths=None, release=False, ttl=DEFAULT_TTL):
    """Heartbeat or release this lane's claims (all of them when paths is None)."""
    wanted = None if paths is None else {normalize(root, p) for p in paths}
    with _registry(root) as (home, rows, events, now):
        out = []
        for file, row in rows:
            if row["lane_id"] != lane or (wanted is not None and row["file"] not in wanted):
                continue
            if release:
                _release(home, file, row, "clean_finish", now, events)
                out.append(events[-1])
            else:
                row.update(heartbeat_at=now, expires_at=now + ttl)
                file.write_text(json.dumps(row, sort_keys=True) + "\n")
                out.append(row)
        return out


def listing(root, paths=()):
    wanted = {normalize(root, p) for p in paths}
    with _registry(root) as (_, rows, events, _):
        return events + [row for _, row in rows if not wanted or row["file"] in wanted]


def _issue(work_order):
    m = re.search(r"(\d+)$", str(work_order or ""))
    return m.group(1) if m else ""


def identified_lane(explicit, branch):
    """The lane named for this process, or None for an unidentified committer (a person on main)."""
    lane = explicit or os.environ.get("TBS_LANE_ID")
    if lane:
        return lane
    return branch if branch.startswith(LANE_BRANCHES) else None


def _emit(row):
    print(json.dumps(row, sort_keys=True))


def _cmd_check(args, named, lane):
    refused = check(args.root, args.paths, named or lane, landing=args.landing and bool(named))
    for path, reason, rows in refused:
        print(f"REFUSED: {path}: {reason}", file=sys.stderr)
        for row in rows:
            print(json.dumps(row, sort_keys=True), file=sys.stderr)
    return 3 if refused else 0


def _cmd_claim(args, named, work_order, parser):
    if not named or not work_order:
        parser.error("claim requires --lane (or $TBS_LANE_ID) and --work-order")
    try:
        rows = claim(args.root, args.paths, named, work_order, args.ttl)
    except Refused as exc:
        for row in exc.rows:
            print("REFUSED: existing claim " + json.dumps(row, sort_keys=True), file=sys.stderr)
        return 3
    for row in rows:
        _emit(row)
    return 0


def _parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["claim", "release", "heartbeat", "list", "check"])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--root", default=".")
    parser.add_argument("--lane")
    parser.add_argument("--work-order", "--issue", dest="work_order")
    parser.add_argument("--branch")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    parser.add_argument("--all", action="store_true", help="release/heartbeat every claim of this lane")
    parser.add_argument("--landing", action="store_true", help="an identified lane must hold a claim on every path")
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if args.ttl < 1:
        parser.error("--ttl must be positive")
    branch = args.branch if args.branch is not None else branch_of(args.root)
    named = identified_lane(args.lane, branch)
    lane = named or branch or "unidentified"
    if args.command == "list":
        rows = listing(args.root, args.paths)
    elif not args.paths and not (args.command in ("release", "heartbeat") and args.all):
        parser.error(f"{args.command} requires paths" + (" or --all" if args.command != "check" else ""))
    elif args.command == "check":
        return _cmd_check(args, named, lane)
    elif args.command == "claim":
        return _cmd_claim(args, named, args.work_order or os.environ.get("COORD_ISSUE") or _issue(branch), parser)
    else:
        rows = touch(args.root, lane, None if args.all else args.paths, args.command == "release", args.ttl)
    for row in rows:
        _emit(row)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"lane-claim: {exc}", file=sys.stderr)
        sys.exit(2)
