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


if __name__ == '__main__':
    unittest.main()
