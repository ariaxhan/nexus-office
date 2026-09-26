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
import tempfile

HANGAR_DIR = "repo"
GIT_TIMEOUT_S = 120.0


class LandingError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _git(cwd, *args, check=True, timeout=GIT_TIMEOUT_S, env=None):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
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


def commit_outputs(hangar: str, outputs, message: str):
    """Commit the declared outputs. Returns the new sha, or HEAD when nothing changed."""
    _git(hangar, "add", "--", *outputs)
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


# In-place landing on the canonical checkout (Tower v2). No clone, no branch switch:
# commits are built from a temporary index, so the person's index and HEAD stay theirs.

HELD_PREFIX = "aria/held/"
GIT_LOCK = os.path.expanduser("~/Developer/Vaults/_meta/services/vault-git-lock.py")


def _locked(repo, *args):
    """A write to the real index or refs goes through the vault git mutex when present."""
    if os.path.exists(GIT_LOCK):
        proc = subprocess.run(["python3", GIT_LOCK, repo, "--", "git", *args], cwd=repo,
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        if proc.returncode:
            raise LandingError("git_failed", f"git {' '.join(args)}: {proc.stderr.strip()[:400]}")
        return proc
    return _git(repo, *args)


def commit_paths(repo, parent, paths, message):
    """A commit of `parent` plus the working-tree bytes of `paths`, via a temp index."""
    fd, index = tempfile.mkstemp(prefix="nexus-index-")
    os.close(fd)
    os.remove(index)
    env = {"GIT_INDEX_FILE": index}
    try:
        _git(repo, "read-tree", parent, env=env)
        for p in paths:
            if os.path.lexists(os.path.join(repo, p)):
                _git(repo, "update-index", "--add", "--", p, env=env)
            else:
                _git(repo, "update-index", "--force-remove", "--", p, env=env)
        tree = _git(repo, "write-tree", env=env).stdout.strip()
    finally:
        if os.path.exists(index):
            os.remove(index)
    return _git(repo, "commit-tree", tree, "-p", parent, "-m", message).stdout.strip()


def push_ref(repo, sha, branch):
    return _git(repo, "push", "--quiet", "origin", f"{sha}:refs/heads/{branch}", check=False).returncode == 0


def _replace_own(repo, sha, branch):
    """A repair rebuilds from main, so its push cannot fast-forward the PR branch. Replace the branch only while
    its tip is still Nexus's own commit: a person's commit on it is never overwritten."""
    if _git(repo, "fetch", "--quiet", "origin", f"refs/heads/{branch}", check=False).returncode:
        return False
    tip = _git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    if "\nNexus-Flight: " not in _git(repo, "log", "-1", "--format=%B", tip).stdout:
        return False
    return _git(repo, "push", "--quiet", f"--force-with-lease=refs/heads/{branch}:{tip}", "origin",
                f"{sha}:refs/heads/{branch}", check=False).returncode == 0


def restore(repo, paths, head):
    """Put only these paths back to `head`; files `head` never had are removed."""
    for p in paths:  # index too: an executor that ran `git rm`/`git add` must not leave it staged
        if _git(repo, "cat-file", "-e", f"{head}:{p}", check=False).returncode == 0:
            _git(repo, "restore", f"--source={head}", "--staged", "--worktree", "--", p)
            continue
        _git(repo, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", p, check=False)
        if os.path.lexists(os.path.join(repo, p)):
            os.remove(os.path.join(repo, p))


def _record(state, record, **extra):
    return {"state": state, "flight": record["flight"], **extra}


def failed_check(check, repo, run):
    """None when the check passes, else what failed and the end of its output: a hold that says only
    `check_failed` sends the next reader to rerun the check to learn what it said."""
    proc = run(check, cwd=repo, capture_output=True, text=True, timeout=1800)
    if not proc.returncode:
        return None
    name = check if isinstance(check, str) else " ".join(check)
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-1500:]
    return f"`{name}` exited {proc.returncode}" + (f"\n\n```\n{tail}\n```" if tail else "")


def _body(record, reason, branch, sha, detail):
    """Only the detail's first line goes public: check output can carry environment values."""
    body = f"Nexus flight {record['flight']} HELD ({reason}): work on `{branch}` at {sha}."
    return f"{body} {detail.splitlines()[0]}; output in the ledger." if detail else body


def hold(repo, record, paths, collisions, reason, comment=None, detail=None):
    """Push the flight's work to aria/held/<flight>, comment, restore non-collision paths.

    Collision paths were dirty before the flight: they may hold a person's bytes and are left."""
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    sha = commit_paths(repo, head, paths, f"HELD {reason}\n\nNexus-Flight: {record['flight']}") if paths else head
    branch = HELD_PREFIX + record["flight"]
    if not push_ref(repo, sha, branch):
        raise LandingError("held_push_failed", branch)
    restore(repo, [p for p in paths if p not in collisions], head)  # the push made the bytes durable
    url, why = notify(comment, _body(record, reason, branch, sha, detail))
    return _record("HELD", record, reason=reason, sha=sha, branch=branch, comment_url=url,
                   comment_error=why, paths=paths, **({"detail": detail} if detail else {}))


def notify(comment, body):
    """Best effort: (url, None) or (None, reason). A failed comment never undoes a durable HELD."""
    if not comment:
        return None, None
    try:
        return comment(body), None
    except Exception as exc:  # noqa: BLE001 - the notification is not the outcome
        return None, f"{type(exc).__name__}: {exc}"


def direct(repo, record, message, comment=None):
    paths, collisions = _flight_paths(repo, record)
    if not paths:
        return _record("CLOSED", record, reason="no_change")
    if collisions:
        return hold(repo, record, paths, collisions, "collision", comment)
    branch = record["branch"]
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != remote_tip(target_key(repo, branch)):
        return hold(repo, record, paths, collisions, "not_at_origin", comment)
    sha = commit_paths(repo, head, paths, f"{message}\n\nNexus-Flight: {record['flight']}")
    if not push_ref(repo, sha, branch):
        return hold(repo, record, paths, collisions, "push_rejected", comment)
    _locked(repo, "update-ref", f"refs/heads/{branch}", sha, head)
    _locked(repo, "update-index", "--add", "--remove", "--", *paths)
    return _record("LANDED", record, sha=sha, branch=branch, paths=paths)


def review(repo, record, message, issue, pr_create, comment=None, reason="in_review"):
    """Branch on origin first, then the PR, then the tree goes back to its human state."""
    paths, collisions = _flight_paths(repo, record)
    if not paths:
        return _record("CLOSED", record, reason="no_change")
    if collisions:
        return hold(repo, record, paths, collisions, "collision", comment)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    sha = commit_paths(repo, head, paths, f"{message}\n\nNexus-Flight: {record['flight']}")
    branch = f"aria/issue-{issue}"
    if not push_ref(repo, sha, branch) and not _replace_own(repo, sha, branch):
        return hold(repo, record, paths, collisions, "branch_push_rejected", comment)
    url = pr_create(branch, record["branch"], f"{message}\n\nCloses #{issue}")
    restore(repo, paths, head)
    _, why = notify(comment, f"needs Aria: {reason}. PR {url}") if reason != "in_review" else (None, None)
    return _record("HELD", record, reason=reason, sha=sha, branch=branch, pr_url=url, comment_error=why, paths=paths)


def nothing_landed(record, proc):
    """No paths changed: a clean exit is no_change; a crash is a failure, never a no-change close."""
    if proc.returncode:
        return _record("FAILED", record, reason=f"exit_{proc.returncode}")
    return _record("CLOSED", record, reason="no_change")


def _flight_paths(repo, record):
    from . import lease
    return lease.flight_paths(repo, record)


def terminal(repo, result):
    """True only when the claimed terminal state is proven against origin."""
    state, sha, branch = result.get("state"), result.get("sha"), result.get("branch")
    try:
        if state == "LANDED" and sha and branch:
            tip = remote_tip(target_key(repo, branch))
            if not tip:
                return False
            _git(repo, "fetch", "--quiet", "origin", branch, check=False)
            return _git(repo, "merge-base", "--is-ancestor", sha, tip, check=False).returncode == 0
        if state == "CLOSED":
            if result.get("reason") == "no_change":
                return not sha
            return bool(branch) and remote_tip(target_key(repo, branch)) is None and bool(result.get("reason"))
        if state == "HELD" and sha and branch:
            return (remote_tip(target_key(repo, branch)) == sha
                    and bool(result.get("comment_url") or result.get("pr_url")))
    except LandingError:
        return False
    return crashed(result)


def crashed(result):
    """A proven executor crash: nonzero exit, nothing committed."""
    return result.get("state") == "FAILED" and not result.get("sha") and str(result.get("reason", "")).startswith("exit_")


def require_terminal(repo, result):
    if not terminal(repo, result):
        raise LandingError("not_terminal", f"{result.get('state')} unproven for {result.get('flight')}")
    return result
