# -*- coding: utf-8 -*-
"""Build an immutable, self-contained reader from an existing release ZIP."""
import base64,hashlib,json,mimetypes,re,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from library import Library
from presentation import refine
version=sys.argv[1] if len(sys.argv)>1 else '2.0.0'
assert re.fullmatch(r'\d+\.\d+\.\d+',version)
out=Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'output'/'standalone'/version
if out.exists(): raise SystemExit('Release already exists: choose a new version; older releases are immutable.')
with tempfile.TemporaryDirectory() as tmp:
 with zipfile.ZipFile(ROOT/'output/engineering-library-latest.zip') as z:z.extractall(tmp)
 lib=Library(root=Path(tmp)/'engineering-library'); c=lib.listing(); c['reading']={};c['reader_mode']=True
 articles={a['id']:{str(v):lib.detail(a['id'],v) for v in range(1,a['latest']+1)} for a in c['articles']}
 # Only distinct original bodies appear in reader history; all IDs remain addressable.
 for versions in articles.values():
  seen=set(); history=[]
  for key in sorted(versions,key=int,reverse=True):
   d=versions[key]
   body=(d.get('original') or {}).get('html')
   signature=hashlib.sha256((body or json.dumps(d['lesson'],ensure_ascii=False,sort_keys=True)).encode()).hexdigest()
   if signature not in seen:
    history.append({k:d[k] for k in ['version','saved_at']});seen.add(signature)
  for d in versions.values():d['reader_history']=history
 assets={}
 for p in (lib.data/'original-assets').iterdir():
  if p.is_file():assets['/original-assets/'+p.name]='data:'+str(mimetypes.guess_type(p.name)[0] or 'application/octet-stream')+';base64,'+base64.b64encode(p.read_bytes()).decode()
 data=dict(version=version,catalog=c,articles=articles,assets=assets)
js=refine((ROOT/'web/app.js').read_text())
js=js.replace('<h1>慢慢读，把工程实践想明白。</h1>', '<h1 class="home-title"><span>慢慢读，</span><span>把工程实践<em>想明白。</em></span></h1>')
js=js.replace('esc(selected.title)', 'esc(displayTitle(selected.title))').replace('esc(catalog.articles.find(a=>a.id===sid)?.title||sid)', 'esc(displayTitle(catalog.articles.find(a=>a.id===sid)?.title||sid))')
js=js.replace('window.scrollTo(0,0);', "$('#sidebar').classList.remove('nav-expanded');$('#nav-toggle').setAttribute('aria-expanded','false');window.scrollTo(0,0);")
js=re.sub(r'async function api\(path, body\)\{[^\n]+', 'async function api(path, body){return localApi(path,body);}',js)
js=js[:js.index("window.addEventListener('hashchange',route)")]+"window.addEventListener('hashchange',route);window.addEventListener('pagehide',()=>{if(pendingDraft)localApi('draft',pendingDraft);});route();"
a=js.index('async function updates()');b=js.index('let routing',a)
js=js[:a]+'''async function updates(){current=null;globalNavigation('reading');$('#main').innerHTML=`<div class="intro"><span class="eyebrow">离线典藏 · OFFLINE EDITION</span><h1>打开，就可以读。</h1><p class="lead">完整内容随文件保存。<br>把阅读留给自己，把连接留作选择。</p><div class="meta"><span>阅读器 ${embedded.version}</span><span>26 篇中英文全文</span><span>记录格式 schema 1</span></div></div>
<dl class="status-list"><dt>内容核对截至</dt><dd>${dt(catalog.content_as_of||catalog.last_success)}</dd><dt>阅读方式</dt><dd>双击 HTML，无需服务器或翻译模型</dd><dt>个人记录</dt><dd>浏览器暂存，可导出独立备份</dd></dl>
<h2>换一个版本，接着读。</h2><p>新版以独立文件发布，旧文件继续保留。升级前在“我的思考”导出个人记录，再在新版导入。文章自身的历史存档可在阅读页切换。</p><div class="notice">浏览器自动保存只是便利功能。移动文件、更换浏览器或清理数据后，记录可能不可用，请定期导出备份。</div><div class="actions"><a class="source-link" href="#records">管理个人记录 →</a><a class="source-link" href="#home">返回阅读</a></div>`;}
'''+js[b:]
js=js.replace('导入前备份已保存在本机。','导入前备份已发起下载，请确认浏览器已保存。')
js=js.replace('草稿已保存在本机',"草稿已暂存（请定期导出备份）")
html=(ROOT/'web/index.html').read_text().replace('<link rel="stylesheet" href="style.css">','<style>'+(ROOT/'web/style.css').read_text()+'\n'+(ROOT/'standalone/design.css').read_text()+'</style>').replace('<script src="app.js" defer></script>','')
html=html.replace('href="icon.png"', 'href="data:image/png;base64,'+base64.b64encode((ROOT/'web/icon.png').read_bytes()).decode()+'"')
html=html.replace('<div class="layout">','<p id="storage-notice" class="tiny muted" style="max-width:1100px;margin:16px auto;padding:0 24px">独立 HTML v'+version+' · 记录由浏览器暂存，请在“我的思考”定期导出；换版本时导入备份。</p><div class="layout">')
html=html.replace('<aside id="sidebar"', '<button class="nav-toggle" id="nav-toggle" aria-controls="sidebar" aria-expanded="false">阅读与思考目录</button><aside id="sidebar"')
html=html.replace('<a href="guide.html">使用说明与术语表</a>','<a href="#updates">版本与使用说明</a>')
payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
script=(ROOT/'standalone/storage.js').read_text()+'\n'+js+'''
$('#nav-toggle').addEventListener('click',()=>{const open=$('#nav-toggle').getAttribute('aria-expanded')!=='true';$('#nav-toggle').setAttribute('aria-expanded',String(open));$('#sidebar').classList.toggle('nav-expanded',open);if(open)$('#nav-toggle').scrollIntoView({block:'start'});});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('#nav-toggle').getAttribute('aria-expanded')==='true'){$('#sidebar').classList.remove('nav-expanded');$('#nav-toggle').setAttribute('aria-expanded','false');$('#nav-toggle').focus();}});
'''
assert '</script' not in script.lower()
html=html.replace('</body>','<script id="library-data" type="application/json">'+payload+'</script><script>'+script+'</script></body>')
out.mkdir(parents=True)
p=out/('工程实践-v'+version+'.html');p.write_text(html)
(out/'release.json').write_text(json.dumps({'version':version,'record_schema':1,'articles':len(articles),'article_versions':sum(map(len,articles.values())),'assets':len(assets),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,'source':'engineering-library-latest.zip'},ensure_ascii=False,indent=2))
print(p)
