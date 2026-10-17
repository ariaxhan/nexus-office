import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_fallback_freshness as fallback  # noqa: E402


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FallbackStatusTest(unittest.TestCase):
    def setUp(self):
        fallback._CACHED = (0.0, None)
        self.env = patch.dict(os.environ, {
            'OFFICE_FALLBACK_FRESHNESS_URL': 'https://fallback-health.ariaxhan.com/api/health',
            'OFFICE_FALLBACK_CF_ACCESS_CLIENT_ID': 'read-id',
            'OFFICE_FALLBACK_CF_ACCESS_CLIENT_SECRET': 'read-secret',
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_unconfigured_makes_no_network_request(self):
        del os.environ['OFFICE_FALLBACK_CF_ACCESS_CLIENT_SECRET']
        with patch.object(fallback.urllib.request, 'build_opener') as opener:
            self.assertEqual(fallback.snapshot(), {'state': 'unconfigured'})
            opener.assert_not_called()

    def test_reader_pins_exact_host_and_never_follows_redirect(self):
        os.environ['OFFICE_FALLBACK_FRESHNESS_URL'] = 'https://other.ariaxhan.com/api/health'
        self.assertEqual(fallback.snapshot(), {'state': 'unconfigured'})
        os.environ['OFFICE_FALLBACK_FRESHNESS_URL'] = 'https://fallback-health.ariaxhan.com/api/health'
        with patch.object(fallback.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('x', 302, 'redirect', {}, None)
            self.assertEqual(fallback.snapshot()['state'], 'unreachable')
            self.assertIs(opener.call_args.args[0], fallback._NoRedirect)

    def test_projection_contains_no_access_secret(self):
        payload = {'service': 'office-fallback-freshness', 'phase': 'prototype',
                   'ok': True, 'snapshot_age_seconds': 10,
                   'report_age_seconds': 20, 'check_age_seconds': 30}
        with patch.object(fallback.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = Response(json.dumps(payload).encode())
            self.assertEqual(fallback.snapshot(), {'state': 'fresh',
                             'snapshot_age_seconds': 10, 'report_age_seconds': 20,
                             'check_age_seconds': 30})
            self.assertEqual(opener.return_value.open.call_args.args[0].headers['Cf-access-client-secret'], 'read-secret')

    def test_stale_503_and_bad_access_fail_closed(self):
        payload = {'service': 'office-fallback-freshness', 'phase': 'prototype',
                   'ok': False, 'snapshot_age_seconds': 700,
                   'report_age_seconds': 5, 'check_age_seconds': 30}
        error = HTTPError('https://fallback-health.ariaxhan.com/api/health', 503, 'stale', {}, Response(json.dumps(payload).encode()))
        with patch.object(fallback.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            self.assertEqual(fallback.snapshot()['state'], 'stale')
        fallback._CACHED = (0.0, None)
        with patch.object(fallback.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('x', 403, 'denied', {}, None)
            self.assertEqual(fallback.snapshot()['state'], 'unreachable')

    def test_missing_or_very_old_age_is_stale(self):
        for age in (None, 100000):
            fallback._CACHED = (0.0, None)
            payload = {'service': 'office-fallback-freshness', 'ok': False,
                       'snapshot_age_seconds': age, 'report_age_seconds': 12,
                       'check_age_seconds': 3}
            error = HTTPError('x', 503, 'stale', {}, Response(json.dumps(payload).encode()))
            with patch.object(fallback.urllib.request, 'build_opener') as opener:
                opener.return_value.open.side_effect = error
                self.assertEqual(fallback.snapshot()['state'], 'stale')


if __name__ == '__main__':
    unittest.main()
