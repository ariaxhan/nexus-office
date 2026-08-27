"""The window onto money-swarm, and the four ways it is allowed to be wrong.

Every test here builds its own money-swarm tree in a temp dir. The real one at
`$HOME/Documents/Vaults/money-swarm` is never read and never written: a test that
needs the real revenue system to be in a particular mood is a test that stops
running the first week it changes.

What is actually being tested is one rule, and it is not ours. money-swarm counts
what is waiting on Aria in `automation/digest.py:41-48`:

    completed_ids = {
        row["action_id"] for row in outcomes
        if ((row.get("kind") == "action_completed" and row.get("status") == "completed")
            or (row.get("kind") == "action_superseded" and row.get("status") == "superseded"))
        and row.get("action_id")
    }
    open_actions = [row for row in actions if row["id"] not in completed_ids]
    aria = [row for row in open_actions if row.get("requires_aria")]

and it prints that as "Decisions requiring Aria". The second rule is in
`automation/policy.py:196-200`:

    if kind in RING1_KINDS:
        envelope = _live_envelope(now)
        if envelope is None:
            reasons.append("ring1 kind with no live signed envelope; escalate")
            return {"ring": 1, "escalate": True, ...}

so an identity-bearing action escalates to a person whatever its own flags claim.
"Decided" is `automation/approval.py::_matching_from_rows`: approved, on this
action id, on the sha256 of this exact draft, unconsumed, unexpired.

The tests that matter are the ones where the office would rather show a calm
empty board: a ledger that will not parse, a run that stopped, a validate gone
red. Every one of those has to be loud.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import datetime
import hashlib
import importlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


def _iso(seconds_ago: float = 0) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - datetime.timedelta(seconds=seconds_ago)).isoformat()


def _hash(payload) -> str:
    """approval.py::payload_hash, so a test approval binds the way a real one does."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def action(action_id: str, **over) -> dict:
    """One row in the shape agents.py::action builds, which is what is on disk."""
    row = {
        "id": action_id,
        "agent": "prospect-signal-agent",
        "subject_id": "signal-1",
        "kind": "research-trigger",
        "summary": "Re-verify the trigger and prepare a draft; do not send.",
        "state": "research_required",
        "requires_aria": False,
        "created_at": "2026-08-20",
        "evidence": ["file:prospects/signals.jsonl:1"],
        "delivery_capability": False,
    }
    row.update(over)
    return row


LANES = [
    {"id": "lane-b", "title": "Second best", "status": "selected", "bucket": "fast_cash",
     "score": 40, "confidence": "medium", "probability": 0.1,
     "transaction_price_usd": 5000, "expected_value": 500},
    {"id": "lane-a", "title": "Convert the batch into interviews", "status": "selected",
     "bucket": "fast_cash", "score": 55, "confidence": "high", "probability": 0.02,
     "transaction_price_usd": 200000, "expected_value": 4000},
]


class Tree:
    """A money-swarm tree under a temp root, written by the test and nobody else."""

    def __init__(self, root: pathlib.Path):
        self.swarm = root / "money-swarm"
        for sub in ("state", "state/runtime", "metrics", "opportunities"):
            (self.swarm / sub).mkdir(parents=True, exist_ok=True)

    def write(self, rel: str, text: str) -> None:
        (self.swarm / rel).write_text(text, encoding="utf-8")

    def jsonl(self, rel: str, rows, raw: str = "") -> None:
        body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows) + raw
        self.write(rel, body)

    def healthy(self, actions=(), *, red=False, run_age_s=60, validate_age_s=60,
                approvals=(), outcomes=(), promotions=(), lanes=None, torn="") -> None:
        self.jsonl("state/actions.jsonl", actions, torn)
        self.jsonl("state/approvals.jsonl", approvals)
        self.jsonl("metrics/outcomes.jsonl", outcomes)
        self.jsonl("state/promotions.jsonl", promotions)
        self.jsonl("opportunities/portfolio.jsonl",
                   LANES if lanes is None else lanes)
        self.jsonl("opportunities/current.jsonl", [])
        self.jsonl("state/run-history.jsonl", [{
            "id": "cycle-1", "at": _iso(run_age_s), "agent": "daily-synthesizer",
            "mode": "local-cycle", "validation": "PASS", "admitted": 3,
            "external_side_effects": 0,
        }])
        self.write("state/runtime/current-health.json", json.dumps({
            "updated_at": _iso(validate_age_s),
            "last_success_at": _iso(validate_age_s if not red else 900000),
            "evidence_cutoff": _iso(validate_age_s if not red else 900000),
            "red": red,
            "status": "failed" if red else "passed",
            "exit_code": 1 if red else 0,
            "run_id": "scheduler-1",
        }, sort_keys=True))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(_restore, "OFFICE_RUNTIME_ROOT",
                        os.environ.get("OFFICE_RUNTIME_ROOT"))
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        self.ms = importlib.import_module("sources.money_swarm")
        self.tree = Tree(self.root)

    def read(self):
        return self.ms.read()


