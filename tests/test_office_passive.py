"""The passive fallback: a second office that can never be a second writer.

The Studio is the one execution owner. A fallback copy started from the same
source starts a GitHub poll, a buzz notifier, a webhook drainer that replays
owed work, a chatroom, and a door that applies decisions. Passive mode must
start none of them, refuse every write and every GET that is not a plain read
of the snapshot it was handed, and say honestly how old that snapshot is.

Nothing here reaches GitHub, a relay, a keychain, an agent or real state. An
audit hook is armed before serve is imported and for every test: HOME, TMPDIR
and OFFICE_STATE are one disposable root, secrets are dropped from the
environment, and it refuses any connect but TCP to 127.0.0.1 (Unix sockets
included), any sqlite open, any subprocess but `git rev-parse HEAD`, any write
outside the root and any read of a credential path. PassiveDoorTest also
replaces every effectful module function with a recorder; RealFlowTest and
SnapshotContractTest run the real startup and routing with nothing replaced.

    python3 -m unittest tests/test_office_passive.py

OFFICE_PASSIVE_SERVE_SOURCE / OFFICE_PASSIVE_CLIENT_DIR load a serve.py from
elsewhere against a client/ directory (the patch artifact's before/after run).
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import re
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.dont_write_bytecode = True
CLIENT = pathlib.Path(os.environ.get("OFFICE_PASSIVE_CLIENT_DIR")
                      or pathlib.Path(__file__).resolve().parents[1] / "client")
SOURCE = pathlib.Path(os.environ.get("OFFICE_PASSIVE_SERVE_SOURCE") or CLIENT / "serve.py")
HOME = tempfile.TemporaryDirectory()
ROOT = pathlib.Path(HOME.name).resolve()
ISO = "%Y-%m-%dT%H:%M:%SZ"
SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|RELAY|WEBHOOK|PROXY|OFFICE_", re.I)
CREDENTIAL_PARTS = {".ssh", ".aws", ".codex", ".claude", ".gnupg", "Keychains", ".netrc", "gh"}
GUARD = {"on": 0, "events": []}


def _refuse(why, *detail):
    GUARD["events"].append((why,) + tuple(str(d) for d in detail))
    raise PermissionError(f"test isolation: {why} {detail!r}")


def _inside(p) -> bool:
    return pathlib.Path(os.fsdecode(p)).resolve().is_relative_to(ROOT)


def _audit(event, args):
    if not GUARD["on"]:
        return
    if event in ("socket.connect", "socket.bind"):
        sock, address = args[0], args[1]
        if not (sock.family == socket.AF_INET and address[0] == "127.0.0.1"):
            _refuse(event, sock.family, address)
    elif event == "sqlite3.connect":
        _refuse(event, args[0])
    elif event == "subprocess.Popen":
        argv = list(args[1] or [])
        if not (argv[:1] == ["git"] and argv[-2:] == ["rev-parse", "HEAD"]):
            _refuse(event, argv)
    elif event == "open" and isinstance(args[0], (str, bytes)):
        mode, flags = args[1], args[2] or 0
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        path = pathlib.Path(os.fsdecode(args[0]))
        if writing and not _inside(path):
            _refuse("write", path)
        if writing:
            GUARD["events"].append(("write-in-root", str(path)))
        if CREDENTIAL_PARTS & set(path.parts) or path.name.startswith(".env"):
            _refuse("credential read", path)
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.chmod", "os.symlink"):
        target = args[1] if event == "os.symlink" else args[0]
        if isinstance(target, (str, bytes)) and not _inside(target):
            _refuse(event, target)


sys.addaudithook(_audit)
tempfile.tempdir = str(ROOT)


def isolated_env(**extra):
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env.update({"HOME": str(ROOT), "TMPDIR": str(ROOT), "OFFICE_STATE": "",
                "GIT_OPTIONAL_LOCKS": "0", **extra})
    return patch.dict(os.environ, env, clear=True)


class guarded:
    """Arm the audit guard for a block (nested-safe)."""

    def __enter__(self):
        GUARD["on"] += 1

    def __exit__(self, *exc):
        GUARD["on"] -= 1


def load_serve():
    """serve.py as the module `serve`, with its siblings importable from CLIENT."""
    if not os.environ.get("OFFICE_PASSIVE_SERVE_SOURCE"):
        # In the repo suite another test may have imported serve already; then
        # only the per-test guard applies, not the import.
        with isolated_env(), guarded():
            sys.path.insert(0, str(CLIENT))
            import serve
        serve.log = lambda msg: None
        return serve
    if "serve" in sys.modules and getattr(sys.modules["serve"], "__passive_source__", None) == str(SOURCE):
        return sys.modules["serve"]
    with isolated_env(), guarded():
        sys.path.insert(0, str(CLIENT))
        mod = types.ModuleType("serve")
        mod.__file__ = str(CLIENT / "serve.py")
        mod.__passive_source__ = str(SOURCE)
        sys.modules["serve"] = mod
        exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), mod.__dict__)
    mod.log = lambda msg: None
    return mod


def stamp(age_s: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime(ISO)


def snap(age_s=10, stations=1):
    return {"generated": stamp(age_s), "pins": [], "sections": {},
            "stations": [{"repo": f"acme/r{i}", "issues": [], "prs": []} for i in range(stations)]}


def routes_in(path: pathlib.Path, pattern: str) -> set:
    return set(re.findall(pattern, path.read_text()))


class Recorder:
    """Stands in for anything with an effect, and remembers being reached."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return Recorder.Inert() if self.result is None else self.result

    class Inert:
        def __getattr__(self, name):
            return lambda *a, **kw: {}


