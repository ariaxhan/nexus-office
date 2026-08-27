"""The automation, as one page instead of three screens and a terminal.

Two things are being protected here, and neither is "does the dict have keys".

**The link must point at what the runner actually said.** The whole reason this
page exists is that a row saying "commented on matra#284" is worthless if you
then have to go and find the comment. The URL comes from the desk's last comment,
so it is only the runner's comment while the runner still has the last word. A
human replying moves it, and a link to a human's comment labelled as the
pipeline's is a lie with an anchor on it.

**The list must not silently hide what it dropped.** 92% of the receipts file is
one `survey` line per repo per sweep, so the list drops them. A list that drops
things without saying so reads as "that is everything that happened", and this
page exists precisely to stop somebody believing that.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

import automation  # noqa: E402


def receipt(repo, issue, outcome, at, detail=""):
    return {"at": at, "repo": repo, "issue": issue, "outcome": outcome, "detail": detail}


def station(repo, issues=()):
    return {"repo": repo, "issues": list(issues)}


def issue(number, bot_last=True, url="", title="a title", at="2026-08-27T21:00:00Z"):
    return {"number": number, "title": title, "bot_last": bot_last,
            "last_word_url": url, "last_word_at": at}


IDLE = {
    "state": "ok", "detail": "nothing running; next look 43 minutes",
    "running": False, "next_in": "43 minutes", "every": "1 hour",
    "covered": {"state": "ok", "repos": 72, "receipts": 487},
}
QUIET_DOOR = {"state": "silent", "events_total": 0, "events_today": 0}


class ActivityTest(unittest.TestCase):
    """What the runner touched, and where what it said is."""

    def build(self, by_repo, stations=(), pipeline=None, webhook=None, counts=None):
        return automation.build(
            by_repo, list(stations),
            {"pipeline": dict(pipeline or IDLE), "webhook": dict(webhook or QUIET_DOOR)},
            counts or {})

    def test_a_row_links_to_the_comment_when_the_bot_still_has_the_last_word(self):
        url = "https://github.com/acme/thing/issues/284#issuecomment-77"
        page = self.build(
            {"acme/thing": [receipt("acme/thing", "284", "landed", "2026-08-27T21:00:00Z")]},
            [station("acme/thing", [issue(284, bot_last=True, url=url)])])
        row = page["activity"][0]
        self.assertEqual(row["comment_url"], url)
        self.assertEqual(row["issue_url"], "https://github.com/acme/thing/issues/284")
        self.assertEqual(row["title"], "a title")

    def test_a_human_reply_removes_the_comment_link_rather_than_moving_it(self):
        """The failure this guards: the desk's `last_word_url` is whatever the
        LAST comment is. Once a human answers, that URL is the human's comment,
        and shipping it as "what the pipeline said" would be wrong in the one
        place a person goes to check what the pipeline said."""
        page = self.build(
            {"acme/thing": [receipt("acme/thing", "284", "landed", "2026-08-27T21:00:00Z")]},
            [station("acme/thing",
                     [issue(284, bot_last=False,
                            url="https://github.com/acme/thing/issues/284#issuecomment-99")])])
        row = page["activity"][0]
        self.assertEqual(row["comment_url"], "")
        self.assertEqual(row["comment_at"], "")
        self.assertEqual(row["issue_url"], "https://github.com/acme/thing/issues/284",
                         "the thread is still reachable; only the deep link is not")

    def test_an_issue_the_office_cannot_see_still_gets_a_row_and_a_thread_link(self):
        """Closed, or on a desk that is put away. The receipt is still true."""
        page = self.build(
            {"acme/thing": [receipt("acme/thing", "12", "landed", "2026-08-27T20:00:00Z")]},
            [station("acme/thing", [])])
        row = page["activity"][0]
        self.assertEqual(row["issue_url"], "https://github.com/acme/thing/issues/12")
        self.assertEqual((row["comment_url"], row["title"]), ("", ""))

    def test_the_issue_number_is_matched_as_a_string_not_a_number(self):
        """dispatch.sh writes `"issue":"284"` and GitHub answers `284`. Comparing
        the two without saying which is which empties this whole column and
        raises nothing."""
        url = "https://github.com/acme/thing/issues/284#issuecomment-1"
        page = self.build(
            {"acme/thing": [receipt("acme/thing", "284", "landed", "2026-08-27T21:00:00Z")]},
            [station("acme/thing", [issue(284, url=url)])])
        self.assertEqual(page["activity"][0]["comment_url"], url)

    def test_sweep_receipts_are_dropped_and_the_survey_never_crowds_the_page(self):
        by_repo = {f"acme/r{i}": [receipt(f"acme/r{i}", "", "survey", "2026-08-27T21:00:00Z",
                                          "9 open: 0 to work, 9 waiting on a human")]
                   for i in range(40)}
        by_repo["acme/real"] = [receipt("acme/real", "7", "landed", "2026-08-27T21:00:00Z")]
        page = self.build(by_repo)
        self.assertEqual([r["issue"] for r in page["activity"]], ["7"])

    def test_a_receipt_naming_no_issue_is_never_a_row(self):
        page = self.build({"acme/t": [receipt("acme/t", "", "refused", "2026-08-27T21:00:00Z",
                                              "no gh token")]})
        self.assertEqual(page["activity"], [])

    def test_rows_are_newest_first_across_every_desk(self):
        page = self.build({
            "a/one": [receipt("a/one", "1", "landed", "2026-08-27T10:00:00Z")],
            "b/two": [receipt("b/two", "2", "landed", "2026-08-27T22:00:00Z")],
            "c/three": [receipt("c/three", "3", "landed", "2026-08-27T16:00:00Z")],
        })
        self.assertEqual([r["repo"] for r in page["activity"]], ["b/two", "c/three", "a/one"])

    def test_the_cap_is_said_out_loud_and_never_silently_applied(self):
        many = [receipt("a/one", str(n), "landed", f"2026-08-27T{n % 24:02d}:00:00Z")
                for n in range(automation.MAX_ACTIVITY + 15)]
        page = self.build({"a/one": many})
        self.assertEqual(len(page["activity"]), automation.MAX_ACTIVITY)
        self.assertEqual(page["activity_dropped"], 15)

    def test_every_outcome_carries_a_tone_and_a_sentence_a_person_can_read(self):
        for outcome in automation.OUTCOMES:
            if outcome in automation.PER_REPO:
                continue
            page = self.build({"a/one": [receipt("a/one", "1", outcome, "2026-08-27T21:00:00Z")]})
            row = page["activity"][0]
            self.assertIn(row["tone"], ("ok", "warn", "bad", "dim", ""))
            self.assertTrue(row["means"], f"{outcome} has no plain-words meaning")

    def test_an_outcome_nobody_has_seen_before_is_a_row_not_a_crash(self):
        page = self.build({"a/one": [receipt("a/one", "1", "invented", "2026-08-27T21:00:00Z")]})
        self.assertEqual(page["activity"][0]["outcome"], "invented")
        self.assertEqual(page["activity"][0]["means"], "")


class HeadlineTest(unittest.TestCase):
    """One sentence, and it says the worst true thing first."""

    def page(self, pipeline, activity_repo=None):
        by_repo = activity_repo or {}
        return automation.build(by_repo, [], {"pipeline": pipeline, "webhook": QUIET_DOOR}, {})

    def test_a_run_in_flight_wins_over_everything(self):
        head = self.page({**IDLE, "running": True, "doing": "acme/thing #12",
                          "kill_switch": True})["headline"]
        self.assertIn("running now", head)

    def test_a_scheduler_that_stopped_firing_says_so_rather_than_reading_idle(self):
        head = self.page({**IDLE, "overdue": True, "late_by": "3 hours",
                          "next_in": "any moment now"})["headline"]
        self.assertIn("overdue by 3 hours", head)

    def test_switched_off_is_a_decision_and_reads_like_one(self):
        head = self.page({**IDLE, "kill_switch": True})["headline"]
        self.assertIn("kill switch", head)

    def test_deferring_on_battery_is_not_an_idle_hour(self):
        """dispatch exits before doing anything on battery. Without this, an
        hourly deferral and a working idle hour read identically."""
        head = self.page({**IDLE, "deferring": True, "power": "battery"})["headline"]
        self.assertIn("battery", head)

    def test_a_pipeline_that_could_not_be_read_says_that_and_nothing_reassuring(self):
        head = self.page({"state": "missing", "detail": "no issue pipeline installed"})["headline"]
        self.assertIn("no issue pipeline installed", head)

    def test_the_headline_always_fits_on_a_card(self):
        long = {**IDLE, "running": True, "doing": "x" * 400}
        self.assertLessEqual(len(self.page(long)["headline"]), 79)


class ShapeTest(unittest.TestCase):
    """Every field is present in every return, so no renderer has to guess
    whether a missing key means "no" or means "nobody looked"."""

    BLOCKS = ("schedule", "now", "trigger", "reached")

    def test_a_pipeline_that_could_not_be_read_still_answers_every_block(self):
        page = automation.build({}, [], {"pipeline": {"state": "unconfigured"},
                                         "webhook": {}}, {})
        for block in self.BLOCKS:
            self.assertIsInstance(page[block], dict, block)
        self.assertEqual(page["activity"], [])
        self.assertEqual(page["state"], "unconfigured")
        self.assertTrue(page["how"], "the explanation is not conditional on health")

    def test_no_sections_at_all_is_still_a_page(self):
        page = automation.build({}, [], {}, {})
        self.assertEqual(page["state"], "unknown")
        for block in self.BLOCKS:
            self.assertIsInstance(page[block], dict, block)

    def test_the_door_being_unreachable_travels_to_this_page(self):
        """The office knew for weeks that nothing had arrived and never said the
        reason. The reason is the only actionable field on that block."""
        page = automation.build({}, [], {
            "pipeline": IDLE,
            "webhook": {"state": "unreachable", "blocked_by": "no Tailscale Funnel mount"},
        }, {})
        self.assertFalse(page["trigger"]["reachable"])
        self.assertIn("Funnel", page["trigger"]["blocked_by"])

    def test_what_the_sweep_reached_is_carried_not_recomputed(self):
        page = automation.build({}, [], {"pipeline": IDLE, "webhook": QUIET_DOOR}, {"landed": 3})
        self.assertEqual(page["reached"]["repos"], 72)
        self.assertEqual(page["counts"], {"landed": 3})


if __name__ == "__main__":
    unittest.main()
