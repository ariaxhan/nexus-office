import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_feed_runner as runner


class EditorialGateTest(unittest.TestCase):
    def test_buzz_held_state_rejects_false_release_but_accepts_negation(self):
        item = {'kind': 'buzz_development',
                'text': 'L044 is held, not published. The deployed page differs from the approved copy.'}
        approved = {'title': 'L044', 'body': 'L044 is currently held and not published.', 'evidence': []}
        runner.prepare_output(approved, [item])
        false_release = {'title': 'L044', 'body': 'L044 is published and live.', 'evidence': []}
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            runner.prepare_output(false_release, [item])

    def test_buzz_is_grouped_before_feed_editorial_review(self):
        stamp = datetime.now(timezone.utc).isoformat()
        def row(ident, channel, text, issue=None):
            return {'id': ident * 64, 'thread': 'a' * 64, 'channel': channel,
                    'author': 'Caleb', 'at': stamp, 'text': text, 'issue': issue,
                    'needs_you': False}
        snapshot = {'errors': [], 'items': [
            row('1', 'workroom', 'Production probe FAIL. Six defects in the current deployment; investigate the release. ' * 2,
                'Thinking-Brain-School/tbs-www#680'),
            row('2', 'workroom', 'Production probe FAIL again. The same release remains broken and needs repair. ' * 2,
                'Thinking-Brain-School/tbs-www#680'),
            row('3', 'workroom', 'Recovered: production probe passes again. Every check PASS after the repair. ' * 2,
                'Thinking-Brain-School/tbs-www#680'),
            row('4', 'queue', 'no new questions · 7 still open above · 93 building ' * 3),
        ]}
        with patch.object(runner.office_buzz, 'listing', return_value=snapshot):
            found = runner.buzz_rows()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['title'], 'Production probe outcome')
        self.assertEqual(len(found[0]['source_records']), 3)
        self.assertTrue(found[0]['url'].startswith('/api/buzz/detail?id='))

    def test_astronomy_paper_is_not_misclassified_as_history(self):
        item = {'bucket': 'research', 'source': 'arXiv',
                'title': 'Continuous Learning from History of Astrophysical Time Series'}
        self.assertEqual(runner.category(item), 'science')

    def test_world_claim_needs_distinct_matching_publications(self):
        base = {'id': 'one', 'bucket': 'world-news', 'source': 'Paper A',
                'title': 'Parliament approves election law after vote', 'text': 'a' * 100,
                'url': 'https://example.org/one'}
        self.assertEqual(runner.bundles([base]), [])
        second = dict(base, id='two', source='Paper B',
                      title='Election law approved by parliament after vote',
                      url='https://example.net/two')
        self.assertEqual(len(runner.bundles([base, second])), 1)

    def test_model_must_quote_each_source_exactly(self):
        sources = [{'text': 'Parliament approved the election law after a vote.', 'url':'https://a.example/story'},
                   {'text': 'Legislators voted to approve the election law.', 'url':'https://b.example/story'}]
        payload = {'title': 'A vote', 'body': 'Two publishers report the election law was approved.',
                   'evidence': [0, 0]}
        self.assertEqual(runner.verify(payload, sources)[0], 'A vote')
        payload['evidence'][1] = 99
        with self.assertRaisesRegex(ValueError, 'Evidence'):
            runner.verify(payload, sources)


if __name__ == '__main__':
    unittest.main()
