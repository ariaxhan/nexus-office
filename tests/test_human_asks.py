"""Behavioral proof that a request lives until its source proves a transition."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import human_asks as asks


class HumanAskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'asks.sqlite'
        self.seeds = Path(self.tmp.name) / 'sources.json'
        self.ref = 'owner/repo#12'
        self.ask = dict(id='github:owner/repo#12', source='github', source_ref=self.ref,
                        owner='aria', action='Choose the source-backed product policy.',
                        created_at='2026-09-01T00:00:00Z')
        self.seeds.write_text(json.dumps([self.ask]))
        self.patcher = patch.object(asks, 'SEEDS', self.seeds)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def read(self, result=None):
        def fetch(_):
            if isinstance(result, Exception):
                raise result
            return result or {'state': 'OPEN', 'url': 'https://github.com/owner/repo/issues/12'}
        return asks.listing(self.db, fetch)['items']

    def test_open_survives_process_stop_and_source_failure(self):
        self.assertEqual(len(self.read()), 1)
        # Reopening the database simulates Office and the worker exiting.
        stale = self.read(RuntimeError('GitHub unavailable'))
        self.assertEqual([x['id'] for x in stale], [self.ask['id']])
        self.assertEqual(stale[0]['source_stale'], 1)
        self.assertTrue(self.read()[0]['last_source_verification'])

    def test_source_closure_removes_ask_with_receipt(self):
        self.read()
        self.assertEqual(self.read({'state': 'CLOSED', 'closedAt': '2026-09-02T00:00:00Z',
                                    'url': 'https://github.com/owner/repo/issues/12'}), [])
        with asks.connect(self.db) as db:
            row = db.execute('SELECT state,resolution_evidence FROM asks').fetchone()
        self.assertEqual(row['state'], 'resolved')
        self.assertEqual(json.loads(row['resolution_evidence'])['closed_at'], '2026-09-02T00:00:00Z')

    def test_reassignment_and_supersession_require_exact_source_evidence(self):
        self.read()
        with asks.connect(self.db) as db:
            with self.assertRaises(ValueError):
                asks.transition(db, self.ask['id'], 'superseded', {'source_ref': 'another/repo#1'})
            self.assertEqual(len(self.read()), 1)
            asks.transition(db, self.ask['id'], 'reassigned', {'source_ref': self.ref,
                           'receipt': 'source-owner-routing-1'}, owner='tim')
        self.assertEqual(self.read(), [])
        with asks.connect(self.db) as db:
            asks.transition(db, self.ask['id'], 'superseded', {'source_ref': self.ref,
                           'receipt': 'source-owner-supersession-2'})
            self.assertEqual(db.execute('SELECT state FROM asks').fetchone()['state'], 'superseded')

    def test_duplicate_observation_and_body_ask_without_bot_last(self):
        # The source declaration names the requested action. No bot-last field exists.
        self.assertEqual(len(self.read()), 1)
        with asks.connect(self.db) as db:
            asks.observe(db, self.ask)
            self.assertEqual(db.execute('SELECT count(*) FROM asks').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM ask_events').fetchone()[0], 1)

    def test_typed_body_ask_is_captured_without_bot_last(self):
        self.seeds.write_text('[]')
        marker = '<!-- office-human-ask\n' + json.dumps({
            'key': 'policy', 'owner': 'aria', 'action': 'Choose the policy.'}) + '\n-->'
        stations = [{'repo': 'owner/repo', 'issues': [{'number': 12, 'body': marker,
                     'bot_last': False, 'updatedAt': '2026-09-01T00:00:00Z',
                     'human_ask_declarations': asks.declarations(marker)}]}]
        first = asks.listing(self.db, lambda _: {'state': 'OPEN'}, stations=stations)['items']
        second = asks.listing(self.db, lambda _: {'state': 'OPEN'}, stations=stations)['items']
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['id'], 'github:owner/repo#12:policy')
        self.assertEqual(len(second), 1)

    def test_source_supersession_comment_removes_ask_only_with_receipt(self):
        self.read()
        unresolved = {'state': 'OPEN', 'comments': [{'body': 'This may be obsolete.'}]}
        self.assertEqual(len(self.read(unresolved)), 1)
        marker = '<!-- office-human-ask-outcome\n' + json.dumps({
            'id': self.ask['id'], 'state': 'superseded', 'evidence': 'later authority ruling'}) + '\n-->'
        source = {'state': 'OPEN', 'comments': [{'body': marker, 'url': 'https://github.com/owner/repo/issues/12#comment-3'}]}
        self.assertEqual(self.read(source), [])
        with asks.connect(self.db) as db:
            row = db.execute('SELECT state,resolution_evidence FROM asks').fetchone()
        self.assertEqual(row['state'], 'superseded')
        self.assertIn('comment-3', row['resolution_evidence'])

    def test_unrelated_failure_and_tim_care_escalation_are_not_aria_asks(self):
        self.seeds.write_text('[]')
        with asks.connect(self.db) as db:
            asks.observe(db, dict(id='care:thread-1', source='care', source_ref='thread-1',
                                  owner='tim', action='Review customer escalation',
                                  created_at='2026-09-01T00:00:00Z'))
        self.assertEqual(self.read(), [])
        # No source declaration or publisher call exists for an automation failure.
        with asks.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM asks').fetchone()[0], 1)

    def test_permission_survives_flight_stop_until_ledger_closes_it(self):
        import office_tasks
        import run_board
        import runtime
        ledger_path = Path(self.tmp.name) / 'ledger.sqlite'
        with sqlite3.connect(ledger_path) as ledger:
            ledger.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,ts REAL,kind TEXT,subject TEXT,payload TEXT)')
            ledger.execute("INSERT INTO events VALUES(8,1790000000,'office.permission','task-a',?)",
                           (json.dumps({'params': {'reason': 'Choose the exact access boundary'}}),))
        with patch.object(run_board, 'LEDGER', ledger_path), \
             patch.object(office_tasks, 'permissions', return_value={'items': []}), \
             patch.object(runtime, 'read_gates', return_value={'state': 'ok', 'gates': []}):
            with asks.connect(self.db) as db:
                asks.ingest_runtime_asks(db)
                row = db.execute("SELECT state,source_stale FROM asks WHERE id='office-permission:8'").fetchone()
                self.assertEqual((row['state'], row['source_stale']), ('open', 1))
            with sqlite3.connect(ledger_path) as ledger:
                ledger.execute("INSERT INTO events VALUES(9,1790000010,'office.permission_closed','task-a',?)",
                               (json.dumps({'permission_id': 8}),))
            with asks.connect(self.db) as db:
                asks.ingest_runtime_asks(db)
                row = db.execute("SELECT state,resolution_evidence FROM asks WHERE id='office-permission:8'").fetchone()
                self.assertEqual(row['state'], 'resolved')
                self.assertEqual(json.loads(row['resolution_evidence'])['ledger_event'], 9)


if __name__ == '__main__':
    unittest.main()
