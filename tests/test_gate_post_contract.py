"""The real Tradition gate writer must render losslessly in Office."""

import os
import json
import pathlib
import sys
import tempfile
import unittest

HARNESS_SRC = pathlib.Path(__file__).resolve().parents[2] / "the-tradition-harness" / "src"
CLIENT = pathlib.Path(__file__).resolve().parents[1] / "client"
sys.path.insert(0, str(HARNESS_SRC))
sys.path.insert(0, str(CLIENT))

import board  # noqa: E402

if HARNESS_SRC.is_dir():
    from tradition_harness.permissions import PermissionGate  # noqa: E402
else:
    PermissionGate = None


class GatePostContractTests(unittest.TestCase):
    @unittest.skipUnless(HARNESS_SRC.is_dir(), "sibling Tradition harness required")
    def test_real_harness_timeout_post_renders_question_author_and_outcome(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(root)
        self.addCleanup(os.environ.pop, "OFFICE_RUNTIME_ROOT", None)
        gate = PermissionGate(root, timeout=0.03, poll_interval=0.005, bot="north")
        gate.add_rule("bash", "git push*", "ask")
        self.assertFalse(gate.ask(
            "bash", "git push origin main", detail="May I publish this change?"))

        post = board.read_feed(repo="aria")["posts"][0]
        self.assertEqual(post["kind"], "asking")
        self.assertEqual(post["text"], "May I publish this change?")
        self.assertEqual(post["by"], "bot:north")
        self.assertEqual(post["gate_id"], post["id"])
        self.assertEqual(len(post["replies"]), 1)
        self.assertIn("failed closed", post["replies"][0]["text"])
        self.assertFalse(post["replies"][0]["authorizes"])

    def test_legacy_display_alias_cannot_impersonate_the_person(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(root)
        self.addCleanup(os.environ.pop, "OFFICE_RUNTIME_ROOT", None)
        pid = "a" * 32
        parent = root / "_meta" / "board" / "repo" / f"post-{pid}.json"
        parent.parent.mkdir(parents=True)
        parent.write_text(json.dumps({
            "id": pid, "ts": "2026-09-01T07:00:00Z", "account": "repo",
            "kind": "asking", "text": "may I",
        }))
        reply = (root / "_meta" / "board" / "_replies" / pid
                 / ("reply-" + "c" * 32 + ".json"))
        reply.parent.mkdir(parents=True)
        reply.write_text(json.dumps({
            "id": "c" * 32, "ts": "2026-09-01T07:01:00Z", "to": "aria",
            "from": "bot:rogue", "kind": "note", "text": "GO", "reply_to": pid,
        }))

        post = board.read_feed()["posts"][0]

        self.assertEqual(post["replies"][0]["account"], "aria")
        self.assertFalse(post["replies"][0]["authorizes"])
        self.assertFalse(post["answered"])


if __name__ == "__main__":
    unittest.main()
