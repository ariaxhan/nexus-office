"""Buzz requests as records at the channel (nexus-office#309, inventory pd-10).

The listener on the MacBook Buzz session writes one record per request post:
{channel, post_id, author, thread_state, state}. The record, not an agent's reading, is the intake.

thread_state holds both sides of the thread as data: every post, the requester's last post and
Aria's last reply. `unanswered` / `answered` is computed from it and the store refuses any other
assertion: a trigger recomputes it from thread_state on every write. Conversion to a work order
is the one other arrow, `-> converted`, and it files a #301 dispatch record on the owning lane's
queue; the dispatch poller does the rest. Converted is terminal; the thread keeps updating.

A request is a thread root that asks Aria (`office_buzz.channel_ask`), not by Aria, in a channel
other than #human-decisions (read-only, decisions have their own path). Posting rules unchanged:
this module never posts.
"""

from __future__ import annotations

import json
import os
import sys
import time

from . import dispatch

ARIA = "Aria"
CHANNELS = ("general", "workroom", "queue")
_ANSWERED = ("json_extract(NEW.thread_state,'$.aria_last_at') IS NOT NULL AND "
             "json_extract(NEW.thread_state,'$.aria_last_at') >= json_extract(NEW.thread_state,'$.requester_last_at')")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS buzz_requests (
  post_id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  author TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  posted_at TEXT NOT NULL,
  text TEXT NOT NULL,
  thread_state TEXT NOT NULL CHECK (json_valid(thread_state)),
  state TEXT NOT NULL CHECK (state IN ('unanswered','answered','converted')),
  state_at REAL NOT NULL,
  work_order TEXT
)""",
    f"""CREATE TRIGGER IF NOT EXISTS buzz_request_state_insert BEFORE INSERT ON buzz_requests
