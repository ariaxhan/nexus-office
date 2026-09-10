"""Product and GitHub subprocess boundaries with a temporary durable ledger."""

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


class WorkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.led = Ledger(str(self.root / "ledger.sqlite"))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.led.close)
        self.script = self.root / "product.py"
        self.script.write_text('''import json, pathlib, sys
mode = sys.argv[1]
data = json.load(sys.stdin)
root = pathlib.Path('.')
number = str(data['issue']['number'])
receipt = root / ('delivered-' + number)
if mode == 'verify':
    if (root / 'outage').exists():
        sys.exit(7)
    if (root / 'unknown').exists():
        print('{}')
    elif (root / 'pending').exists():
        print((root / 'pending').read_text())
    elif any(any(l['name'] == 'direct' for l in pr.get('labels', [])) for pr in data['pull_requests']):
        print(json.dumps(dict(state='pending', reason='PR claimed', evidence=['claim'])))
    elif receipt.exists() and (root / 'source-stage').exists():
        result = json.loads((root / 'source-stage').read_text())
        result.setdefault('idempotency_key', data['idempotency_key'])
        print(json.dumps(result))
    elif receipt.exists():
        print(json.dumps(dict(state='delivered', verified=True,
            evidence=[str(receipt)], idempotency_key=data['idempotency_key'])))
    else:
        print(json.dumps(dict(state='absent', retry_safe=True)))
else:
    with (root / 'calls').open('a') as f:
        f.write(json.dumps(data) + '\\n')
    if (root / ('fail-' + number)).exists():
        sys.exit(9)
    receipt.write_text('delivered')
''')
        self.entry = dict(repo="sample/product", path=str(self.root), enabled=True,
                          provider="local", account="test", executor=[sys.executable, str(self.script), "execute"],
                          verify=[sys.executable, str(self.script), "verify"])
        self.issues = [dict(number=1, title="First", state="open", labels=[{"name": "ready"}])]
        self.prs = []
        self.closed = []
        self.outage = False
        self.real_run = subprocess.run
        self.addCleanup(patch.stopall)
        patch("nexus.work.subprocess.run", side_effect=self.github).start()

    def github(self, argv, **kwargs):
        if argv[0] != "gh":
            return self.real_run(argv, **kwargs)
        if self.outage:
            return subprocess.CompletedProcess(argv, 1, "", "API unavailable")
        if "PATCH" in argv:
            self.closed.append(argv)
            return subprocess.CompletedProcess(argv, 0, '{"state":"closed"}', "")
        if "timeline?" in argv[-1]:
            rows = [dict(source=dict(issue=dict(p, html_url=f"https://github.com/sample/product/pull/{p["number"]}", pull_request={"url": "pr"}))) for p in self.prs]
        elif "/issues/" in argv[-1]:
            number = int(argv[-1].rsplit("/", 1)[1])
            issue = next((i for i in self.issues if i["number"] == number),
                         dict(number=number, title="Closed", state="closed", labels=[]))
            return subprocess.CompletedProcess(argv, 0, json.dumps(issue), "")
        else:
            rows = self.issues
        return subprocess.CompletedProcess(argv, 0, json.dumps([rows]), "")

    def run_work(self):
        return work.run(self.led, [self.entry])

    def calls(self):
        path = self.root / "calls"
        return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []

    def test_default_route_delivers_with_proof(self):
        self.assertEqual("done", self.run_work()[0]["state"])
        self.assertEqual(1, len(self.calls()))
        self.assertEqual(1, len(self.closed))
        self.assertEqual([], self.led.integrity_check())
        self.assertTrue(self.led.events(kind="work.proof"))

    def test_failure_is_local_and_evidence_survives(self):
        self.issues.append(dict(number=2, title="Second", state="open", labels=[{"name": "ready"}]))
        (self.root / "fail-1").touch()
        self.assertEqual(["failed", "done"], [r["state"] for r in self.run_work()])
        self.assertEqual([1, 2], [c["issue"]["number"] for c in self.calls()])
        self.assertEqual("backoff", self.run_work()[0]["state"])
        self.assertTrue(work.status(self.led, [self.entry])["gaps"])
        self.assertTrue(all(Path(a["ref"]).exists() for a in self.led.artifacts()))

    def test_open_pr_is_passed_to_executor_for_resume(self):
        self.prs = [dict(number=20, state="open", body="Closes #1", head={"ref": "existing"})]
        self.run_work()
        self.assertEqual("resume", self.calls()[0]["mode"])
        self.assertEqual("existing", self.calls()[0]["pull_requests"][0]["head"]["ref"])

    def test_nested_registry_deduplicates_canonical_repository(self):
        path = self.root / "registry.json"
        path.write_text(json.dumps({"repositories": [self.entry, dict(self.entry, path=str(self.root / "nested"))]}))
        self.assertEqual(1, len(work.registry(path)))
        self.assertEqual(str(self.root.resolve()), work.registry(path)[0]["path"])

    def test_null_workspace_remains_unavailable_and_never_executes_in_cwd(self):
        entry = dict(self.entry, path=None)
        path = self.root / "registry.json"
        path.write_text(json.dumps({"repositories": [entry]}))
        loaded = work.registry(path)
        self.assertIsNone(loaded[0]["path"])
        self.assertFalse(work.status(self.led, loaded)["repositories"][0]["available"])
        self.assertFalse(work.read_status(self.root / "absent.sqlite", loaded)["repositories"][0]["available"])
        with patch("nexus.work.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(work.WorkError, "checkout unavailable"):
                work.adapter([sys.executable], entry, {}, self.root / "adapter.log")
            spawn.assert_not_called()

    def test_inventory_offloaded_directory_is_not_a_checkout(self):
        entry = dict(self.entry, availability={"local_git_at_inventory": True})
        self.assertFalse(work.workspace_available(entry))
        (self.root / ".git").mkdir()
        self.assertTrue(work.workspace_available(entry))

    def test_intake_survives_source_window(self):
        work.discover(self.led, self.entry)
        self.led.conn.execute("UPDATE tasks SET created_at=1")
        self.issues = []
        self.assertEqual("closed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual(1, len(self.led.tasks()))

    def test_cancel_direct_claim_does_not_signal_desktop_owner(self):
        from nexus import cli
        work.discover(self.led, self.entry)
        fid=work.claim(self.led,self.entry["repo"],1,os.getpid())
        with patch("nexus.work.os.kill") as kill:
            self.assertEqual(0, cli._cancel(self.led,self.led.flight(fid),fid))
            kill.assert_not_called()
        self.assertEqual("cancelled",self.led.flight(fid)["state"])

    def test_cancel_work_kills_recorded_session_and_releases_lease(self):
        from nexus import cli, flights
        import time
        childfile=self.root/"session-child"
        program="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)"
        proc=subprocess.Popen([sys.executable,"-c",program,str(childfile)],start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline=time.monotonic()+3
        while not childfile.exists() and time.monotonic()<deadline: time.sleep(.01)
        child=int(childfile.read_text())
        self.addCleanup(lambda: flights.alive(child) and os.kill(child,9))
        work.discover(self.led,self.entry)
        fid=work.claim(self.led,self.entry["repo"],1,os.getpid(),runner=True)
        self.led.event("work.executing",fid,{"issue":1},"work")
        self.led.event("work.process",fid,{"pid":proc.pid},"work")
        self.assertEqual(0,cli._cancel(self.led,self.led.flight(fid),fid))
        proc.wait(timeout=2)
        self.assertFalse(flights.alive(child))
        self.assertIsNone(self.led.conn.execute("SELECT * FROM leases WHERE holder_flight=?",(fid,)).fetchone())

    def test_cancel_separate_runner_confirms_it_exited_before_release(self):
        from nexus import cli, flights
        import time
        childfile=self.root/"runner-session"
        program="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],start_new_session=True); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)"
        runner=subprocess.Popen([sys.executable,"-c",program,str(childfile)],start_new_session=True)
        self.addCleanup(lambda: runner.poll() is None and runner.kill())
        deadline=time.monotonic()+3
        while not childfile.exists() and time.monotonic()<deadline: time.sleep(.01)
        child=int(childfile.read_text())
        self.addCleanup(lambda: flights.alive(child) and os.kill(child,9))
        work.discover(self.led,self.entry)
        fid=work.claim(self.led,self.entry["repo"],1,runner.pid,runner=True)
        self.led.event("work.executing",fid,{"issue":1},"work")
        self.led.event("work.process",fid,{"pid":child},"work")
        self.assertEqual(0,cli._cancel(self.led,self.led.flight(fid),fid))
        self.assertEqual([],flights._live_pids([runner.pid,child]))
        runner.wait(timeout=2)
        self.assertIsNone(self.led.conn.execute("SELECT * FROM leases WHERE holder_flight=?",(fid,)).fetchone())
        self.assertTrue(work.latest(self.led,"work.teardown",fid)["ok"])

    def test_repeat_unconfirmed_cancellation_keeps_lease_without_transition_error(self):
        from nexus import cli
        work.discover(self.led,self.entry)
        fid=work.claim(self.led,self.entry["repo"],1,os.getpid(),runner=True)
        self.led.event("work.executing",fid,{"issue":1},"work")
        for _ in range(2):
            self.assertEqual(1,cli._cancel(self.led,self.led.flight(fid),fid))
        self.assertEqual("resolving",self.led.flight(fid)["state"])
        self.assertIsNotNone(self.led.conn.execute("SELECT * FROM leases WHERE holder_flight=?",(fid,)).fetchone())

    def test_explicit_recovery_claim_preserves_abandoned_generation(self):
        work.discover(self.led, self.entry)
        old = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        old_task = self.led.flight(old)["task_id"]
        self.led.fail(old, "vanished", "legacy Tower reconciled direct claim")
        self.led.conn.execute("UPDATE tasks SET state='abandoned' WHERE id=?", (old_task,))
        self.led.event("task.state", old_task, {"to": "abandoned"}, "tower")
        with self.assertRaises(work.WorkError):
            work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        fresh = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        task = self.led.flight(fresh)["task_id"]
        self.assertNotEqual(old_task, task)
        self.assertEqual("abandoned", self.led.conn.execute("SELECT state FROM tasks WHERE id=?", (old_task,)).fetchone()[0])
        self.assertEqual("failed", self.led.flight(old)["state"])
        self.assertEqual(2, self.led.flight(fresh)["attempt"])
        self.assertEqual(old_task, work.latest(self.led, "work.generation", task)["previous_task"])
        self.assertEqual(task, work.capture(self.led, self.entry["repo"], self.issues[0]))
        queue, _ = work.selection_queue(self.led, self.entry)
        self.assertEqual([task], [t["id"] for t in queue])
        with self.assertRaises(work.Owned):
            work.claim(self.led, self.entry["repo"], 1, os.getpid())
        work.release(self.led, fresh, os.getpid())
        self.assertEqual([], self.led.integrity_check())

    def test_direct_claim_survives_expiry_and_tower_tick(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        tower.tick(self.led, now=10**12, root=str(self.root / "flights"))
        self.assertEqual("owned", self.run_work()[0]["state"])
        with self.assertRaises(work.WorkError):
            work.claim(self.led, self.entry["repo"], 1, os.getpid())
        self.assertEqual("running", self.led.flight(fid)["state"])
        self.assertEqual([], self.calls())
        work.release(self.led, fid, os.getpid())

    def test_normal_recovery_never_supersedes_live_runner_before_process_receipt(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.assertEqual("owned", self.run_work()[0]["state"])
        self.assertEqual("running", self.led.flight(fid)["state"])
        self.assertEqual([], self.calls())

    def test_delivery_crash_and_api_outage_never_replays(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        self.led.event("work.executing", fid, {"issue": 1}, "work")
        self.led.conn.execute("UPDATE flights SET pid=2147483647 WHERE id=?", (fid,))
        (self.root / "delivered-1").write_text("delivered")
        self.outage = True
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.closed)
        self.assertEqual([], self.calls())
        self.outage = False
        self.assertEqual("done", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual(1, len(self.closed))

    def test_unknown_and_api_error_are_failures(self):
        (self.root / "unknown").touch()
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual([], self.closed)
        self.outage = True
        self.assertEqual("failed", self.run_work()[0]["state"])

    def test_rc_zero_without_outcome_cannot_close(self):
        self.entry["executor"] = [sys.executable, "-c", "print('{}')"]
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.closed)

    def test_conflicting_routes_fail_without_execution(self):
        self.entry["routes"] = {"one": self.entry["executor"], "two": self.entry["executor"]}
        self.issues[0]["labels"] = [{"name": "ready"}, {"name": "one"}, {"name": "two"}]
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())

    def test_closure_outage_retries_closure_only(self):
        with patch("nexus.work.close_issue", side_effect=work.WorkError("API unavailable")):
            self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual("done", self.led.tasks()[0]["state"])
        self.assertEqual("done", self.run_work()[0]["state"])
        self.assertEqual(1, len(self.calls()))
        self.assertEqual(1, len(self.closed))

    def test_status_does_not_create_a_ledger_or_hydrate(self):
        missing = self.root / "offloaded" / "ledger.sqlite"
        report = work.read_status(str(missing), [dict(self.entry, path=str(self.root / "offloaded"))])
        self.assertFalse(missing.parent.exists())
        self.assertFalse(report["repositories"][0]["available"])
        self.assertEqual([], report["tasks"])

    def test_verify_outage_after_execution_never_replays(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        self.led.event("work.executing", fid, {"issue": 1}, "work")
        self.led.conn.execute("UPDATE flights SET pid=2147483647 WHERE id=?", (fid,))
        (self.root / "delivered-1").touch()
        (self.root / "outage").touch()
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual([], self.closed)
        (self.root / "outage").unlink()
        self.led.event("work.failure", self.led.tasks()[0]["id"], {"next_retry": 0}, "fixture")
        self.assertEqual("done", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())

    def test_office_projects_failed_attempts(self):
        from sources import flows
        (self.root / "fail-1").touch()
        self.run_work()
        report = work.status(self.led, [self.entry])
        card = flows.card({"state": "unconfigured", "work": report})
        self.assertGreater(card["needs"], 0)
        self.assertTrue(any(row["label"] == "Work failures" and row["value"] == "1"
                            for row in card["facts"]))

    def test_office_names_registry_coverage_separately_from_desks(self):
        from sources import flows
        report = work.status(self.led, [self.entry, dict(self.entry, repo="other/repo", path=None)])
        card = flows.card({"state": "unconfigured", "work": report})
        self.assertIn({"label": "Work coverage", "value": "2 registered · 1 local · 1 unavailable", "tone": "dim"}, card["facts"])
        self.assertTrue(any(f["label"] == "Work coverage gaps" and f["value"] == "1" for f in card["facts"]))

    def test_capture_all_execute_only_ready(self):
        for number, label in enumerate((None, "hold", "direct", "cancelled"), 2):
            self.issues.append(dict(number=number, title=str(number), state="open",
                                    labels=[] if label is None else [{"name": "ready"}, {"name": label}]))
        self.run_work()
        self.assertEqual(5, len(self.led.tasks()))
        self.assertEqual([1], [c["issue"]["number"] for c in self.calls()])

    def test_unknown_selector_fails(self):
        with self.assertRaisesRegex(work.WorkError, "unknown repository"):
            work.run(self.led, [self.entry], "missing/repo")

    def test_historical_conveyor_retry_deadline_is_not_a_scheduler(self):
        self.led.event("work.item_attempt", "sample/product#1",
                       dict(step="build", status="failed", attempt=1, retry_at=10**12, evidence=[]), "conveyor")
        self.assertEqual("done", self.run_work()[0]["state"])
        self.assertEqual(1, len(self.calls()))

    def test_independent_resource_claim_is_owned(self):
        work.discover(self.led, self.entry)
        pid = work.plan(self.led)
        fid = self.led.create_flight(pid)
        self.led.acquire_leases(fid, ["github:sample/product#1"], 10**12)
        self.assertEqual("owned", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())

    def test_dead_direct_claim_is_not_release_authority(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        self.led.conn.execute("UPDATE flights SET pid=2147483647 WHERE id=?", (fid,))
        self.assertEqual("owned", self.run_work()[0]["state"])
        self.assertEqual("running", self.led.flight(fid)["state"])

    def test_conveyor_steps_remain_evidence_without_vetoing_work(self):
        for step, retry in (("build", 10**12), ("review", 0)):
            self.led.event("work.item_attempt", "Sample/Product#1",
                           dict(step=step, status="failed", retry_at=retry), "conveyor")
        self.assertEqual("done", self.run_work()[0]["state"])
        attempts = work.status(self.led, [self.entry])["item_attempts"]
        self.assertEqual(2, len([row for row in attempts if row["source"] == "conveyor"]))

    def test_existing_pr_direct_claim_prevents_execution(self):
        self.prs = [dict(number=20, state="open", labels=[{"name": "direct"}])]
        self.assertEqual("pending", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())

    def test_independent_repository_survives_failure(self):
        other = dict(self.entry, repo="sample/other")
        with patch("nexus.work.discover", side_effect=[work.WorkError("outage"), None]):
            work.capture(self.led, other["repo"], self.issues[0])
            result = work.run(self.led, [self.entry, other])
        self.assertEqual(["failed", "done"], [row["state"] for row in result])

    def test_disabled_missing_repository_visible_without_hydration(self):
        entry = dict(self.entry, enabled=False, path=str(self.root / "missing"))
        self.assertEqual([], work.run(self.led, [entry]))
        row = work.status(self.led, [entry])["repositories"][0]
        self.assertFalse(row["enabled"])
        self.assertFalse(row["available"])
        self.assertFalse(Path(entry["path"]).exists())

    def test_disabled_plan_runs_already_admitted_task_without_discovery(self):
        work.discover(self.led, self.entry)
        self.led.set_plan_enabled(work.plan(self.led), False)
        with patch("nexus.work.discover") as discover:
            self.assertEqual("done", work.run(self.led, [self.entry])[0]["state"])
        discover.assert_not_called()

    def test_hold_added_during_verification_prevents_execution(self):
        original = work.proof
        def hold(*args):
            result = original(*args)
            self.issues[0]["labels"].append({"name": "hold"})
            return result
        with patch("nexus.work.proof", side_effect=hold):
            self.assertEqual("held", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())

    def test_client_workspace_cannot_be_released_by_pid(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        self.led.conn.execute("UPDATE flights SET workspace=? WHERE id=?", (str(self.root), fid))
        with self.assertRaisesRegex(work.WorkError, "product reconciliation"):
            work.release(self.led, fid, os.getpid())
        self.assertEqual("running", self.led.flight(fid)["state"])

    def test_adapter_timeout_retains_failure_log(self):
        self.entry["executor"] = [sys.executable, "-c", "import time; time.sleep(10)"]
        self.entry["timeout_s"] = 1
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.closed)
        self.assertTrue(all(Path(a["ref"]).exists() for a in self.led.artifacts()))

    def test_conveyor_success_is_not_delivery_proof(self):
        self.led.event("work.item_attempt", "sample/product#1",
                       dict(step="build", status="succeeded", attempt=0, retry_at=0, evidence="log"), "conveyor")
        (self.root / "pending").write_text(json.dumps({"state": "absent", "retry_safe": False}))
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.closed)

    def test_tower_preserves_failed_product_workspace(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid())
        workspace = self.root / "retained"
        workspace.mkdir()
        (workspace / "source").write_text("retained")
        self.led.conn.execute("UPDATE flights SET workspace=? WHERE id=?", (str(workspace), fid))
        self.led.fail(fid, "fixture", "failed")
        tower.tick(self.led, root=str(self.root / "flights"))
        self.assertEqual("retained", (workspace / "source").read_text())

    def test_closed_issue_after_delivery_crash_reconciles_without_execution(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.led.event("work.executing", fid, {"issue": 1}, "work")
        self.led.conn.execute("UPDATE flights SET pid=2147483647 WHERE id=?", (fid,))
        self.issues[0]["state"] = "closed"
        (self.root / "delivered-1").touch()
        self.assertEqual("done", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual([], self.closed)
        self.assertEqual([], self.led.leases())

    def test_closed_uncertain_issue_without_proof_never_executes(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.led.event("work.executing", fid, {"issue": 1}, "work")
        self.led.conn.execute("UPDATE flights SET pid=2147483647 WHERE id=?", (fid,))
        self.issues[0]["state"] = "closed"
        self.assertEqual("closed", self.run_work()[0]["state"])
        self.assertNotEqual("done", self.led.tasks()[0]["state"])
        self.assertEqual([], self.calls())

    def test_uncertain_prior_execution_requires_retry_safe_clearance(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.led.event("work.executing", fid, {"issue": 1}, "work")
        self.led.conn.execute("UPDATE flights SET pid=2147483647 WHERE id=?", (fid,))
        (self.root / "pending").write_text(json.dumps({"state": "absent", "retry_safe": False}))
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual(task["id"], self.led.flight(fid)["task_id"])

    def test_older_execution_receipt_requires_clearance_before_new_predecessor(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        old = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.led.event("work.executing", old, {"issue": 1}, "work")
        self.led.conn.execute("UPDATE flights SET pid=NULL WHERE id=?", (old,))
        successor = work.claim(self.led, self.entry["repo"], 1, os.getpid(),
                               runner=True, predecessor=old)
        self.led.conn.execute("UPDATE flights SET pid=NULL WHERE id=?", (successor,))
        (self.root / "pending").write_text(json.dumps({"state": "absent", "retry_safe": False}))
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual("running", self.led.flight(successor)["state"])

    def test_delivered_proof_resumes_every_legal_finalization_state(self):
        for initial in ("queued", "running", "produced", "verifying", "verified", "landing"):
            with self.subTest(initial=initial):
                work.discover(self.led, self.entry)
                task = self.led.tasks()[-1]
                fid = self.led.create_flight(work.plan(self.led), task_id=task["id"], source="fixture")
                if initial != "queued":
                    self.led.set_state(fid, "running", source="fixture")
                if initial in ("produced", "verifying", "verified", "landing"):
                    self.led.set_state(fid, "produced", source="fixture")
                if initial == "verifying":
                    self.led.set_state(fid, "verifying", source="fixture")
                if initial in ("verified", "landing"):
                    self.led.set_state(fid, "verified", source="fixture")
                if initial == "landing":
                    landing = self.led.create_landing(fid, task["dedupe_key"], state="verified")
                    self.led.start_applying(landing, "expected")
                self.led.acquire_leases(fid, [task["dedupe_key"]], float("inf"))
                payload = {"task": task["id"], "repo": self.entry["repo"],
                           "idempotency_key": task["dedupe_key"]}
                delivered = {"state": "delivered", "verified": True,
                             "idempotency_key": task["dedupe_key"], "evidence": [initial]}
                with patch("nexus.work.close_issue"):
                    work.finish(self.led, fid, payload, delivered)
                    work.finish(self.led, fid, payload, delivered)
                self.assertEqual("landed", self.led.flight(fid)["state"])
                self.assertEqual([], [row for row in self.led.leases()
                                      if row["holder_flight"] == fid])
                self.assertEqual(1, len(self.led.events(kind="work.proof", subject=fid)))

    def test_in_pr_pending_rechecks_without_executor_or_failure(self):
        self.issues[0]['labels'] = [{'name': 'in-pr'}]
        self.prs = [dict(number=20, state='open')]
        marker = self.root / 'pending'
        marker.write_text(json.dumps(dict(state='pending', reason='required client review', evidence=['PR20'], retry_at=10**12)))
        self.assertEqual('pending', self.run_work()[0]['state'])
        task = self.led.tasks()[0]
        pending = work.latest(self.led, 'work.pending', task['id'])
        self.assertLessEqual(pending['next_retry'], work.time.time() + 86400)
        self.assertEqual('backoff', self.run_work()[0]['state'])
        self.assertEqual([], self.led.events(kind='work.failure'))
        self.assertEqual([], self.led.events(kind='work.executing'))
        marker.unlink()
        (self.root / 'delivered-1').touch()
        self.led.event('work.pending', task['id'], {'next_retry': 0}, 'fixture')
        self.assertEqual('done', self.run_work()[0]['state'])
        self.assertEqual([], self.calls())

    def test_historical_hold_reconciles_after_release_but_active_owner_survives(self):
        self.led.event('work.item_attempt', 'sample/product#1', dict(step='build', status='held'), 'conveyor')
        self.issues[0]['labels'].append({'name': 'hold'})
        self.assertEqual('held', self.run_work()[0]['state'])
        self.issues[0]['labels'] = [{'name': 'ready'}]
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid())
        self.assertEqual('owned', self.run_work()[0]['state'])
        self.assertEqual([], self.calls())
        work.release(self.led, fid, os.getpid())
        self.assertEqual('done', self.run_work()[0]['state'])
        self.assertEqual(1, len(self.calls()))

    def test_verified_source_continuation_is_pending_not_failed(self):
        (self.root / 'source-stage').write_text(json.dumps(dict(
            state='absent', retry_safe=True, resume_kind='review',
            evidence=[{'url':'https://github.com/sample/product/pull/2', 'head':'exact-head'}])))
        self.assertEqual('pending', self.run_work()[0]['state'])
        self.assertEqual(1, len(self.calls()))
        self.assertEqual([], self.led.events(kind='work.failure'))
        self.assertEqual([], self.closed)
        self.assertNotEqual('done', self.led.tasks()[0]['state'])
        self.assertTrue(self.led.events(kind='work.pending'))

    def test_source_continuation_with_foreign_identity_cannot_hide_failure(self):
        (self.root / 'source-stage').write_text(json.dumps(dict(
            state='absent', retry_safe=True, resume_kind='review',
            idempotency_key='github:other/repo#1',
            evidence=[{'url':'https://github.com/sample/product/pull/2', 'head':'exact-head'}])))
        self.assertEqual('failed', self.run_work()[0]['state'])
        self.assertEqual([], self.led.events(kind='work.pending'))
        self.assertEqual([], self.closed)

    def test_source_continuation_without_evidence_cannot_hide_failure(self):
        (self.root / 'source-stage').write_text(json.dumps(dict(
            state='absent', retry_safe=True, resume_kind='review', evidence=[])))
        self.assertEqual('failed', self.run_work()[0]['state'])
        self.assertEqual([], self.led.events(kind='work.pending'))
        self.assertEqual([], self.closed)

    def test_malformed_source_evidence_does_not_suppress_failures(self):
        for number, evidence in enumerate((True, ' ', [None], [{}],
                                          [{'url':' ', 'head':'sha'}],
                                          [{'url':'https://example.test/pr', 'head':False}]), 1):
            with self.subTest(evidence=evidence):
                self.issues = [dict(number=number, title='Source stage', state='open',
                                    labels=[{'name':'ready'}])]
                (self.root / 'source-stage').write_text(json.dumps(dict(
                    state='absent', retry_safe=True, resume_kind='review', evidence=evidence)))
                self.assertEqual('failed', self.run_work()[-1]['state'])
                self.assertEqual([], self.led.events(kind='work.pending'))
                self.assertEqual([], self.closed)

    def test_fair_bounded_selection_across_repositories(self):
        self.issues += [dict(number=n, title=str(n), state='open', labels=[{'name': 'ready'}]) for n in range(2, 5)]
        (self.root / 'fail-1').touch()
        other = dict(self.entry, repo='sample/other')
        report = work.run(self.led, [self.entry, other], max_items=2)
        self.assertEqual(['sample/product', 'sample/other'], [r['repo'] for r in report])
        self.assertEqual(2, len(self.calls()))

    def test_single_item_keeps_execution_budget_and_rotates_repositories(self):
        self.script.write_text('import time; time.sleep(0.6)\n' + self.script.read_text())
        entries = [self.entry] + [dict(self.entry, repo=f'sample/other-{i}') for i in range(7)]
        report = work.run(self.led, entries, budget_s=3, max_items=1)
        self.assertEqual(['done'], [row['state'] for row in report])
        self.assertEqual('sample/product', report[0]['repo'])
        self.assertEqual(1, len(self.calls()))
        following = work.run(self.led, entries, budget_s=3, max_items=1)
        self.assertEqual('sample/other-0', following[0]['repo'])
        self.assertEqual('done', following[0]['state'])

    def test_budget_prevents_launch_and_reserves_other_repository_time(self):
        other = dict(self.entry, repo='sample/other')
        self.entry['executor'] = [sys.executable, '-c', 'import time; time.sleep(10)']
        start = work.time.monotonic()
        report = work.run(self.led, [self.entry, other], budget_s=2, max_items=2)
        self.assertEqual(['failed', 'done'], [r['state'] for r in report])
        self.assertLess(work.time.monotonic() - start, 3)
        token = work._deadline.set(work.time.monotonic() - 1)
        try:
            with patch('nexus.work.subprocess.Popen') as spawn:
                with self.assertRaises(work.WorkError):
                    work.adapter(other['executor'], other, {}, self.root / 'log')
                spawn.assert_not_called()
        finally:
            work._deadline.reset(token)

    def test_current_dispositions_retain_held_and_cancelled(self):
        self.issues[0]['labels'] = [{'name': 'cancelled'}]
        self.assertEqual('held', self.run_work()[0]['state'])
        report = work.status(self.led, [self.entry])
        self.assertEqual('held', report['tasks'][0]['disposition']['state'])
        self.assertNotEqual('done', report['tasks'][0]['state'])

    def test_current_done_hold_and_owner_override_historical_retry_receipts(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.failure', task['id'], {'error': 'old', 'next_retry': 10**12}, 'fixture')
        self.issues[0]['labels'] = [{'name': 'hold'}]
        work.capture(self.led, self.entry['repo'], self.issues[0])
        self.assertEqual('held', work.status(self.led, [self.entry])['tasks'][0]['disposition']['state'])
        self.issues[0]['labels'] = [{'name': 'ready'}]
        work.capture(self.led, self.entry['repo'], self.issues[0])
        work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.assertEqual('owned', work.status(self.led, [self.entry])['tasks'][0]['disposition']['state'])
        self.led.set_task_state(task['id'], 'done', decided_by='fixture')
        self.assertEqual('done', work.status(self.led, [self.entry])['tasks'][0]['disposition']['state'])

    def test_executor_pending_is_not_failure_and_later_proof_finishes(self):
        self.entry['executor'] = [sys.executable, '-c', "import pathlib,json; pathlib.Path('pending').write_text(json.dumps(dict(state='pending',reason='capture required',evidence=['source PR'])))"]
        self.assertEqual('pending', self.run_work()[0]['state'])
        self.assertEqual(1, len(self.led.events(kind='work.executing')))
        self.assertEqual([], self.led.events(kind='work.failure'))
        self.assertNotEqual('done', self.led.tasks()[0]['state'])
        (self.root / 'pending').unlink()
        (self.root / 'delivered-1').touch()
        self.led.event('work.pending', self.led.tasks()[0]['id'], {'next_retry': 0}, 'fixture')
        self.assertEqual('done', self.run_work()[0]['state'])
        self.assertEqual(1, len(self.led.events(kind='work.executing')))

    def test_public_run_repairs_then_accepts_exact_new_head_with_disabled_discovery(self):
        from nexus import cli
        product, unrelated = self.root / 'product', self.root / 'unrelated'
        for repo, content in ((product, 'finding\n'), (unrelated, 'untouched\n')):
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.email', 'fixture@example.test'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Fixture'], cwd=repo, check=True)
            (repo / 'result.txt').write_text(content)
            subprocess.run(['git', 'add', 'result.txt'], cwd=repo, check=True)
            subprocess.run(['git', 'commit', '-q', '-m', 'initial'], cwd=repo, check=True)
        old_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=product,
                                  text=True, capture_output=True, check=True).stdout.strip()
        unrelated_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=unrelated,
                                        text=True, capture_output=True, check=True).stdout.strip()
        executor = self.root / 'repair.py'
        executor.write_text('''import json, pathlib, subprocess, sys
json.load(sys.stdin)
pathlib.Path('result.txt').write_text('all findings repaired\\n')
subprocess.run(['git','add','result.txt'],check=True)
subprocess.run(['git','commit','-q','-m','repair findings'],check=True)
''')
        verifier = self.root / 'verify.py'
        verifier.write_text('''import json, pathlib, subprocess, sys
head=subprocess.run(['git','rev-parse','HEAD'],text=True,capture_output=True,check=True).stdout.strip()
approval=pathlib.Path('.approved-head')
if sys.argv[1]=='approve': approval.write_text(head); raise SystemExit
data=json.load(sys.stdin); key=data['idempotency_key']; url='https://github.com/sample/product/pull/20'
evidence=[{'url':url,'head':head}]
if approval.exists() and approval.read_text()==head:
 print(json.dumps({'state':'delivered','verified':True,'idempotency_key':key,'evidence':evidence+[{'approval':'independent','url':url,'head':head}]}))
elif pathlib.Path('result.txt').read_text()=='all findings repaired\\n':
 print(json.dumps({'state':'pending','retry_safe':True,'resume_kind':'review','idempotency_key':key,'evidence':evidence}))
else: print(json.dumps({'state':'absent','retry_safe':True}))
''')
        registry = self.root / 'registry.json'
        entry = dict(self.entry, path=str(product),
                     executor=[sys.executable, str(executor)],
                     verify=[sys.executable, str(verifier), 'verify'])
        registry.write_text(json.dumps({'repositories': [entry]}))
        argv = ['--ledger', str(self.root / 'ledger.sqlite'), 'work', 'run',
                '--registry', str(registry), '--repo', self.entry['repo'], '--max-items', '1']
        self.assertEqual(0, cli.main(argv))
        task = self.led.tasks()[0]
        flight = self.led.flights(task_id=task['id'])[0]
        new_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=product,
                                  text=True, capture_output=True, check=True).stdout.strip()
        self.assertNotEqual(old_head, new_head)
        self.assertEqual('all findings repaired\n', (product / 'result.txt').read_text())
        self.assertEqual(unrelated_head, subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=unrelated, text=True,
            capture_output=True, check=True).stdout.strip())
        self.assertEqual('', subprocess.run(['git', 'status', '--porcelain'], cwd=unrelated,
                                            text=True, capture_output=True, check=True).stdout)
        self.led.set_plan_enabled(work.plan(self.led), False)
        self.assertEqual(0, subprocess.run([sys.executable, str(verifier), 'approve'],
                                          cwd=product).returncode)
        self.assertEqual(new_head, (product / '.approved-head').read_text())
        args = type('Args', (), {'ledger': str(self.root / 'ledger.sqlite'), 'flight': flight['id']})()
        self.assertEqual(0, cli.cmd_retry(args))
        self.assertEqual(0, cli.main(argv))
        self.assertEqual(1, len(self.led.flights(task_id=task['id'])))
        self.assertEqual('landed', self.led.flight(flight['id'])['state'])
        self.assertEqual(new_head, work.latest(self.led, 'work.proof', flight['id'])['evidence'][0]['head'])
        self.assertEqual('done', self.led.task(task['id'])['state'])

    def test_legacy_queued_click_flight_reconciles_through_normal_run(self):
        from nexus import cli
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        queued = self.led.create_flight(work.plan(self.led), task_id=task['id'], source='click')
        tower.tick(self.led, root=str(self.root / 'flights'))
        self.assertEqual('queued', self.led.flight(queued)['state'])
        args = type('Args', (), {'ledger': str(self.root / 'ledger.sqlite'), 'flight': queued})()
        self.assertEqual(0, cli.cmd_retry(args))
        self.assertEqual('done', self.run_work()[0]['state'])
        self.assertEqual('cancelled', self.led.flight(queued)['state'])

    def test_retry_setup_failure_preserves_predecessor_ownership(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        predecessor = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.conn.execute('UPDATE flights SET pid=NULL WHERE id=?', (predecessor,))
        repair = dict(state='pending', retry_safe=True, resume_kind='repair',
                      evidence=[{'url': 'https://example.test/pr/1', 'head': 'old'}],
                      idempotency_key=task['dedupe_key'])
        missing = dict(self.entry, path=str(self.root / 'missing'))
        with patch('nexus.work.proof', return_value=repair):
            self.assertEqual('failed', work.run(self.led, [missing])[0]['state'])
        self.assertEqual([predecessor], [row['id'] for row in self.led.flights(task_id=task['id'])])
        self.assertEqual('running', self.led.flight(predecessor)['state'])
        lease = self.led.conn.execute('SELECT holder_flight FROM leases').fetchone()
        self.assertEqual(predecessor, lease['holder_flight'])

    def test_interrupted_handoff_rolls_back_successor_and_keeps_predecessor_lease(self):
        work.discover(self.led, self.entry)
        predecessor = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.conn.execute('UPDATE flights SET pid=NULL WHERE id=?', (predecessor,))
        original = self.led._event
        def interrupt(kind, subject, payload, source, ts=None):
            if kind == 'flight.state' and payload.get('successor'):
                raise RuntimeError('handoff interrupted')
            return original(kind, subject, payload, source, ts)
        with patch.object(self.led, '_event', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'handoff interrupted'):
                work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True,
                           predecessor=predecessor)
        self.assertEqual([predecessor], [row['id'] for row in self.led.flights()])
        self.assertEqual('running', self.led.flight(predecessor)['state'])
        self.assertEqual(predecessor, self.led.leases()[0]['holder_flight'])

    def test_interrupted_finalization_reuses_landing_and_releases_lease(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        payload = {'task': task['id'], 'repo': self.entry['repo'],
                   'idempotency_key': task['dedupe_key']}
        delivered = {'state': 'delivered', 'verified': True,
                     'idempotency_key': task['dedupe_key'], 'evidence': ['exact-head']}
        with patch.object(self.led, 'apply_landing', side_effect=RuntimeError('crash')):
            with self.assertRaisesRegex(RuntimeError, 'crash'):
                work.finish(self.led, fid, payload, delivered)
        self.assertEqual('verified', self.led.flight(fid)['state'])
        self.assertEqual(1, len(self.led.landings()))
        with patch('nexus.work.close_issue'):
            work.finish(self.led, fid, payload, delivered)
        self.assertEqual('landed', self.led.flight(fid)['state'])
        self.assertEqual(1, len(self.led.landings()))
        self.assertEqual([], self.led.leases())

    def test_stale_dead_process_evidence_does_not_block_reconciliation(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.event('work.executing', fid, {'issue': 1}, 'work')
        self.led.event('work.process', fid, {'pid': 2147483647}, 'work')
        self.led.conn.execute('UPDATE flights SET pid=NULL WHERE id=?', (fid,))
        delivered = dict(state='delivered', verified=True, evidence=['existing'],
                         idempotency_key=task['dedupe_key'])
        with patch('nexus.work.proof', return_value=delivered):
            self.assertEqual('done', self.run_work()[0]['state'])
        self.assertEqual([], self.calls())
        self.assertEqual('landed', self.led.flight(fid)['state'])

    def test_future_backoff_does_not_spend_selection_capacity(self):
        self.issues.append(dict(number=2, title='Second', state='open', labels=[{'name': 'ready'}]))
        self.led.event('work.item_attempt', 'sample/product#1', dict(step='review', status='pending', retry_at=10**12), 'conveyor')
        report = work.run(self.led, [self.entry], max_items=1)
        self.assertEqual(['done'], [r['state'] for r in report])
        self.assertEqual([1], [c['issue']['number'] for c in self.calls()])

    def test_aging_and_urgent_selection(self):
        self.issues.append(dict(number=2, title='Urgent', state='open', labels=[{'name': 'ready'}, {'name': 'urgent'}]))
        work.discover(self.led, self.entry)
        first = next(t for t in self.led.tasks() if t['title'] == 'First')
        self.led.conn.execute('UPDATE tasks SET created_at=1 WHERE id=?', (first['id'],))
        work.run(self.led, [self.entry], max_items=1)
        self.assertEqual([1], [c['issue']['number'] for c in self.calls()])

    def test_office_pending_reason_replaces_historical_failure(self):
        from sources import flows
        (self.root / 'fail-1').touch()
        self.run_work()
        task = self.led.tasks()[0]
        self.led.event('work.failure', task['id'], {'next_retry': 0}, 'fixture')
        (self.root / 'pending').write_text(json.dumps(dict(state='pending', reason='client review', evidence=['PR20'])))
        self.assertEqual('pending', self.run_work()[0]['state'])
        report = work.status(self.led, [self.entry])
        card = flows.card({'state': 'unconfigured', 'work': report})
        self.assertTrue(any(f['label'] == 'Work failures' and f['value'] == '0' for f in card['facts']))
        self.assertEqual('client review', report['tasks'][0]['disposition']['reason'])
        self.assertTrue(report['tasks'][0]['failure'])

    def test_done_obligations_do_not_consume_future_budget(self):
        self.run_work()
        self.issues.append(dict(number=2, title='Next', state='open', labels=[{'name': 'ready'}]))
        report = work.run(self.led, [self.entry], max_items=1)
        self.assertEqual(1, len(report))
        self.assertEqual([1, 2], [c['issue']['number'] for c in self.calls()])

    def test_old_unready_backlog_does_not_starve_new_ready_with_one_slot(self):
        self.issues = [dict(number=n, title=f'Old {n}', state='open',
                            labels=[{'name': 'hold'}] if n % 2 else []) for n in range(1, 21)]
        work.discover(self.led, self.entry)
        self.led.conn.execute('UPDATE tasks SET created_at=1')
        for number in (21, 22):
            self.issues.append(dict(number=number, title=f'Ready {number}', state='open',
                                    labels=[{'name': 'ready'}]))
            work.run(self.led, [self.entry], max_items=1)
            self.assertEqual(number, self.calls()[-1]['issue']['number'])
        self.assertEqual([21, 22], [call['issue']['number'] for call in self.calls()])
        old = [t for t in self.led.tasks() if t['title'].startswith('Old ')]
        self.assertEqual(20, len(old))
        projected = {row['id']: row['disposition']['state']
                     for row in work.status(self.led, [self.entry])['tasks']}
        self.assertTrue(all(projected[t['id']] in ('held', 'ineligible') for t in old))
        self.assertEqual(2, len(self.led.events(kind='work.executing')))

    def test_live_eligibility_change_leaves_slot_for_next_ready(self):
        self.issues.append(dict(number=2, title='Next', state='open', labels=[{'name': 'ready'}]))
        original = work.issue_now
        def changed(led, entry, task):
            if 'title' in task.keys() and task['title'] == 'First':
                self.issues[0]['labels'] = [{'name': 'hold'}]
            return original(led, entry, task)
        with patch('nexus.work.issue_now', side_effect=changed):
            report = work.run(self.led, [self.entry], max_items=1)
        self.assertEqual(['held', 'done'], [row['state'] for row in report])
        self.assertEqual([2], [call['issue']['number'] for call in self.calls()])

    def test_spaced_tbs_in_pr_label_resumes_pending_proof(self):
        self.issues[0]['labels'] = [{'name': 'in pr'}]
        self.prs = [dict(number=20, state='open')]
        (self.root / 'pending').write_text(json.dumps(dict(state='pending', reason='exact-head client review', evidence=['PR20'])))
        self.assertEqual('pending', self.run_work()[0]['state'])
        self.assertEqual([], self.calls())
        self.assertEqual([], self.led.events(kind='work.failure'))
