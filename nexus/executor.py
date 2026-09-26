"""One write flight on a canonical checkout: lease, run, check, classify, land, prove.

Everything project-specific is registry data: `check`, `risk`, `roads`. A road
(e.g. lessons) names the exact skills and docs the agent is given and the only
command the flight may run.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time

from . import landing, lanes, lease, risk

ROUTER = os.path.expanduser("~/Developer/Vaults/_meta/services/tbs/account-router.sh")


def batch_rules(spec):
    """Prompt rules from per-product road data; no product names in code."""
    rules = ["Read every prior lesson of this product in full before writing. Write the batch in order."]
    rules.append("Keep continuity with each other and with the last shipped lesson." if spec.get("continuity")
                 else "Each lesson stands alone and delivers an outcome usable immediately.")
    if spec.get("second_lighter"):
        rules.append("The second lesson of the week is lighter than the first.")
    if spec.get("note"):
        rules.append(spec["note"])
    return " ".join(rules)


class RoadError(Exception):
    pass


def road_for(entry, labels):
    labels = {str(l).lower() for l in labels}
    hits = [(name, road) for name, road in (entry.get("roads") or {}).items()
            if labels & {l.lower() for l in road.get("match", {}).get("labels", ())}]
    if len(hits) > 1:
        raise RoadError("conflicting roads: " + ", ".join(n for n, _ in hits))
    return hits[0] if hits else (None, None)


def product_of(road, labels):
    products = road.get("match", {}).get("products", {})
    found = [p for p in products if p in {str(l).lower() for l in labels}]
    if len(found) != 1:
        raise RoadError("lesson issue must carry exactly one product label: " + ", ".join(products))
    return found[0]


def lesson_batch(repo, road, product):
    """The `batch_size` catalog rows after the last written lesson on origin main, in order.

    Never a self-chosen topic: a missing row or unapproved outline refuses the flight."""
    branch = road.get("catalog_branch", "main")
    raw = landing._git(repo, "show", f"origin/{branch}:{road['catalog']}").stdout
    number = lambda r: int(re.sub(r"\D", "", r["lesson"]) or 0)  # noqa: E731
    rows = sorted((r for r in json.loads(raw) if r.get("product") == product), key=number)
    written = [number(r) for r in rows if (r.get("status") or {}).get("present", {}).get("text")
               or re.search(r"Published|Review|hub|drip", (r.get("status") or {}).get("phase", ""))]
    last = max(written, default=0)
    todo = [r for r in rows if number(r) > last][:int(road["match"]["products"][product]["batch_size"])]
    if not todo:
        raise RoadError(f"{product}: no catalog row after L{last:03d}")
    unapproved = [r["lesson"] for r in todo if (r.get("decision") or {}).get("approval") != "approved"]
    if unapproved:
        raise RoadError(f"{product}: no approved outline for {', '.join(unapproved)}")
    return [r["lesson"] for r in todo]


def road_prompt(repo, road, extra=""):
    """The road's skills and docs, in full. A missing path refuses the flight."""
    parts = []
    for skill in road.get("skills", ()):
        p = os.path.join(repo, ".claude", "skills", skill, "SKILL.md")
        if not os.path.isfile(p):
            raise RoadError(f"missing skill: {p}")
        parts.append(f"## skill {skill}\n{open(p).read()}")
    for doc in road.get("docs", ()):
        p = os.path.join(repo, doc)
        if not os.path.isfile(p):
            raise RoadError(f"missing doc: {p}")
        parts.append(f"## doc {doc}\n{open(p).read()}")
    return "\n\n".join(parts + [extra])


def issue_prompt(entry, issue):
    return (f"Resolve {entry['repo']} issue #{issue['number']}: {issue.get('title', '')}\n\n"
            f"{issue.get('body') or ''}\n\nEdit files in place in this checkout on "
            f"{entry.get('default_branch', 'main')}. Read the latest issue comments first. "
            "If Tower retained work on an aria/held branch, inspect and reuse it before editing. "
            "Do not commit, push, branch, clone, stash or "
            f"open PRs; Nexus lands the change. Run the project's own checks.\n"
            f"Product guidance: {entry.get('product_guidance', '')}{issue.get('nexus_evidence', '')}")


ANTIGRAVITY = re.compile(r"antigravity|tbs-agy|copy[- ]authority", re.I)


def antigravity_method(issue):
    """The issue's METHOD line (or a copy-authority label) names Antigravity as the author."""
    labels = {str(l.get("name", "")).lower() for l in issue.get("labels", [])}
    if labels & {"copy-authority", "route-antigravity", "antigravity"}:
        return True
    return any(ANTIGRAVITY.search(line) for line in (issue.get("body") or "").splitlines()
               if re.match(r"\W*METHOD\b", line, re.I))


