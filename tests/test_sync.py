"""One query per ten desks, and a room that never blanks because GitHub said no.

The office used to ask GitHub twice per desk per minute. With seventy-two desks
that is a hundred and forty-four queries a minute against a budget of five
thousand points an hour, so the room spent most of its life showing every desk
the same sentence: `GraphQL: API rate limit already exceeded for user ID ...`.

Two defects, not one. The spend was the first. The second was worse: a failed
fetch emptied the desk, so the office's answer to "I could not reach GitHub" was
a picture of an office with nothing to do. These tests are about both.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "client"))
sys.path.insert(0, str(HERE))  # the shim, when this file is run on its own

# An hour from now, computed: a literal stamp is a test that starts failing the
# morning the wall clock walks past it.
from datetime import datetime, timedelta, timezone  # noqa: E402
RESET = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue_node(number, body="hello", comment="pipeline-bot: answer this?"):
    return {
        "number": number,
        "title": f"issue {number}",
        "body": body,
        "url": f"https://github.com/x/y/issues/{number}",
        "updatedAt": "2026-08-26T10:00:00Z",
        "labels": {"nodes": [{"name": "bug"}, {"name": "waiting on human"}]},
        "comments": {"nodes": ([{"body": comment}] if comment is not None else [])},
    }


def pr_node(number, head="pipeline/auto-issue-9"):
    return {
        "number": number,
        "title": f"pr {number}",
        "headRefName": head,
        "baseRefName": "main",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
        "url": f"https://github.com/x/y/pull/{number}",
        "body": "Closes #9",
        "updatedAt": "2026-08-26T11:00:00Z",
    }


def repo_node():
    return {
        "issues": {"nodes": [issue_node(4, comment=None), issue_node(9)]},
        "pullRequests": {"nodes": [pr_node(11), pr_node(12, head="aria/my-own-work")]},
    }


class FakeAccess:
    """Push access without a keychain. Every repo answers to one login unless a
    test says otherwise, because which token is used is a batching question."""

    def __init__(self, tokens=None):
        self.tokens = tokens or {}
        self.cache = {}
        self.saved = False

    def token_for(self, nwo):
        who = self.tokens.get(nwo, "ariaxhan")
        self.cache[nwo] = who or ""
        return (who, f"tok-{who}") if who else (None, None)

    def save(self):
        self.saved = True


class SyncCase(unittest.TestCase):
    """A whole office with no network, no keychain and no home directory.

    The module is reloaded per test so the hour's budget, which is module level
    on purpose (a pause outlives one build), cannot leak between them.
    """

    REPOS = ["acme/one", "acme/two"]

    def setUp(self):
        import office_sync_shim

        self.mod = importlib.reload(office_sync_shim).mod
        self.mod.log = lambda m: None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = pathlib.Path(self.tmp.name)
        self.mod.STATE = state
        self.mod.HIDDEN_FILE = state / "hidden.json"
        self.mod.PINS_FILE = state / "pins.json"
        self.mod.DESKS_CACHE = state / "desks.json"
        # Neither of these is what is under test, and both would reach outside
        # the process if left alone.
        self.mod.rt.snapshot = lambda: {"gate": {"state": "clear"}}
        self.mod.sections_mod.read_all = lambda: {}

        self.repos = list(self.REPOS)
        self.set_receipts(self.repos)

        self.queries = []          # one entry per `gh api graphql` call
        self.reply = self.default_reply
        self.mod.sh = self.fake_sh

    # ── the fake GitHub ─────────────────────────────────────────────────────
    def set_receipts(self, repos):
        by_repo = {r: [{"at": "2026-08-26T09:00:00Z", "repo": r, "issue": "1",
                        "outcome": "landed", "detail": "opened a PR"}] for r in repos}
        self.mod.receipts = lambda: (by_repo, {"landed": len(repos)})

    def fake_sh(self, cmd, timeout=45, env=None, check=False):
        if cmd[:3] != ["gh", "api", "graphql"]:
            raise AssertionError(f"nothing but graphql may run: {cmd}")
        names = {}
        for flag, kv in zip(cmd, cmd[1:]):
            if flag == "-f" and "=" in kv:
                k, v = kv.split("=", 1)
                if k != "query":
                    names[k] = v
        n = sum(1 for k in names if k.startswith("o"))
        batch = [f"{names['o%d' % i]}/{names['n%d' % i]}" for i in range(n)]
        self.queries.append({"repos": batch, "query": next(
            kv.split("=", 1)[1] for kv in cmd if kv.startswith("query="))})
        return self.reply(batch)

    def default_reply(self, batch, cost=1, remaining=4000, errors=None, missing=()):
        data = {"rateLimit": {"limit": 5000, "cost": cost, "remaining": remaining,
                              "resetAt": RESET}}
        for i, nwo in enumerate(batch):
            data[f"r{i}"] = None if nwo in missing else repo_node()
        out = {"data": data}
        if errors:
            out["errors"] = errors
        return (1 if errors else 0), json.dumps(out), ""

    def build(self, access=None):
        return self.mod.build_snapshot(access or FakeAccess())

    def desk(self, snap, repo):
        return next(s for s in snap["stations"] if s["repo"] == repo)

    def asked(self):
        return [r for q in self.queries for r in q["repos"]]


class MappingTest(SyncCase):
    """One query in, the exact station shape the app already decodes out."""

    def test_the_batch_becomes_the_shapes_the_app_decodes(self):
        snap = self.build()
        self.assertEqual(len(self.queries), 1, "two desks are one query, not two")
        st = self.desk(snap, "acme/one")

        self.assertEqual([i["number"] for i in st["issues"]], [9, 4],
                         "the bot's last word sorts first, then by number")
        waiting, quiet = st["issues"]
        self.assertTrue(waiting["bot_last"])
        self.assertIn("answer this?", waiting["last_word"])
        self.assertEqual(waiting["labels"], ["bug", "waiting on human"])
        self.assertEqual(waiting["url"], "https://github.com/x/y/issues/9")
        self.assertEqual(waiting["updatedAt"], "2026-08-26T10:00:00Z")
        self.assertFalse(quiet["bot_last"])
        self.assertEqual(quiet["last_word"], "", "no bot, no question")

        # Only the pipeline's own branches. A human's branch is not the office's
        # business and a merge button over it would be a defect, not a feature.
        self.assertEqual([p["number"] for p in st["prs"]], [11])
        pr = st["prs"][0]
        self.assertEqual(pr["head"], "pipeline/auto-issue-9")
        self.assertEqual(pr["base"], "main")
        self.assertEqual(pr["closes"], [9])
        # The body travels with the PR so the desk pane can show why.
        self.assertIn("#9", pr["body"])
        self.assertEqual(pr["mergeable"], "MERGEABLE")
        self.assertEqual(pr["state"], "CLEAN")
        self.assertFalse(pr["draft"])

        self.assertIsNone(st["issues_error"])
        self.assertIsNone(st["prs_error"])
        self.assertFalse(st["hidden"])
        self.assertTrue(st["fetched_at"], "a desk that answered says when")

    def test_a_long_body_is_trimmed_the_way_it_always_was(self):
        self.reply = lambda batch: (0, json.dumps({"data": {
            "rateLimit": {"limit": 5000, "cost": 1, "remaining": 4000, "resetAt": RESET},
            "r0": {"issues": {"nodes": [issue_node(1, body="x" * 9000)]},
                   "pullRequests": {"nodes": []}},
            "r1": repo_node(),
        }}), "")
        st = self.desk(self.build(), "acme/one")
        self.assertEqual(len(st["issues"][0]["body"]), 4000)

    def test_the_snapshot_reports_the_budget_it_spent(self):
        snap = self.build()
        self.assertEqual(snap["github"], {
            "limit": 5000, "remaining": 4000, "reset_at": RESET,
            "cost": 1, "paused_until": "", "error": "",
        })

    def test_repo_names_travel_as_variables_not_as_query_text(self):
        """The query is a constant. A repo name never becomes part of it, so a
        name shaped like a second query is a 404 rather than an injection."""
        self.build()
        q = self.queries[0]["query"]
        for nwo in self.repos:
            self.assertNotIn(nwo, q)
        self.assertIn("$o0: String!", q)
        self.assertIn("r0: repository(owner: $o0, name: $n0)", q)


class BatchingTest(SyncCase):
    def test_23_repos_become_3_batches(self):
        repos = [f"acme/r{i:02d}" for i in range(23)]
        self.repos = repos
        self.set_receipts(repos)
        snap = self.build()
        self.assertEqual(len(self.queries), 3)
        self.assertEqual(sorted(len(q["repos"]) for q in self.queries), [3, 10, 10])
        self.assertEqual(sorted(self.asked()), sorted(repos))
        self.assertEqual(len(snap["stations"]), 23)
        self.assertEqual(snap["github"]["cost"], 3, "one rateLimit block per query")

    def test_two_logins_never_share_a_query(self):
        """A batch is one token's worth of repos. Mixing them would send half of
        them with an account that cannot see them."""
        repos = ["acme/a", "other/b", "acme/c"]
        self.repos = repos
        self.set_receipts(repos)
        access = FakeAccess({"other/b": "ariablinkbuild"})
        self.build(access)
        for q in self.queries:
            self.assertTrue(all(r.startswith("acme/") for r in q["repos"])
                            or all(r.startswith("other/") for r in q["repos"]))
        self.assertEqual(sorted(self.asked()), sorted(repos))


class PartialFailureTest(SyncCase):
    def test_one_dead_repo_leaves_the_other_standing(self):
        """gh exits non-zero when ANY alias in the batch errored. Reading the
        exit code instead of the body would blank ten desks over one."""
        self.reply = lambda batch: self.default_reply(
            batch, missing=("acme/two",),
            errors=[{"type": "NOT_FOUND", "path": ["r1"],
                     "message": "Could not resolve to a Repository with the name 'acme/two'."}])
        snap = self.build()
        alive = self.desk(snap, "acme/one")
        dead = self.desk(snap, "acme/two")
        self.assertEqual(len(alive["issues"]), 2)
        self.assertIsNone(alive["issues_error"])
        self.assertEqual(dead["issues"], [])
        self.assertIn("Could not resolve", dead["issues_error"])
        self.assertIn("Could not resolve", dead["prs_error"])
        self.assertEqual(snap["github"]["error"], "", "one gone repo is not a budget fault")

    def test_a_desk_with_no_account_says_so_without_a_query(self):
        access = FakeAccess({"acme/two": ""})
        snap = self.build(access)
        self.assertEqual(self.asked(), ["acme/one"])
        self.assertEqual(self.desk(snap, "acme/two")["issues_error"],
                         "no account holds push here")
        self.assertFalse(self.desk(snap, "acme/two")["access"])


class LastKnownGoodTest(SyncCase):
    def test_a_failed_batch_keeps_last_good_and_stamps_the_error(self):
        """The defect this whole change exists to kill: GitHub says no and the
        office shows an empty room, which is indistinguishable from an office
        with nothing to do."""
        first = self.build()
        good = self.desk(first, "acme/one")
        self.assertEqual(len(good["issues"]), 2)
        stamp = good["fetched_at"]
        self.assertTrue(stamp)

        self.queries = []
        self.reply = lambda batch: (1, "", "HTTP 502: Bad gateway")
        second = self.build()
        stale = self.desk(second, "acme/one")
        self.assertEqual([i["number"] for i in stale["issues"]], [9, 4],
                         "the desk keeps what it last showed")
        self.assertEqual(len(stale["prs"]), 1)
        self.assertEqual(stale["fetched_at"], stamp, "and says when that was")
        self.assertIn("502", stale["issues_error"])
        self.assertIn("502", stale["prs_error"])

    def test_desks_json_round_trips_across_a_restart(self):
        self.build()
        raw = json.loads(self.mod.DESKS_CACHE.read_text())
        self.assertEqual(sorted(raw["repos"]), self.repos)
        self.assertEqual(raw["repos"]["acme/one"]["issues"][0]["number"], 9)
        self.assertTrue(raw["repos"]["acme/one"]["fetched_at"])

        # A fresh module is a fresh process: nothing in memory, only the file.
        again = importlib.reload(importlib.import_module("office_sync_shim")).mod
        again.log = lambda m: None
        again.DESKS_CACHE = self.mod.DESKS_CACHE
        again.HIDDEN_FILE = self.mod.HIDDEN_FILE
        again.STATE = self.mod.STATE
        again.rt.snapshot = lambda: {"gate": {"state": "clear"}}
        again.sections_mod.read_all = lambda: {}
        again.receipts = self.mod.receipts
        again.sh = lambda cmd, timeout=45, env=None, check=False: (1, "", "no network")
        snap = again.build_snapshot(FakeAccess())
        st = next(s for s in snap["stations"] if s["repo"] == "acme/one")
        self.assertEqual([i["number"] for i in st["issues"]], [9, 4])
        self.assertIn("no network", st["issues_error"])

    def test_the_cache_forgets_a_repo_that_lost_its_desk(self):
        self.build()
        self.set_receipts(["acme/one"])
        self.build()
        raw = json.loads(self.mod.DESKS_CACHE.read_text())
        self.assertEqual(list(raw["repos"]), ["acme/one"])


class HiddenTest(SyncCase):
    def test_a_hidden_repo_is_in_no_query_at_all(self):
        self.mod.set_hidden("acme/two", True)
        snap = self.build()
        self.assertEqual(self.asked(), ["acme/one"])
        for q in self.queries:
            self.assertNotIn("acme/two", json.dumps(q))

    def test_a_hidden_desk_still_exists_carrying_what_it_last_showed(self):
        self.build()
        self.mod.set_hidden("acme/two", True)
        snap = self.build()
        away = self.desk(snap, "acme/two")
        self.assertTrue(away["hidden"])
        self.assertEqual([i["number"] for i in away["issues"]], [9, 4])
        self.assertTrue(away["fetched_at"])
        self.assertIsNone(away["issues_error"], "put away is not an error")
        self.assertFalse(self.desk(snap, "acme/one")["hidden"])

    def test_hiding_a_repo_nobody_has_seen_is_allowed(self):
        """A desk appears the first time the runner touches a repo, so "not that
        one" has to be sayable before it turns up."""
        self.assertEqual(self.mod.set_hidden("future/thing", True), ["future/thing"])
        self.assertEqual(self.mod.read_hidden(), ["future/thing"])

    def test_unhiding_something_that_was_never_hidden_is_a_no_op(self):
        self.assertEqual(self.mod.set_hidden("acme/one", False), [])

    def test_a_malformed_repo_never_reaches_the_file(self):
        for bad in ("", "nope", "a/b/c", "a b/c", "../../etc/passwd", "a/b\n"):
            with self.assertRaises(ValueError, msg=bad):
                self.mod.set_hidden(bad, True)
        self.assertEqual(self.mod.read_hidden(), [])

    def test_two_hands_putting_desks_away_at_once_lose_neither(self):
        import threading
        names = [f"acme/r{i}" for i in range(40)]
        threads = [threading.Thread(target=self.mod.set_hidden, args=(n, True)) for n in names]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(self.mod.read_hidden(), sorted(names))
        self.assertFalse(self.mod.HIDDEN_FILE.with_name(self.mod.HIDDEN_FILE.name + ".tmp").exists())

    def test_hidden_survives_the_file_being_nonsense(self):
        self.mod.HIDDEN_FILE.write_text("{not json")
        self.assertEqual(self.mod.read_hidden(), [])

    # ── pins ────────────────────────────────────────────────────────────────
    def test_pins_keep_their_order_across_a_restart(self):
        self.assertEqual(self.mod.write_pins(["acme/two", "acme/one"]), ["acme/two", "acme/one"])
        import office_sync_shim
        again = importlib.reload(office_sync_shim).mod
        again.PINS_FILE = self.mod.PINS_FILE
        self.assertEqual(again.read_pins(), ["acme/two", "acme/one"], "order, not a sort")
        self.assertFalse(self.mod.PINS_FILE.with_name(self.mod.PINS_FILE.name + ".tmp").exists())

    def test_a_pin_on_a_repo_with_no_desk_is_kept_and_harms_nothing(self):
        self.mod.write_pins(["future/thing", "acme/one"])
        snap = self.build()
        self.assertEqual(snap["pins"], ["future/thing", "acme/one"])
        self.assertEqual(self.desk(snap, "acme/one")["pinned"], 1)
        self.assertIsNone(self.desk(snap, "acme/two")["pinned"])
        self.assertNotIn("future/thing", [s["repo"] for s in snap["stations"]])

    def test_pins_drop_junk_and_duplicates_but_never_the_rest(self):
        kept = self.mod.write_pins(["acme/one", "", "nope", 7, "acme/one", "a/b\n", "acme/two"])
        self.assertEqual(kept, ["acme/one", "acme/two"])
        self.assertEqual(self.mod.read_pins(), ["acme/one", "acme/two"])

    def test_pins_survive_the_file_being_nonsense(self):
        self.mod.PINS_FILE.write_text("[1, 2")
        self.assertEqual(self.mod.read_pins(), [])
        self.mod.PINS_FILE.write_text('{"repos": ["acme/one", 3, "bad name"]}')
        self.assertEqual(self.mod.read_pins(), ["acme/one"])

    def test_the_snapshot_says_whose_office_it_is(self):
        was = self.mod.OWNERS
        try:
            self.mod.OWNERS = ["ariaxhan", "acme"]
            self.assertEqual(self.build()["owners"], ["ariaxhan", "acme"])
            self.mod.OWNERS = []
            access = FakeAccess()
            access.mine = ["someone"]
            self.assertEqual(self.build(access)["owners"], ["someone"], "falls back to the gh logins")
        finally:
            self.mod.OWNERS = was


