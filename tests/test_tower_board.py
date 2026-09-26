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

    def test_no_tick_receipt_reads_as_tower_down_not_idle(self):
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(board['activity'], 'tower-down')
        self.led.event('tower.tick', None, {}, 'tower', time.time() - 10)
        self.assertEqual(tower_board.read(self.root / 'ledger.sqlite')['activity'], 'idle')
        self.assertEqual(tower_board.read(self.root / 'ledger.sqlite', now=time.time() + 600)['activity'],
                         'tower-down')

    def test_tick_receipt_is_throttled(self):
        from nexus import tower
        tower._receipt(self.led, {'launched': 0}, 1000.0)
        tower._receipt(self.led, {'launched': 1}, 1030.0)
        tower._receipt(self.led, {'launched': 1}, 1061.0)
        self.assertEqual(len(self.led.events(kind='tower.tick')), 2)

    def test_queued_says_why_and_paused_wins(self):
        work.discover(self.led, self.entry)
        self.led.event('tower.tick', None, {}, 'tower')
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(board['activity'], 'queued')
        [issue] = board['issues']
        self.assertEqual((issue['state'], issue['detail']), ('queued', 'not yet scheduled'))
        self.assertIsNotNone(issue['since_s'])
        self.led.event('tower.paused', None, {}, 'click')
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(board['activity'], 'paused')
        self.assertEqual(board['issues'][0]['detail'], 'Tower is paused')

    def test_closed_issue_is_dropped(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.disposition', task['id'], {'state': 'closed', 'reason': 'closed'}, 'work')
        self.assertEqual(tower_board.read(self.root / 'ledger.sqlite')['issues'], [])

    def test_only_landed_with_commit_counts_as_verified(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.event('flight.terminal', fid, {'state': 'CLOSED', 'reason': 'no_change'}, 'tower')
        self.assertEqual(tower_board.read(self.root / 'ledger.sqlite')['verified'], [])
        self.led.event('flight.terminal', fid, {'state': 'LANDED', 'sha': 'abc123', 'branch': 'main'}, 'tower')
        self.led.set_state(fid, 'produced')
        self.led.set_state(fid, 'verified')
        self.led.set_state(fid, 'landing')
        self.led.set_state(fid, 'landed')
        [done] = tower_board.read(self.root / 'ledger.sqlite')['verified']
        self.assertEqual((done['flight'], done['sha'], done['issue']), (fid, 'abc123', 'sample/product#1'))


if __name__ == '__main__':
    unittest.main()
