const embedded=JSON.parse(document.getElementById('library-data').textContent);
const storageKey='engineering-library-personal-schema1';
let personal={schema:1,reflections:[],reading:[],drafts:[]};
let storageAvailable=true;
try{const raw=localStorage.getItem(storageKey);if(raw)personal=JSON.parse(raw);}catch(e){storageAvailable=false;}
const copy=value=>JSON.parse(JSON.stringify(value));
const timestamp=()=>new Date().toISOString();
function persist(next){
 try{localStorage.setItem(storageKey,JSON.stringify(next));storageAvailable=true;}
 catch(e){storageAvailable=false;document.getElementById('storage-notice').textContent='浏览器无法持久保存：记录暂存在本页，请在关闭前导出个人记录。';}
 personal=next;
}
function detailFor(id,v){const versions=embedded.articles[id];const result=versions?.[v||embedded.catalog.articles.find(a=>a.id===id)?.latest];if(!result)throw Error('文章或版本不存在');return copy(result);}
function validateBackup(value){
 if(!value||value.schema!==1||!['reflections','reading','drafts'].every(k=>Array.isArray(value[k])))throw Error('备份格式不受支持');
 for(const type of ['reflections','reading','drafts'])for(const r of value[type]){
  const v=detailFor(r.article,r.version);
  if(!Number.isInteger(r.version)||typeof r.updated_at!=='string'&&typeof r.created_at!=='string')throw Error('记录版本或时间无效');
  if(type!=='reading'&&(!Array.isArray(r.answers)||r.answers.length!==v.lesson.questions.length||!r.answers.every(a=>typeof a==='string'&&a.length<=20000)))throw Error('回答与文章版本不匹配');
  if(type==='reading'&&!['read','reading'].includes(r.status))throw Error('阅读状态无效');
  if(type==='reflections'&&(typeof r.id!=='string'||!['','我能解释了','还想再想想','以后再回顾'].includes(r.feeling||'')))throw Error('思考记录无效');
 }
}
async function localApi(path,body){
 const [name,query='']=path.split('?');const q=new URLSearchParams(query);
 if(name==='catalog'){const c=copy(embedded.catalog);c.reading=Object.fromEntries(personal.reading.map(r=>[r.article,r]));return c;}
 if(name==='article'){const d=detailFor(q.get('id'),Number(q.get('v'))||undefined);for(const k of ['original','translation'])if(d[k])d[k].html=d[k].html.replace(/\/original-assets\/[a-f0-9]+\.[a-z]+/g,p=>embedded.assets[p]||p);return d;}
 if(name==='history')return copy(personal.reflections).sort((a,b)=>b.created_at.localeCompare(a.created_at));
 if(name==='export')return {...copy(personal),exported_at:timestamp(),reader_version:embedded.version};
 if(name==='draft'&&body===undefined)return copy(personal.drafts.find(r=>r.article===q.get('id')&&r.version===Number(q.get('v')))?.answers||[]);
 let next=copy(personal);
 if(name==='import'){
  validateBackup(body);
  download('导入前个人记录-'+Date.now()+'.json',JSON.stringify(await localApi('export'),null,2));
  for(const k of ['reflections','reading','drafts'])for(const r of body[k]){
   const i=next[k].findIndex(x=>k==='reflections'?x.id===r.id:x.article===r.article&&(k==='reading'||x.version===r.version));
   if(i<0)next[k].push(copy(r));else if(k!=='reflections'&&r.updated_at>next[k][i].updated_at)next[k][i]=copy(r);
  }
 }else if(['reading','draft','reflect'].includes(name)){
  const d=detailFor(body.article,body.version);
  if(name==='reading'){
   if(!['reading','read'].includes(body.status))throw Error('阅读状态无效');
   const old=next.reading.find(r=>r.article===body.article);
   next.reading=next.reading.filter(r=>r.article!==body.article);
   next.reading.push({...body,status:old?.version===body.version&&old.status==='read'?'read':body.status,updated_at:timestamp()});
  }else{
   if(!Array.isArray(body.answers)||body.answers.length!==d.lesson.questions.length||!body.answers.every(a=>typeof a==='string'&&a.length<=20000))throw Error('回答格式无效');
   next.drafts=next.drafts.filter(r=>!(r.article===body.article&&r.version===body.version));
   if(name==='draft')next.drafts.push({...body,updated_at:timestamp()});
   else{
    if(!body.answers.some(a=>a.trim()))throw Error('请至少写下一点想法再保存');
    next.reflections.push({...body,id:crypto.randomUUID(),created_at:timestamp()});
   }
  }
 }else throw Error('独立版不支持此操作');
 persist(next);return {ok:true};
}
