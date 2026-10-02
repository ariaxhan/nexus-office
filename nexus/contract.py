"""The issue contract Tower reads, and the ONE eligibility gate every Tower path uses.

Triage (thinking-brain-school bin/tbs-issues-hygiene.py) writes a fenced block into each ready issue:

    ```tbs-contract
    depends_on: ["owner/repo#N"]
    write_set: ["path"] | null
    route: "claude" | "antigravity"
    check: "command" | null
    acceptance: "text" | null
    ```

One JSON value per line (valid YAML). Prose stays for humans; Tower never parses prose.
Done is never inferred from an executor exit: see `done_receipt`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

KEYS = ("depends_on", "write_set", "route", "check", "acceptance")
ROUTES = {"claude": "code-judgment", "antigravity": "customer-copy-antigravity"}
ANTIGRAVITY_BIN = os.environ.get("TBS_ANTIGRAVITY_BIN", os.path.expanduser(
    "~/Developer/Vaults/CodingVault/thinking-brain-school/tbs-agy"))
REF = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")


def parse(body):
    """The contract dict, or None when absent or invalid."""
    m = re.search(r"```tbs-contract\n(.*?)\n```", body or "", re.S)
    if not m:
        return None
    try:
        c = {k.strip(): json.loads(v) for k, v in (line.split(":", 1) for line in m.group(1).splitlines())}
    except ValueError:
        return None
    if set(c) != set(KEYS) or c["route"] not in ROUTES:
        return None
    if not isinstance(c["depends_on"], list) or not all(isinstance(d, str) and REF.match(d) for d in c["depends_on"]):
        return None
    if c["write_set"] is not None and not (isinstance(c["write_set"], list) and all(isinstance(p, str) for p in c["write_set"])):
        return None
    if c["check"] is not None and not isinstance(c["check"], str):
        return None
    c["depends_on"] = [d.lower() for d in c["depends_on"]]
    return c


def _issue_state(ref, gh):
    repo, number = ref.split("#")
    proc = gh(["gh", "api", f"repos/{repo}/issues/{number}", "--jq", ".state"])
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _gh(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def route_available(route):
    return route != "antigravity" or os.access(ANTIGRAVITY_BIN, os.X_OK)


def gate(issue, *, required=True, window_open=None, gh=_gh):
    """(contract, None) when this issue may run now; (contract|None, reason) otherwise.

    Every Tower path calls this: plan waves, fallback selection, retries and review resumption.
    required=False admits a contract-less issue for repos triage does not cover (legacy rows)."""
    c = parse(issue.get("body"))
    if c is None:
        return None, ("no valid tbs-contract block; triage writes it" if required else None)
    open_deps = [d for d in c["depends_on"] if _issue_state(d, gh) != "closed"]
    if open_deps:
        return c, "blocked by open dependency " + ", ".join(open_deps)
    if not route_available(c["route"]):
        return c, f"route {c['route']} unavailable on this host"
    labels = {str(l.get("name", "")).lower() for l in issue.get("labels", [])}
    if "sensitive" in labels and window_open is not None and not window_open():
        return c, "sensitive: outside the 01:00-08:00 KST window"
    return c, None


#: a landed commit nothing verified: re-flying cannot help, so it is held for a person
UNVERIFIED = "unverified:"


def _patch_id(git, *diff_args, run=subprocess.run):
    diff = git("diff", "--binary", "--unified=0", *diff_args)  # no context: main moving nearby is not a new change
    if diff.returncode or not diff.stdout:
        return None
    out = run(["git", "patch-id", "--stable"], input=diff.stdout, capture_output=True, text=True, timeout=30)
    return out.stdout.split()[0] if out.stdout.split() else None


def reviewed_receipt(sha, review, checkout, run=subprocess.run):
    """(done, reason) from a DURABLE review record: a PASS for head H, and the landed commit carries
    exactly H's change (same patch-id), so a base that moved under the review cannot slip through."""
    if not review or review.get("verdict") != "PASS" or not review.get("head"):
        return False, f"{UNVERIFIED} landed {sha} with no contract check and no recorded review PASS"
    head = review["head"]
    git = lambda *a: run(["git", *a], cwd=checkout, capture_output=True, text=True, timeout=300)  # noqa: E731
    if git("fetch", "--quiet", "origin", sha, head).returncode:
        return False, f"{UNVERIFIED} cannot fetch landed {sha} and reviewed {head} to compare"
    base = git("merge-base", f"{sha}^", head).stdout.strip()
    landed, reviewed = _patch_id(git, f"{sha}^", sha, run=run), base and _patch_id(git, base, head, run=run)
    if not landed or landed != reviewed:
        return False, f"{UNVERIFIED} landed {sha} is not the change reviewed at {head}"
    return True, f"landed {sha}; review PASS recorded for {head}, same change"


