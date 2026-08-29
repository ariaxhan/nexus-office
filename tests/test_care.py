"""The care fixture: a dead reader must never look like a quiet mailbox.

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

NOW = 1_800_000_000.0   # an arbitrary "now"; snapshots are stamped relative to it


def stamp(seconds_ago: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(NOW - seconds_ago, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CareBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        import sources.care as care
        self.care = importlib.reload(care)
        self.path = self.root / self.care.SNAPSHOT

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.tmp.cleanup()

    def snapshot(self, age_s=60, dry_run=False, notes=None, **extra):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        snap = {"at": stamp(age_s), "dry_run": dry_run, "sources": ["care"],
                "filed": 0, "would_file": 0, "followup": 0, "skipped_duplicate": 0,
                "error": 0, "blocked": {}, "threads": [],
                "notes": notes if notes is not None else
                ["care[hello@thinkingbrainschool.com]: 20 messages read, 9 threads, "
                 "3 unanswered, 5 answered, 2 noise"]}
        snap.update(extra)
        self.path.write_text(json.dumps(snap))

    def read(self):
        return self.care.read(now=NOW)


class States(CareBase):
    def test_never_run_is_not_quiet(self):
        data = self.read()
        self.assertEqual(data["state"], "never")
        card = self.care.card(data)
        assert_card(self, card, "never")
        self.assertEqual(card["needs"], 1)
        self.assertIn("never run", card["headline"])

    def test_a_dark_mailbox_is_named(self):
        self.snapshot(notes=["care[hello@thinkingbrainschool.com]: mailbox unreachable: tbs-mail inbox failed: token expired"])
        data = self.read()
        self.assertEqual(data["state"], "dark")
        card = self.care.card(data)
        assert_card(self, card, "dark")
        self.assertEqual(card["needs"], 1)
        self.assertIn("could not be read", card["headline"])
        self.assertIn("token expired", card["headline"])

    def test_a_stale_sweep_takes_the_headline_over_every_count(self):
        self.snapshot(age_s=3 * 3600, filed=4)
        data = self.read()
        self.assertTrue(data["stale"])
        card = self.care.card(data)
        assert_card(self, card, "stale")
        self.assertEqual(card["needs"], 1)
        self.assertIn("has not run since", card["headline"])

    def test_a_torn_snapshot_has_a_name(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json")
        card = self.care.card(self.read())
        assert_card(self, card, "torn")
        self.assertEqual(card["needs"], 1)

    def test_a_rehearsal_is_not_a_filing(self):
        self.snapshot(dry_run=True, would_file=3)
        card = self.care.card(self.read())
        assert_card(self, card, "dry")
        self.assertEqual(card["needs"], 0)
        self.assertIn("Rehearsal", card["headline"])
        self.assertIn("none were", card["headline"])

    def test_errors_and_holds_raise_a_hand(self):
        self.snapshot(error=2)
        card = self.care.card(self.read())
        self.assertEqual(card["needs"], 1)
        self.assertIn("could not be drafted", card["headline"])
        self.snapshot(blocked={"blocked_on_identity": 3})
        card = self.care.card(self.read())
        self.assertEqual(card["needs"], 1)
        self.assertIn("held back", card["headline"])

    def test_a_healthy_sweep_counts_and_lists_threads(self):
        self.snapshot(filed=2, followup=1, threads=[
            {"title": "문의드립니다 <p@naver.com>", "date": "2026-08-29", "outcome": "filed", "detail": "#7"},
            {"title": "[웹문의] 이은경 <k@naver.com>", "date": "2026-08-29", "outcome": "followup", "detail": "#5: 1 new message(s)"},
        ])
        data = self.read()
        self.assertEqual(data["state"], "ok")
        self.assertEqual(data["unanswered"], 3)
        card = self.care.card(data)
        assert_card(self, card, "ok")
        self.assertEqual(card["needs"], 0)
        self.assertIn("3 threads waiting", card["headline"])
        self.assertIn("3 reached tbs-care", card["headline"])
        self.assertEqual(len(card["rows"]), 2)
        self.assertEqual(card["rows"][0]["badge"], "filed")
        labels = [f["label"] for f in card["facts"]]
        self.assertIn("threads waiting", labels)
        self.assertIn("last sweep", labels)

    def test_a_clear_inbox_says_so_without_an_alarm(self):
        self.snapshot(notes=["care[hello@thinkingbrainschool.com]: 4 messages read, 2 threads, 0 unanswered, 2 answered, 0 noise"])
        card = self.care.card(self.read())
        self.assertEqual(card["needs"], 0)
        self.assertIn("clear", card["headline"])


if __name__ == "__main__":
    unittest.main()
