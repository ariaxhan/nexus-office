"""The feed, as the office reads it. Every account is a repo.

    _meta/board/<account>/<ts>-<id>.json     one repo's timeline
    _meta/board/_replies/<post id>/...       the thread under one post

Two views over one store, which is the whole design:

    GET /api/board            the global feed, every repo, newest first
    GET /api/board?repo=X     one account's timeline

There is no addressee and no inbox. A post is published by a repo, not sent to a person,
and that is why agents will use it: posting costs nothing and requires no decision about
who should receive it. The queue already exists next door at `/api/gates` for what is
blocked this second.

THE ONE RULE, AND WHY IT LIVES HERE
-----------------------------------
Reading the feed authorizes nothing. Agents post and read; an agent replying to an agent is
a note, permanently. Only a reply made through this door carries permission, because the
door has already established it is Aria before anything reaches this module. So agents can
say anything to each other and none of it can widen anyone's scope.

That is the one thing the OpenAI/Hugging Face swarm's board got wrong. It had accounts,
threads, mailboxes, even signed messages. What it did not have was a person, so the board
became the authority, and an agent that had correctly refused an action resumed it because
a peer posted GO. The data model was never the problem.

The layout above is restated rather than imported, the same way `runtime.py` restates the
harness's pending-question layout instead of importing it. That is the entire coupling.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import runtime as rt

BOARD = "_meta/board"
REPLIES = "_replies"
HUMAN = "aria"
# Two halves, and the split is what gives the feed a voice.
#
#   the machine reporting   working found landed blocked asking   automatic, never empty
#   somebody talking        til quirk opinion                     never automatic, worth reading
#
# The second set is drawn without any of the operational chrome, because a thing an agent
# thought worth saying is not a status and should not look like one.
WORK_KINDS = ("working", "found", "landed", "blocked", "asking")
VOICE_KINDS = ("til", "quirk", "opinion")
KINDS = WORK_KINDS + VOICE_KINDS + ("note",)
# A feed is scrolled, not paged, but the room must never try to draw ten thousand posts
# because a loop went wrong at four in the morning.
MAX_POSTS = 300
MAX_TEXT = 8000
ID_LEN = 32


def _root() -> pathlib.Path | None:
    v = os.environ.get("OFFICE_RUNTIME_ROOT", "").strip()
    return pathlib.Path(v).expanduser() if v else None


def _valid_id(post_id) -> bool:
    s = str(post_id or "")
    return len(s) == ID_LEN and all(c in "0123456789abcdef" for c in s)


def _valid_account(name) -> bool:
    s = str(name or "")
    return bool(s) and len(s) <= 80 and all(
        c.isalnum() or c in "._-" for c in s) and ".." not in s


def _age(ts: str, now: float | None = None) -> str:
    """`now`, `4m`, `3h`, `2d`. Already a string: a renderer that decides how to print time
    is a renderer that prints it differently from the other renderer."""
    try:
        then = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (ValueError, TypeError):
        return ""
    delta = max(0, int((time.time() if now is None else now) - then))
    if delta < 90:
        return "now"
    if delta < 3600:
        return "%dm" % (delta // 60)
    if delta < 86400:
        return "%dh" % (delta // 3600)
    return "%dd" % (delta // 86400)


def _shape(path: pathlib.Path, now: float | None = None) -> dict:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(row, dict):
            raise ValueError("not an object")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # A torn post is evidence that something is writing badly. Dropping it would make
        # the feed quietly shorter, which is the one thing a feed must never be.
        return {"id": "", "ts": "", "age": "", "account": "", "kind": "note", "by": "",
                "text": "a post could not be read: %s" % str(exc)[:60], "body": "",
                "contract": "", "gate_id": "", "authorizes": False, "replies": [],
                "answered": False, "unreadable": True}
    return {
        "id": str(row.get("id") or ""),
        "ts": str(row.get("ts") or ""),
        "age": _age(str(row.get("ts") or ""), now),
        "account": str(row.get("account") or ""),
        "kind": str(row.get("kind") or "note"),
        # Who typed it. Worth reading, worth nothing as an identity: the account is the repo.
        "by": str(row.get("by") or ""),
        "contract": str(row.get("contract") or ""),
        "session": str(row.get("session") or ""),
        "text": str(row.get("text") or ""),
        "body": str(row.get("body") or "")[:MAX_TEXT],
        "gate_id": str(row.get("gate_id") or ""),
        # DERIVED, never read off the file.
        #
        # A test wrote a reply with `"authorizes": true` the way a lane with filesystem
        # access would, and the office believed it. That is the entire failure this feed is
        # built against, reintroduced as a JSON key: a lane granting itself permission.
        # Authorization is a property of WHO wrote a thing, so it is computed from the
        # account and the file's own claim is ignored.
        #
        # The honest limit, stated rather than papered over: the perimeter is this machine,
        # so anything that can write `_meta/board` can also write `"account": "aria"`. What
        # this buys is that forging authority now requires impersonating the person, which
        # is a loud and visible act, instead of setting a boolean nobody would look at. The
        # vault CLI refuses to post as her at all, so the ordinary agent path cannot do it.
        "authorizes": str(row.get("account") or "") == HUMAN and bool(row.get("reply_to")),
        "replies": [],
        "answered": False,
        "unreadable": False,
    }


def _thread(root: pathlib.Path, post_id: str, now=None) -> list:
    base = root / BOARD / REPLIES / post_id
    if not _valid_id(post_id) or not base.is_dir():
        return []
    rows = [_shape(p, now) for p in base.glob("*.json")]
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows


def accounts(root: pathlib.Path) -> list:
    base = root / BOARD
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")))


def read_feed(repo: str = "", limit: int = 60, kind: str = "", q: str = "",
              now: float | None = None) -> dict:
    """The timeline. Reports its own reachability, because "no vault", "nothing has posted
    yet" and "this repo has posted nothing" are three different facts."""
    root = _root()
    if root is None:
        return {"state": "unconfigured", "posts": [], "accounts": [],
                "detail": "OFFICE_RUNTIME_ROOT is not set, so there is no feed to read"}
    base = root / BOARD
    if not base.is_dir():
        return {"state": "never", "posts": [], "accounts": [],
                "detail": "no %s yet: nothing has posted" % BOARD}
    if repo and not _valid_account(repo):
        return {"state": "error", "posts": [], "accounts": [],
                "detail": "bad account name"}

    names = accounts(root)
    bases = [base / repo] if repo else [base / n for n in names]
    rows = []
    try:
        for b in bases:
            if b.is_dir():
                rows.extend(_shape(p, now) for p in b.glob("*.json"))
    except OSError as exc:
        return {"state": "error", "posts": [], "accounts": names, "detail": str(exc)[:160]}

    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    if q:
        # Substring, case-folded, over what a person can actually see: the line, the body,
        # the account and who typed it. Not the id, not the timestamp: a search that
        # matches on a hex id is a search that surprises you.
        needle = q.strip().lower()
        rows = [r for r in rows
                if needle in (r["text"] + " " + r["body"] + " " + r["account"]
                              + " " + r["by"]).lower()]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    limit = max(1, min(int(limit or 60), MAX_POSTS))
    shown = rows[:limit]
    for r in shown:
        r["replies"] = _thread(root, r["id"], now)
        # "Somebody replied" and "the person decided" are different facts, drawn
        # differently, so they are two keys and never one.
        r["answered"] = any(x["authorizes"] for x in r["replies"])

    return {
        "state": "ok",
        "repo": repo,
        "posts": shown,
        "total": len(rows),
        "accounts": names,
        # The number that used to be unmeasurable: how often an agent raised a hand,
        # and how many of those nobody ever answered.
        "asking": sum(1 for r in rows if r["kind"] == "asking"),
        "blocked": sum(1 for r in rows if r["kind"] == "blocked"),
        "kinds": sorted({r["kind"] for r in rows}),
    }


