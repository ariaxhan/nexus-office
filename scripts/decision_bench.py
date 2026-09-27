#!/usr/bin/env python3
"""Decision-plane benchmark (#187): same cases, same scoring, every candidate.

  extract  --ledger L --out cases.jsonl        frozen labeled cases from the Nexus ledger
  run      --cases C --candidate K --out R     one candidate over every case
  score    R [R ...]                           correctness, calibration, latency, cost
  overhead --ledger L                          where coordination time actually goes

Candidates: `rules` (deterministic text procedure), `ollama:<model>`,
`claude:<model>` (the current frontier path, via `claude -p`), and
`cmd:<shell command>` (prompt on stdin, JSON {"p": float} on stdout). Jev runs
 through `cmd:` once credentials and an endpoint are available; nothing assumes its API.

The ledger is opened read-only. Nothing here touches the coordinator.
"""

import argparse
import json
import random
import re
import shlex
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_LEDGER = Path.home() / "Library/Application Support/nexus/ledger.sqlite"

#: labels a person put on the issue, and what they mean for each task.
NEEDS_HUMAN = "waiting on human"
HUMAN_ONLY = "\u26d4 **Blocked on you**"
ROUTINE = {"ready", "route-codex", "triaged", "tower-v2"}

QUESTIONS = {
    "needs_human": "Does this work item need a human decision or action before an "
                   "autonomous coding agent can do it?",
    "lane_held": "Will an autonomous coding agent working this issue end HELD for a "
                 "human (failed check, failed review, or no landable change) rather "
                 "than landing cleanly?",
}

BANNER = re.compile(r"blocked on you|waiting on (you|aria|human)|ask (her|his|their|aria)|"
                    r"\bdecide\b|\bapprove\b|your call|needs? (your|a human)", re.I)


# ---------------------------------------------------------------- extract

def _connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _issues(db):
    """Latest GitHub issue payload per issue URL."""
    out = {}
    for payload, in db.execute(
            "select payload from events where kind='work.issue' order by id"):
        issue = json.loads(payload)
        url = issue.get("html_url")
        if url and "/issues/" in url:
            out[url] = issue
    return out


def _text(issue):
    return f"{issue.get('title', '')}\n\n{(issue.get('body') or '')[:2000]}".strip()


def extract(db):
    issues = _issues(db)
    cases = []
    for url, issue in sorted(issues.items()):
        labels = {l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])}
        if not labels & (ROUTINE | {NEEDS_HUMAN}):
            continue  # never triaged by a person: no ground truth
        # Intake writes the banner from the ask's owner; some such asks also carry
        # `ready`, so for them the label alone is not ground truth.
        gold = int(NEEDS_HUMAN in labels or HUMAN_ONLY in (issue.get("body") or ""))
        cases.append({"id": f"needs_human:{url}", "task": "needs_human",
                      "text": _text(issue), "gold": gold, "labels": sorted(labels)})
    lanes = {}
    for payload, in db.execute("select payload from events where kind='work.lane' order by id"):
        lane = json.loads(payload)
        key = f"https://github.com/{lane['repo']}/issues/{lane['issue']}"
        lanes.setdefault(key, []).append(lane["state"])
    for url, states in sorted(lanes.items()):
        if url in issues:
            # First attempt is the decision a router would have faced.
            cases.append({"id": f"lane_held:{url}", "task": "lane_held",
                          "text": _text(issues[url]), "gold": int(states[0] == "HELD")})
    return cases


# ---------------------------------------------------------------- candidates

def _prompt(case):
    return (f"{QUESTIONS[case['task']]}\n\nWork item:\n<<<\n{case['text']}\n>>>\n\n"
            'Answer with JSON only: {"p": <probability 0..1 that the answer is yes>}')


def _parse_p(text):
    match = re.search(r'"p"\s*:\s*([0-9.]+)', text or "")
    if not match:
        raise ValueError(f"no probability in {(text or '')[:120]!r}")
    p = float(match.group(1))
    if not 0 <= p <= 1:
        raise ValueError(f"probability outside [0, 1]: {p}")
    return p


