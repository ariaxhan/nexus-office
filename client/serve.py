#!/usr/bin/env python3
"""The office, served from the machine that already holds the credentials.

This replaces the Cloudflare Worker and its queue. There is no cloud, no
database, no password and no drain: the snapshot is built in this process and a
decision is applied the moment it arrives, so what you clicked is done before the
button finishes moving.

  serve.py                 serve on http://127.0.0.1:8790
  serve.py --port 8791     somewhere else
  serve.py --once          build one snapshot, print it, exit
  serve.py --root DIR      the agent runtime root (OFFICE_RUNTIME_ROOT)

The security model is the bind address. The socket is loopback only, never
0.0.0.0, so the door is the machine and there is nothing to log in to. Reaching
it from a phone goes through Tailscale Serve in front of this, never a wider
bind, and `GET /` is the page that phone opens: three files out of client/phone/
through the same Host and identity checks as every call under /api/, so there is
nothing on it to log in to either. The one thing still checked in software is
the gate: a permission answer
carries the id of the question it is answering, and is refused if the agent has
moved on. Configuration is office-sync.py's, and every call to GitHub is still
made there, unchanged.

One path is not like the others. `POST /webhook` is what Tailscale Funnel puts
on the public internet, and it is the only route exempt from the tailnet login
and the origin rule, because GitHub can satisfy neither. It is held up by an
HMAC over the raw bytes instead, and with no OFFICE_WEBHOOK_SECRET set it
answers 503 rather than accepting anything unsigned. See `client/webhook.py`.

The room rebuilds every OFFICE_POLL_S seconds (default 300). That number is a
GitHub budget, not a taste: one build asks GitHub once per ten desks, and the
hour holds 5000 GraphQL points. `?fresh=1` still forces a build, at most once a
minute, and answers `"fresh": false` when it handed back the cache instead.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import board  # noqa: E402
import buzz  # noqa: E402  (needs the path above)
import chat  # noqa: E402
import context  # noqa: E402
import runtime as rt  # noqa: E402
import sessions  # noqa: E402
import webhook  # noqa: E402

# The command is hyphenated because it is a command first and a module second.
# Loading it by path is what the tests do too; renaming it would rename the thing
# everybody types.
_spec = importlib.util.spec_from_file_location("office_sync", HERE / "office-sync.py")
office_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(office_sync)

ISO = "%Y-%m-%dT%H:%M:%SZ"
# How often the room rebuilds itself. Every build asks GitHub, and GitHub gives
# a user 5000 GraphQL points an hour, so the interval is a budget decision and
# belongs in the environment rather than in this line.
POLL_S = float(os.environ.get("OFFICE_POLL_S", "") or 300)
# `?fresh=1` is the "no, now" button. It stays a button and not a tap dance: a
# second one inside this window gets the cache and is told so.
FRESH_MIN_S = 60.0
FRESH_WAIT_S = 45
BUILD_WAIT_S = 300

GITHUB_KINDS = {"comment", "unblock", "close", "reopen", "label", "nudge", "merge"}
RUNTIME_KINDS = {"permit", "chat", "run", "stop"}
# fullmatch with ASCII: Python's `$` forgives a trailing newline and `\w` is Unicode,
# neither of which the Worker's JS regexes allowed. Parity, not paranoia.
REPO_RE = re.compile(r"[\w.-]+/[\w.-]+\Z", re.ASCII)
QID_RE = re.compile(r"[0-9a-f]{8,64}\Z", re.ASCII)
NUM_RE = re.compile(r"\d+\Z", re.ASCII)

# The bind address keeps the network out. It does not keep the browser out: any
# page you have open can POST to 127.0.0.1, and a text/plain form post needs no
# preflight. So every request must name this door as its Host, and a write must
# come from this origin (or from no page at all: curl, the app) as JSON. A name
# that fronts this server (tailscale serve, M2) is added through OFFICE_TRUSTED_HOSTS.
# When the Host is one of those names, Tailscale Serve is in front: the request must
# carry Tailscale-User-Login equal to OFFICE_LOGIN, or it is 403. Loopback (the Mac
# app) never sends that header, and a forged one on loopback is ignored.
TRUSTED_HOSTS = {h.strip().lower() for h in os.environ.get("OFFICE_TRUSTED_HOSTS", "").split(",") if h.strip()}
LOGIN = os.environ.get("OFFICE_LOGIN", "").strip().lower()

# THE ONE PUBLIC PATH. Tailscale Funnel exposes `POST /webhook` to the open
# internet and nothing else; every other route stays on the tailnet behind
# `Tailscale-User-Login`, or on loopback. GitHub cannot carry a tailnet login and
# does not send an Origin, so that one route is exempt from `_identity_ok` and
# `_write_ok` and is held up instead by an HMAC over the raw request bytes. With
# no secret set the route answers 503: unsigned is never accepted, because a
# public endpoint that skips the check when it is unconfigured runs your pipeline
# for whoever finds it. The secret itself lives in `webhook.SECRET`, next to the
# code that verifies against it, so this file, the status route and the card on
# the wall cannot disagree about whether webhooks are configured.
#
# GitHub's own payload ceiling. Anything larger is not a delivery.
WEBHOOK_LIMIT = 1024 * 1024
# The accounts the pipeline itself speaks as. A comment from one of these is the
# office's own voice coming back, and acting on it is a machine in a loop with
# itself. Owners are the accounts that hold the desks; OFFICE_BOT_LOGINS names
# any extra bot account whose token this process never sees.
OUR_LOGINS = ({o.strip().lower() for o in office_sync.OWNERS if o.strip()}
              | {b.strip().lower()
                 for b in os.environ.get("OFFICE_BOT_LOGINS", "").split(",") if b.strip()})


def _loopback_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}

# Every write is small enough to be a decision typed by a person, except one: a
# chat turn can carry a screenshot. Only that route gets the bigger ceiling, so a
# half-megabyte permit or desk write is still refused as the mistake it is.
WRITE_LIMIT = 256 * 1024
CHAT_LIMIT = 512 * 1024
# How much of an over-sized body is read before it is refused. An unread body is
# not harmless: on a kept-alive connection the leftovers are parsed as the next
# request, and closing on a sender that is still writing costs it the 400 it
# needed to see. Past this, the connection goes instead.
DRAIN_LIMIT = 8 * 1024 * 1024

BAD_ID = "permit needs the question id it is answering"
BAD_ANSWER = "permit answer must be allow or deny"
NO_ROOT = "no runtime root configured (OFFICE_RUNTIME_ROOT)"
NO_PAGE = "the office is at /; there is nothing else here"

# The phone page: three files, named one by one rather than a directory walked
# at request time. An exact map cannot be talked into serving a sibling, so the
# traversal question never has to be answered correctly under pressure.
PHONE = HERE / "phone"
PAGE = {"/": "index.html", "/index.html": "index.html",
        "/phone.css": "phone.css", "/phone.js": "phone.js"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8"}

# The page loads two files from here, talks to this door, and can reach nowhere
# else. No inline script, no inline style and no external host, which is why the
# page is three files rather than one: a strict policy is worth two more GETs.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


def log(msg: str) -> None:
    print(f"[office] {msg}", file=sys.stderr, flush=True)


# The sync client narrates to stdout, which would sit in the middle of the JSON
# `--once` prints. Its narration is a log, so it goes where logs go.
office_sync.log = lambda m: log(m)


class World:
    """The last snapshot, the one builder allowed to replace it, and what has
    been applied since the process started.

    A request never blocks on GitHub: `/api/world` hands back whatever was built
    last and says when, because a room that hangs for forty seconds is a room
    nobody opens twice. `?fresh=1` is the explicit opt in to waiting.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.build_lock = threading.Lock()
        self.snapshot = None
        self.at = None
        self.error = None
        self.decisions = []
        self.seq = 0
        self._access = None
        self.fresh_at = None  # monotonic stamp of the last `?fresh=1` build

    def access(self):
        with self.lock:
            if self._access is None:
                self._access = office_sync.Access()
            return self._access

    def build(self, wait: float = FRESH_WAIT_S) -> bool:
        """Rebuild the snapshot, one at a time. False means it did not happen."""
        if not self.build_lock.acquire(timeout=wait):
            return False
        try:
            # `generated` and the rolling 24 hour window are stamped from a
            # module-level NOW frozen at import. A server outlives that by days,
            # so the clock is wound here rather than by editing the module whose
            # merge path has to stay byte for byte what the tests proved.
            office_sync.NOW = datetime.now(timezone.utc)
            snap = office_sync.build_snapshot(self.access())
        except Exception as exc:  # noqa: BLE001 - one bad build, not a dead server
            with self.lock:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
            log(f"snapshot failed: {self.error}")
            return False
        finally:
            self.build_lock.release()
        with self.lock:
            self.snapshot = snap
            self.at = snap.get("generated") or now_iso()
            self.error = None
        return True

    def fresh_build(self) -> bool:
        """The `?fresh=1` path: build now, but at most once a minute.

        False means the caller got the cache. It never bypasses a GitHub pause:
        a build during one reads from disk and asks GitHub nothing, which is the
        point — the gate, the runtime and the sections stay live while the hour's
        budget recovers.
        """
        now = time.monotonic()
        with self.lock:
            if self.fresh_at is not None and now - self.fresh_at < FRESH_MIN_S:
                return False
            self.fresh_at = now
        return self.build(wait=FRESH_WAIT_S)

    def refresh_desk(self, nwo: str) -> bool:
        """Refetch ONE desk and swap it into the snapshot.

        About two GraphQL points against an hourly budget of five thousand, so a
        webhook telling us one repo moved costs roughly a four-hundredth of what
        rebuilding the room would. That is the whole reason this exists: without
        it, "GitHub says something changed" and "show it" are the same expensive
        thing, and the office would either poll or go stale.

        False means the desk was not replaced, and it never blanks one: a repo
        that could not be fetched keeps what it had, exactly as a build does.
        """
        if not office_sync.NWO_RE.match(nwo or ""):
            return False
        try:
            who, tok = self.access().token_for(nwo)
        except Exception as exc:  # noqa: BLE001
            log(f"could not find a token for {nwo}: {type(exc).__name__}: {exc}")
            return False
        if not tok:
            log(f"no account holds push on {nwo}; the desk keeps what it had")
            return False
        rows, errors, rate, fatal = office_sync.fetch_batch([nwo], tok)
        office_sync.note_rate(rate)
        row = rows.get(nwo)
        if row is None:
            log(f"could not refresh {nwo}: {errors.get(nwo) or fatal or 'GitHub returned nothing'}")
            return False
        stamp = now_iso()
        with self.lock:
            for st in ((self.snapshot or {}).get("stations") or []):
                if st.get("repo") == nwo:
                    st["issues"] = row["issues"]
                    st["prs"] = row["prs"]
                    st["fetched_at"] = stamp
                    st["issues_error"] = None
                    st["prs_error"] = None
                    return True
        return False

    def mark_hidden(self, repo: str, hidden: bool) -> None:
        """Flip the flag on the desk that is already on screen.

        Putting a desk away is a local decision, so it lands now rather than on
        the next poll. Nothing is fetched: the desk keeps the data it has, which
        is exactly what makes it listable when you want it back.
        """
        with self.lock:
            for st in ((self.snapshot or {}).get("stations") or []):
                if st.get("repo") == repo:
                    st["hidden"] = hidden

    def mark_pins(self, pins: list) -> None:
        """Land a new pin order on the snapshot now, not on the next poll."""
        with self.lock:
            snap = self.snapshot or {}
            snap["pins"] = list(pins)
            for st in (snap.get("stations") or []):
                repo = st.get("repo")
                st["pinned"] = pins.index(repo) if repo in pins else None

    def keep_fresh(self, every: float = POLL_S) -> None:
        while True:
            self.build(wait=BUILD_WAIT_S)
            time.sleep(every)

    def record(self, kind, repo, issue, ok, result) -> dict:
        with self.lock:
            self.seq += 1
            row = {"id": self.seq, "at": now_iso(), "kind": kind, "repo": repo,
                   "issue": issue, "status": "done" if ok else "failed",
                   "result": str(result)[:2000]}
            self.decisions.append(row)
            del self.decisions[:-40]
            return row

    def recent(self) -> list:
        with self.lock:
            return list(reversed(self.decisions))

    def bot_evidence(self, bot: str, message: str) -> dict:
        with self.lock:
            return chat.office_evidence(self.snapshot, bot, message)

    def knows_issue(self, repo: str, issue) -> bool:
        """Only an issue on the wall right now: a bot's block names what it was
        shown, never an arbitrary repo it might have typed."""
        with self.lock:
            for st in ((self.snapshot or {}).get("stations") or []):
                if str(st.get("repo") or "") != repo:
                    continue
                return any(str(i.get("number")) == str(issue) for i in st.get("issues") or [])
        return False

    def apply_now(self, body: dict):
        """(ok, result) for one decision, applied this instant: validated, run
        through office-sync with a fresh push probe, recorded, and the one desk
        it moved refetched. The button and a bot's block both come through here."""
        err, d = validate(body)
        if err:
            return False, err
        ok, result = office_sync.apply_decision(d, self.access(), dry=False)
        self.record(d["kind"], d["repo"], d["issue"], ok, result)
        log(f"{d['kind']} {d['repo']}#{d['issue'] or '-'}: {'ok' if ok else 'FAILED'} {result}")
        if ok and d["kind"] in GITHUB_KINDS:
            try:
                self.refresh_desk(d["repo"])
            except Exception as exc:  # noqa: BLE001 - the decision already landed
                log(f"{d['repo']} was decided but could not be refetched: "
                    f"{type(exc).__name__}: {exc}")
        return ok, result

    def bot_decisions(self, bot: str, reply: str) -> list:
        """Every decision block in a bot's reply, applied. Only Sphinx decides,
        and only about an issue the wall shows; anything else is refused and said."""
        out = []
        for d in chat.decision_blocks(reply):
            row = {"bot": bot, "repo": d["repo"], "issue": d["issue"], "answer": d["answer"],
                   "action": d["action"]}
            if bot != chat.DECIDER:
                ok, result = False, f"{bot} does not decide"
            elif not self.knows_issue(d["repo"], d["issue"]):
                ok, result = False, "not an issue on the wall"
            else:
                body = f"{d['answer']}: {d['comment']}" if d["comment"] else d["answer"]
                ok, result = self.apply_now({"kind": d["action"], "repo": d["repo"],
                                             "issue": str(d["issue"]), "body": body})
            row.update(ok=bool(ok), result=str(result)[:300])
            out.append(row)
        return out


