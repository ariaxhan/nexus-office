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

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

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


class RemoteUrlTest(unittest.TestCase):
    def test_the_four_shapes_git_hands_back(self):
        for url in ("git@github.com:acme/thing.git",
                    "https://github.com/acme/thing",
                    "https://github.com/acme/thing.git",
                    "ssh://git@github.com/acme/thing.git"):
            self.assertEqual(wh.normalise_remote(url), "acme/thing", url)

    def test_a_trailing_slash_and_a_missing_dot_git_are_both_fine(self):
        self.assertEqual(wh.normalise_remote("https://github.com/acme/thing/"), "acme/thing")
        self.assertEqual(wh.normalise_remote("git@github.com:acme/thing"), "acme/thing")

    def test_a_remote_that_is_not_github_is_not_a_match(self):
        """This map answers "which checkout is the repo GitHub told me about".
        A GitLab remote at the same owner/name would answer it wrongly, and the
        pipeline would run against the wrong tree."""
        for url in ("git@gitlab.com:acme/thing.git", "https://bitbucket.org/acme/thing",
                    "/srv/mirrors/acme/thing.git", "", "not a url"):
            self.assertEqual(wh.normalise_remote(url), "", repr(url))


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


class RepoMapTest(unittest.TestCase):
    """owner/name to a checkout, by dispatch.sh's own two skip rules."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = pathlib.Path(self.tmp.name)

    def repo(self, rel, url):
        p = self.vault / rel
        p.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=p)
        git("remote", "add", "origin", url, cwd=p)
        return p

    def test_a_checkout_is_found_by_its_origin(self):
        want = self.repo("code/thing", "git@github.com:acme/thing.git")
        self.repo("code/other", "https://github.com/acme/other")
        m = wh.build_repo_map(self.vault)
        self.assertEqual(m["acme/thing"].resolve(), want.resolve())
        self.assertIn("acme/other", m)

    def test_a_checkout_its_parent_gitignores_is_skipped(self):
        """A vendored or cached clone. The parent repo already declared it
        disposable, so running a pipeline lane in it would be a lane in a copy."""
        parent = self.repo("outer", "https://github.com/acme/outer")
        (parent / ".gitignore").write_text("vendor/\n")
        self.repo("outer/vendor/thing", "git@github.com:acme/thing.git")
        m = wh.build_repo_map(self.vault)
        self.assertIn("acme/outer", m)
        self.assertNotIn("acme/thing", m, "the gitignored clone is not a desk's checkout")

    def test_the_origin_comes_from_the_pipelines_own_resolver(self):
        """`pipeline-config.py origin` is that subsystem's single answer to
        "where do this repo's issues live". Re-deriving it here would be a
        second answer, and the two would drift the day a convention changes."""
        fake = self.vault / "pipeline-config.py"
        fake.write_text("print('acme/renamed')\n")
        self.repo("code/thing", "git@github.com:acme/thing.git")
        m = wh.build_repo_map(self.vault, fake)
        self.assertIn("acme/renamed", m)
        self.assertNotIn("acme/thing", m, "the resolver wins over the URL")

    def test_without_the_resolver_the_remote_is_read_directly(self):
        """A machine with no pipeline installed still gets a working map."""
        self.repo("code/thing", "git@github.com:acme/thing.git")
        m = wh.build_repo_map(self.vault, self.vault / "not-installed.py")
        self.assertIn("acme/thing", m)

    def test_a_resolver_that_prints_nonsense_is_ignored(self):
        fake = self.vault / "pipeline-config.py"
        fake.write_text("print('../../etc/passwd')\n")
        self.repo("code/thing", "git@github.com:acme/thing.git")
        self.assertEqual(wh.build_repo_map(self.vault, fake), {})

    def test_a_repo_with_no_github_origin_is_not_in_the_map(self):
        self.repo("code/elsewhere", "git@gitlab.com:acme/elsewhere.git")
        self.assertEqual(wh.build_repo_map(self.vault), {})

    def test_a_vault_that_is_not_there_is_an_empty_map_not_a_crash(self):
        self.assertEqual(wh.build_repo_map(self.vault / "nope"), {})


class FakeRunner:
    """dispatch.sh without dispatch.sh. Records what it was asked to run."""

    def __init__(self, rc=0):
        self.calls = []
        self.rc = rc
        self.lock = threading.Lock()

    def __call__(self, path):
        with self.lock:
            self.calls.append(pathlib.Path(path))
        return self.rc, 0.4, ["a line", "another line"]


class TriggerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.box = wh.Mailbox(self.dir)
        self.runner = FakeRunner()
        self.receipts = self.dir / "receipts.jsonl"
        self.refreshed = []
        wh.log = lambda m: None

    def trigger(self, **over):
        kw = dict(debounce_s=0.05, runner=self.runner, receipts=self.receipts,
                  refresh=self.refreshed.append)
        kw.update(over)
        t = wh.Trigger(self.box, **kw)
        t.local_path = lambda nwo: self.dir / "checkout" / nwo.replace("/", "-")
        self.addCleanup(t.cancel)
        return t

    def until(self, fn, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if fn():
                return True
            time.sleep(0.02)
        return False

    def test_three_events_in_one_window_are_one_act(self):
        """A push, a PR and a comment inside two seconds are the same news.
        Without the window that is three lanes racing over one working tree."""
        t = self.trigger()
        for i in range(3):
            t.notice(wh.parse("issue_comment", f"d{i}", issue_comment(login="tim")))
        self.assertEqual(t.queued(), ["acme/thing"], "and it says so while it waits")
        self.assertTrue(self.until(lambda: t.acts >= 1))
        time.sleep(0.3)
        self.assertEqual(t.acts, 1)
        self.assertEqual(len(self.runner.calls), 1)
        runs = self.box.last_runs(10)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["events"], 3)
        self.assertEqual(runs[0]["delivery"], "d2", "the newest one, not the oldest")
        self.assertEqual(runs[0]["trigger"], "webhook")
        self.assertEqual(runs[0]["rc"], 0)
        self.assertEqual(runs[0]["log"], ["a line", "another line"])

    def test_two_repos_are_two_acts(self):
        t = self.trigger()
        t.notice(wh.parse("issue_comment", "d1", issue_comment(repo="acme/one")))
        t.notice(wh.parse("issue_comment", "d2", issue_comment(repo="acme/two")))
        self.assertEqual(t.queued(), ["acme/one", "acme/two"])
        self.assertTrue(self.until(lambda: t.acts >= 2))
        self.assertEqual({p.name for p in self.runner.calls}, {"acme-one", "acme-two"})

    def test_the_desk_is_refreshed_once_per_act(self):
        t = self.trigger()
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(self.until(lambda: self.refreshed == ["acme/thing"]))

    def test_a_repo_with_no_checkout_still_refreshes_its_desk(self):
        """The news came from GitHub and a desk is a picture of GitHub. Not
        every repo you can push to lives under this vault."""
        t = self.trigger()
        t.local_path = lambda nwo: None
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(self.until(lambda: self.refreshed == ["acme/thing"]))
        self.assertEqual(self.runner.calls, [], "and nothing was dispatched")
        row = self.box.last_runs(5)[0]
        self.assertIsNone(row["rc"])
        self.assertEqual(row["path"], "")

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

    def test_the_receipt_is_written_before_the_dispatch_runs(self):
        """A lane can take half an hour. A desk that says "in pr" for half an
        hour after the PR merged is the stale this path exists to remove."""
        seen = {}

        def slow(path):
            seen["receipt_first"] = self.receipts.exists()
            return 0, 0.1, []

        t = self.trigger(runner=slow)
        t.notice(wh.parse("pull_request", "d1", pull_request()))
        self.assertTrue(self.until(lambda: "receipt_first" in seen))
        self.assertTrue(seen["receipt_first"])

    def test_no_receipts_file_configured_is_not_a_crash(self):
        t = self.trigger(receipts=None)
        t.notice(wh.parse("pull_request", "d1", pull_request()))
        self.assertTrue(self.until(lambda: t.acts >= 1))

    def test_nothing_ever_runs_two_at_once(self):
        """Not a load choice. dispatch.sh holds ONE global lock for the whole
        pipeline, and a second run finding it held exits 0 having done nothing,
        so a pool would silently drop events behind a green exit code."""
        live = []
        peak = []
        lock = threading.Lock()
        release = threading.Event()

        def blocking(path):
            with lock:
                live.append(1)
                peak.append(len(live))
            release.wait(5)
            with lock:
                live.pop()
            return 0, 3.0, []

        t = self.trigger(runner=blocking)
        for i in range(5):
            t.notice(wh.parse("issue_comment", f"d{i}", issue_comment(repo=f"acme/r{i}")))
        self.assertTrue(self.until(lambda: len(peak) >= 1))
        time.sleep(0.4)
        self.assertEqual(max(peak), 1, "one drainer, serial, always")
        release.set()
        self.assertTrue(self.until(lambda: t.acts >= 5, timeout=15))
        self.assertEqual(max(peak), 1, "and still one, all five of them")

    def test_a_second_event_for_a_running_repo_waits_its_turn(self):
        started = threading.Event()
        release = threading.Event()
        overlap = []
        live = []

        def blocking(path):
            live.append(1)
            overlap.append(len(live))
            started.set()
            release.wait(5)
            live.pop()
            return 0, 3.0, []

        t = self.trigger(runner=blocking)
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(started.wait(5))
        for i in range(3):
            t.notice(wh.parse("issue_comment", f"e{i}", issue_comment()))
        time.sleep(0.3)
        self.assertEqual(max(overlap), 1)
        release.set()
        self.assertTrue(self.until(lambda: t.acts >= 2), "and then it runs")

    # ── the lock dispatch.sh actually takes ─────────────────────────────────
    def test_a_held_pipeline_lock_means_nothing_is_spawned_at_all(self):
        """dispatch.sh:472-476 would log one line and exit 0. Spawning into that
        does not queue the work, it loses it."""
        lock = self.dir / "pid"
        lock.write_text(str(os.getppid()))
        t = self.trigger(lock_path=lock, requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment(login="tim")))
        self.assertTrue(self.until(lambda: t.requeued >= 1))
        self.assertEqual(self.runner.calls, [], "nothing was spawned")
        row = self.box.last_runs(5)[0]
        self.assertIsNone(row["rc"])
        self.assertIn("already running", row["note"])
        self.assertIn("acme/thing", t.queued(), "and it is still owed")

        # and when the lock goes, the next drain runs it
        lock.unlink()
        self.assertTrue(self.until(lambda: len(self.runner.calls) == 1, timeout=8))
        self.assertEqual(t.queued(), [])

    def test_a_dead_pid_in_the_lock_file_is_not_busy(self):
        """Exactly how dispatch.sh reads it: `kill -0`, not "the file exists"."""
        lock = self.dir / "pid"
        lock.write_text("999999")
        t = self.trigger(lock_path=lock)
        self.assertIsNone(t.pipeline_busy())
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(self.until(lambda: len(self.runner.calls) == 1))
        self.assertEqual(t.requeued, 0)

    def test_a_torn_or_missing_lock_file_is_not_busy(self):
        lock = self.dir / "pid"
        t = self.trigger(lock_path=lock)
        self.assertIsNone(t.pipeline_busy())
        lock.write_text("not a pid\n")
        self.assertIsNone(t.pipeline_busy())
        lock.write_text("")
        self.assertIsNone(t.pipeline_busy())

    def test_an_exit_zero_that_did_nothing_is_not_believed(self):
        """The silent no-op: dispatch.sh's guard exits 0 in well under a second.
        Believing that green code would drop the event on the floor."""
        lock = self.dir / "pid"

        def quick(path):
            # the lock appears while this "run" is going: somebody else has it
            lock.write_text(str(os.getppid()))
            self.runner.calls.append(pathlib.Path(path))
            return 0, 0.2, ["another run holds the lock (pid 123); exiting."]

        t = self.trigger(runner=quick, lock_path=lock, requeue_s=0.05)
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(self.until(lambda: t.requeued >= 1))
        row = self.box.last_runs(5)[0]
        self.assertEqual(row["rc"], 0)
        self.assertIn("did nothing", row["note"])
        self.assertIn("acme/thing", t.queued())

    def test_a_real_run_that_exits_zero_quickly_is_believed(self):
        """The tell is exit 0 AND under two seconds AND the lock held by
        somebody else. Two out of three is a run that simply had nothing to do."""
        lock = self.dir / "pid"
        t = self.trigger(lock_path=lock)
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        self.assertTrue(self.until(lambda: len(self.runner.calls) == 1))
        time.sleep(0.3)
        self.assertEqual(t.requeued, 0)
        self.assertEqual(t.queued(), [])

    def test_a_requeue_does_not_write_the_receipt_twice(self):
        """Receipts are about the delivery; the dispatch is about the pipeline.
        Only the second one is ever retried."""
        lock = self.dir / "pid"
        lock.write_text(str(os.getppid()))
        t = self.trigger(lock_path=lock, requeue_s=0.05)
        t.notice(wh.parse("pull_request", "d1", pull_request()))
        self.assertTrue(self.until(lambda: t.requeued >= 3, timeout=8))
        self.assertEqual(len(self.receipts.read_text().splitlines()), 1)
        self.assertEqual(self.refreshed, ["acme/thing"], "and the desk is refetched once")

    def test_a_malformed_repo_is_never_queued(self):
        t = self.trigger()
        ev = wh.Event(delivery="d", event="issues", action="opened", repo="../../etc",
                      number=1, login="tim", merged=False, at=wh.now_iso(), body_marker=False)
        t.notice(ev)
        self.assertEqual(t.queued(), [])
        time.sleep(0.2)
        self.assertEqual(t.acts, 0)

    def test_a_dispatch_that_blows_up_does_not_take_the_office_with_it(self):
        def boom(path):
            raise RuntimeError("no")

        t = self.trigger(runner=boom)
        t.notice(wh.parse("issue_comment", "d1", issue_comment()))
        time.sleep(0.4)
        self.assertEqual(t.queued(), [], "the repo is not stuck in the queue")
        t.notice(wh.parse("issue_comment", "d2", issue_comment()))
        self.assertTrue(self.until(lambda: t.acts >= 2), "and the next one still runs")


class DispatchCommandTest(unittest.TestCase):
    """What is actually exec'd, checked against a script that reports itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        wh.log = lambda m: None

    def test_it_runs_dispatch_with_repo_and_never_without(self):
        """Without `--repo` the runner sweeps every repo under the vault. A
        comment on one issue must never be able to start a full sweep."""
        script = self.dir / "dispatch.sh"
        script.write_text('#!/bin/bash\necho "argv: $*"\nexit 3\n')
        script.chmod(0o755)
        t = wh.Trigger(wh.Mailbox(self.dir), dispatch=script, debounce_s=0.05)
        self.addCleanup(t.cancel)
        rc, seconds, tail = t._run_dispatch(self.dir / "code" / "thing")
        self.assertEqual(rc, 3)
        self.assertEqual(tail, [f"argv: --repo {self.dir}/code/thing"])
        self.assertGreaterEqual(seconds, 0)

    def test_a_missing_script_is_reported_rather_than_pretended(self):
        t = wh.Trigger(wh.Mailbox(self.dir), dispatch=self.dir / "nope.sh", debounce_s=0.05)
        self.addCleanup(t.cancel)
        rc, _, tail = t._run_dispatch(self.dir)
        self.assertIsNone(rc, "no exit code, because nothing ran")
        self.assertIn("no dispatch script", tail[0])

    def test_only_the_last_lines_of_a_long_log_are_kept(self):
        script = self.dir / "dispatch.sh"
        script.write_text('#!/bin/bash\nfor i in $(seq 1 200); do echo "line $i"; done\n')
        script.chmod(0o755)
        t = wh.Trigger(wh.Mailbox(self.dir), dispatch=script, debounce_s=0.05)
        self.addCleanup(t.cancel)
        rc, _, tail = t._run_dispatch(self.dir)
        self.assertEqual(rc, 0)
        self.assertEqual(len(tail), wh.LOG_LINES)
        self.assertEqual(tail[-1], "line 200")


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

    def source(self):
        from sources import webhook as src
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
        t = wh.Trigger(box, debounce_s=30, runner=lambda p: (0, 0, []))
        self.addCleanup(t.cancel)
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
