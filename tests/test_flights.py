import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from nexus import flights


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class RunnerCancellationTest(unittest.TestCase):
    def runner(self, workspace, cmd):
        return subprocess.Popen(
            [sys.executable, "-m", "nexus", "flight-run", "--workspace", workspace,
             "--cmd", cmd, "--timeout", "60"],
            start_new_session=True,
        )

    def test_runner_cancels_nested_groups_in_its_owned_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeout_file = os.path.join(tmp, "timeout")
            child_file = os.path.join(tmp, "child")
            cmd = (f"timeout 900 /bin/sh -c 'echo $$ > {child_file}; sleep 900' & "
                   f"echo $! > {timeout_file}; wait")
            runner = self.runner(tmp, cmd)
            self.addCleanup(lambda: runner.poll() is None and runner.kill())
            self.assertTrue(wait_for(lambda: os.path.exists(child_file)
                                     and os.path.exists(timeout_file)))
            with open(child_file) as handle:
                child = int(handle.read())
            with open(timeout_file) as handle:
                timeout_pid = int(handle.read())
            self.assertEqual(timeout_pid, os.getpgid(child))

            os.kill(runner.pid, signal.SIGTERM)
            runner.wait(timeout=5)

            result, error = flights.read_result(tmp)
            self.assertIsNone(error)
            self.assertEqual("cancelled", result["error"]["code"])
            self.assertTrue(wait_for(lambda: not flights.alive(child)))
            self.assertFalse(flights.alive(timeout_pid))
            self.assertTrue(flights.teardown_confirmed(tmp, runner.pid))

    def test_runner_reaps_background_work_after_its_group_leader_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_file = os.path.join(tmp, "child")
            command = (
                "python3 -c \"import subprocess; "
                "p=subprocess.Popen(['sleep','900'], process_group=0); "
                f"open('{child_file}','w').write(str(p.pid))\""
            )
            runner = self.runner(tmp, command)
            self.addCleanup(lambda: runner.poll() is None and runner.kill())
            runner.wait(timeout=5)
            with open(child_file) as handle:
                child = int(handle.read())

            self.assertTrue(wait_for(lambda: not flights.alive(child)))
            with open(os.path.join(tmp, flights.RESULT_NAME)) as handle:
                result = json.load(handle)
            self.assertTrue(result["ok"])
            self.assertTrue(flights.teardown_confirmed(tmp, runner.pid))


class KillContractTest(unittest.TestCase):
    @mock.patch("nexus.flights.subprocess.check_output", side_effect=OSError("no ps"))
    def test_session_enumeration_failure_is_not_confirmed(self, _check):
        self.assertFalse(flights._kill_owned_session(123, timeout_s=0.1))

    @mock.patch("nexus.flights.time.sleep")
    @mock.patch("nexus.flights.alive", return_value=True)
    @mock.patch("nexus.flights.os.kill")
    def test_unresponsive_runner_is_escalated_but_not_reported_clean(self, kill, _alive, _sleep):
        self.assertFalse(flights.kill(123, grace_s=0))
        self.assertEqual(
            [mock.call(123, signal.SIGTERM), mock.call(123, signal.SIGKILL)],
            kill.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
