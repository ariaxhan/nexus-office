import subprocess
import unittest
from unittest.mock import patch

from nexus import executor, remote_review


class RemoteReviewTest(unittest.TestCase):
 def test_remote_review_uses_diff_only_and_bounded_two_account_attempts(self):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "gh":
            return subprocess.CompletedProcess(argv, 0, "diff --git a/a b/a\n", "")
        return subprocess.CompletedProcess(argv, 0, '{"status":"auth_unavailable"}', "")

    remote_review._blocked_until.clear()
    self.assertEqual(remote_review.review("Thinking-Brain-School/tbs-www", "https://github.com/x/pull/1",
                                          "Review", run=run), "")
    self.assertEqual([call[0][0] for call in calls], ["gh", "ssh", "ssh"])
    self.assertTrue(all("Review" not in " ".join(call[0]) for call in calls[1:]))
    self.assertTrue(all(call[1]["timeout"] <= 195 for call in calls[1:]))
    self.assertTrue(all("diff --git" in call[1]["input"] for call in calls[1:]))


 def test_remote_review_refuses_oversized_diff_before_contacting_macbook(self):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "x" * (remote_review.MAX_DIFF + 1), "")

    self.assertEqual(remote_review.review("r", "pr", "Review", run=run), "")
    self.assertEqual(len(calls), 1)


 def test_executor_remote_review_only_for_tbs_account(self):

    def run(argv, **kwargs):
        if argv[1:3] == ["model", "review"]:
            return subprocess.CompletedProcess(argv, 0, "gpt-6-astra", "")
        return subprocess.CompletedProcess(argv, 1, "", "provider unavailable")

    with patch.object(remote_review, "review", return_value="VERDICT: PASS") as remote:
        verdict, _ = executor.review({"repo": "Thinking-Brain-School/tbs-www", "account": "tbs"},
                                     "https://github.com/x/pull/1", "flight", run=run)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(remote.call_count, 1)
        remote.reset_mock()
        try:
            executor.review({"repo": "ariaxhan/example", "account": "personal"},
                            "https://github.com/x/pull/1", "flight", run=run)
        except executor.RoadError:
            pass
        self.assertEqual(remote.call_count, 0)
