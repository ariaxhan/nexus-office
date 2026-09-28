"""The #235 micro-patch benchmark's frozen cases, isolation and verifier. Workers are tradition's.

Cases are real landed commits whose own test fails before and passes after. A case runs in a throwaway
`git archive` snapshot owned by this process: removed in `finally`, on SIGTERM, and by the next run if
its owner died. The worker (scripts/micropatch_tradition.py) is tradition's ProviderAgentExecutor.

    python3 scripts/micropatch.py mine                          # -> tests/fixtures/micropatch/cases.json
    python3 scripts/micropatch.py record MODEL keep|delete WHY URL
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import shutil
import signal
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
WINDOW = 40          # context lines around the change (the historical region; kept for the frozen cases)
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
    reclaim()
    tmp = tempfile.mkdtemp(prefix=PREFIX)
    Path(tmp, OWNER).write_text(str(os.getpid()))
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
        for _ in range(5):  # a straggler can recreate files between passes
            shutil.rmtree(tmp, ignore_errors=True)
            if not os.path.exists(tmp):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"snapshot {tmp} could not be removed")


OWNER = ".micropatch-owner"


def reclaim():
    """Remove snapshots whose owning process is gone: a kill -9 or crash leaves one behind."""
    for name in leaked():
        path = Path(tempfile.gettempdir(), name)
        try:
            pid = int(Path(path, OWNER).read_text())
            os.kill(pid, 0)
            continue  # owner alive: its snapshot, not ours
        except ProcessLookupError:
            pass
        except (OSError, ValueError):
            if time.time() - path.stat().st_mtime < 3600:
                continue  # unowned and fresh: being created right now
        shutil.rmtree(path, ignore_errors=True)


def terminate_cleanly():
    """SIGTERM unwinds through `finally` so the live snapshot is removed, instead of dying in place."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


def disk_bytes(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def leaked():
    return sorted(p for p in os.listdir(tempfile.gettempdir()) if p.startswith(PREFIX))


def run_tests(tree, tests):
    env = dict(os.environ, PYTHONPATH=f"{tree}/client:{tree}")
    fails = []
    for test in tests:
        # Own session, killed whole afterwards: a test's detached child kept writing into the snapshot
        # after rmtree and leaked it (qwen3.8 run, 2026-09-28).
        proc = subprocess.Popen([PYTHON, "-m", "unittest", "discover", "-s", str(Path(tree, test).parent),
                                 "-p", Path(test).name], cwd=tree, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=TEST_S)
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if proc.returncode:
            fails.append((out or "")[-2500:])
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

def resident_bytes(model):
    """What ollama reports holding for this model right now (weights + KV), or None."""
    with contextlib.suppress(OSError, ValueError):
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=10) as resp:
            for m in json.load(resp).get("models", []):
                if m.get("name") == model or m.get("model") == model:
                    return m.get("size")
    return None


def initial_failure(case, overlay):
    with snapshot(case["commit"] + "^", overlay) as tree:
        fails = run_tests(tree, case["tests"])
    return fails[0] if fails else ""


def record(model, verdict, reason, source):
    """Durable provenance for a model tried on #235, then (verdict=delete) its weights go. Weights are cache."""
    show = subprocess.run(["ollama", "show", model], capture_output=True, text=True).stdout
    listed = next((l.split() for l in subprocess.run(["ollama", "list"], capture_output=True, text=True)
                   .stdout.splitlines() if l.split() and l.split()[0] == model), [])
    entry = dict(model=model, source=source, digest=listed[1] if len(listed) > 1 else None,
                 size=" ".join(listed[2:4]) if len(listed) > 3 else None,
                 quantization=(re.search(r"quantization\s+(\S+)", show) or [None, None])[1],
                 parameters=(re.search(r"parameters\s+(\S+)", show) or [None, None])[1],
                 verdict=verdict, reason=reason, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 results=sorted(f.name for f in OUT.glob("*" + model.replace(":", "_") + "*.jsonl")))
    with (OUT / "models.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    if verdict == "delete":
        subprocess.run(["ollama", "rm", model], check=True, capture_output=True)
    return entry


if __name__ == "__main__":
    if sys.argv[1] == "mine":
        print(len(mine()))
    elif sys.argv[1] == "record":  # record MODEL keep|delete REASON SOURCE_URL
        print(json.dumps(record(*sys.argv[2:6]), indent=1))
