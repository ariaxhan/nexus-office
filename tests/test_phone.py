"""The current mobile page is served only through the authenticated Office door."""
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest

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
        cls.httpd=serve.make_server(serve.World(),0)
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


if __name__=='__main__':unittest.main()
