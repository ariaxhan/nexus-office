"""crash-tower: the radio hangs forever while a flight exits.

This is the hcom failure, reproduced on purpose: a stop-time transport that
never answers. The flight must still terminate, record its outcome, release
its lease, and the next flight on the same resource must run. Nothing waits
for the radio, and the hung transport does not survive the flight.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from nexus import flights as fl  # noqa: E402
from nexus import radio, tower  # noqa: E402
from nexus.ledger import Ledger  # noqa: E402


def wait_for(predicate, timeout=20.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def pids_matching(marker):
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


class RadioHang(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nexus-radio-")
        self.root = os.path.join(self.dir, "flights")
        self.led = Ledger(os.path.join(self.dir, "ledger.sqlite"))
        self.marker = f"radio-hang-{uuid.uuid4().hex}"
        self.saved = {k: os.environ.get(k) for k in (radio.ENV_TRANSPORT, radio.ENV_TIMEOUT)}
        # a radio that reads nothing and never returns
        os.environ[radio.ENV_TRANSPORT] = f"exec sleep 100000 # {self.marker}"
        os.environ[radio.ENV_TIMEOUT] = "1"

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for pid in pids_matching(self.marker):
            fl.kill(pid)
        for flight in self.led.flights():
            fl.kill(flight["pid"])
        self.led.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def tick(self):
        return tower.tick(self.led, root=self.root)

    def test_flight_completes_and_next_one_runs_while_radio_hangs(self):
        plan = self.led.add_plan(
            "hang", schedule={"every": 0.1}, inputs={"cmd": "echo hi > out.txt"},
            outputs=["out.txt"], budget={"timeout_s": 30, "max_retries": 1, "concurrency": 1},
            resolution_policy={"may_accept": True}, resources=["repo:radio-hang"])

        started = time.time()
        self.tick()
        first = self.led.flights(states=("running",))
        self.assertEqual(1, len(first), "first flight did not launch")
        first = first[0]
        self.assertTrue(self.led.leases(), "flight holds no lease to release")

        # the flight terminates: pid gone, result read, lease released
        self.assertTrue(wait_for(lambda: (self.tick(), self.led.flight(first["id"])["state"]
                                          == "produced")[1]),
                        f"flight stuck in {self.led.flight(first['id'])['state']}")
        self.assertLess(time.time() - started, 15.0, "termination waited on the radio")
        self.assertTrue(wait_for(lambda: not fl.alive(first["pid"])),
                        "runner process outlived its result")
        self.assertEqual([], [l for l in self.led.leases() if l["holder_flight"] == first["id"]],
                         "lease not released")
        self.assertEqual([], pids_matching(self.marker), "hung radio survived the flight")

        # the next flight on the same resource runs and lands
        def second_produced():
            self.tick()
            done = [f for f in self.led.flights(plan_id=plan, states=("produced",))
                    if f["id"] != first["id"]]
            return bool(done)
        self.assertTrue(wait_for(second_produced), "second flight never produced")
        self.assertEqual([], self.led.integrity_check())
        self.assertEqual([], pids_matching(self.marker))

    def test_notify_is_bounded_and_never_raises(self):
        os.environ[radio.ENV_TIMEOUT] = "0.5"
        t0 = time.time()
        out = radio.notify("probe", {"x": 1})
        self.assertEqual("timeout", out["outcome"])
        self.assertLess(time.time() - t0, 3.0)
        self.assertEqual([], pids_matching(self.marker))

        os.environ[radio.ENV_TRANSPORT] = "/nonexistent/radio"
        self.assertEqual("exit_nonzero", radio.notify("probe")["outcome"])
        os.environ[radio.ENV_TRANSPORT] = "cat >/dev/null"
        self.assertEqual("sent", radio.notify("probe")["outcome"])
        os.environ.pop(radio.ENV_TRANSPORT)
        self.assertEqual("silent", radio.notify("probe")["outcome"])


if __name__ == "__main__":
    unittest.main()
