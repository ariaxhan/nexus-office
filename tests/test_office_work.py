"""The Watch work view: buckets are earned from receipts, never from stale lines."""
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_work

NOW = dt.datetime(2026, 10, 15, 0, 30, tzinfo=dt.timezone.utc).timestamp()
H = 3600


class ClassifyTests(unittest.TestCase):
    def test_fresh_claim_with_fresh_activity_is_worked_on(self):
        self.assertEqual(office_work.classify(True, NOW - 600, NOW - 86400, NOW), 'worked_on')

    def test_claim_without_fresh_activity_is_not_worked_on(self):
        # the tbs#278 shape: a claim whose last activity is a week old
        self.assertEqual(office_work.classify(True, NOW - 7 * 86400, NOW - 8 * 86400, NOW), 'stalled')

    def test_fresh_activity_without_a_claim_is_up_next(self):
        self.assertEqual(office_work.classify(False, NOW - 600, NOW - 86400, NOW), 'up_next')

    def test_old_issue_with_no_receipts_is_stalled(self):
        self.assertEqual(office_work.classify(False, None, NOW - 9 * 86400, NOW), 'stalled')

    def test_brand_new_issue_with_no_receipts_is_up_next(self):
        self.assertEqual(office_work.classify(False, None, NOW - 1800, NOW), 'up_next')

    def test_activity_just_inside_the_window_still_counts(self):
        self.assertEqual(office_work.classify(True, NOW - 23 * H, NOW - 86400, NOW), 'worked_on')
        self.assertEqual(office_work.classify(True, NOW - 25 * H, NOW - 86400, NOW), 'stalled')


class DocClaimsTests(unittest.TestCase):
    DOC = """## Running now

Nothing is running.

### [tbs#278](https://github.com/Thinking-Brain-School/tbs/issues/278) Old zombie
- **Status:** opened 2026-10-06, last change 2026-10-07
- **Coordinator:** 10-07 14:44 UTC. Worker running.
**Aria:**

### [thinking-brain-school#636](https://github.com/ariaxhan/thinking-brain-school/issues/636) Fresh claim
- **Coordinator:** 10-15 00:05 UTC. Worker running.
**Aria:**
"""

    def test_claims_parse_with_stamps_and_running_flags(self):
        claims = office_work.doc_claims(self.DOC, now=dt.datetime.fromtimestamp(NOW, dt.timezone.utc))
        zombie = claims[('Thinking-Brain-School/tbs', 278)]
        self.assertTrue(zombie['running'])
        self.assertGreater(NOW - zombie['activity'], 7 * 86400 - 3600)
        fresh = claims[('ariaxhan/thinking-brain-school', 636)]
        self.assertTrue(fresh['running'])
        self.assertLess(NOW - fresh['activity'], 3600)

    def test_stamp_year_steps_back_over_new_year(self):
        now = dt.datetime(2027, 1, 1, 1, 0, tzinfo=dt.timezone.utc)
        claims = office_work.doc_claims(self.DOC.replace('10-15 00:05', '12-31 23:05'), now=now)
        fresh = claims[('ariaxhan/thinking-brain-school', 636)]
        self.assertLess(now.timestamp() - fresh['activity'], 3 * 3600)


class TowerActivityTests(unittest.TestCase):
    def test_working_state_is_a_claim_and_progress_is_activity(self):
        snapshot = {'automation': {'tower': {'issues': [
            {'repo': 'ariaxhan/nexus-office', 'number': 243, 'state': 'working',
             'since': NOW - 7200, 'progress_at': NOW - 300},
            {'repo': 'ariaxhan/nexus-office', 'number': 99, 'state': 'held',
             'since': NOW - 7200, 'progress_at': None}]}}}
        out = office_work.tower_activity(snapshot)
        self.assertTrue(out[('ariaxhan/nexus-office', 243)]['claim'])
        self.assertEqual(out[('ariaxhan/nexus-office', 243)]['activity'], NOW - 300)
        self.assertFalse(out[('ariaxhan/nexus-office', 99)]['claim'])


import time
from unittest import mock


class ShipCompleteTests(unittest.TestCase):
    """Regression (#278): a classified item must always ship.

    The buckets used to be truncated to CAPS after counting, so the server
    classified items the app never received: tbs#620/#636 sat past position
    10 in a 136-item stalled bucket and nexus-office#268 past the up_next
    cap, while the counts reported the full totals. Counts and shipped
    lengths disagreed, silently.
    """

    def _row(self, number, **kw):
        row = {'number': number, 'title': f'issue {number}',
               'html_url': f'https://example.invalid/i/{number}'}
        row.update(kw)
        return row

    def test_buckets_ship_complete_past_the_old_caps(self):
        now = time.time()
        iso = office_work.iso
        stalled = [self._row(1000 + i, created_at=iso(now - 30 * 86400),
                             updated_at=iso(now - 30 * 86400)) for i in range(30)]
        up_next = [self._row(2000 + i, created_at=iso(now - 1800),
                             updated_at=iso(now - 1800)) for i in range(25)]
        done = [self._row(3000 + i, created_at=iso(now - 2 * 86400),
                          updated_at=iso(now - 86400),
                          closed_at=iso(now - 3600)) for i in range(20)]
        worked = [self._row(4000 + i, created_at=iso(now - 86400),
                            updated_at=iso(now - 300)) for i in range(18)]
        open_rows = stalled + up_next + worked
        snapshot = {'automation': {'tower': {'issues': [
            {'repo': 'ariaxhan/nexus-office', 'number': r['number'],
             'state': 'working', 'since': now - 7200, 'progress_at': now - 300}
            for r in worked]}}}

        def fake_issues(access, known, repo, state):
            return list(open_rows) if state == 'open' else list(done)

        class _World:
            snapshot = None

            def access(self):
                return None

        world = _World()
        world.snapshot = snapshot
        desks = [{'id': 'nexus', 'name': 'Nexus Office',
                  'repos': ['ariaxhan/nexus-office']}]
        with mock.patch.object(office_work, 'DESKS', desks), \
                mock.patch.object(office_work, '_issues', fake_issues), \
                mock.patch.object(office_work, '_doc_text', lambda: ''), \
                mock.patch.object(office_work.objects, 'roots', lambda: {}):
            view = office_work._build(world)
        desk = view['desks'][0]
        self.assertEqual({r['number'] for r in desk['stalled']},
                         {r['number'] for r in stalled})
        self.assertEqual({r['number'] for r in desk['up_next']},
                         {r['number'] for r in up_next})
        self.assertEqual({r['number'] for r in desk['worked_on']},
                         {r['number'] for r in worked})
        self.assertEqual({r['number'] for r in desk['done']},
                         {r['number'] for r in done})
        for bucket in ('worked_on', 'stalled', 'up_next'):
            self.assertEqual(desk['counts'][bucket], len(desk[bucket]),
                             f'{bucket}: count disagrees with shipped length')
        self.assertEqual(desk['counts']['open'],
                         len(stalled) + len(up_next) + len(worked))


if __name__ == '__main__':
    unittest.main()
