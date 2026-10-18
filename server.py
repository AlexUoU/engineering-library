"""Loopback-only HTTP server; no external Python dependencies."""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import mimetypes
import re
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse, parse_qs
import webbrowser
from library import Library, ROOT, now


def make_handler(lib, token):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(lib.root / 'web'), **kwargs)

        def log_message(self, *args):
            pass

        def send_value(self, value, status=200):
            raw = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def end_headers(self):
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            super().end_headers()

        def valid_host(self):
            return self.headers.get('Host') in {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}

        def do_GET(self):
            if not self.valid_host():
                return self.send_value({'error':'无效的本地地址'}, 403)
            p = urlparse(self.path)
            try:
                if p.path == '/api/catalog':
                    return self.send_value(dict(lib.listing(), token=token))
                if p.path == '/api/article':
                    q = parse_qs(p.query)
                    return self.send_value(lib.detail(q['id'][0], int(q['v'][0]) if q.get('v') else None))
                if p.path.startswith('/original-assets/'):
                    name=p.path.rsplit('/',1)[-1]
                    if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp|gif|avif|svg)',name):
                        raise ValueError('无效资源路径')
                    raw=(lib.data/'original-assets'/name).read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type',mimetypes.guess_type(name)[0] or 'application/octet-stream')
                    self.send_header('Content-Length',str(len(raw)))
                    self.end_headers();self.wfile.write(raw)
                    return
                if p.path == '/api/history':
                    return self.send_value(lib.history(parse_qs(p.query).get('id',[None])[0]))
                if p.path == '/api/draft':
                    q = parse_qs(p.query)
                    with lib.db() as db:
                        r = db.execute('SELECT answers FROM drafts WHERE article=? AND version=?', (q['id'][0],int(q['v'][0]))).fetchone()
                    return self.send_value(json.loads(r['answers']) if r else [])
                if p.path == '/api/export':
                    return self.send_value(lib.export_learning())
                if p.path in ['/download/package','/download/handbook']:
                    target = lib.root / ('output/engineering-library-latest.zip' if p.path.endswith('package') else 'output/pdf/工程实践学习讲义.pdf')
                    if not target.exists():
                        return self.send_value({'error':'文件尚未生成'},404)
                    if p.path.endswith('package'):
                        import zipfile
                        with zipfile.ZipFile(target) as z:
                            complete=json.loads(z.read('engineering-library/data/manifest.json')).get('offline_ready')
                        if not complete:
                            return self.send_value({'error':'现有包尚未包含完整中英文正文，请先完成资料更新。'},409)
                    raw = target.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip' if p.path.endswith('package') else 'application/pdf')
                    self.send_header('Content-Disposition', 'attachment; filename="' + ('engineering-library.zip' if p.path.endswith('package') else 'handbook.pdf') + '"')
                    self.send_header('Content-Length',str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if p.path.startswith('/api/'):
                    return self.send_value({'error':'接口不存在'},404)
                if '..' in p.path or p.path.startswith('/.'):
                    return self.send_value({'error':'路径无效'},400)
                super().do_GET()
            except (KeyError, FileNotFoundError, ValueError):
                self.send_value({'error':'文章或版本不存在'},404)

        def do_POST(self):
            expected = 'http://' + self.headers.get('Host','')
            if not self.valid_host() or self.headers.get('X-Library-Token') != token or self.headers.get('Origin', expected) != expected:
                return self.send_value({'error':'请从本地学习页面操作'},403)
            try:
                size = int(self.headers.get('Content-Length','0'))
                if size > 8_000_000 or size <= 0:
                    raise ValueError('请求大小无效')
                value = json.loads(self.rfile.read(size))
                if self.path == '/api/shutdown':
                    self.send_value({'ok':True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if self.path == '/api/reflect':
                    return self.send_value(lib.add_reflection(value))
                if self.path == '/api/reading':
                    lib.mark_reading(value)
                    return self.send_value({'ok':True})
                if self.path == '/api/draft':
                    lib.draft(value)
                    return self.send_value({'ok':True})
                if self.path == '/api/import':
                    return self.send_value(lib.import_learning(value))
                if lib.manifest.get('reader_mode') and self.path in ['/api/check','/api/cache-original','/api/package']:
                    return self.send_value({'error':'这是预制离线阅读包，内容已在发布前准备；请使用制作端生成新版内容包。'},409)
                if self.path == '/api/check':
                    if not lib.updating:
                        threading.Thread(target=lib.check, daemon=True).start()
                    return self.send_value({'started':True})
                if self.path in ['/api/pause','/api/resume']:
                    return self.send_value(lib.control_updates(self.path == '/api/pause'))
                if self.path == '/api/cache-original':
                    return self.send_value(lib.cache_original(value['article']))
                if self.path == '/api/package':
                    lib.build_package(require_complete=True)
                    return self.send_value({'ok':True})
                self.send_value({'error':'接口不存在'},404)
            except (ValueError, KeyError, TypeError, FileNotFoundError) as e:
                self.send_value({'error':str(e)},400)
            except OSError as e:
                self.send_value({'error':'读取或保存失败，请检查网络和本地目录：'+str(e)},502)

    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--data',help='学习数据目录，默认与应用放在一起')
    p.add_argument('--no-open',action='store_true')
    p.add_argument('--runtime-file', help=argparse.SUPPRESS)
    p.add_argument('--no-check',action='store_true')
    a = p.parse_args()
    lib = Library(a.data)
    lib.automatic_checks = not a.no_check and not lib.manifest.get('reader_mode',False)
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(('127.0.0.1',a.port),make_handler(lib,token))
    if lib.automatic_checks:
        def scheduler():
            while True:
                lib.check()
                time.sleep(24*60*60)
        threading.Thread(target=scheduler,daemon=True).start()
    url = 'http://127.0.0.1:%d/' % server.server_port
    if a.runtime_file:
        runtime = Path(a.runtime_file)
        runtime.write_text(json.dumps({'url':url,'token':token}))
        runtime.chmod(0o600)
    print('学习库已启动：' + url + '\n关闭此窗口或按 Ctrl+C 停止。',flush=True)
    if not a.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
