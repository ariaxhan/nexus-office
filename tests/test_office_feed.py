"""The feed must preserve provenance and cross-device feedback."""
import os
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_feed


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'OFFICE_STATE': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.post = {'id': 'rss-1', 'source_hash': 'a' * 64, 'model': 'local:qwen2.5:7b-instruct',
                     'category': 'world', 'format': 'story', 'title': 'A sourced event',
                     'body': 'A publisher reported an event.',
                     'sources': [{'title': 'Publisher', 'url': 'https://example.org/story'}]}

    def test_provenance_and_feedback(self):
        with self.assertRaisesRegex(ValueError, 'local model'):
            office_feed.publish(dict(self.post, model='cloud'))
        with self.assertRaisesRegex(ValueError, 'source'):
            office_feed.publish(dict(self.post, sources=[]))
        office_feed.publish(self.post)
        self.assertEqual(office_feed.listing('world')['items'][0]['id'], 'rss-1')
        self.assertEqual(office_feed.listing('ai')['items'], [])
        office_feed.react({'id': 'rss-1', 'kind': 'love', 'active': True})
        office_feed.react({'id': 'rss-1', 'kind': 'dislike', 'active': True})
        office_feed.react({'id': 'rss-1', 'kind': 'save', 'active': True})
        office_feed.reply({'id': 'rss-1', 'body': 'Needs another perspective.'})
        detail = office_feed.detail('rss-1')
        self.assertFalse(detail['feedback']['reactions']['love'])
        self.assertTrue(detail['feedback']['reactions']['dislike'])
        self.assertEqual(detail['feedback']['reply_count'], 1)
        self.assertEqual(office_feed.listing('saved')['items'][0]['id'], 'rss-1')
        with self.assertRaises(Exception):
            office_feed.publish(self.post)

    def test_evolving_buzz_development_updates_one_post(self):
        first = dict(self.post, id='buzz-probe', source_hash='b' * 64,
                     title='Probe failing', body='The production probe found defects.')
        office_feed.publish(first, replace=True)
        office_feed.react({'id': 'buzz-probe', 'kind': 'save', 'active': True})
        later = dict(first, source_hash='c' * 64, title='Probe recovered',
                     body='The production probe passes all checks.')
        office_feed.publish(later, replace=True)
        with office_feed.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM posts').fetchone()[0], 1)
        detail = office_feed.detail('buzz-probe')
        self.assertEqual(detail['title'], 'Probe recovered')
        self.assertTrue(detail['feedback']['reactions']['save'])

    def test_latest_is_chronological_and_for_you_uses_saved_topic_signal(self):
        older = dict(self.post, published_at=time.time() - 7 * 3600)
        newer = dict(self.post, id='rss-2', source_hash='b' * 64,
                     category='ai', published_at=time.time())
        office_feed.publish(older)
        office_feed.publish(newer)
        old_time = older['published_at']
        with office_feed.connect() as db:
            row = db.execute('SELECT payload FROM posts WHERE id=?', ('rss-1',)).fetchone()
            payload = json.loads(row['payload'])
            payload['published_at'] = old_time
            db.execute('UPDATE posts SET published_at=?,payload=? WHERE id=?',
                       (old_time, json.dumps(payload), 'rss-1'))
        self.assertEqual(office_feed.listing('latest')['items'][0]['id'], 'rss-2')
        office_feed.react({'id': 'rss-1', 'kind': 'save', 'active': True})
        self.assertEqual(office_feed.listing('all')['items'][0]['id'], 'rss-1')


if __name__ == '__main__':
    unittest.main()
