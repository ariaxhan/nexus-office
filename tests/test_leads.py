"""The gig desk: the distinctions that would be worth money to get wrong.

The parsing here is not the risk. Four things are:

  a claim vs a receipt   the scout's guess about where work comes from must
                         never arrive on the board as a counted fact
  quiet vs dead          a sent lead nobody answered is the thing this desk
                         exists to stop losing, and it looks like nothing
  torn vs absent         a lead lost to a bad line must not shrink the total
                         silently, exactly as cost.py refuses to drop a row
  drafted vs sent        the draft-only rule is the whole safety story; a lead
                         with a draft is waiting on Aria, never on a recipient

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import datetime
import importlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

import os  # noqa: E402

from sources import leads as leads_mod  # noqa: E402


def iso(days_ago: float) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


class Desk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.desk = self.root / "_meta" / "leads"
        self.desk.mkdir(parents=True)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("OFFICE_RUNTIME_ROOT", None))
        importlib.reload(leads_mod)

    def write(self, *rows: str) -> None:
        (self.desk / "leads.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def lead(self, **over) -> str:
        row = {"id": "l1", "who": "someone", "need": "an agent runtime",
               "where": "gmail", "stage": "new", "found_at": iso(1),
               "last_touch": iso(1)}
        row.update(over)
        return json.dumps(row)

    # ── the states, kept apart ──────────────────────────────────────────────
    def test_no_root_is_not_an_empty_desk(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        importlib.reload(leads_mod)
        self.assertEqual(leads_mod.read()["state"], "unconfigured")

    def test_desk_that_never_ran_is_missing_not_ok(self):
        # A scout that has never run and a scout that found nothing are two
        # different problems with two different fixes.
        for child in self.desk.iterdir():
            child.unlink()
        self.desk.rmdir()
        self.assertEqual(leads_mod.read()["state"], "missing")

    def test_desk_with_no_ledger_yet_is_ok_and_empty(self):
        out = leads_mod.read()
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["waiting_on_aria"], 0)

    # ── the headline is who is blocked, not how many exist ──────────────────
    def test_headline_counts_only_what_waits_on_aria(self):
        self.write(self.lead(id="a", stage="sent"),
                   self.lead(id="b", stage="dead"),
                   self.lead(id="c", stage="drafted"),
                   self.lead(id="d", stage="replied"))
        out = leads_mod.read()
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["waiting_on_aria"], 2)

    def test_blocked_leads_sort_to_the_top(self):
        self.write(self.lead(id="quiet", stage="sent", last_touch=iso(40)),
                   self.lead(id="fresh", stage="new"),
                   self.lead(id="draft", stage="drafted"))
        self.assertEqual(leads_mod.read()["leads"][0]["id"], "draft")

    # ── quiet is not dead and not fine ──────────────────────────────────────
    def test_a_sent_lead_nobody_answered_goes_quiet(self):
        self.write(self.lead(id="ghost", stage="sent", last_touch=iso(30)))
        out = leads_mod.read()
        self.assertEqual(out["quiet"], 1)
        self.assertTrue(out["leads"][0]["quiet"])
        self.assertGreaterEqual(out["leads"][0]["quiet_days"], 29)

    def test_a_dead_lead_is_never_quiet(self):
        # Closed with a reason is finished work, not a failure to chase.
        self.write(self.lead(id="closed", stage="dead", last_touch=iso(90),
                             why="they hired in house"))
        out = leads_mod.read()
        self.assertEqual(out["quiet"], 0)
        self.assertEqual(out["leads"][0]["why"], "they hired in house")

    def test_a_fresh_sent_lead_is_not_quiet(self):
        self.write(self.lead(id="new-send", stage="sent", last_touch=iso(2)))
        self.assertEqual(leads_mod.read()["quiet"], 0)

    # ── a torn line is a lead we lost, and it says so ───────────────────────
    def test_a_torn_line_is_counted_not_dropped(self):
        self.write(self.lead(id="good"), "{not json", self.lead(id="also-good"))
        out = leads_mod.read()
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["torn_lines"], 1)

    def test_an_undated_lead_still_counts(self):
        self.write(json.dumps({"id": "nodate", "stage": "new", "where": "x"}))
        out = leads_mod.read()
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["undated"], 1)
        self.assertIsNone(out["leads"][0]["quiet_days"])

    def test_an_unknown_stage_keeps_its_own_name(self):
        self.write(self.lead(id="weird", stage="negotiating"))
        out = leads_mod.read()
        self.assertFalse(out["leads"][0]["known_stage"])
        row = [s for s in out["by_stage"] if s["stage"] == "negotiating"]
        self.assertEqual(row[0]["count"], 1)
        self.assertTrue(row[0]["unknown"])

    # ── the claim and the receipt never merge ───────────────────────────────
    def test_a_source_the_scout_only_claims_is_marked_as_a_claim(self):
        (self.desk / "sources.json").write_text(json.dumps({
            "learned_at": iso(1),
            "sources": [{"key": "twitter", "label": "X replies",
                         "confidence": "low", "why": "one gig in 2025, unverified"}],
        }), encoding="utf-8")
        self.write(self.lead(id="a", where="gmail", stage="replied"))
        out = leads_mod.read()["map"]
        by_key = {s["key"]: s for s in out["sources"]}
        self.assertTrue(by_key["twitter"]["claimed"])
        self.assertEqual(by_key["twitter"]["found"], 0)
        self.assertEqual(by_key["twitter"]["claimed_confidence"], "low")
        # gmail was never claimed, but it is measured, and both belong on the wall
        self.assertFalse(by_key["gmail"]["claimed"])
        self.assertEqual(by_key["gmail"]["converted"], 1)

    def test_a_confidence_the_scout_invented_is_refused(self):
        (self.desk / "sources.json").write_text(json.dumps({
            "sources": [{"key": "x", "confidence": "very sure"}]}), encoding="utf-8")
        self.write(self.lead(id="a", where="x"))
        row = leads_mod.read()["map"]["sources"][0]
        self.assertEqual(row["claimed_confidence"], "")

    def test_sources_ranked_by_what_actually_replied(self):
        self.write(self.lead(id="a", where="board", stage="new"),
                   self.lead(id="b", where="board", stage="new"),
                   self.lead(id="c", where="board", stage="new"),
                   self.lead(id="d", where="referral", stage="replied"))
        keys = [s["key"] for s in leads_mod.read()["map"]["sources"]]
        self.assertEqual(keys[0], "referral")

    def test_a_torn_source_map_does_not_cost_the_ledger(self):
        (self.desk / "sources.json").write_text("{oops", encoding="utf-8")
        self.write(self.lead(id="a"))
        out = leads_mod.read()
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["map"]["state"], "unreadable")

    # ── a rate is a signal, never a price ───────────────────────────────────
    def test_a_rate_with_no_confidence_does_not_get_one(self):
        self.write(self.lead(id="a", rate={"signal": "$8-12k/mo"}))
        row = leads_mod.read()["leads"][0]
        self.assertEqual(row["rate_signal"], "$8-12k/mo")
        self.assertEqual(row["rate_confidence"], "")

    # ── the draft-only rule ─────────────────────────────────────────────────
    def test_a_drafted_lead_waits_on_aria_and_never_on_them(self):
        self.write(self.lead(id="a", stage="drafted", draft="hi, saw your post"))
        row = leads_mod.read()["leads"][0]
        self.assertTrue(row["blocked_on_aria"])
        self.assertTrue(row["has_draft"])
        # the draft text itself never travels; the board says one exists
        self.assertNotIn("draft", row)

    # ── the voice profile is reported as loudly as an empty ledger ──────────
    def test_a_missing_voice_profile_is_said_out_loud(self):
        self.write(self.lead(id="a", stage="drafted"))
        self.assertEqual(leads_mod.read()["voice"]["state"], "missing")

    def test_an_empty_voice_profile_is_not_a_voice(self):
        (self.desk / "voice.md").write_text("   \n", encoding="utf-8")
        self.assertEqual(leads_mod.read()["voice"]["state"], "empty")

    def test_a_real_voice_profile_reports_when_it_was_established(self):
        (self.desk / "voice.md").write_text("short. fun. alive.\n", encoding="utf-8")
        voice = leads_mod.read()["voice"]
        self.assertEqual(voice["state"], "ok")
        self.assertEqual(voice["words"], 3)
        self.assertTrue(voice["updated"].endswith("Z"))

    # ── one bad desk never costs the whole snapshot ─────────────────────────
    def test_sections_survives_a_broken_desk(self):
        import sections
        importlib.reload(sections)
        out = sections.read_all()
        self.assertIn("leads", out)
        self.assertIn("state", out["leads"])


if __name__ == "__main__":
    unittest.main()
