"""The shadow-grader fixture: an unread grader must never look like one that agreed with everything.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


class CareGraderBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        import sources.care_grader as cg
        self.cg = importlib.reload(cg)
        self.path = self.root / self.cg.LOG

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.tmp.cleanup()

    def write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))

    def row(self, decision="PASS", agreement="agree", **extra):
        r = {"ts": "2026-09-01T12:00:00Z", "email": "c@x.com", "subject": "환불",
             "decision": decision, "reason": "aligns with precedent", "human_action": "sent",
             "agreement": agreement, "sent_at": "2026-09-01T11:00:00Z"}
        r.update(extra)
        return r


class States(CareGraderBase):
    def test_unconfigured(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        c = self.cg.card(self.cg.read())
        assert_card(self, c)
        self.assertEqual(c["needs"], 0)

    def test_never_is_not_clear(self):
        # no file at all: the job never ran. Must flag needs, never green.
        c = self.cg.card(self.cg.read())
        assert_card(self, c)
        self.assertEqual(c["needs"], 1)

    def test_empty_is_not_false_green(self):
        self.write([])  # file exists, zero rows
        d = self.cg.read()
        self.assertEqual(d["state"], "empty")
        c = self.cg.card(d)
        assert_card(self, c)
        self.assertNotIn("clear", c["headline"].lower())
        self.assertNotIn("agree", c["headline"].lower())

    def test_torn_only_is_unreadable(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json\n{also bad\n")
        d = self.cg.read()
        self.assertEqual(d["state"], "unreadable")
        assert_card(self, self.cg.card(d))

    def test_all_agree(self):
        self.write([self.row(agreement="agree") for _ in range(4)])
        d = self.cg.read()
        c = self.cg.card(d)
        assert_card(self, c)
        self.assertEqual(d["agreement_pct"], 100)
        self.assertEqual(c["needs"], 0)

    def test_disagreement_surfaces_as_needs(self):
        rows = [self.row(agreement="agree") for _ in range(3)]
        rows.append(self.row(decision="REFUSE", agreement="disagree", subject="정지"))
        self.write(rows)
        d = self.cg.read()
        c = self.cg.card(d)
        assert_card(self, c)
        self.assertEqual(d["disagree"], 1)
        self.assertEqual(c["needs"], 1)  # a sent reply the grader would have held is worth a look
        self.assertIn("held", c["headline"].lower())

    def test_torn_line_costs_only_that_row(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps(self.row())
        self.path.write_text(good + "\n{ broken\n" + good + "\n")
        d = self.cg.read()
        self.assertEqual(d["state"], "ok")
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["torn"], 1)
        assert_card(self, self.cg.card(d))


if __name__ == "__main__":
    unittest.main()