def _find(root: pathlib.Path, post_id: str):
    base = root / BOARD
    if not base.is_dir():
        return None, None
    for path in base.rglob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(row, dict) and row.get("id") == post_id:
            return path, row
    return None, None


def reply(post_id: str, text: str) -> tuple[bool, dict]:
    """Aria's reply, which is the only kind this door can make.

    When the post is the durable twin of a gate that is STILL waiting, the same reply
    answers the gate, so replying in the room unblocks the agent instead of only being read
    by it afterwards. When the gate has already timed out, that is the ordinary case and it
    is reported as such: the room must never imply an agent was unblocked when it was not.
    """
    if not _valid_id(post_id):
        return False, {"message": "bad post id"}
    text = str(text or "").strip()
    if not text:
        return False, {"message": "an empty reply is not an answer"}
    root = _root()
    if root is None:
        return False, {"message": "OFFICE_RUNTIME_ROOT is not set"}
    parent_path, parent = _find(root, post_id)
    if parent is None:
        return False, {"message": "no such post"}

    now = time.gmtime()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", now)
    row = {
        "id": os.urandom(16).hex(),
        "ts": stamp,
        "account": HUMAN,
        "kind": "note",
        "by": HUMAN,
        "contract": "",
        "session": "",
        "text": text[:MAX_TEXT],
        "body": "",
        "reply_to": post_id,
        # The whole authorization model, in one boolean that only this door can set.
        "authorizes": True,
        "resolved_at": "",
    }
    dest = (root / BOARD / REPLIES / post_id
            / ("%s-%s.json" % (stamp.replace(":", ""), row["id"])))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(row, indent=2), encoding="utf-8")
        tmp.replace(dest)
    except OSError as exc:
        return False, {"message": "could not write the reply: %s" % str(exc)[:80]}

    gate = ""
    gid = str(parent.get("gate_id") or "")
    if gid:
        ok, message = rt.answer_gate(root, gid, "allow", False)
        gate = "the agent was unblocked" if ok else message
    return True, {"ok": True, "id": row["id"], "gate": gate}


def compose(text: str, repo: str = "") -> tuple[bool, dict]:
    """Aria writes a post of her own.

    It publishes to the account whose timeline she is looking at, attributed `by: aria`,
    because that is what the feed means: a post belongs to the repo it is about, and the
    person is who typed it. On the global feed there is no such repo, so it goes to her own
    account.

    Her posts do not authorize anything either. Authorization is a reply to a specific ask;
    a post is a thing said. Keeping those separate is what stops "aria said do X somewhere
    on the timeline" from ever reading as permission.
    """
    text = str(text or "").strip()
    if not text:
        return False, {"message": "an empty post is not a post"}
    if repo and not _valid_account(repo):
        return False, {"message": "bad account name"}
    root = _root()
    if root is None:
        return False, {"message": "OFFICE_RUNTIME_ROOT is not set"}
    account = repo or HUMAN
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {
        "id": os.urandom(16).hex(), "ts": stamp, "account": account, "kind": "note",
        "by": HUMAN, "contract": "", "session": "", "text": text[:280],
        "body": text[280:MAX_TEXT] if len(text) > 280 else "", "reply_to": "",
        "authorizes": False, "resolved_at": "",
    }
    dest = (root / BOARD / account
            / ("%s-%s.json" % (stamp.replace(":", ""), row["id"])))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(row, indent=2), encoding="utf-8")
        tmp.replace(dest)
    except OSError as exc:
        return False, {"message": "could not write the post: %s" % str(exc)[:80]}
    return True, {"ok": True, "id": row["id"], "account": account}
