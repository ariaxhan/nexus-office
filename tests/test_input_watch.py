import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus import input_watch as W

REPO = 'ariaxhan/nexus-office'
OLD = '2026-10-17T10:00:00Z'
NEW = '2026-10-17T10:05:00Z'
NOW = 1792231500  # 2026-10-17T10:05:00Z


def item(**extra):
    return dict(id=123, created_at=NEW, updated_at=NEW, body='do the work', title='work',
                user={'login': 'ariaxhan'}, html_url='https://github.com/a/b/issues/1',
                issue_url='https://api.github.com/repos/a/b/issues/1', **extra)


def state():
    return {'repos': {REPO: {c: {'watermark': OLD, 'skips': {}} for c in W.CLASSES}}}


class InputTests(unittest.TestCase):
    def test_normalization_all_five(self):
        for cls in W.CLASSES:
            with self.subTest(cls=cls):
                raw = item() if cls != 'failed_check_runs' else dict(
                    id='node', databaseId=123, completedAt=NEW, conclusion='FAILURE',
                    name='unit tests', detailsUrl='https://github.com/a/b/runs/123')
                event = W.normalize(REPO, cls, raw)
                self.assertEqual(event['dedupe'], f'input:{cls}:123')
                self.assertEqual(event['at'], NEW)
                self.assertEqual(event['repo'], REPO)

    def test_actual_ledger_dedupe_and_separate_classes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(W, 'LEDGER', Path(tmp)/'ledger.sqlite'):
            db = sqlite3.connect(W.LEDGER)
            db.execute('CREATE TABLE tasks(id,plan_id,origin,title,reason,state,dedupe_key,created_at,objective)')
            db.commit()
            event = W.normalize(REPO, 'issues', item())
            self.assertEqual(W.register(event), 'registered')
            self.assertEqual(W.register(event), 'duplicate')
            event = W.normalize(REPO, 'pr_comments', item())
            self.assertEqual(W.register(event), 'registered')
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 2)
            db.close()

    def test_first_run_initializes_now_without_fetch_or_registration(self):
        s = {}
        def forbidden(*a, **kw):
            self.fail('initialization must not fetch/register')
        W.poll(s, {REPO: list(W.CLASSES)}, NOW, forbidden, forbidden)
        self.assertEqual({r['watermark'] for r in s['repos'][REPO].values()}, {W.stamp(NOW)})

    def test_watermark_only_after_durable_registration(self):
        s = state()
        registered = []
        W.poll(s, {REPO: ['issues']}, NOW,
               lambda *a, **kw: ([item()], 'etag', False),
               lambda e: registered.append(e) or 'registered')
        self.assertEqual(len(registered), 1)
        self.assertEqual(s['repos'][REPO]['issues']['watermark'], W.stamp(NOW))
        s = state()
        W.poll(s, {REPO: ['issues']}, NOW,
               lambda *a, **kw: ([item()], 'etag', False),
               lambda e: (_ for _ in ()).throw(RuntimeError('ledger unavailable')))
        self.assertEqual(s['last_run']['status'], 'error')
        self.assertEqual(s['repos'][REPO]['issues']['watermark'], OLD)
        self.assertNotIn('conditional', s['repos'][REPO]['issues'])

    def test_counted_skips_and_disabled_class(self):
        cs = {'watermark': OLD, 'skips': {}}
        raw = item()
        raw['user']['login'] = 'github-actions[bot]'
        result = W.consume(REPO, 'issues', [raw], cs, ['issues'], NEW)
        self.assertEqual(cs['skips'], {'automation_author': 1})
        self.assertEqual(result['skipped'], 1)
        event = W.normalize(REPO, 'issues', item())
        self.assertEqual(W.skip_reason(event, []), 'class_disabled')
        s = state()
        W.poll(s, {REPO: []}, NOW)
        self.assertEqual(s['repos'][REPO]['issues']['watermark'], OLD)
        self.assertEqual(s['repos'][REPO]['issues']['skips']['class_disabled_poll'], 1)

    def test_fresh_metadata_routes_pr_and_issue_comments_no_label_gate(self):
        for cls, meta in [('issue_comments', {'state': 'closed', 'labels': []}),
                          ('pr_comments', {'pull_request': {}, 'labels': []})]:
            cs = {'watermark': OLD}
            seen = []
            W.consume(REPO, cls, [item()], cs, list(W.CLASSES), NEW,
                      lambda e: seen.append(e) or 'registered', lambda url: meta)
            self.assertEqual(seen[0]['class'], cls)
        cs = {'watermark': OLD}
        W.consume(REPO, 'issue_comments', [item()], cs, W.CLASSES, NEW,
                  lambda e: self.fail('wrong comment class'), lambda url: {'pull_request': {}})
        self.assertEqual(cs['skips']['other_comment_class'], 1)

    def test_new_failed_checks_only(self):
        cs = {'watermark': OLD}
        seen = []
        runs = [dict(id='old', completedAt='2026-10-17T09:59:59Z', conclusion='FAILURE'),
                dict(id='good', completedAt=NEW, conclusion='SUCCESS'),
                dict(id='bad', completedAt=NEW, conclusion='FAILURE')]
        W.consume(REPO, 'failed_check_runs', runs, cs, W.CLASSES, NEW,
                  lambda e: seen.append(e) or 'registered')
        self.assertEqual([e['id'] for e in seen], ['bad'])
        self.assertEqual(cs['skips'], {'outside_window': 1, 'not_failed': 1})

    def test_rate_limit_ends_run_and_records_backoff(self):
        s = state()
        calls = []
        def rate(*a, **kw):
            calls.append(a)
            raise W.Backoff(429)
        W.poll(s, {REPO: list(W.CLASSES)}, NOW, rate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(s['backoff_until'], NOW + 1800)
        self.assertEqual(s['last_run']['status'], 'backoff')
        W.poll(s, {REPO: list(W.CLASSES)}, NOW + 60, rate)
        self.assertEqual(len(calls), 1)

    def test_full_rest_page_uses_overlapping_timestamp_keyset(self):
        s = state()
        W.poll(s, {REPO: ['issues']}, NOW,
               lambda *a, **kw: ([item() for _ in range(100)], 'etag', False), lambda e: 'duplicate')
        cs = s['repos'][REPO]['issues']
        self.assertEqual(cs['watermark'], NEW)
        paths = []
        W.poll(s, {REPO: ['issues']}, NOW + 60,
               lambda p, **kw: (paths.append(p) or [], None, False))
        self.assertNotIn('page', W.urllib.parse.parse_qs(W.urllib.parse.urlsplit(paths[0]).query))
        self.assertIn('10%3A04%3A59Z', paths[0])
        self.assertEqual(cs['watermark'], W.stamp(NOW + 60))

    def test_timestamp_saturation_records_error_without_advancing(self):
        s = state()
        rows = [item() for _ in range(100)]
        for row in rows:
            row['updated_at'] = OLD
        W.poll(s, {REPO: ['issues']}, NOW,
               lambda *a, **kw: (rows, None, False), lambda e: 'duplicate')
        self.assertEqual(s['repos'][REPO]['issues']['watermark'], OLD)
        self.assertEqual(s['last_run']['status'], 'error')
        self.assertIn('timestamp', s['repos'][REPO]['issues']['last_error']['reason'])

    def test_class_failure_does_not_starve_other_repo(self):
        s = state()
        second = 'ariaxhan/thinking-brain-school'
        s['repos'][second] = {'issues': {'watermark': OLD, 'skips': {}}}
        def fetch(path, **kw):
            if path == '/graphql':
                raise RuntimeError('check failure')
            return [item()], None, False
        seen = []
        W.poll(s, {REPO: ['failed_check_runs'], second: ['issues']}, NOW, fetch,
               lambda e: seen.append(e) or 'registered')
        self.assertEqual(seen[0]['repo'], second)
        self.assertEqual(s['last_run']['status'], 'error')

    def test_checks_sweep_cursor_and_overflow(self):
        def response(more=False, overflow=False):
            return {'data': {'repository': {'refs': {'pageInfo': {'hasNextPage': False}, 'nodes': [
                {'target': {'history': {'pageInfo': {'hasNextPage': more, 'endCursor': 'next'},
                    'nodes': [{'checkSuites': {'pageInfo': {'hasNextPage': overflow}, 'nodes': []}}]}}}
            ]}}}}
        items, nxt = W.unpack_checks(response(True), {})
        self.assertEqual(nxt['commit_cursor'], 'next')
        self.assertEqual(W.unpack_checks(response(), {})[1], {})
        with self.assertRaisesRegex(RuntimeError, 'overflow'):
            W.unpack_checks(response(overflow=True), {})

    def test_conditional_get_is_bound_to_exact_url(self):
        s = state()
        cs = s['repos'][REPO]['issues']
        path = W.rest_path(REPO, 'issues', cs)
        cs['conditional'] = {'path': path, 'etag': 'v1'}
        seen = []
        W.poll(s, {REPO: ['issues']}, NOW,
               lambda p, **kw: (seen.append(kw) or [], 'v1', True))
        self.assertEqual(seen, [{'etag': 'v1'}])
        self.assertEqual(cs['watermark'], OLD)

    def test_config_mirrors_original_repos_all_classes(self):
        self.assertEqual(W.config(), {r: list(W.CLASSES) for r in
            ['ariaxhan/nexus-office', 'ariaxhan/thinking-brain-school']})


if __name__ == '__main__':
    unittest.main()
