"""A tower flight keeps its executor's output, and keeps it even when the flight is killed.

Both #235 flights were SIGKILLed mid-run; `nexus log` answered "no log kept" and the diagnosis had to
come from Claude's own transcripts. Output streamed to a file survives the kill; output held in a
pipe until exit does not."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus import executor, work
from tests import test_tower_contract


def child(code):
    return [sys.executable, "-c", code]


class InvokeLog(unittest.TestCase):
    def setUp(self):
        self.log = Path(tempfile.mkdtemp()) / "flight.log"

    def test_output_lands_in_the_log_and_is_still_returned(self):
        proc = executor.invoke(child("import sys; print('out-1'); print('err-1', file=sys.stderr)"),
                               cwd=".", env=None, input=None, timeout=30, run=subprocess.run, log=str(self.log))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "out-1")
        self.assertIn("err-1", proc.stderr)
        text = self.log.read_text()
        self.assertIn("out-1", text)
        self.assertIn("err-1", text)

    def test_stderr_written_before_a_kill_survives_it(self):
        proc = executor.invoke(child("import sys,time; print('before-kill', file=sys.stderr, flush=True); time.sleep(30)"),
                               cwd=".", env=None, input=None, timeout=1, run=subprocess.run, log=str(self.log))
        self.assertEqual(proc.returncode, 124)
        self.assertIn("before-kill", self.log.read_text())

    def test_the_log_appends_across_invocations(self):
        for word in ("first", "second"):
            executor.invoke(child(f"print('{word}')"), cwd=".", env=None, input=None, timeout=30,
                            run=subprocess.run, log=str(self.log))
        text = self.log.read_text()
        self.assertLess(text.index("first"), text.index("second"))

    def test_without_a_log_nothing_changes(self):
        proc = executor.invoke(child("import sys; print('o'); print('e', file=sys.stderr)"),
                               cwd=".", env=None, input=None, timeout=30, run=subprocess.run)
        self.assertEqual((proc.stdout.strip(), proc.stderr.strip()), ("o", "e"))


class FlyLog(unittest.TestCase):
    def test_fly_hands_its_log_to_every_provider_invocation(self):
        seen = []

        def fake_invoke(argv, **kw):
            seen.append(kw.get("log"))
            return subprocess.CompletedProcess(argv, 0, "", "")

        entry = {"path": "/repo", "repo": "o/r", "default_branch": "main"}
        with patch("nexus.executor.plan", return_value=(["claude", "-p", "x"], "x", None, False)), \
                patch("nexus.executor.lease.recover", return_value=None), \
                patch("nexus.executor.lanes.recover", return_value=[]), \
                patch("nexus.executor.lease.acquire", return_value={}), \
                patch("nexus.executor.lease.release"), \
                patch("nexus.executor._land", return_value={"state": "NO_CHANGE"}), \
                patch("nexus.executor.landing.require_terminal"), \
                patch("nexus.executor.invoke", side_effect=fake_invoke):
            executor.fly(entry, {"number": 1}, "flt_x", pr_create=None, comment=None, log="/tmp/flt_x.log")
        self.assertEqual(seen, ["/tmp/flt_x.log"])


class TowerExecuteLog(unittest.TestCase):
    github = test_tower_contract.TowerYield.github
    setUp = test_tower_contract.TowerYield.setUp  # the fixture only, not its tests

    def test_tower_execute_keeps_a_log_artifact_and_hands_it_to_fly(self):
        with patch("nexus.executor.fly", side_effect=RuntimeError("stop after launch")) as fly:
            work.tower_execute(self.led, self.entry, self.task)
        fid = self.led.flights()[0]["id"]
        logs = [a["ref"] for a in self.led.artifacts(fid) if a["kind"] == "log"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(Path(logs[0]).name, f"{fid}.log")
        self.assertEqual(Path(logs[0]).parent, Path(self.led.path).resolve().parent / "logs")
        self.assertTrue(os.path.exists(logs[0]))
        self.assertEqual(fly.call_args.kwargs.get("log"), logs[0])


if __name__ == "__main__":
    unittest.main()
