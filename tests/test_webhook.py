"""The one public path, and the loop it must not run itself into.

Three things in here can be wrong in a way that costs more than a confusing
screen, and they are what this file is about.

A signature check that passes for a body nobody signed puts a pipeline trigger
on the open internet. So the check is tested against GitHub's own documented
vector, against the wrong secret, and against a body altered after signing.

A trigger that fires on the pipeline's own comments is a machine talking to
itself for as long as GitHub will deliver. So the two refusals are tested from
both sides: our login, and our marker.

A delivery handled twice runs an agent twice. GitHub delivers at least once and
retries anything that is not a 2xx, so the seen set is tested for dedup and for
its bound.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))
sys.path.insert(1, str(pathlib.Path(__file__).resolve().parents[1]))

import webhook as wh  # noqa: E402


# ── fixtures, in the shapes GitHub documents ─────────────────────────────────
# Written out rather than recorded, and cut down to the fields this code reads.
# A recorded payload is 30KB of things that will change; these are the ten facts
# the office decides with, which is exactly what makes a change to them visible.

def issue_comment(login="somebody", body="what about the other case?",
                  number=42, repo="acme/thing", action="created"):
    return {
        "action": action,
        "issue": {"number": number, "title": "the thing", "body": "please"},
        "comment": {"body": body, "user": {"login": login}},
        "repository": {"full_name": repo},
        "sender": {"login": login},
    }


def issues(login="somebody", body="please do the thing", number=42,
           repo="acme/thing", action="opened"):
    return {
        "action": action,
        "issue": {"number": number, "title": "the thing", "body": body},
        "repository": {"full_name": repo},
        "sender": {"login": login},
    }


def pull_request(action="closed", merged=True, number=11, repo="acme/thing",
                 body="Closes #42", login="ariaxhan"):
    return {
        "action": action,
        "number": number,
        "pull_request": {"number": number, "title": "do the thing", "body": body,
                         "merged": merged, "head": {"ref": "pipeline/auto-issue-42"}},
        "repository": {"full_name": repo},
        "sender": {"login": login},
    }


def ping(repo="acme/thing"):
    return {"zen": "Non-blocking is better than blocking.", "hook_id": 1,
            "repository": {"full_name": repo}, "sender": {"login": "ariaxhan"}}


def patch_env(**values):
    """Set env vars for one test; .stop() puts back exactly what was there."""
    import os
    saved = {k: os.environ.get(k) for k in values}
    for k, v in values.items():
        os.environ[k] = v

    class _Stop:
        @staticmethod
        def stop():
            for k, old in saved.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old
    return _Stop


class VerifyTest(unittest.TestCase):
    """The whole security model of the public path is this function."""

    # GitHub's own documented example. A vector from the other side of the wire
    # is the only thing that proves this agrees with the sender; a signature
    # this file both makes and checks would pass while being wrong.
    SECRET = b"It's a Secret to Everybody"
    BODY = b"Hello, World!"
    SIG = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"

    def test_githubs_own_vector(self):
        self.assertTrue(wh.verify(self.SECRET, self.BODY, self.SIG))
        self.assertEqual(wh.sign(self.SECRET, self.BODY), self.SIG)

    def test_the_wrong_secret_does_not_pass(self):
        self.assertFalse(wh.verify(b"not it", self.BODY, self.SIG))

    def test_a_body_altered_after_signing_does_not_pass(self):
        self.assertFalse(wh.verify(self.SECRET, b"Hello, World!!", self.SIG))
        self.assertFalse(wh.verify(self.SECRET, b"", self.SIG))

    def test_no_secret_never_passes_anything(self):
        """Unsigned is never accepted. A receiver that falls open when it is
        unconfigured is a public endpoint that runs a pipeline for strangers."""
        self.assertFalse(wh.verify(b"", self.BODY, self.SIG))
        self.assertFalse(wh.verify(b"", self.BODY, wh.sign(b"", self.BODY)))

    def test_a_header_that_is_not_a_sha256_is_refused(self):
        good = wh.sign(self.SECRET, self.BODY)
        for header in ("", "   ", good[7:], "sha1=" + good[7:], "sha256=", "sha256=zz",
                       "SHA256=" + good[7:], "sha256=" + good[7:].upper()):
            self.assertFalse(wh.verify(self.SECRET, self.BODY, header), repr(header))

    def test_a_non_ascii_header_is_refused_rather_than_raising(self):
        """compare_digest raises on non-ASCII str. A crash here is a 500 on a
        public path, which is a free way to make noise in somebody's logs."""
        self.assertFalse(wh.verify(self.SECRET, self.BODY, "sha256=ü" * 8))

    def test_the_raw_bytes_are_what_is_signed(self):
        """`{"a":1}` and `{"a": 1}` are the same object and different messages.
        Signing a re-serialised parse would accept a body nobody sent."""
        tight, loose = b'{"a":1}', b'{"a": 1}'
        self.assertEqual(json.loads(tight), json.loads(loose))
        self.assertFalse(wh.verify(self.SECRET, loose, wh.sign(self.SECRET, tight)))