WHEN NEW.state != CASE WHEN {_ANSWERED} THEN 'answered' ELSE 'unanswered' END
BEGIN SELECT RAISE(ABORT, 'a buzz request is born in the state its thread shows'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS buzz_request_state_update BEFORE UPDATE ON buzz_requests
WHEN (OLD.state = 'converted' AND NEW.state != 'converted')
  OR (NEW.state = 'converted' AND (NEW.work_order IS NULL OR NOT EXISTS
      (SELECT 1 FROM dispatch_records d WHERE d.record_id = NEW.work_order)))
  OR (NEW.state != 'converted' AND NEW.state != CASE WHEN {_ANSWERED} THEN 'answered' ELSE 'unanswered' END)
  OR NEW.post_id != OLD.post_id OR NEW.channel != OLD.channel OR NEW.author != OLD.author
  OR (OLD.work_order IS NOT NULL AND NEW.work_order IS NOT OLD.work_order)
BEGIN SELECT RAISE(ABORT, 'buzz request state is computed from its thread, or converted to a filed work order'); END""",
    """CREATE TRIGGER IF NOT EXISTS buzz_request_no_delete BEFORE DELETE ON buzz_requests
BEGIN SELECT RAISE(ABORT, 'buzz requests are history'); END""",
]


class BuzzRequestError(ValueError):
    pass


def ensure(led):
    dispatch.ensure(led)
    for statement in SCHEMA:
        led.conn.execute(statement)


def _row(row):
    if row is None:
        return None
    out = dict(row)
    out["thread_state"] = json.loads(out["thread_state"])
    return out


def get(led, post_id):
    ensure(led)
    return _row(led.conn.execute("SELECT * FROM buzz_requests WHERE post_id=?", (post_id,)).fetchone())


def records(led, states=None):
    ensure(led)
    sql, args = "SELECT * FROM buzz_requests", []
    if states:
        sql += f" WHERE state IN ({','.join('?' * len(states))})"
        args = list(states)
    return [_row(r) for r in led.conn.execute(sql + " ORDER BY posted_at", args)]


def _asks(row):
    from client.office_buzz import channel_ask
    return channel_ask(row)


def is_request(row):
    return (not row.get("reply_to") and row.get("channel") in CHANNELS
            and row.get("author") != ARIA and _asks(row))


def thread_state(root, posts, known=()):
    """Both sides of one thread, as data: the posts already held plus whatever this read saw."""
    seen = {p["id"]: p for p in known}
    seen.update({p["id"]: p for p in posts if p.get("thread") == root["id"] or p["id"] == root["id"]})
    seen.setdefault(root["id"], root)
    thread = sorted(seen.values(), key=lambda p: (p["at"], p["id"]))
    last = lambda who: max((p["at"] for p in thread if p["author"] == who), default=None)  # noqa: E731
    return {"posts": [{"id": p["id"], "author": p["author"], "at": p["at"], "text": p["text"]} for p in thread],
            "requester_last_at": last(root["author"]), "aria_last_at": last(ARIA),
            "last_by": thread[-1]["author"] if thread else None}


def computed_state(ts):
    aria = ts.get("aria_last_at")
    return "answered" if aria and aria >= (ts.get("requester_last_at") or "") else "unanswered"


def listen(led, rows, now=None):
    """One pass over a channel snapshot: file new request records, refresh every known thread."""
    ensure(led)
    now = time.time() if now is None else now
    report = {"filed": 0, "changed": 0}
    known = {r["post_id"]: r for r in records(led)}
    # A known thread refreshes from its persisted root and posts, so a reply still lands after the
    # root has scrolled out of the read window.
    roots = {r["post_id"]: {"id": r["post_id"], "channel": r["channel"], "author": r["author"],
                            "thread": r["thread_id"], "at": r["posted_at"], "text": r["text"]}
             for r in known.values()}
    roots.update({r["id"]: r for r in rows if r["id"] not in roots and is_request(r)})
    for root in roots.values():
        held = known.get(root["id"], {}).get("thread_state", {}).get("posts", ())
        ts = thread_state(root, rows, held)
        state = computed_state(ts)
        blob = json.dumps(ts, sort_keys=True)
        with led.tx() as c:
            old = c.execute("SELECT state, thread_state FROM buzz_requests WHERE post_id=?", (root["id"],)).fetchone()
            if old is None:
                c.execute("INSERT INTO buzz_requests (post_id, channel, author, thread_id, posted_at, text,"
                          " thread_state, state, state_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (root["id"], root["channel"], root["author"], root["thread"], root["at"], root["text"],
                           blob, state, now))
                led._event("buzz.request", root["id"], {"from": None, "to": state, "channel": root["channel"]},
                           "buzz-listener", now)
                report["filed"] += 1
                continue
            if old["thread_state"] == blob:
                continue
            if old["state"] == "converted":
                c.execute("UPDATE buzz_requests SET thread_state=? WHERE post_id=?", (blob, root["id"]))
                continue
            c.execute("UPDATE buzz_requests SET thread_state=?, state=?, state_at=CASE WHEN state=? THEN state_at"
                      " ELSE ? END WHERE post_id=?", (blob, state, state, now, root["id"]))
            if old["state"] != state:
                led._event("buzz.request", root["id"], {"from": old["state"], "to": state}, "buzz-listener", now)
                report["changed"] += 1
    return report


def convert(led, post_id, queue, act, verify, now=None):
    """Request -> work order: file a #301 record on the owning lane's queue and link it, atomically."""
    record = get(led, post_id)
    if record is None:
        raise BuzzRequestError(f"no buzz request {post_id}")
    if record["state"] == "converted":
        raise BuzzRequestError(f"{post_id} is already work order {record['work_order']}")
    if not (isinstance(act, str) and act.strip()):
        raise BuzzRequestError("act is required")
    if not (isinstance(verify, list) and verify and all(isinstance(a, str) and a for a in verify)):
        raise BuzzRequestError("verify must be a non-empty argv: the command that reads the completion surface")
    now = time.time() if now is None else now
    payload = {"channel": record["channel"], "post_id": post_id, "author": record["author"], "text": record["text"],
               "thread_state": record["thread_state"], "act": act, "verify": verify}
    # One transaction: the order exists only if the request says converted to it, so a racing
    # converter or listener rolls back its order instead of leaving one unlinked and dispatchable.
    with led.tx() as c:
        was = c.execute("SELECT state FROM buzz_requests WHERE post_id=?", (post_id,)).fetchone()["state"]
        if was == "converted":
            raise BuzzRequestError(f"{post_id} was converted while converting")
        order = dispatch.file_in(led, c, queue, "buzz-request", payload, now)
        c.execute("UPDATE buzz_requests SET state='converted', state_at=?, work_order=? WHERE post_id=?",
                  (now, order, post_id))
        led._event("buzz.request", post_id, {"from": was, "to": "converted", "work_order": order,
                                             "queue": queue}, "buzz-listener", now)
    return order


def read_channels(channels=CHANNELS):
    """The live Buzz session's snapshot (TBS `buzz read`), via the Office's existing reader."""
    from client.office_buzz import _read
    rows = []
    for channel in channels:
        rows.extend(_read(channel))
    return rows


def main(argv=None):
    from .ledger import Ledger
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["listen"]:
        print("usage: python -m nexus.buzz_requests listen [ledger]", file=sys.stderr)
        return 2
    led = Ledger(argv[1] if len(argv) > 1 else os.environ.get("NEXUS_LEDGER"))
    try:
        print(json.dumps(listen(led, read_channels())))
    finally:
        led.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
