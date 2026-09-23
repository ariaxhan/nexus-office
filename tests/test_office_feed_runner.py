import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_feed_runner as runner


class EditorialGateTest(unittest.TestCase):
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