# -- the five states -----------------------------------------------------------


class StateTest(Base):
    def test_no_runtime_root_is_unconfigured_and_wants_nobody(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        data = self.read()
        self.assertEqual(data["state"], "unconfigured")
        self.assertIn("OFFICE_RUNTIME_ROOT", data["detail"])
        card = self.ms.card(data)
        assert_card(self, card, "unconfigured")
        # A machine with no vault is not a broken money swarm.
        self.assertEqual(card["needs"], 0)

    def test_a_root_with_no_money_swarm_in_it_is_missing_not_empty(self):
        # No Tree written at all: the directory genuinely is not there.
        import shutil
        shutil.rmtree(self.tree.swarm)
        data = self.read()
        self.assertEqual(data["state"], "missing")
        self.assertIn("money-swarm", data["path"])
        self.assertEqual(data["waiting_count"], 0)
        card = self.ms.card(data)
        assert_card(self, card, "missing")
        self.assertEqual(card["needs"], 0)

    def test_a_queue_that_will_not_parse_at_all_is_unreadable_never_a_calm_zero(self):
        self.tree.healthy()
        self.tree.write("state/actions.jsonl", "{not json\n[also not\nnope\n")
        data = self.read()
        self.assertEqual(data["state"], "unreadable")
        self.assertEqual(data["torn"], 3)
        # The specific lie this refuses: "nothing waits on you", off a file
        # nobody can read.
        self.assertNotIn("nothing waits", data["detail"])
        card = self.ms.card(data)
        assert_card(self, card, "unreadable")
        self.assertEqual(card["needs"], 1)

    def test_a_queue_directory_that_will_not_open_is_unreadable(self):
        self.tree.healthy()
        path = self.tree.swarm / "state" / "actions.jsonl"
        path.unlink()
        path.mkdir()  # a directory where a file should be: open() raises OSError
        data = self.read()
        self.assertEqual(data["state"], "unreadable")
        self.assertIn("would not read", data["detail"])

    def test_a_run_older_than_its_max_age_is_stale_and_says_how_old(self):
        self.tree.healthy([action("act-1")], run_age_s=self.ms.MAX_RUN_AGE_S + 3600)
        data = self.read()
        self.assertEqual(data["state"], "stale")
        self.assertIn("last run was", data["detail"])
        # The rows are still there. A stale board is never an empty board.
        self.assertEqual(data["counts"]["research_required"], 1)
        self.assertEqual(len(data["lanes"]), 2)
        card = self.ms.card(data)
        assert_card(self, card, "stale-run")
        self.assertTrue(card["headline"].startswith("stale:"))
        self.assertEqual(card["needs"], 1)

    def test_a_red_health_file_is_stale_even_when_the_run_is_fresh(self):
        # scheduler.py::write_current_health is written on success AND failure,
        # and run-history only gains a row when validate PASSED. So a fresh
        # run-history row plus red health is exactly the false green.
        self.tree.healthy([action("act-1")], red=True)
        data = self.read()
        self.assertEqual(data["state"], "stale")
        self.assertFalse(data["last_validate"]["ok"])
        self.assertIn("last run failed", data["detail"])
        card = self.ms.card(data)
        assert_card(self, card, "stale-red")
        validate = _fact(card, "validate")
        self.assertEqual(validate["tone"], "bad")
        self.assertTrue(validate["value"].startswith("red"))

    def test_a_validate_older_than_its_max_age_is_stale(self):
        self.tree.healthy(run_age_s=60, validate_age_s=self.ms.MAX_VALIDATE_AGE_S + 3600)
        data = self.read()
        self.assertEqual(data["state"], "stale")
        self.assertIn("last validate was", data["detail"])

    def test_a_fresh_tree_that_validated_is_ok(self):
        self.tree.healthy([action("act-1")])
        data = self.read()
        self.assertEqual(data["state"], "ok")
        self.assertTrue(data["last_validate"]["ok"])
        self.assertRegex(data["last_run"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(data["waiting_count"], 0)


# -- the waiting rule, which is money-swarm's and not ours ----------------------


class WaitingTest(Base):
    def test_the_flagged_row_is_the_one_digest_counts(self):
        # digest.py:48 -- aria = [row for row in open_actions
        #                         if row.get("requires_aria")]
        self.tree.healthy([
            action("act-quiet"),
            action("act-loud", requires_aria=True, state="approval_required",
                   kind="job-next-action",
                   summary="Prepare the application packet for Aria review."),
        ])
        data = self.read()
        self.assertEqual(data["waiting_count"], 1)
        row = data["waiting"][0]
        self.assertEqual(row["id"], "act-loud")
        self.assertEqual(row["kind"], "job-next-action")
        self.assertIn("application packet", row["what"])
        self.assertIn("requires_aria", row["why"])
        self.assertEqual(row["since"], "2026-08-20")

    def test_approval_required_raises_the_hand_on_its_own(self):
        # agents.py:52 writes the two in lockstep
        # ("approval_required" if requires_aria else "draft"), so a row carrying
        # only one of them is already damaged and must still be seen.
        self.tree.healthy([action("act-1", state="approval_required")])
        data = self.read()
        self.assertEqual(data["waiting_count"], 1)
        self.assertIn("approval_required", data["waiting"][0]["why"])

    def test_a_ring1_kind_escalates_with_no_envelope_whatever_its_flags_say(self):
        # policy.py:196-200 -- "ring1 kind with no live signed envelope; escalate".
        # requires_aria is deliberately false: in money-swarm authority comes
        # from typed fields, never from what a row claims about itself.
        self.tree.healthy([action("act-send", kind="outreach-email",
                                  requires_aria=False, state="draft")])
        data = self.read()
        self.assertEqual(data["waiting_count"], 1)
        self.assertIn("ring 1", data["waiting"][0]["why"])
        self.assertFalse(data["envelope_live"])

    def test_a_live_signed_envelope_stops_a_ring1_row_escalating_on_that_ground(self):
        self.tree.healthy([action("act-send", kind="outreach-email")])
        self.tree.write("state/policy-envelope.json", json.dumps(
            {"signed_by": "aria", "expires_at": _iso(-3600)}))
        data = self.read()
        self.assertTrue(data["envelope_live"])
        self.assertEqual(data["waiting_count"], 0)

    def test_an_expired_or_unsigned_envelope_is_not_an_envelope(self):
        self.tree.healthy([action("act-send", kind="outreach-email")])
        for envelope in ({"signed_by": "aria", "expires_at": _iso(3600)},
                         {"signed_by": "somebody-else", "expires_at": _iso(-3600)},
                         {"expires_at": _iso(-3600)}):
            self.tree.write("state/policy-envelope.json", json.dumps(envelope))
            data = self.read()
            self.assertFalse(data["envelope_live"], envelope)
            self.assertEqual(data["waiting_count"], 1, envelope)

    def test_a_completed_or_superseded_action_stops_waiting(self):
        rows = [action("act-done", requires_aria=True, state="approval_required"),
                action("act-gone", requires_aria=True, state="approval_required"),
                action("act-open", requires_aria=True, state="approval_required")]
        self.tree.healthy(rows, outcomes=[
            {"id": "o1", "kind": "action_completed", "status": "completed",
             "action_id": "act-done"},
            {"id": "o2", "kind": "action_superseded", "status": "superseded",
             "action_id": "act-gone"},
            # A near miss: the right kind, the wrong status. Still open.
            {"id": "o3", "kind": "action_completed", "status": "attempted",
             "action_id": "act-open"},
        ])
        data = self.read()
        self.assertEqual([r["id"] for r in data["waiting"]], ["act-open"])
        self.assertEqual(data["counts"]["closed"], 2)

    def test_a_live_exact_draft_approval_means_decided(self):
        row = action("act-1", requires_aria=True, state="approval_required")
        self.tree.healthy([row], approvals=[{
            "id": "approval-1", "action_id": "act-1", "status": "approved",
            "draft_hash": _hash(row), "expires_at": _iso(-3600),
        }])
        self.assertEqual(self.read()["waiting_count"], 0)

    def test_an_approval_bound_to_a_different_draft_does_not_decide_anything(self):
        # approval.py: "Editing the draft or changing the action voids the
        # approval." A hash that does not match is not an approval of what is on
        # disk now, and money-swarm would escalate it again.
        row = action("act-1", requires_aria=True, state="approval_required")
        self.tree.healthy([row], approvals=[{
            "id": "approval-1", "action_id": "act-1", "status": "approved",
            "draft_hash": _hash({"something": "else"}), "expires_at": _iso(-3600),
        }])
        data = self.read()
        self.assertEqual(data["waiting_count"], 1)
        self.assertIn("not for this exact draft", data["waiting"][0]["why"])

    def test_a_consumed_or_expired_approval_is_not_a_live_one(self):
        row = action("act-1", requires_aria=True, state="approval_required")
        approved = {"id": "approval-1", "action_id": "act-1", "status": "approved",
                    "draft_hash": _hash(row), "expires_at": _iso(-3600)}
        for extra in ([dict(approved, expires_at=_iso(3600))],
                      [approved, {"id": "use-1", "approval_id": "approval-1",
                                  "action_id": "act-1", "status": "consumed"}],
                      [approved, {"id": "rev-1", "approval_id": "approval-1",
                                  "action_id": "act-1", "status": "revoked"}]):
            self.tree.healthy([row], approvals=extra)
            self.assertEqual(self.read()["waiting_count"], 1, extra)

    def test_the_exact_draft_travels_and_is_trimmed(self):
        long_draft = "Hi there. " * 400
        self.tree.healthy([action("act-1", kind="outreach-email", draft=long_draft)])
        row = self.read()["waiting"][0]
        self.assertEqual(len(row["draft"]), self.ms.DRAFT_CHARS)
        self.assertTrue(row["draft"].startswith("Hi there."))

    def test_a_row_with_no_draft_says_so_by_being_empty_not_by_inventing_one(self):
        self.tree.healthy([action("act-1", requires_aria=True)])
        self.assertEqual(self.read()["waiting"][0]["draft"], "")


# -- torn lines, which are never dropped ---------------------------------------


class TornTest(Base):
    def test_a_torn_line_beside_good_ones_is_counted_and_the_rest_still_read(self):
        self.tree.healthy([action("act-1", requires_aria=True)],
                          torn="{oops, half a row\n")
        data = self.read()
        self.assertEqual(data["state"], "ok")
        self.assertEqual(data["torn"], 1)
        self.assertEqual(data["waiting_count"], 1)
        card = self.ms.card(data)
        assert_card(self, card, "torn")
        self.assertEqual(_fact(card, "lines that would not parse")["value"], "1")

    def test_torn_lines_are_counted_across_every_ledger_not_just_the_queue(self):
        self.tree.healthy([action("act-1")])
        self.tree.jsonl("state/promotions.jsonl",
                        [{"id": "p1", "verdict": "promoted"}], "{torn\n")
        self.tree.jsonl("metrics/outcomes.jsonl", [], "also torn\n")
        self.assertEqual(self.read()["torn"], 2)


# -- the card ------------------------------------------------------------------


class CardTest(Base):
    def test_the_waiting_count_leads_the_headline(self):
        self.tree.healthy([action("act-1", requires_aria=True),
                           action("act-2", requires_aria=True),
                           action("act-3", requires_aria=True)])
        data = self.read()
        card = self.ms.card(data)
        assert_card(self, card, "waiting")
        self.assertEqual(card["headline"], "3 decisions wait on you")
        self.assertEqual(card["needs"], 3)

    def test_one_decision_reads_like_one_thing(self):
        self.tree.healthy([action("act-1", requires_aria=True)])
        self.assertEqual(self.ms.card(self.read())["headline"], "1 decision waits on you")

    def test_nothing_waiting_says_so_and_then_says_how_fresh_it_is(self):
        self.tree.healthy([action("act-1")], run_age_s=7200)
        card = self.ms.card(self.read())
        assert_card(self, card, "quiet")
        self.assertTrue(card["headline"].startswith("nothing waits on you"))
        self.assertIn("2 lanes scored", card["headline"])
        self.assertIn("last run 2h ago", card["headline"])
        self.assertEqual(card["needs"], 0)

    def test_a_red_validate_with_nothing_waiting_still_wants_a_person(self):
        self.tree.healthy([action("act-1")], red=True)
        card = self.ms.card(self.read())
        self.assertEqual(card["needs"], 1)

    def test_as_of_is_the_last_run_and_is_a_stamp_a_renderer_can_read(self):
        self.tree.healthy([action("act-1")])
        data = self.read()
        card = self.ms.card(data)
        self.assertEqual(card["as_of"], data["last_run"])
        self.assertRegex(card["as_of"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_the_top_lane_is_the_highest_scored_and_its_value_is_labelled_estimated(self):
        self.tree.healthy([action("act-1")])
        data = self.read()
        # portfolio.jsonl is written second-best first on purpose: the source
        # ranks, it does not trust file order.
        self.assertEqual(data["lanes"][0]["id"], "lane-a")
        ev = data["lanes"][0]["expected_value"]
        # 0.02 x $200,000 = $4,000. That is arithmetic, not a receipt, so the
        # flag is unconditional and the arithmetic travels with it.
        self.assertTrue(ev["estimate"])
        self.assertEqual(ev["value"], 4000.0)
        self.assertIn("probability 0.02", ev["basis"])

        card = self.ms.card(data)
        assert_card(self, card, "lanes")
        self.assertIn("score 55", _fact(card, "top lane")["value"])
        labelled = _fact(card, "its value, estimated (not measured)")
        self.assertEqual(labelled["value"], "$4,000.00")
        # An estimate never renders as a plain measurement, so no other row on
        # the card is allowed to carry that number bare.
        self.assertNotIn("$4,000.00", card["headline"])

    def test_confidence_is_passed_through_and_never_turned_into_a_percentage(self):
        self.tree.healthy([action("act-1")])
        self.assertEqual(self.read()["lanes"][0]["confidence"], "high")

    def test_executed_actions_and_promotions_are_counts_of_things_that_happened(self):
        self.tree.healthy(
            [action("act-1")],
            outcomes=[{"id": "o1", "kind": "external_action_executed",
                       "status": "executed_awaiting_outcome", "action_id": "act-x"},
                      {"id": "o2", "kind": "outcome_observation", "status": "terminal"}],
            promotions=[{"id": "p1", "verdict": "promoted"},
                        {"id": "p2", "verdict": "promoted"},
                        {"id": "p3", "verdict": "rejected_source_mismatch"}])
        data = self.read()
        self.assertEqual(data["actions_executed"], 1)
        self.assertEqual(data["promotions"], {"promoted": 2, "rows": 3})
        card = self.ms.card(data)
        self.assertEqual(_fact(card, "actions executed, lifetime")["value"], "1")
        self.assertEqual(_fact(card, "promotions")["value"], "2 promoted of 3 examined")


# -- the contract every renderer codes against ---------------------------------


class ContractTest(Base):
    def _read_all(self):
        sections = importlib.import_module("sections")
        old = sections.SOURCES
        sections.SOURCES = [self.ms]
        try:
            return sections.read_all()[self.ms.KEY]
        finally:
            sections.SOURCES = old

    def test_the_section_is_the_read_plus_a_card_and_nothing_else(self):
        self.tree.healthy([action("act-1", requires_aria=True)])
        section = self._read_all()
        fresh = self.read()
        for field in fresh:
            self.assertIn(field, section, field)
        self.assertEqual(set(section) - set(fresh), {"card"})
        assert_card(self, section["card"], self.ms.KEY)
        self.assertEqual(section["card"]["title"], self.ms.TITLE)

    def test_every_state_produces_a_card_that_holds_the_shape(self):
        import shutil
        seen = set()

        for setup in (
            lambda: os.environ.pop("OFFICE_RUNTIME_ROOT", None),
            lambda: shutil.rmtree(self.tree.swarm, ignore_errors=True),
            lambda: self.tree.write("state/actions.jsonl", "{torn\n"),
            lambda: self.tree.healthy([action("a")], red=True),
            lambda: self.tree.healthy([action("a")]),
        ):
            os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
            self.tree = Tree(self.root)
            self.tree.healthy()
            setup()
            section = self._read_all()
            seen.add(section["state"])
            assert_card(self, section["card"], section["state"])

        self.assertEqual(seen, {"unconfigured", "missing", "unreadable", "stale", "ok"})

    def test_every_field_the_card_reads_is_present_in_every_state(self):
        needed = {"waiting", "waiting_count", "last_run", "last_validate", "lanes",
                  "lanes_scored", "counts", "torn", "actions_executed", "promotions"}
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        self.assertLessEqual(needed, set(self.read()))
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        import shutil
        shutil.rmtree(self.tree.swarm)
        self.assertLessEqual(needed, set(self.read()))


def _fact(card: dict, label: str) -> dict:
    for row in card["facts"]:
        if row["label"] == label:
            return row
    raise AssertionError(f"no fact labelled {label!r} in {[r['label'] for r in card['facts']]}")


def _restore(var: str, value) -> None:
    if value is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = value


if __name__ == "__main__":
    unittest.main()
