"""The current mobile page is served only through the authenticated Office door."""
import http.client
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'client'))


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import serve
        cls.serve=serve
        cls.log_was=serve.log;serve.log=lambda msg:None
        cls.was=(serve.office_sync.Access,serve.office_sync.build_snapshot)
        serve.office_sync.Access=lambda:object()
        serve.office_sync.build_snapshot=lambda access:{'generated':'','stations':[]}
        cls.world=serve.World();cls.httpd=serve.make_server(cls.world,0)
        cls.port=cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close()
        cls.serve.office_sync.Access,cls.serve.office_sync.build_snapshot=cls.was
        cls.serve.log=cls.log_was

    def fetch(self,path,host=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        conn.request('GET',path,headers={'Host':host or f'127.0.0.1:{self.port}'})
        response=conn.getresponse();body=response.read().decode('utf-8','replace')
        result=(response.status,dict(response.getheaders()),body)
        conn.close();return result

    def post(self,path,body,request_id='client-diagnostic-0001'):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={'Host':f'127.0.0.1:{self.port}','Content-Type':'application/json',
                 'X-Office-Request-ID':request_id}
        conn.request('POST',path,body=json.dumps(body),headers=headers)
        response=conn.getresponse();raw=response.read().decode('utf-8','replace')
        result=(response.status,dict(response.getheaders()),raw)
        conn.close();return result

    def test_current_page_and_bundle_are_served(self):
        code,headers,body=self.fetch('/')
        self.assertEqual(code,200)
        self.assertIn('<title>Nexus Office</title>',body)
        self.assertIn("default-src 'none'",headers['content-security-policy'])
        for path in ('/office.js','/office-bundle.js','/office.css'):
            self.assertEqual(self.fetch(path)[0],200,path)

    def test_classic_human_gate_page_is_removed(self):
        for path in ('/classic','/phone.js','/phone.css'):
            code,_,body=self.fetch(path)
            self.assertEqual(code,404,path)
            self.assertEqual(json.loads(body)['error'],self.serve.NO_PAGE)

    def test_wrong_host_cannot_read_page(self):
        code,_,body=self.fetch('/',host='evil.example.com')
        self.assertEqual(code,403)
        self.assertEqual(json.loads(body)['error'],'wrong host')

    def test_watch_projection_is_small_and_conditionally_unchanged(self):
        self.world.snapshot = {
            'automation': {'tower': {'state': 'ok', 'working': 1}},
            'stations': [{'repo': 'acme/thing', 'large_unused': 'x' * 100000,
                          'issues': [{'number': 7, 'title': 'Choose',
                                      'decision': {'question': 'Which?', 'options': []}}]}],
        }
        coordinators = {'coordinators': [{'id': 'tbs', 'health': 'ok'}], 'as_of': 'now'}
        asks = {'items': [], 'at': 'now'}
        with patch.object(self.serve.office_api.coordinator_chat, 'overview', return_value=coordinators), \
             patch.object(self.serve.office_api.human_asks, 'listing', return_value=asks):
            code, _, raw = self.fetch('/api/watch')
            self.assertEqual(code, 200)
            body = json.loads(raw)
            self.assertNotIn('large_unused', raw)
            self.assertLess(len(raw), 10000)
            code, _, raw = self.fetch('/api/watch?since='+body['revision'])
            self.assertEqual(code, 200)
            unchanged = json.loads(raw)
            self.assertTrue(unchanged['not_modified'])
            self.assertNotIn('data', unchanged)

    def test_shell_starts_without_waiting_and_uses_cache_first(self):
        register = (ROOT/'client/phone/office-register-sw.js').read_text()
        worker = (ROOT/'client/phone/office-sw.js').read_text()
        app = (ROOT/'client/phone/office.js').read_text()
        self.assertLess(register.index('startOffice()'), register.index('serviceWorker.register'))
        fetch_handler = worker[worker.index("self.addEventListener('fetch'"):]
        self.assertLess(fetch_handler.index('caches.match'), fetch_handler.index('fetch(event.request'))
        self.assertNotIn('/api/', worker)
        self.assertIn('current=state;thread.dataset.signature', app)
        self.assertIn('?since_revision=${current.revision}', app)
        self.assertIn('if(next.not_modified){current=merged;updateControls(merged);return;}', app)

    def test_request_diagnostics_retain_context_and_original_exception(self):
        logs=[]
        with patch.object(self.serve,'log',logs.append), \
             patch.object(self.serve.office_api,'get',side_effect=RuntimeError('diagnostic boom')):
            code,headers,body=self.fetch('/api/watch?secret=not-logged')
        self.assertEqual(code,500)
        request_id=headers['x-office-request-id']
        row=next(json.loads(line) for line in logs
                 if '"event":"request"' in line and '"path":"/api/watch"' in line)
        self.assertEqual((row['method'],row['path'],row['request_id'],row['status']),
                         ('GET','/api/watch',request_id,500))
        self.assertEqual((row['outcome'],row['exception'],row['detail']),
                         ('error','RuntimeError','diagnostic boom'))
        self.assertGreaterEqual(row['duration_ms'],0)
        self.assertNotIn('secret',json.dumps(row))
        self.assertIn('RuntimeError: diagnostic boom',json.loads(body)['error'])
        time.sleep(.02)
        self.assertFalse(any(json.loads(line).get('status') == 0 for line in logs
                             if '"event":"request"' in line))

    def test_client_invalid_json_message_is_bounded_and_logged_without_payload(self):
        logs=[]
        report={'kind':'invalid_json','path':'/api/ask','request_id':'source-request-0001',
                'status':200,'message':'incorrect string'}
        with patch.object(self.serve,'log',logs.append):
            code,headers,body=self.post('/api/client-errors',report)
        self.assertEqual(code,202)
        self.assertEqual(json.loads(body),{'recorded':True})
        event=next(json.loads(line) for line in logs if '"event":"client_error"' in line)
        self.assertEqual(event,{'event':'client_error',**report})
        self.assertEqual(headers['x-office-request-id'],'client-diagnostic-0001')

    def test_malformed_json_gets_a_scoped_400_diagnostic(self):
        logs=[];conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={'Host':f'127.0.0.1:{self.port}','Content-Type':'application/json',
                 'X-Office-Request-ID':'malformed-json-0001'}
        with patch.object(self.serve,'log',logs.append):
            conn.request('POST','/api/client-errors',body='{"kind":',headers=headers)
            response=conn.getresponse();body=response.read().decode();conn.close()
        self.assertEqual(response.status,400)
        self.assertIn('Expecting',json.loads(body)['error'])
        row=next(json.loads(line) for line in logs
                 if '"event":"request"' in line and '"request_id":"malformed-json-0001"' in line)
        self.assertEqual((row['request_id'],row['status'],row['exception']),
                         ('malformed-json-0001',400,'JSONDecodeError'))

    def test_client_disconnect_is_one_diagnostic_not_a_traceback(self):
        logs=[]
        def delayed(handler,path,query):
            time.sleep(.05)
            handler._send(200,b'x'*(1024*1024),'application/json')
            return True
        with patch.object(self.serve,'log',logs.append),patch.object(self.serve.office_api,'get',delayed):
            client=socket.create_connection(('127.0.0.1',self.port),timeout=2)
            client.sendall((f'GET /api/disconnect HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                            'X-Office-Request-ID: disconnect-test-0001\r\n\r\n').encode())
            client.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0));client.close()
            deadline=time.time()+2
            while time.time()<deadline and not any('"outcome":"disconnect"' in line for line in logs):
                time.sleep(.01)
        row=next(json.loads(line) for line in logs if '"outcome":"disconnect"' in line)
        self.assertEqual((row['path'],row['request_id']),('/api/disconnect','disconnect-test-0001'))
        self.assertIn(row['exception'],('BrokenPipeError','ConnectionResetError'))
        self.assertFalse(any('Traceback' in line for line in logs))

    def test_browser_api_reports_network_and_invalid_json_without_response_payload(self):
        ui=(ROOT/'client/phone/office-ui.js').read_text()
        self.assertIn("fetch('/api/client-errors'",ui)
        self.assertIn("kind:'invalid_json'",ui)
        self.assertIn("kind:'network'",ui)
        self.assertIn('JSON.parse(await response.text())',ui)
        self.assertNotIn('response.json()',ui)


if __name__=='__main__':unittest.main()
