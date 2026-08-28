"""What memory holds, as a source the office can read.

Two stores, and they are not the same thing, so they are read separately and
each reports its own reachability:

  agentdb   a SQLite file at <root>/_meta/agentdb/agent.db. Several hundred flat
            learnings typed failure | gotcha | pattern | preference, each with a
            hit count and an `evidence` string that says where it came from.
            On disk, so it is readable whether or not any server is running.
            This is the reliable half of the library.

  the runtime's semantic memory
            HTTP on <url>/api/memory/summary and /api/memory/review. The runtime
            is a foreground dev server that is usually CLOSED, exactly like the
            board in runtime.py. Down is the normal case, and it is reported as
            "down" rather than swallowed into an empty library.

"the store is not configured", "the store is broken" and "the store is genuinely
empty" must never render the same. Every block below carries its own `state` for
that reason.

Read only. POST /api/memory/review is gated behind a review token and writing
memory from a phone is a separate decision.

Why sqlite3 and not the `agentdb` CLI: the CLI has no machine-readable listing
(`query` prints an aligned ASCII table, `export-json` writes a whole-DB file),
and shelling out adds a process that can hang. Opening the same file read-only
is one import, has a hard timeout, and cannot mutate anything.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import urllib.error
import urllib.request

from sources import _card

KEY = "library"
# The human name of the fixture, fixed. A card whose title moves is a
# card the eye has to find again every time it is drawn.
TITLE = "Library"

DEFAULT_URL = "http://127.0.0.1:8787"
DB_REL = "_meta/agentdb/agent.db"

# The snapshot is pushed every ten minutes and must never hang. sqlite is local
# and a lock is the only thing that could ever block; the runtime is a dev server
# that is either there in milliseconds or not there at all.
DB_TIMEOUT = 2.0
HTTP_TIMEOUT = 2.0

# There is no shelf cap any more, and that is a decision with a cost.
#
# It used to carry the top twenty of each type by hit count and count the rest,
# on the theory that nobody scrolls a shelf to the bottom. That theory was about
# a shelf nobody could open: the room only ever drew the COUNT. Now the card
# carries the whole shelf as rows a person can actually read, and a learning you
# cannot reach from the room is a learning the room does not have. The 632 live
# lessons in the vault this was measured against cost about 850 KB per push
# every ten minutes, over a loopback socket, which is the price of the library
# being real rather than a number.
#
# What still bounds it is the CLIP: no single lesson can be more than these two
# numbers long, so the payload grows with the count and never with one essay.
INSIGHT_CHARS = 420
EVIDENCE_CHARS = 240

# Eye level first. failure and gotcha are the ones that save you, so they get the
# shelves you actually look at; the fixture reads this order top-down.
TYPE_ORDER = ["failure", "gotcha", "pattern", "preference"]


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _url() -> str:
    return os.environ.get("OFFICE_RUNTIME_URL", DEFAULT_URL).rstrip("/")


def _db_path() -> pathlib.Path | None:
    """Absolute, always. agentdb resolves its DB from the working directory, and
    a sub-repo carrying its own `_meta/` routes to a different database with a
    different few hundred learnings in it. The vault root is passed explicitly so
    that cannot happen."""
    env = os.environ.get("AGENTDB_ROOT", "").strip()
    if env:
        return pathlib.Path(env).expanduser().resolve() / DB_REL
    root = _root()
    return (root.resolve() / DB_REL) if root else None


def _rows(cur, sql, args=()):
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _clip(text, n):
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _item(row) -> dict:
    """One learning, with its provenance attached.

    A memory you cannot source is a rumour. `evidence` is what agentdb recorded
    at the moment the lesson was learned, and nine of the 594 have none: those are
    marked `sourced: false` rather than quietly rendered as if they had a source.
    """
    evidence = _clip(row.get("evidence"), EVIDENCE_CHARS)
    return {
        "id": str(row.get("id") or ""),
        "type": str(row.get("type") or "unknown"),
        "insight": _clip(row.get("insight"), INSIGHT_CHARS),
        "hits": int(row.get("hit_count") or 0),
        "loads": int(row.get("load_count") or 0),
        "provenance": {
            "sourced": bool(evidence),
            "evidence": evidence,
            "domain": str(row.get("domain") or ""),
            "learned_at": str(row.get("ts") or ""),
            "last_hit": str(row.get("last_hit") or ""),
            "record": str(row.get("id") or ""),
        },
    }


def read_agentdb() -> dict:
    path = _db_path()
    if path is None:
        return {"state": "unconfigured",
                "detail": "no OFFICE_RUNTIME_ROOT and no AGENTDB_ROOT, so there is no vault to read"}
    if not path.exists():
        return {"state": "absent", "detail": str(path)}

    try:
        # Read-only URI: this process must never be able to write to the brain,
        # and immutable=0 so a live agent writing beside us is still readable.
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=DB_TIMEOUT)
    except sqlite3.Error as exc:
        return {"state": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}

    try:
        cur = con.cursor()
        # Archived learnings are soft-deleted and do not belong on a shelf, but
        # they are still part of the store's size, so they are counted apart.
        shape = _rows(cur, """
            SELECT type,
                   SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END) AS live,
                   SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END) AS archived,
                   SUM(hit_count) AS hits
              FROM learnings GROUP BY type
        """)
        span = _rows(cur, """
            SELECT MIN(ts) AS oldest, MAX(ts) AS newest,
                   COUNT(DISTINCT domain) AS domains, COUNT(*) AS total
              FROM learnings
        """)[0]

        shelves = []
        omitted = 0
        by_type = {}
        for row in shape:
            by_type[str(row["type"])] = int(row["live"] or 0)
        # Types are listed eye-level-first, and anything the store grows that this
        # file has never heard of still gets a shelf rather than disappearing.
        order = TYPE_ORDER + sorted(t for t in by_type if t not in TYPE_ORDER)
        for t in order:
            live = by_type.get(t)
            if live is None:
                continue
            # No LIMIT. Most-recalled first is now only an ORDER, not a filter:
            # what the hit count decides is where a lesson sits, never whether
            # it is carried at all.
            items = [_item(r) for r in _rows(cur, """
                SELECT id, ts, type, insight, evidence, domain, hit_count, last_hit, load_count
                  FROM learnings
                 WHERE type = ? AND archived_at IS NULL
              ORDER BY hit_count DESC, ts DESC
            """, (t,))]
            omitted += max(0, live - len(items))
            shelves.append({"type": t, "count": live, "shown": len(items), "items": items})
    except sqlite3.Error as exc:
        return {"state": "error", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        con.close()

    total_live = sum(s["count"] for s in shelves)
    return {
        "state": "ok",
        "store": {
            "path": str(path),
            "bytes": path.stat().st_size,
            "total": int(span["total"] or 0),
            "live": total_live,
            "archived": sum(int(r["archived"] or 0) for r in shape),
            "hits": sum(int(r["hits"] or 0) for r in shape),
            "domains": int(span["domains"] or 0),
            "oldest": str(span["oldest"] or ""),
            "newest": str(span["newest"] or ""),
        },
        "shelves": shelves,
        # Kept, and now it says zero. The accounting for what was left behind is
        # more useful than ever precisely because the answer is "nothing": a
        # future cap that creeps back in has a place to be noticed rather than a
        # deleted key nobody misses.
        "capped": {
            "shown": sum(s["shown"] for s in shelves),
            "omitted": omitted,
            "insight_chars": INSIGHT_CHARS,
            "evidence_chars": EVIDENCE_CHARS,
            "note": (
                f"{omitted} live learnings were counted but not carried, which "
                "should not be able to happen"
            ) if omitted else "",
        },
    }


def _get(path: str):
    req = urllib.request.Request(_url() + path, method="GET")
    req.add_header("user-agent", "nexus-office-sync/1.0")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode() or "{}")


def _fetch(path: str) -> dict:
    """One endpoint of the semantic store, or an honest reason why not."""
    try:
        return {"state": "up", "data": _get(path)}
    except urllib.error.HTTPError as exc:
        return {"state": "error", "detail": f"{exc.code} from {path}"}
    except urllib.error.URLError as exc:
        # Not running is the NORMAL case, and it is still a state the room has to
        # show. An unknown review queue must never draw as an empty one.
        return {"state": "down", "detail": str(getattr(exc, "reason", exc))[:160]}
    except Exception as exc:  # noqa: BLE001
        return {"state": "error", "detail": f"{type(exc).__name__}: {exc}"[:160]}


def read_review() -> dict:
    """The pending-review cart. Runtime only: there is no on-disk copy of it, so
    when the runtime is down the honest answer is "unknown", not "nothing"."""
    got = _fetch("/api/memory/review")
    if got["state"] != "up":
        return got
    data = got["data"]
    raw = data.get("pending") or data.get("items") or (data if isinstance(data, list) else [])
    items = []
    for r in (raw or [])[:12]:
        if not isinstance(r, dict):
            r = {"insight": r}
        items.append({
            "id": str(r.get("id") or ""),
            "type": str(r.get("type") or "review"),
            "insight": _clip(r.get("insight") or r.get("content") or r.get("text"), INSIGHT_CHARS),
            "hits": int(r.get("hit_count") or 0),
            "provenance": {
                "sourced": bool(r.get("evidence") or r.get("source")),
                "evidence": _clip(r.get("evidence") or r.get("source"), EVIDENCE_CHARS),
                "domain": str(r.get("domain") or ""),
                "learned_at": str(r.get("ts") or r.get("created_at") or ""),
                "last_hit": "",
                "record": str(r.get("id") or ""),
            },
        })
    total = data.get("count")
    count = int(total) if isinstance(total, int) else len(raw or [])
    return {"state": "up", "count": count, "shown": len(items), "items": items}


def read_semantic() -> dict:
    """The shape of the runtime's semantic store, when it is serving."""
    got = _fetch("/api/memory/summary")
    if got["state"] != "up":
        return got
    d = got["data"] if isinstance(got["data"], dict) else {}
    return {"state": "up", "summary": {k: d[k] for k in list(d)[:16]}}