class ParseTest(unittest.TestCase):
    def test_a_comment_carries_its_author_and_its_issue(self):
        ev = wh.parse("issue_comment", "d1", issue_comment(login="tim"))
        self.assertEqual((ev.event, ev.action, ev.repo), ("issue_comment", "created", "acme/thing"))
        self.assertEqual((ev.number, ev.login, ev.delivery), (42, "tim", "d1"))
        self.assertFalse(ev.body_marker)
        self.assertFalse(ev.merged)
        self.assertTrue(ev.at.endswith("Z"))

    def test_the_comment_author_wins_over_the_sender(self):
        body = issue_comment(login="tim")
        body["sender"]["login"] = "somebody-else"
        self.assertEqual(wh.parse("issue_comment", "d", body).login, "tim")

    def test_an_issue_carries_its_number(self):
        ev = wh.parse("issues", "d2", issues(login="aria", number=7))
        self.assertEqual((ev.event, ev.action, ev.number, ev.login), ("issues", "opened", 7, "aria"))

    def test_a_merged_pull_request_says_so_and_names_what_it_closes(self):
        ev = wh.parse("pull_request", "d3", pull_request(body="Fixes #42\n\nand so on"))
        self.assertEqual((ev.event, ev.action, ev.number), ("pull_request", "closed", 11))
        self.assertTrue(ev.merged)
        self.assertEqual(ev.closes, 42)

    def test_a_closed_pull_request_that_did_not_merge_is_not_merged(self):
        ev = wh.parse("pull_request", "d", pull_request(merged=False))
        self.assertFalse(ev.merged)

    def test_merged_is_only_read_on_closed(self):
        """GitHub sends `merged: false` on every other action, and a `merged`
        flag read on `synchronize` would be a receipt for a PR still open."""
        ev = wh.parse("pull_request", "d", pull_request(action="synchronize", merged=True))
        self.assertFalse(ev.merged)

    def test_a_ping_parses_and_carries_nothing_to_act_on(self):
        ev = wh.parse("ping", "d4", ping())
        self.assertEqual(ev.event, "ping")
        self.assertIsNone(ev.number)

    def test_anything_else_is_none(self):
        for event in ("push", "star", "workflow_run", "", "issue_comments", "Issues"):
            self.assertIsNone(wh.parse(event, "d", issue_comment()), event)
        # Surrounding space is a header artefact, not a different event.
        self.assertIsNotNone(wh.parse(" issue_comment ", "d", issue_comment()))
        self.assertIsNone(wh.parse("issues", "d", None))
        self.assertIsNone(wh.parse("issues", "d", []))

    def test_a_repo_that_is_not_owner_slash_name_is_refused_at_the_door(self):
        """This name reaches `gh` and the local repo map later on. It stops
        here, where refusing it costs nothing."""
        for repo in ("", "nope", "a/b/c", "../../etc/passwd", "a b/c"):
            self.assertIsNone(wh.parse("issues", "d", issues(repo=repo)), repr(repo))

    def test_the_marker_is_read_off_a_comment_and_off_an_issue(self):
        self.assertTrue(wh.parse("issue_comment", "d",
                                 issue_comment(body=f"{wh.BOT_MARKER}: I opened a PR")).body_marker)
        self.assertTrue(wh.parse("issues", "d",
                                 issues(body=f"filed by {wh.BOT_MARKER}")).body_marker)

    def test_a_pull_request_body_is_never_read_for_the_marker(self):
        """The pipeline writes its own PR bodies. Reading the marker off one
        would suppress the merge, which is the single event this whole path
        exists to hear about."""
        ev = wh.parse("pull_request", "d",
                      pull_request(body=f"Closes #42\n\n{wh.BOT_MARKER} opened this"))
        self.assertFalse(ev.body_marker)
        self.assertTrue(wh.should_trigger(ev, {"ariaxhan"}))


