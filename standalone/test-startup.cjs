const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
const html=fs.readFileSync(process.argv[2],'utf8');
const payload=html.match(/<script id="library-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nodes=new Map();
function node(k){if(!nodes.has(k))nodes.set(k,{dataset:{},innerHTML:'',textContent:'',value:'',classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},querySelectorAll(){return []}});return nodes.get(k);}
const c=vm.createContext({document:{getElementById:k=>k==='library-data'?{textContent:payload}:node('#'+k),querySelector:node,querySelectorAll:()=>[],addEventListener(){}},localStorage:{getItem(){return null},setItem(){}},window:{addEventListener(){},scrollTo(){}},location:{hash:'#home'},URLSearchParams,crypto,setTimeout,clearTimeout,console});
process.on('unhandledRejection',e=>{console.error(e);process.exit(1)});
(async()=>{
 vm.runInContext(script,c);
 await new Promise(r=>setImmediate(r));
 assert(node('#main').innerHTML.includes('慢慢读'), 'Home did not render: '+node('#main').innerHTML);
 assert(node('#article-list').innerHTML.includes('building-effective-agents'),'Catalog absent');
 for(const hash of ['#article/building-effective-agents','#records','#updates','#home']){
  c.location.hash=hash;await vm.runInContext('route()',c);
  assert(!node('#main').innerHTML.startsWith('<div class="empty">'),'Route error '+hash);
  if(hash.startsWith('#article/')){assert(!node('#main').innerHTML.includes('待复核'));assert(!node('#main').innerHTML.includes('>v3<'));assert(node('#main').innerHTML.includes('历史存档'));}
  if(hash.startsWith('#article/'))assert(node('#original-content').innerHTML.includes('什么是 agent'),'Chinese body absent');
 }
 console.log('PASS: full emitted script initialization and home/article/records/updates/home routing (DOM simulation, not browser acceptance)');
})().catch(e=>{console.error(e);process.exit(1)});
