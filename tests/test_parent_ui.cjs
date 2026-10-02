// Real parent-page functions with in-memory transport/storage; no provider calls.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');

function page(){
 const store=new Map(),sent=[];
 const c=vm.createContext({URLSearchParams,Date,crypto:webcrypto,setTimeout,clearTimeout,
  location:{search:'?view=ask&record=A'},
  localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
  esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),
  timeLabel:()=> '今天',conversationTitle:c=>c.title,scopeNames:{conversation:'仅这次聊天',topic:'相关话题',general:'日常讲法'},
  state:{id:'childA',data:{memories:[],conversations:[{id:'latest',title:'最新聊天'},{id:'chosen',title:'明确选择的聊天'}]}},
  render(){},toast(){},path:()=>'/parent',
  api:async(path,body)=>{sent.push({path,body:JSON.parse(JSON.stringify(body))});throw Error('模拟响应丢失');}
 });
 const shared=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
 vm.runInContext(shared.slice(shared.indexOf('function reminderScopeFields('),shared.indexOf('function reminderForm(')),c);
 vm.runInContext(fs.readFileSync(require.resolve('../static/parent.js'),'utf8'),c);
 vm.runInContext('loadParent=async()=>{};',c);
 return {c,store,sent,run:s=>vm.runInContext(s,c)};
}

test('uncertain request A does not follow the same question to record B',async()=>{
 const p=page();
 await p.run("parentSend('同一问题')");
 p.c.location.search='?view=ask&record=B';
 await p.run("parentSend('同一问题')");
 assert.equal(p.sent[0].body.recordId,'A');
 assert.equal(p.sent[1].body.recordId,'B');
 assert.notEqual(p.sent[0].body.requestId,p.sent[1].body.requestId);
 assert.equal(JSON.parse(p.store.get('parent-outbox-childA')).recordId,'B');
});

test('retry of identical child, text and record reuses the request and clears after acknowledgement',async()=>{
 const p=page();
 await p.run("parentSend('同一问题')");
 p.c.api=async(path,body)=>{p.sent.push({path,body});return {status:'completed'};};
 await p.run("parentSend(' 同一问题 ')");
 assert.equal(p.sent[0].body.requestId,p.sent[1].body.requestId);
 assert.equal(p.sent[1].body.recordId,'A');
 assert.equal(p.store.has('parent-outbox-childA'),false);
 assert.equal(p.store.get('parent-input-childA'),'');
});

test('same words from another child or no selected record form distinct requests',async()=>{
 const p=page();
 p.store.set('parent-outbox-childA',JSON.stringify({childId:'childB',requestId:'old_request',text:'同一问题',recordId:'A'}));
 await p.run("parentSend('同一问题')");
 assert.equal(p.sent[0].body.childId,'childA');
 assert.notEqual(p.sent[0].body.requestId,'old_request');
 p.c.location.search='?view=ask';
 await p.run("parentSend('同一问题')");
 assert.equal(p.sent[1].body.recordId,'');
 assert.notEqual(p.sent[0].body.requestId,p.sent[1].body.requestId);
});

test('conversation draft exposes scope and asks for a choice without selecting newest conversation',()=>{
 const p=page();
 let html=p.run("parentDraft({id:'m',draft:{status:'pending',action:'add',summary:'本次提醒',topic:'',scope:'conversation'}})");
 assert.match(html,/<option value="conversation" selected>仅这次聊天/);
 assert.match(html,/<option value="">请选择聊天/);
 assert.ok(!/<option value="latest" selected>/.test(html));
 p.store.set('parent-edit-childA-m',JSON.stringify({summary:'本次提醒',scope:'conversation',conversationId:'chosen'}));
 html=p.run("parentDraft({id:'m',draft:{status:'pending',action:'add',summary:'本次提醒',topic:'',scope:'conversation'}})");
 assert.match(html,/<option value="chosen" selected>/);
 assert.ok(!/<option value="latest" selected>/.test(html));
});
