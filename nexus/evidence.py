"""Have we seen this before? A small evidence packet at flight start (#210 D5).

Two sources, both authoritative and neither copied: this task's own prior attempts from the
ledger, and the institution's earned scars from agentdb (a side-effect-free scored recall,
resolved read-only). Every line carries its provenance id so the agent can say which one it
used. Retrieval never blocks a flight; a retrieval failure is recorded, not hidden.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time

LIMIT = 1200  # characters of packet text: small enough that noise costs nothing measurable
GLOBAL_DB = os.environ.get("NEXUS_AGENTDB_GLOBAL", os.path.expanduser("~/Developer/Vaults/_meta/agentdb/global.db"))
STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "it", "as", "by", "from", "at"}


def prior_attempts(led, task, limit=3, exclude=None):
    """This obligation's earlier flights (every generation of its dedupe key): outcome and what they left."""
    rows = led.conn.execute(
        "SELECT f.id, f.state, f.result FROM flights f JOIN tasks t ON t.id = f.task_id "
        "WHERE t.dedupe_key = ? AND f.id != ? ORDER BY f.created_at DESC LIMIT ?",
        (task["dedupe_key"], exclude or "", limit)).fetchall()
    out = []
    for fid, state, result in rows:
        error = (json.loads(result or "{}").get("error") or {})
        pending = led.conn.execute("SELECT payload FROM events WHERE kind='work.pending' AND subject=? "
                                   "ORDER BY id DESC LIMIT 1", (fid,)).fetchone()
        held = [r[0] for r in led.conn.execute("SELECT ref FROM artifacts WHERE flight_id=? AND kind='held_branch'", (fid,))]
        why = error.get("detail") or (json.loads(pending[0]).get("reason") if pending else "") or ""
        out.append({"id": f"nexus:flight:{fid}", "state": state, "why": str(why)[:160], "held": held})
    return out


def _terms(text):
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()) if w not in STOP]
    return " ".join(dict.fromkeys(words))[:120]


def _insight(dbs, lid):
    for db in dbs:
        if db and os.path.exists(db):
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as c:
                row = c.execute("SELECT type, insight FROM learnings WHERE id=? AND archived_at IS NULL", (lid,)).fetchone()
            if row:
                return row
    return None


MIN_SCORE = 3  # 2026-09-26: every score-2 hit on #183/#190/#191 shared only "make"/"office"; empty beats noise


_INDEX = {}
REFRESH = 600  # seconds between re-reads of agentdb for learnings not yet embedded


def semantic(query):
    """[(id, cosine)] from the local embedding index (#235 B): 98% recall@5 on the frozen corpus
    where lexical recall gets 18%. Raises when the local model or index is unavailable."""
    from . import precedent
    index = _INDEX.get("index")
    if index is None or time.time() - _INDEX["at"] > REFRESH:
        index = index or precedent.Index()
        index.refresh(budget=200)
        _INDEX.update(index=index, at=time.time())
    return index.search(query, 8), index


def scars(query, repo, limit=2, run=subprocess.run):
    """([{id, type, insight}], error|None): semantic precedent first, `agentdb recall --scores` as the
    fallback. Read-only either way: ids only, no hit_count bump."""
    terms = _terms(query)
    if not terms:
        return [], None
    try:
        from . import precedent
        hits, index = semantic(query)
    except Exception as exc:  # any local-model failure degrades to lexical, never blocks a flight
        fallback = f"precedent unavailable: {type(exc).__name__}"
    else:
        out = []
        for lid in precedent.gate([h for h, _ in hits], [], hits, query, index.texts, limit=limit, structure=False):
            p = precedent.packet(lid, index.texts[lid].split("\n")[0])
            out.append({"id": p["id"], "type": "precedent", "insight": precedent.render(p)[:300]})
        return out, None
    found, error = _lexical(terms, repo, limit, run)
    return found, error or fallback


def _lexical(terms, repo, limit, run):
    repo = repo or os.getcwd()  # recall reads the project db of its cwd; resolve ids against the same one
    try:
        proc = run(["agentdb", "recall", terms, "--global", "--scores"], cwd=repo,
                   capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"recall unavailable: {type(exc).__name__}"
    rows = [line.split("\t") for line in proc.stdout.splitlines() if "\t" in line]
    ids = [row[0] for row in rows if _score(row[1]) >= MIN_SCORE]
    dbs = [os.path.join(repo, "_meta", "agentdb", "agent.db"), GLOBAL_DB]
    out = []
    for lid in ids:
        try:
            row = _insight(dbs, lid)
        except sqlite3.Error:
            row = None
        if row:
            out.append({"id": f"agentdb:{lid}", "type": row[0], "insight": row[1][:220]})
        if len(out) == limit:
            break
    return out, (None if proc.returncode == 0 else f"recall exit {proc.returncode}")


def _score(text):
    try:
        return float(text)
    except ValueError:
        return 0.0


def packet(led, task, issue, repo, moment="start", run=subprocess.run, flight=None):
    """The packet as recorded (`work.evidence`) and the text the agent reads."""
    prior = prior_attempts(led, task, exclude=flight)  # never the flight being briefed
    found, error = scars(f"{issue.get('title', '')} {moment if moment != 'start' else ''}", repo, run=run)
    lines = []
    for p in prior:
        lines.append(f"- [{p['id']}] {p['state']}: {p['why']}" + (f" (held on {', '.join(p['held'])})" if p["held"] else ""))
    for s in found:
        lines.append(f"- [{s['id']}] {s['type']}: {s['insight']}")
    text = ""
    if lines:
        text = ("\n\nEarlier evidence (Nexus ledger and institutional memory). Check it against the live "
                "repo before relying on it; live state wins. If an item changed what you did, name its id "
                "in your final message.\n" + "\n".join(lines))[:LIMIT]
    return {"moment": moment, "prior": [p["id"] for p in prior], "scars": [s["id"] for s in found],
            "error": error, "chars": len(text)}, text
