#!/usr/bin/env python3
"""Prior-incident retrieval benchmark (#235 B): same frozen cases, every method.

  index --model M                 embed every live learning once (cached by text hash)
  run   --model M [--rerank R] [--hyde G]   score lexical / embedding / hybrid / hybrid+structure
                                  (plus a local reranker or HyDE query expansion when named)
  weak  --gen G --samples N       a weak model diagnoses forward10 blind / with current (lexical)
                                  retrieval / with precedent retrieval / with the oracle lesson

Cases: tests/fixtures/retrieval/frozen45.json (the 2026-09-27 report's corpus, query =
the lesson's own symptom half) and forward10.json (incident reports written before the
diagnosis, gold = the lesson that held the verified fix; the Matra chat.db-wal case plus
three forward paraphrases are the regression). Lexical is `nexus.evidence`'s exact query
path; nothing here writes to agentdb.
"""

import argparse
import json
import os
import re
import resource
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nexus import precedent  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "retrieval"
MATRA = "LRN-20260927041409-34145-16574"
MATRA_FORWARD = [  # no mechanism words: no chat.db, no wal, no TOCTOU
    "scheduled job intermittently fails to open a sqlite db under ~/Library, 'unable to open database file', works interactively",
    "cron task reading a sqlite database in Application Support sometimes errors with no such file, rerun by hand is fine",
    "hourly run hits sqlite3.OperationalError unable to open database file on the messages database, next run succeeds",
]


def cases():
    frozen = [{"set": "frozen45", "query": c["query"], "gold": c["id"], "distinctive": c["distinctive"]}
              for c in json.loads((FIX / "frozen45.json").read_text())]
    forward = [{"set": "forward", "query": c["query"], "gold": c["gold"], "distinctive": []}
               for c in json.loads((FIX / "forward10.json").read_text()) if c["gold"].startswith(("learn_", "LRN-"))]
    fwd45 = json.loads((FIX / "forward45.json").read_text())["cases"]
    distinct = {c["id"]: c["distinctive"] for c in json.loads((FIX / "frozen45.json").read_text())}
    forward45 = [{"set": "forward45", "query": c["query"], "gold": c["id"], "distinctive": distinct[c["id"]]} for c in fwd45]
    matra = [{"set": "matra-forward", "query": q, "gold": MATRA, "distinctive": []} for q in MATRA_FORWARD]
    return frozen + forward45 + forward + matra


def lexical(query):
    """evidence.scars' candidate list, before the insight lookup: (id, score) in recall order."""
    import subprocess
    terms = precedent.terms(query)
    proc = subprocess.run(["agentdb", "recall", terms, "--global", "--scores"], cwd=str(ROOT.parents[1]),
                          capture_output=True, text=True, timeout=20)
    rows = [line.split("\t") for line in proc.stdout.splitlines() if "\t" in line]
    out = []
    for row in rows:
        try:
            out.append((row[0], float(row[1])))
        except ValueError:
            pass
    return out


def relevant(case, lid, texts):
    if lid == case["gold"]:
        return True
    toks = {t.lower() for t in case["distinctive"]}
    return bool(toks and toks & precedent.signature(texts.get(lid, "")))


def metrics(rows, texts, k_packet=2):
    n = len(rows)
    out = {"n": n}
    for k in (1, 3, 5):
        out[f"recall@{k}"] = round(sum(1 for r in rows if r["rank"] and r["rank"] <= k) / n, 3)
    out["mrr@5"] = round(sum(1 / r["rank"] for r in rows if r["rank"] and r["rank"] <= 5) / n, 3)
    delivered = [r for r in rows if r["packet"]]
    noisy = [r for r in delivered if not any(relevant(r["case"], lid, texts) for lid in r["packet"])]
    out["packet_irrelevant_rate"] = round(len(noisy) / n, 3)  # a packet was sent and none of it applied
    out["packet_empty_rate"] = round((n - len(delivered)) / n, 3)
    out["packet_items_mean"] = round(sum(len(r["packet"]) for r in rows) / n, 2)
    out["latency_ms_p50"] = sorted(r["ms"] for r in rows)[n // 2]
    return out


RUBRIC = ROOT.parents[1] / "_meta/reports/context-evals/retrieval/fourarm"


def _retry(call):
    for attempt in range(3):  # ollama drops a connection when it swaps models under memory pressure
        try:
            return call()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(10)


def _generate(model, prompt, seed):
    import urllib.request
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"num_predict": 350, "seed": seed}}).encode()
    req = urllib.request.Request(precedent.OLLAMA + "/api/generate", body, {"Content-Type": "application/json"})
    return _retry(lambda: json.loads(urllib.request.urlopen(req, timeout=300).read())["response"])


