"""Real HTTP/WebSocket coverage of the authenticated Screen Sharing bridge."""
import base64
import http.client
import json
import socket
import struct
import sys
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import desktops
import serve


def frame(payload, fin=True, opcode=2):
    size = len(payload)
    length = bytes([size | 128]) if size < 126 else bytes([126 | 128]) + struct.pack('>H', size)
    if size >= 65536:
        length = bytes([127 | 128]) + struct.pack('>Q', size)
    mask = b'test'
    return bytes([opcode | (128 if fin else 0)]) + length + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload))


class DesktopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = serve.make_server(serve.World(), 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def setUp(self):
        self.logs = patch.object(serve, 'log', lambda _: None); self.logs.start()

    def tearDown(self):
        self.logs.stop()

    def get(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse(); body = response.read()
        result = response.status, dict(response.getheaders()), body
        connection.close(); return result

    def headers(self, origin=None, host=None):
        host = host or f'127.0.0.1:{self.port}'
        return {'Host': host, 'Origin': origin or f'http://{host}',
                'Upgrade': 'websocket', 'Connection': 'Upgrade',
                'Sec-WebSocket-Version': '13',
                'Sec-WebSocket-Key': base64.b64encode(b'0123456789abcdef').decode()}

    def test_pages_and_scoped_csp(self):
        code, headers, body = self.get('/desktops')
        self.assertEqual(code, 200); self.assertIn(b'Mac desktops', body)
        self.assertIn(f'ws://127.0.0.1:{self.port}', headers['content-security-policy'])
        self.assertIn("img-src 'self' data:", headers['content-security-policy'])
        self.assertIn("'unsafe-inline'", headers['content-security-policy'])
        self.assertNotIn("'unsafe-inline'", self.get('/')[1]['content-security-policy'])
        for path in ('/desktops.css', '/desktops-bundle.js'):
            self.assertEqual(self.get(path)[0], 200)

    def test_split_http_body_still_reaches_existing_json_api(self):
        body = json.dumps({'kind': 'network', 'path': '/api/watch',
                           'request_id': 'desktop-split-test-01', 'status': 0,
                           'message': 'network unavailable'}).encode()
        request = (f'POST /api/client-errors HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                   f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode()
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as client:
            client.sendall(request + body[:1])
            time.sleep(.05)
            client.sendall(body[1:])
            response = http.client.HTTPResponse(client); response.begin()
            self.assertEqual(response.status, 202, response.read())

    def test_split_signed_webhook_body_is_verified_in_full(self):
        body = b'{"zen": "TCP packets are not request bodies"}'
        secret = b'desktop-test-only-webhook-key'
        signature = serve.webhook.sign(secret, body)
        request = (f'POST /webhook HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                   f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n'
                   f'X-Hub-Signature-256: {signature}\r\nX-GitHub-Event: ping\r\n'
                   'X-GitHub-Delivery: desktop-split-webhook-test\r\n\r\n').encode()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(serve.webhook, 'SECRET', secret):
            mailbox = serve.webhook.Mailbox(Path(directory))
            with patch.object(self.server.RequestHandlerClass, 'mailbox', mailbox):
                with socket.create_connection(('127.0.0.1', self.port), timeout=5) as client:
                    client.sendall(request + body[:1]); time.sleep(.05); client.sendall(body[1:])
                    response = http.client.HTTPResponse(client); response.begin()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(json.loads(response.read())['pong'])
                self.assertEqual(mailbox.count(), 1)

    def test_wrong_host_and_identity_refused_before_desktop_access(self):
        with patch.object(desktops, 'targets', side_effect=AssertionError('must not discover')):
            for path in ('/desktops', '/api/desktops', '/api/desktop/local/socket'):
                self.assertEqual(self.get(path, {'Host': 'evil.test'})[0], 403)
            with patch.object(serve, 'TRUSTED_HOSTS', {'office.test'}), patch.object(serve, 'LOGIN', 'owner@test'):
                self.assertEqual(self.get('/api/desktop/local/socket', self.headers(host='office.test'))[0], 403)

    def test_cross_origin_and_malformed_upgrade_cannot_connect(self):
        with patch.object(desktops, 'targets', side_effect=AssertionError('must not discover')):
            bad = [self.headers(origin='https://evil.test'), self.headers(origin=f'http://127.0.0.1:{self.port}/extra'),
                   self.headers(origin=f'https://127.0.0.1:{self.port}')]
            missing = self.headers(); del missing['Origin']; bad.append(missing)
            key = self.headers(); key['Sec-WebSocket-Key'] = 'bad'; bad.append(key)
            connection = self.headers(); connection['Connection'] = 'close'; bad.append(connection)
            site = self.headers(); site['Sec-Fetch-Site'] = 'cross-site'; bad.append(site)
            for headers in bad:
                self.assertEqual(self.get('/api/desktop/local/socket', headers)[0], 403)

    def test_client_cannot_choose_a_target_address(self):
        with patch.object(desktops, 'targets', return_value={}):
            code, _, body = self.get('/api/desktop/mac-123/socket?host=192.168.1.1&port=22', self.headers())
            self.assertEqual(code, 404); self.assertEqual(json.loads(body)['error'], 'Unknown Mac')

    def test_discovery_excludes_other_users_and_shared_nodes(self):
        data = {'Self': {'UserID': 9}, 'Peer': {
            'mine': {'OS': 'macOS', 'UserID': 9, 'NodeID': 12, 'HostName': 'MacBook', 'Online': True, 'TailscaleIPs': ['100.64.0.2']},
            'other': {'OS': 'macOS', 'UserID': 8, 'NodeID': 13, 'TailscaleIPs': ['100.64.0.3']},
            'shared': {'OS': 'macOS', 'UserID': 9, 'NodeID': 14, 'ShareeNode': True, 'TailscaleIPs': ['100.64.0.4']},
            'invalid': {'OS': 'macOS', 'UserID': 9, 'NodeID': 15, 'TailscaleIPs': ['127.0.0.2']},
        }}
        with patch.object(desktops, '_command', return_value=json.dumps(data)), \
                patch.object(desktops, '_local_name', return_value='Studio'), \
                patch.object(desktops.shutil, 'which', return_value='/tailscale'):
            rows = desktops._discover()
        self.assertEqual(set(rows), {'local', 'mac-12'})
        self.assertEqual(rows['mac-12']['host'], '100.64.0.2')

    def test_unavailable_machine_is_not_presented_as_connected(self):
        with patch.object(desktops.socket, 'create_connection', side_effect=ConnectionRefusedError):
            data = desktops._probe({'id': 'local', 'name': 'Studio', 'host': '127.0.0.1', 'local': True})
        self.assertEqual(data['state'], 'unavailable'); self.assertNotIn('host', data)

    def test_session_limit_refuses_before_connecting(self):
        guard = threading.BoundedSemaphore(0)
        with patch.object(desktops, 'targets', return_value={'local': {}}), patch.object(desktops, 'SESSIONS', guard):
            self.assertEqual(self.get('/api/desktop/local/socket', self.headers())[0], 429)

    def bridge(self, payload):
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen()
        got = []; finished = threading.Event()
        def remote():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(5); connection.sendall(b'RFB 003.008\n')
                    data = connection.recv(64); got.append(data); connection.sendall(data)
            finally:
                listener.close(); finished.set()
        threading.Thread(target=remote, daemon=True).start()
        create = socket.create_connection
        def redirected(address, *args, **kwargs):
            if address == ('127.0.0.1', 5900):
                address = listener.getsockname()
            return create(address, *args, **kwargs)
        request = '\r\n'.join(['GET /api/desktop/local/socket HTTP/1.1',
                               *(f'{key}: {value}' for key, value in self.headers().items()), '', '']).encode()
        with patch.object(desktops, 'targets', return_value={'local': {'host': '127.0.0.1'}}), \
                patch.object(desktops.socket, 'create_connection', redirected):
            with create(('127.0.0.1', self.port), timeout=5) as client:
                client.sendall(request + payload)
                received = b''
                while not finished.is_set() or b'RFB 003.008\n' not in received:
                    chunk = client.recv(4096)
                    if not chunk: break
                    received += chunk
                self.assertIn(b'101 Switching Protocols', received)
                self.assertIn(b'RFB 003.008\n', received)
            self.assertTrue(finished.wait(5))
        return got

    def test_first_frame_coalesced_with_upgrade_is_not_lost(self):
        self.assertEqual(self.bridge(frame(b'hello')), [b'hello'])

    def test_fragmented_websocket_message_reaches_mac_once(self):
        self.assertEqual(self.bridge(frame(b'hel', fin=False) + frame(b'lo', opcode=0)), [b'hello'])

    def test_oversized_raw_frame_and_fragmented_message_are_bounded(self):
        websocket = desktops.BoundedWebSocket()
        websocket._recv_buffer = b'x' * (desktops.LIMIT + 1)
        with self.assertRaisesRegex(ValueError, 'too large'):
            websocket._recv()
        websocket._recv_buffer = b''
        websocket._partial_msg = b'x' * desktops.LIMIT
        websocket._recv_queue = [{'payload': b'y'}]
        with self.assertRaisesRegex(ValueError, 'too large'):
            websocket._recvmsg()


if __name__ == '__main__':
    unittest.main()
