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
import shutil
import sys
import tempfile
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


# A stand-in for intake.py. It prints whatever summary the test wants, so the
# parsing and the staleness rule are exercised without a vault, a network or a
# real Granola cache anywhere near it.
FAKE = """#!/usr/bin/env python3
import json, sys, time
{body}
"""


class MailBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.service = self.root / "_meta" / "services" / "intake"
        (self.service / "cache").mkdir(parents=True)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        os.environ.pop("INTAKE_STATE", None)
        # The capture watcher works OUTSIDE the vault, in iCloud and in
        # ~/Library/Logs. Without these two the tests would read Aria's real
        # phone drop folder and pass or fail on what she captured this morning.
        self.drop = self.root / "drop"
        self.log = self.root / "ingest.log"
        os.environ["OFFICE_CAPTURE_DIR"] = str(self.drop)
        os.environ["OFFICE_CAPTURE_LOG"] = str(self.log)
        # A healthy vault is the default fixture, so a test that cares about a
        # dead courier has to say so.
        self.capture(queued=0, ever=1, last="2026-08-25T18:00:00Z")
        self.granola(synced=50, last_run="2026-08-25T18:20:00+00:00")
        import sources.mail as mail
        self.mail = importlib.reload(mail)

    def tearDown(self):
        for key in ("OFFICE_RUNTIME_ROOT", "OFFICE_CAPTURE_DIR", "OFFICE_CAPTURE_LOG"):
            os.environ.pop(key, None)
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

    def capture(self, queued=0, ever=0, last="", processed=True):
        """A drop folder in exactly the shape the test needs, plus an ingest log.

        Rebuilt from nothing every time, so a test that wants an empty history
        gets one even though setUp already laid down a healthy fixture.
        """
        shutil.rmtree(self.drop, ignore_errors=True)
        self.log.unlink(missing_ok=True)
        processed_dir = self.drop / ".processed"
        (processed_dir if processed else self.drop).mkdir(parents=True, exist_ok=True)
        # iCloud bookkeeping. It is a file in the folder and it is never a
        # queued capture; counting it shows a permanent backlog of one.
        (self.drop / ".gitkeep").write_text("")
        for n in range(queued):
            (self.drop / f"queued-{n}.txt").write_text("from the phone")
        if processed:
            for n in range(ever):
                (processed_dir / f"done-{n}.txt").write_text("already moved")
        if last:
            self.log.write_text(
                "2026-08-25T17:00:00Z starting\n"
                f"{last} run complete: 1 file(s), errors=0\n")

    def granola(self, **fields):
        path = self.root / "_meta" / "granola"
        path.mkdir(parents=True, exist_ok=True)
        (path / "state.json").write_text(json.dumps(fields))

    def couriers(self):
        return {c["key"]: c for c in self.mail.read()["couriers"]}

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

class MailTest(MailBase):
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


