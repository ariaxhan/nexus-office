import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nexus import receipt as rc  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402


def done():
    return rc.emit("done", ["commit abc1234"], "npm test at abc1234", ["NPMTEST_RC=0", "117 files"])


class Grammar(unittest.TestCase):
    def test_emit_round_trips_through_parse(self):
        self.assertEqual({"outcome": "done", "artifacts": ["commit abc1234"],
                          "verification": "npm test at abc1234", "evidence": ["NPMTEST_RC=0", "117 files"],
                          "next": "none"}, rc.parse("Landed.\n\n" + done()))

    def test_emitter_refuses_what_the_parser_would_reject(self):
        for bad in (dict(outcome="finished"), dict(evidence=[]), dict(artifacts=[]), dict(next="later")):
            args = dict(outcome="done", artifacts=["a"], verification="v", evidence=["e"], next="none") | bad
            with self.subTest(bad), self.assertRaises(rc.ReceiptError):
                rc.emit(**args)

    def test_failed_needs_no_artifact_but_needs_evidence_and_named_next(self):
        self.assertEqual("tower: retry", rc.parse(rc.emit("failed", [], "check", ["exit 1"], "tower: retry"))["next"])

    def test_malformed_blocks_are_rejected(self):
        for text in ("```receipt\noutcome: done\n```",
                     "```receipt\nartifacts:\n- a\noutcome: done\nverification: v\nevidence:\n- e\nnext: none\n```",
                     "```receipt\noutcome: done\nartifacts: a\nverification: v\nevidence:\n- e\nnext: none\n```",
                     done() + "\n" + done()):
            with self.subTest(text), self.assertRaises(rc.ReceiptError):
                rc.parse(text)


class Consumer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.led = Ledger(os.path.join(self.tmp.name, "l.db"))
        self.calls = []

    def tearDown(self):
        self.led.close()
        self.tmp.cleanup()

    def gh(self, argv, **_):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "https://github.com/o/r/issues/1#issuecomment-9\n", "")

    def test_receipt_advances_and_posts_nothing(self):
        state, r = rc.consume(self.led, "o/r", 1, done(), "u", run=self.gh)
        self.assertEqual(("RECEIPT", "done"), (state, r["outcome"]))
        self.assertEqual([], self.calls)
        self.assertEqual(1, len(self.led.events("receipt.accepted", "o/r#1")))

    def test_prose_completion_goes_back_to_claimed_with_rejection_on_the_issue(self):
        state, detail = rc.consume(self.led, "o/r", 1, "Outcome: done. All green, shipped.", "u", run=self.gh)
        self.assertEqual("CLAIMED", state)
        self.assertIn(rc.REJECTION_MARK, self.calls[0][-1])
        self.assertTrue(detail["rejection"].endswith("issuecomment-9"))
        self.assertEqual(1, len(self.led.events("receipt.rejected", "o/r#1")))

    def test_talk_and_rejections_are_not_claims(self):
        self.assertEqual((None, None), rc.consume(self.led, "o/r", 1, "Looking at the ledger now.", run=self.gh))
        self.assertEqual((None, None), rc.consume(self.led, "o/r", 1, rc.rejection("x"), run=self.gh))
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