class BudgetTest(SyncCase):
    def test_running_low_pauses_before_the_next_query_goes_out(self):
        repos = [f"acme/r{i:02d}" for i in range(23)]
        self.repos = repos
        self.set_receipts(repos)
        self.reply = lambda batch: self.default_reply(batch, remaining=500)

        snap = self.build()
        self.assertEqual(len(self.queries), 1,
                         "the reserve is only a reserve if it stops the NEXT query")
        self.assertEqual(snap["github"]["remaining"], 500)
        self.assertEqual(snap["github"]["paused_until"], RESET)
        self.assertIn("500", snap["github"]["error"])

        asked = set(self.asked())
        for repo in repos:
            st = self.desk(snap, repo)
            if repo not in asked:
                self.assertIn("paused until", st["issues_error"])
                self.assertEqual(st["issues"], [])

    def test_github_saying_rate_limit_pauses_at_once(self):
        self.reply = lambda batch: (1, json.dumps({"data": {"rateLimit": None}, "errors": [
            {"type": "RATE_LIMITED",
             "message": "API rate limit already exceeded for user ID 113392746."}]}), "")
        snap = self.build()
        self.assertTrue(snap["github"]["paused_until"])
        self.assertIn("rate limit", snap["github"]["error"].lower())

    def test_a_paused_build_asks_github_nothing_and_still_builds(self):
        self.mod.pause("out of points", RESET)
        snap = self.build()
        self.assertEqual(self.queries, [], "a pause means a pause")
        self.assertEqual(snap["github"]["cost"], 0)
        self.assertEqual(len(snap["stations"]), 2)
        # The rest of the room is not GitHub's to take down.
        self.assertEqual(snap["runtime"], {"gate": {"state": "clear"}})
        self.assertIn("paused until", self.desk(snap, "acme/one")["issues_error"])

    def test_the_pause_lifts_by_itself_when_its_hour_is_up(self):
        self.mod.BUDGET["paused_until"] = "2020-01-01T00:00:00Z"
        self.mod.BUDGET["error"] = "out of points"
        # The number that tripped the reserve. If it survives the lift, the next
        # build re-pauses on it without asking, and the pause is forever.
        self.mod.BUDGET["remaining"] = 5
        self.assertEqual(self.mod.paused_until(), "")
        snap = self.build()
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(snap["github"]["paused_until"], "")

    def test_a_pause_with_no_usable_reset_time_still_ends(self):
        """GitHub not saying when is not permission to stop forever, and a reset
        time already in the past is not permission to not pause at all."""
        for given in ("", "nonsense", "2020-01-01T00:00:00Z"):
            self.mod.BUDGET["paused_until"] = ""
            stamp = self.mod.pause("something went wrong", given)
            self.assertTrue(stamp)
            self.assertEqual(self.mod.paused_until(), stamp, given)

    def test_the_reserve_is_configurable(self):
        self.mod.GH_RESERVE = 4500
        repos = [f"acme/r{i:02d}" for i in range(23)]
        self.repos = repos
        self.set_receipts(repos)
        self.build()
        self.assertEqual(len(self.queries), 1)


