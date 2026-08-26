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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

JOB_ID = "com.nexus.issue-dispatch"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class PipelineTest(unittest.TestCase):
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
            for field in ("state", "detail", "running", "running_for", "doing", "next_in"):
                self.assertIn(field, out, f"{field} missing for state {out.get('state')}")
            self.assertIsInstance(out["running"], bool)


if __name__ == "__main__":
    unittest.main()
