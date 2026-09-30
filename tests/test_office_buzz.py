import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_buzz


class BuzzAttentionTest(unittest.TestCase):
    def test_channel_prose_cannot_assign_human_ownership(self):
        request = {'id': 'a' * 64, 'thread': 'a' * 64, 'at': '2026-09-24T10:00:00Z',
                   'author': 'Tim', 'channel': 'human-decisions', 'acted': False,
                   'text': 'Hey Aria, can you check this product and tell me what you think?\n\nDetails below.'}
        [pending] = office_buzz._annotate([dict(request)], [])
        self.assertNotIn('needs_you',pending)
        reply = {'id': 'b' * 64, 'thread': request['thread'], 'at': '2026-09-24T11:00:00Z',
                 'author': 'Aria', 'channel': 'human-decisions', 'acted': False, 'text': 'I checked it.'}
        resolved = office_buzz._annotate([dict(request), reply], [])
        self.assertNotIn('needs_you',next(row for row in resolved if row['id'] == request['id']))

    def test_open_queue_instruction_is_not_human_authority(self):
        row = {'id': 'c' * 64, 'thread': 'c' * 64, 'at': '2026-09-24T10:00:00Z',
               'author': 'Caleb', 'channel': 'queue', 'acted': False,
               'text': 'ARIA · tbs#169 Build the script? A. Build it B. Hold'}
        self.assertNotIn('needs_you',office_buzz._annotate([row], [])[0])


if __name__ == '__main__':
    unittest.main()
