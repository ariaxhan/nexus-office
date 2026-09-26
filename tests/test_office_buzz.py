import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_buzz


class BuzzReadTests(unittest.TestCase):
    def test_closed_queue_ask_stays_in_history_and_open_ask_needs_you(self):
        base = {'channel': 'queue', 'text': 'ARIA · tbs#225\nA. done\nB. cannot\nreply with the letter\nhttps://github.com/Thinking-Brain-School/tbs/issues/225',
                'at': '2026-09-24T12:36:17Z', 'acted': False, 'id': 'a'}
        rows = [dict(base), dict(base, id='b')]
        with patch.object(office_buzz, '_issue_state', return_value='open'):
            office_buzz._annotate(rows, [])
        self.assertEqual(sum(row['needs_you'] for row in rows), 1)
        with patch.object(office_buzz, '_issue_state', return_value='closed'):
            office_buzz._annotate(rows, [])
        self.assertEqual(sum(row['needs_you'] for row in rows), 0)
        self.assertEqual(len(rows), 2)

    def test_mirror_and_read_receipts_do_not_imply_action(self):
        row = {'channel': 'general', 'text': '@Aria please decide?', 'at': '2026-09-24T15:30:53Z',
               'acted': False, 'mirrored': True, 'coordinator_read': True, 'id': 'caleb'}
        office_buzz._annotate([row], [])
        self.assertFalse(row['needs_you'])
        self.assertFalse(row['acted'])


if __name__ == '__main__':
    unittest.main()