def plan(entry, issue):
    """(argv, prompt, road name, mode override). Pure; refuses before any lease."""
    labels = [l["name"] for l in issue.get("labels", [])]
    name, road = road_for(entry, labels)
    if not road and antigravity_method(issue):
        prompt = (issue_prompt(entry, issue) + f"\nCheckout (absolute; edit files only here): {entry['path']}\n"
                  "You are the Antigravity author this issue's METHOD names.")
        return [ROUTER, "run", "customer-copy-antigravity", "--", "-p", prompt], prompt, None, None
    if not road:
        prompt = issue_prompt(entry, issue)
        return [ROUTER, "run", "code-judgment", "--", "-p", prompt, "--allowedTools",
                "Bash,Read,Edit,Write,Glob,Grep,Skill", "--dangerously-skip-permissions"], prompt, None, None
    product = product_of(road, labels)
    batch = lesson_batch(entry["path"], road, product) if road.get("catalog") else []
    spec = road["match"]["products"][product]
    rules = batch_rules(spec) if road.get("catalog") else ""
    prompt = road_prompt(entry["path"], road, f"{issue_prompt(entry, issue)}\n\nBatch: {', '.join(batch)}\n{rules}")
    argv = [a.format(product=product, batch=",".join(batch)) for a in road["command"]]
    return argv, prompt, name, road.get("risk")


def provider_fallback(argv, prompt, repo):
    """Translate the routed CLI invocation, preserving the issue's copy authority."""
    if len(argv) < 4 or argv[0] != ROUTER or argv[1] != "run":
        return None
    kind = argv[2]
    if kind == "customer-copy-antigravity":
        return None
    note = ("The previous provider failed. Inspect current files and external state first. "
            "Continue unfinished work; do not repeat completed sends, publishes, merges, "
            "payments, or other external actions.\n\n" + prompt)
    if kind == "code-judgment":
        return [ROUTER, "run-provider", kind, "codex", "--", "exec", "--json", "--ephemeral",
                "--model", "gpt-6-sol", "--sandbox", "danger-full-access",
                "--skip-git-repo-check", "-C", repo, note]
    return [ROUTER, "run-provider", kind, "claude", "--", "-p", note,
            "--allowedTools", "Bash,Read,Edit,Write,Glob,Grep,Skill",
            "--dangerously-skip-permissions"]


