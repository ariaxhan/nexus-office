"""Precedent retrieval and packets (#235 B/C): the right earlier incident, or nothing.

Lexical recall (`agentdb recall`) misses a lesson the moment the new incident uses different
words (24% recall@5 on the frozen corpus). A local embedding index over the same learnings,
fused with lexical rank and a structural overlap signal (file names, symbols, error codes),
finds it by meaning. Embeddings are cached by text hash, read-only against agentdb, and every
failure degrades to lexical: retrieval never blocks a flight.

A packet is the lesson split into what a flight can act on (symptom, scope, cause, repair),
derived deterministically from the lesson text, with its provenance id. No model rewrites it.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import os
import re
import sqlite3
import urllib.request

OLLAMA = os.environ.get("NEXUS_OLLAMA", "http://127.0.0.1:11434")
VAULTS = os.path.expanduser("~/Developer/Vaults")
SOURCES = [os.environ.get("NEXUS_AGENTDB_GLOBAL", f"{VAULTS}/_meta/agentdb/global.db"), f"{VAULTS}/_meta/agentdb/agent.db"]
CACHE = os.environ.get("NEXUS_PRECEDENT_CACHE", os.path.expanduser("~/Library/Caches/nexus/precedent"))
STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "it", "as", "by", "from", "at"}
MODEL = os.environ.get("NEXUS_PRECEDENT_MODEL", "qwen3-embedding:0.6b")
FLOOR = {"qwen3-embedding:0.6b": 0.55, "nomic-embed-text": 0.72, "embeddinggemma": 0.45}  # ~lowest gold hit (docs/local-delegation.md)
PREFIX = {  # each model's own query/document convention
    "nomic-embed-text": ("search_query: ", "search_document: "),
    "embeddinggemma": ("task: search result | query: ", "title: none | text: "),
    "qwen3-embedding:0.6b": ("Instruct: Given a failure report, retrieve the lesson that explains it\nQuery: ", ""),
}


def terms(text):
    """The lexical query `nexus.evidence` has always sent."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()) if w not in STOP]
    return " ".join(dict.fromkeys(words))[:120]


def signature(text):
    """Distinctive tokens: file names, CamelCase and snake_case symbols, ERRNO-style codes, #refs."""
    toks = set(re.findall(r"[A-Za-z_][\w-]{2,}\.[a-z]{2,4}\b", text))
    toks |= set(re.findall(r"\b[A-Z][a-z0-9]+[A-Z]\w*\b|\b[a-z]+_[a-z_]+\b|\b[A-Z]{4,}\b|#\d{2,}", text))
    return {t.lower() for t in toks}


def _embed(model, texts, query=False, timeout=120):
    pre = PREFIX.get(model, ("", ""))[0 if query else 1]
    body = json.dumps({"model": model, "input": [pre + t[:2000] for t in texts]}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/embed", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        vecs = json.loads(resp.read())["embeddings"]
    out = []
    for v in vecs:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


class Index:
    """Every live learning, embedded once per (model, text); search is a dot product."""

    def __init__(self, model=MODEL, sources=None, cache=None, evidence=True):
        self.model, self.sources, self.evidence = model, sources or SOURCES, evidence
        os.makedirs(cache or CACHE, exist_ok=True)
        path = os.path.join(cache or CACHE, re.sub(r"[^\w.-]", "_", model) + ".sqlite")
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS vec (hash TEXT PRIMARY KEY, v BLOB)")
        self.ids, self.texts, self.vecs = [], {}, []

    def _rows(self):
        for src in self.sources:
            if not os.path.exists(src):
                continue
            with sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=2) as c:
                for lid, insight, evidence in c.execute(
                        "SELECT id, insight, evidence FROM learnings WHERE archived_at IS NULL"):
                    yield lid, (f"{insight}\n{evidence}" if self.evidence and evidence else insight)

    def refresh(self, batch=64, budget=None):
        """Load every learning; embed text not seen before, at most `budget` of it this call (a cold
        cache fills over several calls instead of stalling one flight). Returns how many were embedded."""
        rows = {lid: text for lid, text in self._rows()}
        key = {lid: hashlib.sha256(f"{self.model}\0{t}".encode()).hexdigest() for lid, t in rows.items()}
        have = {h for h, in self.db.execute("SELECT hash FROM vec")}
        todo = [lid for lid in rows if key[lid] not in have][:budget]
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            for lid, v in zip(chunk, _embed(self.model, [rows[lid] for lid in chunk])):
                self.db.execute("INSERT OR REPLACE INTO vec VALUES (?, ?)", (key[lid], array.array("f", v).tobytes()))
            self.db.commit()
        blobs = dict(self.db.execute("SELECT hash, v FROM vec"))
        self.ids = [lid for lid in rows if key[lid] in blobs]
        self.texts = rows
        self.vecs = [array.array("f", blobs[key[lid]]) for lid in self.ids]
        return len(todo)

    def search(self, query, k=20):
        q = _embed(self.model, [query], query=True, timeout=30)[0]
        scored = [(sum(a * b for a, b in zip(q, v)), lid) for lid, v in zip(self.ids, self.vecs)]
        scored.sort(reverse=True)
        return [(lid, round(s, 4)) for s, lid in scored[:k]]