class GuardBase(unittest.TestCase):
    """Disposable root, scrubbed environment, audit guard armed; nothing replaced."""

    def setUp(self):
        self.serve = load_serve()
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.state = pathlib.Path(self.tmp.name) / "fallback"
        self.state.mkdir()
        self.effects = {}
        env = isolated_env(OFFICE_STATE=str(self.state))
        env.start()
        self.addCleanup(env.stop)
        GUARD["on"] += 1
        self.addCleanup(lambda: GUARD.__setitem__("on", GUARD["on"] - 1))
        GUARD["events"].clear()

    def refused(self):
        return [e for e in GUARD["events"] if e[0] != "write-in-root"]


class PassiveBase(GuardBase):
    def setUp(self):
        super().setUp()
        s = self.serve
        for owner, name in [
                (s.World, "keep_fresh"), (s.World, "build"), (s.World, "refresh_desk"),
                (s.World, "apply_now"), (s.buzz, "watch"), (s.buzz, "notify"),
                (s.webhook, "Mailbox"), (s.webhook, "Trigger"), (s.chat, "Chatroom"),
                (s.office_api, "get"), (s.office_api, "post"),
                (s.desktops, "get"), (s.desktops, "post"),
                (s.office_sync, "Access"), (s.office_sync, "build_snapshot"),
                (s.office_sync, "apply_decision"), (s.office_sync, "set_hidden"),
                (s.office_sync, "write_pins"), (s.office_sync, "read_hidden"),
                (s.office_sync, "read_pins"), (s.rt, "read_gate"), (s.rt, "read_gates"),
                (s.rt, "answer_gate"), (s.board, "read_feed"), (s.board, "reply"),
                (s.board, "compose"), (s.lesson_status, "build"), (s.lesson_status, "report"),
                (s.lesson_status, "steer"), (s.lesson_outlines, "build"),
                (s.lesson_outlines, "approve"), (s.context, "read"), (s.context, "write"),
                (s.live, "read"), (s.live, "transcript"), (s.sessions, "read"),
                (s.sessions, "transcript"), (s.sessions, "readme"), (s.sessions, "screen"),
                (s.sessions, "say"), (s.sessions, "start"), (s.sessions, "enrolment_failures"),
                (s.search, "run"), (s.tower_incident, "after_snapshot")]:
            # A delegated router that is reached says "not mine", so the live
            # door falls through to its own routes exactly as it would.
            rec = Recorder(False if owner in (s.office_api, s.desktops) else None)
            self.effects[f"{getattr(owner, '__name__', owner)}.{name}"] = rec
            p = patch.object(owner, name, rec)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def reached(self):
        return {k: len(v.calls) for k, v in self.effects.items() if v.calls}

    def write_snapshot(self, body):
        (self.state / "snapshot.json").write_text(json.dumps(body))

    def passive_server(self, body=None):
        if body is not None:
            self.write_snapshot(body)
        world = self.serve.World()
        if body is not None:
            world.load(self.state / "snapshot.json")
        httpd = self.serve.make_server(world, 0, passive=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd

    def call(self, httpd, method, path, body=None, headers=None):
        port = httpd.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"content-type": "application/json", **(headers or {})})
        try:
            with opener.open(req, timeout=5) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw and r.headers.get_content_subtype() == "json" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def run_main(self, argv, env):
        """main() on a thread; returns its result or exception and the server it bound."""
        s, got = self.serve, {}
        real = s.make_server

        def capture(*a, **kw):
            got["httpd"] = real(*a, **kw)
            return got["httpd"]

        def body():
            try:
                got["rc"] = s.main(argv)
            except BaseException as exc:  # noqa: BLE001 - SystemExit is an answer here
                got["rc"] = exc

        with patch.object(s, "make_server", capture), patch.dict(os.environ, env):
            t = threading.Thread(target=body, daemon=True)
            t.start()
            deadline = time.monotonic() + 5
            while "httpd" not in got and "rc" not in got and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)  # long enough for main to start whatever it starts
            if "httpd" in got:
                got["httpd"].shutdown()
            t.join(5)
        return got