def validate(body: dict):
    """Exactly the Worker's validation, restated. A bad decision is refused here,
    before anything is applied, and says the same words it always did.

    Returns (error message, payload). One of the two is always None.
    """
    kind = str(body.get("kind") or "")
    is_runtime = kind in RUNTIME_KINDS
    if not is_runtime and kind not in GITHUB_KINDS:
        return f"unknown kind {kind}", None

    repo = str(body.get("repo") or "")
    if not is_runtime and not REPO_RE.match(repo):
        return "bad repo", None
    if is_runtime and repo and not REPO_RE.match(repo):
        return "bad repo", None

    issue = None if body.get("issue") is None else str(body.get("issue"))
    if issue is not None and not NUM_RE.match(issue):
        return "bad issue", None
    # A merge is about a PR, not an issue: the issue it closes is written in the
    # PR body and is GitHub's job to act on.
    if not is_runtime and kind not in ("nudge", "merge") and issue is None:
        return f"{kind} needs an issue", None
    if kind == "merge" and not NUM_RE.match(str(body.get("pr") or "")):
        return "a merge needs a numeric pr", None

    # A permit answers ONE specific question. The id travels with it so a gate
    # that has already moved on cannot be answered by position.
    if kind == "permit":
        if not QID_RE.match(str(body.get("question_id") or "")):
            return BAD_ID, None
        if body.get("answer") not in ("allow", "deny"):
            return BAD_ANSWER, None

    payload = {
        "body": body["body"][:20000] if isinstance(body.get("body"), str) else "",
        "label": body["label"][:100] if isinstance(body.get("label"), str) else "",
        "question_id": body["question_id"][:64] if isinstance(body.get("question_id"), str) else "",
        "answer": body["answer"] if body.get("answer") in ("allow", "deny") else "",
        "always": body.get("always") is True,
        "run_id": body["run_id"][:128] if isinstance(body.get("run_id"), str) else "",
        "pr": int(body["pr"]) if NUM_RE.match(str(body.get("pr") or "")) else None,
    }
    return None, {"kind": kind, "repo": repo, "issue": issue, "payload": payload}


