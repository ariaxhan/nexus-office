"""Local micro-patch workers, scored only by real tests (#235).

A contract names one file, one window of it, one objective and one test command. The worker is
stateless: it sees the window and returns its replacement; this module splices it, rejects anything
outside the window or over the diff bound, runs the test in a throwaway snapshot, allows one
repair with the test's failure, and escalates otherwise. Every snapshot is removed in `finally`.

    python3 scripts/micropatch.py mine            # real landed commits -> tests/fixtures/micropatch/cases.json
    python3 scripts/micropatch.py bench MODEL...  # one row per case and model -> docs/micropatch/<model>.jsonl
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fixtures" / "micropatch" / "cases.json"
OUT = ROOT / "docs" / "micropatch"
SNAPSHOT = ("nexus", "client", "tests", "scripts")
PREFIX = "nexus-mp-"
WINDOW = 40          # context lines around the change handed to the worker
MAX_SRC = 40         # a micro-patch: at most this many changed source lines historically
TEST_S = 180
PYTHON = os.environ.get("OFFICE_TEST_PYTHON", "python3.12")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def git(*args, input=None):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, input=input, check=True).stdout


# ---- isolation ---------------------------------------------------------------------------------

@contextlib.contextmanager
def snapshot(rev, overlay):
    """A throwaway tree of `rev` with `overlay` {path: text} written over it; always removed."""
    tmp = tempfile.mkdtemp(prefix=PREFIX)
    try:
        present = [d for d in SNAPSHOT if git("ls-tree", "--name-only", rev, d).strip()]
        archive = subprocess.run(["git", "archive", rev, *present], cwd=ROOT, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tmp], input=archive, check=True)
        for path, text in overlay.items():
            target = Path(tmp, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        yield Path(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def disk_bytes(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def leaked():
    return sorted(p for p in os.listdir(tempfile.gettempdir()) if p.startswith(PREFIX))


def run_tests(tree, tests):
    env = dict(os.environ, PYTHONPATH=f"{tree}/client:{tree}")
    fails = []
    for test in tests:
        proc = subprocess.run([PYTHON, "-m", "unittest", "discover", "-s", str(Path(tree, test).parent),
                               "-p", Path(test).name], cwd=tree, env=env, capture_output=True, text=True,
                              timeout=TEST_S)
        if proc.returncode:
            fails.append((proc.stdout + proc.stderr)[-2500:])
    return fails


# ---- mining ------------------------------------------------------------------------------------

def objective(commit):
    body = git("log", "-1", "--format=%B", commit)
    lines = [l for l in body.splitlines() if not re.match(r"^(Co-Authored-By|Claude-Session|Signed-off-by):", l)]
    return "\n".join(lines).strip()


def window_of(before, after):
    a, b = before.splitlines(True), after.splitlines(True)
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    lo, hi = min(op[1] for op in ops), max(op[2] for op in ops)
    lo2, hi2 = min(op[3] for op in ops), max(op[4] for op in ops)
    start, end = max(0, lo - WINDOW), min(len(a), hi + WINDOW)
    return start, end, "".join(b[lo2 - (lo - start):hi2 + (end - hi)])


def mine(since="2026-08-01"):
    cases, seen = [], {}
    cache = Path(tempfile.gettempdir(), "nexus-micropatch-mine.json")  # verdicts only; resumable
    with contextlib.suppress(OSError, ValueError):
        seen = json.loads(cache.read_text())
    for commit in git("log", "--no-merges", "--format=%H", f"--since={since}", "--", "nexus", "client").split():
        rows = [l.split("\t") for l in git("diff-tree", "--no-commit-id", "-r", "--numstat", commit).splitlines()]
        src = [r for r in rows if r[2].endswith(".py") and not r[2].startswith("tests/")]
        tests = [r[2] for r in rows if r[2].startswith("tests/test_") and r[2].endswith(".py")]
        if len(src) != 1 or not tests or src[0][0] == "-" or int(src[0][0]) + int(src[0][1]) > MAX_SRC:
            continue
        path, parent = src[0][2], commit + "^"
        try:
            before, after = git("show", f"{parent}:{path}"), git("show", f"{commit}:{path}")
        except subprocess.CalledProcessError:
            continue  # a new file: not an edit
        overlay = {}
        for r in rows:  # the commit's tests and fixtures, never its source change
            if r[2] != path and r[0] != "-":
                with contextlib.suppress(subprocess.CalledProcessError):
                    overlay[r[2]] = git("show", f"{commit}:{r[2]}")
        verdict = seen.get(commit)
        if verdict is None:
            try:
                with snapshot(parent, overlay) as tree:
                    fails_before = bool(run_tests(tree, tests))
                with snapshot(parent, dict(overlay, **{path: after})) as tree:
                    passes_after = not run_tests(tree, tests)
                verdict = "ok" if fails_before and passes_after else "no-verifier" if not fails_before else "flaky"
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                verdict = "unbuildable"
            seen[commit] = verdict
            cache.write_text(json.dumps(seen))
        print(commit[:8], path, int(src[0][0]) + int(src[0][1]), verdict, file=sys.stderr)
        if verdict != "ok":
            continue
        start, end, _ = window_of(before, after)
        test_diff = "".join(git("diff", parent, commit, "--", t) for t in tests)
        cases.append(dict(commit=commit, path=path, tests=tests, overlay=sorted(overlay), start=start, end=end,
                          changed=int(src[0][0]) + int(src[0][1]), objective=objective(commit),
                          test_diff=test_diff[:6000]))
    CASES.parent.mkdir(parents=True, exist_ok=True)
    CASES.write_text(json.dumps(cases, indent=1) + "\n")
    return cases


# ---- the worker --------------------------------------------------------------------------------

def prompt(case, snippet, failure):
    return (f"Make the failing test pass by editing one region of one Python file.\n\n"
            f"File: {case['path']}\n\nObjective:\n{case['objective']}\n\n"
            f"Acceptance test (already written, do not change it):\n{case['test_diff']}\n\n"
            f"It currently fails with:\n```\n{failure[-1800:]}\n```\n\n"
            f"Region you may edit (lines {case['start'] + 1}-{case['end']}):\n```python\n{snippet}```\n\n"
            "Reply with one or more edits in exactly this form and nothing else:\n"
            "<<<<<<< SEARCH\n(lines copied exactly from the region)\n=======\n(their replacement)\n>>>>>>> REPLACE\n"
            "SEARCH text must appear exactly once in the region. Change as little as possible.")


EDIT = re.compile(r"(?:<{3,} ?)?SEARCH\n(.*?)\n?={3,}\n(.*?)\n?>{3,} ?REPLACE", re.S)  # tests judge, not markup


def ask(model, text):
    # The model's own recommended sampling when given (OPTIONS='{"temperature":0.7,...}'); else greedy.
    options = dict({"temperature": 0}, **json.loads(os.environ.get("OPTIONS", "{}")), num_ctx=16384)
    body = json.dumps({"model": model, "prompt": text, "stream": False,
                       "think": os.environ.get("THINK") == "1", "options": options}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.load(resp)
    ns = lambda k: (data.get(k) or 0) / 1e9  # noqa: E731
    STATS.update(load_s=round(ns("load_duration"), 2),
                 gen_tps=round(data.get("eval_count", 0) / ns("eval_duration"), 1) if ns("eval_duration") else None,
                 prompt_tps=round(data.get("prompt_eval_count", 0) / ns("prompt_eval_duration"), 1)
                 if ns("prompt_eval_duration") else None)
    reply = re.sub(r"<think>.*?</think>", "", data.get("response", ""), flags=re.S)
    return reply, data.get("prompt_eval_count", 0), data.get("eval_count", 0)


STATS = {}


def resident_bytes(model):
    """What ollama reports holding for this model right now (weights + KV), or None."""
    with contextlib.suppress(OSError, ValueError):
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=10) as resp:
            for m in json.load(resp).get("models", []):
                if m.get("name") == model or m.get("model") == model:
                    return m.get("size")
    return None


FORMAT = os.environ.get("FORMAT", "search")  # search: SEARCH/REPLACE edits; function: the whole enclosing function


def function_prompt(case, snippet, failure):
    return (f"Make the failing test pass by rewriting one Python function.\n\n"
            f"File: {case['path']}\n\nObjective:\n{case['objective']}\n\n"
            f"Acceptance test (already written, do not change it):\n{case['test_diff']}\n\n"
            f"It currently fails with:\n```\n{failure[-1800:]}\n```\n\n"
            f"The function (lines {case['start'] + 1}-{case['end']}):\n```python\n{snippet}```\n\n"
            "Reply with the complete corrected function in one ```python block and nothing else. "
            "Keep its name, signature, indentation and every line that need not change.")


def extract_block(reply):
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", reply, re.S)
    if not blocks:
        return None, "no python block"
    new = max(blocks, key=len)
    return (new if new.endswith("\n") else new + "\n"), None


def enclosing_function(before, start, end):
    """(start, end) of the smallest def containing the historical change window's core, else None."""
    import ast
    lo, hi = start + WINDOW, end - WINDOW  # the changed lines, 0-based, as window_of padded them
    best = None
    for node in ast.walk(ast.parse(before)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            if first <= lo and node.end_lineno >= hi and (best is None or node.end_lineno - first < best[1] - best[0]):
                best = (first, node.end_lineno)
    return best


def apply_edits(snippet, reply):
    """(new snippet, None) or (None, why): every SEARCH must occur exactly once in the window."""
    edits = EDIT.findall(reply)
    if not edits:
        return None, "no edit blocks"
    for search, replace in edits:
        if not search.strip():
            return None, "empty SEARCH"
        if snippet.count(search) != 1:
            return None, f"SEARCH found {snippet.count(search)} times, must be exactly once:\n{search[:300]}"
        snippet = snippet.replace(search, replace)
    return snippet, None


def scope_check(before_snippet, new_snippet, case):
    """Mechanical bound: some change, and not far beyond the historical diff size."""
    changed = sum(1 for l in difflib.unified_diff(before_snippet.splitlines(), new_snippet.splitlines(), lineterm="")
                  if l[:1] in "+-" and not l.startswith(("+++", "---")))
    if changed == 0:
        return "no change"
    if changed > max(20, 3 * case["changed"]):
        return f"diff of {changed} lines is over the bound of {max(20, 3 * case['changed'])}"
    return None


def attempt(case, model, lines, snippet, failure, overlay):
    t0 = time.time()
    if FORMAT == "function":
        reply, tin, tout = ask(model, function_prompt(case, snippet, failure))
        new, reject = extract_block(reply)
    else:
        reply, tin, tout = ask(model, prompt(case, snippet, failure))
        new, reject = apply_edits(snippet, reply)
    reject = reject or scope_check(snippet, new, case)
    row = dict(latency_s=round(time.time() - t0, 1), tokens_in=tin, tokens_out=tout, reject=reject and reject[:120],
               reply=reply[:4000], **STATS)
    if reject:
        return row, "Your edit was rejected before testing: " + reject
    text = "".join(lines[:case["start"]]) + new + "".join(lines[case["end"]:])
    with snapshot(case["commit"] + "^", dict(overlay, **{case["path"]: text})) as tree:
        row["disk_bytes"] = disk_bytes(tree)
        fails = run_tests(tree, case["tests"])
    row["passed"] = not fails
    row["diff"] = "".join(difflib.unified_diff(lines, text.splitlines(True), case["path"], case["path"]))[:3000]
    return row, (fails[0] if fails else None)


def initial_failure(case, overlay):
    with snapshot(case["commit"] + "^", overlay) as tree:
        fails = run_tests(tree, case["tests"])
    return fails[0] if fails else ""


def solve(case, model):
    """(result, rows): first pass from the failing test, one repair from its new failure, then escalate."""
    parent = case["commit"] + "^"
    before = git("show", f"{parent}:{case['path']}")
    lines = before.splitlines(True)
    if FORMAT == "function":
        span = enclosing_function(before, case["start"], case["end"])
        if span is None:
            return "no_function", []
        case = dict(case, start=span[0], end=span[1])
    snippet = "".join(lines[case["start"]:case["end"]])
    overlay = {p: git("show", f"{case['commit']}:{p}") for p in case["overlay"]}
    failure, rows = initial_failure(case, overlay), []
    for n in (1, 2):
        try:
            row, failure = attempt(case, model, lines, snippet, failure, overlay)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            row, failure = dict(error=str(exc)[:300], passed=False), str(exc)
        rows.append(dict(row, attempt=n))
        if row.get("passed"):
            return ("first_pass" if n == 1 else "repaired"), rows
    return "escalated", rows


def bench(models, limit=None):
    cases = json.loads(CASES.read_text())[::int(os.environ.get("STRIDE", 1))][:limit]
    if os.environ.get("CLASSES"):  # e.g. CLASSES=wiring,schema: only those reasoning shapes
        labels = json.loads((CASES.parent / "classes.json").read_text())
        cases = [c for c in cases if labels[c["commit"]]["class"] in os.environ["CLASSES"].split(",")]
    OUT.mkdir(parents=True, exist_ok=True)
    for model in models:
        out = OUT / f"{model.replace('/', '_').replace(':', '_')}{'' if FORMAT == 'search' else '.' + FORMAT}{'.think' if os.environ.get('THINK') == '1' else ''}.jsonl"
        done = {json.loads(l)["commit"] for l in out.open()} if out.exists() else set()
        with out.open("a") as handle:
            for case in cases:
                if case["commit"] in done:
                    continue
                t0 = time.time()
                result, rows = solve(case, model)
                handle.write(json.dumps(dict(model=model, commit=case["commit"], path=case["path"],
                                             changed=case["changed"], result=result, wall_s=round(time.time() - t0, 1),
                                             attempts=rows, leaked=leaked(), format=FORMAT,
                                             resident_bytes=resident_bytes(model))) + "\n")
                handle.flush()
                print(model, case["commit"][:8], result, file=sys.stderr)


def summarize(rows):
    """Per interface: pass rates, failure shapes, speed, memory. By class when labels exist."""
    labels = {}
    with contextlib.suppress(OSError, ValueError):
        labels = json.loads((CASES.parent / "classes.json").read_text())
    out = {}
    for fmt in sorted({r.get("format", "search") for r in rows}):
        rs = [r for r in rows if r.get("format", "search") == fmt]
        attempts = [a for r in rs for a in r["attempts"]]
        by_class = {}
        for r in rs:
            c = labels.get(r["commit"], {}).get("class", "?")
            by_class.setdefault(c, [0, 0, 0])
            by_class[c][0] += 1
            by_class[c][1] += r["result"] == "first_pass"
            by_class[c][2] += r["result"] in ("first_pass", "repaired")
        tps = [a["gen_tps"] for a in attempts if a.get("gen_tps")]
        out[fmt] = dict(cases=len(rs), first_pass=sum(r["result"] == "first_pass" for r in rs),
                        after_repair=sum(r["result"] in ("first_pass", "repaired") for r in rs),
                        no_function=sum(r["result"] == "no_function" for r in rs),
                        rejected_attempts=sum(bool(a.get("reject")) for a in attempts),
                        test_failed_attempts=sum(a.get("passed") is False and not a.get("reject") for a in attempts),
                        attempts=len(attempts), median_gen_tps=sorted(tps)[len(tps) // 2] if tps else None,
                        median_wall_s=sorted(r["wall_s"] for r in rs)[len(rs) // 2] if rs else None,
                        max_load_s=max((a.get("load_s") or 0 for a in attempts), default=None),
                        resident_bytes=max((r.get("resident_bytes") or 0 for r in rs), default=None),
                        leaked=sorted({p for r in rs for p in r.get("leaked", [])}),
                        by_class={k: dict(zip(("cases", "first_pass", "after_repair"), v)) for k, v in by_class.items()})
    return out


def record(model, verdict, reason, source):
    """Durable evidence for a model, then (verdict=delete) its weights go. Weights are cache; this is not."""
    show = subprocess.run(["ollama", "show", model], capture_output=True, text=True).stdout
    listed = next((l.split() for l in subprocess.run(["ollama", "list"], capture_output=True, text=True)
                   .stdout.splitlines() if l.split() and l.split()[0] == model), [])
    rows = [json.loads(l) for f in OUT.glob(model.replace("/", "_").replace(":", "_") + "*.jsonl") for l in f.open()]
    entry = dict(model=model, source=source, digest=listed[1] if len(listed) > 1 else None,
                 size=" ".join(listed[2:4]) if len(listed) > 3 else None,
                 quantization=(re.search(r"quantization\s+(\S+)", show) or [None, None])[1],
                 parameters=(re.search(r"parameters\s+(\S+)", show) or [None, None])[1],
                 verdict=verdict, reason=reason, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), results=summarize(rows))
    with (OUT / "models.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    if verdict == "delete":
        subprocess.run(["ollama", "rm", model], check=True, capture_output=True)
    return entry


if __name__ == "__main__":
    if sys.argv[1] == "mine":
        print(len(mine()))
    elif sys.argv[1] == "summary":
        for f in sorted(OUT.glob("*.jsonl")):
            if f.name != "models.jsonl":
                print(f.name, json.dumps(summarize([json.loads(l) for l in f.open()])))
    elif sys.argv[1] == "record":  # record MODEL keep|delete REASON SOURCE_URL
        print(json.dumps(record(*sys.argv[2:6]), indent=1))
    elif sys.argv[1] == "bench":
        bench(sys.argv[2:], limit=int(os.environ["LIMIT"]) if os.environ.get("LIMIT") else None)
