"""Tower settles from authoritative GitHub state: a merge exit code and a failed comment are not evidence."""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from nexus import work
from nexus import terminal
from tests import test_work  # fixture only

PR = "https://github.com/sample/product/pull/9"


class GithubReconcile(unittest.TestCase):
    github = test_work.WorkTests.github

    def setUp(self):
        test_work.WorkTests.setUp(self)
        self.pr = dict(headRefName="aria/issue-1", headRefOid="h1", baseRefName="main", labels=[], files=[])
        self.merge_rc, self.view_rc, self.comment_rc = 1, 0, 0
        self.after = {"state": "OPEN", "mergeCommit": None}
        self.edits, self.terminals = [], []
        real = self.github

        def gh(argv, **kw):
            if argv[:3] == ["gh", "pr", "merge"]:
                return subprocess.CompletedProcess(argv, self.merge_rc, "", "merge conflict or race")
            if argv[:3] == ["gh", "pr", "view"]:
                if "state,mergeCommit" in argv:
                    return subprocess.CompletedProcess(argv, self.view_rc, json.dumps(self.after), "api down")
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.pr), "")
            if argv[:3] == ["gh", "pr", "comment"]:
                return subprocess.CompletedProcess(argv, self.comment_rc, "https://c/1" if not self.comment_rc else "",
                                                   "comment refused")
            if argv[:3] == ["gh", "issue", "edit"]:
                self.edits.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real(argv, **kw)
        patch("nexus.work.subprocess.run", side_effect=gh).start()
        patch("nexus.work._sensitive_hold", return_value=None).start()

        def landing(ledger, fid, repo, result):
            self.terminals.append(result)
            if result["state"] == "LANDED":
                ledger.set_state(fid, "produced"), ledger.set_state(fid, "verified", evidence=terminal.landed(result, result["sha"]))
        patch("nexus.tower.land_write_flight", side_effect=landing).start()
        work.discover(self.led, self.entry)
        self.task = self.led.tasks()[0]
        token = work._lane.set(work.TOWER_LABEL)
        self.addCleanup(lambda: work._lane.reset(token))

    def review(self, verdict="PASS"):
        with patch("nexus.executor.review", return_value=(verdict, "reason")):
            return work.tower_review(self.led, self.entry, self.task, PR)

    def hold_labels(self):
        return [e for e in self.edits if "hold" in e]

    def test_merge_exit_failure_but_pr_merged_settles_landed(self):
        self.after = {"state": "MERGED", "mergeCommit": {"oid": "m1"}}
        self.assertEqual("done", self.review())
        self.assertEqual(("LANDED", "m1"), (self.terminals[-1]["state"], self.terminals[-1]["sha"]))
        self.assertEqual([], self.hold_labels())
        self.assertEqual(1, len(self.closed))

    def test_unmerged_pr_is_requeued_without_hold(self):
        self.assertEqual("pending", self.review())
        self.assertEqual([], self.hold_labels())
        waiting = work.latest(self.led, "work.pending", self.task["id"])
        self.assertTrue(waiting["reason"].startswith("merge failed"), waiting)
        self.assertGreater(waiting["next_retry"], work.time.time())
        with patch("nexus.work.next_retry", return_value=0), \
                patch("nexus.work.tower_review", return_value="pending") as again, \
                patch("nexus.work.tower_execute") as fly:
            work._run_task(self.led, self.entry, self.task)
        again.assert_called_once_with(self.led, self.entry, unittest.mock.ANY, PR)
        fly.assert_not_called()

    def test_unreadable_pr_state_stays_unsettled_and_retryable(self):
        self.view_rc = 1
        self.assertEqual("pending", self.review())
        self.assertEqual([], self.hold_labels())
        self.assertIn("unknown", work.latest(self.led, "work.pending", self.task["id"])["reason"])
        self.assertEqual([], self.terminals)  # nothing settled as terminal
        self.assertNotEqual("done", self.led.conn.execute("SELECT state FROM tasks WHERE id=?",
                                                          (self.task["id"],)).fetchone()[0])

    def test_fail_verdict_still_holds(self):
        self.assertEqual("pending", self.review("FAIL"))
        self.assertEqual(1, len(self.hold_labels()))

    def test_failed_comment_is_never_proof(self):
        self.comment_rc = 1
        self.review("FAIL")
        held = self.terminals[-1]
        self.assertIsNone(held["comment_url"])
        self.assertIn("comment refused", held["comment_error"])
        self.assertEqual(PR, held["pr_url"])  # the PR is named as the PR, not as the comment
        self.assertEqual("comment refused", work.latest(self.led, "work.pending", self.task["id"])["comment_error"])


if __name__ == "__main__":
    unittest.main()