class RealBase(PassiveBase):
    """The helpers of PassiveBase with no recorder installed: real modules only."""

    def setUp(self):
        GuardBase.setUp(self)


class StartupTest(PassiveBase):
    def test_readonly_env_starts_no_poll_buzz_webhook_or_chat(self):
        self.write_snapshot(snap())
        got = self.run_main(["--port", "0"], {"OFFICE_READONLY": "1"})
        self.assertIn("httpd", got)
        self.assertEqual(got["httpd"].server_address[0], "127.0.0.1")
        self.assertEqual(self.reached(), {})
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["snapshot.json"])

    def test_readonly_flag_is_the_same_switch(self):
        self.write_snapshot(snap())
        got = self.run_main(["--port", "0", "--readonly"], {})
        self.assertIn("httpd", got)
        self.assertEqual(self.reached(), {})

    def test_readonly_refuses_unset_or_shared_state(self):
        shared = getattr(self.serve, "SHARED_STATE", pathlib.Path(HOME.name) / ".local/state/nexus-office")
        for state in ("", str(shared)):
            with self.subTest(state=state):
                got = self.run_main(["--port", "0"], {"OFFICE_READONLY": "1", "OFFICE_STATE": state})
                self.assertIsInstance(got.get("rc"), SystemExit)
                self.assertNotIn("httpd", got)
                self.assertEqual(self.reached(), {})

    def test_readonly_once_never_builds(self):
        got = self.run_main(["--once"], {"OFFICE_READONLY": "1"})
        self.assertEqual(got.get("rc"), 2)
        self.assertEqual(self.reached(), {})


