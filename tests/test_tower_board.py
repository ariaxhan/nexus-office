"""Office shows the retry state from Tower's ledger, not a stale ownership label."""

import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'client'))
import tower_board
from nexus import terminal, tower, work
from nexus.ledger import Ledger
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
    def test_open_dependency_gate_is_waiting_not_retrying(self):
        work.discover(self.led, self.entry)
        task = self.led.tasks()[0]
        self.led.event('work.pending', task['id'], {'reason': 'gate: blocked by open dependency x#2',
                                                    'next_retry': time.time() + 600}, 'work')
        [issue] = tower_board.read(self.root / 'ledger.sqlite')['issues']
        self.assertEqual(('waiting', 'recheck in 10 min'), (issue['state'], issue['next']))

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

    def test_only_landed_terminals_with_a_receipt_count_as_verified(self):
        work.discover(self.led, self.entry)
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.event('flight.terminal', fid, {'state': 'CLOSED', 'reason': 'no_change'}, 'tower')
        self.assertEqual([], tower_board.read(self.root / 'ledger.sqlite')['completions'])
        self.led.event('flight.terminal', fid, {'state': 'LANDED', 'sha': 'abc1234'}, 'tower')
        self.assertEqual([], tower_board.read(self.root / 'ledger.sqlite')['completions'])
        self.led.event('work.receipt', self.led.flight(fid)['task_id'], {'flight': fid, 'sha': 'abc1234'}, 'work')
        [done] = tower_board.read(self.root / 'ledger.sqlite')['completions']
        self.assertEqual((fid, 'abc1234', 'sample/product#1'), (done['flight'], done['sha'], done['issue']))


DAY = 86400