def read() -> dict:
    flat = read_agentdb()
    out = dict(flat)
    out["review"] = read_review()
    out["semantic"] = read_semantic()
    out["url"] = _url()
    return out


# `absent` is a vault that has never learned anything, which is a real and
# blameless state. Only a store that will not open wants a person.
TROUBLE = {
    "unconfigured": ("not configured", 0),
    "absent": ("no agentdb on disk yet", 0),
    "error": ("the store would not open", 1),
}


def _review_row(review: dict) -> tuple:
    """The review cart as (what to say, tone).

    Down is the normal case and it is NOT an empty queue: the cart lives only in
    the runtime, so when the runtime is closed the honest word is "unknown".
    """
    state = review.get("state")
    if state == "up":
        n = int(review.get("count") or 0)
        return f"{_card.count(n)} pending", ("warn" if n else "ok")
    if state == "down":
        return "unknown, the runtime is closed", "dim"
    return _card.clip(review.get("detail") or state, _card.FACT_CHARS), "bad"


def _semantic_row(semantic: dict) -> tuple:
    state = semantic.get("state")
    if state == "up":
        return "serving", "ok"
    if state == "down":
        return "the runtime is closed", "dim"
    return _card.clip(semantic.get("detail") or state, _card.FACT_CHARS), "bad"


