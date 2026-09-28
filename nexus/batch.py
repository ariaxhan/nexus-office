"""A bounded mechanical batch below the supervisor (#235 A).

A supervisor turn re-reads ~230k tokens of conversation; a grep does not need that. So a run of
mechanical reads (ledger sqlite, git history, file sections, greps, json fields) is written once
as a spec, executed here deterministically, and returned as one result the supervisor reads once.

  python3 -m nexus.batch spec.json [--out result.json]

Read-only by construction: sqlite opens `mode=ro`, git runs only read subcommands with no output
or external-diff flags, files are read in-process. No shell. A step that fails records its error
and the batch says `ok: false`; nothing is filled in. Every step carries its provenance (command,
database, path, sha256 of what it saw).

spec: {"task": str, "steps": [{"id": str, "op": "sqlite|git|read|grep|json|local", ...}]}
  sqlite  db (path, or "ledger"), sql, params?, limit? (default 50)
  git     repo, args (argv after `git`)
  read    path [repo + rev: the file as of that commit], and one of section ("## Heading"), around (regex) + ctx?, range [a, b]
  grep    pattern, path (file or dir), glob? (default *), max? (default 50)
  json    path [repo + rev], select ("a.b.0.c"), and for a list: match (regex over each item), fields
  local   operation, model, schema, instruction, input (a prior step id): one named bounded
          transform through nexus.delegation.local_task; a failed validator escalates, never guesses
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

from . import delegation

LEDGER = os.path.expanduser("~/Library/Application Support/nexus/ledger.sqlite")
OUT_DIR = os.path.expanduser("~/Library/Application Support/nexus/batches")
CAP = 20000  # characters per step result; more is marked truncated, never silently cut
GIT_READ = {"log", "show", "diff", "rev-parse", "ls-files", "ls-tree", "status", "branch", "grep", "blame",
            "cat-file", "merge-base", "for-each-ref", "rev-list", "describe", "shortlog"}
GIT_DENY = ("--output", "-o", "--ext-diff", "--textconv", "-c", "--exec", "--open-files-in-pager", "-O")


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _cap(text):
    return (text[:CAP], True) if len(text) > CAP else (text, False)


def op_sqlite(step, _results):
    db = LEDGER if step["db"] == "ledger" else os.path.expanduser(step["db"])
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as conn:
        cur = conn.execute(step["sql"], step.get("params", []))
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(step.get("limit", 50) + 1)
    more = len(rows) > step.get("limit", 50)
    rows = [dict(zip(cols, r)) for r in rows[:step.get("limit", 50)]]
    return {"rows": rows, "truncated": more}, {"db": db, "sql": step["sql"], "sha256": _sha(json.dumps(rows, default=str))}


def op_git(step, _results):
    args = list(step["args"])
    if not args or args[0] not in GIT_READ:
        raise ValueError(f"git {args[:1]} is not a read subcommand")
    if any(a == d or a.startswith(d + "=") for a in args for d in GIT_DENY):
        raise ValueError("git flag that writes or runs external programs")
    repo = os.path.expanduser(step["repo"])
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {"GIT_PAGER": "cat", "GIT_EXTERNAL_DIFF": ""}
    proc = subprocess.run(["git", "--no-pager", *args], cwd=repo, capture_output=True, text=True, timeout=60, env=env)
    if proc.returncode:
        raise RuntimeError(f"git exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    text, cut = _cap(proc.stdout)
    return {"text": text, "truncated": cut}, {"cmd": ["git", *args], "repo": repo, "sha256": _sha(proc.stdout)}


def _file(step):
    """A file's text now, or at `rev` in `repo` (git show) for a replay of an earlier moment."""
    if step.get("rev"):
        repo = os.path.expanduser(step["repo"])
        proc = subprocess.run(["git", "--no-pager", "show", f"{step['rev']}:{step['path']}"], cwd=repo,
                              capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise RuntimeError(f"git show exit {proc.returncode}: {proc.stderr.strip()[:300]}")
        return f"{repo}@{step['rev']}:{step['path']}", proc.stdout
    path = os.path.expanduser(step["path"])
    with open(path, errors="replace") as fh:
        return path, fh.read()


def op_read(step, _results):
    path, body = _file(step)
    lines = body.splitlines()
    if "section" in step:
        level = len(step["section"]) - len(step["section"].lstrip("#"))
        start = next(i for i, ln in enumerate(lines) if ln.strip() == step["section"].strip())
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("#") and len(lines[i]) - len(lines[i].lstrip("#")) <= level), len(lines))
        a, b = start + 2, end  # 1-indexed, after the heading
    elif "around" in step:
        hit = next(i for i, ln in enumerate(lines) if re.search(step["around"], ln))
        ctx = step.get("ctx", 20)
        a, b = max(1, hit + 1 - ctx), min(len(lines), hit + 1 + ctx)
    else:
        a, b = step["range"]
    text, cut = _cap("\n".join(lines[a - 1:b]))
    return {"text": text, "lines": [a, b], "truncated": cut}, {"path": path, "sha256": _sha(body)}


