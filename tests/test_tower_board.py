"""Office shows the retry state from Tower's ledger, not a stale ownership label."""

import os
import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'client'))
import tower_board
from nexus import work
from tests import test_work


class TowerBoard(unittest.TestCase):
    github = test_work.WorkTests.github

    def setUp(self):
        test_work.WorkTests.setUp(self)

    def test_pending_flight_is_retrying_even_if_old_disposition_says_owned(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        work.pending(self.led, fid, {'reason': 'No code landed', 'retry_at': time.time() + 600})
        self.led.event('work.disposition', task['id'], {'state': 'owned', 'reason': 'old claim'}, 'work')
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(board['state'], 'ok')
        [issue] = board['issues']
        self.assertEqual(issue['state'], 'retrying')
        self.assertIn('No code landed', issue['detail'])
        self.assertIn('/sample/product/issues/1', issue['url'])

    def test_ambiguous_dead_owner_is_visible_as_held(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.event('work.recovery_ambiguous', fid,
                       {'reason': 'checkout bytes cannot be attributed to dead flight'}, 'tower')
        self.led.set_state(fid, 'resolving', expect='running',
                           resolution_step='checkout_ownership_ambiguous')
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertEqual(issue['state'], 'held')
        self.assertIn('cannot be attributed', issue['detail'])

    def test_dead_owner_without_checkout_is_shown_as_proof_pending(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.fail(fid, 'owner_exited', 'outcome requires proof', expect='running')
        self.led.event('work.recovered', fid,
                       {'reason': 'dead_owner_claim_released', 'replay': False}, 'tower')
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertEqual(issue['state'], 'retrying')
        self.assertIn('Outcome proof', issue['detail'])

    def test_silence_is_tower_down_not_idle(self):
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(board['summary'], 'tower-down')
        self.led.event('tower.tick', None, {}, 'tower', time.time() - 200)
        self.assertEqual(tower_board.read(self.root / 'ledger.sqlite')['summary'], 'tower-down')
        self.led.event('tower.tick', None, {}, 'tower')
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual((board['summary'], board['liveness']), ('idle', 'running'))

    def test_tick_receipt_is_rate_limited(self):
        from nexus import tower
        now = time.time()
        tower.tick(self.led, now=now)
        tower.tick(self.led, now=now + 30)
        tower.tick(self.led, now=now + 61)
        self.assertEqual(len(self.led.events(kind='tower.tick')), 2)

    def test_ready_says_why_and_paused_is_not_idle(self):
        work.discover(self.led, self.entry)
        self.led.event('tower.tick', None, {}, 'tower')
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual((board['summary'], board['queued']), ('queued', 1))
        self.assertEqual(board['issues'][0]['detail'], 'not yet scheduled')
        self.led.event('tower.paused', None, {}, 'click')
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertIn('paused', issue['detail'])

    def test_working_has_ages_and_second_claim_is_a_fault(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertEqual((issue['state'], issue['attempt']), ('working', fid))
        self.assertTrue(issue['started'] and issue['progress'])
        plan = self.led.flight(fid)['plan_id']
        other = 'flt_second'  # the ledger refuses this now; legacy or racing rows must still show
        with self.led.tx():
            self.led.conn.execute("INSERT INTO flights(id,task_id,plan_id,state,created_at) "
                                  "VALUES (?,?,?,'running',?)", (other, task['id'], plan, time.time()))
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertEqual(issue['state'], 'fault')
        self.assertIn(other, issue['detail'])

    def test_closed_issue_leaves_and_receipt_is_a_verified_completion(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.receipt', task['id'], {'flight': 'flt_x', 'sha': 'abc', 'receipt': 'merged'}, 'work')
        self.led.event('work.issue', task['id'], {'number': 1, 'state': 'closed', 'labels': []}, 'work')
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(board['issues'], [])
        self.assertEqual(board['recent'][0]['flight'], 'flt_x')


if __name__ == '__main__':
    unittest.main()