def rules(case):
    """The deterministic tier: no model, text only (labels are withheld)."""
    if case["task"] == "needs_human":
        return (0.9 if BANNER.search(case["text"]) else 0.1), 0.0
    return 0.5, 0.0  # no text rule predicts lane outcome: abstain


def _call(argv, prompt, timeout=300):
    out = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError(f"rc={out.returncode}: {out.stderr.strip()[-160:]}")
    return out.stdout


def ollama(model, url="http://127.0.0.1:11434/api/generate"):
    # The HTTP API, not `ollama run --format json`: constrained JSON breaks
    # reasoning models (gpt-oss aborts on token repeat), so every local model
    # gets the same unconstrained call and the same parser.
    def ask(case):
        body = {"model": model, "prompt": _prompt(case), "stream": False}
        if model.startswith("gpt-oss"):
            body["think"] = "low"
        request = urllib.request.Request(url, json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=300) as reply:
            return _parse_p(json.load(reply)["response"]), 0.0
    return ask


def claude(model):
    def ask(case):
        reply = json.loads(_call(["claude", "-p", "--model", model, "--output-format",
                                  "json", "--max-turns", "1"], _prompt(case)))
        return _parse_p(reply.get("result", "")), float(reply.get("total_cost_usd") or 0)
    return ask


def command(cmd):
    def ask(case):
        reply = json.loads(_call(shlex.split(cmd), _prompt(case), timeout=120))
        p = float(reply["p"])
        if not 0 <= p <= 1:
            raise ValueError(f"probability outside [0, 1]: {p}")
        return p, float(reply.get("cost_usd", 0))
    return ask


def candidate(name):
    kind, _, arg = name.partition(":")
    return {"rules": lambda: rules, "ollama": lambda: ollama(arg),
            "claude": lambda: claude(arg), "cmd": lambda: command(arg)}[kind]()


def run(cases, name, ask=None):
    ask = ask or candidate(name)
    for case in cases:
        started = time.perf_counter()
        row = {"id": case["id"], "task": case["task"], "gold": case["gold"],
               "candidate": name}
        try:
            row["p"], row["cost_usd"] = ask(case)
        except Exception as exc:  # a failure is a result, not a crash
            row["p"], row["cost_usd"], row["error"] = None, 0.0, str(exc)[:200]
        row["latency_s"] = round(time.perf_counter() - started, 4)
        yield row


# ---------------------------------------------------------------- score

def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def bootstrap_ci(hits, n=2000, seed=187):
    if not hits:
        return None, None
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(hits, k=len(hits))) / len(hits) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def score(rows, band=(0.2, 0.8)):
    """Per candidate and task. A failed call counts as wrong, never as skipped."""
    groups = {}
    for row in rows:
        groups.setdefault((row["candidate"], row["task"]), []).append(row)
    report = []
    for (name, task), group in sorted(groups.items()):
        answered = [r for r in group if r["p"] is not None]
        hits = [int(r["p"] is not None and (r["p"] >= 0.5) == bool(r["gold"])) for r in group]
        low, high = bootstrap_ci(hits)
        confident = [r for r in answered if not band[0] < r["p"] < band[1]]
        latencies = [r["latency_s"] for r in group]
        report.append({
            "candidate": name, "task": task, "n": len(group),
            "positives": sum(r["gold"] for r in group),
            "accuracy": round(sum(hits) / len(group), 3),
            "accuracy_ci95": [round(low, 3), round(high, 3)],
            "errors": len(group) - len(answered),
            "brier": round(statistics.fmean((r["p"] - r["gold"]) ** 2 for r in answered), 3)
            if answered else None,
            # Escalation usefulness: how often it is confident, and right when it is.
            "confident_share": round(len(confident) / len(group), 3),
            "confident_accuracy": round(statistics.fmean(
                int((r["p"] >= 0.5) == bool(r["gold"])) for r in confident), 3)
            if confident else None,
            "latency_p50_s": percentile(latencies, 0.5),
            "latency_p95_s": percentile(latencies, 0.95),
            "latency_p99_s": percentile(latencies, 0.99),
            "cost_usd_total": round(sum(r.get("cost_usd", 0) for r in group), 4),
        })
    return report


# ---------------------------------------------------------------- overhead

