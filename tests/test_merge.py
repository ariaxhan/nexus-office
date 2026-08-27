"""Merging is the only intent that puts code on a default branch.

Everything else the office can queue is reversible or loud: a comment, a label,
a nudge. A merge is neither, so the tests that matter here are the ones about
merging the WRONG thing, not the right one.

The browser holds a view token and can queue any intent at all. That is by
design: a stolen token must be able to ask and never to act. These tests are the
proof that asking is not acting.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

os.environ.setdefault("OFFICE_URL", "https://example.invalid")


class MergeTest(unittest.TestCase):
    def setUp(self):
        import importlib
        import office_sync_shim  # noqa: F401  (see the shim note below)
        self.mod = importlib.reload(office_sync_shim).mod
        self.calls = []
        self.pr = {
            "headRefName": "pipeline/auto-issue-7",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "state": "OPEN",
            "title": "#7: fix the thing",
        }
        self.merge_rc = 0
        self.merge_err = ""

        def fake_sh(cmd, timeout=45, env=None, check=False):
            self.calls.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                if self.pr is None:
                    return 1, "", "could not resolve to a PullRequest"
                return 0, json.dumps(self.pr), ""
            if cmd[:3] == ["gh", "pr", "merge"]:
                return self.merge_rc, "", self.merge_err
            raise AssertionError(f"unexpected command {cmd}")

        self.mod.sh = fake_sh

    def merge(self, pr="7", dry=False):
        return self.mod.apply_merge("acme/site", "ariaxhan", "tok", {"pr": pr}, dry)

    def merged(self):
        return [c for c in self.calls if c[:3] == ["gh", "pr", "merge"]]

    # -- the boundary ---------------------------------------------------------

    def test_a_branch_that_is_not_the_pipelines_is_REFUSED(self):
        # THE test. A stolen view token can queue a merge for any PR number it
        # likes, including a human's own branch. The prefix is the only thing
        # standing between that and merged code, and it is checked HERE, on the
        # laptop, against GitHub's answer rather than the browser's claim.
        self.pr["headRefName"] = "aria/my-own-work"
        ok, msg = self.merge()
        self.assertFalse(ok)
        self.assertIn("not a pipeline branch", msg)
        self.assertIn("aria/my-own-work", msg)
        self.assertEqual(self.merged(), [], "it must never reach gh pr merge")

    def test_a_branch_merely_containing_the_prefix_is_refused(self):
        # "not-pipeline/auto-issue-7" contains the prefix but does not start with
        # it. A substring check here would be a hole.
        self.pr["headRefName"] = "not-pipeline/auto-issue-7"
        ok, _ = self.merge()
        self.assertFalse(ok)
        self.assertEqual(self.merged(), [])

    def test_a_draft_is_never_merged(self):
        self.pr["isDraft"] = True
        ok, msg = self.merge()
        self.assertFalse(ok)
        self.assertIn("draft", msg)
        self.assertEqual(self.merged(), [])

    def test_a_conflicting_pr_is_refused(self):
        self.pr["mergeable"] = "CONFLICTING"
        ok, msg = self.merge()
        self.assertFalse(ok)
        self.assertIn("conflicts", msg)
        self.assertEqual(self.merged(), [])

    def test_an_already_closed_pr_is_refused(self):
        self.pr["state"] = "MERGED"
        ok, msg = self.merge()
        self.assertFalse(ok)
        self.assertEqual(self.merged(), [])

    def test_a_pr_that_cannot_be_read_is_refused_not_assumed(self):
        # Not reaching GitHub is not permission. A merge that proceeds when the
        # check could not run is the same defect as a green build nobody ran.
        self.pr = None
        ok, msg = self.merge()
        self.assertFalse(ok)
        self.assertIn("could not read", msg)
        self.assertEqual(self.merged(), [])

    def test_a_non_numeric_pr_never_shells_out(self):
        for bad in ("", "7; rm -rf /", "../7", "abc", None):
            ok, msg = self.mod.apply_merge("acme/site", "who", "tok", {"pr": bad}, False)
            self.assertFalse(ok, bad)
            self.assertIn("needs a PR number", msg)
        self.assertEqual(self.calls, [], "nothing may run before the number is validated")

    # -- the happy path, which is the least interesting one -------------------

    def test_a_pipeline_pr_merges_squashed_and_deletes_its_branch(self):
        ok, msg = self.merge()
        self.assertTrue(ok, msg)
        cmd = self.merged()[0]
        self.assertIn("--squash", cmd)
        self.assertIn("--delete-branch", cmd)
        self.assertIn("7", cmd)

    def test_a_dry_run_says_what_it_would_do_and_does_nothing(self):
        ok, msg = self.merge(dry=True)
        self.assertTrue(ok)
        self.assertIn("would", msg)
        self.assertEqual(self.merged(), [])

    def test_github_refusing_the_merge_is_reported_verbatim(self):
        self.merge_rc = 1
        self.merge_err = "Pull request is not mergeable: the base branch was modified"
        ok, msg = self.merge()
        self.assertFalse(ok)
        self.assertIn("base branch was modified", msg)

    def test_unknown_mergeability_still_tries_rather_than_guessing(self):
        # UNKNOWN means GitHub has not finished computing it. Treating that as a
        # refusal would make the button randomly dead; treating it as permission
        # is fine because gh itself refuses an unmergeable PR and its refusal is
        # reported verbatim.
        self.pr["mergeable"] = "UNKNOWN"
        ok, _ = self.merge()
        self.assertTrue(ok)


class RoutingTest(unittest.TestCase):
    """A merge must be routed to apply_merge before any issue-shaped handling."""

    def test_merge_is_a_github_kind_on_the_local_server(self):
        import serve
        self.assertIn("merge", serve.GITHUB_KINDS)
        self.assertNotIn("merge", serve.RUNTIME_KINDS)
        # And a merge with nothing to merge never reaches apply_merge at all.
        err, d = serve.validate({"kind": "merge", "repo": "acme/thing"})
        self.assertEqual(err, "a merge needs a numeric pr")
        self.assertIsNone(d)

    def test_the_prefix_is_configurable_but_defaults_to_pipeline(self):
        import office_sync_shim
        self.assertTrue(office_sync_shim.mod.PR_PREFIX.startswith("pipeline"))


if __name__ == "__main__":
    unittest.main()
