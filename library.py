"""Local-first Engineering reading library. Runtime: Python 3.9+, standard library only."""
import argparse
import copy
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError
import zipfile

ROOT = Path(__file__).resolve().parent
SOURCE = 'https://www.anthropic.com/engineering'
HOSTS = {'www.anthropic.com', 'anthropic.com', 'code.claude.com'}
TOPICS = ['Agent 基础与架构选择', '上下文工程与知识检索', '工具设计、MCP 与 Skills',
          '长任务执行与交接', '多 Agent 协作', '评测与实验可信度', '权限、安全与隔离', '生产运维与规模化', '待归类']


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def allowed(url):
    p = urlparse(url)
    return p.scheme == 'https' and p.hostname in HOSTS and not p.username and p.port in (None, 443)


class SafeRedirect(HTTPRedirectHandler):
    http_error_308 = HTTPRedirectHandler.http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError('来源跳转到了未支持的站点，保留本地版本')
        # Python 3.9 does not recognize 308 as a redirect in redirect_request.
        return super().redirect_request(req, fp, 301 if code == 308 else code, msg, headers, newurl)


def fetch(url):
    if not allowed(url):
        raise ValueError('仅支持已配置的官方来源')
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; LocalReadingLibrary/1.0)',
                               'Accept': 'text/html'})
    with build_opener(SafeRedirect()).open(req, timeout=25) as response:
        data = response.read(8_000_001)
        if len(data) > 8_000_000:
            raise ValueError('页面超出大小限制')
        if 'html' not in response.headers.get('Content-Type', ''):
            raise ValueError('来源不是 HTML 页面')
        return data.decode('utf-8', errors='replace'), response.url


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.stack, self.nodes, self.links, self.meta = [], [], [], {}
        self.resources = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if any(t in {'article','main'} for t,_ in self.stack) and not any(t in {'nav','footer','script','style'} for t,_ in self.stack):
            resource = {k:v for k,v in attrs if k in {'src','srcset','href','data-src','poster'} or (tag in {'svg','path','rect','circle','polygon','polyline','line'} and k in {'d','points','viewbox','x','y','width','height','r','cx','cy','x1','x2','y1','y2'})}
            if resource:
                self.resources.append((tag,resource))
        if tag == 'meta':
            self.meta[a.get('property', a.get('name', ''))] = a.get('content', '')
        if tag in {'meta', 'br', 'hr', 'img', 'input', 'link', 'source', 'wbr', 'area', 'embed'}:
            return
        self.stack.append((tag, a))
        if tag == 'a':
            self.links.append(a.get('href', ''))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, value):
        tags = [t for t, _ in self.stack]
        if any(t in {'script', 'style', 'nav', 'footer', 'noscript', 'button'} for t in tags):
            return
        if 'header' in tags and 'main' not in tags and 'article' not in tags:
            return
        value = re.sub(r'\s+', ' ', value).strip()
        if value:
            self.nodes.append((tuple(t for t, _ in self.stack), value))

    def snapshot(self, original_url, resolved_url):
        headings = [v for tags, v in self.nodes if 'h1' in tags]
        if not headings:
            raise ValueError('未找到文章标题，可能遇到验证页或页面结构变化')
        title = headings[0]
        # Only article/main text; ignore site chrome. Retain a digest, not full article text.
        body = [v for tags, v in self.nodes if ('article' in tags or 'main' in tags)]
        if not body:
            raise ValueError('未找到正文区域，停止更新以保护旧版本')
        joined = ' '.join(body)
        if title in joined:
            joined = joined[joined.index(title) + len(title):]
        for stop in ['Get the developer newsletter', 'Related content', 'Was this page helpful?']:
            joined = joined.split(stop)[0]
        if len(joined) < 300 or any(x in title.lower() for x in ['access denied', 'just a moment', 'not found']):
            raise ValueError('正文不完整，未创建新版本')
        published = self.meta.get('article:published_time') or self.meta.get('datePublished')
        modified = self.meta.get('article:modified_time') or self.meta.get('dateModified')
        if not published:
            m = re.search(r'Published\s+([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})', joined)
            if m:
                for fmt in ('%b %d, %Y', '%B %d, %Y'):
                    try:
                        published = datetime.strptime(m.group(1), fmt).date().isoformat()
                        break
                    except ValueError:
                        pass
        # No verbatim article excerpts are distributed. Metadata + full-body hash detect changes.
        material = title + '\n' + joined
        text_digest = hashlib.sha256(material.encode()).hexdigest()
        if self.resources:
            material += '\n' + json.dumps(self.resources, sort_keys=True)
        digest = hashlib.sha256(material.encode()).hexdigest()
        return dict(title=title, url=original_url, resolved_url=resolved_url,
                    published_at=published, source_modified_at=modified, digest=digest, text_digest=text_digest)