class PassiveDoorTest(PassiveBase):
    def test_every_get_but_health_and_world_is_off_before_any_module(self):
        own = routes_in(SOURCE, r"'(/api/[A-Za-z0-9_/.-]+)': self\._get_")
        delegated = routes_in(CLIENT / "office_api.py", r"'(/api/[A-Za-z0-9_/.-]+)'")
        desktop = {"/api/desktops", "/api/desktop/local/socket", "/api/desktop/local/credentials"}
        paths = (own | delegated | desktop) - {"/api/health", "/api/world"}
        self.assertGreater(len(own), 20)
        self.assertGreater(len(delegated), 60)
        httpd = self.passive_server(snap())
        for path in sorted(paths):
            with self.subTest(path=path):
                code, body = self.call(httpd, "GET", path + "?id=x&fresh=1")
                self.assertEqual(code, 503)
                self.assertIs(body.get("passive"), True)
        self.assertEqual(self.reached(), {})

    def test_every_post_including_signed_webhook_is_refused(self):
        posts = (routes_in(SOURCE, r"'(/api/[A-Za-z0-9_/.-]+)': (?:self\._|\(|lesson|context)")
                 | routes_in(CLIENT / "office_api.py", r"'(/api/[A-Za-z0-9_/.-]+)'")
                 | {"/api/desktop/local/credentials", "/api/desktop/local/credentials/forget",
                    "/api/decision", "/api/gate", "/api/chat", "/api/session/start",
                    "/api/client-errors", "/webhook", "/"})
        httpd = self.passive_server(snap())
        with patch.object(self.serve.webhook, "SECRET", "test-secret"):
            for path in sorted(posts):
                with self.subTest(path=path):
                    code, body = self.call(httpd, "POST", path,
                                           {"kind": "close", "repo": "acme/r0", "issue": "1"},
                                           {"x-github-delivery": "d1", "x-github-event": "ping"})
                    self.assertEqual(code, 405)
                    self.assertIs(body.get("passive"), True)
        self.assertEqual(self.reached(), {})
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["snapshot.json"])

    def test_world_serves_the_file_and_never_builds(self):
        body = snap()
        httpd = self.passive_server(body)
        code, got = self.call(httpd, "GET", "/api/world?fresh=1")
        self.assertEqual(code, 200)
        self.assertEqual(got["world"], body)
        self.assertEqual((got["passive"], got["fresh"], got["decisions"]), (True, False, []))
        self.assertEqual(got["source"], str(self.state / "snapshot.json"))
        self.assertEqual(self.reached(), {})

    def test_page_is_still_served(self):
        code, _ = self.call(self.passive_server(snap()), "GET", "/")
        self.assertEqual(code, 200)

    def test_server_is_loopback(self):
        self.assertEqual(self.passive_server(snap()).server_address[0], "127.0.0.1")


class PassiveHealthTest(PassiveBase):
    def health(self, body):
        if body == "missing":
            (self.state / "snapshot.json").unlink(missing_ok=True)
            world = self.serve.World()
            world.load(self.state / "snapshot.json")
            httpd = self.serve.make_server(world, 0, passive=True)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
        else:
            httpd = self.passive_server(body)
        return self.call(httpd, "GET", "/api/health")

    def test_fresh_snapshot_is_ok_but_never_authoritative(self):
        code, h = self.health(snap(age_s=30))
        self.assertEqual(code, 200)
        self.assertEqual((h["ok"], h["mode"], h["authoritative"], h["controls"]),
                         (True, "passive", False, "off"))
        self.assertEqual(h["snapshot_source"], str(self.state / "snapshot.json"))
        self.assertLess(abs(h["snapshot_age_s"] - 30), 5)

    def test_missing_empty_stale_or_unstamped_is_not_healthy(self):
        stale = snap(age_s=10 * self.serve.POLL_S)
        unstamped = snap()
        del unstamped["generated"]
        for name, body in (("missing", "missing"), ("empty", snap(stations=0)),
                           ("stale", stale), ("unstamped", unstamped)):
            with self.subTest(name=name):
                code, h = self.health(body)
                self.assertEqual(code, 503)
                self.assertIs(h["ok"], False)
                self.assertEqual(h["mode"], "passive")
        code, h = self.health("missing")
        self.assertIs(h["snapshot_missing"], True)
        self.assertIn("FileNotFoundError", h["snapshot_error"])