def overhead(db):
    """Stage durations from what the ledger actually recorded, in seconds."""
    stages = {}

    def add(name, value):
        if value is not None and value >= 0:
            stages.setdefault(name, []).append(value)

    for created, started, ended, state in db.execute(
            "select created_at, started_at, ended_at, state from flights"):
        if started:
            add("flight: queued -> started", started - created)
        if started and ended:
            add(f"flight: started -> ended ({state})", ended - started)
    by_key = {}
    for kind, payload in db.execute(
            "select kind, payload from events where kind like 'lifecycle.%' order by id"):
        event = json.loads(payload)
        key = event.get("attempt_id") or event.get("work_key") or event.get("task_id")
        by_key.setdefault(key, {}).setdefault(kind.split(".", 1)[1], event.get("at"))
    for marks in by_key.values():
        add("lifecycle: selected -> execution_started",
            _gap(marks, "selected", "execution_started"))
        add("lifecycle: execution_started -> attempt_finished",
            _gap(marks, "execution_started", "attempt_finished"))
        add("lifecycle: verification_started -> finished",
            _gap(marks, "verification_started", "verification_finished"))
    # Input -> noticed: GitHub creation to the first disposition Tower wrote for it.
    created, noticed = {}, {}
    for subject, ts, kind, payload in db.execute(
            "select subject, ts, kind, payload from events "
            "where kind in ('work.issue','work.disposition') order by id"):
        if kind == "work.issue" and subject not in created:
            stamp = json.loads(payload).get("created_at")
            if stamp:
                created[subject] = time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) \
                    - time.timezone
        elif kind == "work.disposition":
            noticed.setdefault(subject, ts)
    for subject, at in noticed.items():
        if subject in created:
            add("issue: created -> noticed", at - created[subject])
    recovered = [json.loads(p)["reason"] for p, in db.execute(
        "select payload from events where kind='work.recovered'")]
    return {
        "stages": {name: {"n": len(v), "p50_s": round(percentile(v, 0.5), 1),
                          "p95_s": round(percentile(v, 0.95), 1),
                          "p99_s": round(percentile(v, 0.99), 1)}
                   for name, v in sorted(stages.items())},
        "decided_by": dict(db.execute(
            "select coalesce(json_extract(payload,'$.decided_by'),'(none)'), count(*) "
            "from events where kind='task.state' group by 1 order by 2 desc limit 5")),
        "recoveries": {r: recovered.count(r) for r in set(recovered)},
    }


def _gap(marks, a, b):
    return marks[b] - marks[a] if marks.get(a) and marks.get(b) else None


# ---------------------------------------------------------------- cli

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    p.add_argument("--out", required=True)
    p = sub.add_parser("run")
    p.add_argument("--cases", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("score")
    p.add_argument("--cases", required=True)
    p.add_argument("results", nargs="+")
    p = sub.add_parser("overhead")
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    args = parser.parse_args(argv)

    if args.cmd == "extract":
        with _connect(args.ledger) as db:
            cases = extract(db)
        Path(args.out).write_text("".join(json.dumps(c) + "\n" for c in cases))
        print(f"{len(cases)} cases -> {args.out}")
    elif args.cmd == "run":
        cases = [json.loads(l) for l in Path(args.cases).read_text().splitlines() if l]
        with open(args.out, "w") as out:
            for row in run(cases, args.candidate):
                out.write(json.dumps(row) + "\n")
                out.flush()
    elif args.cmd == "score":
        rows = [json.loads(l) for f in args.results
                for l in Path(f).read_text().splitlines() if l]
        gold = {c["id"]: c["gold"] for c in map(json.loads,
                Path(args.cases).read_text().splitlines())}
        expected = set(gold)
        by_candidate = {}
        for row in rows:
            by_candidate.setdefault(row["candidate"], []).append(row["id"])
        for name, ids in by_candidate.items():
            if len(ids) != len(expected) or set(ids) != expected:
                parser.error(f"{name}: missing, duplicate, or extra case IDs")
        rows = [dict(r, gold=gold[r["id"]]) for r in rows]
        print(json.dumps(score(rows), indent=1))
    else:
        with _connect(args.ledger) as db:
            print(json.dumps(overhead(db), indent=1))


if __name__ == "__main__":
    sys.exit(main())
