"""The mailroom source, and the one rule it exists to enforce.

Intake can be behind without being broken, and that is the dangerous case: every
number it prints is still a number, and rendering it as current is a lie you
cannot see. So the tests that matter here are the ones about a summary that is
NOT current, a summary that never arrived, and the three counts that must never
be added together.

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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


# A stand-in for intake.py. It prints whatever summary the test wants, so the
# parsing and the staleness rule are exercised without a vault, a network or a
# real Granola cache anywhere near it.
FAKE = """#!/usr/bin/env python3
import json, sys, time
{body}
"""


class MailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.service = self.root / "_meta" / "services" / "intake"
        (self.service / "cache").mkdir(parents=True)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        os.environ.pop("INTAKE_STATE", None)
        import sources.mail as mail
        self.mail = importlib.reload(mail)

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.tmp.cleanup()

    # -- fixtures --------------------------------------------------------------

    def intake(self, body):
        (self.service / "intake.py").write_text(FAKE.format(body=body))

    def prints(self, summary: dict):
        self.intake(f"print(json.dumps({summary!r}))")

    def snapshot(self, **fields):
        (self.service / "cache" / "last-run.json").write_text(json.dumps(fields))

    def state(self, data: dict):
        (self.service / "state.json").write_text(json.dumps(data))

    def summary(self, **over):
        base = {
            "items": 21, "would_file": 6, "filed": 3, "declined": 11,
            "blocked": {"blocked_on_identity": 2},
            "excluded_meetings": 0, "cached": 72,
            "on_disk": {"granola": 50, "capture": 2},
            "sources": ["granola"],
            "stale": False, "stale_reason": "",
            "last_run": "2026-08-25T18:30:44+00:00", "watermark": "2026-08-25",
        }
        base.update(over)
        return base

    # -- the staleness rule, which is the whole point --------------------------

    def test_a_stale_summary_says_so_and_carries_its_reason(self):
        self.prints(self.summary(stale=True, stale_reason="granola: 12 of 50 on disk"))
        out = self.mail.read()
        self.assertEqual(out["state"], "ok")
        self.assertTrue(out["stale"])
        self.assertEqual(out["stale_reason"], "granola: 12 of 50 on disk")

    def test_a_current_summary_carries_no_stale_reason(self):
        self.prints(self.summary(stale=False, stale_reason=""))
        out = self.mail.read()
        self.assertFalse(out["stale"])
        self.assertEqual(out["stale_reason"], "")

    def test_stale_never_erases_the_counts_it_qualifies(self):
        # The room subordinates them; the source must still SHIP them, or the
        # panel cannot say what the stale numbers actually were.
        self.prints(self.summary(stale=True, stale_reason="behind"))
        out = self.mail.read()
        self.assertEqual(out["counts"]["would_file"], 6)
        self.assertEqual(out["counts"]["items"], 21)

    # -- failure modes, each with its own name ---------------------------------

    def test_a_timeout_is_its_own_state_and_never_an_empty_mailroom(self):
        self.intake("time.sleep(30)")
        self.mail.TIMEOUT = 1
        try:
            out = self.mail.read()
        finally:
            self.mail.TIMEOUT = 10
        self.assertEqual(out["state"], "timeout")
        self.assertIn("did not answer", out["detail"])
        # The false-green test: a timeout must not look like "nothing waiting".
        self.assertNotIn("pigeonholes", out)
        self.assertNotIn("counts", out)

    def test_an_unparseable_summary_is_unreadable_not_empty(self):
        self.intake("print('intake: 3 items decided, 1 would file')")
        out = self.mail.read()
        self.assertEqual(out["state"], "unreadable")
        self.assertNotIn("counts", out)

    def test_a_crashing_intake_is_an_error_carrying_its_last_word(self):
        self.intake("sys.stderr.write('ImportError: no granola\\n'); sys.exit(1)")
        out = self.mail.read()
        self.assertEqual(out["state"], "error")
        self.assertIn("ImportError", out["detail"])

    def test_a_missing_intake_says_where_it_looked(self):
        out = self.mail.read()
        self.assertEqual(out["state"], "missing")
        self.assertIn("intake.py", out["detail"])

    def test_an_unconfigured_root_says_so_rather_than_looking_clear(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        rt = importlib.reload(self.mail)
        self.assertEqual(rt.read()["state"], "unconfigured")

    # -- pigeonholes -----------------------------------------------------------

    def test_email_is_unknown_rather_than_zero(self):
        # It lives on a server. Counting it from disk is impossible, and saying
        # zero would be a lie the room would draw as an empty shelf.
        self.prints(self.summary())
        hole = {h["key"]: h for h in self.mail.read()["pigeonholes"]}["email"]
        self.assertIsNone(hole["on_disk"])
        self.assertIsNone(hole["waiting"])
        self.assertIn("Unknown, not zero", hole["why"])

    def test_waiting_is_what_is_on_disk_minus_what_the_last_run_covered(self):
        self.prints(self.summary())
        self.snapshot(covered={"granola": 12})
        hole = {h["key"]: h for h in self.mail.read()["pigeonholes"]}["granola"]
        self.assertEqual(hole["on_disk"], 50)
        self.assertEqual(hole["covered"], 12)
        self.assertEqual(hole["waiting"], 38)

    def test_a_fully_covered_feed_is_genuinely_clear(self):
        self.prints(self.summary())
        self.snapshot(covered={"granola": 50})
        hole = {h["key"]: h for h in self.mail.read()["pigeonholes"]}["granola"]
        self.assertEqual(hole["waiting"], 0)
        self.assertIn("everything on disk was covered", hole["why"])

    def test_a_feed_left_out_of_the_last_run_says_that_and_not_zero(self):
        self.prints(self.summary(sources=["granola"]))
        hole = {h["key"]: h for h in self.mail.read()["pigeonholes"]}["capture"]
        self.assertFalse(hole["in_last_run"])
        self.assertEqual(hole["waiting"], 2)
        self.assertIn("not in the last run", hole["why"])

    def test_filed_is_counted_per_feed_from_the_real_ledger(self):
        self.prints(self.summary())
        self.state({"filed": {
            "a": {"source": "granola"}, "b": {"source": "granola"},
            "c": {"source": "capture"},
        }})
        holes = {h["key"]: h for h in self.mail.read()["pigeonholes"]}
        self.assertEqual(holes["granola"]["filed"], 2)
        self.assertEqual(holes["capture"]["filed"], 1)
        self.assertEqual(holes["email"]["filed"], 0)

    # -- the three counts that are three different things ----------------------

    def test_declined_rate_limited_and_blocked_stay_apart(self):
        self.prints(self.summary(declined=11, blocked={"blocked_on_identity": 2}))
        self.snapshot(rate_limited=4, no_transcript=2)
        held = self.mail.read()["held"]
        self.assertEqual(held["declined"], 11)
        self.assertEqual(held["rate_limited"], 4)
        self.assertEqual(held["no_transcript"], 2)
        self.assertEqual(held["blocked"], {"blocked_on_identity": 2})

    def test_rate_limited_survives_a_summary_that_drops_it(self):
        # intake's --summary does not carry rate_limited through. Reading it
        # straight from the run snapshot is why: a 429 that reads as zero is an
        # absence the room would draw as a clear shelf.
        self.prints(self.summary())
        self.snapshot(rate_limited=7)
        self.assertEqual(self.mail.read()["held"]["rate_limited"], 7)

    def test_a_missing_snapshot_costs_the_detail_not_the_section(self):
        self.prints(self.summary())
        out = self.mail.read()
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["held"]["rate_limited"], 0)
        self.assertIsNone(out["dry_run"])

    def test_a_torn_snapshot_does_not_take_the_mailroom_down(self):
        self.prints(self.summary())
        (self.service / "cache" / "last-run.json").write_text('{"covered": {"gran')
        out = self.mail.read()
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["held"]["rate_limited"], 0)

    # -- exclusions ------------------------------------------------------------

    def test_exclusions_arrive_as_names_not_as_a_number(self):
        # Deliberately skipped and accidentally dropped must never look the same,
        # and a bare count cannot tell you which one you are looking at.
        self.prints(self.summary(excluded_meetings=0))
        self.state({"exclude": {"meetings": [
            "1dc01aab-ec47  Career options and self-worth with mentor",
            "1e8a370a-3070  Discuss Principles + Values",
        ], "title_patterns": ["standup"]}})
        ex = self.mail.read()["excluded"]
        self.assertEqual(len(ex), 3)
        self.assertEqual(ex[0]["id"], "1dc01aab-ec47")
        self.assertEqual(ex[0]["title"], "Career options and self-worth with mentor")
        self.assertIn("/standup/", ex[2]["title"])

    def test_no_exclusions_is_an_empty_list_not_a_missing_key(self):
        self.prints(self.summary())
        self.assertEqual(self.mail.read()["excluded"], [])


class SectionsTest(unittest.TestCase):
    def test_a_raising_source_is_an_error_section_not_a_missing_one(self):
        # sections.py owns this, but the mailroom is the first source that can
        # actually fail, so it is the first place the guarantee is worth proving.
        import sections
        rt = importlib.reload(sections)
        out = rt.read_all()
        self.assertIn("mail", out)
        self.assertIn("state", out["mail"])


if __name__ == "__main__":
    unittest.main()
