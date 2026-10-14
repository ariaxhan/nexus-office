"""The Wall of Failure: plain lines, up until fixed AND verified.

The cases this file exists for are the wall's own failure modes:

  * a retirement with no verification note must NOT take a line down —
    not via the CLI (which refuses to write it) and not via a hand edit
    (the fold coerces it back up and names it)
  * the states stay apart: no file is an alarm, no root is a quiet zero,
    and a watched line is still UP — a rule existing is not a fix proven
  * a repeat leads the wall and carries its badge

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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))


def rec(rid, line, status="open", **kw):
    out = {"id": rid, "date": kw.pop("date", "2026-10-13"), "line": line,
           "detail": "", "repeat": False, "status": status, "source": ""}
    out.update(kw)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wall_file = pathlib.Path(self.tmp.name) / "wall.jsonl"
        os.environ["OFFICE_FAILURE_WALL"] = str(self.wall_file)
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        from sources import failure_wall
        self.wall = importlib.reload(failure_wall)

    def tearDown(self):
        os.environ.pop("OFFICE_FAILURE_WALL", None)
        self.tmp.cleanup()

    def write(self, records):
        self.wall_file.write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    def card(self):
        return self.wall.card(self.wall.read())


class States(Base):
    def test_unconfigured_is_a_quiet_zero(self):
        os.environ.pop("OFFICE_FAILURE_WALL")
        importlib.reload(self.wall)
        data = self.wall.read()
        self.assertEqual(data["state"], "unconfigured")
        card = self.wall.card(data)
        assert_card(self, card)
        self.assertEqual(card["needs"], 0)

    def test_missing_file_is_an_alarm_not_a_blank(self):
        data = self.wall.read()
        self.assertEqual(data["state"], "missing")
        card = self.wall.card(data)
        assert_card(self, card)
        self.assertEqual(card["needs"], 1)

    def test_empty_wall_is_ok_and_needs_nobody(self):
        self.write([])
        data = self.wall.read()
        self.assertEqual(data["state"], "ok")
        card = self.card()
        assert_card(self, card)
        self.assertEqual(card["needs"], 0)
        self.assertIn("nothing up", card["headline"])


class TheWall(Base):
    def setUp(self):
        super().setUp()
        self.write([
            rec("wf-001", "An old fault, fixed and proven.", "fixed",
                date="2026-10-01", fixed_on="2026-10-02",
                verification="checked on the surface it was about"),
            rec("wf-002", "A repeat failure, still open.", "open",
                date="2026-10-13", repeat=True),
            rec("wf-003", "A plain open failure.", "open", date="2026-10-12"),
            rec("wf-004", "A watched failure: rule exists, unproven.", "watched",
                date="2026-10-11"),
        ])

    def test_counts_stay_apart(self):
        data = self.wall.read()
        self.assertEqual(data["counts"],
                         {"up": 3, "open": 2, "watched": 1, "repeats": 1, "down": 1})

    def test_fixed_line_is_down_not_rendered(self):
        card = self.card()
        assert_card(self, card)
        titles = [r["title"] for r in card["rows"]]
        self.assertNotIn("An old fault, fixed and proven.", titles)
        self.assertEqual(card["needs"], 3)

    def test_repeat_leads_and_carries_its_badge(self):
        card = self.card()
        first = card["rows"][0]
        self.assertEqual(first["title"], "A repeat failure, still open.")
        self.assertEqual(first["badge"], "REPEAT")
        self.assertEqual(first["tone"], "bad")

    def test_watched_is_still_up(self):
        card = self.card()
        watched = [r for r in card["rows"] if r["badge"] == "WATCHED"]
        self.assertEqual(len(watched), 1)
        self.assertEqual(watched[0]["tone"], "warn")


class RetirementRule(Base):
    def test_fixed_without_verification_stays_up(self):
        self.write([rec("wf-001", "Claimed fixed, never verified.", "fixed",
                        fixed_on="2026-10-13")])
        data = self.wall.read()
        self.assertEqual(data["counts"]["up"], 1)
        self.assertEqual(data["counts"]["down"], 0)
        self.assertEqual(len(data["problems"]), 1)
        card = self.card()
        assert_card(self, card)
        self.assertEqual(card["rows"][0]["title"], "Claimed fixed, never verified.")

    def test_fixed_without_a_date_stays_up(self):
        self.write([rec("wf-001", "Verified, but no date.", "fixed",
                        verification="checked twice")])
        data = self.wall.read()
        self.assertEqual(data["counts"]["up"], 1)

    def test_last_record_wins(self):
        self.write([
            rec("wf-001", "A failure.", "open"),
            rec("wf-001", "A failure.", "fixed", fixed_on="2026-10-13",
                verification="the fix was exercised on the real surface"),
        ])
        data = self.wall.read()
        self.assertEqual(data["counts"]["down"], 1)
        self.assertEqual(data["counts"]["up"], 0)

    def test_junk_records_are_skipped_not_fatal(self):
        self.wall_file.write_text(
            'not json\n{"id": "wf-001"}\n'
            + json.dumps(rec("wf-002", "A real line.", "open")) + "\n",
            encoding="utf-8")
        data = self.wall.read()
        self.assertEqual(data["state"], "ok")
        self.assertEqual(data["counts"]["up"], 1)


class Cli(Base):
    def setUp(self):
        super().setUp()
        import wall as cli
        self.cli = importlib.reload(cli)

    def run_cli(self, *argv):
        return self.cli.main(list(argv))

    def test_add_then_retire_needs_a_verification(self):
        self.run_cli("add", "A thing broke quietly.")
        with self.assertRaises(SystemExit):
            self.run_cli("retire", "wf-001")
        data = self.wall.read()
        self.assertEqual(data["counts"]["up"], 1)  # still up: the refusal held
        self.run_cli("retire", "wf-001", "--verification",
                     "re-ran the failing case; it holds")
        data = self.wall.read()
        self.assertEqual(data["counts"]["down"], 1)

    def test_retire_unknown_id_is_an_error(self):
        self.run_cli("add", "Something.")
        with self.assertRaises(SystemExit):
            self.run_cli("retire", "wf-099", "--verification", "n/a")

    def test_add_marks_a_repeat(self):
        self.run_cli("add", "The same break again.", "--repeat")
        data = self.wall.read()
        self.assertEqual(data["counts"]["repeats"], 1)


if __name__ == "__main__":
    unittest.main()
