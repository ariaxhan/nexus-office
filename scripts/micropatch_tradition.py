"""The frozen #235 cases, executed by the tradition harness.

Tradition owns the worker: its ProviderAgentExecutor reads, edits and runs the focused test in a loop.
This file only hands it a case inside micropatch's throwaway snapshot and applies micropatch's final
verifier and scope check afterwards, so the two harnesses differ in the worker and nothing else.

Run with tradition's interpreter:
    "$HOME/Library/Application Support/tradition-harness/imessage-venv/bin/python" \\
        scripts/micropatch_tradition.py MODEL cold|contract
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import micropatch as mp  # noqa: E402
from tradition_harness.executors import ProviderAgentExecutor  # noqa: E402

MAX_STEPS = int(os.environ.get("MAX_STEPS", 16))


def digests(tree):
    return {str(p.relative_to(tree)): hashlib.sha1(p.read_bytes()).hexdigest()
            for d in mp.SNAPSHOT for p in Path(tree, d).rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def trace(transcript):
    """Tool steps, model tokens and failing test runs, from tradition's own transcript."""
    turns = [t for t in (json.loads(l) for l in transcript.splitlines() if l.strip()) if t.get("type") == "turn"]
    tools = [(t.get("action") or {}).get("tool") for t in turns]
    tests = [str(t.get("observation", "")) for t, tool in zip(turns, tools) if tool == "run_tests"]
    return dict(tool_steps=len(turns), model_tokens=sum(t.get("tokens") or 0 for t in turns),
                tools={k: tools.count(k) for k in set(tools) if k},
                test_runs=len(tests), test_failures_caught=sum(not o.startswith("[return code 0]") for o in tests),
                refusals=sum(str(t.get("observation", "")).startswith("refused") for t in turns))


def contract_text(case, mode, failure):
    test_cmd = "python -m unittest " + " ".join(case["tests"])
    if mode == "contract":
        c = json.loads((mp.CASES.parent / "contracts.json").read_text())[case["commit"]]
        body = (f"Target: {c['target']}\nMechanism: {c['mechanism']}\nRequired behavior: {c['behavior']}\n"
                f"Constraints: {c['constraints']}")
    else:
        body = f"Objective:\n{case['objective']}"
    return (f"Implementation contract. Edit ONLY {case['path']}; do not create or change any other file.\n\n"
            f"{body}\n\nAcceptance test (already written, do not change it): {test_cmd}\n"
            f"It currently fails with:\n{failure[-1500:]}\n\n"
            f"Read {case['path']}, make the smallest change, run the acceptance test with run_tests, and repeat "
            "until it passes. Finish only when the test passes.")


def solve(case, model, mode):
    overlay = {p: mp.git("show", f"{case['commit']}:{p}") for p in case["overlay"]}
    failure = mp.initial_failure(case, overlay)
    t0 = time.time()
    with mp.snapshot(case["commit"] + "^", overlay) as tree:
        before = digests(tree)
        os.environ["PYTHONPATH"] = f"{tree}/client:{tree}"  # what run_tests inherits (nexus test convention)
        executor = ProviderAgentExecutor("ollama", model, max_steps=MAX_STEPS, allow_verification=True,
                                         expect_mutation=True, require_investigation=True, allowed_paths=(case["path"],),
                                         spends_budget=False, max_tokens_per_turn=4096)
        try:
            result = executor.run(contract_text(case, mode, failure), cwd=Path(tree),
                                  commission_slug=f"mp-{case['commit'][:8]}")
            ok, err = result.ok, None
            info = dict(parse_retries=result.parse_retries, files_written=list(result.files_written),
                        files_read=list(result.files_read), **trace(result.transcript_jsonl))
        except Exception as exc:  # noqa: BLE001 - a crashed worker is an escalation, recorded
            ok, err, info = False, f"{type(exc).__name__}: {exc}"[:300], {}
        after = digests(tree)
        touched = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        written = [str(Path(f)) for f in info.get("files_written", [])]
        out_of_scope = sorted({p for p in touched if p in before and p != case["path"]}
                              | {f for f in written if Path(tree, f).resolve() != Path(tree, case["path"]).resolve()})
        in_scope_only = not out_of_scope and case["path"] in touched
        passed = in_scope_only and not mp.run_tests(tree, case["tests"])
        disk = mp.disk_bytes(tree)
    return dict(model=model, harness="tradition", mode=mode, commit=case["commit"], path=case["path"],
                changed=case["changed"], result="verified" if passed else "escalated", executor_ok=ok,
                error=err, touched=touched, out_of_scope=out_of_scope, wall_s=round(time.time() - t0, 1),
                disk_bytes=disk, leaked=mp.leaked(), resident_bytes=mp.resident_bytes(model), **info)


def main(model, mode):
    mp.terminate_cleanly()
    cases = json.loads(mp.CASES.read_text())[::int(os.environ.get("STRIDE", 3))]
    if os.environ.get("CLASSES"):
        labels = json.loads((mp.CASES.parent / "classes.json").read_text())
        cases = [c for c in cases if labels[c["commit"]]["class"] in os.environ["CLASSES"].split(",")]
    out = mp.OUT / f"tradition.{model.replace(':', '_')}.{mode}.jsonl"
    done = {json.loads(l)["commit"] for l in out.open()} if out.exists() else set()
    with out.open("a") as handle:
        for case in cases:
            if case["commit"] not in done:
                row = solve(case, model, mode)
                handle.write(json.dumps(row) + "\n")
                handle.flush()
                print(model, mode, case["commit"][:8], row["result"], row["wall_s"], file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