def op_grep(step, _results):
    root, pat, limit = os.path.expanduser(step["path"]), re.compile(step["pattern"]), step.get("max", 50)
    files = [root] if os.path.isfile(root) else []
    for d, dirs, names in ([] if files else os.walk(root)):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x != "node_modules")
        files += [os.path.join(d, f) for f in sorted(names) if fnmatch.fnmatch(f, step.get("glob", "*"))]
    hits = []
    for path in files:
        try:
            with open(path, errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if pat.search(line):
                        hits.append({"path": path, "line": n, "text": line.rstrip()[:300]})
                        if len(hits) > limit:
                            break
        except OSError:
            continue
        if len(hits) > limit:
            break
    return {"hits": hits[:limit], "truncated": len(hits) > limit}, {"path": root, "pattern": step["pattern"], "files": len(files)}


def op_json(step, _results):
    path, body = _file(step)
    value = json.loads(body)
    for part in filter(None, step["select"].split(".")):
        value = value[int(part)] if isinstance(value, list) else value[part]
    if isinstance(value, list) and ("match" in step or "fields" in step):
        pat = re.compile(step.get("match", ""), re.I)
        value = [{k: v.get(k) for k in step["fields"]} if step.get("fields") and isinstance(v, dict) else v
                 for v in value if pat.search(json.dumps(v, default=str))]
    text, cut = _cap(json.dumps(value, default=str))
    return {"value": json.loads(text) if not cut else text, "truncated": cut}, {"path": path, "select": step["select"], "sha256": _sha(body)}


def op_local(step, results):
    source = results[step["input"]]
    if "error" in source:
        raise RuntimeError(f"input step {step['input']} failed")
    data = json.dumps({k: v for k, v in source.items() if k not in ("provenance",)}, default=str)[:CAP]
    result, rec = delegation.local_task(
        step["operation"], step["schema"], data, model=step["model"],
        prompt=lambda d: f"{step['instruction']}\n\nInput:\n{d}\n\nReply with JSON matching the schema only.",
        fallback=lambda d, reason: (None, "frontier"))  # the supervisor decides; nothing is guessed here
    if result is None:
        raise RuntimeError(f"escalated to supervisor: {rec['escalation']}")
    return {"value": result, "tier": rec["tier"]}, {"model": step["model"], "operation": step["operation"], "input": step["input"]}


OPS = {"sqlite": op_sqlite, "git": op_git, "read": op_read, "grep": op_grep, "json": op_json, "local": op_local}


def run(spec):
    t0 = time.time()
    results, steps = {}, []
    for step in spec["steps"]:
        started = time.time()
        entry = {"id": step["id"], "op": step["op"]}
        try:
            if step["op"] not in OPS:
                raise ValueError(f"unknown op {step['op']}")
            body, provenance = OPS[step["op"]](step, results)
            entry.update(body, provenance=provenance)
        except Exception as exc:  # the batch reports every failure as evidence and keeps going
            entry["error"] = f"{type(exc).__name__}: {exc}"[:400]
        entry["ms"] = int((time.time() - started) * 1000)
        results[step["id"]] = entry
        steps.append(entry)
    errors = [s["id"] for s in steps if "error" in s]
    out = {"task": spec["task"], "spec_sha256": _sha(json.dumps(spec, sort_keys=True)), "ok": not errors,
           "errors": errors, "ms": int((time.time() - t0) * 1000), "steps": steps}
    delegation.record(f"batch:{spec['task'][:60]}", "deterministic", latency_ms=out["ms"], ok=out["ok"],
                      provenance=out["spec_sha256"])
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("spec")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    with open(a.spec) as fh:
        spec = json.load(fh)
    out = run(spec)
    path = a.out or os.path.join(OUT_DIR, f"{re.sub(r'[^a-z0-9]+', '-', spec['task'].lower())[:40]}-{int(time.time())}.json")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(dict(out, result=path), default=str, separators=(",", ":")))  # read once, not step by step
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
