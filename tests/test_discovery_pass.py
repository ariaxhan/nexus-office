"""Discovery keeps capturing while a github-work execution runs, and a webhook request moves a repo first.

2026-09-25: #192 was labelled ready at 01:26:50Z and captured only at 01:40:07Z, because discovery
lived inside the one github-work flight and that flight was executing #183 for 15 minutes.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nexus import tower, work
from nexus.ledger import Ledger


class DiscoveryPassTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.led = Ledger(str(self.root / "ledger.sqlite"))
        self.addCleanup(self.led.close)
        self.led.add_plan("code-work", kind="work")
        self.entries = []
        for name in ("alpha", "beta"):
            checkout = self.root / name
            checkout.mkdir()
            self.entries.append(dict(repo=f"sample/{name}", path=str(checkout), enabled=True, provider="local",
                                     account="t", executor=[sys.executable, "-c", "pass"],
                                     verify=[sys.executable, "-c", "pass"], risk={"paths": ["*"]}))
        self.issues = {"sample/alpha": [dict(number=1, title="A1", state="open", labels=[{"name": "ready"}])],
                       "sample/beta": [dict(number=5, title="B5", state="open", labels=[{"name": "ready"}])]}
        self.asked = []
        self.outage = False
        real = subprocess.run

        def github(argv, **kwargs):
            if argv[0] != "gh":
                return real(argv, **kwargs)
            repo = argv[-1].split("/")[1] + "/" + argv[-1].split("/")[2]
            self.asked.append(repo)
            if self.outage:
                return subprocess.CompletedProcess(argv, 1, "", "API unavailable")
            return subprocess.CompletedProcess(argv, 0, json.dumps([self.issues[repo]]), "")

        patch("nexus.work.subprocess.run", side_effect=github).start()
        self.addCleanup(patch.stopall)

    def tasks(self, repo=None):
        return [t for t in self.led.tasks() if repo is None or t["dedupe_key"].startswith(f"github:{repo}#")]

    def request(self, repo):
        self.led.event(work.REQUESTED, repo, {"delivery": f"d-{repo}"}, "office-webhook")

    def test_pass_captures_and_never_claims_or_executes(self):
        now = work.time.time()
        self.assertEqual(1, work.discovery_pass(self.led, self.entries, now))
        self.assertEqual(1, work.discovery_pass(self.led, self.entries, now))
        self.assertEqual(["sample/alpha", "sample/beta"], self.asked, "one repo per pass")
        self.assertEqual(2, len(self.tasks()))
        self.assertEqual([], self.led.flights())
        self.assertEqual([], self.led.events(kind="work.executing"))

    def test_pass_is_rate_limited_to_the_poll_interval(self):
        now = work.time.time()
        work.discovery_pass(self.led, self.entries, now, limit=2)
        self.assertEqual(0, work.discovery_pass(self.led, self.entries, now + 10, limit=2))
        self.assertEqual(2, len(self.asked), "no extra polling inside the interval")
        self.assertEqual(2, work.discovery_pass(self.led, self.entries, now + work.DISCOVERY_EVERY_S + 1, limit=2))

    def test_a_webhook_request_is_consumed_on_the_next_pass_without_executing(self):
        now = work.time.time()
        work.discovery_pass(self.led, self.entries, now, limit=2)
        self.issues["sample/beta"].append(dict(number=6, title="B6", state="open", labels=[{"name": "ready"}]))
        self.request("sample/beta")
        self.assertEqual(1, work.discovery_pass(self.led, self.entries, now + 5, limit=2))
        self.assertEqual("sample/beta", self.asked[-1])
        self.assertEqual(2, len(self.tasks("sample/beta")))
        self.assertEqual(0, work.discovery_pass(self.led, self.entries, now + 6, limit=2), "consumed")
        self.assertEqual([], self.led.flights())

    def test_polling_outage_is_recorded_and_retried_at_the_poll_rate(self):
        self.outage = True
        self.request("sample/alpha")
        now = work.time.time()
        work.discovery_pass(self.led, self.entries, now, limit=2)
        self.assertEqual(2, len(self.led.events(kind="work.discovery_failed")))
        self.assertEqual(0, work.discovery_pass(self.led, self.entries, now + 5, limit=2), "never a hot loop")

    def test_disabled_code_work_switch_discovers_nothing(self):
        self.led.conn.execute("UPDATE plans SET enabled=0 WHERE name='code-work'")
        self.assertEqual(0, work.discovery_pass(self.led, self.entries))
        self.assertEqual([], self.asked)

    def test_long_execution_does_not_stop_capture_in_any_repo(self):
        work.discover(self.led, self.entries[0])
        fid = work.claim(self.led, "sample/alpha", 1, os.getpid())  # an execution holds alpha
        self.issues["sample/alpha"].append(dict(number=2, title="A2", state="open", labels=[{"name": "ready"}]))
        self.request("sample/alpha")
        work.discovery_pass(self.led, self.entries, limit=2)
        self.assertEqual({"github:sample/alpha#1", "github:sample/alpha#2", "github:sample/beta#5"},
                         {t["dedupe_key"] for t in self.tasks()})
        self.assertEqual([fid], [f["id"] for f in self.led.flights()], "captured, not executed")
        self.assertEqual("running", self.led.flight(fid)["state"])

    def test_webhook_plus_poll_for_one_issue_is_one_capture(self):
        now = work.time.time()
        self.request("sample/alpha")
        work.discovery_pass(self.led, self.entries, now, limit=2)
        work.discover(self.led, self.entries[0])  # the github-work flight's own poll
        self.request("sample/alpha")  # a redelivered webhook
        work.discovery_pass(self.led, self.entries, now + 1, limit=2)
        self.assertEqual(1, len(self.tasks("sample/alpha")))

    def test_restart_replays_nothing_twice(self):
        self.request("sample/beta")
        work.discovery_pass(self.led, self.entries, limit=2)
        self.led.close()
        self.led = Ledger(str(self.root / "ledger.sqlite"))
        self.assertEqual(0, work.discovery_pass(self.led, self.entries, limit=2))
        self.assertEqual(2, len(self.tasks()))

    def test_run_sorts_a_requested_repo_ahead_of_a_less_recently_serviced_one(self):
        self.led.event("work.serviced", "sample/alpha", {"at": 100}, "work")
        self.led.event("work.serviced", "sample/beta", {"at": 200}, "work")
        self.request("sample/beta")
        with patch("nexus.work.run_task", return_value="held"):
            work.run(self.led, self.entries)
        self.assertEqual(["sample/beta", "sample/alpha"], self.asked)
        self.assertFalse(work.requested_since(self.led, "sample/beta", "work.serviced"), "consumed by the pass")

    def test_tower_tick_runs_the_discovery_pass(self):
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({"repositories": self.entries}))
        with patch.dict(os.environ, {"NEXUS_WORK_REGISTRY": str(registry)}):
            report = tower.tick(self.led, root=str(self.root / "flights"))
        self.assertEqual(1, report["discovered"])
        self.assertEqual(1, len(self.tasks()))


if __name__ == "__main__":
    unittest.main()