def done_receipt(result, contract_, checkout, run=subprocess.run, review=None, budget=None):
    """Prove the landed change within the caller's remaining budget; timeouts remain unproven."""
    def bounded(argv, **kw):
        if budget is not None:
            cleanup = argv[:3] == ["git", "worktree", "remove"]
            kw["timeout"] = 10 if cleanup else budget(kw.get("timeout", 300))
        return run(argv, **kw)
    try:
        return _done_receipt(result, contract_, checkout, bounded, review)
    except subprocess.TimeoutExpired:
        return False, f"{UNVERIFIED} receipt budget exhausted at {result.get('sha')}"


def _done_receipt(result, contract_, checkout, run, review):
    """(done, reason). Done needs a landed commit AND the contract CHECK passing at that exact sha.

    The check runs in a throwaway detached worktree of the landed sha, never the canonical checkout,
    which a PR merge does not move. A sha that cannot be checked out is not done."""
    if result.get("state") != "LANDED" or not result.get("sha"):
        return False, f"no landed commit ({result.get('state')}: {result.get('reason')})"
    sha = result["sha"]
    check = (contract_ or {}).get("check")
    if not check:  # a missing verifier is not a passing one; a recorded review PASS of this change is
        return reviewed_receipt(sha, review, checkout, run)
    state, why = check_at(sha, check, checkout, run=run)
    return state == "passed", why


def check_at(sha, check, checkout, run=subprocess.run, budget=None):
    """Check one immutable head. Unrunnable is infrastructure; failed is evidence about the change."""
    underlying = run
    def run(argv, **kw):
        if budget is not None:
            kw["timeout"] = 10 if argv[:3] == ["git", "worktree", "remove"] else budget(kw.get("timeout", 300))
        return underlying(argv, **kw)
    tmp = tempfile.mkdtemp(prefix="nexus-receipt-")
    tree = os.path.join(tmp, "tree")
    git = lambda *a: run(["git", *a], cwd=checkout, capture_output=True, text=True, timeout=300)  # noqa: E731
    try:
        if git("fetch", "--quiet", "origin", sha).returncode:
            return "unrunnable", f"cannot fetch {sha} to verify"
        if git("worktree", "add", "--detach", tree, sha).returncode:
            return "unrunnable", f"cannot check out {sha} to verify"
        if os.path.exists(os.path.join(tree, "package-lock.json")):  # a fresh tree has no node_modules: an npm
            # check would fail on its own tools (esbuild exit 127, #192) and call merged work unproven
            if run(["bash", "-c", "npm ci --prefer-offline --no-audit --no-fund"], cwd=tree,
                   capture_output=True, text=True, timeout=900).returncode:
                return "unrunnable", f"cannot install dependencies at {sha} to verify"
        # Tower's own environment, not a login shell: a login PATH puts /usr/bin first (python3 3.9), so the
        # receipt would judge the change with different tools than every check that passed before merge
        proc = run(["bash", "-c", check], cwd=tree, capture_output=True, text=True, timeout=1800)
        if proc.returncode:
            tail = ((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")).strip()[-1500:]
            return ("unrunnable" if proc.returncode in (126, 127) else "failed"), f"contract check failed at {sha}: {check} (exit {proc.returncode})\n{tail}"
        return "passed", f"landed {sha}; check passed at {sha}: {check}"
    except subprocess.TimeoutExpired:
        return "unrunnable", f"{UNVERIFIED} receipt budget exhausted at {sha}"
    finally:
        try:
            git("worktree", "remove", "--force", tree)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
