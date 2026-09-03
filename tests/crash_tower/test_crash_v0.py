"""crash-tower v0: kill things at random and demand one reading afterwards.

A real script plan, real detached flights, and a driver that runs `nexus tower
once` in a subprocess over and over while injecting four faults at random:

  * SIGKILL the tower mid-tick
  * SIGKILL a running flight
  * two ticks at the same instant
  * a malformed `result.json`

Afterwards there is exactly one true story: the integrity check passes, no flight
claims to be running behind a dead pid, every `out.txt` on disk belongs to a
`produced` flight with an artifact row, and every failure has a code.

This suite is worth more than the guards it replaces, so it must stay fast: the
whole thing runs in well under 90 seconds.
"""

import json
import os
import random
import signal
import subprocess
import sys
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from nexus import flights as fl  # noqa: E402
from nexus.ledger import Ledger, loads  # noqa: E402

ITERATIONS = 42
MIN_FAULTS = 30
DEADLINE_S = 85.0


class CrashTowerV0(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="nexus-crash-")
        self.ledger_path = os.path.join(self.dir, "ledger.sqlite")
        self.root = os.path.join(self.dir, "flights")
        self.env = dict(os.environ)
        self.env["NEXUS_LEDGER"] = self.ledger_path
        self.env["NEXUS_FLIGHTS"] = self.root
        self.env["PYTHONPATH"] = REPO + os.pathsep + self.env.get("PYTHONPATH", "")
        self.faults = {"kill_tower": 0, "kill_flight": 0, "duplicate_tick": 0,
                       "malformed_result": 0}

    def tearDown(self):
        import shutil

        led = Ledger(self.ledger_path)
        for flight in led.flights():
            fl.kill(flight["pid"])
        led.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- driving ---------------------------------------------------------

    def nexus(self, *args, wait=True, check=False):
        proc = subprocess.Popen(
            [sys.executable, "-m", "nexus", *args], cwd=REPO, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not wait:
            return proc
        out, err = proc.communicate(timeout=60)
        if check:
            self.assertEqual(0, proc.returncode, err.decode())
        return proc

    def running_flights(self, led):
        return led.flights(states=("running",))

    def test_kill_anything_and_the_ledger_still_has_one_reading(self):
        random.seed(20260903)
        started = time.time()
        self.nexus("plans", "add", "--name", "hello", "--cmd", "sleep 2; echo hi > out.txt",
                   "--every", "1", "--timeout", "20", "--output", "out.txt",
                   "--max-retries", "100", "--concurrency", "2", check=True)

        led = Ledger(self.ledger_path)
        for i in range(ITERATIONS):
            if time.time() - started > DEADLINE_S - 20:
                break
            fault = random.choice(list(self.faults))

            if fault == "kill_tower":
                proc = self.nexus("tower", "once", wait=False)
                time.sleep(random.uniform(0.0, 0.25))
                proc.kill()
                proc.communicate()
                self.faults["kill_tower"] += 1
            elif fault == "duplicate_tick":
                first = self.nexus("tower", "once", wait=False)
                second = self.nexus("tower", "once", wait=False)
                first.communicate(timeout=60)
                second.communicate(timeout=60)
                self.faults["duplicate_tick"] += 1
            else:
                self.nexus("tower", "once")
                live = self.running_flights(led)
                if not live:
                    continue
                victim = random.choice(live)
                if fault == "kill_flight":
                    fl.kill(victim["pid"])
                    self.faults["kill_flight"] += 1
                else:
                    workspace = victim["workspace"]
                    if workspace and os.path.isdir(workspace):
                        with open(os.path.join(workspace, "result.json"), "w") as handle:
                            handle.write("{ this is not json at all")
                        self.faults["malformed_result"] += 1
            time.sleep(random.uniform(0.05, 0.35))

        # settle: no more launching, just reconcile until nothing is in the air
        self.nexus("pause", check=True)
        for _ in range(25):
            self.nexus("tower", "once")
            if not self.running_flights(led):
                break
            time.sleep(0.4)

        elapsed = time.time() - started
        injected = sum(self.faults.values())

        # -- the assertions ------------------------------------------------
        self.assertGreaterEqual(injected, MIN_FAULTS,
                                f"only {injected} faults injected: {self.faults}")
        self.assertLess(elapsed, DEADLINE_S, f"crash_tower took {elapsed:.1f}s")

        self.assertEqual([], led.integrity_check(), "ledger has more than one reading")

        for flight in self.running_flights(led):
            self.fail(f"{flight['id']} is running behind pid {flight['pid']} "
                      f"(alive: {fl.alive(flight['pid'])})")

        produced = led.flights(states=("produced",))
        with_artifact = [f for f in produced if led.artifacts(f["id"])]
        on_disk = []
        for dirpath, _, filenames in os.walk(self.root):
            if "out.txt" in filenames:
                on_disk.append(os.path.join(dirpath, "out.txt"))
        self.assertEqual(len(with_artifact), len(on_disk),
                         f"{len(with_artifact)} produced flights with an artifact but "
                         f"{len(on_disk)} out.txt files")

        for flight in with_artifact:
            for artifact in led.artifacts(flight["id"]):
                self.assertTrue(os.path.exists(artifact["ref"]),
                                f"recorded artifact does not exist: {artifact['ref']}")

        failed = led.flights(states=("failed",))
        self.assertTrue(failed or produced, "the run did nothing at all")
        known = {"timeout", "vanished", "malformed_result", "missing_result",
                 "exit_nonzero", "missing_output", "spawn_failed", "cancelled"}
        for flight in failed:
            result = loads(flight["result"], {}) or {}
            code = ((result.get("error") or {}).get("code"))
            self.assertIn(code, known, f"{flight['id']} failed without a known code: "
                                       f"{json.dumps(result)}")

        led.close()


if __name__ == "__main__":
    unittest.main()