class SingleRepoTest(SyncCase):
    """The nudge path still has one repo in hand and still needs its issues."""

    def test_fetch_issues_is_one_query_for_one_repo(self):
        issues, err = self.mod.fetch_issues("acme/one", "tok")
        self.assertIsNone(err)
        self.assertEqual([i["number"] for i in issues], [9, 4])
        self.assertEqual(self.queries[0]["repos"], ["acme/one"])

    def test_fetch_issues_refuses_a_malformed_repo_without_running_anything(self):
        issues, err = self.mod.fetch_issues("not-a-repo", "tok")
        self.assertIsNone(issues)
        self.assertEqual(err, "malformed repo")
        self.assertEqual(self.queries, [])

    def test_the_old_per_repo_commands_are_gone(self):
        """`gh issue list` fetched a hundred comments per issue and `gh pr list`
        ran beside it, twice a minute per desk. Neither may come back."""
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "client" / "office-sync.py").read_text()
        for gone in ('"issue", "list"', '"pr", "list"', "gh issue list", "gh pr list"):
            self.assertNotIn(gone, src)
        self.assertFalse(hasattr(self.mod, "fetch_prs"))


class DecisionTest(SyncCase):
    """One issue decision becomes an ordered list of gh commands, or a refusal."""

    def setUp(self):
        super().setUp()
        self.ran = []
        self.mod.sh = self.record_sh

    def record_sh(self, cmd, timeout=45, env=None, check=False):
        self.ran.append(cmd)
        return 0, "", ""

    def decide(self, kind, issue=7, dry=False, **payload):
        return self.mod.apply_decision(
            {"repo": "acme/one", "kind": kind, "issue": issue, "payload": payload},
            FakeAccess(), dry)

    def test_a_comment_step_needs_words_and_an_issue(self):
        self.assertEqual(self.mod._comment_step("comment", "7", "acme/one", ""),
                         (None, "nothing to say"))
        self.assertEqual(self.mod._comment_step("nudge", "", "acme/one", ""),
                         (None, "a comment needs an issue"))
        self.assertEqual(self.mod._comment_step("close", "7", "acme/one", ""), (None, ""))
        self.assertEqual(self.mod._comment_step("label", "7", "acme/one", "x"), (None, ""))
        cmd, err = self.mod._comment_step("nudge", "7", "acme/one", "")
        self.assertEqual(err, "")
        self.assertEqual(cmd[-1], self.mod.REQUEUE_LINE)

    def test_edit_steps_per_kind(self):
        steps, err = self.mod._edit_steps("unblock", "7", "acme/one", {})
        self.assertEqual((err, len(steps)), ("", 1))
        self.assertIn("--remove-label", steps[0])
        self.assertEqual(self.mod._edit_steps("label", "7", "acme/one", {"label": " "}),
                         ([], "no label given"))
        self.assertEqual(self.mod._edit_steps("close", "", "acme/one", {}), ([], ""))
        self.assertEqual(self.mod._edit_steps("reopen", "7", "acme/one", {})[0][0][:3],
                         ["gh", "issue", "reopen"])

    def test_issue_steps_refuses_when_nothing_would_run(self):
        self.assertEqual(self.mod._issue_steps("bogus", "7", "acme/one", "", {}),
                         (None, "nothing to do for bogus"))
        steps, err = self.mod._issue_steps("unblock", "7", "acme/one", "yes", {})
        self.assertEqual(err, "")
        self.assertEqual([s[2] for s in steps], ["comment", "edit"])

    def test_a_missing_label_is_not_a_failure(self):
        self.assertTrue(self.mod._is_missing_label_error(
            ["gh", "issue", "edit", "7", "--remove-label", "x"], "label not found"))
        self.assertFalse(self.mod._is_missing_label_error(
            ["gh", "issue", "comment", "7"], "label not found"))
        self.assertFalse(self.mod._is_missing_label_error(
            ["gh", "issue", "edit", "7", "--remove-label", "x"], "permission denied"))

    def test_run_steps_dry_runs_nothing_and_stops_on_the_first_failure(self):
        steps = [["gh", "issue", "comment", "7"], ["gh", "issue", "close", "7"]]
        self.assertEqual(self.mod._run_steps(steps, {}, True),
                         (True, ["would issue comment 7", "would issue close 7"]))
        self.assertEqual(self.ran, [])
        self.mod.sh = lambda cmd, timeout=45, env=None, check=False: (1, "", "boom\nmore")
        self.assertEqual(self.mod._run_steps(steps, {}, False), (False, "issue comment: boom"))

    def test_an_unblock_comments_then_drops_the_label_as_this_login(self):
        ok, msg = self.decide("unblock", body="answered")
        self.assertTrue(ok)
        self.assertEqual(msg, "as ariaxhan: issue comment; issue edit")
        self.assertEqual([c[:3] for c in self.ran],
                         [["gh", "issue", "comment"], ["gh", "issue", "edit"]])

    def test_a_repo_level_nudge_requeues_what_the_bot_sits_on(self):
        self.mod.fetch_issues = lambda repo, tok: (
            [{"number": 4, "bot_last": True}, {"number": 5, "bot_last": False}], None)
        ok, msg = self.mod._requeue_stuck_issues("acme/one", "ariaxhan", "tok", True)
        self.assertEqual((ok, msg), (True, "as ariaxhan: requeued would requeue #4"))
        self.assertEqual(self.ran, [])
        self.mod.fetch_issues = lambda repo, tok: ([{"number": 5, "bot_last": False}], None)
        self.assertEqual(self.mod._requeue_stuck_issues("acme/one", "a", "tok", False),
                         (False, "nothing here is waiting on a human"))

    def test_refusals_before_any_command_runs(self):
        self.assertEqual(self.decide("close", issue=None),
                         (False, "close needs an issue number"))
        self.assertEqual(self.decide("comment"), (False, "nothing to say"))
        self.assertEqual(self.decide("label", label=""), (False, "no label given"))
        self.assertEqual(self.ran, [])


