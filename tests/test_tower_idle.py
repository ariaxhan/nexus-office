"""Idle tower lanes are cut off, flagged and requeued; the second cut blocks the issue."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus import work
from nexus.ledger import Ledger


class IdleCutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.led = Ledger(str(Path(tmp.name) / "ledger.sqlite"))
        self.addCleanup(self.led.close)
        self.entries = [dict(repo="sample/product", path=tmp.name, enabled=True)]
        self.gh = []
        for target, value in [("progress", lambda path, fid: "same"), ("stop", lambda led, f: True)]:
            p = patch.object(work, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(work.subprocess, "run", lambda cmd, **k: self.gh.append(cmd))
        p.start()
        self.addCleanup(p.stop)

    def fly(self):
        task = self.led.conn.execute("SELECT id FROM tasks WHERE dedupe_key='github:sample/product#1'").fetchone()
        if task is None:
            with self.led.tx() as c:
                c.execute("INSERT INTO tasks(id,origin,title,state,dedupe_key,created_at) VALUES"
                          " ('task_1','github-work','First','accepted','github:sample/product#1',0)")
            self.led.event("work.issue", "task_1", {"number": 1, "state": "open", "labels": [{"name": "ready"}]}, "work")
        fid = work.claim(self.led, "sample/product", 1, os.getpid(), runner=True)
        self.led.event("work.executing", fid, {"repo": "sample/product", "issue": 1, "lane": work.TOWER_LABEL}, "work")
        return fid

    def tick(self, now):
        return work.cut_idle(self.led, self.entries, now=now)

    def test_idle_lane_cut_flagged_requeued_then_blocked(self):
        fid = self.fly()
        self.assertEqual([], self.tick(1000))  # first sighting records progress
        self.assertEqual([], self.tick(1000 + work.IDLE_S - 1))  # not idle long enough
        self.assertEqual("requeued", self.tick(1000 + work.IDLE_S)[0]["state"])
        self.assertEqual("cancelled", self.led.flight(fid)["state"])
        self.assertEqual(1, sum("comment" in c for c in self.gh))
        self.assertFalse(any("blocked-needs-look" in c for c in self.gh))
        self.fly()
        self.tick(5000)
        self.assertEqual("blocked", self.tick(5000 + work.IDLE_S)[0]["state"])
        self.assertTrue(any("blocked-needs-look" in c for c in self.gh))
        self.assertEqual("held", work.eligibility({"state": "open", "labels": [{"name": "blocked-needs-look"}]}))

    def test_progressing_lane_is_not_cut(self):
        self.fly()
        fps = iter(["a", "b", "c"])
        with patch.object(work, "progress", lambda path, fid: next(fps)):
            for now in (0, work.IDLE_S, 2 * work.IDLE_S):
                self.assertEqual([], self.tick(now))


if __name__ == "__main__":
    unittest.main()