class TriggerRuleTest(unittest.TestCase):
    OURS = {"ariaxhan", "pipeline-bot"}

    def test_our_own_comment_never_triggers(self):
        ev = wh.parse("issue_comment", "d", issue_comment(login="ariaxhan"))
        self.assertFalse(wh.should_trigger(ev, self.OURS))
        # and case never rescues it
        ev = wh.parse("issue_comment", "d", issue_comment(login="AriaXhan"))
        self.assertFalse(wh.should_trigger(ev, self.OURS))

    def test_a_comment_carrying_our_marker_never_triggers(self):
        """The second lock. A bot whose token this process never sees is not in
        the login list, and its words still must not start a run."""
        ev = wh.parse("issue_comment", "d",
                      issue_comment(login="some-app[bot]",
                                    body=f"{wh.BOT_MARKER}: waiting on you"))
        self.assertFalse(wh.should_trigger(ev, self.OURS))

    def test_a_human_comment_triggers(self):
        ev = wh.parse("issue_comment", "d", issue_comment(login="tim"))
        self.assertTrue(wh.should_trigger(ev, self.OURS))

    def test_a_ping_never_triggers(self):
        self.assertFalse(wh.should_trigger(wh.parse("ping", "d", ping()), self.OURS))

    def test_a_merged_pull_request_triggers_even_though_we_merged_it(self):
        """The office merges as one of our own logins. Suppressing our logins
        on a PR would drop the one event that matters most."""
        ev = wh.parse("pull_request", "d", pull_request(login="ariaxhan"))
        self.assertTrue(wh.should_trigger(ev, self.OURS))

    def test_only_the_four_pull_request_actions_trigger(self):
        for action in ("closed", "opened", "reopened", "synchronize"):
            ev = wh.parse("pull_request", "d", pull_request(action=action, merged=False))
            self.assertTrue(wh.should_trigger(ev, self.OURS), action)
        for action in ("labeled", "assigned", "review_requested", "edited", ""):
            ev = wh.parse("pull_request", "d", pull_request(action=action, merged=False))
            self.assertFalse(wh.should_trigger(ev, self.OURS), action)

    def test_nothing_triggers_on_nothing(self):
        self.assertFalse(wh.should_trigger(None, self.OURS))


class MailboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.box = wh.Mailbox(self.dir)

    def test_a_delivery_is_seen_only_after_it_is_remembered(self):
        self.assertFalse(self.box.seen("abc"))
        self.box.remember("abc")
        self.assertTrue(self.box.seen("abc"))
        self.assertFalse(self.box.seen("def"))

    def test_the_seen_set_survives_the_process(self):
        """The only dedup key that outlives a restart. Without this, a crash
        mid-run turns GitHub's redelivery into a second agent."""
        self.box.remember("abc")
        self.assertTrue(wh.Mailbox(self.dir).seen("abc"))

    def test_remembering_twice_does_not_grow_it(self):
        self.box.remember("abc")
        self.box.remember("abc")
        self.assertEqual(self.box.count(), 1)

    def test_the_seen_set_is_bounded_and_keeps_the_newest(self):
        for i in range(wh.SEEN_MAX + 50):
            self.box.remember(f"d{i}")
        self.assertEqual(self.box.count(), wh.SEEN_MAX)
        self.assertFalse(self.box.seen("d0"), "the oldest fell off")
        self.assertTrue(self.box.seen(f"d{wh.SEEN_MAX + 49}"), "the newest is kept")
        fresh = wh.Mailbox(self.dir)
        self.assertEqual(fresh.count(), wh.SEEN_MAX, "and the bound is on disk, not just in ram")

    def test_an_unreadable_seen_set_reads_as_empty_rather_than_refusing(self):
        (self.dir / wh.SEEN_FILE).write_text("{not json")
        box = wh.Mailbox(self.dir)
        self.assertFalse(box.seen("abc"))
        box.remember("abc")
        self.assertTrue(box.seen("abc"))

    def test_a_refused_delivery_is_handled_fresh_next_time(self):
        """GitHub sends the SAME id when a person presses redeliver. Dropping
        everything already seen would turn the one recovery control into a
        no-op, which is exactly when it gets pressed."""
        self.assertTrue(self.box.claim("abc"))
        self.box.settle("abc", False)
        self.assertFalse(self.box.seen("abc"))
        self.assertTrue(self.box.claim("abc"), "the redeliver button still works")
        self.box.settle("abc", True)
        self.assertTrue(self.box.seen("abc"))
        self.assertFalse(self.box.claim("abc"), "and now it is a duplicate")

    def test_a_delivery_in_flight_cannot_be_claimed_twice(self):
        """Two retries can land at once. A check that is not also a claim lets
        both of them through."""
        self.assertTrue(self.box.claim("abc"))
        self.assertFalse(self.box.claim("abc"))

    def test_a_refusal_is_never_written_down(self):
        """The set is bounded, so anything an unsigned poster could add to it
        they could use to evict the real entries and make the next redelivery
        run everything twice. Absence IS the record of a refusal."""
        for i in range(50):
            self.box.claim(f"junk{i}")
            self.box.settle(f"junk{i}", False)
        self.assertEqual(self.box.count(), 0)

    def test_the_outcome_travels_to_disk(self):
        self.box.remember("abc")
        self.assertEqual(wh.Mailbox(self.dir).outcome("abc"), "ok")
        self.assertIsNone(wh.Mailbox(self.dir).outcome("never-arrived"))

    def test_an_empty_delivery_id_is_never_seen_and_never_stored(self):
        self.box.remember("")
        self.assertEqual(self.box.count(), 0)
        self.assertFalse(self.box.seen(""))

    def test_events_are_one_line_each_and_carry_whether_they_fired(self):
        ev = wh.parse("issue_comment", "d1", issue_comment())
        self.box.append(ev, trigger=True)
        self.box.append(wh.parse("ping", "d2", ping()), trigger=False)
        rows = self.box.last_events(10)
        self.assertEqual([r["delivery"] for r in rows], ["d1", "d2"])
        self.assertTrue(rows[0]["trigger"])
        self.assertFalse(rows[1]["trigger"])
        self.assertEqual(rows[0]["repo"], "acme/thing")

    def test_the_event_log_is_trimmed_at_the_ceiling_not_at_the_keep(self):
        path = self.dir / wh.EVENTS_FILE
        path.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(wh.EVENTS_MAX - 1)))
        self.box.append(wh.parse("ping", "x", ping()))
        self.assertEqual(len(path.read_text().splitlines()), wh.EVENTS_MAX,
                         "at the ceiling it is left alone")
        self.box.append(wh.parse("ping", "y", ping()))
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), wh.EVENTS_KEEP)
        self.assertEqual(json.loads(lines[-1])["delivery"], "y", "the newest survives")

    def test_runs_are_their_own_file(self):
        self.box.record_run({"at": "2026-08-27T10:00:00Z", "repo": "acme/thing", "rc": 0})
        self.assertEqual(self.box.last_runs(5)[0]["rc"], 0)
        self.assertEqual(self.box.last_events(5), [], "and never mixed with events")