class TowerOutcomeCard(unittest.TestCase):
    """Did Tower do real work: the card, from a ledger built through the ledger's own API."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / 'ledger.sqlite'
        self.led = Ledger(str(self.path))
        self.addCleanup(self.led.close)
        self.now = time.time()
        self.lane = self.led.add_plan('issue-lane', budget={'timeout_s': 600})
        self.ticker = self.led.add_plan('ticker', schedule={'every': 300})
        self.led.event('tower.tick', None, {}, 'tower', ts=self.now)

    def issue(self, number, title):
        return self.led.add_task(title, 'github-work', plan_id=self.lane, dedupe_key=f'github:sample/product#{number}')

    def fly(self, task, ago, plan=None):
        fid = self.led.create_flight(plan or self.lane, task, now=self.now - ago)
        self.led.set_state(fid, 'running', started_at=self.now - ago, now=self.now - ago)
        return fid

    def land(self, task, ago, sha='abc1234def'):
        fid = self.fly(task, ago)
        self.led.event('work.executing', fid, {}, 'work', ts=self.now - ago)
        proof = terminal.landed({'state': 'LANDED', 'sha': sha}, f'landed {sha}')
        for state in ('produced', 'verified', 'landing', 'landed'):
            self.led.set_state(fid, state, now=self.now - ago, evidence=proof)
        self.led.event('work.receipt', task, {'flight': fid, 'sha': sha, 'receipt': f'landed {sha}'}, 'work',
                       ts=self.now - ago)
        return fid

    def ticks(self, count):
        for n in range(count):
            task = self.led.add_scheduled_task(self.led.plan(self.ticker), f'every:{n}', self.now - 60)
            fid = self.fly(task, 60, plan=self.ticker)
            self.led.set_state(fid, 'produced', now=self.now - 59, result={'ok': True, 'artifacts': []})

    def card(self):
        board = tower_board.read(self.path, now=self.now)
        self.assertEqual(set(board['card']), {'title', 'headline', 'needs', 'as_of', 'facts'})
        self.assertLess(len(board['card']['headline']), 80)
        self.assertTrue(3 <= len(board['card']['facts']) <= 8)
        return board, board['card'], {f['label']: f for f in board['card']['facts']}

    def test_healthy_says_what_landed_and_needs_nobody(self):
        self.land(self.issue(7, 'Ship the thing'), 3600)
        board, card, facts = self.card()
        self.assertEqual((0, '1 change landed in 24 h; last product#7 1h ago'), (card['needs'], card['headline']))
        self.assertEqual(('product#7 Ship the thing · abc1234 · 1h ago', 'ok'),
                         (facts['last landed change']['value'], facts['last landed change']['tone']))
        self.assertEqual('1 / 1', facts['landed, 24 h / 7 d']['value'])
        self.assertEqual(('1 executed, 1 landed', 'ok'),
                         (facts['issue flights, 7 d']['value'], facts['issue flights, 7 d']['tone']))
        self.assertTrue(card['as_of'].endswith('Z'))
        self.assertEqual(card['headline'], board['tower']['detail'])  # the pulse line both views already draw

    def test_noop_ticks_are_never_work(self):
        self.ticks(12)
        board, card, facts = self.card()
        self.assertEqual('No change has ever landed', card['headline'])
        self.assertEqual(('0 / 0', 'bad'), (facts['landed, 24 h / 7 d']['value'], facts['landed, 24 h / 7 d']['tone']))
        self.assertEqual('0 executed, 0 landed', facts['issue flights, 7 d']['value'])
        self.assertEqual('12 no-op, not counted as work', facts['scheduler ticks, 24 h']['value'])
        self.assertEqual('nothing', facts['running now']['value'])
        self.assertEqual((0, 'idle'), (card['needs'], board['activity']))

    def test_nothing_landed_recently_is_the_headline(self):
        self.land(self.issue(7, 'Old change'), 5 * DAY + 60)
        fid = self.fly(self.issue(8, 'Tried'), 2 * DAY)
        self.led.event('work.executing', fid, {}, 'work', ts=self.now - 2 * DAY)
        self.led.fail(fid, 'owner_exited', now=self.now - 2 * DAY)
        _, card, facts = self.card()
        self.assertEqual('No change landed in 5 days', card['headline'])
        self.assertEqual(0, card['needs'])  # a quiet week is a fact, not a page
        self.assertEqual('bad', facts['last landed change']['tone'])
        self.assertEqual(('0 / 1', 'warn'), (facts['landed, 24 h / 7 d']['value'], facts['landed, 24 h / 7 d']['tone']))
        self.assertEqual(('2 executed, 1 landed', 'ok'),
                         (facts['issue flights, 7 d']['value'], facts['issue flights, 7 d']['tone']))
        self.assertEqual('none', facts['failed flights, 24 h']['value'])  # that failure is two days old

    def test_quarantined_plan_needs_a_person_and_is_not_idle(self):
        self.land(self.issue(7, 'Ship the thing'), 3600)
        self.led.quarantine_plan(self.ticker, '5 consecutive failures', now=self.now - 7200)
        board, card, facts = self.card()
        self.assertEqual((1, '1 plan quarantined'), (card['needs'], card['headline']))
        self.assertEqual(('ticker (2h)', 'bad'), (facts['quarantined plans']['value'], facts['quarantined plans']['tone']))
        self.assertEqual('failed', board['activity'])

    def test_retry_loop_names_the_worst_issue(self):
        self.land(self.issue(7, 'Ship the thing'), 3600)
        for number, title, flights in ((44, 'Share bank details', 6), (9, 'Other loop', 5), (3, 'Two tries', 2)):
            task = self.issue(number, title)
            for n in range(flights):
                self.led.fail(self.fly(task, 600 + n), 'owner_exited', now=self.now - 500)
        _, card, facts = self.card()
        self.assertEqual((2, '2 issues in retry loops'), (card['needs'], card['headline']))
        self.assertEqual('product#44 Share bank details: 6 flights, none landed (+1 more)',
                         facts['retry loops, 24 h']['value'])
        self.assertEqual(('13 owner_exited', 'bad'),
                         (facts['failed flights, 24 h']['value'], facts['failed flights, 24 h']['tone']))

    def test_flight_past_its_plan_timeout_needs_a_person(self):
        self.fly(self.issue(5, 'Stuck'), 3600)  # the plan allows 600 s
        _, card, facts = self.card()
        self.assertEqual(1, card['needs'])
        self.assertIn('1 flight past its timeout', card['headline'])
        self.assertEqual(('product#5 Stuck, 1h (past timeout)', 'bad'),
                         (facts['running now']['value'], facts['running now']['tone']))

    def test_unreadable_or_missing_ledger_is_a_card_that_says_so(self):
        self.led.close()
        self.path.write_bytes(b'not a sqlite file, and long enough to be read as a header' * 40)
        for path, state in ((self.path, 'unreadable'), (self.path.with_name('gone.sqlite'), 'missing')):
            board = tower_board.read(path, now=self.now)
            self.assertEqual(state, board['state'])
            self.assertEqual(1, board['card']['needs'])
            self.assertIn(f'Tower ledger {state}', board['card']['headline'])
            self.assertEqual('bad', board['card']['facts'][0]['tone'])


if __name__ == '__main__':
    unittest.main()
