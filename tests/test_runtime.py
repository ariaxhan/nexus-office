"""The permission gate, which is the sharpest edge in this project.

Everything else here can be wrong and cost you a confusing screen. This can be
wrong and authorise a command nobody looked at, so the tests that matter are the
ones about answering the WRONG question, not the right one.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "_meta" / "state").mkdir(parents=True)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        # Import after the env is set: the module reads it lazily, but reloading
        # keeps each test honest about that rather than relying on it.
        import runtime
        import importlib
        self.rt = importlib.reload(runtime)

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.tmp.cleanup()

    @property
    def path(self):
        return self.root / "_meta" / "state" / "pending-question.json"

    def ask(self, qid="abc123def456", target="npx playwright install"):
        self.path.write_text(json.dumps({
            "id": qid, "permission": "run_bash", "target": target,
            "detail": "", "asked_at": time.time() - 12,
        }))
        return qid

    # -- reading ---------------------------------------------------------------

    def test_no_file_is_clear_not_an_error(self):
        self.assertEqual(self.rt.read_gate()["state"], "clear")

    def test_a_pending_question_carries_its_target_verbatim(self):
        self.ask(target="npx playwright install --with-deps chromium")
        gate = self.rt.read_gate()
        self.assertEqual(gate["state"], "pending")
        self.assertEqual(gate["target"], "npx playwright install --with-deps chromium")
        self.assertEqual(gate["permission"], "run_bash")
        self.assertIsNotNone(gate["waiting_s"])

    def test_an_answered_question_is_clear(self):
        self.ask()
        data = json.loads(self.path.read_text())
        data["answer"] = "allow"
        self.path.write_text(json.dumps(data))
        self.assertEqual(self.rt.read_gate()["state"], "clear")

    def test_a_torn_read_is_not_reported_as_clear(self):
        # The runtime writes this file whole but it can be caught mid-write. A
        # half-written gate must never look like the absence of a gate, or the
        # room quietly stops showing that an agent is blocked.
        self.path.write_text('{"id": "abc", "permis')
        self.assertEqual(self.rt.read_gate()["state"], "unreadable")

    def test_an_unconfigured_runtime_says_so_rather_than_looking_clear(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        import importlib, runtime
        rt = importlib.reload(runtime)
        self.assertEqual(rt.read_gate()["state"], "unconfigured")

    # -- answering, which is where the danger is --------------------------------

    def test_answering_the_right_question_works(self):
        qid = self.ask()
        ok, msg = self.rt.answer_gate(self.root, qid, "allow", False)
        self.assertTrue(ok, msg)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["answer"], "allow")
        self.assertFalse(data["always"])

    def test_allow_always_is_recorded_as_always(self):
        qid = self.ask()
        ok, _ = self.rt.answer_gate(self.root, qid, "allow", True)
        self.assertTrue(ok)
        self.assertTrue(json.loads(self.path.read_text())["always"])

    def test_answering_a_DIFFERENT_question_is_refused(self):
        # THE test. Between seeing a gate in a browser and the answer draining,
        # the agent can time out and a new gate can open. Answering by position
        # rather than by id would approve a command nobody ever saw.
        self.ask(qid="1111aaaa2222", target="npm ci")
        ok, msg = self.rt.answer_gate(self.root, "9999ffff8888", "allow", False)
        self.assertFalse(ok)
        self.assertIn("older question", msg)
        self.assertNotIn("answer", json.loads(self.path.read_text()))

    def test_answering_a_question_that_is_gone_is_refused(self):
        ok, msg = self.rt.answer_gate(self.root, "abc123def456", "allow", False)
        self.assertFalse(ok)
        self.assertIn("gone", msg)

    def test_double_answering_is_refused(self):
        qid = self.ask()
        self.assertTrue(self.rt.answer_gate(self.root, qid, "allow", False)[0])
        ok, msg = self.rt.answer_gate(self.root, qid, "deny", False)
        self.assertFalse(ok)
        self.assertIn("already answered", msg)

    def test_a_deny_is_written_as_a_deny(self):
        qid = self.ask()
        ok, _ = self.rt.answer_gate(self.root, qid, "deny", False)
        self.assertTrue(ok)
        self.assertEqual(json.loads(self.path.read_text())["answer"], "deny")


class BoardTest(unittest.TestCase):
    def test_an_unreachable_dashboard_is_down_not_empty(self):
        # "The runtime is not running" and "nothing is happening" must never
        # render the same. A silent zero is the false-green this project exists
        # to kill.
        os.environ["OFFICE_RUNTIME_URL"] = "http://127.0.0.1:59999"
        try:
            import importlib, runtime
            rt = importlib.reload(runtime)
            board = rt.read_board()
            self.assertEqual(board["state"], "down")
            self.assertIn("detail", board)
        finally:
            os.environ.pop("OFFICE_RUNTIME_URL", None)


if __name__ == "__main__":
    unittest.main()