class LiveCompatibilityTest(PassiveBase):
    """Without the switch, the door is the same door: writes land, health has no mode."""

    def test_default_server_still_applies_writes(self):
        s = self.serve
        self.effects["office_sync.write_pins"] = rec = Recorder()
        with patch.object(s.office_sync, "write_pins", lambda pins: rec(pins) and list(pins)):
            world = s.World()
            world.snapshot, world.at = snap(), stamp(5)
            httpd = s.make_server(world, 0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            code, body = self.call(httpd, "POST", "/api/pins", {"pins": ["acme/r0"]})
            self.assertEqual((code, body.get("ok")), (200, True))
            self.assertEqual(len(rec.calls), 1)
            code, h = self.call(httpd, "GET", "/api/health")
            self.assertEqual(code, 200)
            self.assertNotIn("mode", h)
        self.assertEqual(len(self.effects["webhook.Mailbox"].calls), 1)
        self.assertEqual(len(self.effects["webhook.Trigger"].calls), 1)


NWO_BAD = "not a repo"
GOOD_STATION = {"repo": "acme/r0", "issues": [], "prs": []}
API_GETS = None


def all_api_gets():
    own = routes_in(SOURCE, r"'(/api/[A-Za-z0-9_/.-]+)': self\._get_")
    delegated = routes_in(CLIENT / "office_api.py", r"'(/api/[A-Za-z0-9_/.-]+)'")
    return own | delegated | {"/api/desktops", "/api/desktop/local/socket",
                              "/api/desktop/local/credentials", "/api/not-a-route-yet"}


class RealFlowTest(RealBase):
    """main() for real: nothing replaced but the line that hands back its server."""

    def test_real_passive_startup_serves_reads_refuses_the_rest_and_starts_nothing(self):
        self.write_snapshot(snap())
        before = set(threading.enumerate())
        seen = {}
        s = self.serve
        real = s.make_server

        def capture(*a, **kw):
            httpd = real(*a, **kw)
            seen["httpd"] = httpd
            return httpd

        def body():
            try:
                seen["rc"] = s.main(["--port", "0"])
            except BaseException as exc:  # noqa: BLE001
                seen["rc"] = exc

        with patch.object(s, "make_server", capture), patch.dict(os.environ, {"OFFICE_READONLY": "1"}):
            runner = threading.Thread(target=body, daemon=True)
            runner.start()
            deadline = time.monotonic() + 5
            while "httpd" not in seen and "rc" not in seen and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn("httpd", seen, seen.get("rc"))
            httpd = seen["httpd"]
            time.sleep(0.2)
            GUARD["events"].clear()
            try:
                self.assertEqual(httpd.server_address[0], "127.0.0.1")
                self.assertEqual(self.call(httpd, "GET", "/")[0], 200)
                self.assertEqual(self.call(httpd, "GET", "/api/health")[0], 200)
                self.assertEqual(self.call(httpd, "GET", "/api/world?fresh=1")[0], 200)
                for path in sorted(all_api_gets() - {"/api/health", "/api/world"}):
                    self.assertEqual(self.call(httpd, "GET", path + "?id=x&fresh=1")[0], 503, path)
                for path in ("/api/pins", "/api/decision", "/api/chat", "/webhook",
                             "/api/tasks/start", "/api/system/command", "/api/desktop/local/credentials"):
                    self.assertEqual(self.call(httpd, "POST", path, {"pins": []})[0], 405, path)
                extra = [t for t in threading.enumerate() if t not in before and t is not runner
                         and "process_request_thread" not in t.name]
                targets = {getattr(getattr(t, "_target", None), "__qualname__", t.name) for t in extra}
                self.assertEqual(targets, set())
            finally:
                httpd.shutdown()
                runner.join(5)
        self.assertEqual(seen.get("rc"), 0)
        self.assertEqual(GUARD["events"], [])  # no write, sqlite, subprocess or foreign connect
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["snapshot.json"])


def _with(**kw):
    body = snap()
    body.update(kw)
    return body


INVALID = {
    "stations-string": _with(stations="oops"), "stations-int": _with(stations=1),
    "stations-null": _with(stations=None), "stations-dict": _with(stations={}),
    "stations-empty": _with(stations=[]), "station-null": _with(stations=[None]),
    "station-empty-dict": _with(stations=[{}]), "station-int": _with(stations=[1]),
    "station-string": _with(stations=["acme/r0"]), "station-list": _with(stations=[[]]),
    "station-bad-repo": _with(stations=[dict(GOOD_STATION, repo=NWO_BAD)]),
    "station-int-repo": _with(stations=[dict(GOOD_STATION, repo=123)]),
    "station-issues-string": _with(stations=[dict(GOOD_STATION, issues="x")]),
    "station-prs-null": _with(stations=[dict(GOOD_STATION, prs=None)]),
    "generated-missing": {k: v for k, v in snap().items() if k != "generated"},
    "generated-null": _with(generated=None), "generated-int": _with(generated=123),
    "generated-empty": _with(generated=""), "generated-garbage": _with(generated="garbage"),
    "generated-offset-zone": _with(generated="2026-10-16T12:00:00+00:00"),
    "generated-naive": _with(generated="2026-10-16T12:00:00"),
    "generated-space": _with(generated="2026-10-16 12:00:00Z"),
    "generated-bad-month": _with(generated="2026-13-01T00:00:00Z"),
    "top-list": [snap()], "top-string": "snapshot",
}
SKEW = 60  # the documented PASSIVE_SKEW_S; asserted against the module below


