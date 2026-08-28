"""Is the issue pipeline working right now, and can this source be trusted to say.

The test that matters here is the STALE PID. dispatch.sh writes its own pid and
removes it in an EXIT trap, so a killed run leaves the file behind. Reporting
that as "a run is in flight" would be exactly the false-green this project
exists to kill, and it would be indistinguishable from the real thing to anyone
reading the room.

Everything else is the same discipline: switched off, unreadable, unconfigured
and genuinely idle are four different answers and none of them is allowed to
render as another.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from test_sections import assert_card

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

JOB_ID = "com.nexus.issue-dispatch"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class PipelineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.pipeline_dir = self.root / "_meta" / "services" / "issue-pipeline"
        self.runtime = self.pipeline_dir / ".runtime"
        self.runtime.mkdir(parents=True)
        (self.root / "_meta" / "state").mkdir(parents=True)
        (self.root / "_meta" / "logs" / "jobs").mkdir(parents=True)
        (self.root / "_meta" / "services" / "jobs").mkdir(parents=True)
        self.registry(enabled=True)
        self.spawned = []
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root)
        self.mod = self.reload()

    def tearDown(self):
        for proc in self.spawned:
            # The whole group, not just the shell: a bash script's `sleep` child
            # survives a terminate on its parent and holds the inherited pipe
            # open, which quietly adds two minutes to `npm test`.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        # A log made unreadable on purpose has to come back before the temp dir
        # can be removed.
        try:
            self.log_path.chmod(0o644)
        except OSError:
            pass
        self.tmp.cleanup()

    def reload(self):
        from sources import pipeline
        return importlib.reload(pipeline)

    # -- fixtures --------------------------------------------------------------

    @property
    def pid_path(self):
        return self.runtime / "pid"

    @property
    def log_path(self):
        return self.root / "_meta" / "logs" / "jobs" / f"{JOB_ID}.log"

    def registry(self, enabled=True, seconds=3600):
        path = self.root / "_meta" / "services" / "jobs" / "registry.jsonl"
        path.write_text(json.dumps({
            "id": JOB_ID, "state": "enabled" if enabled else "disabled",
            "command": ["/bin/bash", "dispatch.sh"],
            "schedule": {"kind": "interval", "seconds": seconds},
        }) + "\n", encoding="utf-8")

    def heartbeat(self, ago_s):
        """The pipeline's own last-success stamp. Stands in for jobctl, which a
        fake root does not have, and is the fallback the source declares."""
        (self.runtime / "last-success").write_text(_iso(time.time() - ago_s), encoding="utf-8")

    def log(self, lines):
        self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def receipts(self, rows, extra=()):
        """The runner's own receipts. `rows` are (repo, seconds ago), `extra`
        are raw lines, so a torn one can be written on purpose."""
        lines = [json.dumps({"at": _iso(time.time() - ago), "repo": repo,
                             "outcome": "survey", "detail": ""})
                 for repo, ago in rows]
        lines.extend(extra)
        (self.runtime / "receipts.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def dead_pid(self) -> int:
        """A pid that is definitely not running: spawn a process and reap it."""
        proc = subprocess.Popen(["/bin/bash", "-c", "exit 0"])
        proc.wait()
        return proc.pid

    def live_dispatch(self) -> int:
        """A real dispatch.sh, actually running, exactly as the vault would have."""
        script = self.pipeline_dir / "dispatch.sh"
        script.write_text("#!/bin/bash\nsleep 60\n", encoding="utf-8")
        script.chmod(0o755)
        # Its own session so the whole group can be killed, and no inherited
        # stdout, so a test process never holds the caller's pipe open.
        proc = subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(proc)
        return proc.pid

class PipelineTest(PipelineBase):
    # -- the one that matters --------------------------------------------------

    def test_a_stale_pid_file_never_reports_a_run(self):
        # dispatch removes its pid in an EXIT trap, so a killed run leaves the
        # file behind. The file existing is a claim, not liveness.
        pid = self.dead_pid()
        self.pid_path.write_text(f"{pid}\n", encoding="utf-8")
        out = self.mod.read()
        self.assertFalse(out["running"])
        self.assertEqual(out["state"], "ok")
        self.assertIsNotNone(out["stale_pid"])
        self.assertEqual(out["stale_pid"]["pid"], pid)
        self.assertIn("not running", out["stale_pid"]["why"])
        # And it does not leak into the field the room reads as "in flight".
        self.assertIsNone(out["running_for"])

    def test_a_recycled_pid_is_stale_even_though_the_process_exists(self):
        # kill -0 alone cannot tell a live dispatch from whatever else the OS
        # handed that number to next. This test process is a fine stand-in.
        self.pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        out = self.mod.read()
        self.assertFalse(out["running"])
        self.assertIsNotNone(out["stale_pid"])
        self.assertIn("not dispatch.sh", out["stale_pid"]["why"])

    def test_a_pid_file_that_is_not_a_pid_is_stale_not_a_crash(self):
        self.pid_path.write_text("not a pid\n", encoding="utf-8")
        out = self.mod.read()
        self.assertFalse(out["running"])
        self.assertIsNotNone(out["stale_pid"])

    # -- a run that is genuinely alive ----------------------------------------

    def test_a_live_dispatch_reports_running_with_what_it_is_doing(self):
        pid = self.live_dispatch()
        self.pid_path.write_text(f"{pid}\n", encoding="utf-8")
        self.log([
            "[2026-08-26T05:38:43Z]   survey: 161 open issue(s); 95 to work this run.",
            "[2026-08-26T06:13:40Z]   #202: claude in a scratch worktree, 9m cap.",
        ])
        out = self.mod.read()
        self.assertTrue(out["running"])
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["pid"], pid)
        self.assertTrue(out["pid_verified"])
        self.assertIsNone(out["stale_pid"])
        self.assertIsNotNone(out["running_for"])
        self.assertIn("#202", out["doing"])
        # The timestamp is stripped: the room shows the sentence, not the log line.
        self.assertNotIn("2026-08-26T06:13:40Z", out["doing"])
        self.assertEqual(out["last_said_at"], "2026-08-26T06:13:40Z")

    def test_an_untimestamped_last_line_still_reads_as_what_it_is_doing(self):
        # Continuation rows under a timestamped line carry no stamp of their own.
        pid = self.live_dispatch()
        self.pid_path.write_text(f"{pid}\n", encoding="utf-8")
        self.log([
            "[2026-08-26T06:13:40Z]   1 open: 0 to work, 1 waiting on a human.",
            "    #3       waiting-human  the bot spoke last",
        ])
        out = self.mod.read()
        self.assertIn("#3", out["doing"])
        self.assertEqual(out["last_said_at"], "2026-08-26T06:13:40Z")

    # -- switched off is never idle -------------------------------------------

    def test_the_kill_switch_is_its_own_state_not_an_idle_room(self):
        (self.root / "_meta" / "state" / "pipeline-off").write_text("", encoding="utf-8")
        self.heartbeat(ago_s=600)
        out = self.mod.read()
        self.assertEqual(out["state"], "off")
        self.assertFalse(out["running"])
        self.assertTrue(out["kill_switch"])
        self.assertIn("kill switch", out["detail"])

    def test_a_run_in_flight_outranks_a_switch_flipped_underneath_it(self):
        pid = self.live_dispatch()
        self.pid_path.write_text(f"{pid}\n", encoding="utf-8")
        (self.root / "_meta" / "state" / "pipeline-off").write_text("", encoding="utf-8")
        out = self.mod.read()
        self.assertTrue(out["running"])
        self.assertEqual(out["state"], "ok")
        # The switch is still reported; it just does not erase a live run.
        self.assertTrue(out["kill_switch"])

    def test_a_job_disabled_in_the_registry_is_off_not_idle(self):
        self.registry(enabled=False)
        out = self.mod.read()
        self.assertEqual(out["state"], "off")
        self.assertFalse(out["enabled"])

    def test_a_job_missing_from_the_registry_is_off_because_nothing_fires_it(self):
        (self.root / "_meta" / "services" / "jobs" / "registry.jsonl").write_text(
            json.dumps({"id": "com.nexus.something-else"}) + "\n", encoding="utf-8")
        out = self.mod.read()
        self.assertEqual(out["state"], "off")
        self.assertEqual(out["schedule_state"], "unregistered")

    # -- when it next looks ----------------------------------------------------

    def test_an_idle_pipeline_says_when_it_next_looks(self):
        self.heartbeat(ago_s=42 * 60 - 5)
        out = self.mod.read()
        self.assertEqual(out["state"], "ok")
        self.assertFalse(out["running"])
        self.assertEqual(out["next_in"], "18m")
        self.assertFalse(out["overdue"])
        self.assertEqual(out["next_source"], "heartbeat")
        self.assertEqual(out["every"], "1h")

    def test_a_schedule_that_has_already_slipped_says_so_instead_of_zero(self):
        self.heartbeat(ago_s=3 * 3600 + 5)
        out = self.mod.read()
        self.assertTrue(out["overdue"])
        self.assertEqual(out["next_in"], "any moment now")
        self.assertEqual(out["late_by"], "2h")

    def test_no_receipt_at_all_leaves_the_next_look_unknown_rather_than_invented(self):
        out = self.mod.read()
        self.assertIsNone(out["next_in"])
        self.assertEqual(out["next_source"], "unknown")
        self.assertIn("could not be worked out", out["detail"])

    # -- the source failing to tell -------------------------------------------

    def test_an_unreadable_log_is_not_a_quiet_pipeline(self):
        self.log(["[2026-08-26T06:13:40Z] working"])
        self.log_path.chmod(0o000)
        if os.access(self.log_path, os.R_OK):
            self.skipTest("running as a user that can read anything; no unreadable log to test")
        out = self.mod.read()
        self.assertEqual(out["log_state"], "unreadable")
        self.assertEqual(out["last_said"], "")

    def test_a_missing_log_is_its_own_state(self):
        out = self.mod.read()
        self.assertEqual(out["log_state"], "missing")

    def test_a_missing_runtime_dir_is_not_a_run(self):
        import shutil
        shutil.rmtree(self.runtime)
        out = self.mod.read()
        self.assertFalse(out["running"])
        self.assertIsNone(out["stale_pid"])

    def test_a_root_with_no_pipeline_in_it_says_missing(self):
        import shutil
        shutil.rmtree(self.pipeline_dir)
        out = self.mod.read()
        self.assertEqual(out["state"], "missing")
        self.assertFalse(out["running"])

    def test_a_root_that_is_not_there_says_so(self):
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.root / "nope")
        out = self.reload().read()
        self.assertEqual(out["state"], "missing-root")
        self.assertFalse(out["running"])

    def test_an_unconfigured_office_never_looks_idle(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT")
        out = self.reload().read()
        self.assertEqual(out["state"], "unconfigured")
        self.assertFalse(out["running"])
        # "unconfigured" and "nothing running" are different sentences in the
        # panel, and this is the assertion that keeps them different.
        self.assertNotEqual(out["state"], "ok")

    # -- the contract the room depends on -------------------------------------

    def test_every_field_the_panel_reads_is_always_present(self):
        for setup in (lambda: None,
                      lambda: self.pid_path.write_text(str(self.dead_pid())),
                      lambda: (self.root / "_meta" / "state" / "pipeline-off").touch()):
            setup()
            out = self.mod.read()
            for field in ("state", "detail", "running", "running_for", "doing",
                          "next_in", "heartbeat", "covered"):
                self.assertIn(field, out, f"{field} missing for state {out.get('state')}")
            self.assertIsInstance(out["running"], bool)


class CoverageTest(PipelineBase):
    """What the last sweep actually reached.

    Every other field on this source answers "is it about to run". None of them
    answers "did the last run do anything", and a runner that fires on schedule
    and touches zero repos is green on all of them at once.
    """

    def test_the_last_finished_sweep_is_reported_not_just_the_next_one(self):
        # last-success is written at the END of a sweep, so a run that started
        # and died never moves it. That is what makes it worth reading.
        self.heartbeat(600)
        out = self.mod.read()
        self.assertIsNotNone(out["heartbeat"])
        self.assertLess(abs(out["heartbeat_age_s"] - 600), 5)

    def test_a_pipeline_that_never_finished_a_sweep_says_so_rather_than_zero(self):
        out = self.mod.read()
        self.assertIsNone(out["heartbeat"])
        self.assertIsNone(out["heartbeat_age_s"])

    def test_coverage_counts_repos_and_decisions_inside_the_window(self):
        self.receipts([("a/one", 60), ("a/one", 120), ("b/two", 300)])
        cover = self.mod.read()["covered"]
        self.assertEqual(cover["state"], "ok")
        self.assertEqual(cover["repos"], 2)
        self.assertEqual(cover["receipts"], 3)

    def test_receipts_older_than_the_window_are_not_this_run(self):
        # A rolling 24 hours, the same window office-sync uses for world.today,
        # so the two never describe different spans of time.
        self.receipts([("a/one", 60), ("b/two", 90000)])
        cover = self.mod.read()["covered"]
        self.assertEqual(cover["repos"], 1)
        self.assertEqual(cover["receipts"], 1)

    def test_a_torn_receipt_line_is_counted_and_never_skipped_in_silence(self):
        self.receipts([("a/one", 60)], extra=['{"at": "2026-08-27T0'])
        cover = self.mod.read()["covered"]
        self.assertEqual(cover["receipts"], 1)
        self.assertEqual(cover["unparsed"], 1)

    def test_no_receipts_file_is_missing_rather_than_zero_repos(self):
        cover = self.mod.read()["covered"]
        self.assertEqual(cover["state"], "missing")
        self.assertIsNone(cover["repos"])

    def test_a_sweep_that_reached_nothing_is_visible_on_the_card(self):
        # The whole point: on time, no errors, and it did not touch a repo.
        self.heartbeat(600)
        self.receipts([])
        card = self.mod.card(self.mod.read())
        assert_card(self, card)
        facts = {f["label"]: f for f in card["facts"]}
        self.assertIn("0 repos in 24h", facts["last full run"]["value"])
        self.assertEqual(facts["last full run"]["tone"], "warn")

    def test_a_healthy_sweep_says_when_and_how_wide(self):
        self.heartbeat(600)
        self.receipts([("a/one", 60), ("b/two", 300)])
        facts = {f["label"]: f["value"]
                 for f in self.mod.card(self.mod.read())["facts"]}
        self.assertIn("2 repos in 24h", facts["last full run"])
        self.assertIn("ago", facts["last full run"])

    def test_receipts_nobody_can_read_is_a_hole_that_wants_a_person(self):
        path = self.runtime / "receipts.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        path.chmod(0o000)
        try:
            self.heartbeat(600)
            data = self.mod.read()
            self.assertEqual(data["covered"]["state"], "unreadable")
            card = self.mod.card(data)
            self.assertEqual(card["needs"], 1)
        finally:
            path.chmod(0o644)


class CardTest(PipelineBase):
    """`detail` is already the sentence this source exists to write, so the card
    repeats it rather than inventing a second one that can disagree."""

    def test_the_card_carries_the_sentence_the_source_already_wrote(self):
        self.heartbeat(60)
        self.log([f"[{_iso(time.time() - 120)}] looked, nothing to do"])
        data = self.mod.read()
        card = self.mod.card(data)

        assert_card(self, card)
        self.assertEqual(card["headline"], data["detail"])
        self.assertEqual(card["needs"], 0)
        facts = {f["label"]: f["value"] for f in card["facts"]}
        self.assertEqual(facts["running"], "no")
        self.assertEqual(facts["pid"], "nobody claims a run")
        self.assertIn("next look", facts)

    def test_a_pid_file_that_is_lying_is_a_thing_that_needs_a_person(self):
        # `running` stays false, which is the point of the source. The card is
        # the only place that says somebody should go and look at it.
        self.pid_path.write_text(str(self.dead_pid()))
        self.heartbeat(60)
        self.log([f"[{_iso(time.time() - 120)}] ran"])
        card = self.mod.card(self.mod.read())

        assert_card(self, card)
        self.assertEqual(card["needs"], 1)
        self.assertIn("stale pid", [f["label"] for f in card["facts"]])

    def test_a_log_nobody_can_read_needs_a_person_even_when_all_is_calm(self):
        self.heartbeat(60)
        self.log(["[2026-08-26T05:00:00Z] fine"])
        self.log_path.chmod(0o000)
        card = self.mod.card(self.mod.read())

        assert_card(self, card)
        self.assertEqual(card["needs"], 1)
        facts = {f["label"]: f["value"] for f in card["facts"]}
        self.assertIn("unreadable", facts["last said"])

    def test_a_pipeline_switched_off_on_purpose_wants_nobody(self):
        (self.root / "_meta" / "state" / "pipeline-off").touch()
        self.heartbeat(60)
        self.log([f"[{_iso(time.time() - 120)}] looked, nothing to do"])
        card = self.mod.card(self.mod.read())

        assert_card(self, card)
        self.assertIn("switched off", card["headline"])
        self.assertEqual(card["needs"], 0)
        self.assertIn("kill switch", [f["label"] for f in card["facts"]])

    def test_an_unconfigured_office_still_produces_a_card(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        card = self.reload().card(self.reload().read())
        assert_card(self, card)
        self.assertEqual(card["needs"], 0)


if __name__ == "__main__":
    unittest.main()


class OffCardTest(PipelineBase):
    def test_a_pipeline_switched_off_with_no_log_is_not_an_alarm(self):
        # Off on purpose, never ran: nothing to look at, so nothing needs a person.
        (self.root / "_meta" / "state" / "pipeline-off").write_text("", encoding="utf-8")
        out = self.mod.read()
        self.assertEqual(out["state"], "off")
        self.assertEqual(self.mod.card(out)["needs"], 0)


class LaneTest(PipelineBase):
    """The lanes are the work; the sweep is the few seconds that starts them.

    Since 2026-08-28 a lane is a detached process that outlives the dispatcher,
    so reading `.runtime/pid` alone reports an idle pipeline while five lanes
    are building code. Every assertion here is that specific false green, or its
    mirror: a lane directory left behind by a process that is gone must never be
    reported as work in progress.
    """

    def lane(self, repo: str, issue: str, pid: int, quiet_s: float = 0.0):
        d = self.runtime / "lanes" / f"{repo.replace('/', '_')}__{issue}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "pid").write_text(str(pid), encoding="utf-8")
        (d / "repo").write_text(f"/repos/{repo}", encoding="utf-8")
        (d / "started").write_text(_iso(time.time() - 600), encoding="utf-8")
        log = d / "log"
        log.write_text("working\n", encoding="utf-8")
        if quiet_s:
            old = time.time() - quiet_s
            os.utime(log, (old, old))
        return d

    def test_lanes_are_running_even_when_no_sweep_holds_the_pid(self):
        # The exact regression: the dispatcher exits after starting lanes, so
        # the pid file is absent for almost all of the time work is happening.
        self.lane("acme/thing", "42", self.live_dispatch())
        data = self.mod.read()
        self.assertTrue(data["running"], "lanes are work, so the pipeline is running")
        self.assertFalse(data["sweeping"], "no sweep holds the lock")
        self.assertEqual(data["lane_count"], 1)

    def test_a_lane_whose_process_is_gone_is_not_work_in_progress(self):
        self.lane("acme/thing", "42", self.dead_pid())
        data = self.mod.read()
        self.assertEqual(data["lane_count"], 0)
        self.assertFalse(data["running"])

    def test_a_lane_that_is_merely_quiet_is_still_working(self):
        # Quiet is not lateness. A lane thinking hard says nothing for minutes,
        # and this card must not invent a fault out of that.
        self.receipts([("acme/thing", 60)])
        self.log([f"[{_iso(time.time() - 60)}] working acme/thing#42"])
        self.lane("acme/thing", "42", self.live_dispatch(), quiet_s=1800)
        card = self.mod.card(self.mod.read())
        self.assertEqual(card["needs"], 0, "a working lane needs nobody")
        row = [r for r in card["rows"] if r["group"] == "lanes"][0]
        self.assertEqual(row["tone"], "ok")

    def test_the_headline_says_what_is_being_worked_not_which_pid(self):
        self.lane("acme/thing", "42", self.live_dispatch())
        self.lane("acme/other", "7", self.live_dispatch())
        card = self.mod.card(self.mod.read())
        self.assertIn("2 issues being worked", card["headline"])

    def test_the_queue_is_the_last_sweep_only(self):
        now = time.time()
        self.receipts([], extra=[
            json.dumps({"at": _iso(now - 30), "repo": "acme/thing", "issue": "",
                        "outcome": "waiting", "detail": "3 issue(s) waiting for a free lane"}),
            # An hour old: that queue has already drained, and adding it would
            # grow a backlog that does not exist.
            json.dumps({"at": _iso(now - 3600), "repo": "old/repo", "issue": "",
                        "outcome": "waiting", "detail": "9 issue(s) waiting for a free lane"}),
        ])
        data = self.mod.read()
        self.assertEqual(data["queue_total"], 3)
        self.assertEqual([q["repo"] for q in data["queue"]], ["acme/thing"])

    def test_a_queue_with_no_lanes_still_says_so_on_the_card(self):
        self.receipts([], extra=[
            json.dumps({"at": _iso(time.time() - 30), "repo": "acme/thing", "issue": "",
                        "outcome": "waiting", "detail": "2 issue(s) waiting for a free lane"}),
        ])
        card = self.mod.card(self.mod.read())
        self.assertIn("2 waiting", card["facts"][0]["value"])
        self.assertEqual([r["group"] for r in card["rows"]], ["queue"])

    def test_no_lanes_directory_is_an_idle_pipeline_not_a_broken_one(self):
        self.receipts([("acme/thing", 60)])
        self.log([f"[{_iso(time.time() - 60)}] nothing to do"])
        data = self.mod.read()
        self.assertEqual(data["lanes_state"], "none")
        self.assertEqual(data["lane_count"], 0)
        self.assertEqual(self.mod.card(data)["needs"], 0)