def fuse(lex, emb, k=60):
    """Reciprocal-rank fusion of two ranked id lists."""
    score = {}
    for ranked in (lex, emb):
        for i, (lid, _) in enumerate(ranked):
            score[lid] = score.get(lid, 0.0) + 1.0 / (k + i + 1)
    return sorted(score, key=lambda lid: -score[lid])


def structural(order, query, texts):
    """Stable re-sort: candidates sharing a distinctive token with the incident first."""
    sig = signature(query)
    if not sig:
        return list(order)
    return sorted(order, key=lambda lid: -len(sig & signature(texts.get(lid, ""))))


def gate(order, lex, emb, query, texts, limit=2, structure=True, floor=FLOOR[MODEL]):
    """0-2 ids worth a flight's attention: each must clear lexical, semantic or structural evidence."""
    lex_ok = {lid for lid, s in lex if s >= 3}
    emb_ok = {lid for lid, s in emb if s >= floor}
    sig = signature(query) if structure else set()
    keep = [lid for lid in order if lid in lex_ok or lid in emb_ok or (sig & signature(texts.get(lid, "")))]
    return keep[:limit]


def hypothesis(model, query):
    """HyDE: a local model's guess at the lesson, used only as retrieval text, never shown to a flight."""
    prompt = (f"Failure report:\n{query}\n\nIn one sentence, state the most likely root cause and fix, naming the "
              "concrete mechanism (files, tools, error codes).")
    try:
        body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                           "options": {"temperature": 0, "num_predict": 80}}).encode()
        req = urllib.request.Request(f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["response"].strip()
    except (OSError, ValueError, KeyError):
        return ""


def rerank(model, query, ids, texts):
    """Optional local reranker over a small candidate set; any failure keeps the input order."""
    listing = "\n".join(f"{i}. {texts.get(lid, '')[:300]}" for i, lid in enumerate(ids))
    prompt = (f"Incident:\n{query}\n\nEarlier lessons:\n{listing}\n\nWhich lesson most likely explains the "
              'incident? Reply JSON only: {"best": <number or null>}')
    try:
        body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json",
                           "options": {"temperature": 0, "num_predict": 30}}).encode()
        req = urllib.request.Request(f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            best = json.loads(json.loads(resp.read())["response"]).get("best")
        best = int(best)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return list(ids)
    if not 0 <= best < len(ids):
        return list(ids)
    return [ids[best]] + [lid for i, lid in enumerate(ids) if i != best]


CAUSE = re.compile(r"\s*(?:->|→|;|\bbecause\b|\bso\b|\bwere\b|\bwas\b|\bnot\b)\s*", re.I)
REPAIR = re.compile(r"\b(?:fix(?:ed)?(?: by| =|:)|instead(?: of)?|always|never|must|use|keep|add)\b", re.I)


def packet(lid, text, source="agentdb"):
    """A verified lesson as fields a flight can act on: symptom, cause and repair are substrings of the
    lesson, scope its distinctive tokens. Deterministic; no model rewrites a lesson."""
    text = " ".join(text.split())
    m = REPAIR.search(text)
    head, repair = (text[:m.start()], text[m.start():]) if m and m.start() > 20 else (text, "")
    c = CAUSE.search(head)
    symptom, cause = (head[:c.start()], head[c.end():]) if c and c.start() > 10 else (head, "")
    return {"id": f"{source}:{lid}", "symptom": symptom.strip(" ,.")[:160], "cause": cause.strip(" ,.")[:200],
            "repair": repair.strip(" ,.")[:200], "scope": sorted(signature(text))[:6]}


def render(p):
    """The packet as one line of flight text (without its id)."""
    parts = [p["symptom"]]
    if p["cause"]:
        parts.append(f"cause: {p['cause']}")
    if p["repair"]:
        parts.append(f"repair: {p['repair']}")
    if p["scope"]:
        parts.append(f"scope: {', '.join(p['scope'])}")
    return " | ".join(parts)
