const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
const html=fs.readFileSync(process.argv[2],'utf8');
const payload=html.match(/<script id="library-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const data=JSON.parse(payload);
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);assert(!/\bfetch\(/.test(script));
let stored=new Map(),downloads=[];
function context(){const c=vm.createContext({document:{getElementById:id=>id==='library-data'?{textContent:payload}:{textContent:''}},localStorage:{getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v)},URLSearchParams,crypto,download:(...v)=>downloads.push(v)});vm.runInContext(script.slice(0,script.indexOf('const chineseTitles')),c);return c;}
(async()=>{let c=context();const api=(p,b)=>{c.p=p;c.b=b;return vm.runInContext('localApi(p,b)',c);};
assert.equal((await api('catalog')).articles.length,26);
for(const a of data.catalog.articles){const d=await api('article?id='+a.id);assert(d.original?.html&&d.translation?.html);for(const k of ['original','translation']){assert(!d[k].html.includes('/original-assets/'));assert(!/<(?:img|iframe)[^>]+src=["']https?:/i.test(d[k].html));}}
const id='building-effective-agents',v=data.catalog.articles.find(a=>a.id===id).latest;
const answers=data.articles[id][v].lesson.questions.map(()=> '验收回答');
await api('reading',{article:id,version:v,status:'read'});await api('reading',{article:id,version:v,status:'reading'});assert.equal((await api('catalog')).reading[id].status,'read');
await api('draft',{article:id,version:v,answers});await api('reflect',{article:id,version:v,answers,feeling:'还想再想想'});
const backup=await api('export');assert.equal(backup.reflections.length,1);assert.equal(backup.drafts.length,0);
c=context();assert.equal((await api('history')).length,1);
stored=new Map();c=context();await api('import',backup);await api('import',backup);assert.equal((await api('history')).length,1);assert.equal(downloads.length,2);
const before=JSON.stringify(await api('history'));const bad=JSON.parse(JSON.stringify(backup));bad.reading[0].article='does-not-exist';await assert.rejects(()=>api('import',bad));assert.equal(JSON.stringify(await api('history')),before);
console.log(JSON.stringify({passed:true,articles:26,versions:Object.values(data.articles).reduce((s,x)=>s+Object.keys(x).length,0),checks:['script syntax','no fetch calls','26 bilingual bodies','inline image references','read state retained','draft and reflection','persistent reload simulated','export/import roundtrip','duplicate import','invalid import atomic rejection'],browser_file_open:'not tested: automation URL policy blocks file scheme'},null,2));
})().catch(e=>{console.error(e);process.exit(1)});