class SnapshotContractTest(RealBase):
    """Real World.load, real make_server, real routing: invalid input never
    becomes a healthy room and never takes the page down."""

    def serve_file(self, writer):
        path = self.state / "snapshot.json"
        writer(path)
        world = self.serve.World()
        done = threading.Thread(target=world.load, args=(path,), daemon=True)
        done.start()
        done.join(5)
        self.assertFalse(done.is_alive(), "loading the snapshot blocked")
        httpd = self.serve.make_server(world, 0, passive=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd

    def assert_refused_but_page_up(self, httpd, problem=None):
        code, h = self.call(httpd, "GET", "/api/health")
        self.assertEqual(code, 503)
        self.assertEqual((h["ok"], h["mode"], h["authoritative"]), (False, "passive", False))
        self.assertTrue(h.get("problem"), h)
        if problem:
            self.assertIn(problem, h["problem"])
        code, w = self.call(httpd, "GET", "/api/world")
        self.assertEqual((code, w["passive"]), (200, True))
        for page in ("/", "/index.html", "/office.js"):
            self.assertEqual(self.call(httpd, "GET", page)[0], 200, page)
        return h, w

    def test_documented_skew(self):
        self.assertEqual(getattr(self.serve, "PASSIVE_SKEW_S", None), SKEW)

    def test_malformed_snapshots_are_refused_structurally_and_the_page_stays(self):
        for name, body in INVALID.items():
            with self.subTest(name=name):
                httpd = self.serve_file(lambda p: p.write_text(json.dumps(body)))
                h, w = self.assert_refused_but_page_up(httpd)
                self.assertIsNone(w["world"])
                self.assertIs(h["snapshot_missing"], True)
                self.assertTrue(h["snapshot_error"])

    def test_unreadable_files(self):
        cases = {"not-json": lambda p: p.write_text("{"), "empty": lambda p: p.write_text(""),
                 "not-utf8": lambda p: p.write_bytes(b"\xff\xfe{}"),
                 "directory": lambda p: p.mkdir()}
        for name, writer in cases.items():
            with self.subTest(name=name):
                (self.state / "snapshot.json").unlink(missing_ok=True)
                if (self.state / "snapshot.json").is_dir():
                    (self.state / "snapshot.json").rmdir()
                self.assert_refused_but_page_up(self.serve_file(writer))
                p = self.state / "snapshot.json"
                p.rmdir() if p.is_dir() else p.unlink()

    def test_symlink_fifo_and_oversize_are_refused_without_blocking(self):
        outside = pathlib.Path(self.tmp.name) / "elsewhere.json"
        outside.write_text(json.dumps(snap()))
        limit = getattr(self.serve, "PASSIVE_MAX_BYTES", 16 * 1024 * 1024)
        cases = {"symlink": lambda p: p.symlink_to(outside), "fifo": lambda p: os.mkfifo(p),
                 "oversize": lambda p: p.write_bytes(b" " * (limit + 1))}
        for name, writer in cases.items():
            with self.subTest(name=name):
                (self.state / "snapshot.json").unlink(missing_ok=True)
                h, w = self.assert_refused_but_page_up(self.serve_file(writer))
                self.assertIsNone(w["world"])
                (self.state / "snapshot.json").unlink()

    def test_time_edges(self):
        poll = self.serve.POLL_S
        cases = {"future-year": (-365 * 86400, "future"), "future-past-skew": (-(SKEW + 30), "future"),
                 "future-within-skew": (-(SKEW - 30), None), "just-fresh": (2 * poll - 30, None),
                 "just-stale": (2 * poll + 30, "stale")}
        for name, (age, problem) in cases.items():
            with self.subTest(name=name):
                httpd = self.serve_file(lambda p: p.write_text(json.dumps(snap(age_s=age))))
                if problem is None:
                    code, h = self.call(httpd, "GET", "/api/health")
                    self.assertEqual((code, h["ok"], h["problem"]), (200, True, None))
                else:
                    h, w = self.assert_refused_but_page_up(httpd, problem)
                    self.assertIsNotNone(w["world"])  # well formed; only its age is wrong

    def test_valid_production_shaped_snapshot_is_healthy(self):
        body = snap(stations=3)
        body["stations"][0].update(hidden=True, pinned=0, fetched_at=stamp(20), issues_error=None)
        httpd = self.serve_file(lambda p: p.write_text(json.dumps(body)))
        code, h = self.call(httpd, "GET", "/api/health")
        self.assertEqual((code, h["ok"], h["snapshot_stations"]), (200, True, 3))
        self.assertEqual(self.call(httpd, "GET", "/")[0], 200)


_FRESH = [0]


def _strict(raw):
    """Standard JSON only: NaN/Infinity are not JSON and a browser refuses them."""
    def bad(token):
        raise ValueError(f"non-standard JSON constant {token}")
    return json.loads(raw, parse_constant=bad)


class FreshEnvMainTest(RealBase):
    """A new import of serve under the given environment, then real main().

    Nothing is replaced but the line that hands back the bound server, and the
    audit trail from import to shutdown is kept whole, never cleared.
    """

    def fresh(self, **env):
        _FRESH[0] += 1
        with isolated_env(OFFICE_STATE=str(self.state), **env), guarded():
            mod = types.ModuleType(f"serve_fresh_{_FRESH[0]}")
            mod.__file__ = str(CLIENT / "serve.py")
            # The same bytes as the serve under test, wherever they came from.
            source = pathlib.Path(getattr(self.serve, "__passive_source__", None) or SOURCE)
            exec(compile(source.read_text(), str(source), "exec"), mod.__dict__)
        mod.log = lambda msg: None
        return mod

    def main_and_get(self, mod, paths):
        got, raw = {}, {}
        real = mod.make_server

        def capture(*a, **kw):
            got["httpd"] = real(*a, **kw)
            return got["httpd"]

        def body():
            try:
                got["rc"] = mod.main(["--port", "0"])
            except Exception as exc:  # noqa: BLE001 - the failure IS the observation
                import traceback
                got["rc"] = f"{exc!r}\n{traceback.format_exc()}"

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with patch.object(mod, "make_server", capture), patch.dict(os.environ, {"OFFICE_READONLY": "1"}):
            runner = threading.Thread(target=body, daemon=True)
            runner.start()
            deadline = time.monotonic() + 10
            while "httpd" not in got and "rc" not in got and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn("httpd", got, f"no server: {got.get('rc')!r}")
            try:
                port = got["httpd"].server_address[1]
                for path in paths:
                    try:
                        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                            raw[path] = (r.status, r.read())
                    except urllib.error.HTTPError as e:
                        raw[path] = (e.code, e.read())
            finally:
                got["httpd"].shutdown()
                runner.join(5)
        self.assertEqual(got.get("rc"), 0)
        snapshot = str(self.state / "snapshot.json")
        self.assertEqual([e for e in GUARD["events"] if e != ("write-in-root", snapshot)], [])
        return raw

    def test_deeply_nested_snapshot_still_starts_and_reports(self):
        deep = '"extra": ' + "[" * 10000 + "]" * 10000
        body = json.dumps(snap())[:-1] + ", " + deep + "}"
        self.assertLess(len(body), 16 * 1024 * 1024)
        (self.state / "snapshot.json").write_text(body)
        raw = self.main_and_get(self.fresh(), ["/api/health", "/api/world", "/"])
        code, h = raw["/api/health"][0], _strict(raw["/api/health"][1])
        self.assertEqual((code, h["ok"], h["snapshot_missing"]), (503, False, True))
        self.assertIn("deep", h["snapshot_error"])
        w = _strict(raw["/api/world"][1])
        self.assertEqual((raw["/api/world"][0], w["world"]), (200, None))
        self.assertEqual(raw["/"][0], 200)

    def test_nonfinite_or_nonpositive_poll_budget_is_never_healthy(self):
        for value in ("NaN", "nan", "Infinity", "-Infinity", "inf", "0", "-5"):
            with self.subTest(OFFICE_POLL_S=value):
                GUARD["events"].clear()
                self.write_snapshot(snap(age_s=365 * 86400))
                raw = self.main_and_get(self.fresh(OFFICE_POLL_S=value), ["/api/health", "/api/world", "/"])
                code, h = raw["/api/health"][0], _strict(raw["/api/health"][1])
                self.assertEqual((code, h["ok"], h["snapshot_fresh"]), (503, False, False))
                self.assertIn("OFFICE_POLL_S", h["problem"])
                self.assertIsNone(h["max_snapshot_age_s"])
                for v in h.values():
                    if isinstance(v, float):
                        self.assertTrue(math.isfinite(v), h)
                _strict(raw["/api/world"][1])
                self.assertEqual(raw["/"][0], 200)

    def test_finite_budget_boundaries_still_hold(self):
        for age, ok in ((5, True), (15, True), (25, False)):
            with self.subTest(age=age):
                GUARD["events"].clear()
                self.write_snapshot(snap(age_s=age))
                raw = self.main_and_get(self.fresh(OFFICE_POLL_S="10"), ["/api/health"])
                code, h = raw["/api/health"][0], _strict(raw["/api/health"][1])
                self.assertEqual((code, h["ok"], h["max_snapshot_age_s"]), (200 if ok else 503, ok, 20.0))


class NonFiniteNumberTest(FreshEnvMainTest):
    """Real main: a handoff carrying a number standard JSON cannot carry is
    refused before install, so /api/world never re-serves invalid JSON."""

    def run_case(self, text):
        (self.state / "snapshot.json").write_text(text)
        raw = self.main_and_get(self.fresh(), ["/api/health", "/api/world", "/"])
        return {p: (code, body) for p, (code, body) in raw.items()}

    def test_nonfinite_tokens_and_overflow_anywhere_are_refused(self):
        base = json.dumps(snap())[:-1]
        cases = {
            "sections-NaN": ', "sections": {"cost": {"total": NaN}}}',
            "sections-Infinity": ', "sections": {"cost": {"total": Infinity}}}',
            "sections-minus-Infinity": ', "sections": {"cost": {"total": -Infinity}}}',
            "overflow-1e400": ', "sections": {"cost": {"total": 1e400}}}',
            "overflow-minus-1e400": ', "sections": {"cost": {"total": -1e400}}}',
            "station-issue-nested": None,
            "top-level-extra-list": ', "extra": [[1, [2, NaN]]]}',
        }
        for name, tail in cases.items():
            with self.subTest(name=name):
                GUARD["events"].clear()
                if tail is None:
                    text = base.replace('"issues": []', '"issues": [{"number": 1, "score": 1e999}]', 1) + "}"
                else:
                    text = base + tail
                raw = self.run_case(text)
                code, h = raw["/api/health"][0], _strict(raw["/api/health"][1])
                self.assertEqual((code, h["ok"], h["snapshot_missing"]), (503, False, True))
                self.assertTrue(h["snapshot_error"])
                code, w = raw["/api/world"][0], _strict(raw["/api/world"][1])
                self.assertEqual((code, w["world"]), (200, None))
                self.assertEqual(raw["/"][0], 200)

    def test_finite_producer_numbers_still_install_and_serve_strict_json(self):
        body = snap()
        body["sections"] = {"cost": {"total": 12.5, "big": 1e300, "small": -2.5e-300, "n": 7}}
        GUARD["events"].clear()
        raw = self.run_case(json.dumps(body))
        code, h = raw["/api/health"][0], _strict(raw["/api/health"][1])
        self.assertEqual((code, h["ok"]), (200, True))
        w = _strict(raw["/api/world"][1])
        self.assertEqual(w["world"]["sections"], body["sections"])


class LiveFreshnessUnchangedTest(PassiveBase):
    def test_live_health_still_clamps_a_future_stamp(self):
        world = self.serve.World()
        world.snapshot, world.at = snap(), stamp(-3600)
        httpd = self.serve.make_server(world, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        code, h = self.call(httpd, "GET", "/api/health")
        self.assertEqual((code, h["ok"], h["snapshot_age_s"]), (200, True, 0))
        self.assertNotIn("mode", h)


if __name__ == "__main__":
    unittest.main()