class RuntimeDecisionTest(SyncCase):
    """A runtime decision goes to the local runtime by kind, and never to GitHub."""

    def setUp(self):
        super().setUp()
        self.posted = []
        self.mod.rt.post = lambda path, body, timeout=20: self.posted.append((path, body))
        self.mod.rt._root = lambda: pathlib.Path(self.tmp.name)
        self.mod.rt.read_gate = lambda: {"state": "pending", "id": "q1"}
        self.mod.rt.answer_gate = lambda root, qid, answer, always: (True, f"{answer} {qid}")

    def test_the_table_routes_every_runtime_kind_and_nothing_else(self):
        self.assertEqual(set(self.mod.RUNTIME_HANDLERS), self.mod.RUNTIME_KINDS)
        self.assertEqual(self.mod.apply_runtime_decision({"kind": "merge"}, True),
                         (False, "unknown runtime kind merge"))

    def test_a_permit_answers_the_gate_it_was_asked_about(self):
        permit = lambda dry, **p: self.mod.apply_runtime_decision(
            {"kind": "permit", "payload": p}, dry)
        self.assertEqual(permit(True, question_id="q1", answer="allow"), (True, "would allow"))
        self.assertEqual(permit(True, question_id="q2", answer="allow"),
                         (False, "the agent has moved on"))
        self.assertEqual(permit(False, question_id="q1", answer="deny"), (True, "deny q1"))
        self.assertEqual(permit(False, question_id="q1", answer="maybe"),
                         (False, "a permit must answer allow or deny"))
        self.mod.rt.read_gate = lambda: {"state": "clear"}
        self.assertEqual(permit(True, question_id="q1", answer="allow"),
                         (False, "nothing is waiting on a gate right now"))

    def test_chat_and_run_and_stop_post_to_the_runtime(self):
        self.assertEqual(self.mod._apply_chat({}, {"body": " "}, False), (False, "nothing to say"))
        self.assertEqual(self.mod._apply_chat({}, {"body": "hi"}, False), (True, "said 'hi'"))
        ok, msg = self.mod._apply_run({"repo": "acme/one", "issue": 4}, {}, False)
        self.assertEqual((ok, msg), (True, "started 'Work acme/one#4'"))
        self.assertEqual(self.mod._apply_run({"repo": "acme/one"}, {}, True),
                         (True, "would run 'Work on acme/one'"))
        self.assertEqual(self.mod._apply_stop({}, {"run_id": "r9"}, False)[0], True)
        self.assertEqual(self.posted, [("/api/chat", {"message": "hi"}),
                                       ("/api/run", {"task": "Work acme/one#4"}),
                                       ("/api/run/stop", {"run_id": "r9"})])

    def test_a_runtime_that_refuses_is_reported_not_raised(self):
        def refuse(path, body, timeout=20):
            raise RuntimeError("down")
        self.mod.rt.post = refuse
        self.assertEqual(self.mod._apply_chat({}, {"body": "hi"}, False),
                         (False, "the runtime did not take it: down"))
        self.assertEqual(self.mod._apply_stop({}, {}, False), (False, "could not stop it: down"))


