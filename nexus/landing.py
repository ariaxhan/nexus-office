"""Landing: the only path from a hangar to shared state.

A flight with a target works in a hangar: `git clone --shared` of the human
checkout, on the target branch, whose `origin` is the checkout's own remote.
Landing commits the declared outputs there and pushes. GitHub and sqlite cannot
share a transaction, so the ledger records `applying` with the expected sha
BEFORE the push and `applied` after; every `applying` row found on a restart is
reconciled against the remote tip (`remote_tip`), never guessed at.

A human's tree is fast-forwarded only: on the target branch, clean, and only to
the sha just applied. Anything else is left alone and recorded.
"""

from __future__ import annotations

import os
import subprocess

HANGAR_DIR = "repo"
GIT_TIMEOUT_S = 120.0


class LandingError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _git(cwd, *args, check=True, timeout=GIT_TIMEOUT_S):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and proc.returncode != 0:
        raise LandingError("git_failed", f"git {' '.join(args)}: {proc.stderr.strip()[:400]}")
    return proc


def hangar_path(workspace: str) -> str:
    return os.path.join(workspace, HANGAR_DIR)


def target_of(plan_inputs: dict):
    """(repo checkout path, branch) or None. The branch defaults to the checkout's."""
    target = (plan_inputs or {}).get("target")
    if not target or not target.get("repo"):
        return None
    repo = os.path.expanduser(target["repo"])
    branch = target.get("branch") or _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return repo, branch


def origin_url(repo: str) -> str:
    return _git(repo, "remote", "get-url", "origin").stdout.strip()


def target_key(repo: str, branch: str) -> str:
    """The landing row's `target`: what to ask, and where, on reconcile."""
    return f"{origin_url(repo)}#{branch}"


def clone_hangar(repo: str, branch: str, workspace: str) -> str:
    """A shared clone of the human checkout on the target branch, pushing to its origin."""
    dst = hangar_path(workspace)
    os.makedirs(workspace, exist_ok=True)
    _git(workspace, "clone", "--shared", "--quiet", "--no-checkout", repo, dst)
    _git(dst, "remote", "set-url", "origin", origin_url(repo))
    _git(dst, "fetch", "--quiet", "origin", branch)
    _git(dst, "checkout", "--quiet", "-B", branch, "FETCH_HEAD")
    _git(dst, "config", "user.name", "nexus tower")
    _git(dst, "config", "user.email", "nexus@localhost")
    return dst


def changed_paths(hangar: str):
    """Every path git would commit: modified, added, deleted, untracked."""
    out = _git(hangar, "status", "--porcelain", "--untracked-files=all", check=False).stdout
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return sorted(paths)


def commit_outputs(hangar: str, outputs, message: str):
    """Commit the declared outputs. Returns the new sha, or HEAD when nothing changed."""
    if outputs:
        _git(hangar, "add", "--", *outputs)
    else:
        _git(hangar, "add", "-A")
    staged = _git(hangar, "diff", "--cached", "--quiet", check=False).returncode
    if staged == 0:
        return _git(hangar, "rev-parse", "HEAD").stdout.strip(), False
    _git(hangar, "commit", "--quiet", "-m", message)
    return _git(hangar, "rev-parse", "HEAD").stdout.strip(), True


def push(hangar: str, branch: str):
    """Fast-forward only. A rejection is a fact to report, never something to force."""
    proc = _git(hangar, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip()
        code = "push_rejected" if ("rejected" in err or "fetch first" in err
                                   or "non-fast-forward" in err) else "push_failed"
        raise LandingError(code, err[:400])


def remote_tip(target: str):
    """The branch tip at the remote named by a landing's target key, or None if absent."""
    url, _, branch = target.rpartition("#")
    proc = subprocess.run(["git", "ls-remote", url, f"refs/heads/{branch}"],
                          capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if proc.returncode != 0:
        raise LandingError("remote_unreachable", proc.stderr.strip()[:400])
    line = proc.stdout.strip().split("\n")[0] if proc.stdout.strip() else ""
    return line.split()[0] if line else None


def fast_forward(repo: str, branch: str, sha: str) -> str:
    """Move a person's tree forward to `sha`, only when that is all it would do.

    Returns what happened: `fast_forwarded`, `already`, `dirty`, `other_branch`,
    `not_fast_forward`. Never touches an unclean tree or a different branch.
    """
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if head != branch:
        return "other_branch"
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return "dirty"
    if _git(repo, "rev-parse", "HEAD").stdout.strip() == sha:
        return "already"
    _git(repo, "fetch", "--quiet", "origin", branch)
    ancestor = _git(repo, "merge-base", "--is-ancestor", "HEAD", sha, check=False)
    if ancestor.returncode != 0:
        return "not_fast_forward"
    _git(repo, "merge", "--ff-only", "--quiet", sha)
    return "fast_forwarded"
