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

    def test_runner_cancels_descendant_new_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_file = os.path.join(tmp, "detached-child")
            script = os.path.join(tmp, "spawn.py")
            with open(script, "w") as f:
                f.write("import subprocess,time,sys\np=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],start_new_session=True)\nopen(sys.argv[1],'w').write(str(p.pid))\ntime.sleep(60)\n")
            runner = self.runner(tmp, f"{sys.executable} {script} {child_file}")
            self.addCleanup(lambda: runner.poll() is None and runner.kill())
            self.assertTrue(wait_for(lambda: os.path.exists(child_file)))
            with open(child_file) as handle:
                child = int(handle.read())
            self.addCleanup(lambda: flights.alive(child) and os.kill(child, signal.SIGKILL))
            self.assertEqual(child, os.getsid(child))
            os.kill(runner.pid, signal.SIGTERM)
            runner.wait(timeout=5)
            self.assertTrue(wait_for(lambda: not flights.alive(child)))
            self.assertTrue(flights.teardown_confirmed(tmp, runner.pid))

    def test_runner_reaps_background_work_after_its_group_leader_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_file = os.path.join(tmp, "child")
            command = (
                f"{sys.executable} -c \"import subprocess; "  # process_group needs 3.11; a login PATH's python3 is 3.9
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


class StartFailureTest(unittest.TestCase):
    DEAD = (b"Python path configuration:\n  PYTHONHOME = (not set)\n"
            b"Fatal Python error: Failed to import encodings module\n"
            b"InterruptedError: [Errno 4] Interrupted system call: '/Volumes/x/release'\n")

    def test_the_shell_not_finding_the_command_is_a_start_failure(self):
        said = b"env: /x/.venv/bin/python: No such file or directory\n"
        self.assertEqual("exit 127: env: /x/.venv/bin/python: No such file or directory",
                         flights.start_failure(127, 0.02, said))
        self.assertTrue(flights.start_failure(126, 0.02, b"sh: ./job: Permission denied\n"))

    def test_an_interpreter_that_died_initialising_is_a_start_failure(self):
        self.assertIn("Interrupted system call", flights.start_failure(1, 0.4, self.DEAD))
        old = b"Fatal Python error: init_fs_encoding: failed to get the Python codec\n"
        self.assertTrue(flights.start_failure(1, 9.0, old))

    def test_work_that_ran_and_failed_is_not(self):
        self.assertIsNone(flights.start_failure(1, 0.1, b"Traceback\nValueError: bad row\n"))
        self.assertIsNone(flights.start_failure(127, 90.0, b"sh: jq: command not found\n"))
        self.assertIsNone(flights.start_failure(0, 0.1, self.DEAD))

    def test_the_runner_records_it_under_its_own_code(self):
        with tempfile.TemporaryDirectory() as workspace:
            subprocess.run([sys.executable, "-m", "nexus", "flight-run", "--workspace", workspace,
                            "--cmd", "/nonexistent/python job.py", "--timeout", "30"], check=False)
            error = flights.read_result(workspace)[0]["error"]
            self.assertEqual(("start_failed", 127), (error["code"], error["exit_code"]))
            subprocess.run([sys.executable, "-m", "nexus", "flight-run", "--workspace", workspace,
                            "--cmd", "exit 3", "--timeout", "30"], check=False)
            self.assertEqual("exit_nonzero", flights.read_result(workspace)[0]["error"]["code"])


class KillContractTest(unittest.TestCase):
    @mock.patch("nexus.flights.subprocess.run")
    def test_empty_pid_inventory_is_already_stopped(self, run):
        self.assertEqual([], flights._live_pids([]))
        run.assert_not_called()

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
