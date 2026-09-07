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

    def test_intake_survives_source_window(self):
        work.discover(self.led, self.entry)
        self.led.conn.execute("UPDATE tasks SET created_at=1")
        self.issues = []
        self.assertEqual("closed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())
        self.assertEqual(1, len(self.led.tasks()))

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

    def test_conveyor_backoff_is_shared(self):
        self.led.event("work.item_attempt", "sample/product#1",
                       dict(step="build", status="failed", attempt=1, retry_at=10**12, evidence=[]), "conveyor")
        self.assertEqual("backoff", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())

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

    def test_conveyor_steps_and_case_preserve_backoff(self):
        for step, retry in (("build", 10**12), ("review", 0)):
            self.led.event("work.item_attempt", "Sample/Product#1",
                           dict(step=step, status="failed", retry_at=retry), "conveyor")
        self.assertEqual("backoff", self.run_work()[0]["state"])
        self.assertEqual(2, len(work.status(self.led, [self.entry])["item_attempts"]))

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

    def test_executor_budget_still_allows_reconciliation(self):
        self.entry["max_attempts"] = 1
        (self.root / "fail-1").touch()
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.led.event("work.failure", self.led.tasks()[0]["id"], {"next_retry": 0}, "fixture")
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual(1, len(self.calls()))
        (self.root / "delivered-1").touch()
        self.led.event("work.failure", self.led.tasks()[0]["id"], {"next_retry": 0}, "fixture")
        self.assertEqual("done", self.run_work()[0]["state"])
        self.assertEqual(1, len(self.calls()))

    def test_disabled_missing_repository_visible_without_hydration(self):
        entry = dict(self.entry, enabled=False, path=str(self.root / "missing"))
        self.assertEqual([], work.run(self.led, [entry]))
        row = work.status(self.led, [entry])["repositories"][0]
        self.assertFalse(row["enabled"])
        self.assertFalse(row["available"])
        self.assertFalse(Path(entry["path"]).exists())

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
        (self.root / "unknown").touch()
        self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.closed)

    def test_retry_requires_authoritative_clearance(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry["repo"], 1, os.getpid(), runner=True)
        self.led.event("work.executing", fid, {"issue": 1}, "work")
        self.led.conn.execute("UPDATE flights SET pid=2147483647 WHERE id=?", (fid,))
        with patch("nexus.work.proof", return_value={"state": "absent"}):
            self.assertEqual("failed", self.run_work()[0]["state"])
        self.assertEqual([], self.calls())

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

    def test_fair_bounded_selection_across_repositories(self):
        self.issues += [dict(number=n, title=str(n), state='open', labels=[{'name': 'ready'}]) for n in range(2, 5)]
        (self.root / 'fail-1').touch()
        other = dict(self.entry, repo='sample/other')
        report = work.run(self.led, [self.entry, other], max_items=2)
        self.assertEqual(['sample/product', 'sample/other'], [r['repo'] for r in report])
        self.assertEqual(2, len(self.calls()))

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

    def test_future_backoff_does_not_spend_selection_capacity(self):
        self.issues.append(dict(number=2, title='Second', state='open', labels=[{'name': 'ready'}]))
        self.led.event('work.item_attempt', 'sample/product#1', dict(step='review', status='pending', retry_at=10**12), 'conveyor')
        report = work.run(self.led, [self.entry], max_items=1)
        self.assertEqual(['backoff', 'done'], [r['state'] for r in report])
        self.assertEqual([2], [c['issue']['number'] for c in self.calls()])

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

    def test_pending_execution_requires_retry_clearance_when_ready_remains(self):
        self.entry['executor'] = [sys.executable, '-c', "import pathlib,json; pathlib.Path('pending').write_text(json.dumps(dict(state='pending',reason='review')))" ]
        self.assertEqual('pending', self.run_work()[0]['state'])
        task = self.led.tasks()[0]
        self.led.event('work.pending', task['id'], {'next_retry': 0}, 'fixture')
        with patch('nexus.work.proof', return_value={'state': 'absent'}):
            self.assertEqual('failed', self.run_work()[0]['state'])
        self.assertEqual(1, len(self.led.events(kind='work.executing')))

    def test_historical_started_requires_adapter_clearance(self):
        self.led.event('work.item_attempt', 'sample/product#1', dict(step='build', status='started'), 'conveyor')
        with patch('nexus.work.proof', return_value={'state': 'absent'}) as verify:
            self.assertEqual('failed', self.run_work()[0]['state'])
            verify.assert_called_once()
        self.assertEqual([], self.calls())

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
        self.assertTrue(all(work.latest(self.led, 'work.disposition', t['id'])['state']
                            in ('held', 'ineligible') for t in old))
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
