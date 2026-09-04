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

    def do_GET(self):
        bot = self.path.split("bot=", 1)[-1]
        self.reply(200, {"turns": self.turns[bot]})

    def do_POST(self):
        size = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(size))
        bot = body["bot"]
        self.turns[bot].append({"id": f"reply-{bot}", "role": "assistant", "content": "done"})
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


if __name__ == "__main__":
    unittest.main()