class CourierTest(MailBase):
    """The two things that carry post into the room.

    A pigeonhole counts what arrived. Neither of these tells you that, and the
    failure they exist to catch is the one that reads as good news: a feed
    nothing is arriving on holds at zero waiting, in green, forever. The capture
    path did exactly that for 31 days.
    """

    def setUp(self):
        super().setUp()
        self.prints(self.summary())

    # -- mobile capture --------------------------------------------------------

    def test_the_queue_and_the_run_total_are_two_different_numbers(self):
        self.capture(queued=3, ever=7, last="2026-08-25T18:00:00Z")
        cap = self.couriers()["capture"]
        self.assertEqual(cap["state"], "live")
        self.assertEqual(cap["waiting"], 3)
        self.assertEqual(cap["delivered"], 7)
        self.assertEqual(cap["last_run"], "2026-08-25T18:00:00Z")

    def test_the_icloud_marker_file_is_never_a_queued_capture(self):
        # .gitkeep forces iCloud to publish the folder to the phone. Counting it
        # shows a backlog of one that no amount of processing ever clears.
        self.capture(queued=0, ever=1, last="2026-08-25T18:00:00Z")
        self.assertEqual(self.couriers()["capture"]["waiting"], 0)

    def test_nothing_ever_processed_is_never_fired_and_not_idle(self):
        # The 31-day silence. An empty queue and an empty history look the same
        # from the pigeonhole and have completely different fixes.
        self.capture(queued=0, ever=0)
        cap = self.couriers()["capture"]
        self.assertEqual(cap["state"], "never-fired")
        self.assertEqual(cap["delivered"], 0)
        self.assertIn("Shortcut", cap["why"])

    def test_a_missing_drop_folder_says_where_it_looked(self):
        os.environ["OFFICE_CAPTURE_DIR"] = str(self.root / "not-there")
        cap = self.couriers()["capture"]
        self.assertEqual(cap["state"], "missing")
        self.assertIn("not-there", cap["why"])

    def test_the_stamp_is_the_last_completed_run_not_the_last_line(self):
        self.capture(queued=0, ever=1)
        self.log.write_text(
            "2026-08-25T10:00:00Z run complete: 1 file(s), errors=0\n"
            "2026-08-25T11:00:00Z run complete: 2 file(s), errors=0\n"
            "2026-08-25T12:00:00Z scanning\n")
        self.assertEqual(self.couriers()["capture"]["last_run"], "2026-08-25T11:00:00Z")

    def test_a_missing_ingest_log_costs_the_stamp_and_not_the_courier(self):
        self.capture(queued=1, ever=2)
        cap = self.couriers()["capture"]
        self.assertEqual(cap["state"], "live")
        self.assertEqual(cap["last_run"], "")

    # -- granola sync ----------------------------------------------------------

    def test_the_sync_reports_what_it_holds_and_when_it_last_ran(self):
        self.granola(synced=51, last_run="2026-08-27T05:55:35+00:00", last_error=None)
        gran = self.couriers()["granola"]
        self.assertEqual(gran["state"], "live")
        self.assertEqual(gran["delivered"], 51)
        self.assertEqual(gran["last_run"], "2026-08-27T05:55:35+00:00")

    def test_synced_as_a_map_is_counted_rather_than_carried(self):
        # Some versions write a map of meeting id -> metadata here. Shipping it
        # raw would put 49 records where a number belongs.
        self.granola(synced={"a": {}, "b": {}, "c": {}},
                     last_run="2026-08-27T05:55:35+00:00")
        self.assertEqual(self.couriers()["granola"]["delivered"], 3)

    def test_a_failing_sync_is_failing_even_with_a_healthy_note_count(self):
        # The dangerous shape: 51 notes on disk and nothing new since Tuesday.
        self.granola(synced=51, last_run="2026-08-27T05:55:35+00:00",
                     last_error="granola: 429 rate limited")
        gran = self.couriers()["granola"]
        self.assertEqual(gran["state"], "failing")
        self.assertEqual(gran["delivered"], 51)
        self.assertIn("429", gran["error"])

    def test_a_sync_that_never_ran_is_not_a_sync_with_nothing_to_do(self):
        self.granola(synced=0)
        self.assertEqual(self.couriers()["granola"]["state"], "never-fired")

    def test_a_missing_sync_state_says_nothing_is_fetching_meetings(self):
        (self.root / "_meta" / "granola" / "state.json").unlink()
        gran = self.couriers()["granola"]
        self.assertEqual(gran["state"], "missing")
        self.assertIn("nothing is fetching", gran["why"])

    def test_a_torn_sync_state_is_unreadable_rather_than_empty(self):
        (self.root / "_meta" / "granola" / "state.json").write_text('{"synced": 5')
        self.assertEqual(self.couriers()["granola"]["state"], "unreadable")

    def test_the_live_mailbox_behind_the_sync_is_unknown_not_zero(self):
        # What granola is still holding cannot be counted from disk, and zero
        # would draw as an empty shelf that is nothing of the kind.
        self.granola(synced=51, last_run="2026-08-27T05:55:35+00:00")
        self.assertIsNone(self.couriers()["granola"]["waiting"])


class SectionsTest(unittest.TestCase):
    def test_a_raising_source_is_an_error_section_not_a_missing_one(self):
        # sections.py owns this, but the mailroom is the first source that can
        # actually fail, so it is the first place the guarantee is worth proving.
        import sections
        rt = importlib.reload(sections)
        out = rt.read_all()
        self.assertIn("mail", out)
        self.assertIn("state", out["mail"])