def discover(source):
    page = Page(source)
    urls = list(dict.fromkeys(urljoin(SOURCE, x).split('#')[0].rstrip('/') for x in page.links
                             if urlparse(urljoin(SOURCE, x)).path.startswith('/engineering/')))
    urls = [u for u in urls if allowed(u)]
    # A suspiciously short listing must never mark every known article removed.
    if not urls:
        raise ValueError('未解析到文章目录，保留本地内容')
    return urls


class Library:
    def __init__(self, data=None, root=ROOT):
        self.root = Path(root)
        self.data = Path(data) if data else self.root / 'data'
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.pause_condition = threading.Condition(self.lock)
        self.pause_requested = False
        self.updating = False
        self.progress = dict(stage='idle', completed=0, total=0, percent=0, message='')
        self.manifest_path = self.data / 'manifest.json'
        if not self.manifest_path.exists():
            seed = json.loads((self.root / 'content/seed.json').read_text(encoding='utf-8'))
            self.manifest = dict(schema=1, articles={}, last_attempt=None, last_success=None,
                                 updated_at=seed['created_at'], package_at=None, events=[])
            for lesson in seed['articles']:
                sid = lesson['id']
                payload = dict(lesson=lesson, source=lesson.get('source', {}), version=1,
                               saved_at=seed['created_at'], reason='初始中文学习版', editorial='ready')
                write_json(self.version_path(sid, 1), payload)
                self.manifest['articles'][sid] = dict(id=sid, latest=1, title=lesson['title'],
                    topic=lesson['topic'], url=lesson['url'], state='active', missing_checks=0,
                    deleted_checks=0, last_observed=seed['created_at'])
            self.save()
        else:
            self.manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        if self.manifest.get('last_progress'):
            self.progress = dict(self.manifest['last_progress'])
            if self.progress.get('stage') not in {'done','partial','error','idle'}:
                self.progress.update(stage='partial', message='上次更新被中断；重新检查会复用已完成的缓存。')
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS reflections
                (id TEXT PRIMARY KEY, article TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL,
                 answers TEXT NOT NULL, feeling TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reading
                (article TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts
                (article TEXT NOT NULL, version INTEGER NOT NULL, answers TEXT NOT NULL,
                 updated_at TEXT NOT NULL, PRIMARY KEY(article,version));''')

    def db(self):
        conn = sqlite3.connect(self.data / 'learning.sqlite3', timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def version_path(self, sid, version):
        if not re.fullmatch(r'[a-z0-9-]{1,100}', sid):
            raise ValueError('文章编号无效')
        return self.data / 'versions' / sid / ('v%d.json' % int(version))

    def save(self):
        write_json(self.manifest_path, self.manifest)

    def version(self, sid, version=None):
        article = self.manifest['articles'][sid]
        return json.loads(self.version_path(sid, version or article['latest']).read_text(encoding='utf-8'))

    def listing(self):
        with self.lock:
            rows = []
            for a in self.manifest['articles'].values():
                v = self.version(a['id'])
                rows.append(dict(a, summary=v['lesson']['summary'], saved_at=v['saved_at'],
                                 source_modified_at=v['source'].get('source_modified_at'), editorial=v['editorial'],
                                 search_text=' '.join([v['source'].get('title','')] +
                                     [s['body'] for s in v['lesson']['sections']])))
            with self.db() as db:
                reading = {r['article']: dict(r) for r in db.execute('SELECT * FROM reading')}
            return dict(reader_mode=bool(self.manifest.get('reader_mode')), content_as_of=self.manifest.get('content_as_of'), package=self.package_status(), articles=rows, topics=TOPICS, reading=reading, updating=self.updating, pause_requested=self.pause_requested, progress=dict(self.progress), cache_originals=True, offline=self.offline_status(), automatic_checks=getattr(self,'automatic_checks',True),
                        **{k: self.manifest.get(k) for k in ['last_attempt','last_success','updated_at','package_at','events']})

    def detail(self, sid, version=None):
        with self.lock:
            article = self.manifest['articles'][sid]
            result = self.version(sid, version)
            versions = []
            reader_history, seen = [], set()
            for n in range(article['latest'], 0, -1):
                v = self.version(sid, n)
                versions.append({k: v[k] for k in ['version','saved_at','reason','editorial']})
                source_digest = v['source'].get('digest', '')
                body_path = self.data/'originals'/sid/(source_digest+'.json') if re.fullmatch(r'[a-f0-9]{64}', source_digest) else None
                body = json.loads(body_path.read_text(encoding='utf-8')).get('html') if body_path and body_path.exists() else None
                signature = body or json.dumps(v['lesson'], ensure_ascii=False, sort_keys=True)
                if signature not in seen:
                    reader_history.append({k:v[k] for k in ['version','saved_at']})
                    seen.add(signature)
            digest=result['source'].get('digest','')
            original_path=self.data/'originals'/sid/(digest+'.json') if re.fullmatch(r'[a-f0-9]{64}',digest) else None
            original=json.loads(original_path.read_text(encoding='utf-8')) if original_path and original_path.exists() else None
            translated_path=self.data/'translations'/sid/(digest+'.json') if original_path else None
            translated=json.loads(translated_path.read_text(encoding='utf-8')) if translated_path and translated_path.exists() else None
            return dict(result, article=article, versions=versions, reader_history=reader_history, original=original, translation=translated)

    def package_status(self):
        path=self.root/'output/engineering-library-latest.zip'
        try:
            with zipfile.ZipFile(path) as archive:
                value=json.loads(archive.read('engineering-library/data/manifest.json'))
            return dict(ready=bool(value.get('offline_ready')), at=value.get('package_at'))
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):
            return dict(ready=False,at=None)

    def cache_status(self, sid, digest):
        result = dict(id=sid, original=False, translation=False, issues=[], warnings=[])
        values = {}
        for folder, key in [('originals','original'),('translations','translation')]:
            path = self.data/folder/sid/(digest+'.json')
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if value.get('source_digest') != digest or not re.sub(r'<[^>]+>', '', value.get('html','')).strip():
                    raise ValueError('正文或版本校验不通过')
                values[key] = value
                result[key] = True
            except (OSError, ValueError, TypeError):
                result['issues'].append('英文正文缺失或损坏' if key=='original' else '中文全文缺失或损坏')
        original = values.get('original', {})
        result['warnings'] = original.get('warnings', [])
        if any(w.startswith(('插图未缓存','矢量图未缓存')) for w in result['warnings']):
            result['issues'].append('插图下载未完成')
        for value in values.values():
            for asset in re.findall(r'src="(/original-assets/[^"/]+)"', value['html']):
                if not (self.data/asset.lstrip('/')).is_file():
                    result['issues'].append('本地插图文件缺失')
                    break
        expected = hashlib.sha256(original.get('html','').encode()).hexdigest()
        translated = values.get('translation', {})
        if translated.get('original_hash') and translated['original_hash'] != expected:
            result['translation'] = False
            result['issues'].append('中文全文需与修复后的原文重新同步')
        result['ready'] = not result['issues']
        return result

    def offline_status(self):
        rows=[]
        for a in self.manifest['articles'].values():
            digest=self.version(a['id'])['source'].get('digest','')
            row=self.cache_status(a['id'],digest)
            row['title']=a['title']
            row['last_result']=self.manifest.get('update_results',{}).get(a['id'])
            rows.append(row)
        missing=[r['id'] for r in rows if not r['ready']]
        pending=[dict(id=sid,**result) for sid,result in self.manifest.get('update_results',{}).items() if sid not in self.manifest['articles']]
        return dict(total=len(rows), ready=len(rows)-len(missing), missing=missing, articles=rows, pending=pending)

    def store_translation(self, sid, snap):
        from translation import translate_html
        digest=snap['digest']
        path=self.data/'translations'/sid/(digest+'.json')
        original=json.loads((self.data/'originals'/sid/(digest+'.json')).read_text(encoding='utf-8'))
        original_hash=hashlib.sha256(original['html'].encode()).hexdigest()
        if path.exists():
            try:
                previous=json.loads(path.read_text(encoding='utf-8'))
                if previous.get('html') and previous.get('source_digest')==digest and previous.get('original_hash')==original_hash:
                    return
            except (ValueError,OSError):
                pass
        translated=translate_html(original['html'],self.root)
        write_json(path,dict(translated,source_digest=digest,original_hash=original_hash,cached_at=now()))

    def store_original(self, sid, snap, raw):
        from originals import ReaderHTML, fetch_image, cache_svg
        digest=snap['digest']
        if not re.fullmatch(r'[a-f0-9]{64}',digest):
            raise ValueError('来源校验值无效')
        path=self.data/'originals'/sid/(digest+'.json')
        if path.exists():
            status=self.cache_status(sid,digest)
            if status['original'] and json.loads(path.read_text(encoding='utf-8')).get('reader_schema',0)>=2 and (json.loads(path.read_text(encoding='utf-8')).get('reader_schema',0)>=3 or not status['warnings']) and not any('插图' in x or '矢量图' in x for x in status['issues']):
                return
        resources={}
        if path.exists():
            try:
                resources=json.loads(path.read_text(encoding='utf-8')).get('resources',{})
            except (ValueError,OSError):
                pass
        def cache_image(url):
            local=resources.get(url,'')
            if re.fullmatch(r'/original-assets/[a-f0-9]+\.[a-z]+',local) and (self.data/local.lstrip('/')).is_file():
                return local
            local=fetch_image(url,self.data/'original-assets')
            resources[url]=local
            return local
        reader=ReaderHTML(raw,snap['resolved_url'],cache_image,lambda data:cache_svg(data,self.data/'original-assets'))
        content=''.join(reader.output)
        if len(re.sub(r'<[^>]+>','',content).strip())<200:
            raise ValueError('原文正文提取不完整，保留现有版本')
        write_json(path,dict(html=content,cached_at=now(),warnings=reader.warnings,resources=resources,reader_schema=3,source_digest=digest))

    def cache_original(self, sid, fetcher=fetch):
        with self.lock:
            if self.updating:
                raise ValueError('正在更新资料，请稍后缓存原文')
            article=dict(self.manifest['articles'][sid])
        raw,resolved=fetcher(article['url'])
        snap=Page(raw).snapshot(article['url'],resolved)
        # Cache before moving a version pointer, so failure leaves the old version usable.
        self.store_original(sid,snap,raw)
        self.store_translation(sid,snap)
        with self.lock:
            self.apply_snapshot(sid,snap)
            self.manifest['cache_originals']=True
            self.save()
        if not self.offline_status()['missing']:
            self.build_package(require_complete=True)
        return dict(ok=True,version=self.manifest['articles'][sid]['latest'],source_digest=snap['digest'])

    def history(self, sid=None):
        with self.db() as db:
            rows = db.execute('SELECT * FROM reflections' + (' WHERE article=?' if sid else '') +
                              ' ORDER BY created_at DESC, id DESC', (sid,) if sid else ()).fetchall()
        return [dict(dict(r), answers=json.loads(r['answers'])) for r in rows]

    def add_reflection(self, value):
        import uuid
        sid, version = value['article'], int(value['version'])
        v = self.version(sid, version)
        answers = value.get('answers')
        if not isinstance(answers, list) or not v['lesson'].get('questions') or len(answers) != len(v['lesson']['questions']):
            raise ValueError('回答与文章版本不匹配')
        if not all(isinstance(a, str) and len(a) <= 20000 for a in answers) or not any(a.strip() for a in answers):
            raise ValueError('请至少写下一点想法再保存')
        feeling = value.get('feeling', '')
        if feeling not in ['', '我能解释了', '还想再想想', '以后再回顾']:
            raise ValueError('回顾标记无效')
        row = dict(id=str(uuid.uuid4()), article=sid, version=version, created_at=now(), answers=answers, feeling=feeling)
        with self.db() as db:
            db.execute('INSERT INTO reflections VALUES (?,?,?,?,?,?)',
                       (row['id'], sid, version, row['created_at'], json.dumps(answers, ensure_ascii=False), feeling))
            db.execute('DELETE FROM drafts WHERE article=? AND version=?', (sid, version))
        return row

    def export_learning(self):
        with self.db() as db:
            reading = [dict(r) for r in db.execute('SELECT * FROM reading')]
            drafts = [dict(dict(r), answers=json.loads(r['answers'])) for r in db.execute('SELECT * FROM drafts')]
        return dict(schema=1, exported_at=now(), reflections=self.history(), reading=reading, drafts=drafts)

    def import_learning(self, value):
        # Validate the complete backup before any writes; import is additive and idempotent.
        if not isinstance(value, dict) or value.get('schema') != 1:
            raise ValueError('备份格式不受支持')
        rows, reading, drafts = value.get('reflections'), value.get('reading'), value.get('drafts', [])
        if not all(isinstance(x, list) for x in (rows, reading, drafts)):
            raise ValueError('备份内容不完整')
        for r in rows + reading + drafts:
            v = self.version(r['article'], int(r['version']))
            if not isinstance(r.get('updated_at', r.get('created_at')), str):
                raise ValueError('记录时间无效')
            if 'answers' in r and (not isinstance(r['answers'], list) or
                not all(isinstance(a, str) and len(a) <= 20000 for a in r['answers']) or
                len(r['answers']) != len(v['lesson'].get('questions', []))):
                raise ValueError('回答与本地题目版本不匹配，请先恢复对应的完整资料包')
        for r in rows:
            if not isinstance(r.get('id'), str) or r.get('feeling','') not in ['', '我能解释了', '还想再想想', '以后再回顾']:
                raise ValueError('回想记录无效')
        for r in reading:
            if r.get('status') not in ['reading', 'read']:
                raise ValueError('阅读标记无效')
        backup = self.data / 'backups' / ('before-import-' + str(time.time_ns()) + '.json')
        write_json(backup, self.export_learning())
        with self.db() as db:
            for r in rows:
                db.execute('INSERT OR IGNORE INTO reflections VALUES (?,?,?,?,?,?)',
                    (r['id'], r['article'], int(r['version']), r['created_at'], json.dumps(r['answers'], ensure_ascii=False), r.get('feeling','')))
            for r in reading:
                db.execute('INSERT INTO reading VALUES (?,?,?,?) ON CONFLICT(article) DO UPDATE SET '
                           'version=excluded.version, updated_at=excluded.updated_at, status=excluded.status '
                           'WHERE excluded.updated_at > reading.updated_at',
                           (r['article'], r['version'], r['updated_at'], r['status']))
            for r in drafts:
                db.execute('INSERT INTO drafts VALUES (?,?,?,?) ON CONFLICT(article,version) DO UPDATE SET '
                           'answers=excluded.answers, updated_at=excluded.updated_at WHERE excluded.updated_at > drafts.updated_at',
                           (r['article'], r['version'], json.dumps(r['answers'], ensure_ascii=False), r['updated_at']))
        return dict(imported=len(rows), backup=backup.name)

    def mark_reading(self, value):
        self.version(value['article'], value['version'])
        status = value.get('status', 'reading')
        if status not in ['reading','read']:
            raise ValueError('阅读状态无效')
        with self.db() as db:
            # Opening a completed article again must not reset its read marker.
            db.execute('INSERT INTO reading VALUES (?,?,?,?) ON CONFLICT(article) DO UPDATE SET '
                       'version=excluded.version, updated_at=excluded.updated_at, '
                       'status=CASE WHEN reading.status="read" AND reading.version=excluded.version '
                       'THEN "read" ELSE excluded.status END',
                       (value['article'], value['version'], now(), status))

    def draft(self, value):
        v = self.version(value['article'], value['version'])
        answers = value['answers']
        if not isinstance(answers, list) or len(answers) != len(v['lesson'].get('questions', [])) or not all(isinstance(a,str) and len(a)<=20000 for a in answers):
            raise ValueError('草稿格式无效')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?,?)',
                       (value['article'], value['version'], json.dumps(answers, ensure_ascii=False), now()))

    def event(self, sid, kind, message):
        self.manifest['events'].insert(0, dict(article=sid, kind=kind, message=message, at=now()))
        self.manifest['events'] = self.manifest['events'][:500]

    def apply_snapshot(self, sid, snap):
        a = self.manifest['articles'].get(sid)
        if a:
            old = self.version(sid)
            baseline = old
            if not old['source'].get('text_digest') and old['version'] > 1:
                previous = self.version(sid,old['version']-1)
                if previous['source'].get('digest') == snap.get('text_digest'):
                    baseline = previous
            upgrading = not old['source'].get('text_digest') and baseline['source'].get('digest') == snap.get('text_digest')
            if not upgrading and old['source'].get('digest') == snap['digest'] and old['source'].get('source_modified_at') == snap.get('source_modified_at') and old['source'].get('resolved_url') == snap.get('resolved_url'):
                return False
            if not old['source'].get('digest'):
                # Establish baseline without pretending that the original lesson was re-authored.
                reason = '建立来源校验基线；中文导读沿用 v1'
            else:
                reason = '来源正文或元信息发生变化；中文导读待复核'
            lesson = copy.deepcopy(baseline['lesson'] if upgrading else old['lesson'])
            if upgrading:
                reason = '补充资源版本校验；沿用原有导读和回想'
            else:
                lesson['questions'] = []  # prior exercises remain in their old version
            version = a['latest'] + 1
        else:
            lesson = dict(id=sid, title=snap['title'], topic=8, url=snap['url'], summary='新收录的来源文章。中文导读尚未编写，可查看来源信息；当前没有回想练习。',
                          sections=[], questions=[])
            version, reason = 1, '新增来源文章；中文导读待编写'
            a = dict(id=sid, latest=0, title=snap['title'], topic=8, url=snap['url'], state='active', missing_checks=0, deleted_checks=0)
        payload = dict(lesson=lesson, source=snap, version=version, saved_at=now(), reason=reason, editorial='pending')
        write_json(self.version_path(sid, version), payload)
        a.update(latest=version, title=snap['title'], last_observed=now())
        self.manifest['articles'][sid] = a
        self.manifest['updated_at'] = now()
        self.event(sid, 'version', reason)
        return True

    def control_updates(self, paused):
        with self.pause_condition:
            if not self.updating:
                raise ValueError('当前没有正在执行的更新')
            self.pause_requested = paused
            self.pause_condition.notify_all()
            return dict(ok=True, pause_requested=paused)

    def update_checkpoint(self):
        with self.pause_condition:
            if not self.pause_requested:
                return
            stage, message = self.progress['stage'], self.progress['message']
            self.progress.update(stage='paused', message='更新已暂停，点击恢复后从当前位置继续。')
            self.pause_condition.notify_all()
            while self.pause_requested:
                self.pause_condition.wait()
            self.progress.update(stage=stage, message=message)

    def check(self, fetcher=fetch):
        with self.lock:
            if self.updating:
                return False
            self.updating = True
            self.pause_requested = False
            self.progress = dict(stage='discovering', completed=0, failed=0, total=0, percent=0, message='正在读取文章目录…')
            self.manifest['last_attempt'] = now()
            self.save()
        try:
            self.update_checkpoint()
            listing, _ = fetcher(SOURCE)
            urls = discover(listing)
            known = list(self.manifest['articles'].items())
            if len(known) >= 10 and len(urls) < len(known) // 2:
                raise ValueError('来源目录数量异常减少，本轮停止，未修改文章状态')
            url_to_id = {a['url']: sid for sid, a in known}
            targets = list(dict.fromkeys(urls + list(url_to_id)))
            with self.lock:
                self.progress.update(stage='checking', total=len(targets))
            errors, changed = [], False
            for url in targets:
                self.update_checkpoint()
                sid = url_to_id.get(url) or (urlparse(url).path.rstrip('/').split('/')[-1].lower())
                with self.lock:
                    self.progress['message'] = '正在核对：' + self.manifest['articles'].get(sid, {}).get('title', sid)
                if not re.fullmatch(r'[a-z0-9-]{1,100}', sid):
                    continue
                in_listing = url in urls
                try:
                    source, resolved = fetcher(url)
                    snap = Page(source).snapshot(url, resolved)
                    self.update_checkpoint()
                    with self.lock:
                        self.progress['message']='正在缓存原文与插图：'+sid
                    self.store_original(sid,snap,source)
                    self.update_checkpoint()
                    with self.lock:
                        self.progress['message']='正在本地翻译：'+sid
                    self.store_translation(sid,snap)
                    status=self.cache_status(sid,snap['digest'])
                    if not status['ready']:
                        raise ValueError('；'.join(status['issues']))
                    self.update_checkpoint()
                    with self.lock:
                        changed = self.apply_snapshot(sid, snap) or changed
                        a = self.manifest['articles'][sid]
                        a['deleted_checks'] = 0
                        a['missing_checks'] = 0 if in_listing else a.get('missing_checks',0) + 1
                        state = 'removed' if a['missing_checks'] >= 2 else 'active'
                        if a['state'] != state:
                            self.event(sid, 'state', '已从来源目录移除' if state == 'removed' else '来源文章恢复')
                            changed = True
                        a['state'] = state
                        a['last_observed'] = now()
                        self.manifest.setdefault('update_results',{})[sid]=dict(ok=True,at=now(),message='正文与译文已保存',warnings=status['warnings'])
                        self.save()
                except HTTPError as e:
                    with self.lock:
                        if e.code in (404,410) and sid in self.manifest['articles']:
                            a = self.manifest['articles'][sid]
                            a['deleted_checks'] = a.get('deleted_checks',0) + 1
                            if a['deleted_checks'] >= 2 and a['state'] != 'deleted':
                                a['state'] = 'deleted'
                                changed = True
                                self.event(sid, 'state', '来源连续两次返回 404/410；本地内容全部保留')
                            self.save()
                        else:
                            if sid in self.manifest['articles']:
                                self.manifest['articles'][sid]['deleted_checks'] = 0
                            errors.append(sid + ': HTTP ' + str(e.code))
                            self.progress['failed'] += 1
                except Exception as e:
                    with self.lock:
                        if sid in self.manifest['articles']:
                            self.manifest['articles'][sid]['deleted_checks'] = 0
                    errors.append(sid + ': ' + str(e))
                    with self.lock:
                        self.progress['failed'] += 1
                finally:
                    with self.lock:
                        if errors and errors[-1].startswith(sid + ':'):
                            self.manifest.setdefault('update_results',{})[sid]=dict(ok=False,at=now(),message=errors[-1])
                        self.progress['completed'] += 1
                        self.progress['percent'] = round(self.progress['completed'] / len(targets) * 95)
                        self.manifest['last_progress']=dict(self.progress)
                        self.save()
            with self.lock:
                if not errors:
                    self.manifest['last_success'] = now()
                self.event(None, 'check', '核对完成' if not errors else '部分来源未完成核对：' + '；'.join(errors))
                if changed:
                    self.manifest['updated_at'] = now()
                self.save()
            if not errors:
                self.update_checkpoint()
                with self.lock:
                    self.progress.update(stage='packaging', percent=95, message='中英文正文已备齐，正在生成离线包…')
                self.build_package(require_complete=True)
            with self.lock:
                self.progress.update(stage='partial' if errors else 'done', percent=100,
                                     message='部分资料未完成，本轮未更新离线包。请查看更新记录并重试。' if errors else '中英文正文与离线包更新已完成。')
            return not errors
        except Exception as e:
            with self.lock:
                self.progress.update(stage='error', message='检查未完成：' + str(e))
                self.event(None, 'error', str(e))
                self.save()
            return False
        finally:
            with self.lock:
                self.updating = False
                self.pause_requested = False
                self.manifest['last_progress']=dict(self.progress)
                self.save()

    def build_package(self, require_complete=False):
        with self.lock:
            if require_complete and self.offline_status()['missing']:
                raise ValueError('原文或中文译文尚未备齐，请先完成资料更新；保留上次完整离线包')
            if require_complete:
                from translation import validate_quality
                for sid in self.manifest['articles']:
                    detail = self.detail(sid)
                    validate_quality(detail['original']['html'], detail['translation']['html'])
            out = self.root / 'output'
            out.mkdir(exist_ok=True)
            target = out / 'engineering-library-latest.zip'
            temp = out / 'engineering-library-latest.tmp'
            snapshot = copy.deepcopy(self.manifest)
            snapshot['offline_ready'] = not self.offline_status()['missing']
            snapshot['package_at'] = now()
            snapshot['reader_mode'] = True
            snapshot['content_as_of'] = snapshot.get('last_success') or snapshot['package_at']
            snapshot.pop('last_progress',None)
            snapshot['events']=[]
            snapshot['update_results']={}
            with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as z:
                include = ['library.py','originals.py','server.py','launcher.py','start.command','start.sh','start.bat','README.md']
                for name in include:
                    p = self.root / ('READER-README.md' if name=='README.md' and (self.root/'READER-README.md').exists() else name)
                    if p.exists():
                        z.write(p, 'engineering-library/' + name)
                for folder in ['web', 'content', 'output/pdf', '工程实践.app', '停止工程实践.app']:
                    for p in (self.root / folder).rglob('*'):
                        if p.is_file() and '__pycache__' not in p.parts:
                            z.write(p, 'engineering-library/' + p.relative_to(self.root).as_posix())
                z.writestr('engineering-library/data/manifest.json', json.dumps(snapshot, ensure_ascii=False, indent=2))
                for a in snapshot['articles'].values():
                    for n in range(1, a['latest'] + 1):
                        p = self.version_path(a['id'], n)
                        z.write(p, 'engineering-library/data/' + p.relative_to(self.data).as_posix())
                for folder in ['originals','original-assets','translations']:
                    for p in (self.data/folder).rglob('*'):
                        if p.is_file():
                            z.write(p,'engineering-library/data/'+p.relative_to(self.data).as_posix())
            with zipfile.ZipFile(temp) as z:
                if z.testzip():
                    raise ValueError('离线包校验失败')
            os.replace(temp, target)
            # Preserve uniquely timestamped distributions as well as the latest convenience copy.
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            archive = out / ('engineering-library-' + stamp + '-' + str(time.time_ns())[-6:] + '.zip')
            import shutil
            shutil.copy2(target, archive)
            self.manifest['package_at'] = snapshot['package_at']
            self.save()
            return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check','package','prepare'])
    parser.add_argument('--data')
    args = parser.parse_args()
    lib = Library(args.data)
    if args.action in {'check','prepare'}:
        ok = lib.check()
        print(json.dumps(lib.listing()['events'][:1], ensure_ascii=False))
        raise SystemExit(0 if ok else 1)
    print(lib.build_package(require_complete=True))


if __name__ == '__main__':
    main()
