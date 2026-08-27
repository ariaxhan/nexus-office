"""The local server that replaced the Worker.

Two things here can be wrong in a way that costs more than a confusing screen.
The socket could be bound somewhere other than loopback, which would put a
surface that applies decisions immediately on whatever network this machine is
sitting on. And a permission answer could reach a gate it was not written for.
So those are what is tested, along with the plain "does it serve the room".

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

SNAP = {
    "generated": "2026-08-26T12:00:00Z",
    "heartbeat": "", "killed": False, "today": {"landed": 2},
    "stations": [{"repo": "acme/thing", "issues": [], "prs": [],
                   "hidden": False, "pinned": None, "fetched_at": "2026-08-26T11:59:00Z"}],
    "pins": [], "owners": [],
    "runtime": {"gate": {"state": "clear"}, "board": {"state": "down"}},
    "sections": {},
    "github": {"limit": 5000, "remaining": 4800, "reset_at": "2026-08-26T13:00:00Z",
               "cost": 8, "paused_until": "", "error": ""},
}


class ServeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import serve

        cls.serve = serve
        serve.log = lambda msg: None  # a passing test suite says nothing
        # Neither of these may touch GitHub or a keychain from a test. The point
        # of the harness is that it runs anywhere, with no credentials at all.
        serve.office_sync.Access = lambda: object()
        serve.office_sync.build_snapshot = lambda access: copy.deepcopy(SNAP)

        # Putting a desk away writes a file. It writes it somewhere disposable
        # here: a test suite that edits ~/.local/state is a test suite that
        # changes the room it is supposed to be checking.
        cls.state = tempfile.TemporaryDirectory()
        sd = pathlib.Path(cls.state.name)
        cls.state_was = (serve.office_sync.STATE, serve.office_sync.HIDDEN_FILE,
                         serve.office_sync.DESKS_CACHE)
        serve.office_sync.STATE = sd
        serve.office_sync.HIDDEN_FILE = sd / "hidden.json"
        serve.office_sync.PINS_FILE = sd / "pins.json"
        serve.office_sync.DESKS_CACHE = sd / "desks.json"
        # The mailbox writes three more files in the same place, and is built
        # inside make_server, so it has to be pointed somewhere disposable
        # before the server exists rather than after.
        cls.webhook_state_was = serve.webhook.STATE
        serve.webhook.STATE = sd

        cls.world = serve.World()
        cls.world.build()
        # Port 0 is "any free port", so several of these can run at once.
        cls.httpd = serve.make_server(cls.world, 0)
        cls.host, cls.port = cls.httpd.server_address[:2]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        (cls.serve.office_sync.STATE, cls.serve.office_sync.HIDDEN_FILE,
         cls.serve.office_sync.DESKS_CACHE) = cls.state_was
        cls.serve.webhook.STATE = cls.webhook_state_was
        cls.state.cleanup()

    # ── plumbing ────────────────────────────────────────────────────────────
    def get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            exc.close()
            return exc.code, json.loads(body or "{}")

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(), method="POST",
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            exc.close()
            return exc.code, json.loads(raw or "{}")

    # ── the room ────────────────────────────────────────────────────────────
    def test_the_socket_is_loopback_only(self):
        """The bind address IS the security model. There is no password behind
        it, so a wider bind would put a surface that merges PRs on the network."""
        self.assertEqual(self.host, "127.0.0.1")

    def test_world_is_the_snapshot_that_was_built(self):
        code, body = self.get("/api/world")
        self.assertEqual(code, 200)
        self.assertEqual(body["world"], SNAP)
        self.assertEqual(body["at"], SNAP["generated"])
        self.assertIsInstance(body["decisions"], list)
        self.assertTrue(body["server_time"])

    def test_health_says_when_the_picture_was_taken(self):
        code, body = self.get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["snapshot_at"], SNAP["generated"])

    def test_gate_reports_its_own_state_rather_than_nothing(self):
        code, body = self.get("/api/gate")
        self.assertEqual(code, 200)
        self.assertIn("state", body)

    # ── the budget ──────────────────────────────────────────────────────────
    # Every build asks GitHub, and GitHub gives a user 5000 GraphQL points an
    # hour. Rebuilding every minute spent about seventy times that, so the room
    # showed "API rate limit already exceeded" on every desk at once. The
    # interval is a budget, and `?fresh=1` is a button, not a tap dance.

    def test_the_room_rebuilds_on_a_budget_not_every_minute(self):
        self.assertGreaterEqual(self.serve.POLL_S, 300)
        self.assertEqual(self.serve.FRESH_MIN_S, 60.0)

    def test_fresh_forces_one_build_a_minute_and_says_which_you_got(self):
        self.world.fresh_at = None
        code, body = self.get("/api/world?fresh=1")
        self.assertEqual(code, 200)
        self.assertTrue(body["fresh"], "the first one really rebuilds")

        code, body = self.get("/api/world?fresh=1")
        self.assertEqual(code, 200)
        self.assertFalse(body["fresh"], "the second one inside the minute does not")
        self.assertEqual(body["world"], SNAP, "and still hands back the room")

        code, body = self.get("/api/world")
        self.assertFalse(body["fresh"], "a plain read never claims to be fresh")

    # ── desks you put away ──────────────────────────────────────────────────
    def test_desks_lists_what_is_put_away(self):
        code, body = self.get("/api/desks")
        self.assertEqual(code, 200)
        self.assertIsInstance(body["hidden"], list)

    def test_putting_a_desk_away_lands_on_the_snapshot_at_once(self):
        """No fetch, no wait for the next poll. The desk keeps its data so the
        app can list it and bring it back."""
        try:
            code, body = self.post("/api/desks", {"repo": "acme/thing", "hidden": True})
            self.assertEqual(code, 200)
            self.assertTrue(body["ok"])
            self.assertIn("acme/thing", body["hidden"])

            code, world = self.get("/api/world")
            desk = world["world"]["stations"][0]
            self.assertTrue(desk["hidden"])
            self.assertEqual(desk["repo"], "acme/thing")
            self.assertEqual(self.get("/api/desks")[1]["hidden"], ["acme/thing"])
        finally:
            code, body = self.post("/api/desks", {"repo": "acme/thing", "hidden": False})
        self.assertEqual(code, 200)
        self.assertEqual(body["hidden"], [])
        self.assertFalse(self.get("/api/world")[1]["world"]["stations"][0]["hidden"])

    def test_bringing_back_a_desk_that_was_never_away_is_a_no_op(self):
        code, body = self.post("/api/desks", {"repo": "never/hidden", "hidden": False})
        self.assertEqual(code, 200)
        self.assertNotIn("never/hidden", body["hidden"])

    def test_a_malformed_desk_is_refused(self):
        for repo in ("", "nope", "a/b/c", "a b/c", "../../etc/passwd", "a/b\n"):
            code, body = self.post("/api/desks", {"repo": repo, "hidden": True})
            self.assertEqual(code, 400, repr(repo))
            self.assertEqual(body["error"], "bad repo")
        self.assertEqual(self.get("/api/desks")[1]["hidden"], [])

    def test_hidden_must_be_a_boolean_and_nothing_else(self):
        for value in ("true", 1, None, "yes", []):
            code, body = self.post("/api/desks", {"repo": "acme/thing", "hidden": value})
            self.assertEqual(code, 400, repr(value))
            self.assertIn("error", body)
        self.assertEqual(self.get("/api/desks")[1]["hidden"], [])

    # ── desks you pinned ────────────────────────────────────────────────────
    def test_pins_start_empty_and_ride_in_the_world(self):
        code, body = self.get("/api/pins")
        self.assertEqual(code, 200)
        self.assertEqual(body["pins"], [])
        world = self.get("/api/world")[1]["world"]
        self.assertEqual(world["pins"], [])
        self.assertIsNone(world["stations"][0]["pinned"])

    def test_posting_pins_replaces_the_whole_order_and_lands_at_once(self):
        try:
            code, body = self.post("/api/pins", {"pins": ["zed/last", "acme/thing"]})
            self.assertEqual(code, 200)
            self.assertEqual(body["pins"], ["zed/last", "acme/thing"], "order kept, not sorted")
            world = self.get("/api/world")[1]["world"]
            self.assertEqual(world["pins"], ["zed/last", "acme/thing"])
            self.assertEqual(world["stations"][0]["pinned"], 1, "rank, on the desk")

            code, body = self.post("/api/pins", {"pins": ["acme/thing"]})
            self.assertEqual(body["pins"], ["acme/thing"], "a replacement, not a merge")
            self.assertEqual(self.get("/api/pins")[1]["pins"], ["acme/thing"])
            self.assertEqual(self.get("/api/world")[1]["world"]["stations"][0]["pinned"], 0)
        finally:
            code, body = self.post("/api/pins", {"pins": []})
        self.assertEqual(code, 200)
        self.assertEqual(body["pins"], [])
        self.assertIsNone(self.get("/api/world")[1]["world"]["stations"][0]["pinned"])

    def test_pins_must_be_a_list_of_well_formed_repos(self):
        for value in ("acme/thing", None, 1, {"a": 1}, True):
            code, body = self.post("/api/pins", {"pins": value})
            self.assertEqual(code, 400, repr(value))
            self.assertIn("error", body)
        for bad in ([""], ["nope"], ["a/b/c"], [1], ["acme/thing", "../../etc/passwd"]):
            code, body = self.post("/api/pins", {"pins": bad})
            self.assertEqual(code, 400, repr(bad))
            self.assertEqual(body["error"], "bad repo")
        self.assertEqual(self.get("/api/pins")[1]["pins"], [], "and wrote nothing")

    # ── refusals ────────────────────────────────────────────────────────────
    def test_an_unknown_kind_is_refused_before_anything_runs(self):
        code, body = self.post("/api/decision", {"kind": "delete", "repo": "a/b"})
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "unknown kind delete")

    def test_a_permit_without_a_well_formed_id_is_refused(self):
        code, body = self.post("/api/decision",
                               {"kind": "permit", "question_id": "nope", "answer": "allow"})
        self.assertEqual(code, 400)
        self.assertIn("question id", body["error"])

    def test_a_permit_for_a_question_that_moved_on_is_409_and_writes_nothing(self):
        """The sharpest edge in the project. Between a gate being shown and being
        answered, the agent can time out and a different gate can open; answering
        by position would approve a command nobody ever saw."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "_meta" / "state").mkdir(parents=True)
            gate = root / "_meta" / "state" / "pending-question.json"
            gate.write_text(json.dumps({
                "id": "a" * 16, "permission": "Bash", "target": "rm -rf /",
                "asked_at": time.time(),
            }))
            before = gate.read_bytes()

            old = os.environ.get("OFFICE_RUNTIME_ROOT")
            os.environ["OFFICE_RUNTIME_ROOT"] = str(root)
            try:
                code, body = self.post("/api/decision", {
                    "kind": "permit", "question_id": "b" * 16, "answer": "allow",
                })
            finally:
                if old is None:
                    os.environ.pop("OFFICE_RUNTIME_ROOT", None)
                else:
                    os.environ["OFFICE_RUNTIME_ROOT"] = old

            self.assertEqual(code, 409)
            self.assertFalse(body["ok"])
            self.assertIn("moved on", body["result"])
            self.assertEqual(gate.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()


class DoorTest(ServeTest):
    """The bind address keeps the network out; these keep the browser out."""

    def raw(self, method, path, headers=None, body=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"host": f"127.0.0.1:{self.port}", "content-type": "application/json"}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, data

    def test_a_request_for_another_host_is_refused(self):
        """DNS rebinding: an attacker's name resolving to 127.0.0.1 must read nothing."""
        code, body = self.raw("GET", "/api/world", {"host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertNotIn("world", body)

    def test_a_cross_site_write_is_refused(self):
        code, _ = self.raw("POST", "/api/gate", {"sec-fetch-site": "cross-site"},
                           {"question_id": "deadbeefcafe", "answer": "allow"})
        self.assertEqual(code, 403)

    def test_a_write_from_another_origin_is_refused(self):
        code, _ = self.raw("POST", "/api/decision", {"origin": "https://evil.example.com"},
                           {"kind": "comment", "repo": "a/b", "issue": 1, "body": "x"})
        self.assertEqual(code, 403)

    def test_a_form_post_is_refused(self):
        """text/plain needs no preflight, so a plain HTML form could reach here."""
        code, _ = self.raw("POST", "/api/gate", {"content-type": "text/plain"},
                           {"question_id": "deadbeefcafe", "answer": "allow"})
        self.assertEqual(code, 403)

    def test_a_same_origin_write_still_works(self):
        code, body = self.raw("POST", "/api/decision",
                              {"origin": f"http://127.0.0.1:{self.port}", "sec-fetch-site": "same-origin"},
                              {"kind": "nope"})
        self.assertEqual(code, 400)  # past the door, refused by validation as before
        self.assertIn("error", body)

    def test_putting_a_desk_away_obeys_the_same_door_as_every_other_write(self):
        """It is a write. It writes a file this server reads on every build, so
        a page you happen to have open must not be able to empty your office."""
        away = {"repo": "acme/thing", "hidden": True}
        for headers in ({"content-type": "text/plain"},
                        {"sec-fetch-site": "cross-site"},
                        {"origin": "https://evil.example.com"}):
            code, _ = self.raw("POST", "/api/desks", headers, away)
            self.assertEqual(code, 403, headers)
        code, _ = self.raw("POST", "/api/desks", {"host": "evil.example.com"}, away)
        self.assertEqual(code, 403)
        self.assertEqual(self.get("/api/desks")[1]["hidden"], [], "and wrote nothing")

        code, body = self.raw("POST", "/api/desks",
                              {"origin": f"http://127.0.0.1:{self.port}",
                               "sec-fetch-site": "same-origin"}, away)
        self.assertEqual(code, 200)
        self.assertEqual(body["hidden"], ["acme/thing"])
        self.raw("POST", "/api/desks", {}, {"repo": "acme/thing", "hidden": False})

    def test_pinning_obeys_the_same_door_as_putting_away(self):
        pins = {"pins": ["acme/thing"]}
        for headers in ({"content-type": "text/plain"},
                        {"sec-fetch-site": "cross-site"},
                        {"origin": "https://evil.example.com"}):
            code, _ = self.raw("POST", "/api/pins", headers, pins)
            self.assertEqual(code, 403, headers)
        code, _ = self.raw("POST", "/api/pins", {"host": "evil.example.com"}, pins)
        self.assertEqual(code, 403)
        code, body = self.raw("GET", "/api/pins", {"host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertNotIn("pins", body)
        self.assertEqual(self.get("/api/pins")[1]["pins"], [], "and wrote nothing")

        code, body = self.raw("POST", "/api/pins",
                              {"origin": f"http://127.0.0.1:{self.port}",
                               "sec-fetch-site": "same-origin"}, pins)
        self.assertEqual(code, 200)
        self.assertEqual(body["pins"], ["acme/thing"])
        self.raw("POST", "/api/pins", {}, {"pins": []})

    def test_reading_the_desk_list_from_another_host_is_refused(self):
        code, body = self.raw("GET", "/api/desks", {"host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertNotIn("hidden", body)

    # ── M2: the phone over Tailscale ────────────────────────────────────────
    # Tailscale Serve stamps Tailscale-User-Login on tailnet traffic, including
    # shared-device users. Loopback is the Mac app and has no such header; a
    # forged one there must not become a login.

    TAILNET = "arias-macbook-pro-2.tail4f309a.ts.net"

    def _as_tailnet(self, login="aria"):
        was_hosts, was_login = set(self.serve.TRUSTED_HOSTS), self.serve.LOGIN
        self.serve.TRUSTED_HOSTS = was_hosts | {self.TAILNET}
        self.serve.LOGIN = login
        return was_hosts, was_login

    def test_a_tailnet_request_with_the_right_login_is_let_through(self):
        was_hosts, was_login = self._as_tailnet()
        try:
            code, body = self.raw("GET", "/api/health", {
                "host": self.TAILNET, "tailscale-user-login": "aria"})
            self.assertEqual(code, 200)
            self.assertTrue(body["ok"])
        finally:
            self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was_hosts, was_login

    def test_a_tailnet_request_with_the_wrong_login_is_refused(self):
        was_hosts, was_login = self._as_tailnet()
        try:
            code, body = self.raw("GET", "/api/health", {
                "host": self.TAILNET, "tailscale-user-login": "tim"})
            self.assertEqual(code, 403)
            self.assertNotIn("ok", body)
        finally:
            self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was_hosts, was_login

    def test_a_tailnet_request_without_a_login_is_refused(self):
        was_hosts, was_login = self._as_tailnet()
        try:
            code, body = self.raw("GET", "/api/health", {"host": self.TAILNET})
            self.assertEqual(code, 403)
            self.assertEqual(body.get("error"), "not you")
        finally:
            self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was_hosts, was_login

    def test_a_forged_login_header_on_loopback_is_ignored(self):
        code, body = self.raw("GET", "/api/health",
                              {"tailscale-user-login": "not-aria"})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_a_tailnet_write_without_a_login_is_refused(self):
        was_hosts, was_login = self._as_tailnet()
        try:
            code, _ = self.raw("POST", "/api/desks", {"host": self.TAILNET},
                               {"repo": "acme/thing", "hidden": True})
            self.assertEqual(code, 403)
            self.assertEqual(self.get("/api/desks")[1]["hidden"], [])
        finally:
            self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was_hosts, was_login

    def test_validation_matches_the_worker_not_python(self):
        v = self.serve.validate
        self.assertIsNotNone(v({"kind": "comment", "repo": "a/b\n", "issue": 7, "body": "x"})[0])
        self.assertIsNotNone(v({"kind": "merge", "repo": "a/b", "pr": "7\n"})[0])
        self.assertIsNotNone(v({"kind": "merge", "repo": "a/b", "pr": "١٢"})[0])
        self.assertIsNone(v({"kind": "merge", "repo": "a/b", "pr": "7"})[0])


# ── the chatroom ────────────────────────────────────────────────────────────
# Four bots you talk to like colleagues. Two things here are worth a test more
# than the happy path is. The roster has to survive the harness being closed,
# because a chatroom whose desks vanish when a dev server stops is a chatroom
# nobody trusts. And a turn takes a minute, so the office must answer at once
# and refuse a second turn for the same bot rather than let two agents write
# one transcript.


def free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def api_get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        exc.close()
        return exc.code, json.loads(raw or "{}")


def api_post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        exc.close()
        return exc.code, json.loads(raw or "{}")


class FakeHarness:
    """The agent runtime, small enough to hold still.

    The real one runs a whole agent per turn. This one records what it was told,
    can be held open on demand so "busy" is a fact rather than a race, and knows
    exactly two bots so an unknown one still 404s.
    """

    BOTS = ("chief", "inbox")

    def __init__(self):
        import urllib.parse as up
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.lock = threading.Lock()
        self.turns = {b: [] for b in self.BOTS}
        self.received = []
        # The whole body of every turn, not just (bot, message): an attachment
        # is only forwarded correctly if it arrives here byte for byte.
        self.bodies = []
        self.hold = None  # an Event a test sets to keep a turn in flight
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def reply(self, obj, code=200):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                path, _, query = self.path.partition("?")
                bot = (up.parse_qs(query).get("bot") or [""])[0]
                if path == "/api/bots":
                    return self.reply({"bots": [outer.row(b) for b in outer.BOTS]})
                if path == "/api/chat":
                    if bot not in outer.turns:
                        return self.reply({"error": "no such bot"}, 404)
                    with outer.lock:
                        return self.reply({"bot": bot, "turns": list(outer.turns[bot])})
                self.reply({"error": "not found"}, 404)

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n).decode() or "{}")
                if outer.hold is not None:
                    outer.hold.wait(20)
                bot, msg = body.get("bot"), body.get("message")
                if bot not in outer.turns:
                    return self.reply({"error": "no such bot"}, 404)
                with outer.lock:
                    outer.received.append((bot, msg))
                    outer.bodies.append(body)
                    outer.turns[bot].append({"role": "user", "text": msg, "at": "then"})
                    outer.turns[bot].append({"role": "assistant", "text": f"heard {msg}",
                                             "at": "then"})
                self.reply({"ok": True, "bot": bot})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def row(self, bot):
        with self.lock:
            turns = self.turns[bot]
            return {"id": bot, "name": bot.title(), "color": "#111",
                    "last": turns[-1] if turns else None, "busy": False}

    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import serve

        cls.serve = serve
        serve.log = lambda msg: None
        serve.office_sync.Access = lambda: object()
        serve.office_sync.build_snapshot = lambda access: copy.deepcopy(SNAP)

        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        (root / "_meta").mkdir(parents=True)
        # `identity` is the whole persona and must never reach the wire.
        (root / "_meta" / "bots.json").write_text(json.dumps({"bots": [
            {"id": "chief", "name": "Chief", "color": "#8FD3C7",
             "identity": "SECRET-PERSONA-CHIEF"},
            {"id": "inbox", "name": "Inbox", "color": "#B7A8F0",
             "identity": "SECRET-PERSONA-INBOX"},
        ]}))

        cls.env = {k: os.environ.get(k) for k in ("OFFICE_RUNTIME_ROOT", "OFFICE_RUNTIME_URL")}
        os.environ["OFFICE_RUNTIME_ROOT"] = str(root)
        cls.harness = FakeHarness()
        cls.nowhere = f"http://127.0.0.1:{free_port()}"

        cls.world = serve.World()
        cls.world.build()
        cls.httpd = serve.make_server(cls.world, 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.harness.close()
        cls.tmp.cleanup()
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        os.environ["OFFICE_RUNTIME_URL"] = self.harness.url()
        # Every test starts from an empty transcript, so none of them can pass
        # because of what an earlier one happened to say.
        with self.harness.lock:
            self.harness.turns = {b: [] for b in self.harness.BOTS}
            self.harness.received = []
            self.harness.bodies = []

    def tearDown(self):
        self.harness.hold = None
        os.environ["OFFICE_RUNTIME_URL"] = self.harness.url()
        # No test may leave a bot mid-turn for the next one to trip over.
        self.assertTrue(self.until(lambda: not any(
            b["busy"] for b in api_get(self.port, "/api/bots")[1]["bots"])))

    def until(self, fn, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if fn():
                return True
            time.sleep(0.02)
        return False

    def bot(self, bot_id):
        code, body = api_get(self.port, "/api/bots")
        self.assertEqual(code, 200)
        return body, next(b for b in body["bots"] if b["id"] == bot_id)

    # ── the roster ──────────────────────────────────────────────────────────
    def test_the_roster_survives_the_harness_being_closed(self):
        """Four desks with nobody home reads completely differently from an
        empty floor, and only one of the two is the truth."""
        os.environ["OFFICE_RUNTIME_URL"] = self.nowhere
        code, body = api_get(self.port, "/api/bots")
        self.assertEqual(code, 200)
        self.assertEqual(body["runtime"], "down")
        self.assertTrue(body["at"])
        self.assertEqual([b["id"] for b in body["bots"]], ["chief", "inbox"])
        self.assertEqual([b["name"] for b in body["bots"]], ["Chief", "Inbox"])
        for b in body["bots"]:
            self.assertIsNone(b["last"])
            self.assertFalse(b["busy"])

    def test_the_roster_carries_the_last_word_when_the_harness_is_up(self):
        with self.harness.lock:
            self.harness.turns["inbox"] = [{"role": "assistant", "text": "two waiting",
                                            "at": "then"}]
        body, inbox = self.bot("inbox")
        self.assertEqual(body["runtime"], "up")
        self.assertEqual(inbox["last"]["text"], "two waiting")
        # A bot who has said nothing has no last word, rather than someone else's.
        self.assertIsNone(self.bot("chief")[1]["last"])

    def test_a_persona_never_leaves_the_machine(self):
        """`identity` is the script the harness feeds a turn. The office ships
        the name, not the script."""
        for url in (self.harness.url(), self.nowhere):
            os.environ["OFFICE_RUNTIME_URL"] = url
            _, body = api_get(self.port, "/api/bots")
            raw = json.dumps(body)
            self.assertNotIn("identity", raw)
            self.assertNotIn("SECRET-PERSONA", raw)

    # ── the conversation ────────────────────────────────────────────────────
    def test_history_is_proxied_from_the_harness(self):
        with self.harness.lock:
            self.harness.turns["chief"] = [{"role": "user", "text": "morning", "at": "then"}]
        code, body = api_get(self.port, "/api/chat?bot=chief")
        self.assertEqual(code, 200)
        self.assertEqual(body["bot"], "chief")
        self.assertEqual(body["turns"][0]["text"], "morning")

    def test_history_says_the_harness_is_down_rather_than_showing_silence(self):
        os.environ["OFFICE_RUNTIME_URL"] = self.nowhere
        code, body = api_get(self.port, "/api/chat?bot=chief")
        self.assertEqual(code, 503)
        self.assertEqual(body["error"], "the harness is not running")

    def test_a_bad_bot_id_is_refused_before_the_harness_is_touched(self):
        for q in ("", "?bot=", "?bot=../../etc/passwd", "?bot=Chief", "?bot=" + "a" * 33):
            code, body = api_get(self.port, "/api/chat" + q)
            self.assertEqual(code, 400, q)
            self.assertIn("error", body)

    def test_a_message_reaches_the_bot_and_becomes_its_last_word(self):
        code, body = api_post(self.port, "/api/chat", {"bot": "chief", "message": "status?"})
        self.assertEqual(code, 202)  # answered before the turn has run
        self.assertEqual(body, {"ok": True, "bot": "chief"})
        self.assertTrue(self.until(lambda: ("chief", "status?") in self.harness.received))
        self.assertTrue(self.until(lambda: self.bot("chief")[1]["last"] is not None))
        self.assertEqual(self.bot("chief")[1]["last"]["text"], "heard status?")

    def test_a_second_message_while_the_bot_is_busy_is_refused(self):
        """Two agents writing one session file is a corrupted transcript, not a
        fast conversation."""
        self.harness.hold = threading.Event()
        code, _ = api_post(self.port, "/api/chat", {"bot": "inbox", "message": "one"})
        self.assertEqual(code, 202)
        self.assertTrue(self.bot("inbox")[1]["busy"])

        code, body = api_post(self.port, "/api/chat", {"bot": "inbox", "message": "two"})
        self.assertEqual(code, 409)
        self.assertEqual(body["error"], "busy")
        # A different bot is not blocked by this one.
        code, _ = api_post(self.port, "/api/chat", {"bot": "chief", "message": "meanwhile"})
        self.assertEqual(code, 202)

        self.harness.hold.set()
        self.assertTrue(self.until(lambda: not self.bot("inbox")[1]["busy"]))
        self.assertNotIn(("inbox", "two"), self.harness.received)

    def test_an_empty_or_oversized_message_is_refused(self):
        for message in ("", "   ", "x" * 8001):
            code, body = api_post(self.port, "/api/chat",
                                  {"bot": "chief", "message": message})
            self.assertEqual(code, 400, repr(message[:12]))
            self.assertIn("error", body)
        code, _ = api_post(self.port, "/api/chat", {"bot": "chief", "message": "x" * 8000})
        self.assertEqual(code, 202)

    def test_a_message_to_a_bot_nobody_has_is_refused_at_once(self):
        code, body = api_post(self.port, "/api/chat", {"bot": "nope", "message": "hi"})
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], "no such bot")
        code, body = api_post(self.port, "/api/chat", {"bot": "NOPE!", "message": "hi"})
        self.assertEqual(code, 400)

    def test_a_turn_that_failed_is_still_on_the_desk_afterwards(self):
        """The bot is free again, and the room still says the last try broke.
        A failure that clears itself on the next poll is one nobody ever sees."""
        os.environ["OFFICE_RUNTIME_URL"] = self.nowhere
        code, _ = api_post(self.port, "/api/chat", {"bot": "chief", "message": "into the void"})
        self.assertEqual(code, 202)
        os.environ["OFFICE_RUNTIME_URL"] = self.harness.url()
        self.assertTrue(self.until(lambda: "error" in self.bot("chief")[1]))
        self.assertFalse(self.bot("chief")[1]["busy"])

        # And it is gone once the bot manages a whole turn.
        code, _ = api_post(self.port, "/api/chat", {"bot": "chief", "message": "again"})
        self.assertEqual(code, 202)
        self.assertTrue(self.until(lambda: "error" not in self.bot("chief")[1]))

    # ── a turn that carries a picture ───────────────────────────────────────
    # The office is a courier here. It checks that there is one attachment, that
    # it says it is a PNG or a JPEG, and that the whole body fits; then it hands
    # the thing over untouched. What the bytes actually are is the harness's
    # question, and opening the parcel would only add a place to be wrong.

    def shot(self, **over):
        item = {"name": "screen.png", "mime_type": "image/png",
                "data_base64": "iVBORw0KGgo="}
        item.update(over)
        return item

    def last_body(self, bot):
        self.assertTrue(self.until(lambda: any(b.get("bot") == bot
                                               for b in self.harness.bodies)))
        with self.harness.lock:
            return [b for b in self.harness.bodies if b.get("bot") == bot][-1]

    def test_a_turn_without_a_picture_is_the_same_turn_it_always_was(self):
        """The Mac app sends no attachments today. Nothing it sends may grow a
        new key on the wire, or the harness sees a shape it never agreed to."""
        code, _ = api_post(self.port, "/api/chat", {"bot": "chief", "message": "morning"})
        self.assertEqual(code, 202)
        self.assertEqual(self.last_body("chief"), {"bot": "chief", "message": "morning"})

    def test_an_attachment_reaches_the_harness_exactly_as_it_was_sent(self):
        shot = self.shot(name="desk.png", data_base64="QUJD")
        code, _ = api_post(self.port, "/api/chat",
                           {"bot": "chief", "message": "look", "attachments": [shot]})
        self.assertEqual(code, 202)
        self.assertEqual(self.last_body("chief"),
                         {"bot": "chief", "message": "look", "attachments": [shot]})

    def test_a_jpeg_is_a_picture_too(self):
        shot = self.shot(name="desk.jpg", mime_type="image/jpeg")
        code, _ = api_post(self.port, "/api/chat",
                           {"bot": "chief", "message": "look", "attachments": [shot]})
        self.assertEqual(code, 202)
        self.assertEqual(self.last_body("chief")["attachments"], [shot])

    def test_a_picture_with_no_words_is_a_message(self):
        shot = self.shot(name="desk.png", data_base64="QUJD")
        code, _ = api_post(self.port, "/api/chat",
                           {"bot": "chief", "message": "", "attachments": [shot]})
        self.assertEqual(code, 202)
        self.assertEqual(self.last_body("chief")["attachments"], [shot])
        code, body = api_post(self.port, "/api/chat", {"bot": "inbox", "message": ""})
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "a message is required")

    def test_an_empty_attachment_list_is_a_turn_with_no_picture(self):
        code, _ = api_post(self.port, "/api/chat",
                           {"bot": "chief", "message": "hi", "attachments": []})
        self.assertEqual(code, 202)
        self.assertNotIn("attachments", self.last_body("chief"))

    def test_a_second_attachment_is_refused_rather_than_quietly_dropped(self):
        """A trimmed attachment is a turn about a screenshot nobody sent."""
        code, body = api_post(self.port, "/api/chat", {
            "bot": "chief", "message": "look", "attachments": [self.shot(), self.shot()]})
        self.assertEqual(code, 400)
        self.assertIn("at most", body["error"])
        self.assertNotIn(("chief", "look"), self.harness.received)

    def test_an_attachment_that_is_not_a_png_or_a_jpeg_is_refused(self):
        for mime in ("image/gif", "application/pdf", "text/html", "image/PNG", ""):
            code, body = api_post(self.port, "/api/chat", {
                "bot": "chief", "message": "look",
                "attachments": [self.shot(mime_type=mime)]})
            self.assertEqual(code, 400, mime)
            self.assertIn("error", body)

    def test_an_attachment_missing_a_field_is_refused(self):
        broken = [{"name": "a.png", "mime_type": "image/png"},   # no bytes
                  {"mime_type": "image/png", "data_base64": "QUJD"},  # no name
                  {"name": "a.png", "data_base64": "QUJD"},      # no type
                  {"name": "", "mime_type": "image/png", "data_base64": "QUJD"},
                  {"name": "a.png", "mime_type": "image/png", "data_base64": ""},
                  "not-an-object", 7, None]
        for item in broken:
            code, body = api_post(self.port, "/api/chat", {
                "bot": "chief", "message": "look", "attachments": [item]})
            self.assertEqual(code, 400, repr(item))
            self.assertIn("error", body)

    def test_attachments_that_are_not_a_list_are_refused(self):
        for value in ("a.png", {"name": "a.png"}, 3):
            code, body = api_post(self.port, "/api/chat", {
                "bot": "chief", "message": "look", "attachments": value})
            self.assertEqual(code, 400, repr(value))
            self.assertIn("error", body)

    def test_a_picture_sized_body_gets_through_and_a_bigger_one_does_not(self):
        """512 KB is the ceiling for a chat turn and only for a chat turn."""
        code, _ = api_post(self.port, "/api/chat", {
            "bot": "chief", "message": "look",
            "attachments": [self.shot(data_base64="A" * (400 * 1024))]})
        self.assertEqual(code, 202)

        code, body = api_post(self.port, "/api/chat", {
            "bot": "inbox", "message": "look",
            "attachments": [self.shot(data_base64="A" * (600 * 1024))]})
        self.assertEqual(code, 400)
        self.assertIn("too large", body["error"])
        self.assertNotIn(("inbox", "look"), self.harness.received)

    def test_every_other_write_keeps_the_smaller_ceiling(self):
        """The picture ceiling belongs to the chat route alone. A half-megabyte
        permit is a mistake, not a photo, and is still refused as one."""
        code, body = api_post(self.port, "/api/decision", {
            "kind": "comment", "repo": "acme/thing", "issue": "1",
            "body": "x" * (400 * 1024)})
        self.assertEqual(code, 400)
        self.assertIn("too large", body["error"])


class GatesTest(unittest.TestCase):
    """`/api/gates`: the whole floor, not just the hand at the front of it.

    The harness gives every asking bot its own gate file, because two bots on two
    threads can raise a hand in the same second and one shared file meant the
    second write erased the first. So the office reads the whole directory, and
    a hand it cannot see is a hand nobody will ever answer.
    """

    @classmethod
    def setUpClass(cls):
        import serve

        cls.serve = serve
        serve.log = lambda msg: None
        serve.office_sync.Access = lambda: object()
        serve.office_sync.build_snapshot = lambda access: copy.deepcopy(SNAP)

        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.tmp.name)
        cls.state = cls.root / "_meta" / "state"
        cls.state.mkdir(parents=True)
        cls.was = os.environ.get("OFFICE_RUNTIME_ROOT")
        os.environ["OFFICE_RUNTIME_ROOT"] = str(cls.root)

        cls.world = serve.World()
        cls.world.build()
        cls.httpd = serve.make_server(cls.world, 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls.was is None:
            os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        else:
            os.environ["OFFICE_RUNTIME_ROOT"] = cls.was
        cls.tmp.cleanup()

    def setUp(self):
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        for path in self.state.glob("pending-question*"):
            path.unlink()

    def file(self, bot=""):
        name = "pending-question.json" if not bot else f"pending-question.{bot}.json"
        return self.state / name

    def ask(self, qid, bot="", target="npm ci", ago=30):
        self.file(bot).write_text(json.dumps({
            "id": qid, "permission": "Bash", "target": target,
            "detail": "", "asked_at": time.time() - ago,
        }))
        return qid

    def test_an_empty_floor_is_an_empty_list_and_nothing_is_wrong(self):
        code, body = api_get(self.port, "/api/gates")
        self.assertEqual(code, 200)
        self.assertEqual(body["gates"], [])
        self.assertTrue(body["at"])
        self.assertNotIn("state", body)  # `state` appears only when something IS wrong

    def test_every_raised_hand_is_listed_oldest_first_with_its_bot(self):
        self.ask("a" * 16, bot="chief", ago=20)
        self.ask("b" * 16, ago=900)
        code, body = api_get(self.port, "/api/gates")
        self.assertEqual(code, 200)
        self.assertEqual([g["id"] for g in body["gates"]], ["b" * 16, "a" * 16])
        self.assertEqual([g["bot"] for g in body["gates"]], [None, "chief"])
        for gate in body["gates"]:
            self.assertEqual(gate["state"], "pending")
            self.assertEqual(gate["target"], "npm ci")
            self.assertIsNotNone(gate["waiting_s"])

    def test_the_single_gate_is_the_first_of_the_list_in_the_old_shape(self):
        """Nothing that reads `/api/gate` today may learn a new shape to keep
        working, and the two endpoints may never disagree about the front hand."""
        self.ask("a" * 16, bot="chief", ago=20)
        self.ask("b" * 16, ago=900)
        one = api_get(self.port, "/api/gate")[1]
        many = api_get(self.port, "/api/gates")[1]["gates"]
        self.assertEqual(one["state"], "pending")
        self.assertEqual(one["id"], many[0]["id"])
        self.assertEqual(sorted(one), sorted(many[0]))

    def test_answering_one_gate_leaves_the_other_one_listed(self):
        """THE test for this endpoint. Two bots blocked, one answered: the other
        hand is still up, still listed, and its file was never touched."""
        chief = self.ask("a" * 16, bot="chief", ago=60)
        release = self.ask("b" * 16, bot="release", ago=30)
        untouched = self.file("release").read_bytes()

        code, body = api_post(self.port, "/api/gate",
                              {"question_id": chief, "answer": "allow"})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(json.loads(self.file("chief").read_text())["answer"], "allow")
        self.assertEqual(self.file("release").read_bytes(), untouched)

        listed = api_get(self.port, "/api/gates")[1]["gates"]
        self.assertEqual([g["id"] for g in listed], [release])
        self.assertEqual(api_get(self.port, "/api/gate")[1]["id"], release)

    def test_answering_an_id_nobody_carries_is_409_and_writes_nothing(self):
        self.ask("a" * 16, bot="chief")
        before = self.file("chief").read_bytes()
        code, body = api_post(self.port, "/api/gate",
                              {"question_id": "c" * 16, "answer": "allow"})
        self.assertEqual(code, 409)
        self.assertFalse(body["ok"])
        self.assertEqual(self.file("chief").read_bytes(), before)
        self.assertEqual(len(api_get(self.port, "/api/gates")[1]["gates"]), 1)

    def test_an_unconfigured_runtime_says_so_rather_than_showing_a_clear_floor(self):
        """An empty list and a broken channel must never render the same. The
        word is the same one `/api/gate` uses, so the two cannot tell different
        stories about whether the gate channel works at all."""
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        gates = api_get(self.port, "/api/gates")[1]
        one = api_get(self.port, "/api/gate")[1]
        self.assertEqual(gates["gates"], [])
        self.assertEqual(gates["state"], "unconfigured")
        self.assertEqual(gates["state"], one["state"])

    def test_the_floor_is_listed_with_the_harness_closed(self):
        """Why this reads files and not the harness's HTTP.

        The gate is the channel that matters, so it is the one that must not
        depend on a dev server being up: a hand raised by an agent that is still
        standing there blocked has to be visible, and answerable, with nothing
        else running. Answering writes the file too, so a list read over the wire
        would be a list of gates that could not be answered.
        """
        self.ask("a" * 16, bot="chief")
        was = os.environ.get("OFFICE_RUNTIME_URL")
        os.environ["OFFICE_RUNTIME_URL"] = f"http://127.0.0.1:{free_port()}"
        try:
            body = api_get(self.port, "/api/gates")[1]
            self.assertEqual([g["id"] for g in body["gates"]], ["a" * 16])
            self.assertNotIn("state", body)
            code, answered = api_post(self.port, "/api/gate",
                                      {"question_id": "a" * 16, "answer": "deny"})
            self.assertEqual(code, 200, answered)
            self.assertEqual(api_get(self.port, "/api/gates")[1]["gates"], [])
        finally:
            if was is None:
                os.environ.pop("OFFICE_RUNTIME_URL", None)
            else:
                os.environ["OFFICE_RUNTIME_URL"] = was

    def test_a_torn_file_says_unreadable_and_still_lists_what_it_could_read(self):
        self.file().write_text('{"id": "abc", "permis')
        self.ask("a" * 16, bot="chief")
        body = api_get(self.port, "/api/gates")[1]
        self.assertEqual(body["state"], "unreadable")
        self.assertEqual([g["id"] for g in body["gates"]], ["a" * 16])



# ── the one public path ─────────────────────────────────────────────────────
# `POST /webhook` is what Tailscale Funnel puts on the open internet, and it is
# the only route that cannot pass `_identity_ok` (GitHub is not on the tailnet)
# or `_write_ok` (GitHub sends no Origin). Everything below is about the two
# claims that replaces them with: the Host still has to be this door, and the
# bytes still have to be signed.

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import FakeAccess, repo_node  # noqa: E402  (needs the path above)


class FakeTrigger:
    """The Trigger, without the pipeline. Records what it was told."""

    def __init__(self):
        self.seen = []

    def notice(self, ev):
        self.seen.append(ev)

    def queued(self):
        return sorted({e.repo for e in self.seen})


class WebhookTest(unittest.TestCase):
    SECRET = b"a shared secret"
    TAILNET = "hook.tail4f309a.ts.net"

    @classmethod
    def setUpClass(cls):
        import serve
        import webhook

        cls.serve = serve
        cls.webhook = webhook
        serve.log = lambda msg: None
        serve.office_sync.Access = lambda: object()
        serve.office_sync.build_snapshot = lambda access: copy.deepcopy(SNAP)

        cls.state = tempfile.TemporaryDirectory()
        sd = pathlib.Path(cls.state.name)
        cls.was = (serve.office_sync.STATE, serve.office_sync.HIDDEN_FILE,
                   serve.office_sync.DESKS_CACHE, webhook.STATE,
                   webhook.SECRET, serve.OUR_LOGINS)
        serve.office_sync.STATE = sd
        serve.office_sync.HIDDEN_FILE = sd / "hidden.json"
        serve.office_sync.PINS_FILE = sd / "pins.json"
        serve.office_sync.DESKS_CACHE = sd / "desks.json"
        webhook.STATE = sd
        webhook.SECRET = cls.SECRET
        serve.OUR_LOGINS = {"ariaxhan"}

        cls.world = serve.World()
        cls.world.build()
        cls.httpd = serve.make_server(cls.world, 0)
        cls.port = cls.httpd.server_address[1]
        cls.handler = cls.httpd.RequestHandlerClass
        cls.mailbox = cls.handler.mailbox
        # The real Trigger would walk the vault and run a pipeline. It is built
        # and then replaced, so what is under test is the door.
        cls.handler.trigger.cancel()
        cls.trigger = FakeTrigger()
        cls.handler.trigger = cls.trigger
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        (cls.serve.office_sync.STATE, cls.serve.office_sync.HIDDEN_FILE,
         cls.serve.office_sync.DESKS_CACHE, cls.webhook.STATE,
         cls.webhook.SECRET, cls.serve.OUR_LOGINS) = cls.was
        cls.state.cleanup()

    def setUp(self):
        self.trigger.seen.clear()
        self.webhook.SECRET = self.SECRET

    # ── plumbing ────────────────────────────────────────────────────────────
    def deliver(self, body, event="issue_comment", delivery=None, headers=None,
                secret=None, raw=None, sign=True):
        import http.client
        raw = raw if raw is not None else json.dumps(body).encode()
        delivery = delivery or f"d-{time.time_ns()}"
        h = {"host": f"127.0.0.1:{self.port}", "content-type": "application/json",
             "x-github-event": event, "x-github-delivery": delivery,
             "user-agent": "GitHub-Hookshot/abc123"}
        if sign:
            h["x-hub-signature-256"] = self.webhook.sign(secret or self.SECRET, raw)
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/webhook", body=raw, headers=h)
        r = c.getresponse()
        out = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, out, delivery

    def get(self, path, headers=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"host": f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request("GET", path, headers=h)
        r = c.getresponse()
        out = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, out

    def comment(self, login="tim", body="what about the other case?", repo="acme/thing"):
        return {"action": "created",
                "issue": {"number": 42, "title": "the thing", "body": "please"},
                "comment": {"body": body, "user": {"login": login}},
                "repository": {"full_name": repo},
                "sender": {"login": login}}

    # ── the signature is the door ───────────────────────────────────────────
    def test_a_signed_delivery_is_taken_and_queued(self):
        code, body, delivery = self.deliver(self.comment())
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["delivery"], delivery)
        self.assertEqual([e.delivery for e in self.trigger.seen], [delivery])
        row = self.mailbox.last_events(1)[0]
        self.assertEqual((row["delivery"], row["repo"], row["login"]),
                         (delivery, "acme/thing", "tim"))
        self.assertTrue(row["trigger"])

    def test_an_unsigned_delivery_is_refused(self):
        code, body, _ = self.deliver(self.comment(), sign=False)
        self.assertEqual(code, 403)
        self.assertEqual(body["error"], "bad signature")
        self.assertEqual(self.trigger.seen, [])

    def test_a_delivery_signed_with_the_wrong_secret_is_refused(self):
        code, body, _ = self.deliver(self.comment(), secret=b"guessed it")
        self.assertEqual(code, 403)
        self.assertEqual(self.trigger.seen, [])

    def test_a_body_altered_after_signing_is_refused(self):
        """The signature is over the raw bytes, so one changed character is a
        different message even though it parses to a legal payload."""
        raw = json.dumps(self.comment()).encode()
        sig = self.webhook.sign(self.SECRET, raw)
        code, _, _ = self.deliver(None, raw=raw.replace(b"acme", b"evil"), sign=False,
                                  headers={"x-hub-signature-256": sig})
        self.assertEqual(code, 403)

    def test_no_secret_configured_answers_503_and_never_accepts_it(self):
        """Unsigned is never accepted, and 'unconfigured' is not a fallback to
        open. A public path that falls open when nobody set a secret runs your
        pipeline for whoever finds the URL."""
        self.webhook.SECRET = b""
        try:
            code, body, _ = self.deliver(self.comment(), sign=False)
            self.assertEqual(code, 503)
            self.assertEqual(body["error"], "no webhook secret configured")
            self.assertEqual(self.trigger.seen, [])
        finally:
            self.webhook.SECRET = self.SECRET

    def test_a_body_over_a_megabyte_is_refused_before_anything_else(self):
        big = json.dumps({"action": "created", "pad": "x" * (1024 * 1024 + 64)}).encode()
        code, body, _ = self.deliver(None, raw=big)
        self.assertEqual(code, 413)
        self.assertEqual(self.trigger.seen, [])

    def test_a_body_that_is_not_json_is_refused_by_content_type(self):
        code, body, _ = self.deliver(self.comment(), headers={"content-type": "text/plain"})
        self.assertEqual(code, 415)

    def test_a_delivery_with_no_id_is_refused_out_loud(self):
        """GitHub always sends one, and it is the only key a redelivery can be
        told apart by. A poster without one is told what is missing rather than
        quietly deduped against nothing."""
        code, body, _ = self.deliver(self.comment(), headers={"x-github-delivery": ""})
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "no delivery id")

    def test_a_request_for_another_host_is_refused(self):
        code, body, _ = self.deliver(self.comment(), headers={"host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertEqual(body["error"], "wrong host")

    # ── the exemption, and its exact size ───────────────────────────────────
    def test_a_tailnet_host_with_no_login_header_is_still_taken(self):
        """THE one route that must work without a tailnet login. Funnel traffic
        is a stranger by definition; if this 403s, no webhook ever arrives."""
        was_hosts, was_login = set(self.serve.TRUSTED_HOSTS), self.serve.LOGIN
        self.serve.TRUSTED_HOSTS = was_hosts | {self.TAILNET}
        self.serve.LOGIN = "aria"
        try:
            code, body, delivery = self.deliver(self.comment(), headers={"host": self.TAILNET})
            self.assertEqual(code, 200)
            self.assertEqual(body["delivery"], delivery)
            self.assertEqual([e.delivery for e in self.trigger.seen], [delivery])
        finally:
            self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was_hosts, was_login

    def test_a_forged_login_header_buys_nothing_here(self):
        """The signature is the whole check on this path, so a header anyone can
        type must not be able to add or subtract from it."""
        code, _, _ = self.deliver(self.comment(),
                                  headers={"tailscale-user-login": "not-aria"})
        self.assertEqual(code, 200, "a forged login does not break a signed delivery")
        code, _, _ = self.deliver(self.comment(), sign=False,
                                  headers={"tailscale-user-login": "aria"})
        self.assertEqual(code, 403, "and does not rescue an unsigned one")

    def test_the_exemption_is_this_one_route_and_no_other(self):
        """Every other write still meets the full door, tailnet login included."""
        was_hosts, was_login = set(self.serve.TRUSTED_HOSTS), self.serve.LOGIN
        self.serve.TRUSTED_HOSTS = was_hosts | {self.TAILNET}
        self.serve.LOGIN = "aria"
        try:
            import http.client
            for path, payload in (("/api/desks", {"repo": "acme/thing", "hidden": True}),
                                  ("/api/pins", {"pins": []}),
                                  ("/api/gate", {"question_id": "a" * 16, "answer": "allow"})):
                raw = json.dumps(payload).encode()
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                c.request("POST", path, body=raw, headers={
                    "host": self.TAILNET, "content-type": "application/json",
                    "x-hub-signature-256": self.webhook.sign(self.SECRET, raw)})
                r = c.getresponse()
                r.read()
                c.close()
                self.assertEqual(r.status, 403, f"{path} is not exempt, signature or not")
        finally:
            self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was_hosts, was_login

    # ── at least once ───────────────────────────────────────────────────────
    def test_a_redelivery_is_answered_200_and_does_nothing(self):
        """GitHub retries anything that is not a 2xx. A redelivery that ran the
        pipeline again would be a second agent on the same news."""
        code, _, delivery = self.deliver(self.comment())
        self.assertEqual(code, 200)
        self.assertEqual(len(self.trigger.seen), 1)
        code, body, _ = self.deliver(self.comment(), delivery=delivery)
        self.assertEqual(code, 200)
        self.assertTrue(body["duplicate"])
        self.assertEqual(len(self.trigger.seen), 1, "and nothing was queued the second time")

    def test_a_redelivery_of_one_that_was_refused_is_handled_fresh(self):
        """The redeliver button sends the SAME id. A delivery that was refused
        must not be remembered as done, or the one recovery control a person
        has is a no-op exactly when they reach for it."""
        code, _, delivery = self.deliver(self.comment(), sign=False)
        self.assertEqual(code, 403)
        code, body, _ = self.deliver(self.comment(), delivery=delivery)
        self.assertEqual(code, 200)
        self.assertNotIn("duplicate", body)
        self.assertEqual(len(self.trigger.seen), 1)

    def test_a_post_to_the_root_is_a_404_and_never_an_alias(self):
        """Funnel's `--set-path` strips the prefix before proxying, so a mount
        pointed at a target with no path arrives here as `POST /`. A second name
        for the public path is a second thing to keep signed."""
        import http.client
        raw = json.dumps(self.comment()).encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/", body=raw, headers={
            "host": f"127.0.0.1:{self.port}", "content-type": "application/json",
            "x-github-event": "issue_comment", "x-github-delivery": "root-1",
            "x-hub-signature-256": self.webhook.sign(self.SECRET, raw)})
        r = c.getresponse()
        body = json.loads(r.read().decode() or "{}")
        c.close()
        self.assertEqual(r.status, 404)
        self.assertIn("not an alias", body["error"])
        self.assertEqual(self.trigger.seen, [])

    def test_a_ping_says_hello_and_starts_nothing(self):
        code, body, _ = self.deliver({"zen": "hi", "hook_id": 1,
                                      "repository": {"full_name": "acme/thing"}},
                                     event="ping")
        self.assertEqual(code, 200)
        self.assertTrue(body["pong"])
        self.assertEqual(self.trigger.seen, [])

    def test_an_event_nobody_subscribed_this_code_to_is_noted_and_dropped(self):
        code, body, _ = self.deliver({"ref": "refs/heads/main",
                                      "repository": {"full_name": "acme/thing"}},
                                     event="push")
        self.assertEqual(code, 200, "never a retry: the subscription is wide, not broken")
        self.assertEqual(body["ignored"], "push")
        self.assertEqual(self.trigger.seen, [])

    def test_our_own_comment_is_recorded_and_never_queued(self):
        """The feedback loop. The pipeline comments; a comment is a webhook."""
        code, _, delivery = self.deliver(self.comment(login="ariaxhan"))
        self.assertEqual(code, 200)
        self.assertEqual(self.trigger.seen, [])
        row = self.mailbox.last_events(1)[0]
        self.assertEqual(row["delivery"], delivery)
        self.assertFalse(row["trigger"], "seen, and deliberately not acted on")

    # ── what the app and the phone read ─────────────────────────────────────
    def test_api_webhook_reports_the_mailbox(self):
        code, _, delivery = self.deliver(self.comment())
        self.assertEqual(code, 200)
        code, body = self.get("/api/webhook")
        self.assertEqual(code, 200)
        self.assertTrue(body["configured"])
        self.assertGreaterEqual(body["seen"], 1)
        self.assertEqual(body["last"][-1]["delivery"], delivery)
        self.assertIsInstance(body["runs"], list)
        self.assertEqual(body["queued"], ["acme/thing"])

    def test_api_webhook_is_behind_the_normal_door(self):
        """It says what has arrived and from whom. That is this office's
        business, and the exemption is for GitHub's route only."""
        was_hosts, was_login = set(self.serve.TRUSTED_HOSTS), self.serve.LOGIN
        self.serve.TRUSTED_HOSTS = was_hosts | {self.TAILNET}
        self.serve.LOGIN = "aria"
        try:
            code, body = self.get("/api/webhook", {"host": self.TAILNET})
            self.assertEqual(code, 403)
            self.assertEqual(body.get("error"), "not you")
            code, body = self.get("/api/webhook", {"host": self.TAILNET,
                                                   "tailscale-user-login": "aria"})
            self.assertEqual(code, 200)
            self.assertTrue(body["configured"])
        finally:
            self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was_hosts, was_login
        code, body = self.get("/api/webhook", {"host": "evil.example.com"})
        self.assertEqual(code, 403)

    def test_the_webhook_card_rides_in_the_world(self):
        self.deliver(self.comment())
        section = self.serve.office_sync.sections_mod.read_all()["webhook"]
        card = section["card"]
        self.assertEqual(card["title"], "Webhooks")
        self.assertIn("event", card["headline"])

    # ── one desk, not the whole room ────────────────────────────────────────
    def test_refreshing_one_desk_swaps_that_station_and_no_other(self):
        """About two GraphQL points against an hourly budget of five thousand.
        Rebuilding the room instead would make hearing from GitHub cost more
        than not hearing from it."""
        snap = self.world.snapshot
        snap["stations"].append({"repo": "acme/other", "issues": [], "prs": [],
                                 "hidden": False, "pinned": None, "fetched_at": ""})
        asked = []

        def fake_sh(cmd, timeout=45, env=None, check=False):
            # The same fake GitHub tests/test_sync.py uses: the repos ride in as
            # variables, and r0..rN come back in the same order.
            if cmd[:3] != ["gh", "api", "graphql"]:
                raise AssertionError(f"nothing but graphql may run: {cmd}")
            names = dict(kv.split("=", 1) for kv in cmd if "=" in kv and kv[:6] != "query=")
            asked.append(f"{names['o0']}/{names['n0']}")
            return 0, json.dumps({"data": {
                "rateLimit": {"limit": 5000, "cost": 2, "remaining": 4000,
                              "resetAt": "2026-08-27T13:00:00Z"},
                "r0": repo_node()}}), ""

        was_sh, was_access = self.serve.office_sync.sh, self.world._access
        self.serve.office_sync.sh = fake_sh
        self.world._access = FakeAccess()
        try:
            self.assertTrue(self.world.refresh_desk("acme/thing"))
        finally:
            self.serve.office_sync.sh = was_sh
            self.world._access = was_access

        self.assertEqual(asked, ["acme/thing"], "one desk, one query")
        code, body = self.get("/api/world")
        stations = {s["repo"]: s for s in body["world"]["stations"]}
        self.assertEqual([i["number"] for i in stations["acme/thing"]["issues"]], [9, 4])
        self.assertEqual([p["number"] for p in stations["acme/thing"]["prs"]], [11])
        self.assertTrue(stations["acme/thing"]["fetched_at"])
        self.assertIsNone(stations["acme/thing"]["issues_error"])
        self.assertEqual(stations["acme/other"]["issues"], [], "and only that desk moved")
        self.world.build()

    def test_a_desk_that_cannot_be_fetched_keeps_what_it_had(self):
        was_access = self.world._access
        self.world._access = FakeAccess(tokens={"acme/thing": ""})
        try:
            self.assertFalse(self.world.refresh_desk("acme/thing"))
        finally:
            self.world._access = was_access
        self.assertEqual(self.world.snapshot["stations"][0]["issues"], [])

    def test_a_malformed_repo_never_reaches_github(self):
        for repo in ("", "nope", "a/b/c", "../../etc/passwd"):
            self.assertFalse(self.world.refresh_desk(repo), repr(repo))
