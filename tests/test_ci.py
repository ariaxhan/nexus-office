"""The build lamps' data: keeping six states apart, batching, and the cache.

The sharp edges here are not the GraphQL. They are:

  never vs none      a repo with workflows and no check has a broken trigger;
                     a repo with no workflows has nothing to fix. Folding them
                     together sends you hunting a bug in a docs repo
  unknown vs passing "we could not look" rendered as green is the exact
                     false-green this project exists to kill
  the age            a cached board is honest only while it is labelled, and a
                     commit's date must never be served as a check's date
  the batching       seventy repos in one call per ten minutes, not seventy

`gh` is faked by putting a script first on PATH, rather than mocked, so the
timeout, the exit code and the argument shape all stay honest.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys
import textwrap
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


class CiTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        (self.dir / "bin").mkdir()
        self.calls = self.dir / "calls.jsonl"
        os.environ["PATH"] = f"{self.dir / 'bin'}{os.pathsep}{os.environ['PATH']}"
        os.environ["OFFICE_CI_CACHE"] = str(self.dir / "ci.json")
        os.environ.pop("OFFICE_CI_TTL_S", None)
        self._path = os.environ["PATH"]
        from sources import ci  # noqa: PLC0415 - reloaded so each test reads the env fresh
        self.ci = importlib.reload(ci)

    def tearDown(self):
        os.environ["PATH"] = self._path.split(os.pathsep, 1)[1]
        os.environ.pop("OFFICE_CI_CACHE", None)
        os.environ.pop("OFFICE_CI_TTL_S", None)
        self.tmp.cleanup()

    # -- fixtures --------------------------------------------------------------

    def gh(self, body: str):
        """Install a fake `gh`. `body` is python, run when it is executed."""
        p = self.dir / "bin" / "gh"
        p.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
        p.chmod(0o755)

    def answers(self, per_repo: dict, errors=()):
        """A gh that answers whatever `per_repo` says, keyed by nameWithOwner.

        It writes each invocation's query to a log so the batching can be
        counted, and it reads the aliases out of the query the way GitHub does,
        so an alias this code stops emitting fails the test instead of passing.
        """
        # The payloads are embedded as JSON TEXT and parsed, not as python
        # literals: a repo with no rollup carries a `null`, and a null pasted
        # into a python source file is a NameError, not a test.
        self.gh(f"""
            import json, re, sys, pathlib
            per = json.loads({json.dumps(json.dumps(per_repo))})
            errs = json.loads({json.dumps(json.dumps(list(errors)))})
            q = ""
            for a in sys.argv:
                if a.startswith("query="):
                    q = a[len("query="):]
            # One line per INVOCATION, so the batching can be counted. The query
            # itself is many lines, and logging it verbatim counted those.
            with pathlib.Path({json.dumps(str(self.calls))}).open("a") as f:
                f.write(json.dumps({{"len": len(q)}}) + "\\n")
            data = {{}}
            for alias, owner, name in re.findall(
                    r'(r\\d+): repository\\(owner: "([^"]+)", name: "([^"]+)"\\)', q):
                data[alias] = per.get(owner + "/" + name)
            out = {{"data": data}}
            if errs:
                out["errors"] = [{{"message": m}} for m in errs]
            print(json.dumps(out))
        """)

    @staticmethod
    def repo(rollup=None, workflows=("ci.yml",), branch="main",
             committed="2026-08-26T09:00:00Z"):
        return {
            "nameWithOwner": "x/y",
            "defaultBranchRef": {
                "name": branch,
                "target": {"oid": "abc", "committedDate": committed,
                           "statusCheckRollup": rollup},
            },
            "workflows": ({"entries": [{"name": n} for n in workflows]}
                          if workflows is not None else None),
        }

    @staticmethod
    def rollup(state, nodes=()):
        return {"state": state, "contexts": {"nodes": list(nodes)}}

    def by_repo(self, sec):
        return {r["repo"]: r for r in sec["repos"]}

    # -- the six states --------------------------------------------------------

    def test_passing_failing_and_running_are_three_different_things(self):
        self.answers({
            "a/green": self.repo(self.rollup("SUCCESS", [
                {"name": "test", "conclusion": "SUCCESS",
                 "completedAt": "2026-08-26T09:10:00Z", "detailsUrl": "u"}])),
            "a/red": self.repo(self.rollup("FAILURE", [
                {"name": "test", "conclusion": "FAILURE",
                 "completedAt": "2026-08-26T09:11:00Z", "detailsUrl": "run/1"}])),
            "a/busy": self.repo(self.rollup("PENDING")),
        })
        rows = self.by_repo(self.ci.read(["a/green", "a/red", "a/busy"]))
        self.assertEqual(rows["a/green"]["ci"], "passing")
        self.assertEqual(rows["a/red"]["ci"], "failing")
        self.assertEqual(rows["a/busy"]["ci"], "running")

    def test_workflows_and_no_check_is_never_but_no_workflows_is_not_a_fault(self):
        """The whole point of asking for the workflow tree. Both have no rollup."""
        self.answers({
            "a/untriggered": self.repo(None, workflows=("ci.yml",)),
            "a/nothing": self.repo(None, workflows=()),
        })
        sec = self.ci.read(["a/untriggered", "a/nothing"])
        rows = self.by_repo(sec)
        self.assertEqual(rows["a/untriggered"]["ci"], "never")
        self.assertEqual(rows["a/nothing"]["ci"], "none")
        # `none` is a decision, so it must not lift the alarm count.
        self.assertEqual(sec["alarm"], 1)

    def test_a_readme_only_workflow_dir_is_still_no_ci(self):
        self.answers({"a/x": self.repo(None, workflows=("README.md",))})
        self.assertEqual(self.by_repo(self.ci.read(["a/x"]))["a/x"]["ci"], "none")

    def test_a_repo_we_cannot_read_is_unknown_and_counts_as_an_alarm(self):
        """Never green. Not looking and being fine are different facts."""
        self.answers({"a/ok": self.repo(self.rollup("SUCCESS"))},
                     errors=["Could not resolve to a Repository named 'a/secret'."])
        sec = self.ci.read(["a/ok", "a/secret"])
        rows = self.by_repo(sec)
        self.assertEqual(rows["a/secret"]["ci"], "unknown")
        self.assertEqual(rows["a/ok"]["ci"], "passing")
        self.assertEqual(sec["alarm"], 1)
        self.assertIn("secret", sec["detail"])

    def test_a_cancelled_required_job_is_failing(self):
        self.answers({"a/x": self.repo(self.rollup("FAILURE", [
            {"name": "e2e", "conclusion": "CANCELLED",
             "completedAt": "2026-08-26T09:00:00Z", "detailsUrl": "run/7"}]))})
        row = self.by_repo(self.ci.read(["a/x"]))["a/x"]
        self.assertEqual(row["ci"], "failing")
        self.assertEqual([j["name"] for j in row["failing"]], ["e2e"])

    # -- the failing job, and the link to it -----------------------------------

    def test_the_failing_jobs_are_named_and_linked_and_the_green_ones_are_not(self):
        self.answers({"a/x": self.repo(self.rollup("FAILURE", [
            {"name": "lint", "conclusion": "SUCCESS",
             "completedAt": "2026-08-26T09:00:00Z", "detailsUrl": "run/ok"},
            {"name": "test (node 22)", "conclusion": "FAILURE",
             "completedAt": "2026-08-26T09:05:00Z", "detailsUrl": "run/bad"},
            {"context": "ci/legacy", "state": "ERROR", "targetUrl": "run/legacy",
             "createdAt": "2026-08-26T09:02:00Z"},
        ]))})
        row = self.by_repo(self.ci.read(["a/x"]))["a/x"]
        self.assertEqual([j["name"] for j in row["failing"]], ["test (node 22)", "ci/legacy"])
        self.assertEqual(row["run_url"], "run/bad")
        self.assertEqual(row["branch"], "main")

    # -- the age ---------------------------------------------------------------

    def test_the_check_time_comes_from_the_checks_and_never_from_the_commit(self):
        """A commit's date served as a check's date is an estimate with a
        measurement's face on, which this repo does not do anywhere."""
        self.answers({
            "a/timed": self.repo(self.rollup("SUCCESS", [
                {"name": "t", "conclusion": "SUCCESS",
                 "completedAt": "2026-08-26T09:30:00Z", "detailsUrl": "u"},
                {"name": "u", "conclusion": "SUCCESS",
                 "completedAt": "2026-08-26T09:10:00Z", "detailsUrl": "u"}]),
                committed="2026-08-20T00:00:00Z"),
            "a/untimed": self.repo(None, workflows=(), committed="2026-08-20T00:00:00Z"),
        })
        rows = self.by_repo(self.ci.read(["a/timed", "a/untimed"]))
        # The newest completion, not the oldest and not the commit.
        self.assertEqual(rows["a/timed"]["checked_at"], "2026-08-26T09:30:00Z")
        self.assertEqual(rows["a/timed"]["commit_at"], "2026-08-20T00:00:00Z")
        self.assertIsNone(rows["a/untimed"]["checked_at"])
        self.assertEqual(rows["a/untimed"]["commit_at"], "2026-08-20T00:00:00Z")

    def test_the_board_says_when_it_was_asked(self):
        self.answers({"a/x": self.repo(self.rollup("SUCCESS"))})
        sec = self.ci.read(["a/x"])
        self.assertTrue(sec["fetched_at"].endswith("Z"))
        self.assertLess(sec["age_s"], 5)

    # -- the rate limit --------------------------------------------------------

    def test_ninety_repos_cost_three_calls_not_ninety(self):
        repos = [f"a/r{i}" for i in range(90)]
        self.answers({r: self.repo(self.rollup("SUCCESS")) for r in repos})
        sec = self.ci.read(repos)
        self.assertEqual(sec["checked"], 90)
        self.assertEqual(sec["counts"]["passing"], 90)
        self.assertEqual(len(self.calls.read_text().strip().splitlines()), 3)

    def test_a_second_read_inside_the_ttl_spends_nothing(self):
        self.answers({"a/x": self.repo(self.rollup("SUCCESS"))})
        self.ci.read(["a/x"])
        self.ci.read(["a/x"])
        self.assertEqual(len(self.calls.read_text().strip().splitlines()), 1)

    def test_a_new_desk_invalidates_the_cache(self):
        """A repo that got a desk since the last push has never been looked at,
        and answering for it out of a cache that predates it would be a guess."""
        self.answers({"a/x": self.repo(self.rollup("SUCCESS")),
                      "a/y": self.repo(self.rollup("FAILURE"))})
        self.ci.read(["a/x"])
        sec = self.ci.read(["a/x", "a/y"])
        self.assertEqual(len(self.calls.read_text().strip().splitlines()), 2)
        self.assertEqual(self.by_repo(sec)["a/y"]["ci"], "failing")

    def test_a_zero_ttl_asks_every_time(self):
        os.environ["OFFICE_CI_TTL_S"] = "0"
        self.answers({"a/x": self.repo(self.rollup("SUCCESS"))})
        self.ci.read(["a/x"])
        self.ci.read(["a/x"])
        self.assertEqual(len(self.calls.read_text().strip().splitlines()), 2)

    # -- the source itself breaking -------------------------------------------

    def test_a_failed_refresh_serves_the_last_board_and_says_so(self):
        self.answers({"a/x": self.repo(self.rollup("SUCCESS"))})
        self.ci.read(["a/x"])
        os.environ["OFFICE_CI_TTL_S"] = "0"
        self.gh("import sys; sys.stderr.write('gh: rate limit exceeded\\n'); sys.exit(1)")
        sec = self.ci.read(["a/x"])
        self.assertEqual(sec["state"], "cached")
        self.assertEqual(self.by_repo(sec)["a/x"]["ci"], "passing")
        self.assertIn("rate limit", sec["detail"])
        # And the age is what stops the stale board reading as a fresh one.
        self.assertGreaterEqual(sec["age_s"], 0)
        self.assertTrue(sec["fetched_at"])

    def test_a_broken_gh_with_no_cache_is_unreadable_and_never_empty(self):
        self.gh("import sys; sys.stderr.write('gh: not logged in\\n'); sys.exit(1)")
        sec = self.ci.read(["a/x"])
        self.assertEqual(sec["state"], "unreadable")
        self.assertIn("not logged in", sec["detail"])

    def test_a_hung_gh_is_its_own_state_and_not_a_quiet_room(self):
        self.ci.TIMEOUT_S = 1
        self.gh("import time; time.sleep(30)")
        sec = self.ci.read(["a/x"])
        self.assertEqual(sec["state"], "unreadable")
        self.assertIn("did not answer", sec["detail"])

    def test_no_desks_is_its_own_state(self):
        sec = self.ci.read([])
        self.assertEqual(sec["state"], "no-desks")
        self.assertFalse(self.calls.exists())

    def test_a_bad_repo_name_never_reaches_gh(self):
        sec = self.ci.read(["not-a-repo", ""])
        self.assertEqual(sec["state"], "no-desks")


class SectionsTest(unittest.TestCase):
    """The one seam this fixture opened: a source may ask for the desk list."""

    def test_only_a_source_that_asks_is_given_the_repos(self):
        import sections
        self.assertTrue(getattr(sections.ci, "NEEDS_REPOS", False))
        for mod in sections.SOURCES:
            if mod is sections.ci:
                continue
            self.assertFalse(getattr(mod, "NEEDS_REPOS", False), mod.KEY)

    def test_one_broken_source_does_not_cost_the_snapshot(self):
        import sections

        class Boom:
            KEY = "boom"
            NEEDS_REPOS = True

            @staticmethod
            def read(repos):
                raise RuntimeError("nope")

        original = sections.SOURCES
        try:
            sections.SOURCES = [Boom]
            out = sections.read_all(["a/x"])
        finally:
            sections.SOURCES = original
        self.assertEqual(out["boom"]["state"], "error")


if __name__ == "__main__":
    unittest.main()
