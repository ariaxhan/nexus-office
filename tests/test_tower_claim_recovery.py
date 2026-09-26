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
        # the legacy row, written before that rule (and before terminal evidence)
        self.led.conn.execute("UPDATE flights SET state='verified' WHERE id=?", (fid,))
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

    def test_dirty_checkout_from_dead_owner_is_preserved_uncaptured_and_the_flight_exits(self):
        """Bytes dirty before the flight began may be a person's: never captured, never restored, named.
        The dead flight still exits instead of sitting in `resolving` forever (#210 D3)."""
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        record = {'flight': fid, 'head': 'original'}
        with patch.dict(os.environ, {'NEXUS_WORK_REGISTRY': 'fixture'}), \
             patch('nexus.flights.alive', return_value=False), \
             patch('nexus.work.registry', return_value=[self.entry]), \
             patch('nexus.lease.read', return_value=record), \
             patch('nexus.lease.flight_paths', return_value=(['human.txt'], ['human.txt'])), \
             patch('nexus.lease.release') as release, \
             patch('nexus.landing.hold') as hold, \
             patch('nexus.landing._git') as git:
            git.return_value.stdout = 'original\n'
            self.assertEqual(tower._reconcile_vanished(self.led, time.time(), self.entry['path']), 1)
        hold.assert_not_called()
        release.assert_called_once_with(self.entry['path'], fid)
        self.assertEqual(self.led.flight(fid)['state'], 'failed')
        self.assertEqual(work.latest(self.led, 'work.recovery_preserved', fid)['left_in_place'], ['human.txt'])

    def test_bytes_the_dead_flight_wrote_are_held_on_its_own_issue(self):
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        record = {'flight': fid, 'head': 'original'}
        held = {'state': 'HELD', 'flight': fid, 'branch': 'aria/held/' + fid, 'sha': 'h', 'comment_url': 'u'}
        with patch.dict(os.environ, {'NEXUS_WORK_REGISTRY': 'fixture'}), \
             patch('nexus.flights.alive', return_value=False), \
             patch('nexus.work.registry', return_value=[self.entry]), \
             patch('nexus.lease.read', return_value=record), \
             patch('nexus.lease.flight_paths', return_value=(['mine.py', 'human.txt'], ['human.txt'])), \
             patch('nexus.lease.release'), \
             patch('nexus.landing.hold', return_value=held) as hold, \
             patch('nexus.landing.terminal', return_value=True), \
             patch('nexus.landing._git') as git:
            git.return_value.stdout = 'original\n'
            self.assertEqual(tower._reconcile_vanished(self.led, time.time(), self.entry['path']), 1)
        repo, rec, paths, collisions, reason, comment = hold.call_args.args
        self.assertEqual((paths, collisions), (['mine.py'], ['mine.py']))  # captured, nothing restored
        self.assertIsNotNone(comment)
        task = self.led.task(self.led.flight(fid)['task_id'])
        with patch('nexus.tower.subprocess.run') as run:
            comment('body')
        self.assertIn(task['dedupe_key'].rsplit('#', 1)[1], run.call_args.args[0])  # the flight's own issue
        self.assertEqual(self.led.flight(fid)['state'], 'failed')
        [artifact] = self.led.conn.execute("SELECT kind, ref FROM artifacts WHERE flight_id=?", (fid,)).fetchall()
        self.assertEqual(tuple(artifact), ('held_branch', 'aria/held/' + fid))

    def test_resolving_dead_owner_with_no_lease_left_exits(self):
        """The live #183 shape: the flight sat in `resolving` and its lease is gone. Nothing is attributable."""
        fid = work.claim(self.led, self.entry['repo'], 1, os.getpid(), runner=True)
        self.led.set_state(fid, 'resolving', expect='running', resolution_step='checkout_ownership_ambiguous')
        with patch.dict(os.environ, {'NEXUS_WORK_REGISTRY': 'fixture'}), \
             patch('nexus.flights.alive', return_value=False), \
             patch('nexus.work.registry', return_value=[self.entry]), \
             patch('nexus.lease.read', return_value=None), \
             patch('nexus.lanes.recover', return_value=[]), patch('nexus.lanes.read', return_value=None):
            self.assertEqual(tower._resolve_ambiguous(self.led, time.time()), 1)
            self.assertEqual(tower._resolve_ambiguous(self.led, time.time()), 0)  # once
        self.assertEqual(self.led.flight(fid)['state'], 'failed')
        self.assertTrue(self.led.conn.execute("SELECT 1 FROM leases WHERE holder_flight=?", (fid,)).fetchone() is None)

if __name__ == '__main__':
    unittest.main()