def weak(a, index):
    """The 2026-09-27 four-arm experiment's prompt and rubric, plus a precedent-retrieval arm."""
    sys.path.insert(0, str(RUBRIC))
    import score_rubric
    manifest = json.loads((RUBRIC / "manifest.json").read_text())
    prompt = lambda f, h=None: ("Diagnose the root cause of this failure and propose the smallest fix. "  # noqa: E731
                                "Be specific about the mechanism, not generic advice.\n\nFailure:\n" + f +
                                ("\n\nRetrieved prior institutional history (may or may not be relevant; verify "
                                 "before trusting):\n" + h if h else ""))
    rows, tally = {}, {}
    for name, c in manifest.items():
        hits = index.search(c["failure"], 8)
        keep = precedent.gate([h for h, _ in hits], [], hits, c["failure"], index.texts, structure=False, floor=precedent.FLOOR[a.model])
        improved = "\n".join(f"- [agentdb:{lid}] " + precedent.render(precedent.packet(lid, index.texts[lid].split("\n")[0])) for lid in keep)
        arms = {"blind": None, "current": c["current_retrieval_history"], "precedent": improved or "(none)",
                "oracle": c["oracle_history"]}
        rows[name] = {"precedent_ids": keep, "gold": c["gold_id"], "arms": {}}
        for arm, hist in arms.items():
            verdicts = []
            for seed in range(1, a.samples + 1):
                out = _generate(a.gen, prompt(c["failure"], hist), seed)
                det, _ = score_rubric.deterministic_score(name, out)
                verdicts.append(det or _retry(lambda: score_rubric.qwen_judge(name, c["verified_fix"], out)) + "(residual)")
            rows[name]["arms"][arm] = verdicts
            t = tally.setdefault(arm, {"MATCH": 0, "PARTIAL": 0, "MISS": 0})
            for v in verdicts:
                t[v.split("(")[0]] += 1
            print(name, arm, verdicts, file=sys.stderr, flush=True)
    report = {"gen": a.gen, "embed": a.model, "samples": a.samples, "tally": tally, "cases": rows}
    text = json.dumps(report, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(json.dumps(tally, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("index", "run", "weak"):
        p = sub.add_parser(name)
        p.add_argument("--model", default="nomic-embed-text")
        p.add_argument("--rerank")
        p.add_argument("--hyde")
        p.add_argument("--insight-only", action="store_true")
        p.add_argument("--out")
        p.add_argument("--gen", default="qwen2.5:7b-instruct")
        p.add_argument("--samples", type=int, default=3)
    a = ap.parse_args()
    t0 = time.time()
    index = precedent.Index(a.model, evidence=not a.insight_only)
    n = index.refresh()
    if a.cmd == "index":
        print(json.dumps({"model": a.model, "embedded": n, "rows": len(index.ids), "s": round(time.time() - t0, 1)}))
        return
    if a.cmd == "weak":
        return weak(a, index)
    texts = index.texts
    methods = {"lexical": [], "embedding": [], "hybrid": [], "hybrid+structure": []}
    if a.rerank:
        methods["rerank:" + a.rerank] = []
    if a.hyde:
        methods["hyde:" + a.hyde] = []
    F = precedent.FLOOR[a.model]
    for case in cases():
        q = case["query"]
        t = time.time(); lex = lexical(q); lex_ms = int((time.time() - t) * 1000)
        t = time.time(); emb = index.search(q, 20); emb_ms = int((time.time() - t) * 1000)
        ranked = {
            "lexical": ([lid for lid, _ in lex], [lid for lid, s in lex if s >= 3][:2], lex_ms),
            "embedding": ([lid for lid, _ in emb], [lid for lid, s in emb if s >= precedent.FLOOR[a.model]][:2], emb_ms),
        }
        t = time.time()
        hyb = precedent.fuse(lex, emb)
        ranked["hybrid"] = (hyb, precedent.gate(hyb, lex, emb, q, texts, structure=False, floor=F), lex_ms + emb_ms + int((time.time() - t) * 1000))
        t = time.time()
        st = precedent.structural(hyb, q, texts)
        ranked["hybrid+structure"] = (st, precedent.gate(st, lex, emb, q, texts, floor=F), lex_ms + emb_ms + int((time.time() - t) * 1000))
        if a.rerank:
            t = time.time()
            eo = [lid for lid, _ in emb]
            rr = precedent.rerank(a.rerank, q, eo[:8], texts) + eo[8:]
            ranked["rerank:" + a.rerank] = (rr, precedent.gate(rr, lex, emb, q, texts, floor=F), ranked["hybrid+structure"][2] + int((time.time() - t) * 1000))
        if a.hyde:
            t = time.time()
            he = index.search(q + "\n" + precedent.hypothesis(a.hyde, q), 20)
            hs = [lid for lid, _ in he]
            ranked["hyde:" + a.hyde] = (hs, precedent.gate(hs, [], he, q, texts, structure=False, floor=F), int((time.time() - t) * 1000))
        for name, (order, packet, ms) in ranked.items():
            rank = order.index(case["gold"]) + 1 if case["gold"] in order else None
            methods[name].append({"case": case, "rank": rank, "packet": packet, "ms": ms})
    report = {"model": a.model, "rows_indexed": len(index.ids),
              "rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1),
              "sets": {}}
    for s in ("frozen45", "forward45", "forward", "matra-forward"):
        report["sets"][s] = {m: metrics([r for r in rows if r["case"]["set"] == s], texts) for m, rows in methods.items()}
    report["matra_forward_ranks"] = {m: [r["rank"] for r in rows if r["case"]["set"] == "matra-forward"] for m, rows in methods.items()}
    text = json.dumps(report, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