class Handler(BaseHTTPRequestHandler):
    server_version = "nexus-office"
    protocol_version = "HTTP/1.1"
    world = None
    chatroom = None
    mailbox = None
    trigger = None
    # Said once, not once per delivery. A hook pointed at a door with no secret
    # gets retried by GitHub for days, and one log line an hour is a note while
    # one per delivery is a wall.
    _no_secret_said = 0.0

    def log_message(self, fmt, *args):
        # One line per API call, on stderr. The page and its two files are noise.
        if getattr(self, "path", "").startswith("/api/"):
            log(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}".rstrip())

    # ── the door ────────────────────────────────────────────────────────────
    def _host_ok(self) -> bool:
        host = (self.headers.get("host") or "").lower()
        port = self.server.server_address[1]
        return host in _loopback_hosts(port) or host in TRUSTED_HOSTS

    def _identity_ok(self) -> bool:
        """Loopback is this Mac. Anything else is the tailnet, and must be Aria."""
        host = (self.headers.get("host") or "").lower()
        port = self.server.server_address[1]
        if host in _loopback_hosts(port):
            return True
        expected = LOGIN
        if not expected:
            return False
        got = (self.headers.get("tailscale-user-login") or "").strip().lower()
        return got == expected

    def _write_ok(self) -> str | None:
        """None when a write may proceed, else the reason it may not."""
        ctype = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return "writes are application/json"
        site = (self.headers.get("sec-fetch-site") or "").lower()
        if site and site not in ("same-origin", "none"):
            return "cross-site write refused"
        origin = (self.headers.get("origin") or "").lower()
        if origin:
            host = origin.split("://", 1)[-1]
            if host not in _loopback_hosts(self.server.server_address[1]) and host not in TRUSTED_HOSTS:
                return "cross-origin write refused"
        return None

    # ── plumbing ────────────────────────────────────────────────────────────
    def _send(self, code, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _drain(self, n: int) -> None:
        """Read a body we are not going to use, so the socket stays in step."""
        left = n
        while left > 0:
            chunk = self.rfile.read(min(left, 64 * 1024))
            if not chunk:
                return
            left -= len(chunk)

    def _read_json(self, limit=WRITE_LIMIT):
        n = int(self.headers.get("content-length") or 0)
        if n > limit:
            if n <= DRAIN_LIMIT:
                self._drain(n)
            else:
                self.close_connection = True
            raise ValueError("payload too large")
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        out = json.loads(raw or "{}")
        if not isinstance(out, dict):
            raise ValueError("expected an object")
        return out

    # ── routes ──────────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802 (http.server's name)
        path, _, query = self.path.partition("?")
        if not self._host_ok():
            return self._json({"error": "wrong host"}, 403)
        if not self._identity_ok():
            return self._json({"error": "not you"}, 403)
        try:
            if path == "/api/world":
                fresh = False
                if urllib.parse.parse_qs(query).get("fresh"):
                    fresh = self.world.fresh_build()
                return self._json({"at": self.world.at, "world": self.world.snapshot,
                                   "decisions": self.world.recent(),
                                   "fresh": fresh, "server_time": now_iso()})
            if path == "/api/desks":
                return self._json({"hidden": office_sync.read_hidden()})
            if path == "/api/pins":
                return self._json({"pins": office_sync.read_pins()})
            if path == "/api/gate":
                return self._json(rt.read_gate())
            if path == "/api/gates":
                return self._json(self._gates())
            if path == "/api/board":
                # The feed. Every account is a repo. No `repo` is the global timeline;
                # `?repo=owner-name` is one account's. `/api/gates` next door is what is
                # blocked this second; this is the durable record of everything said.
                q = urllib.parse.parse_qs(query)
                try:
                    limit = int((q.get("limit") or ["60"])[0])
                except ValueError:
                    limit = 60
                return self._json(board.read_feed(
                    repo=(q.get("repo") or [""])[0],
                    kind=(q.get("kind") or [""])[0],
                    q=(q.get("q") or [""])[0],
                    limit=limit))
            if path == "/api/bots":
                return self._json(self.chatroom.roster())
            if path == "/api/chat":
                bot = (urllib.parse.parse_qs(query).get("bot") or [""])[0]
                code, body = self.chatroom.history(bot)
                return self._json(body, code)
            if path == "/api/webhook":
                return self._json(self._webhook_status())
            if path == "/api/automation":
                # Straight off the snapshot, never a rebuild: every number on
                # this page was measured when the room was built, and a page
                # that re-measures on open would disagree with the card that
                # sent you to it.
                return self._json({"at": self.world.at,
                                   "automation": self.world.snapshot.get("automation") or {}})
            if path == "/api/context":
                # Two names off the query string and nothing else. Every rule
                # about where this may look and what it may open lives in
                # `context.read`, including the refusal of a repo nobody named:
                # a door that pre-judged the request would be a second place
                # deciding what is safe, and two of those drift.
                q = urllib.parse.parse_qs(query)
                code, body = context.read((q.get("repo") or [""])[0],
                                          (q.get("path") or [""])[0])
                return self._json(body, code)
            if path == "/api/sessions":
                repo = (urllib.parse.parse_qs(query).get("repo") or [""])[0]
                return self._json(sessions.read(repo))
            if path == "/api/session":
                q = urllib.parse.parse_qs(query)
                code, body = sessions.transcript((q.get("name") or [""])[0],
                                                 (q.get("last") or [""])[0]
                                                 or sessions.DEFAULT_EXCHANGES)
                return self._json(body, code)
            if path == "/api/readme":
                # The desk's front page, read off this machine. A read, so the
                # only thing it can be pointed at is a checkout the office
                # already knows about, by a name that has to look like a repo.
                repo = (urllib.parse.parse_qs(query).get("repo") or [""])[0]
                code, body = sessions.readme(repo)
                return self._json(body, code)
            if path == "/api/session/screen":
                name = (urllib.parse.parse_qs(query).get("name") or [""])[0]
                code, body = sessions.screen(name)
                return self._json(body, code)
            if path == "/api/health":
                return self._json({"ok": True, "snapshot_at": self.world.at,
                                   "server_time": now_iso()})
            if path.startswith("/api/"):
                return self._json({"error": "not found"}, 404)
            return self._page(path)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"[:300]}, 500)

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        path = self.path.partition("?")[0]
        # THE ONE EXEMPTION FROM `_identity_ok` AND `_write_ok`, AND WHY.
        #
        # This is the single path Tailscale Funnel puts on the public internet.
        # GitHub is not on the tailnet, so it cannot carry `Tailscale-User-Login`,
        # and it is not a browser, so it sends no `Origin` and no `Sec-Fetch-Site`.
        # Both checks would refuse every real delivery. What holds this route up
        # instead is an HMAC-SHA256 over the raw bytes against a secret only
        # GitHub and this machine know, which is a stronger claim than either
        # header: those two say "you came from somewhere I trust", this one says
        # "these exact bytes came from someone holding the secret".
        #
        # `_host_ok` still applies, so a request that does not name this door is
        # refused before the signature is even computed.
        if path == "/webhook":
            return self._webhook()
        # Funnel's `--set-path=/webhook` STRIPS the prefix before proxying, so a
        # mount pointed at a target without a path would arrive here as `POST /`.
        # That is a misconfiguration, and it is told so rather than quietly
        # aliased: a second name for the one public path is a second thing to
        # keep in step, and the first time they drift one of them is unsigned.
        if path == "/":
            return self._json({"error": "the webhook is at /webhook; this is not an alias"}, 404)
        if not self._host_ok():
            return self._json({"error": "wrong host"}, 403)
        if not self._identity_ok():
            return self._json({"error": "not you"}, 403)
        why = self._write_ok()
        if why:
            return self._json({"error": why}, 403)
        try:
            if path == "/api/decision":
                return self._decision(self._read_json())
            if path == "/api/gate":
                return self._gate(self._read_json())
            if path == "/api/board":
                return self._board(self._read_json())
            if path == "/api/desks":
                return self._desks(self._read_json())
            if path == "/api/pins":
                return self._pins(self._read_json())
            if path == "/api/context":
                code, body = context.write(
                    self._read_json(limit=context.MAX_BYTES + 4096))
                return self._json(body, code)
            if path == "/api/chat":
                # Returns before the turn has run: a chat turn is an agent run,
                # and nothing on the other end of this socket waits two minutes.
                code, body = self.chatroom.say(self._read_json(limit=CHAT_LIMIT))
                return self._json(body, code)
            if path == "/api/session/say":
                code, body = sessions.say(self._read_json(limit=CHAT_LIMIT))
                return self._json(body, code)
            if path == "/api/session/start":
                # This starts a real agent with Aria's credentials, so it is the
                # one write on this door that runs a program. Everything that
                # makes that safe is in sessions.start(): the engine is one of
                # two exact names, and the directory is one the office already
                # knows about. Nothing from this body is interpolated anywhere.
                code, body = sessions.start(self._read_json())
                return self._json(body, code)
            return self._json({"error": "not found"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)[:200]}, 400)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"[:300]}, 500)

    def _decision(self, body):
        err, _ = validate(body)
        if err:
            return self._json({"error": err}, 400)
        ok, result = self.world.apply_now(body)
        # A permit that did not apply means the gate is closed or has moved on.
        # That is a conflict, not a server fault, and the room has to say which.
        if not ok and body.get("kind") == "permit":
            return self._json({"ok": False, "result": result}, 409)
        return self._json({"ok": bool(ok), "result": result})

    def _gates(self):
        """Every raised hand, not just the oldest.

        `/api/gate` answers with the one at the front of the queue, which is the
        shape every existing reader has always seen. This one answers with the
        whole floor, because two bots on two threads can both be blocked and a
        room that draws one of them is a room that hides the other.

        `state` appears only when something is wrong, and is the same word
        `/api/gate` would have used, so the two can never disagree about whether
        the gate channel is working.
        """
        got = rt.read_gates()
        out = {"at": now_iso(), "gates": got.get("gates") or []}
        if got.get("state") != "ok":
            out["state"] = got.get("state") or "error"
        return out

    def _gate(self, body):
        qid = str(body.get("question_id") or "")
        answer = body.get("answer")
        if not QID_RE.match(qid):
            return self._json({"ok": False, "message": BAD_ID}, 400)
        if answer not in ("allow", "deny"):
            return self._json({"ok": False, "message": BAD_ANSWER}, 400)
        root = rt._root()
        if root is None:
            return self._json({"ok": False, "message": NO_ROOT}, 409)
        ok, message = rt.answer_gate(root, qid, answer, body.get("always") is True)
        return self._json({"ok": ok, "message": message}, 200 if ok else 409)

    def _board(self, body):
        """Reply to a post in the feed.

        A reply from this door is Aria's, because the door already decided that: nothing
        reaches here without passing `_identity_ok`. That is the whole authorization model
        of the board, and it is why it is safe for agents to post freely into it. They can
        say anything; only what comes back through this method carries permission.
        """
        text = str(body.get("text") or "")
        post_id = str(body.get("id") or "")
        # No id is a post of her own rather than an answer to somebody. Two different acts:
        # a reply can authorize, a post never does, and conflating them would make anything
        # she happened to say on the timeline read as permission.
        if not post_id:
            ok, result = board.compose(text, repo=str(body.get("repo") or ""))
        else:
            ok, result = board.reply(post_id, text)
        return self._json(result if ok else {"ok": False, **result}, 200 if ok else 409)

    def _desks(self, body):
        """Put a desk away, or bring it back.

        A write like any other: same Host check, same JSON-only rule, same origin
        rule. It touches GitHub not at all, which is the whole point of it — a
        hidden desk is never fetched, so putting one away is how you stop paying
        for a repo you are not watching this month.
        """
        repo = str(body.get("repo") or "")
        if not office_sync.NWO_RE.match(repo):
            return self._json({"error": "bad repo"}, 400)
        hidden = body.get("hidden")
        if hidden is not True and hidden is not False:
            return self._json({"error": "hidden must be true or false"}, 400)
        try:
            rows = office_sync.set_hidden(repo, hidden)
        except ValueError as exc:
            return self._json({"error": str(exc)[:120]}, 400)
        self.world.mark_hidden(repo, hidden)
        log(f"desk {repo} is now {'put away' if hidden else 'back'}")
        return self._json({"ok": True, "hidden": rows})

    def _pins(self, body):
        """Replace the pin order, whole.

        The same door as putting a desk away: Host, JSON only, same origin. The
        body is the entire ordered list and never a delta, because the order IS
        the state and a delta against an order is a guess about where the other
        entries went. GitHub is never touched.
        """
        pins = body.get("pins")
        if not isinstance(pins, list):
            return self._json({"error": "pins must be a list of repos"}, 400)
        for repo in pins:
            if not isinstance(repo, str) or not office_sync.NWO_RE.match(repo):
                return self._json({"error": "bad repo"}, 400)
        rows = office_sync.write_pins(pins)
        self.world.mark_pins(rows)
        log(f"pins are now {rows}")
        return self._json({"ok": True, "pins": rows})

    # ── the one public path ─────────────────────────────────────────────────
    def _webhook(self):
        """One GitHub delivery.

        The order is deliberate and it is: size, host, signature, then parse.
        Size first because it is the only check that costs nothing and is the
        only one that can be answered without touching the body. Signature
        before parse because a body that has not proved who sent it is not a
        document, it is bytes, and json.loads on it is the first thing an
        attacker gets to choose.

        The 200 goes out BEFORE any work. GitHub gives a delivery ten seconds
        and retries anything that is not a 2xx, so a door that dispatched first
        and answered second would be redelivered mid-run and start a second one.
        """
        n = int(self.headers.get("content-length") or 0)
        if n > WEBHOOK_LIMIT:
            if n <= DRAIN_LIMIT:
                self._drain(n)
            else:
                self.close_connection = True
            return self._json({"error": "payload too large"}, 413)
        if not self._host_ok():
            self._drain(n)
            return self._json({"error": "wrong host"}, 403)
        ctype = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._drain(n)
            return self._json({"error": "webhooks are application/json"}, 415)
        if not webhook.SECRET:
            self._drain(n)
            now = time.monotonic()
            if now - Handler._no_secret_said > 3600:
                Handler._no_secret_said = now
                log("a webhook arrived and OFFICE_WEBHOOK_SECRET is not set; "
                    "refusing it, because unsigned is never accepted")
            return self._json({"error": "no webhook secret configured"}, 503)

        raw = self.rfile.read(n) if n else b""
        ok = webhook.verify(webhook.SECRET, raw, self.headers.get("x-hub-signature-256") or "")
        run = webhook.note_signature(ok)
        if not ok:
            # Not logged per refusal: a public path that writes a line for every
            # bad post is a disk somebody else gets to fill. The run of them is
            # counted instead, and the webhook card says it out loud.
            if run in (1, 3, 10) or run % 100 == 0:
                log(f"refused a webhook with a bad signature ({run} in a row)")
            return self._json({"error": "bad signature"}, 403)

        delivery = (self.headers.get("x-github-delivery") or "").strip()[:120]
        event = (self.headers.get("x-github-event") or "").strip()[:60]
        if not delivery:
            # GitHub always sends one, and it is the only key that makes a
            # redelivery tell itself apart from a new event. A poster without
            # one is told exactly what is missing rather than quietly deduped
            # against nothing.
            return self._json({"error": "no delivery id"}, 400)

        # Claim, rather than merely check. Two retries can land at once, and a
        # look that is not also a claim lets both through. The claim is released
        # again unless this finishes, so a delivery that was refused or that
        # died half-done comes back on the redeliver button and is handled
        # fresh: that button is the only recovery control there is, and GitHub
        # sends the SAME delivery id when it is pressed.
        if not self.mailbox.claim(delivery):
            return self._json({"ok": True, "duplicate": True, "delivery": delivery})

        handled = False
        try:
            if event == "ping":
                log(f"ping from GitHub, delivery {delivery}")
                handled = True
                return self._json({"ok": True, "pong": True, "delivery": delivery})

            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception as exc:  # noqa: BLE001
                # It carried a valid signature, so this is our misunderstanding
                # and not GitHub's. A non-2xx here would buy a retry of the same
                # unreadable bytes for days.
                log(f"delivery {delivery} carried a body that would not read: {exc}")
                handled = True
                return self._json({"ok": True, "ignored": "unreadable body",
                                   "delivery": delivery})

            ev = webhook.parse(event, delivery, body if isinstance(body, dict) else {})
            if ev is None:
                handled = True
                return self._json({"ok": True, "ignored": event or "unknown",
                                   "delivery": delivery})

            fire = webhook.should_trigger(ev, OUR_LOGINS)
            self.mailbox.append(ev, trigger=fire)
            handled = True
            self._json({"ok": True, "delivery": delivery})
            # Everything past the answer. `notice` drops the event in a mailbox
            # and returns, so the request thread is never the thing waiting on a
            # thirty minute lane.
            if fire and self.trigger is not None:
                self.trigger.notice(ev)
            log(f"{event}.{ev.action or '-'} {ev.repo}#{ev.number or '-'} by {ev.login or '?'}"
                f" -> {'queued' if fire else 'noted'}")
            return None
        finally:
            self.mailbox.settle(delivery, handled)

    def _webhook_status(self) -> dict:
        """Is the mailbox alive, in the shape the app and the phone read.

        Behind the normal door, unlike the route it describes: what arrived is
        this office's business, and nothing here is needed by GitHub.
        """
        box = self.mailbox
        return {
            "configured": bool(webhook.SECRET),
            "seen": box.count() if box else 0,
            "last": box.last_events(20) if box else [],
            "runs": box.last_runs(20) if box else [],
            "queued": self.trigger.queued() if self.trigger else [],
        }

    def _page(self, path):
        """The phone, through the same door as everything else.

        It went through `_host_ok` and `_identity_ok` on the way in, exactly
        like `/api/world` did, so a tailnet request without Aria's login never
        reaches this line. The page itself therefore carries no login UI: there
        is nothing here for a stranger to be asked.
        """
        if path == "/favicon.ico":
            # Chrome asks for this on every load. Nothing here, and no 404 in
            # the console for a thing nobody requested.
            self.send_response(204)
            self.send_header("cache-control", "no-store")
            self.end_headers()
            return None
        name = PAGE.get(path)
        if name is None:
            return self._json({"error": NO_PAGE}, 404)
        target = PHONE / name
        try:
            body = target.read_bytes()
        except OSError:
            return self._json({"error": NO_PAGE}, 404)
        self._send(200, body, TYPES[target.suffix], {
            "content-security-policy": CSP,
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
        })


