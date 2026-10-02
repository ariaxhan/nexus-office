"""A same-origin bridge to Screen Sharing on this Mac and the user's own Macs."""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
import select
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import warnings
from concurrent.futures import ThreadPoolExecutor

with warnings.catch_warnings():
    warnings.filterwarnings('ignore', message="no 'numpy' module.*")
    from vendor.websocket import WebSocket, WebSocketWantReadError, WebSocketWantWriteError

LIMIT = 1024 * 1024
SESSIONS = threading.BoundedSemaphore(4)
CACHE_LOCK = threading.Lock()
CACHE = (0.0, {})
TAILSCALE = ('tailscale', '/Applications/Tailscale.app/Contents/MacOS/Tailscale',
             '/usr/local/bin/tailscale', '/opt/homebrew/bin/tailscale')


def _command(args):
    return subprocess.check_output(args, text=True, timeout=4).strip()


def _local_name():
    try:
        return _command(['/usr/sbin/scutil', '--get', 'ComputerName'])
    except (OSError, subprocess.SubprocessError):
        return socket.gethostname()


def _tail_ip(ips):
    for value in ips:
        address = ipaddress.ip_address(value)
        if address in ipaddress.ip_network('100.64.0.0/10'):
            return value
    return None


def _discover():
    targets = {'local': {'id': 'local', 'name': _local_name(), 'host': '127.0.0.1',
                         'local': True}}
    binary = next((shutil.which(name) for name in TAILSCALE if shutil.which(name)), None)
    if binary is None:
        return targets
    data = json.loads(_command([binary, 'status', '--json']))
    owner = data.get('Self', {}).get('UserID')
    if not owner:
        return targets
    for peer in data.get('Peer', {}).values():
        if peer.get('OS') != 'macOS' or peer.get('UserID') != owner or peer.get('ShareeNode'):
            continue
        host = _tail_ip(peer.get('TailscaleIPs', []))
        identifier = 'mac-' + str(peer.get('NodeID', ''))
        if host and re.fullmatch(r'mac-\d+', identifier):
            targets[identifier] = {'id': identifier, 'name': peer.get('HostName') or 'Mac',
                                   'host': host, 'local': False,
                                   'online': bool(peer.get('Online'))}
    return targets


def targets():
    global CACHE
    with CACHE_LOCK:
        at, rows = CACHE
        if time.monotonic() - at < 30 and rows:
            return rows
        try:
            rows = _discover()
        except (OSError, subprocess.SubprocessError, ValueError):
            # Local Screen Sharing remains available when Tailscale cannot answer.
            rows = {'local': {'id': 'local', 'name': _local_name(),
                              'host': '127.0.0.1', 'local': True}}
        CACHE = (time.monotonic(), rows)
        return rows


def _probe(row):
    public = {key: row[key] for key in ('id', 'name', 'local')}
    if row.get('online') is False:
        return {**public, 'state': 'offline', 'detail': 'This Mac is offline.'}
    try:
        with socket.create_connection((row['host'], 5900), timeout=2) as remote:
            remote.settimeout(2)
            banner = remote.recv(12)
        if not banner.startswith(b'RFB '):
            raise OSError('Not a Screen Sharing server')
        return {**public, 'state': 'ready', 'detail': 'Screen Sharing is available.'}
    except OSError:
        return {**public, 'state': 'unavailable',
                'detail': 'Screen Sharing is unavailable. Enable it in System Settings → General → Sharing on this Mac.'}


def listing():
    rows = list(targets().values())
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {'items': list(pool.map(_probe, rows))}


def desktop_csp(host):
    # The Host has already passed Office's exact allowlist.
    return ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"connect-src 'self' ws://{host} wss://{host}; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


def _origin_ok(handler):
    headers = handler.headers
    origin = headers.get('Origin', '')
    parsed = urllib.parse.urlsplit(origin)
    host = headers.get('Host', '').lower()
    port = handler.server.server_address[1]
    scheme = 'http' if host in {f'127.0.0.1:{port}', f'localhost:{port}'} else 'https'
    return (len(headers.get_all('Origin', [])) == 1 and parsed.scheme == scheme
            and parsed.netloc.lower() == host and not parsed.path
            and not parsed.query and not parsed.fragment)


