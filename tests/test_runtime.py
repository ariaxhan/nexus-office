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
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

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

    def gate_file(self, bot=""):
        """Where the harness puts this bot's gate. One file per asking bot: two
        bots run on two threads and can raise a hand in the same second."""
        name = "pending-question.json" if not bot else f"pending-question.{bot}.json"
        return self.root / "_meta" / "state" / name

    def ask(self, qid="abc123def456", target="npx playwright install", bot="", ago=12):
        self.gate_file(bot).write_text(json.dumps({
            "id": qid, "permission": "run_bash", "target": target,
            "detail": "", "asked_at": time.time() - ago,
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

    def test_answering_preserves_a_private_gate_mode_under_umask_022(self):
        qid = self.ask()
        os.chmod(self.path, 0o600)
        previous = os.umask(0o022)
        try:
            ok, msg = self.rt.answer_gate(self.root, qid, "allow", False)
        finally:
            os.umask(previous)
        self.assertTrue(ok, msg)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        lock = self.root / "_meta" / "state" / "pending-question.lock"
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_a_gate_replaced_before_the_commit_point_is_refused(self):
        stale_id = self.ask(qid="1" * 12)
        stale = json.loads(self.path.read_text())
        replacement = stale | {"id": "2" * 12, "target": "rm -rf irreplaceable"}
        self.path.write_text(json.dumps(replacement))

        with mock.patch.object(
            self.rt, "_pending_files", return_value=([(self.path, stale)], False)
        ):
            ok, msg = self.rt.answer_gate(self.root, stale_id, "allow", False)

        self.assertFalse(ok, msg)
        self.assertNotIn("answer", json.loads(self.path.read_text()))
        self.assertEqual(json.loads(self.path.read_text())["id"], "2" * 12)

    def test_answering_refuses_a_symlinked_meta_ancestor(self):
        with tempfile.TemporaryDirectory() as outside_name:
            outside = pathlib.Path(outside_name)
            shutil.rmtree(self.root / "_meta")
            (outside / "state").mkdir()
            external_gate = outside / "state" / "pending-question.json"
            external_gate.write_text(json.dumps({
                "id": "3" * 12, "permission": "run_bash", "target": "npm ci",
                "detail": "", "asked_at": time.time(),
            }))
            before = external_gate.read_bytes()
            (self.root / "_meta").symlink_to(outside, target_is_directory=True)

            ok, msg = self.rt.answer_gate(self.root, "3" * 12, "allow", False)

            self.assertFalse(ok, msg)
            self.assertIn("symlinked component", msg)
            self.assertEqual(external_gate.read_bytes(), before)

    def test_answering_refuses_a_symlinked_gate_file(self):
        with tempfile.TemporaryDirectory() as outside_name:
            external_gate = pathlib.Path(outside_name) / "pending-question.json"
            external_gate.write_text(json.dumps({
                "id": "4" * 12, "permission": "run_bash", "target": "npm ci",
                "detail": "", "asked_at": time.time(),
            }))
            before = external_gate.read_bytes()
            self.path.symlink_to(external_gate)

            ok, msg = self.rt.answer_gate(self.root, "4" * 12, "allow", False)

            self.assertFalse(ok, msg)
            self.assertIn("symlink", msg)
            self.assertEqual(external_gate.read_bytes(), before)
            self.assertTrue(self.path.is_symlink())


class ManyGatesTest(GateTest):
    """One file per asking bot, which is how the harness writes them now.

    The failure this guards is the worst one this project has: a bot raises its
    hand, the office reads the one file it has always read, sees nothing, and
    reports a clear floor while an agent stands there blocked forever.
    """

    # -- the single gate still answers, and now answers about everybody ---------

    def test_a_named_bots_gate_is_not_hidden_by_the_unnamed_file_being_absent(self):
        self.ask(qid="c" * 12, target="git push --force-with-lease", bot="chief")
        gate = self.rt.read_gate()
        self.assertEqual(gate["state"], "pending")
        self.assertEqual(gate["target"], "git push --force-with-lease")
        self.assertEqual(gate["bot"], "chief")

    def test_the_single_gate_is_the_oldest_hand_on_the_floor(self):
        self.ask(qid="1" * 12, ago=5)                      # the unnamed one, recent
        self.ask(qid="2" * 12, bot="release", ago=600)     # a bot, waiting ten minutes
        self.assertEqual(self.rt.read_gate()["id"], "2" * 12)
        self.assertEqual(self.rt.read_gate()["bot"], "release")

    def test_an_answered_hand_is_skipped_for_the_one_still_up(self):
        self.ask(qid="1" * 12, ago=600)
        data = json.loads(self.path.read_text())
        data["answer"] = "allow"
        self.path.write_text(json.dumps(data))
        self.ask(qid="2" * 12, bot="chief", ago=5)
        self.assertEqual(self.rt.read_gate()["id"], "2" * 12)

    # -- the whole floor -------------------------------------------------------

    def test_an_empty_floor_is_an_empty_list_and_says_it_read_cleanly(self):
        self.assertEqual(self.rt.read_gates(), {"state": "ok", "gates": []})

    def test_one_gate_is_a_list_of_one(self):
        self.ask(qid="a" * 12)
        got = self.rt.read_gates()
        self.assertEqual(got["state"], "ok")
        self.assertEqual([g["id"] for g in got["gates"]], ["a" * 12])
        self.assertIsNone(got["gates"][0]["bot"])

    def test_every_raised_hand_is_listed_oldest_first(self):
        self.ask(qid="1" * 12, bot="chief", ago=30)
        self.ask(qid="2" * 12, ago=900)
        self.ask(qid="3" * 12, bot="release", ago=120)
        got = self.rt.read_gates()
        self.assertEqual([g["id"] for g in got["gates"]], ["2" * 12, "3" * 12, "1" * 12])
        self.assertEqual([g["bot"] for g in got["gates"]], [None, "release", "chief"])

    def test_a_listed_gate_is_the_same_object_as_the_single_one(self):
        """One shape, defined once. If the list and the single gate could drift
        apart, the app would render two different truths about one raised hand."""
        self.ask(qid="d" * 12, bot="chief", target="npm ci")
        one, many = self.rt.read_gate(), self.rt.read_gates()["gates"]
        self.assertEqual(len(many), 1)
        self.assertEqual(sorted(one), sorted(many[0]))
        self.assertEqual({k: one[k] for k in one if k != "waiting_s"},
                         {k: many[0][k] for k in many[0] if k != "waiting_s"})

    def test_an_answered_gate_is_not_a_raised_hand(self):
        qid = self.ask(qid="e" * 12, bot="chief")
        self.assertEqual(len(self.rt.read_gates()["gates"]), 1)
        self.assertTrue(self.rt.answer_gate(self.root, qid, "allow", False)[0])
        self.assertEqual(self.rt.read_gates()["gates"], [])

    def test_a_torn_file_never_hides_the_hand_next_to_it(self):
        """A half-written neighbour is a reason to say so out loud, never a
        reason to drop a gate that IS readable."""
        self.path.write_text('{"id": "abc", "permis')
        self.ask(qid="f" * 12, bot="chief")
        got = self.rt.read_gates()
        self.assertEqual(got["state"], "unreadable")
        self.assertEqual([g["id"] for g in got["gates"]], ["f" * 12])

    def test_an_unconfigured_runtime_lists_nothing_and_says_why(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        import importlib, runtime
        got = importlib.reload(runtime).read_gates()
        self.assertEqual(got, {"state": "unconfigured", "gates": []})

    def test_a_root_that_is_not_there_is_not_an_empty_floor(self):
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root / "nope")
        import importlib, runtime
        got = importlib.reload(runtime).read_gates()
        self.assertEqual(got["state"], "missing-root")
        self.assertEqual(got["gates"], [])

    # -- answering, across the whole floor -------------------------------------

    def test_answering_one_bots_gate_leaves_the_other_hand_up(self):
        """THE multi-gate test. Two bots blocked, one answered: the other is
        still listed, still waiting, and its file is untouched."""
        chief = self.ask(qid="1" * 12, bot="chief", ago=60)
        release = self.ask(qid="2" * 12, bot="release", ago=30)
        untouched = self.gate_file("release").read_bytes()

        ok, msg = self.rt.answer_gate(self.root, chief, "allow", False)
        self.assertTrue(ok, msg)
        self.assertEqual(json.loads(self.gate_file("chief").read_text())["answer"], "allow")
        self.assertEqual(self.gate_file("release").read_bytes(), untouched)

        left = self.rt.read_gates()["gates"]
        self.assertEqual([g["id"] for g in left], [release])
        self.assertEqual(self.rt.read_gate()["id"], release)

    def test_an_id_nobody_on_the_floor_carries_writes_to_nobody(self):
        self.ask(qid="1" * 12, bot="chief")
        self.ask(qid="2" * 12)
        before = {b: self.gate_file(b).read_bytes() for b in ("chief", "")}
        ok, msg = self.rt.answer_gate(self.root, "9" * 12, "allow", False)
        self.assertFalse(ok)
        self.assertIn("moved on", msg)
        self.assertEqual({b: self.gate_file(b).read_bytes() for b in ("chief", "")}, before)

    def test_a_gate_that_is_only_torn_is_not_reported_as_gone(self):
        """"Gone" means the agent stopped waiting. A file we could not parse
        means we do not know, and saying the wrong one of those is how an answer
        gets abandoned."""
        self.path.write_text('{"id": "abc", "permis')
        ok, msg = self.rt.answer_gate(self.root, "a" * 12, "allow", False)
        self.assertFalse(ok)
        self.assertIn("could not read", msg)


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
