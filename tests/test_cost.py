"""The spend ledger, which is nine schemas wearing one filename.

The tests that matter here are not the ones about a well formed row. They are the
ones about the rows that do not fit: a different name for the money field, no
timestamp at all, no `estimate` flag, a line that does not parse. Every one of
those carries real money, and the specific defect this source exists to prevent
is any of them being quietly dropped out of a total that still calls itself
"lifetime".

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import datetime
import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


def _local_iso(days_ago: int = 0) -> str:
    """A timestamp with a real offset, so the source has to do tz work."""
    now = datetime.datetime.now().astimezone() - datetime.timedelta(days=days_ago)
    return now.isoformat()


# One row per schema actually found in the ledger on 2026-08-25, plus the awkward
# ones. Keep this list in step with the census in client/sources/cost.py.
def rows(today: str, older: str):
    return [
        # the modern lane row, with agent_id
        {"timestamp": today, "agent_id": "a1", "family": "opus", "source": "lane",
         "task": "build", "session_id": "s1", "turns": 4, "model": "opus-4",
         "input_tokens": 10, "output_tokens": 20, "total_cost": 1.50, "estimate": False},
        # the transcript row, same shape minus agent_id
        {"timestamp": today, "family": "sonnet", "source": "transcript",
         "task": "session", "session_id": "s2", "turns": 9, "model": "sonnet-4.5",
         "input_tokens": 10, "output_tokens": 20, "total_cost": 2.25, "estimate": False},
        # an estimated row: real money, but never a measurement
        {"timestamp": today, "family": "haiku", "source": "lane", "task": "guess",
         "total_cost": 4.00, "estimate": True},
        # a second estimate, on an older day, so the band spans more than one bar
        {"timestamp": older, "family": "opus", "source": "lane", "task": "guess",
         "total_cost": 1.00, "estimate": True},
        # the provider row: no timestamp key at all, and no family
        {"model": "deepseek.v3.2", "provider": "bedrock", "slug": "health-check",
         "source": "provider", "total_cost": 0.25},
        # cost_usd instead of total_cost, and dated
        {"timestamp": older, "agent": "reporter", "model": "sonnet-4.5",
         "task": "report", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.50},
        # cost_usd AND an explicit null timestamp
        {"timestamp": None, "agent": "free-tide", "task": "maintenance",
         "model": "sonnet", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.10},
        # `ts` rather than `timestamp`. It is a usable date, so it is dated.
        {"ts": _local_iso(2), "agent": "spark-curator", "task": "daily",
         "session": "sc-1", "cost_usd": 0.05},
        # a legacy row with no `estimate` key: neither measured nor estimated
        {"timestamp": older, "model": "sonnet", "task": "legacy", "notes": "old",
         "input_tokens": 1, "output_tokens": 1, "input_cost": 0.1,
         "output_cost": 0.2, "total_cost": 0.30},
    ]


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "_meta" / "logs").mkdir(parents=True)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.tmp.cleanup()

    @property
    def path(self):
        return self.root / "_meta" / "logs" / "costs.jsonl"

    def write(self, lines):
        body = "\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines)
        self.path.write_text(body + "\n", encoding="utf-8")

    def read(self):
        from sources import cost
        return importlib.reload(cost).read()

    def full(self):
        self.today = _local_iso(0)
        self.older = _local_iso(3)
        body = list(rows(self.today, self.older))
        # A line that is not JSON at all. Counted, never skipped in silence.
        return body[:4] + ["{ this is not json"] + body[4:] + [""]

    # -- the states ------------------------------------------------------------

    def test_no_root_configured_is_not_zero(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.assertEqual(self.read()["state"], "unconfigured")

    def test_a_root_that_is_not_there_says_so(self):
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root / "nope")
        self.assertEqual(self.read()["state"], "missing-root")

    def test_no_ledger_is_not_an_empty_ledger(self):
        self.assertEqual(self.read()["state"], "missing-ledger")

    def test_an_empty_ledger_is_not_a_missing_one(self):
        self.write([""])
        self.assertEqual(self.read()["state"], "empty")

    # -- the money -------------------------------------------------------------

    def test_the_total_is_every_row_including_the_awkward_ones(self):
        self.write(self.full())
        got = self.read()
        self.assertEqual(got["state"], "ok")
        # 1.50 + 2.25 + 4.00 + 1.00 + 0.25 + 0.50 + 0.10 + 0.05 + 0.30
        self.assertAlmostEqual(got["lifetime"]["total"], 9.95, places=6)
        self.assertEqual(got["rows"], 9)

    def test_cost_usd_is_money_too(self):
        self.write([{"timestamp": _local_iso(0), "cost_usd": 7.5, "estimate": False}])
        self.assertAlmostEqual(self.read()["lifetime"]["measured"], 7.5, places=6)

    def test_money_is_never_re_derived_from_tokens(self):
        # The row's own total disagrees with its tokens on purpose. The ledger's
        # number wins, always: a second calculation of money is a second number
        # to be wrong.
        self.write([{"timestamp": _local_iso(0), "input_tokens": 10 ** 9,
                     "output_tokens": 10 ** 9, "total_cost": 0.01, "estimate": False}])
        self.assertAlmostEqual(self.read()["lifetime"]["total"], 0.01, places=6)

    def test_a_row_with_no_money_field_is_counted_not_invented(self):
        self.write([{"timestamp": _local_iso(0), "task": "no money here", "estimate": False}])
        got = self.read()
        self.assertEqual(got["unpriced"], 1)
        self.assertEqual(got["lifetime"]["total"], 0.0)

    # -- the estimate split ----------------------------------------------------

    def test_estimated_money_stays_in_its_own_band(self):
        self.write(self.full())
        life = self.read()["lifetime"]
        self.assertAlmostEqual(life["estimated"], 5.00, places=6)
        # measured is the explicitly flagged rows only
        self.assertAlmostEqual(life["measured"], 3.75, places=6)

    def test_rows_with_no_estimate_flag_are_neither(self):
        self.write(self.full())
        life = self.read()["lifetime"]
        # 0.25 provider + 0.50 cost_usd + 0.10 null-ts + 0.05 ts + 0.30 legacy
        self.assertAlmostEqual(life["unflagged"], 1.20, places=6)
        self.assertNotIn(life["unflagged"], (life["measured"], life["estimated"]))

    def test_the_three_bands_add_up_to_the_total(self):
        self.write(self.full())
        life = self.read()["lifetime"]
        self.assertAlmostEqual(
            life["measured"] + life["estimated"] + life["unflagged"],
            life["total"], places=6)

    def test_the_estimated_fraction_is_of_the_whole_total(self):
        self.write(self.full())
        got = self.read()
        self.assertAlmostEqual(got["estimated_fraction"], 5.00 / 9.95, places=5)

    def test_no_estimated_rows_means_a_zero_fraction_not_a_missing_one(self):
        self.write([{"timestamp": _local_iso(0), "total_cost": 3.0, "estimate": False}])
        got = self.read()
        self.assertEqual(got["lifetime"]["estimated"], 0.0)
        self.assertEqual(got["estimated_fraction"], 0.0)

    # -- the rows that cannot be plotted ---------------------------------------

    def test_undated_rows_are_counted_and_their_money_kept(self):
        self.write(self.full())
        got = self.read()
        # the provider row (no time key) and the explicit null timestamp
        self.assertEqual(got["undated"]["rows"], 2)
        self.assertAlmostEqual(got["undated"]["value"], 0.35, places=6)
        # and that money is still inside the lifetime total
        self.assertAlmostEqual(got["lifetime"]["total"], 9.95, places=6)

    def test_undated_money_is_absent_from_every_bar(self):
        self.write(self.full())
        got = self.read()
        plotted = sum(d["measured"] + d["estimated"] + d["unflagged"] for d in got["days"])
        self.assertAlmostEqual(plotted, 9.95 - 0.35, places=6)

    def test_a_ts_field_is_a_usable_date(self):
        self.write(self.full())
        self.assertEqual(self.read()["undated"]["rows"], 2)

    def test_an_unparseable_line_is_counted_not_skipped(self):
        self.write(self.full())
        self.assertEqual(self.read()["unparseable"], 1)

    def test_an_unparseable_line_does_not_lose_the_rows_after_it(self):
        self.write(self.full())
        self.assertEqual(self.read()["rows"], 9)

    # -- the axis --------------------------------------------------------------

    def test_the_window_is_fourteen_days_ending_today(self):
        self.write(self.full())
        got = self.read()
        self.assertEqual(len(got["days"]), 14)
        self.assertEqual(got["window_days"], 14)
        self.assertEqual(got["days"][-1]["date"], datetime.date.today().isoformat())
        self.assertLess(got["days"][0]["date"], got["days"][-1]["date"])

    def test_a_day_with_no_spend_is_present_and_zero(self):
        self.write([{"timestamp": _local_iso(0), "total_cost": 1.0, "estimate": False}])
        days = self.read()["days"]
        self.assertEqual(days[0]["rows"], 0)
        self.assertEqual(days[0]["measured"], 0.0)
        self.assertAlmostEqual(days[-1]["measured"], 1.0, places=6)

    def test_a_row_older_than_the_window_stays_in_the_lifetime_total(self):
        self.write([{"timestamp": _local_iso(400), "total_cost": 5.0, "estimate": False}])
        got = self.read()
        self.assertAlmostEqual(got["lifetime"]["total"], 5.0, places=6)
        self.assertEqual(got["window_total"], 0.0)
        self.assertEqual(got["undated"]["rows"], 0)

    # -- the breakdowns --------------------------------------------------------

    def test_a_missing_family_is_its_own_bucket_not_a_lump(self):
        self.write(self.full())
        fams = {(r["key"] or "<missing>"): r for r in self.read()["by_family"]}
        self.assertIn("<missing>", fams)
        self.assertTrue(fams["<missing>"]["missing"])
        # provider 0.25 + cost_usd 0.50 + null-ts 0.10 + ts 0.05 + legacy 0.30
        self.assertAlmostEqual(fams["<missing>"]["value"], 1.20, places=6)
        self.assertFalse(fams["opus"]["missing"])

    def test_breakdowns_sum_to_the_lifetime_total(self):
        self.write(self.full())
        got = self.read()
        for dim in ("by_family", "by_source"):
            self.assertAlmostEqual(
                sum(r["value"] for r in got[dim]), got["lifetime"]["total"],
                places=5, msg=dim)

    def test_breakdowns_are_biggest_first(self):
        self.write(self.full())
        vals = [r["value"] for r in self.read()["by_family"]]
        self.assertEqual(vals, sorted(vals, reverse=True))


if __name__ == "__main__":
    unittest.main()
