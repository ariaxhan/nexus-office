import unittest

from nexus import receipt


GOOD = receipt.emit("done", ["commit 993f534"], "origin/main contains 993f534",
                    ["git branch -r --contains 993f534 -> origin/main"])


class Grammar(unittest.TestCase):
    def test_emitted_receipt_round_trips_and_advances(self):
        verdict = receipt.consume("Landed.\n\n" + GOOD)
        self.assertEqual(verdict["transition"], "RECEIPT")
        self.assertEqual(verdict["receipt"], {
            "outcome": "done", "artifacts": ["commit 993f534"],
            "verification": "origin/main contains 993f534",
            "evidence": ["git branch -r --contains 993f534 -> origin/main"], "next": "none"})

    def test_prose_completion_goes_back_to_claimed(self):
        verdict = receipt.consume("Done! Fixed it and pushed, all tests pass.")
        self.assertEqual(verdict["transition"], "CLAIMED")
        self.assertIn("prose is not a completion", verdict["reasons"][0])

    def test_every_defect_is_named(self):
        body = "```receipt\noutcome: finished\nartifacts:\nverification: none\nnext: later\n```"
        reasons = receipt.consume(body)["reasons"]
        self.assertTrue(any("missing evidence" in r for r in reasons))
        self.assertTrue(any("outcome 'finished'" in r for r in reasons))
        self.assertTrue(any("verification must" in r for r in reasons))
        self.assertTrue(any("next must" in r for r in reasons))

    def test_done_needs_artifacts_and_evidence(self):
        with self.assertRaises(receipt.Rejected):
            receipt.emit("done", [], "ci", ["run 1"])
        with self.assertRaises(receipt.Rejected):
            receipt.emit("done", ["commit a"], "ci", [])

    def test_failed_may_carry_no_artifacts(self):
        body = receipt.emit("failed", [], "tower check", ["exit 1"], "tower: next attempt")
        self.assertEqual(receipt.parse(body)["artifacts"], [])

    def test_order_duplicates_unknown_and_two_blocks_reject(self):
        swapped = GOOD.replace("outcome: done\n", "").replace("next: none", "next: none\noutcome: done")
        self.assertIn("out of order", receipt.consume(swapped)["reasons"][0])
        self.assertTrue(receipt.consume(GOOD.replace("next: none", "next: none\nnext: none"))["reasons"])
        self.assertIn("unknown line", receipt.consume(GOOD.replace("next: none", "status: ok\nnext: none"))["reasons"][0])
        self.assertIn("2 receipt blocks", receipt.consume(GOOD + "\n" + GOOD)["reasons"][0])
        self.assertIn("not closed", receipt.consume(GOOD[:-3])["reasons"][0])

    def test_rejection_teaches_the_grammar_and_parses_once_filled(self):
        text = receipt.rejection(["no ```receipt block: prose is not a completion"], "https://x/c/1")
        self.assertIn("stays CLAIMED", text)
        self.assertIn("outcome: done | partial | failed | blocked", text)

    def test_emit_flattens_newlines_into_one_value(self):
        body = receipt.emit("partial", ["file a.py"], "npm\ntest", ["3 of 4"], "aria: review")
        self.assertEqual(receipt.parse(body)["verification"], "npm test")


if __name__ == "__main__":
    unittest.main()
