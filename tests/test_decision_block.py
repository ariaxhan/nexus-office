"""The decision block a parked issue carries, and the PR a landed one names.

This is the seam between the pipeline and the phone: the pipeline writes a
question with numbered options into its parking comment, and the office turns it
into buttons. Everything that can go wrong here is silent. A block half-parsed
draws a button that answers a question nobody asked; a block not parsed at all
just falls back to the comment box, which is why the parser refuses anything
that is not exactly the contract.

    python3 -m unittest tests.test_decision_block
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from office_sync_shim import mod as office_sync  # noqa: E402

GOOD = """❓ Ship the fix behind a flag, or straight to main?

- [ ] **1.** flag it: nobody sees it until you flip it (recommended)
- [ ] **2.** straight to main: live the moment it merges

<!-- pipeline-bot -->
"""


class ParseDecisionTest(unittest.TestCase):
    def parse(self, text):
        return office_sync.parse_decision(text)

    def test_a_good_block_becomes_a_question_and_its_options(self):
        got = self.parse(GOOD)
        self.assertEqual(got["question"],
                         "Ship the fix behind a flag, or straight to main?")
        self.assertEqual([o["n"] for o in got["options"]], [1, 2])
        self.assertEqual(got["options"][0]["label"], "flag it")
        self.assertEqual(got["options"][0]["consequence"],
                         "nobody sees it until you flip it")
        self.assertEqual(got["options"][1]["label"], "straight to main")

    def test_the_recommended_option_is_marked_and_the_others_are_not(self):
        got = self.parse(GOOD)
        self.assertEqual([o["recommended"] for o in got["options"]], [True, False])
        # And the word never survives into the sentence a person reads.
        self.assertNotIn("recommended", got["options"][0]["consequence"])

    def test_the_bot_marker_riding_at_the_end_is_tolerated(self):
        """The marker is how the comment is found at all, so it is always there."""
        self.assertIsNotNone(self.parse(GOOD))
        self.assertIsNotNone(self.parse(GOOD.replace("<!-- pipeline-bot -->", "")))

    def test_four_options_are_the_ceiling_and_two_are_the_floor(self):
        rows = ["- [ ] **%d.** o%d: c%d" % (n, n, n) for n in range(1, 6)]
        head = "❓ how many?\n"
        self.assertIsNone(self.parse(head + "\n".join(rows[:1])))
        self.assertIsNotNone(self.parse(head + "\n".join(rows[:2])))
        self.assertIsNotNone(self.parse(head + "\n".join(rows[:4])))
        self.assertIsNone(self.parse(head + "\n".join(rows)))

    def test_a_comment_without_a_question_mark_first_is_not_a_decision(self):
        self.assertIsNone(self.parse(
            "Automated pass complete. Comment here to send this back.\n\n"
            "- [ ] **1.** a: x\n- [ ] **2.** b: y"))
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse(None))
        self.assertIsNone(self.parse("❓\n- [ ] **1.** a: x\n- [ ] **2.** b: y"))

    def test_options_that_do_not_count_from_one_are_refused(self):
        """Numbers out of order mean the human's '**2.**' would answer option 3."""
        self.assertIsNone(self.parse("❓ q\n- [ ] **2.** a: x\n- [ ] **3.** b: y"))
        self.assertIsNone(self.parse("❓ q\n- [ ] **1.** a: x\n- [ ] **1.** b: y"))

    def test_a_label_with_no_consequence_still_parses(self):
        got = self.parse("❓ q\n- [ ] **1.** just do it\n- [ ] **2.** wait")
        self.assertEqual(got["options"][0]["label"], "just do it")
        self.assertEqual(got["options"][0]["consequence"], "")


class LandedPrTest(unittest.TestCase):
    def test_the_pr_a_landed_comment_names_is_a_number(self):
        self.assertEqual(office_sync.parse_landed_pr(
            "Automated pass landed a fix. **Merging PR #412 closes this issue.**"), 412)

    def test_no_pr_named_is_none_and_never_zero(self):
        self.assertIsNone(office_sync.parse_landed_pr("Automated pass complete."))
        self.assertIsNone(office_sync.parse_landed_pr(""))
        self.assertIsNone(office_sync.parse_landed_pr("see PR #12 sometime"))


class IssueRowTest(unittest.TestCase):
    """The row the phone actually reads. The keys are ABSENT when there is
    nothing to say, so the page tests a key and not a truthy empty shape."""

    def row(self, comment):
        return office_sync._issue_row({
            "number": 7, "title": "t", "body": "", "url": "", "updatedAt": "",
            "labels": {"nodes": []},
            "comments": {"nodes": [{"body": comment, "url": "u",
                                    "createdAt": "2026-09-01T00:00:00Z"}]},
        })

    def test_a_parked_issue_carries_its_decision(self):
        row = self.row(GOOD + "\n<!-- pipeline-bot -->")
        self.assertTrue(row["bot_last"])
        self.assertEqual(len(row["decision"]["options"]), 2)
        self.assertNotIn("landed_pr", row)

    def test_a_landed_issue_carries_its_pr_and_no_decision(self):
        row = self.row("Automated pass landed a fix. **Merging PR #9 closes "
                       "this issue.**\n<!-- pipeline-bot -->")
        self.assertEqual(row["landed_pr"], 9)
        self.assertNotIn("decision", row)

    def test_an_ordinary_park_carries_neither(self):
        row = self.row("Automated pass complete.\n<!-- pipeline-bot -->")
        self.assertNotIn("decision", row)
        self.assertNotIn("landed_pr", row)

    def test_a_block_past_the_1500_character_trim_still_parses(self):
        """`last_word` is trimmed for the page; the parse reads the whole comment."""
        row = self.row("x" * 0 + "❓ q\n- [ ] **1.** a: " + ("y" * 1600)
                       + "\n- [ ] **2.** b: z\n<!-- pipeline-bot -->")
        self.assertEqual(len(row["decision"]["options"]), 2)
        self.assertEqual(len(row["last_word"]), 1500)


if __name__ == "__main__":
    unittest.main()
