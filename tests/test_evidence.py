"""#210 D5: a flight starts with its own history and the institution's scars, with provenance."""

import os
import subprocess
import unittest
import unittest.mock

from nexus import evidence, work
from tests import test_work


class EvidencePacket(unittest.TestCase):
    github = test_work.WorkTests.github

    def setUp(self):
        test_work.WorkTests.setUp(self)
        work.discover(self.led, self.entry)
        self.task = self.led.tasks()[0]

    def recall(self, ids, rc=0):
        return lambda argv, **k: subprocess.CompletedProcess(argv, rc, "".join(f"{i}\t2\n" for i in ids), "")

    def test_prior_attempts_carry_outcome_reason_and_held_branch(self):
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.led.fail(fid, "owner_exited", "work owner exited", expect="running")
        self.led.add_artifact(fid, "held_branch", "aria/held/" + fid)
        [p] = evidence.prior_attempts(self.led, self.task)
        self.assertEqual(("nexus:flight:" + fid, "failed"), (p["id"], p["state"]))
        self.assertIn("owner exited", p["why"])
        self.assertEqual(["aria/held/" + fid], p["held"])

    def test_packet_is_small_named_and_recorded_without_touching_recall_counts(self):
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.led.fail(fid, "work_failed", "x" * 500, expect="running")
        runs = []
        run = lambda argv, **k: runs.append(argv) or self.recall([])(argv, **k)  # noqa: E731
        current = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        recorded, text = evidence.packet(self.led, self.task, {"title": "Tower flight stuck resolving"}, None,
                                         run=run, flight=current)
        self.assertNotIn(current, text)
        self.assertIn("--scores", runs[0])  # the side-effect-free mode
        self.assertLessEqual(len(text), evidence.LIMIT)
        self.assertIn("nexus:flight:" + fid, text)
        self.assertEqual(["nexus:flight:" + fid], recorded["prior"])

    def test_unresolvable_or_failed_recall_is_recorded_not_raised(self):
        found, error = evidence.scars("dead owner", None, run=self.recall(["missing-id"], rc=1))
        self.assertEqual(([], "recall exit 1"), (found, error))
        def down(argv, **k):
            raise OSError("no agentdb")
        self.assertEqual("recall unavailable: OSError", evidence.scars("dead owner", None, run=down)[1])

    def test_a_hit_that_shares_only_generic_words_is_left_out(self):
        run = lambda argv, **k: subprocess.CompletedProcess(argv, 0, "weak\t2\nstrong\t4\n", "")  # noqa: E731
        with unittest.mock.patch.object(evidence, "_insight", side_effect=lambda dbs, lid: ("gotcha", lid)):
            found, _ = evidence.scars("office selection copy paste", None, run=run)
        self.assertEqual(["agentdb:strong"], [s["id"] for s in found])

    def test_nothing_known_adds_nothing(self):
        recorded, text = evidence.packet(self.led, self.task, {"title": ""}, None, run=self.recall([]))
        self.assertEqual("", text)
        self.assertEqual(0, recorded["chars"])


if __name__ == "__main__":
    unittest.main()
