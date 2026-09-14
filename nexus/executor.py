"""One write flight on a canonical checkout: lease, run, check, classify, land, prove.

Everything project-specific is registry data: `check`, `risk`, `roads`. A road
(e.g. lessons) names the exact skills and docs the agent is given and the only
command the flight may run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from . import landing, lease, risk

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
            f"{entry.get('default_branch', 'main')}. Do not commit, push, branch, clone, stash or "
            f"open PRs; Nexus lands the change. Run the project's own checks.\n"
            f"Product guidance: {entry.get('product_guidance', '')}")


def plan(entry, issue):
    """(argv, prompt, road name, mode override). Pure; refuses before any lease."""
    labels = [l["name"] for l in issue.get("labels", [])]
    name, road = road_for(entry, labels)
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


def fly(entry, issue, flight, *, pr_create, comment, timeout_s=900, run=subprocess.run):
    repo, branch = entry["path"], entry.get("default_branch", "main")
    argv, prompt, road, forced = plan(entry, issue)
    recovered = lease.recover(repo, comment)
    record = lease.acquire(repo, branch, flight, os.getpid(), timeout_s + 600)
    env = dict(os.environ, NEXUS_FLIGHT=flight, NEXUS_ROAD=road or "", NEXUS_PROMPT=prompt,
               ACCOUNT_SCOPE=entry.get("account", ""))
    proc = run(argv, cwd=repo, env=env, input=prompt if road else None, text=True,
               capture_output=True, timeout=timeout_s)
    result = _land(entry, issue, record, proc, road, forced, pr_create, comment, run)
    landing.require_terminal(repo, result)
    lease.release(repo, flight)
    return dict(result, recovered=recovered, road=road)


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
                if paths else {"state": "CLOSED", "flight": record["flight"], "reason": "no_change"})
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
