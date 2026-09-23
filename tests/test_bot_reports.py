from __future__ import annotations

import json
import pathlib
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
import bot_reports  # noqa: E402


class Door(BaseHTTPRequestHandler):
    turns = {bot: [] for bot in bot_reports.BOTS}
    messages = []
    scenario = "success"
    world = {
        "generated": "2026-09-04T08:00:00Z",
        "stations": [{"repo": "owner/repo"}],
        "sections": {"clock": {"state": "ok"}},
    }

    def do_GET(self):
        if self.path.startswith("/api/world"):
            return self.reply(200, {"world": self.world})
        bot = self.path.split("bot=", 1)[-1]
        self.reply(200, {"turns": self.turns[bot]})

    def do_POST(self):
        size = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(size))
        bot = body["bot"]
        self.messages.append(body["message"])
        group = None if self.scenario == "missing-group" else f"group-{bot}"
        self.turns[bot].append({"id": f"user-{bot}", "role": "user", "content": "Daily report." if self.scenario == "wrong-request" else body["message"], "inference_group_id": group})
        if self.scenario != "unrelated-only":
            self.turns[bot].append({"id": f"reply-{bot}", "role": "assistant", "content": "done",
                                    "inference_group_id": group, "ok": self.scenario != "failed"})
        self.turns[bot].append({"id": "unrelated", "role": "assistant", "content": "other",
                                "inference_group_id": "other-group", "ok": True})
        self.reply(202, {"ok": True})

    def reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_):
        pass


class BotReportsTest(unittest.TestCase):
    def setUp(self):
        Door.turns = {bot: [] for bot in bot_reports.BOTS}
        Door.messages = []
        Door.scenario = "success"
        Door.world = {
            "generated": "2026-09-04T08:00:00Z",
            "stations": [{"repo": "owner/repo"}],
            "sections": {"clock": {"state": "ok"}},
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Door)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_every_daily_role_posts_and_receives_a_new_reply(self):
        self.assertEqual(bot_reports.main(["--base", self.base, "--timeout", "1"]), 0)
        self.assertTrue(all(Door.turns[bot] for bot in bot_reports.BOTS))

    def test_one_bot_can_be_run_for_a_focused_retry(self):
        turn = bot_reports.run_report("north", self.base, timeout_s=1)
        self.assertEqual(turn["id"], "reply-north")
        self.assertFalse(Door.turns["relay"])

    def test_empty_evidence_and_unverified_results_fail_closed(self):
        Door.world = {}
        with self.assertRaisesRegex(ValueError, "no completed snapshot"):
            bot_reports.run_report("north", self.base, timeout_s=1)

    def test_the_scheduler_triggers_identity_instead_of_puppeteering_the_voice(self):
        bot_reports.run_report("north", self.base, timeout_s=1)
        self.assertEqual(len(Door.messages), 1)
        self.assertRegex(Door.messages[0], r"^Daily report\.\n\n<!-- office-report:[0-9a-f]{32} -->$")

    def test_failed_matching_inference_is_not_a_success(self):
        Door.scenario = "failed"
        with self.assertRaisesRegex(ValueError, "inference did not succeed"):
            bot_reports.run_report("north", self.base, timeout_s=1)

    def test_missing_group_and_wrong_request_fail_closed(self):
        for scenario in ("missing-group", "wrong-request"):
            with self.subTest(scenario=scenario):
                Door.turns = {bot: [] for bot in bot_reports.BOTS}
                Door.scenario = scenario
                with self.assertRaises(TimeoutError):
                    bot_reports.run_report("north", self.base, timeout_s=0.05)

    def test_unrelated_assistant_does_not_complete_report(self):
        Door.scenario = "unrelated-only"
        with self.assertRaises(TimeoutError):
            bot_reports.run_report("north", self.base, timeout_s=0.05)


if __name__ == "__main__":
    unittest.main()


class LatestTest(unittest.TestCase):
    def test_newest_reply_to_a_daily_report_per_bot(self):
        turns = [{"role": "user", "content": "Daily report.\n\n<!-- office-report:a -->", "inference_group_id": "g1"},
                 {"role": "assistant", "content": "old", "inference_group_id": "g1", "timestamp": "t1", "ok": True},
                 {"role": "user", "content": "hi", "inference_group_id": "g2"},
                 {"role": "assistant", "content": "chat", "inference_group_id": "g2", "timestamp": "t2", "ok": True}]
        rows = bot_reports.latest(lambda bot: (200, {"turns": turns}) if bot == "rune" else (503, {"error": "down"}),
                                  bots=("rune", "sphinx"))
        self.assertEqual((rows[0]["text"], rows[0]["at"]), ("old", "t1"))
        self.assertEqual(rows[1]["error"], "down")

    def test_a_503_is_retried_not_fatal(self):
        import urllib.error
        self.assertTrue(bot_reports.transient(urllib.error.HTTPError("u", 503, "x", {}, None)))
        self.assertFalse(bot_reports.transient(urllib.error.HTTPError("u", 404, "x", {}, None)))
