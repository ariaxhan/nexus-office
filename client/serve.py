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
bind. The one thing still checked in software is the gate: a permission answer
carries the id of the question it is answering, and is refused if the agent has
moved on. Configuration is office-sync.py's, and every call to GitHub is still
made there, unchanged.

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
import chat  # noqa: E402  (needs the path above)
import runtime as rt  # noqa: E402

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
TRUSTED_HOSTS = {h.strip().lower() for h in os.environ.get("OFFICE_TRUSTED_HOSTS", "").split(",") if h.strip()}

BAD_ID = "permit needs the question id it is answering"
BAD_ANSWER = "permit answer must be allow or deny"
NO_ROOT = "no runtime root configured (OFFICE_RUNTIME_ROOT)"
NO_PAGE = "no web page here; open the Office app, or the phone page arrives in milestone 2"

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
         ".woff2": "font/woff2", ".map": "application/json"}


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
    dist = None
    chatroom = None

    def log_message(self, fmt, *args):
        # One line per API call, on stderr. Every asset in dist/ is noise.
        if getattr(self, "path", "").startswith("/api/"):
            log(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}".rstrip())

    # ── the door ────────────────────────────────────────────────────────────
    def _host_ok(self) -> bool:
        host = (self.headers.get("host") or "").lower()
        port = self.server.server_address[1]
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"} or host in TRUSTED_HOSTS

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
            if host not in {f"127.0.0.1:{self.server.server_address[1]}",
                            f"localhost:{self.server.server_address[1]}"} and host not in TRUSTED_HOSTS:
                return "cross-origin write refused"
        return None

    # ── plumbing ────────────────────────────────────────────────────────────
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _read_json(self, limit=256 * 1024):
        n = int(self.headers.get("content-length") or 0)
        if n > limit:
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
            if path == "/api/gate":
                return self._json(rt.read_gate())
            if path == "/api/bots":
                return self._json(self.chatroom.roster())
            if path == "/api/chat":
                bot = (urllib.parse.parse_qs(query).get("bot") or [""])[0]
                code, body = self.chatroom.history(bot)
                return self._json(body, code)
            if path == "/api/health":
                return self._json({"ok": True, "snapshot_at": self.world.at,
                                   "server_time": now_iso()})
            if path.startswith("/api/"):
                return self._json({"error": "not found"}, 404)
            return self._static(path)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"[:300]}, 500)

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        path = self.path.partition("?")[0]
        if not self._host_ok():
            return self._json({"error": "wrong host"}, 403)
        why = self._write_ok()
        if why:
            return self._json({"error": why}, 403)
        try:
            if path == "/api/decision":
                return self._decision(self._read_json())
            if path == "/api/gate":
                return self._gate(self._read_json())
            if path == "/api/desks":
                return self._desks(self._read_json())
            if path == "/api/chat":
                # Returns before the turn has run: a chat turn is an agent run,
                # and nothing on the other end of this socket waits two minutes.
                code, body = self.chatroom.say(self._read_json())
                return self._json(body, code)
            return self._json({"error": "not found"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)[:200]}, 400)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"[:300]}, 500)

    def _decision(self, body):
        err, d = validate(body)
        if err:
            return self._json({"error": err}, 400)
        # Applied now, not queued. Everything that re-derives the intent from its
        # own fields and re-probes push access still happens, in office-sync.py,
        # exactly as it did when a drain ran it two minutes later.
        ok, result = office_sync.apply_decision(d, self.world.access(), dry=False)
        self.world.record(d["kind"], d["repo"], d["issue"], ok, result)
        log(f"{d['kind']} {d['repo']}#{d['issue'] or '-'}: {'ok' if ok else 'FAILED'} {result}")
        # A permit that did not apply means the gate is closed or has moved on.
        # That is a conflict, not a server fault, and the room has to say which.
        if not ok and d["kind"] == "permit":
            return self._json({"ok": False, "result": result}, 409)
        return self._json({"ok": bool(ok), "result": result})

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

    def _static(self, path):
        """A built page, if one exists. Anything that is not a file falls back to
        index.html. There is no web build today: the surface is the Mac app, and
        `dist/` is where the phone page will land in milestone 2."""
        if not self.dist or not self.dist.is_dir():
            return self._json({"error": NO_PAGE}, 404)
        rel = urllib.parse.unquote(path).lstrip("/")
        root = self.dist.resolve()
        target = (root / rel).resolve() if rel else (root / "index.html")
        if not target.is_relative_to(root) or not target.is_file():
            target = root / "index.html"
        if not target.is_file():
            return self._json({"error": NO_PAGE}, 404)
        self._send(200, target.read_bytes(),
                   TYPES.get(target.suffix, "application/octet-stream"))


def make_server(world: World, dist: pathlib.Path | None = None, port: int = 8790):
    """Loopback only. Never 0.0.0.0: the bind address IS the security model."""
    handler = type("BoundHandler", (Handler,),
                   {"world": world, "dist": dist, "chatroom": chat.Chatroom()})
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

    httpd = make_server(world, HERE.parent / "dist", a.port)
    threading.Thread(target=world.keep_fresh, daemon=True).start()
    log(f"http://127.0.0.1:{a.port}/  (loopback only; the door is this machine)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("closing")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
