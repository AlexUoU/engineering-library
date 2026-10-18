"""Reader-facing history labels; internal identifiers remain stable for records."""
import re

def refine(js):
    start=js.index('function versionPicker(')
    end=js.index('function versionDate(',start)
    js=js[:start]+'''function versionPicker(v,id){const rows=v.reader_history||v.versions;return `<details class="version-picker"><summary class="version-trigger">历史存档</summary><section class="version-panel" aria-label="文章历史存档"><div class="version-panel-heading">按保存时间查看<span>${rows.length} 份存档</span></div>${rows.map(x=>{const selected=x.version===v.version;const content=`<span class="version-row-title"><strong>${day(x.saved_at)}</strong><span>${selected?'正在阅读':x.version===v.article.latest?'当前内容':'历史内容'}</span></span><span class="version-row-date">本地保存：${dt(x.saved_at)}</span>`;return selected?`<div class="version-row current-version" aria-current="true">${content}</div>`:`<a class="version-row" aria-label="查看 ${dt(x.saved_at)} 保存的内容" href="${articleLink(id,x.version)}">${content}</a>`;}).join('')}<p class="version-panel-note">相同正文的内部维护记录已合并展示。切换存档不会改变已保存的思考。</p></section></details>`;}
'''+js[end:]
    js=js.replace('<span>v${a.latest}</span>','')
    js=js.replace("${a.editorial==='pending'?'<span>导读待复核</span>':''}",'')
    js=js.replace("${isOld?'旧版':'当前'} v${v.version}","${isOld?'历史内容':'当前内容'}")
    js=re.sub(r"\$\{v.editorial==='pending'\?.*?:''\}", '',js)
    js=js.replace('${dt(r.created_at)} · v${r.version}','${dt(r.created_at)}')
    js=js.replace('<p>${esc(v.reason)}</p>','<p>本地保存：${dt(v.saved_at)}。原文内容、中文导读与学习记录分别保存。</p>')
    return js