def _row(item: dict, shelf_type: str) -> dict:
    """One learning as a table line a renderer that knows nothing can draw.

    The provenance travels IN the row rather than one level up, because the row
    is the only thing a person reads: a lesson whose evidence stayed behind in a
    field nobody drew is a rumour with a hit count on it. An unsourced memory
    says so in the place the evidence would have been, and is dimmed, so it can
    never be mistaken at a glance for one that can be checked.
    """
    prov = item.get("provenance") or {}
    evidence = str(prov.get("evidence") or "")
    where = " · ".join(p for p in (str(prov.get("domain") or ""),
                                   _card.zulu(prov.get("learned_at")) or
                                   str(prov.get("learned_at") or "")) if p)
    return _card.row(
        str(prov.get("record") or item.get("id") or ""),
        item.get("insight"),
        subtitle=evidence or "no evidence recorded",
        detail=where,
        badge=f"{_card.count(item.get('hits') or 0)}×",
        tone="" if evidence else "dim",
        group=str(item.get("type") or shelf_type),
    )


def rows(data: dict) -> list:
    """Every live learning, in shelf order: eye-level types first, and inside
    each of those the most-recalled first. The order is the shelf's, not a
    second opinion formed here."""
    out = []
    for shelf in data.get("shelves") or []:
        for item in shelf.get("items") or []:
            out.append(_row(item, str(shelf.get("type") or "")))
    return out


def card(data: dict) -> dict:
    """What memory holds, in one line, plus whether anything is waiting on you.

    The shelves are on disk and the review cart is not, so the two are reported
    apart: a closed runtime leaves the cart unknown and must never draw as an
    empty one, and it never takes the shelf counts down with it.
    """
    if data.get("state") != "ok":
        return _card.trouble(TITLE, data.get("state"), data.get("detail"), TROUBLE)

    store = data.get("store") or {}
    live = int(store.get("live") or 0)
    hits = int(store.get("hits") or 0)
    review = data.get("review") or {}
    semantic = data.get("semantic") or {}

    facts = [
        _card.fact("live", _card.count(live), "ok" if live else "dim"),
        _card.fact("archived", _card.count(store.get("archived") or 0), "dim"),
        _card.fact("domains", _card.count(store.get("domains") or 0), "dim"),
    ]
    # The biggest shelves, named. One row rather than one row per type, so the
    # card has room left for the two states that can actually want a person.
    top = sorted((data.get("shelves") or []),
                 key=lambda s: -int(s.get("count") or 0))[:3]
    by_type = ", ".join(f"{s.get('type')} {_card.count(s.get('count') or 0)}"
                        for s in top if int(s.get("count") or 0))
    if by_type:
        facts.append(_card.fact("by type", by_type, "dim"))
    facts.append(_card.fact("review queue", *_review_row(review)))
    facts.append(_card.fact("semantic memory", *_semantic_row(semantic)))

    return _card.build(
        TITLE,
        f"{_card.count(live)} {_card.plural(live, 'learning')}, "
        f"{_card.count(hits)} {_card.plural(hits, 'recall')}",
        1 if review.get("state") == "error" else 0,
        _card.zulu(store.get("newest")),
        facts,
        rows(data),
    )