def make_server(world: World, port: int = 8790):
    """Loopback only. Never 0.0.0.0: the bind address IS the security model.

    The one path that is reachable from outside this machine, `/webhook`, is
    still served on this same loopback socket. Tailscale Funnel is what puts it
    on the internet, and Funnel forwards to 127.0.0.1 like everything else, so
    there is still no wider bind anywhere in this program.
    """
    mailbox = webhook.Mailbox(webhook.STATE)
    trigger = webhook.Trigger(mailbox,
                              root=os.environ.get("OFFICE_RUNTIME_ROOT") or None,
                              refresh=world.refresh_desk,
                              receipts=office_sync.RECEIPTS)
    handler = type("BoundHandler", (Handler,),
                   {"world": world,
                    "chatroom": chat.Chatroom(world.bot_evidence, world.bot_decisions),
                    "mailbox": mailbox, "trigger": trigger})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the office, served on this machine")
    ap.add_argument("--port", type=int, default=int(os.environ.get("OFFICE_PORT", 8790)))
    ap.add_argument("--once", action="store_true", help="one snapshot as JSON, then exit")
    ap.add_argument("--root", help="the agent runtime root (sets OFFICE_RUNTIME_ROOT)")
    a = ap.parse_args(argv)
    if a.root:
        os.environ["OFFICE_RUNTIME_ROOT"] = str(pathlib.Path(a.root).expanduser())

    world = World()
    if a.once:
        if not world.build(wait=BUILD_WAIT_S) or world.snapshot is None:
            log(world.error or "could not build a snapshot")
            return 1
        print(json.dumps(world.snapshot, indent=2))
        return 0

    httpd = make_server(world, a.port)
    threading.Thread(target=world.keep_fresh, daemon=True).start()
    threading.Thread(target=buzz.watch, args=(rt.read_gates, office_sync.RECEIPTS), daemon=True).start()
    log(f"http://127.0.0.1:{a.port}/  (loopback only; the door is this machine)")
    log("webhooks: " + ("/webhook is signed and listening"
                        if webhook.SECRET else
                        "OFF (no OFFICE_WEBHOOK_SECRET; /webhook answers 503)"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("closing")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
