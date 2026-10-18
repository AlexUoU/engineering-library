import json
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from library import Library,ROOT
from server import make_handler


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.lib=Library(self.tmp.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.lib,'test-token'))
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,body=None,headers=None):
        h={'X-Library-Token':'test-token','Content-Type':'application/json','Origin':self.url}
        h.update(headers or {})
        r=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        with urlopen(r) as response:return response.status,json.load(response)

    def test_catalog_detail_and_unknown_version(self):
        _,value=self.request('/api/catalog');self.assertEqual(len(value['articles']),26)
        _,detail=self.request('/api/article?id=building-effective-agents');self.assertEqual(detail['version'],1)
        with self.assertRaises(HTTPError) as c:self.request('/api/article?id=building-effective-agents&v=999')
        self.assertEqual(c.exception.code,404)

    def test_write_requires_local_origin_and_token(self):
        value=dict(article='building-effective-agents',version=1,status='read')
        for h in [{'Origin':'https://untrusted.example'},{'X-Library-Token':'wrong'}]:
            with self.assertRaises(HTTPError) as c:self.request('/api/reading',value,h)
            self.assertEqual(c.exception.code,403)
        self.assertEqual(self.request('/api/reading',value)[0],200)

    def test_reflection_and_backup_over_http(self):
        row=dict(article='building-effective-agents',version=1,answers=['我的理解','',''])
        self.request('/api/reflect',row)
        _,backup=self.request('/api/export')
        self.assertEqual(backup['reflections'][0]['answers'][0],'我的理解')
        self.request('/api/import',backup)
        self.assertEqual(len(self.request('/api/history')[1]),1)

    def test_draft_over_http(self):
        row=dict(article='building-effective-agents',version=1,answers=['尚未完成','',''])
        self.request('/api/draft',row)
        self.assertEqual(self.request('/api/draft?id=building-effective-agents&v=1')[1][0],'尚未完成')

    def test_static_assets_and_no_external_dependencies(self):
        with urlopen(self.url+'/') as r:
            body=r.read().decode();self.assertEqual(r.status,200)
            self.assertIn("default-src 'self'",r.headers['Content-Security-Policy'])
            self.assertNotIn('https://',body)
        for name in ['app.js','style.css']:
            with urlopen(self.url+'/'+name) as r:self.assertEqual(r.status,200)


if __name__=='__main__':unittest.main()

class ReaderModeTests(unittest.TestCase):
    setUp=ServerTests.setUp
    tearDown=ServerTests.tearDown
    request=ServerTests.request
    def test_reader_package_cannot_start_preparation(self):
        self.lib.manifest['reader_mode']=True
        for path in ['/api/check','/api/cache-original','/api/package']:
            with self.assertRaises(HTTPError) as caught:self.request(path,{'article':'building-effective-agents'})
            self.assertEqual(caught.exception.code,409)
        self.assertEqual(self.request('/api/reading',dict(article='building-effective-agents',version=1,status='read'))[0],200)
