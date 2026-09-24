"""A no-change Tower result must release its issue claim for the next attempt."""

import os
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
        self.assertEqual(self.led.flight(fid)['state'], 'verified')
        with patch('nexus.work.flights.alive', return_value=False):
            self.assertEqual(work.recover_terminal(self.led), [fid])
        self.assertEqual(self.led.flight(fid)['state'], 'cancelled')
        self.assertEqual(self.led.conn.execute('SELECT count(*) FROM leases WHERE holder_flight=?', (fid,)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
