import os
import signal
import subprocess
import tempfile
import time
import unittest

from nexus import flights


class KillTest(unittest.TestCase):
    def test_kill_reaches_a_child_that_started_its_own_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_file = os.path.join(tmp, "child")
            script = (
                "import os, subprocess, sys, time; "
                "p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "start_new_session=True); "
                f"open({child_file!r}, 'w').write(str(p.pid)); "
                "time.sleep(60)"
            )
            root = subprocess.Popen(
                ["python3", "-c", script], start_new_session=True)
            self.addCleanup(lambda: flights.kill(root.pid))
            deadline = time.time() + 5
            while not os.path.exists(child_file) and time.time() < deadline:
                time.sleep(0.02)
            with open(child_file) as handle:
                child = int(handle.read())

            flights.kill(root.pid)
            root.wait(timeout=5)
            deadline = time.time() + 5
            while flights.alive(child) and time.time() < deadline:
                time.sleep(0.02)

            self.assertFalse(flights.alive(child))

    def test_kill_freezes_a_grandchild_created_after_the_first_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_file = os.path.join(tmp, "child")
            grand_file = os.path.join(tmp, "grand")
            go_file = os.path.join(tmp, "go")
            child_script = (
                "import os, subprocess, sys, time; "
                f"open({child_file!r}, 'w').write(str(os.getpid())); "
                f"\nwhile not os.path.exists({go_file!r}): time.sleep(.01)\n"
                "p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "start_new_session=True); "
                f"open({grand_file!r}, 'w').write(str(p.pid)); "
                "time.sleep(60)"
            )
            root_script = (
                "import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
                "time.sleep(60)"
            )
            root = subprocess.Popen(["python3", "-c", root_script], start_new_session=True)
            self.addCleanup(lambda: flights.kill(root.pid))
            deadline = time.time() + 5
            while not os.path.exists(child_file) and time.time() < deadline:
                time.sleep(0.02)
            original = flights._descendants
            first = True

            def spawn_after_snapshot(pid):
                nonlocal first
                rows = original(pid)
                if first:
                    first = False
                    open(go_file, "w").close()
                    deadline = time.time() + 5
                    while not os.path.exists(grand_file) and time.time() < deadline:
                        time.sleep(0.01)
                return rows

            flights._descendants = spawn_after_snapshot
            try:
                self.assertTrue(flights.kill(root.pid))
            finally:
                flights._descendants = original
            root.wait(timeout=5)
            with open(grand_file) as handle:
                grand = int(handle.read())
            deadline = time.time() + 5
            while flights.alive(grand) and time.time() < deadline:
                time.sleep(0.02)
            self.assertFalse(flights.alive(grand))

    def test_kill_reports_enumeration_failure_and_resumes_the_runner(self):
        root = subprocess.Popen(["python3", "-c", "import time; time.sleep(60)"],
                                start_new_session=True)
        def cleanup():
            if flights.alive(root.pid):
                root.kill()
            root.wait(timeout=5)
        self.addCleanup(cleanup)
        original = flights._descendants
        flights._descendants = lambda _pid: (_ for _ in ()).throw(RuntimeError("no ps"))
        try:
            self.assertFalse(flights.kill(root.pid))
        finally:
            flights._descendants = original
        self.assertIsNone(root.poll())


if __name__ == "__main__":
    unittest.main()