class FakeRunner:
    """The Tower handoff without a ledger. Records what it was handed."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
        self.lock = threading.Lock()

    def __call__(self, repo, events):
        with self.lock:
            self.calls.append((repo, [ev.delivery for ev in events]))
        if self.fail:
            raise self.fail
        return "handed to Tower"


class TowerFixture:
    """A real Nexus ledger and a registry with one Tower-owned repo, all disposable."""

    def make_tower(self, root):
        from nexus.ledger import Ledger
        self.ledger = root / "ledger.sqlite"
        Ledger(str(self.ledger)).close()
        checkout = root / "thing-checkout"
        checkout.mkdir()
        row = dict(repo="acme/thing", path=str(checkout), enabled=True, provider="local", account="t",
                   executor=["true"], verify=["true"], risk={"paths": ["*"]})
        self.registry = root / "registry.json"
        self.registry.write_text(json.dumps({"repositories": [row, dict(row, repo="acme/other", path=None)]}))

    def requested(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            return [(s, json.loads(p), src) for s, p, src in db.execute(
                "SELECT subject,payload,source FROM events WHERE kind='work.discovery_requested' ORDER BY id")]


class HandoffFixtureError(RuntimeError):
    pass


class TriggerTest(TowerFixture, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.box = wh.Mailbox(self.dir)
        self.runner = FakeRunner()
        self.receipts = self.dir / "receipts.jsonl"
        self.refreshed = []
        wh.log = lambda m: None
        self.make_tower(self.dir)

    def trigger(self, **over):
        kw = dict(debounce_s=0.05, runner=self.runner, receipts=self.receipts,
                  refresh=self.refreshed.append)
        kw.update(over)
        t = wh.Trigger(self.box, **kw)
        # join, not just cancel: a drainer mid-handoff still writes into the tempdir being removed
        self.addCleanup(lambda: (t.cancel(), t.thread.join(5)))
        return t

    def real(self, **over):
        """The default runner: the real handoff into this fixture's ledger."""
        return self.trigger(runner=None, ledger=over.pop("ledger", self.ledger),
                            registry=over.pop("registry", self.registry), **over)

    def until(self, fn, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if fn():
                return True
            time.sleep(0.02)
        return False

    def owed(self):
        return [ev.delivery for ev in self.box.pending_obligations()]

    # ── settlement: only a durable downstream record settles ────────────────
    def test_successful_handoff_settles_and_writes_one_discovery_request(self):
        t = self.real()
        t.notice(wh.parse("issue_comment", "d1", issue_comment(login="tim")))
        self.assertTrue(self.until(lambda: not self.owed()))
        rows = self.requested()
        self.assertEqual(len(rows), 1)
        subject, payload, source = rows[0]
        self.assertEqual((subject, source), ("acme/thing", "office-webhook"))
        self.assertEqual((payload["delivery"], payload["event"], payload["number"]), ("d1", "issue_comment", 42))
        self.assertIn("requested_at", payload)
        self.assertEqual(self.box.last_runs(5)[-1]["rc"], 0)

    def test_missing_downstream_cannot_settle(self):
        t = self.real(ledger=self.dir / "no-ledger.sqlite", requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment(login="tim")))
        self.assertTrue(self.until(lambda: t.requeued >= 1))
        self.assertEqual(self.owed(), ["d1"])
        row = self.box.last_runs(5)[0]
        self.assertEqual((row["rc"], row["attempt"]), (1, 1))
        self.assertIn("no Nexus ledger", row["note"])
        self.assertFalse((self.dir / "no-ledger.sqlite").exists(), "never creates a ledger")

    def test_missing_registry_cannot_settle(self):
        env = patch_env(OFFICE_WORK_REGISTRY="", NEXUS_WORK_REGISTRY="")
        self.addCleanup(env.stop)
        t = self.real(registry=None, requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment(login="tim")))
        self.assertTrue(self.until(lambda: t.requeued >= 1))
        self.assertEqual(self.owed(), ["d1"])

    def test_downstream_sqlite_failure_cannot_settle(self):
        broken = self.dir / "broken.sqlite"
        broken.write_bytes(b"this is not a database" * 100)
        t = self.real(ledger=broken, requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment(login="tim")))
        self.assertTrue(self.until(lambda: t.requeued >= 1))
        self.assertEqual(self.owed(), ["d1"])
        self.assertIn("ledger write failed", self.box.last_runs(5)[0]["note"])

    def test_unknown_outcome_cannot_settle_and_backs_off(self):
        t = self.trigger(runner=FakeRunner(fail=TimeoutError("mid-insert")), requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment(login="tim")))
        self.assertTrue(self.until(lambda: t.requeued >= 3))
        self.assertEqual(self.owed(), ["d1"])
        runs = self.box.last_runs(10)
        self.assertEqual([r["attempt"] for r in runs[:3]], [1, 2, 3])
        self.assertEqual([r["retry_in_s"] for r in runs[:3]], [0.05, 0.1, 0.2], "exponential")
        t.failures["acme/thing"] = 99
        self.assertEqual(t.backoff("acme/thing"), wh.RETRY_CAP_S, "capped, never a hot loop")

    def test_a_recovered_downstream_settles_the_owed_delivery(self):
        missing = self.dir / "later.sqlite"
        t = self.real(ledger=missing, requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment(login="tim")))
        self.assertTrue(self.until(lambda: t.requeued >= 1))
        self.ledger.rename(missing)
        self.ledger = missing
        self.assertTrue(self.until(lambda: not self.owed()))
        self.assertEqual([p["delivery"] for _, p, _ in self.requested()], ["d1"])

    def test_duplicate_delivery_is_one_event_and_one_settlement(self):
        ev = wh.parse("issue_comment", "dup", issue_comment(login="tim"))
        wh.handoff("acme/thing", [ev], self.ledger, self.registry)
        t = self.real()
        t.notice(ev)
        self.assertTrue(self.until(lambda: not self.owed()))
        self.assertEqual(len(self.requested()), 1)
        self.assertIn("1 already recorded", self.box.last_runs(5)[0]["note"])
        self.assertFalse(wh.Mailbox(self.dir).claim("dup"), "and redelivery stays deduplicated")

    def test_a_repo_with_no_tower_owner_settles_with_the_reason(self):
        t = self.real()
        t.notice(wh.parse("issue_comment", "d1", issue_comment(repo="acme/other")))
        self.assertTrue(self.until(lambda: not self.owed()))
        self.assertEqual(self.requested(), [])
        self.assertEqual(self.box.last_runs(5)[0]["note"], "no Tower owner for acme/other; desk refreshed")
        self.assertTrue(self.until(lambda: self.refreshed == ["acme/other"]))

    def test_restart_between_acceptance_and_handoff_hands_off_once(self):
        ev = wh.parse("issue_comment", "durable-1", issue_comment(login="tim"))
        self.box.accept(ev)  # the service stops before the drainer sees it
        self.assertFalse(wh.Mailbox(self.dir).claim(ev.delivery), "redelivery is deduplicated")
        self.real()
        self.assertTrue(self.until(lambda: not self.owed()))
        self.real()  # a second restart finds nothing owed
        time.sleep(0.3)
        self.assertEqual([p["delivery"] for _, p, _ in self.requested()], ["durable-1"])

    def test_pending_debounce_survives_restart_without_duplicate_handoff(self):
        first = self.trigger(debounce_s=30)
        for delivery in ("durable-1", "durable-2"):
            first.notice(wh.parse("issue_comment", delivery, issue_comment(login="tim")))
        first.stop()
        self.assertEqual(len(wh.Mailbox(self.dir).pending_obligations()), 2)
        self.trigger()
        self.assertTrue(self.until(lambda: len(self.runner.calls) == 1))
        self.assertTrue(self.until(lambda: not self.owed()))
        self.assertEqual(len(self.box.last_runs(10)), 1)

    def test_acceptance_failure_cannot_be_recorded_as_handled(self):
        ev = wh.parse("issue_comment", "durable-1", issue_comment(login="tim"))
        self.box.obligations_path.unlink()
        self.box.obligations_path.mkdir()
        with self.assertRaises(Exception):
            self.box.accept(ev)
        self.box.obligations_path.rmdir()
        self.assertTrue(wh.Mailbox(self.dir).claim(ev.delivery), "GitHub may retry a failed durable commit")

    # ── reconciliation of historical obligations ─────────────────────────────
    def test_reconciliation_settles_only_what_tower_later_covered(self):
        from nexus.ledger import Ledger
        from nexus import work
        ev = wh.Event(delivery="old", event="issues", action="opened", repo="acme/thing", number=7,
                      login="tim", merged=False, at="2026-09-01T00:00:00Z", body_marker=False)
        self.assertIsNone(wh.reconcile_obligation(ev, self.ledger), "nothing proves it yet")
        led = Ledger(str(self.ledger))
        self.addCleanup(led.close)
        work.capture(led, "acme/thing", dict(number=7, title="t", state="open", labels=[]))
        self.assertIn("captured acme/thing#7", wh.reconcile_obligation(ev, self.ledger))
        pr = dataclasses.replace(ev, event="pull_request", number=99)
        self.assertIsNone(wh.reconcile_obligation(pr, self.ledger))
        led.event("work.serviced", "acme/thing", {"at": time.time()}, "work")
        self.assertIn("serviced acme/thing", wh.reconcile_obligation(pr, self.ledger))
        late = dataclasses.replace(pr, at="2099-01-01T00:00:00Z")
        self.assertIsNone(wh.reconcile_obligation(late, self.ledger), "coverage must come after the delivery")

    # ── debounce, receipts, desk ─────────────────────────────────────────────
    def test_three_events_in_one_window_are_one_act(self):
        t = self.trigger()
        for i in range(3):
            t.notice(wh.parse("issue_comment", f"d{i}", issue_comment(login="tim")))
        self.assertEqual(t.queued(), ["acme/thing"], "and it says so while it waits")
        self.assertTrue(self.until(lambda: t.acts >= 1))
        time.sleep(0.3)
        self.assertEqual(t.acts, 1)
        self.assertEqual(self.runner.calls, [("acme/thing", ["d0", "d1", "d2"])])
        runs = self.box.last_runs(10)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["events"], 3)
        self.assertEqual(runs[0]["delivery"], "d2", "the newest one, not the oldest")
        self.assertEqual(runs[0]["trigger"], "webhook")
        self.assertEqual(runs[0]["rc"], 0)

    def test_two_repos_are_two_acts(self):
        t = self.trigger()
        t.notice(wh.parse("issue_comment", "d1", issue_comment(repo="acme/one")))
        t.notice(wh.parse("issue_comment", "d2", issue_comment(repo="acme/two")))
        self.assertEqual(t.queued(), ["acme/one", "acme/two"])
        self.assertTrue(self.until(lambda: t.acts >= 2))
        self.assertEqual({r for r, _ in self.runner.calls}, {"acme/one", "acme/two"})

    def test_the_desk_is_refreshed_once_per_act(self):
        t = self.trigger()
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(self.until(lambda: self.refreshed == ["acme/thing"]))

    def test_a_merged_pull_request_writes_a_receipt_for_the_issue_it_closes(self):
        t = self.trigger()
        t.notice(wh.parse("pull_request", "d1", pull_request(body="Closes #42")))
        self.assertTrue(self.until(lambda: self.receipts.exists() and self.receipts.read_text()))
        row = json.loads(self.receipts.read_text().splitlines()[0])
        self.assertEqual(row["repo"], "acme/thing")
        self.assertEqual(row["issue"], "42", "the issue that is finished, not the PR")
        self.assertEqual(row["outcome"], "landed")
        self.assertEqual(row["trigger"], "webhook")
        self.assertIn("#11 merged", row["detail"])
        self.assertTrue(row["at"].endswith("Z"))

    def test_a_merged_pull_request_that_closes_nothing_falls_back_to_its_own_number(self):
        t = self.trigger()
        t.notice(wh.parse("pull_request", "d1", pull_request(body="just a tidy-up")))
        self.assertTrue(self.until(lambda: self.receipts.exists() and self.receipts.read_text()))
        self.assertEqual(json.loads(self.receipts.read_text().splitlines()[0])["issue"], "11")

    def test_a_pull_request_that_did_not_merge_writes_no_receipt(self):
        t = self.trigger()
        t.notice(wh.parse("pull_request", "d1", pull_request(merged=False)))
        self.assertTrue(self.until(lambda: t.acts >= 1))
        self.assertFalse(self.receipts.exists())

    def test_the_receipt_is_written_before_the_handoff(self):
        seen = {}

        def slow(repo, events):
            seen["receipt_first"] = self.receipts.exists()
            return "ok"

        t = self.trigger(runner=slow)
        t.notice(wh.parse("pull_request", "d1", pull_request()))
        self.assertTrue(self.until(lambda: "receipt_first" in seen))
        self.assertTrue(seen["receipt_first"])

    def test_no_receipts_file_configured_is_not_a_crash(self):
        # `receipts=None` means "read OFFICE_RECEIPTS", and a shell that has it
        # set would send this fixture's acme/thing merge into the real wall.
        self.env = patch_env(OFFICE_RECEIPTS="")
        self.addCleanup(self.env.stop)
        t = self.trigger(receipts=None)
        t.notice(wh.parse("pull_request", "d1", pull_request()))
        self.assertTrue(self.until(lambda: t.acts >= 1))

    def test_a_retry_does_not_write_the_receipt_twice(self):
        """Receipts and the desk are about the delivery; only the handoff is retried."""
        t = self.trigger(runner=FakeRunner(fail=HandoffFixtureError("down")), requeue_s=0.05)
        t.notice(wh.parse("pull_request", "d1", pull_request()))
        self.assertTrue(self.until(lambda: t.requeued >= 3, timeout=8))
        self.assertEqual(len(self.receipts.read_text().splitlines()), 1)
        self.assertEqual(self.refreshed, ["acme/thing"], "and the desk is refetched once")

    def test_nothing_ever_runs_two_at_once(self):
        live, peak = [], []
        lock = threading.Lock()
        release = threading.Event()

        def blocking(repo, events):
            with lock:
                live.append(1)
                peak.append(len(live))
            release.wait(5)
            with lock:
                live.pop()
            return "ok"

        t = self.trigger(runner=blocking)
        for i in range(5):
            t.notice(wh.parse("issue_comment", f"d{i}", issue_comment(repo=f"acme/r{i}")))
        self.assertTrue(self.until(lambda: len(peak) >= 1))
        time.sleep(0.4)
        self.assertEqual(max(peak), 1, "one drainer, serial, always")
        release.set()
        self.assertTrue(self.until(lambda: t.acts >= 5, timeout=15))
        self.assertEqual(max(peak), 1)

    def test_a_malformed_repo_is_never_queued(self):
        t = self.trigger()
        ev = wh.Event(delivery="d", event="issues", action="opened", repo="../../etc",
                      number=1, login="tim", merged=False, at=wh.now_iso(), body_marker=False)
        t.notice(ev)
        self.assertEqual(t.queued(), [])
        time.sleep(0.2)
        self.assertEqual(t.acts, 0)

    def test_a_handoff_that_blows_up_does_not_take_the_office_with_it(self):
        t = self.trigger(runner=FakeRunner(fail=RuntimeError("no")), requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(self.until(lambda: t.acts >= 1))
        self.assertEqual(self.owed(), ["d1"], "failed handoff remains owed")
        t.notice(wh.parse("issue_comment", "d2", issue_comment(repo="acme/two")))
        self.assertTrue(self.until(lambda: t.acts >= 3), "and the next one still runs")



class SectionTest(unittest.TestCase):
    """The card a person reads from across the room."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.was_state, self.was_trigger = wh.STATE, wh.RUNNING_TRIGGER
        wh.STATE = self.dir
        wh.RUNNING_TRIGGER = None
        wh.BAD_SIGNATURES = 0
        self.was_secret = wh.SECRET
        wh.SECRET = b"s3cret"
        self.addCleanup(self.restore)

    def restore(self):
        wh.STATE, wh.RUNNING_TRIGGER = self.was_state, self.was_trigger
        wh.SECRET = self.was_secret
        wh.BAD_SIGNATURES = 0

    def source(self, reach=None):
        """The section module, with its one call to the outside world pinned.

        `reach()` shells out to Tailscale, so left alone this whole class would
        pass or fail on whether the machine running it happens to have a Funnel
        up. A test that reads the developer's own network is not a test.
        """
        from sources import webhook as src
        was = src.reach
        self.addCleanup(lambda: setattr(src, "reach", was))
        answer = reach or {"state": "unknown", "detail": "not asked in tests"}
        src.reach = lambda: dict(answer)
        return src

    def test_unconfigured_says_so_and_asks_nobody_for_anything(self):
        wh.SECRET = b""
        src = self.source()
        data = src.read()
        self.assertEqual(data["state"], "unconfigured")
        card = src.card(data)
        self.assertEqual(card["headline"], "not configured")
        self.assertEqual(card["needs"], 0, "switched off is not an alarm")

    def test_configured_and_never_delivered_is_its_own_state(self):
        """A hook nobody registered looks exactly like a quiet Sunday from in
        here. It is the one a person has to act on, so it must not read the same."""
        src = self.source()
        data = src.read()
        self.assertEqual(data["state"], "silent")
        self.assertEqual(src.card(data)["needs"], 1)
        self.assertEqual(data["blocked_by"], "",
                         "nothing is proven to be blocking; the check did not complete")

    def test_no_public_path_is_not_the_same_room_as_silence(self):
        """The state this fixture went weeks without having.

        Silence with a Funnel up is a quiet Sunday. Silence with no Funnel at
        all is a door nothing can reach, and it will stay silent forever without
        anybody being told why.
        """
        src = self.source({"state": "off", "detail": "no Tailscale Funnel mount"})
        data = src.read()
        self.assertEqual(data["state"], "unreachable")
        self.assertEqual(data["public_path"], "off")
        self.assertIn("Funnel", data["blocked_by"])
        card = src.card(data)
        self.assertEqual(card["needs"], 1)
        self.assertIn("no public path", card["headline"])
        self.assertTrue(any(f["label"] == "blocked by" for f in card["facts"]),
                        "the reason has to be on the card, not only in the state")

    def test_a_door_with_deliveries_is_never_asked_about_its_path(self):
        """The proof is the deliveries. Asking Tailscale again would be a
        subprocess on every snapshot push to re-learn what the data says."""
        box = wh.Mailbox(self.dir)
        box.append(wh.parse("issue_comment", "d1", issue_comment()), trigger=True)
        asked = []
        src = self.source()
        src.reach = lambda: asked.append(1) or {"state": "off", "detail": "x"}
        data = src.read()
        self.assertEqual(data["state"], "ok")
        self.assertEqual(asked, [], "reach() must not run when events exist")
        self.assertEqual(data["public_path"], "not-asked")

    def test_a_delivered_event_shows_up_with_its_age(self):
        box = wh.Mailbox(self.dir)
        box.append(wh.parse("issue_comment", "d1", issue_comment()), trigger=True)
        box.record_run({"at": wh.now_iso(), "repo": "acme/thing", "rc": 0,
                        "trigger": "webhook", "delivery": "d1"})
        src = self.source()
        data = src.read()
        self.assertEqual(data["state"], "ok")
        self.assertEqual(data["events_today"], 1)
        self.assertEqual(data["runs_today"], 1)
        self.assertEqual(data["last_run_rc"], 0)
        card = src.card(data)
        self.assertIn("1 event today", card["headline"])
        self.assertEqual(card["needs"], 0)
        labels = {f["label"]: f["value"] for f in card["facts"]}
        self.assertEqual(labels["events today"], "1")
        self.assertIn("acme/thing", labels["last event"])
        self.assertEqual(labels["queued"], "nothing waiting")

    def test_a_run_of_refused_signatures_wants_a_person(self):
        """A secret rotated on one side only drops every delivery while the
        room looks merely quiet."""
        box = wh.Mailbox(self.dir)
        box.append(wh.parse("issue_comment", "d1", issue_comment()))
        for _ in range(wh.BAD_SIGNATURES + 5):
            wh.note_signature(False)
        src = self.source()
        card = src.card(src.read())
        self.assertEqual(card["needs"], 1)
        self.assertIn("refused signatures", {f["label"] for f in card["facts"]})

    def test_one_good_signature_clears_the_run(self):
        wh.note_signature(False)
        wh.note_signature(False)
        self.assertEqual(wh.note_signature(True), 0)

    def test_the_queue_is_read_off_the_trigger_and_is_empty_without_one(self):
        box = wh.Mailbox(self.dir)
        box.append(wh.parse("issue_comment", "d1", issue_comment()))
        src = self.source()
        self.assertEqual(src.read()["queued"], [])
        t = wh.Trigger(box, debounce_s=30, runner=lambda r, e: "ok")
        self.addCleanup(lambda: (t.cancel(), t.thread.join(5)))
        t.notice(wh.parse("issue_comment", "d2", issue_comment()))
        self.assertEqual(src.read()["queued"], ["acme/thing"])
        card = src.card(src.read())
        self.assertIn("acme/thing", {f["value"] for f in card["facts"]})

    def test_the_card_holds_the_contract_every_other_card_holds(self):
        src = self.source()
        card = src.card(src.read())
        from sources import _card
        self.assertEqual(set(card), set(_card.KEYS))
        self.assertLessEqual(len(card["headline"]), _card.HEADLINE_CHARS)
        for row in card["facts"]:
            self.assertIn(row["tone"], _card.TONES)


if __name__ == "__main__":
    unittest.main()
