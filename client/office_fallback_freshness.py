"""Read only, bounded Cloudflare freshness projection for Office Watch.

The receiver is dormant until a protected HTTPS URL and a read-only Access
service token are configured. No Worker credential reaches the phone browser.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_LOCK = threading.Lock()
_CACHED = (0.0, None)
_TTL = 60.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Access credentials must never follow a redirect to another host.
        return None


def _configured():
    url = os.environ.get('OFFICE_FALLBACK_FRESHNESS_URL', '')
    client_id = os.environ.get('OFFICE_FALLBACK_CF_ACCESS_CLIENT_ID', '')
    secret = os.environ.get('OFFICE_FALLBACK_CF_ACCESS_CLIENT_SECRET', '')
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path != '/api/health' or
            parsed.port not in (None, 443) or host != 'fallback-health.ariaxhan.com' or
            not client_id or not secret):
        return None
    return url, client_id, secret


def _read(url, client_id, secret):
    req = urllib.request.Request(url, headers={
        'CF-Access-Client-Id': client_id,
        'CF-Access-Client-Secret': secret,
        'Accept': 'application/json',
    })
    try:
        response = urllib.request.build_opener(_NoRedirect).open(req, timeout=4)
    except urllib.error.HTTPError as error:
        if error.code != 503:
            return {'state': 'unreachable', 'detail': 'External check unavailable'}
        response = error
    try:
        with response:
            raw = response.read(2049)
            if len(raw) > 2048:
                raise ValueError('oversize')
            body = json.loads(raw)
    except (ValueError, OSError):
        return {'state': 'unreachable', 'detail': 'External check unreadable'}
    if not isinstance(body, dict) or body.get('service') != 'office-fallback-freshness':
        return {'state': 'unreachable', 'detail': 'External check identity mismatch'}
    ages = (body.get('snapshot_age_seconds'), body.get('report_age_seconds'), body.get('check_age_seconds'))
    if any(age is not None and (type(age) is not int or age < 0) for age in ages):
        return {'state': 'unreachable', 'detail': 'External check malformed'}
    healthy = body.get('ok') is True and response.status == 200 and all(age is not None for age in ages)
    return {'state': 'fresh' if healthy else 'stale',
            'snapshot_age_seconds': ages[0], 'report_age_seconds': ages[1],
            'check_age_seconds': ages[2]}


def snapshot():
    global _CACHED
    config = _configured()
    if not config:
        return {'state': 'unconfigured'}
    now = time.monotonic()
    with _LOCK:
        if now - _CACHED[0] < _TTL and _CACHED[1] is not None:
            return dict(_CACHED[1])
        try:
            result = _read(*config)
        except (OSError, TimeoutError):
            result = {'state': 'unreachable', 'detail': 'External check unreachable'}
        _CACHED = (now, result)
        return dict(result)