class CardTest(MailBase):
    def card(self):
        return self.mail.card(self.mail.read())

    def test_the_card_counts_everything_waiting_across_the_pigeonholes(self):
        self.prints(self.summary())
        self.snapshot(covered={"granola": 47})
        card = self.card()
        assert_card(self, card)
        # 3 granola still uncovered, plus the 2 captures the last run never
        # looked at. Email is unknown and is not counted as zero.
        self.assertEqual(card["headline"], "5 waiting to be filed")
        self.assertEqual(card["needs"], 5)
        self.assertEqual(card["as_of"], "2026-08-25T18:30:44Z")
        facts = {f["label"]: f["value"] for f in card["facts"]}
        self.assertEqual(facts["granola"], "3 waiting, 0 filed")
        self.assertTrue(facts["email"].startswith("unknown"))

    def test_a_stale_summary_takes_the_headline_off_the_counts(self):
        self.prints(self.summary(stale=True, stale_reason="granola: 12 of 50 on disk"))
        self.snapshot(covered={"granola": 12})
        card = self.card()
        assert_card(self, card)
        self.assertTrue(card["headline"].startswith("behind:"), card["headline"])
        self.assertIn("12 of 50", card["headline"])

    def test_an_empty_mailroom_says_so(self):
        self.prints(self.summary(sources=["granola", "capture"]))
        self.snapshot(covered={"granola": 50, "capture": 2})
        card = self.card()
        assert_card(self, card)
        self.assertEqual(card["headline"], "nothing waiting to be filed")
        self.assertEqual(card["needs"], 0)

    def test_a_stopped_courier_takes_the_headline_off_nothing_waiting(self):
        # The false-green in one test: the mailroom is clear precisely BECAUSE
        # nothing is arriving, and the card has to say the second thing.
        self.prints(self.summary(sources=["granola", "capture"]))
        self.snapshot(covered={"granola": 50, "capture": 2})
        self.capture(queued=0, ever=0)
        card = self.card()
        assert_card(self, card)
        self.assertIn("capture watcher stopped", card["headline"])
        self.assertEqual(card["needs"], 1)

    def test_a_stopped_courier_is_a_row_a_person_can_act_on(self):
        self.prints(self.summary())
        self.snapshot(covered={"granola": 50, "capture": 2})
        self.granola(synced=51, last_run="2026-08-27T05:55:35+00:00",
                     last_error="granola: 429 rate limited")
        card = self.card()
        facts = {f["label"]: f for f in card["facts"]}
        self.assertIn("granola sync", facts)
        self.assertEqual(facts["granola sync"]["tone"], "bad")

    def test_staleness_still_outranks_a_stopped_courier(self):
        # Two bad things at once. Staleness wins the headline because it means
        # every number under it describes a moment that has passed.
        self.prints(self.summary(stale=True, stale_reason="granola: 12 of 50 on disk"))
        self.capture(queued=0, ever=0)
        card = self.card()
        self.assertTrue(card["headline"].startswith("behind:"), card["headline"])

    def test_working_couriers_get_one_row_saying_the_post_is_still_coming(self):
        self.prints(self.summary(sources=["granola", "capture"]))
        self.snapshot(covered={"granola": 50, "capture": 2})
        card = self.card()
        assert_card(self, card)
        facts = {f["label"]: f["value"] for f in card["facts"]}
        self.assertIn("arriving", facts)
        self.assertIn("capture", facts["arriving"])
        self.assertIn("granola", facts["arriving"])

    def test_two_stopped_couriers_both_want_a_person(self):
        self.prints(self.summary(sources=["granola", "capture"]))
        self.snapshot(covered={"granola": 50, "capture": 2})
        self.capture(queued=0, ever=0)
        (self.root / "_meta" / "granola" / "state.json").unlink()
        self.assertEqual(self.card()["needs"], 2)

    def test_an_intake_that_would_not_run_says_that_and_wants_a_person(self):
        # Nothing installed: the mailroom cannot be counted, and an uncounted
        # mailroom must never draw as an empty one.
        card = self.card()
        assert_card(self, card)
        self.assertIn("intake", card["headline"])
        self.assertEqual(card["needs"], 1)


if __name__ == "__main__":
    unittest.main()
