"""The flow pulse: eight named flows, each judged from its own receipts.

The one case this file exists for is the false green: a run that exited 0 and
did nothing. jobctl cannot see it, so the tests here feed receipts straight in
and prove that `rc 0` with a "did nothing" tail is `broken`, that the five
states stay apart, that a missing receipts file is an alarm and never a blank,
and that the tail-only read stays fast on a file full of junk.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

REGISTRY_REL = "_meta/services/jobs/registry.jsonl"
NOW = 1_800_000_000.0
H = 3600


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Base(unittest.TestCase):
    """A fake vault root with a registry, and a receipts file pointed at by
    OFFICE_JOB_RECEIPTS. No test methods here; subclasses carry them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "_meta" / "services" / "jobs").mkdir(parents=True)
        self.receipts = self.root / "receipts.jsonl"
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        os.environ["OFFICE_JOB_RECEIPTS"] = str(self.receipts)
        from sources import flows  # noqa: PLC0415
        self.flows = importlib.reload(flows)

    def tearDown(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        os.environ.pop("OFFICE_JOB_RECEIPTS", None)
        self.tmp.cleanup()

    # -- fixtures --------------------------------------------------------------

    def registry(self, states: dict | None = None, budgets: dict | None = None):
        """Every flow enabled with an 8h budget unless overridden."""
        rows = ["# header, skipped"]
        for job, _name in self.flows.FLOWS:
            budget = (budgets or {}).get(job, 8)
            rows.append(json.dumps({
                "id": job,
                "state": (states or {}).get(job, "enabled"),
                "schedule": {"kind": "interval", "seconds": 7200},
                "max_success_age_h": budget,
            }))
        (self.root / REGISTRY_REL).write_text("\n".join(rows) + "\n", encoding="utf-8")

    def receipt(self, job, started, rc=0, tail="", duration=10):
        return json.dumps({"job": job, "started": iso(started), "rc": rc,
                           "ok": rc == 0, "duration_s": duration,
                           "stderr_tail": tail + f"\n=== jobrun {job} exit {rc} ==="})

    def write_receipts(self, *lines, prefix=""):
        self.receipts.write_text(prefix + "\n".join(lines) + "\n", encoding="utf-8")

    def all_ok(self, exclude=()):
        return [self.receipt(job, NOW - 1 * H, 0, "[x] done")
                for job, _ in self.flows.FLOWS if job not in exclude]

    def by_id(self, data):
        return {f["id"]: f for f in data["flows"]}


class States(Base):

    def test_all_holding(self):
        self.registry()
        self.write_receipts(*self.all_ok())
        d = self.flows.read(now=NOW)
        self.assertEqual(d["state"], "ok")
        self.assertEqual(d["alarm"], 0)
        self.assertEqual({f["state"] for f in d["flows"]}, {"ok"})
        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "all ok")
        self.assertEqual(c["headline"], "8 flows, all holding")
        self.assertEqual(c["needs"], 0)
        self.assertEqual(len(c["facts"]), 8)
        self.assertEqual(c["as_of"], iso(NOW - H))
        self.assertTrue(all(f["tone"] == "ok" for f in c["facts"]))
        self.assertIn("1h ago", c["facts"][0]["value"])

    def test_rc0_did_nothing_is_broken(self):
        """The inbox-fill false green from 2026-08-26. rc 0, jobctl says ok."""
        self.registry()
        tail = ("**Structured MCP is not available in this session.** "
                "No task tools were found. Stopping without writing anything.\n"
                "[07:31:07] OK")
        self.write_receipts(
            *self.all_ok(exclude=("com.nexus.inbox-fill",)),
            self.receipt("com.nexus.inbox-fill", NOW - 17 * H, 0, tail),
        )
        d = self.flows.read(now=NOW)
        fl = self.by_id(d)["com.nexus.inbox-fill"]
        self.assertEqual(fl["state"], "broken")
        self.assertEqual(fl["rc"], 0)
        self.assertTrue(fl["did_nothing"])
        self.assertEqual(fl["last_ok"], "")
        # The detail is the last real line, never the runner's framing.
        self.assertEqual(fl["detail"], "[07:31:07] OK")
        self.assertEqual(d["alarm"], 1)

        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "did nothing")
        self.assertEqual(c["headline"],
                         "1 of 8 flows needs a look: Inbox did nothing (rc 0)")
        self.assertEqual(c["needs"], 1)
        self.assertEqual(c["facts"][0]["label"], "Inbox")
        self.assertEqual(c["facts"][0]["tone"], "bad")
        self.assertIn("did nothing", c["facts"][0]["value"])
        self.assertIn("17h ago", c["facts"][0]["value"])

    def test_nonzero_rc_is_broken(self):
        self.registry()
        self.write_receipts(
            *self.all_ok(exclude=("com.aria.granola-sync",)),
            self.receipt("com.aria.granola-sync", NOW - 3 * H, 0, "fine"),
            self.receipt("com.aria.granola-sync", NOW - 1 * H, 1, "Traceback\nKeyError: 'token'"),
        )
        d = self.flows.read(now=NOW)
        fl = self.by_id(d)["com.aria.granola-sync"]
        self.assertEqual(fl["state"], "broken")
        self.assertEqual(fl["rc"], 1)
        self.assertFalse(fl["did_nothing"])
        self.assertEqual(fl["detail"], "KeyError: 'token'")
        # last_ok still remembers the run before the break.
        self.assertEqual(fl["last_ok"], iso(NOW - 3 * H))
        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "rc 1")
        self.assertEqual(c["headline"], "1 of 8 flows needs a look: Granola rc 1")
        self.assertIn("rc 1", c["facts"][0]["value"])

    def test_stale_past_budget(self):
        self.registry()
        self.write_receipts(
            *self.all_ok(exclude=("com.aria.outbound-daily",)),
            self.receipt("com.aria.outbound-daily", NOW - 3 * 24 * H, 0, "sent"),
        )
        d = self.flows.read(now=NOW)
        fl = self.by_id(d)["com.aria.outbound-daily"]
        self.assertEqual(fl["state"], "stale")
        self.assertEqual(fl["age_s"], 3 * 24 * H)
        self.assertEqual(fl["budget_h"], 8)
        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "stale")
        self.assertEqual(c["headline"],
                         "1 of 8 flows needs a look: Outbound stale, budget 8h")
        self.assertEqual(c["facts"][0]["value"], "stale · 3d ago, budget 8h")
        self.assertEqual(c["facts"][0]["tone"], "bad")

    def test_null_budget_is_never_stale(self):
        self.registry(budgets={"com.aria.outbound-daily": None})
        self.write_receipts(
            *self.all_ok(exclude=("com.aria.outbound-daily",)),
            self.receipt("com.aria.outbound-daily", NOW - 30 * 24 * H, 0, "sent"),
        )
        fl = self.by_id(self.flows.read(now=NOW))["com.aria.outbound-daily"]
        self.assertEqual(fl["state"], "ok")
        self.assertEqual(fl["watch"], "nothing")

    def test_never_ran(self):
        self.registry()
        self.write_receipts(*self.all_ok(exclude=("com.nexus.money-swarm",)))
        d = self.flows.read(now=NOW)
        fl = self.by_id(d)["com.nexus.money-swarm"]
        self.assertEqual(fl["state"], "never")
        self.assertEqual(fl["last_run"], "")
        self.assertIsNone(fl["age_s"])
        self.assertEqual(d["alarm"], 1)
        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "never")
        self.assertEqual(c["facts"][0]["label"], "Money swarm")
        self.assertEqual(c["facts"][0]["value"], "never ran")
        self.assertEqual(c["facts"][0]["tone"], "warn")

    def test_off_is_not_an_alarm(self):
        self.registry(states={"com.aria.distillations": "disabled"})
        # Off wins even over a broken receipt: it is a decision, not a fault.
        self.write_receipts(
            *self.all_ok(exclude=("com.aria.distillations",)),
            self.receipt("com.aria.distillations", NOW - H, 1, "boom"),
        )
        d = self.flows.read(now=NOW)
        fl = self.by_id(d)["com.aria.distillations"]
        self.assertEqual(fl["state"], "off")
        self.assertEqual(d["alarm"], 0)
        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "off")
        self.assertEqual(c["headline"], "8 flows, all holding")
        self.assertEqual(c["needs"], 0)
        # Off sorts last, after the ok rows, with a dim tone.
        self.assertEqual(c["facts"][-1]["label"], "Distillations")
        self.assertEqual(c["facts"][-1]["value"], "off")
        self.assertEqual(c["facts"][-1]["tone"], "dim")

    def test_unregistered_flow_is_named(self):
        """A flow in FLOWS that the registry does not declare is a finding,
        not an ok row and not a crash."""
        self.registry()
        reg = self.root / REGISTRY_REL
        lines = [l for l in reg.read_text().splitlines()
                 if '"com.aria.mobile-capture"' not in l]
        reg.write_text("\n".join(lines) + "\n")
        self.write_receipts(*self.all_ok())
        d = self.flows.read(now=NOW)
        fl = self.by_id(d)["com.aria.mobile-capture"]
        self.assertEqual(fl["state"], "unregistered")
        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "unregistered")
        self.assertEqual(c["facts"][0]["value"], "not in the registry")
        self.assertEqual(c["facts"][0]["tone"], "warn")
        # And it counts. A row nobody is asked to look at is the false-green
        # this card exists to refuse.
        self.assertEqual(d["alarm"], 1)
        self.assertEqual(c["needs"], 1)
        self.assertEqual(
            c["headline"],
            "1 of 8 flows needs a look: Mobile capture not in the registry")

    def test_precedence_broken_beats_stale(self):
        """A flow whose last success is past budget AND whose last run failed
        is broken. Broken is the more specific fact and the one to fix first."""
        self.registry()
        self.write_receipts(
            *self.all_ok(exclude=("com.nexus.issue-dispatch",)),
            self.receipt("com.nexus.issue-dispatch", NOW - 2 * 24 * H, 0, "ok"),
            self.receipt("com.nexus.issue-dispatch", NOW - H, 2, "gh: rate limited"),
        )
        fl = self.by_id(self.flows.read(now=NOW))["com.nexus.issue-dispatch"]
        self.assertEqual(fl["state"], "broken")

    def test_worst_first_and_counts(self):
        self.registry(states={"com.aria.distillations": "disabled"})
        self.write_receipts(
            *self.all_ok(exclude=("com.nexus.inbox-fill", "com.aria.outbound-daily",
                                  "com.nexus.money-swarm", "com.aria.distillations")),
            self.receipt("com.nexus.inbox-fill", NOW - H, 0, "MCP not authenticated"),
            self.receipt("com.aria.outbound-daily", NOW - 3 * 24 * H, 0, "sent"),
        )
        d = self.flows.read(now=NOW)
        self.assertEqual(d["counts"], {"ok": 4, "stale": 1, "broken": 1, "never": 1,
                                       "off": 1, "unregistered": 0})
        self.assertEqual(d["alarm"], 3)
        c = self.flows.card(d, now=NOW)
        assert_card(self, c, "mixed")
        self.assertTrue(c["headline"].startswith("3 of 8 flows need a look: Inbox"))
        self.assertEqual([f["label"] for f in c["facts"]][:3],
                         ["Inbox", "Outbound", "Money swarm"])
        self.assertEqual(c["facts"][-1]["label"], "Distillations")


