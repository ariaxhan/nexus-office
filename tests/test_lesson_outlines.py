"""Outlines are read from the lesson repository; approval runs its CLI and reports the new status."""
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
import lesson_outlines as outlines
import serve

OUTLINE = """status: draft
product: superpowerai
lesson: L017-clubhouse-makeover-1
decision: makeover; approval pending
template: L016 (superpowerai/paid/lesson016/index.html@53ca3157)
lane: 872
drafted: 2026-09-09T05:22:44+00:00

## Template skeleton (copied, do not change)
1. (no heading)

## Outline
# L017

- 미니 퀴즈
- Mini quiz
"""
STUB = """#!/usr/bin/env python3
import re, sys, pathlib, datetime
action, product, lesson = sys.argv[1:4]
pdir = "superkidsai" if product == "superpowerai" else "mommyai"
path = pathlib.Path("proposed/tbs-curriculum") / pdir / lesson / "OUTLINE.md"
by = sys.argv[sys.argv.index("--by") + 1]
path.write_text(re.sub(r"^status:.*$", f"status: approved by {by} 2026-09-09", path.read_text(), count=1, flags=re.M))
print("OUTLINE OK")
"""


class OutlineFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.path = self.write("proposed/tbs-curriculum/superkidsai/L017-clubhouse-makeover-1/OUTLINE.md", OUTLINE)
        self.write(outlines.APPROVE, STUB).chmod(0o755)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class BuildTest(OutlineFixture):
    def test_list_parses_header_and_body(self):
        self.write("proposed/tbs-curriculum/mommyai/L030-email/OUTLINE.md", "status: approved by aria 2026-09-08\nproduct: mommyai\n\n## Outline\n- x")
        self.write("proposed/tbs-curriculum/mommyai/notes/OUTLINE.md", "status: draft")
        data = outlines.build(self.root)
        self.assertEqual(data["state"], "ok")
        self.assertEqual([r["lesson"] for r in data["outlines"]], ["L030-email", "L017-clubhouse-makeover-1"])
        row = data["outlines"][1]
        self.assertEqual((row["product"], row["status"], row["approved"]), ("superpowerai", "draft", False))
        self.assertEqual(row["decision"], "makeover; approval pending")
        self.assertEqual(row["drafted"], "2026-09-09T05:22:44+00:00")
        self.assertTrue(row["body"].startswith("## Template skeleton"))
        self.assertIn("- Mini quiz", row["body"])
        self.assertTrue(data["outlines"][0]["approved"])

    def test_approve_runs_cli_and_reports_status(self):
        code, body = outlines.approve({"product": "superpowerai", "lesson": "L017-clubhouse-makeover-1"}, self.root)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "approved by aria 2026-09-09")
        self.assertIn("status: approved by aria", self.path.read_text())
        self.assertEqual(outlines.approve({"product": "superpowerai", "lesson": "L017-clubhouse-makeover-1"}, self.root)[0], 409)

    def test_a_factory_proposal_is_marked_proposed_and_carries_its_value_sentence(self):
        self.write("proposed/tbs-curriculum/superkidsai/L021-OPTIONS.md",
                   "# L021\ndecision-proposed: robot-pen; free tool, poster in 10 minutes\n"
                   "value: after this, mom/kid can print a poster and she cares because it takes ten minutes\n"
                   "proposed-by: factory 2026-09-09\n")
        self.write("proposed/tbs-curriculum/superkidsai/L021-robot-pen/OUTLINE.md",
                   "status: draft\nproduct: superpowerai\nlesson: L021-robot-pen\n"
                   "decision: robot-pen; free tool, poster in 10 minutes\nsource: proposed\n\n## Outline\n- x\n")
        row = {r["lesson"]: r for r in outlines.build(self.root)["outlines"]}["L021-robot-pen"]
        self.assertTrue(row["proposed"])
        self.assertFalse(row["queued"])
        self.assertEqual(row["decision"], "robot-pen; free tool, poster in 10 minutes")
        self.assertTrue(row["value"].startswith("after this, mom/kid can print a poster"))
        self.assertEqual(row["options"], "L021-OPTIONS.md")

    def test_an_options_decision_outranks_a_stale_proposal_and_an_approved_row_is_queued(self):
        self.write("proposed/tbs-curriculum/superkidsai/L017-OPTIONS.md",
                   "decision: makeover; Aria ruled\ndecision-proposed: old proposal\nvalue: a sentence\n")
        row = {r["lesson"]: r for r in outlines.build(self.root)["outlines"]}["L017-clubhouse-makeover-1"]
        self.assertFalse(row["proposed"])
        self.assertEqual(row["decision"], "makeover; Aria ruled")
        outlines.approve({"product": "superpowerai", "lesson": "L017-clubhouse-makeover-1"}, self.root)
        row = {r["lesson"]: r for r in outlines.build(self.root)["outlines"]}["L017-clubhouse-makeover-1"]
        self.assertTrue(row["queued"] and row["approved"] and not row["proposed"])

    def test_invalid_targets_rejected(self):
        for body in ({"product": "superpowerai", "lesson": "../L017"}, {"product": "nope", "lesson": "L017-x"}, {"product": "mommyai", "lesson": "L017"}):
            with self.assertRaises(ValueError):
                outlines.approve(body, self.root)
        with self.assertRaises(FileNotFoundError):
            outlines.approve({"product": "mommyai", "lesson": "L099-missing"}, self.root)

    def test_failed_cli_is_reported_not_hidden(self):
        self.write(outlines.APPROVE, "#!/usr/bin/env python3\nimport sys; print('FATAL: no', file=sys.stderr); sys.exit(1)\n")
        code, body = outlines.approve({"product": "superpowerai", "lesson": "L017-clubhouse-makeover-1"}, self.root)
        self.assertEqual(code, 500)
        self.assertIn("FATAL", body["error"])
        self.assertEqual(body["status"], "draft")


class RouteTest(OutlineFixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict("os.environ", {"OFFICE_LESSON_ROOT": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        handler = type("OutlineHandler", (serve.Handler,), {"chatroom": Mock(), "world": Mock(snapshot={})})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(self.url + path, data=body, headers=headers or {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def test_list_page_and_approve_roundtrip(self):
        code, raw = self.request("/api/lesson-outlines")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["outlines"][0]["lesson"], "L017-clubhouse-makeover-1")
        for page in ("/outlines", "/outlines.js"):
            self.assertEqual(self.request(page)[0], 200)
        body = json.dumps({"product": "superpowerai", "lesson": "L017-clubhouse-makeover-1"}).encode()
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("/api/lesson-outlines/approve", body, headers | {"Origin": "https://evil.example"})[0], 403)
        code, raw = self.request("/api/lesson-outlines/approve", body, headers)
        self.assertEqual(code, 200, raw)
        self.assertTrue(json.loads(raw)["status"].startswith("approved by aria"))
        self.assertTrue(json.loads(self.request("/api/lesson-outlines")[1])["outlines"][0]["approved"])

    def test_the_list_api_carries_every_field_the_row_shows(self):
        row = json.loads(self.request("/api/lesson-outlines")[1])["outlines"][0]
        for key in ("product", "lesson", "status", "decision", "proposed", "value", "queued"):
            self.assertIn(key, row)

    def test_the_page_renders_the_decision_the_value_and_the_queued_state(self):
        script = (pathlib.Path(__file__).resolve().parents[1] / "client/phone/outlines.js").read_text()
        for token in ("proposed", "queued for the factory", "row.value", "row.decision", "approve-row"):
            self.assertIn(token, script)