class BatchPartsTest(SyncCase):
    """The pieces fetch_batch is made of, each on its own."""

    def test_the_body_is_a_dict_or_nothing(self):
        self.assertEqual(self.mod._json_body(""), {})
        self.assertEqual(self.mod._json_body("not json"), {})
        self.assertEqual(self.mod._json_body("[1]"), {})
        self.assertEqual(self.mod._json_body('{"data": 1}'), {"data": 1})

    def test_an_error_names_its_repo_or_sinks_the_batch(self):
        nwos = ["acme/one", "acme/two"]
        self.assertEqual(self.mod._alias_index({"path": ["r1"]}, 2), 1)
        self.assertEqual(self.mod._alias_index({"path": ["r7"]}, 2), -1)
        self.assertEqual(self.mod._alias_index({}, 2), -1)
        errors, fatal = self.mod._sort_errors(
            [{"path": ["r0"], "message": "gone"}, {"message": "bad query"},
             {"type": "RATE_LIMITED", "message": "slow down"}], nwos)
        self.assertEqual(errors, {"acme/one": "gone"})
        self.assertEqual(fatal, "slow down")
        self.assertTrue(self.mod._is_rate_limited("API rate limit exceeded", {}))
        self.assertFalse(self.mod._is_rate_limited("not found", {"type": "NOT_FOUND"}))

    def test_the_bot_last_word_travels_only_when_the_bot_spoke_last(self):
        self.assertEqual(self.mod._bot_last_word(issue_node(1, comment=None)), "")
        self.assertEqual(self.mod._bot_last_word(issue_node(1, comment="a human")), "")
        word = self.mod._bot_last_word(issue_node(1))
        self.assertIn(self.mod.BOT_MARKER, word)
        row = self.mod._issue_row(issue_node(1))
        self.assertEqual((row["bot_last"], row["last_word"]), (True, word))


if __name__ == "__main__":
    unittest.main()
