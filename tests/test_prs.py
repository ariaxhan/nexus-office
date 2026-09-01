"""The merge-plan fixture: a stale plan, a torn plan and no plan are three
different answers, and none of them is a green "nothing to merge".

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

NOW = 1_800_000_000.0


class PrsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        import sources.prs as prs
        self.prs = importlib.reload(prs)
        self.plans = self.root / self.prs.PLANS
        self.plans.mkdir(parents=True)

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.tmp.cleanup()

    def beat(self, when: float):
        import time
        p = self.root / self.prs.HEARTBEAT
        p.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when)))

    def plan(self, repo, steps=(), parked=()):
        (self.plans / (repo.replace("/", "_") + ".json")).write_text(json.dumps(
            {"repo": repo, "steps": list(steps), "parked": list(parked)}))


class States(PrsBase):
    def test_unconfigured(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        c = self.prs.card(self.prs.read(NOW))
        assert_card(self, c)
        self.assertEqual(c["needs"], 0)

    def test_never_swept_needs_a_person(self):
        self.plans.rmdir()
        d = self.prs.read(NOW)
        self.assertEqual(d["state"], "never")
        c = self.prs.card(d)
        assert_card(self, c)
        self.assertEqual(c["needs"], 1)

    def test_swept_with_no_prs_is_a_calm_zero(self):
        self.beat(NOW - 60)
        c = self.prs.card(self.prs.read(NOW))
        assert_card(self, c)
        self.assertEqual(c["needs"], 0)
        self.assertIn("No open PRs", c["headline"])

    def test_merge_now_counts_and_rolling_asks(self):
        self.beat(NOW - 60)
        self.plan("o/a", steps=[{"number": 1, "merge_now": True}, {"number": 2, "merge_now": False}],
                  parked=[{"number": 3, "kind": "rolling"}])
        self.plan("o/b", steps=[{"number": 5, "merge_now": True}])
        c = self.prs.card(self.prs.read(NOW))
        assert_card(self, c)
        self.assertEqual(c["headline"], "2 of 4 open PRs can merge now")
        self.assertEqual(c["needs"], 1)
        self.assertEqual([r["id"] for r in c["rows"]], ["o/a", "o/b"])
        self.assertEqual(c["rows"][0]["badge"], "1/3")

    def test_a_torn_plan_is_a_need_not_a_zero(self):
        self.beat(NOW - 60)
        (self.plans / "o_c.json").write_text("{not json")
        c = self.prs.card(self.prs.read(NOW))
        assert_card(self, c)
        self.assertEqual(c["needs"], 1)
        self.assertTrue(any(f["label"] == "torn plans" for f in c["facts"]))

    def test_a_day_old_plan_is_stale(self):
        self.beat(NOW - 2 * 24 * 3600)
        self.plan("o/a", steps=[{"number": 1, "merge_now": True}])
        c = self.prs.card(self.prs.read(NOW))
        assert_card(self, c)
        self.assertIn("stale", c["headline"])
        self.assertGreaterEqual(c["needs"], 1)


if __name__ == "__main__":
    unittest.main()
