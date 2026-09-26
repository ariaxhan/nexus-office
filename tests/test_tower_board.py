"""Office shows the retry state from Tower's ledger, not a stale ownership label."""

import os
import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'client'))
import tower_board
from nexus import tower, work
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


    def test_issue_nobody_queued_is_not_advertised_as_waiting_for_tower(self):
        work.discover(self.led, self.entry)
        self.issues[0]['labels'] = []  # e.g. a human-owned commission: no ready label, never flown
        work.discover(self.led, self.entry)
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual([], board['issues'])
        self.assertEqual(1, board['not_queued'])

    def test_closed_gate_is_shown_as_waiting_with_its_reason(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.disposition', task['id'],
                       {'state': 'backoff', 'reason': 'gate: blocked by open dependency #183'}, 'work')
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertEqual(('waiting', 'gate: blocked by open dependency #183'), (issue['state'], issue['detail']))

    def test_held_rows_are_never_cut_behind_a_long_ready_queue(self):
        self.issues[:] = [dict(number=n, title=f'n{n}', state='open', labels=[{'name': 'ready'}]) for n in range(1, 71)]
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry['repo'], 70, os.getpid(), runner=True)
        self.led.set_state(fid, 'resolving', expect='running', resolution_step='checkout_ownership_ambiguous')
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(('held', 70), (board['issues'][0]['state'], board['issues'][0]['number']))
        self.assertEqual(60, len(board['issues']))
        self.assertEqual(10, board['dropped'])

    def test_fresh_ineligible_disposition_beats_a_leftover_ready_label(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.disposition', task['id'],
                       {'state': 'ineligible', 'reason': 'x', 'labels': ['ready']}, 'work')
        self.assertEqual([], tower_board.read(self.root / 'ledger.sqlite')['issues'])
        self.led.event('work.disposition', task['id'], {'state': 'held', 'reason': 'held: hold', 'labels': ['ready']}, 'work')
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertEqual('held', issue['state'])

    def test_disposition_about_older_labels_is_not_authoritative(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.disposition', task['id'], {'state': 'ineligible', 'reason': 'x', 'labels': []}, 'work')
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']  # ready was added after that decision
        self.assertEqual('ready', issue['state'])
    def test_no_tick_receipt_is_down_not_idle(self):
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(('down', 'down'), (board['tower']['state'], board['activity']))
        self.assertIn('no Tower tick receipt', board['tower']['detail'])

    def test_fresh_tick_with_nothing_eligible_is_idle(self):
        tower.tick(self.led)
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual(('running', 'idle'), (board['tower']['state'], board['activity']))
        self.assertEqual([], board['completions'])

    def test_stale_tick_is_down_and_receipts_are_rate_limited(self):
        now = time.time()
        tower.tick(self.led, now=now - 1000)
        tower.tick(self.led, now=now - 990)
        self.assertEqual(1, len(self.led.events(kind='tower.tick')))
        board = tower_board.read(self.root / 'ledger.sqlite', now=now)
        self.assertEqual('down', board['activity'])
        self.assertIn('16m', board['tower']['detail'])

    def test_paused_tower_says_why_ready_work_is_not_launching(self):
        work.discover(self.led, self.entry)
        tower.pause(self.led, 'test')
        tower.tick(self.led)
        board = tower_board.read(self.root / 'ledger.sqlite')
        self.assertEqual('paused', board['activity'])
        [issue] = board['issues']
        self.assertEqual(('ready', 'queued: Tower is paused'), (issue['state'], issue['detail']))
        self.assertEqual(1, board['queued'])

    def test_working_row_carries_flight_phase_and_ages(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        tower.tick(self.led)
        board = tower_board.read(self.root / 'ledger.sqlite')
        [issue] = board['issues']
        self.assertEqual(('working', fid, 'running'), (issue['state'], issue['attempt'], issue['phase']))
        self.assertTrue(issue['age'])
        self.assertEqual('', issue['progress'])  # running alone is not progress
        self.assertEqual('working', board['activity'])

    def test_closed_issue_leaves_the_board(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.issue', task['id'], dict(number=1, title='x', state='closed',
                                                      labels=[{'name': 'hold'}]), 'work')
        self.assertEqual([], tower_board.read(self.root / 'ledger.sqlite')['issues'])

    def test_only_landed_terminals_count_as_verified(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.event('flight.terminal', fid, {'state': 'CLOSED', 'reason': 'no_change'}, 'tower')
        self.assertEqual([], tower_board.read(self.root / 'ledger.sqlite')['completions'])
        self.led.event('flight.terminal', fid, {'state': 'LANDED', 'sha': 'abc1234'}, 'tower')
        [done] = tower_board.read(self.root / 'ledger.sqlite')['completions']
        self.assertEqual((fid, 'abc1234', 'sample/product#1'), (done['flight'], done['sha'], done['issue']))


if __name__ == '__main__':
    unittest.main()