class Trouble(Base):

    def test_unconfigured_needs_nobody(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        d = self.flows.read()
        self.assertEqual(d["state"], "unconfigured")
        c = self.flows.card(d)
        assert_card(self, c, "unconfigured")
        self.assertEqual(c["needs"], 0)

    def test_missing_root(self):
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root / "nope")
        d = self.flows.read()
        self.assertEqual(d["state"], "missing-root")
        c = self.flows.card(d)
        assert_card(self, c, "missing-root")
        self.assertEqual(c["needs"], 1)

    def test_missing_receipts_is_an_alarm(self):
        """Blank must never be green: no receipts file means every flow went
        silent at once, and that wants a person."""
        self.registry()
        d = self.flows.read()
        self.assertEqual(d["state"], "missing-receipts")
        self.assertNotIn("flows", d)
        c = self.flows.card(d)
        assert_card(self, c, "missing-receipts")
        self.assertEqual(c["needs"], 1)
        self.assertIn("silent", c["headline"])
        self.assertEqual(c["facts"][0]["tone"], "bad")

    def test_unreadable_receipts(self):
        self.registry()
        self.receipts.mkdir()  # a directory where a file should be
        d = self.flows.read()
        self.assertEqual(d["state"], "unreadable")
        c = self.flows.card(d)
        assert_card(self, c, "unreadable")
        self.assertEqual(c["needs"], 1)

    def test_receipts_path_derives_from_jobctl_runtime_dir(self):
        os.environ.pop("OFFICE_JOB_RECEIPTS")
        os.environ["JOBCTL_RUNTIME_DIR"] = str(self.root / "rt")
        try:
            self.assertEqual(self.flows._receipts_path(),
                             self.root / "rt" / "state" / "receipts.jsonl")
        finally:
            os.environ.pop("JOBCTL_RUNTIME_DIR")
        self.assertEqual(
            self.flows._receipts_path(),
            pathlib.Path.home() / "Library" / "Application Support" / "nexus-jobs"
            / "state" / "receipts.jsonl")


