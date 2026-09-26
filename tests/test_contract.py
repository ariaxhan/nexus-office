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

    def test_missing_check_is_not_a_pass_unless_a_review_flight_passed(self):
        no_check = dict(contract.parse(BLOCK), check=None)
        for c in (no_check, None):  # a null check, and a contract-less repo
            done, why = contract.done_receipt({"state": "LANDED", "sha": "abc"}, c, "/")
            self.assertFalse(done)
            self.assertTrue(why.startswith(contract.UNVERIFIED))
            done, why = contract.done_receipt({"state": "LANDED", "sha": "abc", "review": "flt_r PASS at abc"}, c, "/")
            self.assertTrue(done)
            self.assertIn("review flight passed", why)


if __name__ == "__main__":
    unittest.main()