def _upgrade_error(handler):
    headers = handler.headers
    if not _origin_ok(handler):
        return 'Cross-origin desktop connection refused'
    if headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin':
        return 'Cross-site desktop connection refused'
    connection = {word.strip().lower() for word in headers.get('Connection', '').split(',')}
    if headers.get('Upgrade', '').lower() != 'websocket' or 'upgrade' not in connection:
        return 'A WebSocket connection is required'
    if headers.get('Sec-WebSocket-Version') != '13' or headers.get('Sec-WebSocket-Protocol'):
        return 'Unsupported WebSocket protocol'
    try:
        if len(base64.b64decode(headers.get('Sec-WebSocket-Key', ''), validate=True)) != 16:
            return 'Invalid WebSocket key'
    except (ValueError, binascii.Error):
        return 'Invalid WebSocket key'
    return None


class BoundedWebSocket(WebSocket):
    """Bound raw/fragmented input while keeping the upstream protocol unchanged."""

    def _check_input(self):
        size = len(self._recv_buffer) + len(self._partial_msg)
        size += sum(len(frame['payload']) for frame in self._recv_queue)
        if size > LIMIT:
            raise ValueError('Desktop message is too large')

    def _recv(self):
        self._check_input()
        result = super()._recv()
        self._check_input()
        return result

    def _recv_frames(self):
        result = super()._recv_frames()
        self._check_input()
        return result

    def _recvmsg(self):
        self._check_input()
        result = super()._recvmsg()
        self._check_input()
        return result


def _send_frame(websocket, payload):
    # Backpressure: don't read another remote chunk until this one is delivered.
    deadline = time.monotonic() + 10
    while True:
        try:
            websocket.sendmsg(payload)
            return
        except WebSocketWantWriteError:
            if time.monotonic() >= deadline:
                raise TimeoutError('Desktop client stopped reading')
            select.select([], [websocket], [], 1)


def _read_client(websocket, remote):
    while True:
        try:
            data = websocket.recvmsg()
        except (WebSocketWantReadError, WebSocketWantWriteError):
            return True
        if data is None:
            return False
        if data:
            remote.sendall(data)
        if not websocket.pending():
            return True


def _relay(websocket, remote):
    deadline = time.monotonic() + 8 * 3600
    while time.monotonic() < deadline:
        ready, _, _ = select.select([websocket, remote], [], [], 60)
        if websocket in ready and not _read_client(websocket, remote):
            return
        if remote in ready:
            chunk = remote.recv(64 * 1024)
            if not chunk:
                return
            _send_frame(websocket, chunk)


def connect(handler, identifier):
    reason = _upgrade_error(handler)
    if reason:
        return handler._json({'error': reason}, 403)
    row = targets().get(identifier)
    if row is None:
        return handler._json({'error': 'Unknown Mac'}, 404)
    if not SESSIONS.acquire(blocking=False):
        return handler._json({'error': 'Four desktops are already open. Close one and try again.'}, 429)
    upgraded = False
    try:
        with socket.create_connection((row['host'], 5900), timeout=3) as remote:
            remote.settimeout(10)
            websocket = BoundedWebSocket()
            handler.connection.settimeout(10)
            websocket.accept(handler.connection, handler.headers)
            handler.response_status = 101
            handler.close_connection = True
            upgraded = True
            _relay(websocket, remote)
    except (OSError, ValueError):
        if not upgraded:
            return handler._json({'error': 'Screen Sharing is unavailable on this Mac.'}, 503)
    finally:
        if upgraded:
            handler.close_connection = True
        SESSIONS.release()


def get(handler, path):
    if path == '/api/desktops':
        handler._json(listing())
        return True
    match = re.fullmatch(r'/api/desktop/(local|mac-\d+)/socket', path)
    if match:
        connect(handler, match[1])
        return True
    return False