class Tail(Base):

    def test_tail_only_read_on_a_junk_heavy_file(self):
        """More junk than TAIL_BYTES before the real receipts: the tail still
        parses, the real rows still land, and the whole thing is fast because
        the head is never read. Sized off the constant, so bumping it cannot
        quietly turn this into a whole-file read that still passes."""
        self.registry()
        line = json.dumps({"job": "com.other.noise", "started": iso(NOW - 99 * H),
                           "rc": 0, "ok": True, "stderr_tail": "x" * 40})
        parts, size = [], 0
        while size <= self.flows.TAIL_BYTES + 4096:
            row = line if len(parts) % 7 else "{not json at all"
            parts.append(row)
            size += len(row) + 1
        junk = "\n".join(parts) + "\n"
        self.write_receipts(*self.all_ok(), prefix=junk)
        self.assertGreater(self.receipts.stat().st_size, self.flows.TAIL_BYTES)

        t0 = time.perf_counter()
        d = self.flows.read(now=NOW)
        elapsed = time.perf_counter() - t0
        self.assertEqual(d["state"], "ok")
        self.assertEqual({f["state"] for f in d["flows"]}, {"ok"})
        self.assertLess(elapsed, 1.0, f"tail read took {elapsed:.2f}s")

    def test_torn_first_line_is_dropped(self):
        """Seeking into the middle of a line yields a torn fragment. It is
        dropped, and the receipt after it still counts."""
        self.registry()
        lines = self.all_ok()
        # Pad so the seek lands mid-line: TAIL_BYTES minus a fragment.
        real = "\n".join(lines) + "\n"
        pad = "P" * (self.flows.TAIL_BYTES - len(real) + 17) + "\n"
        self.write_receipts(*lines, prefix=pad)
        d = self.flows.read(now=NOW)
        self.assertEqual({f["state"] for f in d["flows"]}, {"ok"})

    def test_last_receipt_wins_by_started_not_file_order(self):
        self.registry()
        self.write_receipts(
            *self.all_ok(exclude=("com.aria.granola-sync",)),
            self.receipt("com.aria.granola-sync", NOW - H, 0, "fine"),
            self.receipt("com.aria.granola-sync", NOW - 5 * H, 1, "older, failed"),
        )
        fl = self.by_id(self.flows.read(now=NOW))["com.aria.granola-sync"]
        self.assertEqual(fl["state"], "ok")
        self.assertEqual(fl["last_run"], iso(NOW - H))


if __name__ == "__main__":
    unittest.main()
