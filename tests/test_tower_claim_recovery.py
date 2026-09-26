"""A no-change Tower result must release its issue claim for the next attempt."""

import os
import time
import unittest
from unittest.mock import patch

from nexus import tower, work
from tests import test_work


class TowerClaimRecovery(unittest.TestCase):
    github = test_work.WorkTests.github

    def setUp(self):
        test_work.WorkTests.setUp(self)
        work.discover(self.led, self.entry)
        self.task = self.led.tasks()[0]
        token = work._lane.set(work.TOWER_LABEL)
        self.addCleanup(lambda: work._lane.reset(token))

    def test_no_change_settlement_releases_claim(self):
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        state = work._settle(self.led, fid, self.entry, self.task, self.issues[0],
                             {'state': 'CLOSED', 'flight': fid, 'reason': 'no_change'})
        self.assertEqual(state, 'pending')
        self.assertEqual(self.led.flight(fid)['state'], 'cancelled')
        self.assertEqual(self.led.conn.execute('SELECT count(*) FROM leases WHERE holder_flight=?', (fid,)).fetchone()[0], 0)
        self.assertTrue(work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True))

    def test_legacy_verified_no_change_is_recovered_after_owner_exits(self):
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        tower.land_write_flight(self.led, fid, self.entry['path'],
                                {'state': 'CLOSED', 'flight': fid, 'reason': 'no_change'})
        self.assertEqual(self.led.flight(fid)['state'], 'running')  # no_change is never verified now
        for state in ('produced', 'verified'):  # the legacy row, written before that rule
            self.led.set_state(fid, state, source='tower')
        with patch('nexus.work.flights.alive', return_value=False):
            self.assertEqual(work.recover_terminal(self.led), [fid])
        self.assertEqual(self.led.flight(fid)['state'], 'cancelled')
        self.assertEqual(self.led.conn.execute('SELECT count(*) FROM leases WHERE holder_flight=?', (fid,)).fetchone()[0], 0)

    def test_vanished_work_owner_releases_claim_without_replaying_execution(self):
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.event('work.executing', fid, {'action': 'may_have_run'}, 'work')
        with patch.dict(os.environ, {'NEXUS_WORK_REGISTRY': 'fixture'}), \
             patch('nexus.flights.alive', return_value=False), \
             patch('nexus.work.registry', return_value=[dict(self.entry, path=None)]):
            self.assertEqual(tower._reconcile_vanished(self.led, 10000000000, self.entry['path']), 1)
        self.assertEqual(self.led.flight(fid)['state'], 'failed')
        self.assertEqual(self.led.conn.execute('SELECT count(*) FROM leases WHERE holder_flight=?', (fid,)).fetchone()[0], 0)
        self.assertEqual(work.latest(self.led, 'work.recovered', fid)['replay'], False)

    def test_dirty_checkout_from_dead_owner_is_escalated_without_capturing_bytes(self):
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        record = {'flight': fid, 'head': 'original'}
        with patch.dict(os.environ, {'NEXUS_WORK_REGISTRY': 'fixture'}), \
             patch('nexus.flights.alive', return_value=False), \
             patch('nexus.work.registry', return_value=[self.entry]), \
             patch('nexus.lease.read', return_value=record), \
             patch('nexus.lease.flight_paths', return_value=(['human.txt'], ['human.txt'])), \
             patch('nexus.lease.release') as release, \
             patch('nexus.landing._git') as git:
            git.return_value.stdout = 'original\n'
            self.assertEqual(tower._reconcile_vanished(self.led, time.time(), self.entry['path']), 0)
        release.assert_not_called()
        self.assertEqual(self.led.flight(fid)['state'], 'resolving')
        self.assertEqual(work.latest(self.led, 'work.recovery_ambiguous', fid)['changed_paths'], 1)


if __name__ == '__main__':
    unittest.main()
