"""One contract, one gate, receipts for done."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexus import contract  # noqa: E402

BLOCK = ('```tbs-contract\ndepends_on: ["o/r#66"]\nwrite_set: ["src/a.jsx"]\nroute: "antigravity"\n'
         'check: "npm run verify:lessons"\nacceptance: "English renders"\n```')


def gh(states):
    return lambda argv: SimpleNamespace(returncode=0, stdout=states.get(argv[2].split("/issues/")[1], "open"))


class Contract(unittest.TestCase):
    def test_parse_valid_and_invalid(self):
        c = contract.parse("prose\n" + BLOCK)
        self.assertEqual((["o/r#66"], "antigravity"), (c["depends_on"], c["route"]))
        for bad in ("", BLOCK.replace('"antigravity"', '"nope"'), BLOCK.replace('["o/r#66"]', '["#66"]'),
                    BLOCK.replace('acceptance: "English renders"\n', '')):
            self.assertIsNone(contract.parse(bad), bad)

    def test_open_dependency_blocks_closed_admits(self):
        issue = {"body": BLOCK, "labels": []}
        contract.ANTIGRAVITY_BIN = sys.executable
        self.assertEqual("blocked by open dependency o/r#66", contract.gate(issue, gh=gh({"66": "open"}))[1])
        self.assertIsNone(contract.gate(issue, gh=gh({"66": "closed"}))[1])

    def test_missing_contract_is_ineligible_when_required(self):
        self.assertIn("no valid tbs-contract", contract.gate({"body": "Depends on: #1"}, gh=gh({}))[1])
        self.assertIsNone(contract.gate({"body": "x"}, required=False, gh=gh({}))[1])

    def test_route_and_window(self):
        contract.ANTIGRAVITY_BIN = "/nonexistent"
        self.assertIn("route antigravity unavailable", contract.gate({"body": BLOCK}, gh=gh({"66": "closed"}))[1])
        claude = BLOCK.replace('"antigravity"', '"claude"')
        issue = {"body": claude, "labels": [{"name": "sensitive"}]}
        self.assertIn("KST", contract.gate(issue, window_open=lambda: False, gh=gh({"66": "closed"}))[1])

    def test_done_needs_landed_commit_and_passing_check(self):
        c = contract.parse(BLOCK)
        ok = lambda *a, **k: SimpleNamespace(returncode=0)  # noqa: E731
        bad = lambda *a, **k: SimpleNamespace(returncode=1)  # noqa: E731
        self.assertFalse(contract.done_receipt({"state": "CLOSED", "reason": "no_change"}, c, "/", run=ok)[0])
        self.assertFalse(contract.done_receipt({"state": "LANDED", "sha": "abc"}, c, "/", run=bad)[0])
        self.assertTrue(contract.done_receipt({"state": "LANDED", "sha": "abc"}, c, "/", run=ok)[0])

    def test_missing_check_is_not_a_pass_without_a_recorded_review(self):
        for c in (dict(contract.parse(BLOCK), check=None), None):  # a null check, and a contract-less repo
            for review in (None, {"verdict": "FAIL", "head": "h"}, {"verdict": "PASS"}):
                done, why = contract.done_receipt({"state": "LANDED", "sha": "abc"}, c, "/", review=review)
                self.assertFalse(done)
                self.assertTrue(why.startswith(contract.UNVERIFIED))


class ReviewedChange(unittest.TestCase):
    """Terminal proof from a recorded review holds only for the exact change that was reviewed."""

    def setUp(self):
        import subprocess, tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.git = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
        origin, self.repo = root / "o.git", root / "w"
        self.git(root, "init", "-q", "--bare", "-b", "main", str(origin))
        self.git(root, "clone", "-q", str(origin), str(self.repo))
        self.git(self.repo, "config", "user.email", "t@t"), self.git(self.repo, "config", "user.name", "t")
        self.commit("a", "1")
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "switch", "-q", "-c", "pr")
        self.commit("b", "reviewed")
        self.head = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "push", "-q", "origin", "main", "pr")

    def commit(self, name, text):
        (self.repo / name).write_text(text)
        self.git(self.repo, "add", name), self.git(self.repo, "commit", "-q", "-m", name)

    def squash(self, extra=None):
        self.git(self.repo, "switch", "-q", "main")
        self.git(self.repo, "merge", "-q", "--squash", "pr")
        if extra:
            (self.repo / extra).write_text("unreviewed")
            self.git(self.repo, "add", extra)
        self.git(self.repo, "commit", "-q", "-m", "squash")
        sha = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "push", "-q", "origin", "main")
        return sha

    def test_squash_of_the_reviewed_head_is_done(self):
        sha = self.squash()
        done, why = contract.done_receipt({"state": "LANDED", "sha": sha}, None, str(self.repo),
                                          review={"verdict": "PASS", "head": self.head})
        self.assertTrue(done, why)
        self.assertIn(sha, why)

    def test_landed_change_that_differs_from_the_reviewed_one_is_not_done(self):
        sha = self.squash(extra="c")
        done, why = contract.done_receipt({"state": "LANDED", "sha": sha}, None, str(self.repo),
                                          review={"verdict": "PASS", "head": self.head})
        self.assertFalse(done)
        self.assertIn("not the change reviewed", why)

if __name__ == "__main__":
    unittest.main()
