"""Issues whose METHOD names Antigravity are authored through the router's Antigravity class."""
import sys
import subprocess
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexus import executor  # noqa: E402

ENTRY = {"repo": "o/r", "path": "/tmp/r"}


class Route(unittest.TestCase):
    def issue(self, body, labels=()):
        return {"number": 1, "title": "t", "body": body, "labels": [{"name": n} for n in labels]}

    def test_method_naming_antigravity_routes_to_antigravity(self):
        argv, prompt, road, _ = executor.plan(ENTRY, self.issue("- METHOD: English authored by Antigravity via ./tbs-agy"))
        self.assertEqual(["run", "customer-copy-antigravity", "--", "-p"], argv[1:5])
        self.assertIn("/tmp/r", prompt)
        self.assertIsNone(road)

    def test_copy_authority_label_routes_to_antigravity(self):
        self.assertEqual("customer-copy-antigravity", executor.plan(ENTRY, self.issue("", ["copy-authority"]))[0][2])

    def test_plain_issue_and_non_method_mention_stay_claude(self):
        for body in ("fix a bug", "Background: Antigravity wrote this once.\n- METHOD: edit the parser"):
            self.assertEqual("code-judgment", executor.plan(ENTRY, self.issue(body))[0][2], body)

    def test_claude_failure_has_codex_continuation_and_copy_has_claude(self):
        argv, prompt, _, _ = executor.plan(ENTRY, self.issue("fix a bug"))
        fallback = executor.provider_fallback(argv, prompt, ENTRY["path"])
        self.assertEqual(fallback[1:4], ["run-provider", "code-judgment", "codex"])
        self.assertIn("do not repeat completed sends", fallback[-1])
        copy_argv=[executor.ROUTER,"run","customer-copy","--","exec"]
        self.assertEqual(executor.provider_fallback(copy_argv, prompt, ENTRY["path"])[1:4],
                         ["run-provider", "customer-copy", "claude"])
        antigravity, copy_prompt, _, _ = executor.plan(ENTRY, self.issue("", ["copy-authority"]))
        self.assertIsNone(executor.provider_fallback(antigravity, copy_prompt, ENTRY["path"]))

    def test_codex_reviewer_failure_continues_on_claude(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["model", "review"]:
                return subprocess.CompletedProcess(argv, 0, "gpt-6-astra\n", "")
            if argv[1] == "run":
                return subprocess.CompletedProcess(argv, 1, "", "limit")
            return subprocess.CompletedProcess(argv, 0, "VERDICT: PASS\n", "")
        verdict, _ = executor.review(ENTRY, "https://github.com/o/r/pull/1", "flt_1", run=run)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(calls[-1][1:4], ["run-provider", "review", "claude"])

    def test_both_review_providers_failing_retries_instead_of_recording_a_verdict(self):
        def run(argv, **kwargs):
            if argv[1:3] == ["model", "review"]:
                return subprocess.CompletedProcess(argv, 0, "gpt-6-astra\n", "")
            return subprocess.CompletedProcess(argv, 1, "", "provider unavailable")
        with self.assertRaisesRegex(executor.RoadError, "no verdict"):
            executor.review(ENTRY, "https://github.com/o/r/pull/1", "flt_2", run=run)


if __name__ == "__main__":
    unittest.main()
