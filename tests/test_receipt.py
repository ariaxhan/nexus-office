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
        self.assertEqual("RECEIPT", rc.state(self.led, "o/r", 1)["state"])

    def test_prose_completion_goes_back_to_claimed_with_rejection_on_the_issue(self):
        state, detail = rc.consume(self.led, "o/r", 1, "Outcome: done. All green, shipped.", "u", run=self.gh)
        self.assertEqual("CLAIMED", state)
        self.assertIn(rc.REJECTION_MARK, self.calls[0][-1])
        self.assertTrue(detail["rejection"].endswith("issuecomment-9"))
        self.assertEqual("CLAIMED", rc.state(self.led, "o/r", 1)["state"])

    def test_talk_and_rejections_are_not_claims(self):
        self.assertEqual((None, None), rc.consume(self.led, "o/r", 1, "Looking at the ledger now.", run=self.gh))
        self.assertEqual((None, None), rc.consume(self.led, "o/r", 1, rc.rejection("x"), run=self.gh))
        self.assertEqual([], self.calls)

    def test_every_prose_outcome_is_a_claim(self):
        for outcome in rc.OUTCOMES:
            body = f"Outcome: {outcome}. Remaining work needs another owner."
            self.assertEqual("CLAIMED", rc.judge(body)[0], body)

    def test_latest_comment_reads_every_page(self):
        import json
        pages = [[{"body": f"c{i}"} for i in range(100)], [{"body": "newest"}, {"body": rc.rejection("x")}]]
        seen = []

        def gh(argv, **_):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(pages), "")
        self.assertEqual("newest", rc.latest_comment("o/r", 1, run=gh)["body"])
        self.assertIn("--paginate", seen[0])


class Issue:
    """A fake GitHub issue: posts append comments (or fail), reads return them paginated."""

    def __init__(self, comments=(), up=True):
        self.comments, self.up, self.posts = [{"body": c, "html_url": f"u{i}"} for i, c in enumerate(comments)], up, 0

    def __call__(self, argv, **_):
        if argv[1:3] == ["issue", "comment"]:
            if not self.up:
                return subprocess.CompletedProcess(argv, 1, "", "HTTP 502")
            self.posts += 1
            url = f"u{len(self.comments)}"
            self.comments.append({"body": argv[-1], "html_url": url})
            return subprocess.CompletedProcess(argv, 0, url + "\n", "")
        import json
        pages = [self.comments[i:i + 100] for i in range(0, len(self.comments), 100)]
        return subprocess.CompletedProcess(argv, 0, json.dumps(pages), "")


class State(unittest.TestCase):
    """Finding: consumption recorded events but no item state. The state is now what `consume` writes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.led = Ledger(os.path.join(self.tmp.name, "l.db"))

    def tearDown(self):
        self.led.close()
        self.tmp.cleanup()

    def test_receipt_advances_prose_returns_to_claimed_and_is_rejected_once(self):
        issue = Issue([done()])
        rc.check(self.led, "o/r", 1, run=issue)
        self.assertEqual("RECEIPT", rc.state(self.led, "o/r", 1)["state"])
        issue.comments.append({"body": "Actually done now, shipped.", "html_url": "late"})
        rc.check(self.led, "o/r", 1, run=issue)
        rc.check(self.led, "o/r", 1, run=issue)
        self.assertEqual("CLAIMED", rc.state(self.led, "o/r", 1)["state"])
        self.assertEqual(1, issue.posts)  # one rejection per comment, not one per sweep

    def test_newest_comment_past_the_first_page_is_the_one_judged(self):
        issue = Issue(["Outcome: done."] + [f"chat {i}" for i in range(140)] + [done()])
        self.assertEqual("RECEIPT", rc.check(self.led, "o/r", 1, run=issue)[0])

    def test_publish_returns_nothing_unless_the_issue_shows_the_receipt(self):
        self.assertIsNone(rc.publish(self.led, "o/r", 1, done(), run=Issue(up=False)))
        self.assertEqual({}, rc.state(self.led, "o/r", 1))
        self.assertEqual("done", rc.publish(self.led, "o/r", 1, done(), run=Issue())["outcome"])


class TowerGate(unittest.TestCase):
    """Findings: a failed post, its retry, and the reproof path each completed without a receipt on the issue."""

    def setUp(self):
        from nexus import work
        self.work = work
        self.tmp = tempfile.TemporaryDirectory()
        self.led = Ledger(os.path.join(self.tmp.name, "l.db"))
        self.task = {"id": "t1"}
        self.entry, self.issue = {"repo": "o/r", "path": self.tmp.name}, {"number": 1, "state": "open"}
        self.led.event("work.receipt", "t1", {"flight": "f1", "sha": "a" * 40, "receipt": "check passed"}, "work")

    def tearDown(self):
        self.led.close()
        self.tmp.cleanup()

    def close(self, issue):
        from unittest import mock
        with mock.patch("subprocess.run", issue), \
                mock.patch.object(self.work, "close_issue", return_value="closed") as closed, \
                mock.patch.object(self.led, "set_task_state") as done, \
                mock.patch.object(self.work, "cleanup_held"):
            return self.work.close_received(self.led, self.entry, self.task, self.issue,
                                            self.work.latest(self.led, "work.receipt", "t1")), closed.called, done.called

    def test_failed_publication_and_its_retry_never_close_without_the_receipt(self):
        issue = Issue(up=False)
        self.assertEqual(("pending", False, False), self.close(issue))
        self.assertEqual(("pending", False, False), self.close(issue))  # the retry tick: same gate
        issue.up = True
        self.assertEqual(("done", True, True), self.close(issue))
        self.assertIn("commit " + "a" * 40, rc.parse(issue.comments[-1]["body"])["artifacts"])
        self.assertEqual(("done", True, True), self.close(issue))
        self.assertEqual(1, issue.posts)  # already on the issue: not posted again

    def test_reproof_passes_the_same_gate(self):
        from unittest import mock
        self.task = {"id": "t2"}
        with mock.patch.object(self.work, "last_landed", return_value=("f1", {"sha": "b" * 40})), \
                mock.patch.object(self.work, "next_retry", return_value=0), \
                mock.patch.object(self.work, "tower_gate", return_value=(None, None)), \
                mock.patch("nexus.contract.done_receipt", return_value=(True, "reproved")), \
                mock.patch("subprocess.run", Issue(up=False)), \
                mock.patch.object(self.work, "close_issue") as closed:
            self.assertEqual("pending", self.work.reprove(self.led, self.entry, self.task, self.issue))
        self.assertFalse(closed.called)


if __name__ == "__main__":
    unittest.main()