def invoke(argv, *, cwd, env, input, timeout, run):
    """Bound the whole provider process group before starting its replacement."""
    if run is not subprocess.run:
        try:
            return run(argv, cwd=cwd, env=env, input=input, text=True,
                       capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", "provider timeout")
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if input else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(input, timeout=timeout)
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return subprocess.CompletedProcess(argv, 124, "", "provider timeout")


def fly(entry, issue, flight, *, pr_create, comment, timeout_s=900, run=subprocess.run, write_set=None,
        per_repo=lanes.PER_REPO, comment_for=None):
    """write_set: this lane leases only those paths in the shared checkout; None leases the whole repo.

    comment_for(flight) comments on a recovered dead flight's OWN issue, never this one (#210 W5)."""
    repo, branch = entry["path"], entry.get("default_branch", "main")
    argv, prompt, road, forced = plan(entry, issue)
    write_set = None if road else write_set  # roads (lessons) keep the whole-repo lane
    recovered = [lease.recover(repo, comment_for=comment_for)] + lanes.recover(repo, comment_for=comment_for)
    base = prompt
    if write_set:
        record = lanes.acquire(repo, branch, flight, os.getpid(), timeout_s + 600, write_set, per_repo)
        prompt += (f"\n\nWrite set: edit ONLY {', '.join(write_set)}. Other Tower lanes are editing other files in "
                   "this same checkout right now. If the change needs any other path, stop; do not edit it.")
    else:
        record = lease.acquire(repo, branch, flight, os.getpid(), timeout_s + 600)
    env = dict(os.environ, TBS_LANE_OWNER=lease.owner(flight), TBS_LANE_PID=str(os.getpid()), NEXUS_FLIGHT=flight,
               NEXUS_ROAD=road or "", NEXUS_PROMPT=prompt, ACCOUNT_SCOPE=entry.get("account", ""))
    if write_set:
        env["NEXUS_WRITE_SET"] = json.dumps(write_set)
        argv = [prompt if a == base else a for a in argv]
    started = time.monotonic()
    proc = invoke(argv, cwd=repo, env=env, input=prompt if road else None,
                  timeout=max(1, timeout_s * .65), run=run)
    fallback = provider_fallback(argv, prompt, repo) if proc.returncode else None
    if fallback:
        proc = invoke(fallback, cwd=repo, env=env, input=None,
                      timeout=max(1, timeout_s - (time.monotonic() - started) - 5), run=run)
    if write_set:
        result = lanes.land(entry, issue, record, proc, forced, pr_create, comment, run, risk.classify, _lines)
        landing.require_terminal(repo, result)
        lanes.release(repo, flight)
    else:
        result = _land(entry, issue, record, proc, road, forced, pr_create, comment, run)
        landing.require_terminal(repo, result)
        lease.release(repo, flight)
    if fallback and proc.returncode and result["state"] == "HELD":
        result = dict(result, requeue=True, retry_s=3600)
    return dict(result, recovered=[r for r in recovered if r], road=road)


REVIEW_BAR = ("Bar: correctness, and the change does what the issue asks without breaking callers. "
              "The builder flight already ran the repo checks green before opening this PR. Nits, style and "
              "optional improvements never block.")


def review(entry, pr_url, flight, *, run=subprocess.run, timeout_s=900):
    """Independent reviewer: codex (router class `review`), a different identity from the claude builder.

    Read-only by instruction, in a neutral directory, never the canonical checkout. (verdict, text)."""
    import tempfile
    prompt = (f"You are an independent reviewer. Review {pr_url} in {entry['repo']}. Read it with `gh pr view`, "
              f"`gh pr diff` and `gh issue view` (all with -R {entry['repo']}); `gh api` reads are fine. Do not "
              f"clone, check out, edit, comment or merge. Being unable to read something is not a defect: judge "
              f"the diff you can see. {REVIEW_BAR}\nEnd with exactly one line: `VERDICT: PASS` or "
              f"`VERDICT: FAIL <one-line blocking reason>`.")
    with tempfile.TemporaryDirectory(prefix="nexus-review-") as tmp:
        out = os.path.join(tmp, "verdict.txt")
        model = run([ROUTER, "model", "review"], capture_output=True, text=True).stdout.strip()
        env = dict(os.environ, ACCOUNT_SCOPE=entry.get("account", ""), NEXUS_FLIGHT=flight)
        started = time.monotonic()
        primary = [ROUTER, "run", "review", "--", "exec", "--ephemeral", "--ignore-user-config",
                   "--model", model, "--sandbox", "danger-full-access", "--skip-git-repo-check",
                   "-C", tmp, "-o", out, prompt]
        proc = invoke(primary, cwd=tmp, env=env, input=None, timeout=max(1, timeout_s * .65), run=run)
        text = open(out).read() if os.path.exists(out) else ""
        if proc.returncode or not text.strip():
            fallback = [ROUTER, "run-provider", "review", "claude", "--", "-p",
                        "Codex review failed. Read the live PR and issue, then give only the requested verdict. "
                        "Do not edit, comment, merge or publish.\n\n" + prompt,
                        "--tools", "Bash,Read,Glob,Grep"]
            proc = invoke(fallback, cwd=tmp, env=env, input=None,
                          timeout=max(1, timeout_s - (time.monotonic() - started) - 5), run=run)
            text = proc.stdout if not proc.returncode else ""
    verdict = re.findall(r"VERDICT:\s*(PASS|FAIL)(.*)", text)
    if not verdict:
        raise RoadError("review providers returned no verdict: " + text[-300:])
    return verdict[-1][0], verdict[-1][1].strip() or text[-300:]


def _land(entry, issue, record, proc, road, forced, pr_create, comment, run):
    repo, labels = entry["path"], [l["name"] for l in issue.get("labels", [])]
    paths, collisions = lease.flight_paths(repo, record)
    head = landing._git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != record["head"]:
        # The road's own `land` (tbs end) committed. Landed only if origin has it.
        pushed = landing._git(repo, "merge-base", "--is-ancestor", head,
                              f"origin/{record['branch']}", check=False).returncode == 0
        if pushed and not paths:
            return {"state": "LANDED", "flight": record["flight"], "sha": head, "branch": record["branch"]}
        return landing.hold(repo, record, paths, collisions, "local_commit", comment)
    if proc.returncode:
        return (landing.hold(repo, record, paths, collisions, f"exit_{proc.returncode}", comment)
                if paths else landing.nothing_landed(record, proc))
    if paths and entry.get("check"):
        if run(entry["check"], cwd=repo, capture_output=True, text=True, timeout=1800).returncode:
            return landing.hold(repo, record, paths, collisions, "check_failed", comment)
    lines = _lines(repo, paths)
    mode = forced or risk.classify(entry.get("risk"), labels, paths, lines, entry.get("first_road", False))
    message = f"{entry['repo'].split('/')[-1]}: #{issue['number']} {issue.get('title', '')}".strip()
    if mode == "direct":
        return landing.direct(repo, record, message, comment)
    return landing.review(repo, record, message, issue["number"], pr_create, comment,
                          reason="in_review" if mode == "review" else f"human:{road or 'risk'}")


def _lines(repo, paths):
    if not paths:
        return 0
    out = landing._git(repo, "diff", "--numstat", "HEAD", "--", *paths, check=False).stdout
    total = sum(int(a) + int(b) for a, b, *_ in (l.split("\t") for l in out.splitlines()) if a.isdigit())
    for p in paths:
        if landing._git(repo, "ls-files", "--error-unmatch", "--", p, check=False).returncode:
            full = os.path.join(repo, p)
            if os.path.isfile(full):
                with open(full, "rb") as f:
                    total += f.read().count(b"\n")
    return total
