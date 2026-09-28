"""A flight's budget is paid in time that keeps counting while the Mac sleeps (#235).

Tower kills a script flight at started_at + timeout_s in wall seconds. The runner inside it timed
itself with time.monotonic(), which stops during sleep on macOS, and gave its executor the whole
budget. Both #235 flights were killed by tower mid-run and recorded as owner_exited."""

import subprocess
import sys
import unittest
from unittest.mock import patch

from nexus import executor, flights, work


class SleepClockTests(unittest.TestCase):
    def test_clock_is_the_sleep_counting_monotonic_clock(self):
        a = flights.clock()
        self.assertGreaterEqual(flights.clock(), a)
        self.assertIn(flights._SLEEP_CLOCK, {getattr(flights.time, "CLOCK_BOOTTIME", None),
                                             flights.time.CLOCK_MONOTONIC})

    def test_communicate_times_out_on_the_sleep_clock_not_popen_time(self):
        """Sleep is a clock jump with no elapsed process time: the wait must still end."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        ticks = iter(range(0, 10_000, 100))  # every read, 100 s have passed while "asleep"
        with patch.object(flights, "clock", side_effect=lambda: next(ticks)):
            with self.assertRaises(subprocess.TimeoutExpired):
                flights.communicate(proc, None, 150, slice_s=0.05)
        self.assertIsNone(proc.poll(), "timed out by the clock, not by the process ending")

    def test_communicate_returns_output_across_slices(self):
        proc = subprocess.Popen([sys.executable, "-c", "import sys,time; time.sleep(.3); print(sys.stdin.read())"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, _ = flights.communicate(proc, "hello", 10, slice_s=0.05)
        self.assertEqual(out.strip(), "hello")

    def test_executor_invoke_kills_at_the_sleep_clock_deadline(self):
        ticks = iter(range(0, 10_000, 100))
        with patch.object(flights, "clock", side_effect=lambda: next(ticks)), \
                patch.object(flights, "communicate", _communicate_fast):
            proc = executor.invoke([sys.executable, "-c", "import time; time.sleep(30)"], cwd=".", env=None,
                                   input=None, timeout=150, run=subprocess.run)
        self.assertEqual(proc.returncode, 124)


_real_communicate = flights.communicate


def _communicate_fast(proc, input, timeout):
    return _real_communicate(proc, input, timeout, slice_s=0.05)


class LandReserveTests(unittest.TestCase):
    def test_executor_gets_the_budget_less_the_landing_reserve(self):
        token = work._deadline.set(flights.clock() + 2400)
        try:
            budget = work.executor_budget(2400)
        finally:
            work._deadline.reset(token)
        self.assertLessEqual(budget, 2400 - work.LAND_RESERVE_S)
        self.assertGreater(budget, 2400 - work.LAND_RESERVE_S - 5)

    def test_remaining_counts_time_spent_asleep(self):
        with patch.object(flights, "clock", return_value=1000.0):
            token = work._deadline.set(1500.0)
        try:
            with patch.object(flights, "clock", return_value=1600.0):  # woke after the deadline
                with self.assertRaises(work.WorkError):
                    work.remaining(900)
        finally:
            work._deadline.reset(token)


if __name__ == "__main__":
    unittest.main()
