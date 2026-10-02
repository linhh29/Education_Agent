// Actual page code with in-memory browser/HTTP substitutes; no service or model access.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {randomUUID}=require('node:crypto');
function page(shared=new Map()){
 const handlers={},root={innerHTML:'',addEventListener(k,fn){handlers[k]=fn;}};
 const c=vm.createContext({URLSearchParams,Intl,Date,console,crypto:{randomUUID},
  localStorage:{getItem:k=>shared.get(k)||null,setItem:(k,v)=>shared.set(k,v),removeItem:k=>shared.delete(k)},
  location:{pathname:'/memory',search:'?trash=1'},document:{querySelector:s=>s==='#app'?root:null},
  window:{addEventListener(){}},AbortController,setTimeout(){},clearTimeout(){},fetch:()=>new Promise(()=>{}),
  CuriosityVoice:class{stop(){}}
 });
 vm.runInContext(fs.readFileSync(require.resolve('../static/app.js'),'utf8'),c);
 const run=s=>vm.runInContext(s,c);
 run(`state.id='child';state.profiles=[{id:'child',nickname:'合成',kind:'demo'}];state.data={memories:[],messages:[]};parentTabs=()=>'';sync=async()=>{};toast=()=>{};`);
 const click=async dataset=>{const b={dataset,disabled:false,isConnected:true};await handlers.click({target:{closest:s=>s==='[data-action]'?b:null},detail:1});};
 return {run,root,click,shared};
}
test('trash recovery submits its own displayed version even though active list omits it',async()=>{
 const p=page();
 p.run(`sent=[];api=async(path,body)=>{if(body){sent.push(body);return {};}return {conversations:[],memories:[{id:'deleted',summary:'第二版',version:3}]};}`);
 await p.run('trashPage()');
 assert.match(p.root.innerHTML,/data-action="restore-memory" data-memory="deleted" data-version="3"/);
 await p.click({action:'restore-memory',memory:'deleted',version:'3'});
 assert.equal(p.run('sent[0].expectedVersion'),'3');
 assert.ok(p.run('sent[0].requestId'));
});
test('unknown response and a second tab reuse the same complete recovery request',async()=>{
 const shared=new Map(),a=page(shared),b=page(shared);
 for(const p of [a,b])p.run(`sent=[];api=async(path,body)=>{sent.push(body);throw new Error('响应丢失');}`);
 await assert.rejects(a.run("memoryAction('m','restore','3')"),/响应丢失/);
 await assert.rejects(b.run("memoryAction('m','restore','3')"),/响应丢失/);
 assert.equal(a.run('sent[0].requestId'),b.run('sent[0].requestId'));
 await assert.rejects(b.run("memoryAction('m','restore','4')"),/响应丢失/);
 assert.notEqual(b.run('sent[0].requestId'),b.run('sent[1].requestId'));
});
test('successful restore followed by failed refresh retains original request for recovery',async()=>{
 const p=page();
 p.run(`sent=[];api=async(path,body)=>{sent.push(body);return {};};sync=async()=>{throw new Error('刷新失败');};`);
 await assert.rejects(p.run("memoryAction('m','restore','3')"),/刷新失败/);
 p.run('sync=async()=>{}');
 await p.run("memoryAction('m','restore','3')");
 assert.ok(p.run('sent[0].requestId'));
 assert.equal(p.run('sent[0].requestId'),p.run('sent[1].requestId'));
 assert.equal(p.shared.size,0);
});
test('missing recovery version sends nothing and known conflicts do not retain a retry',async()=>{
 const p=page();
 p.run(`sent=[];api=async(path,body)=>{sent.push(body);const err=new Error('已更新');err.status=409;err.code='memory_conflict';throw err;};`);
 await assert.rejects(p.run("memoryAction('m','restore')"),/最新|版本/);
 assert.equal(p.run('sent.length'),0);
 await assert.rejects(p.run("memoryAction('m','restore','3')"),/已更新/);
 assert.equal(p.shared.size,0);
});
test('reactivation and intentional history restoration are separate explicit targets',async()=>{
 const p=page();
 p.run(`memory={id:'m',kind:'reminder',status:'withdrawn',version:4,summary:'第二版',sourceMessageIds:[],history:[{status:'parent_confirmed',summary:'第一版',scope:'general'},{status:'parent_confirmed',summary:'第二版',scope:'general'}]};sent=[];api=async(path,body)=>{sent.push(body);return {};};`);
 const html=p.run('recordDetail(memory)');
 assert.match(html,/data-action="restore-memory" data-memory="m" data-version="4"/);
 assert.match(html,/data-action="restore-version-memory" data-memory="m" data-version="4" data-history-index="0"/);
 await p.click({action:'restore-version-memory',memory:'m',version:'4',historyIndex:'0'});
 assert.equal(p.run('sent[0].action'),'restore_version');
 assert.equal(p.run('sent[0].historyIndex'),0);
 assert.equal(p.run('sent[0].expectedVersion'),'4');
});
