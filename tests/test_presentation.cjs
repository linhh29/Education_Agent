// Presentation contracts only; no network, model, browser or device access.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function page(){
 const root={innerHTML:'',addEventListener(){}},events=[];
 const c=vm.createContext({URLSearchParams,Intl,Date,console,
  localStorage:{getItem(){return null;}},location:{pathname:'/memory',search:''},
  document:{querySelector:s=>s==='#app'?root:null,getElementById:()=>({classList:{add(){}},focus(){},scrollIntoView(){events.push('source');}})},
  window:{addEventListener(){},scrollTo(){events.push('top');}},
  history:{pushState(_a,_b,url){c.location.pathname=url.split('?')[0];c.location.search=url.includes('?')?'?'+url.split('?')[1]:'';}},
  AbortController, setTimeout(){},clearTimeout(){},fetch:()=>new Promise(()=>{}),
  CuriosityVoice:class{constructor(){this.phase='idle';}inputBusy(){return false;}stop(){}}
 });
 vm.runInContext(fs.readFileSync(require.resolve('../static/app.js'),'utf8'),c);
 const run=s=>vm.runInContext(s,c);
 run(`state.id='child';state.profiles=[{id:'child',nickname:'小禾（合成）',kind:'demo'}];state.status={speech:{enabled:true}};state.data={activeConversation:{id:'now'},conversations:[],messages:[],memories:[]};`);
 return {root,events,run};
}
test('memory list separates current scope, old conversation scope and withdrawn evidence',()=>{
 const p=page();
 p.run(`state.data.memories=[
 {id:'live',kind:'reminder',scope:'general',status:'parent_confirmed',summary:'仍有效的提醒',sourceMessageIds:[]},
 {id:'old',kind:'preference',scope:'general',effectiveScope:'conversation',conversationId:'old',status:'observed',summary:'只限旧聊天',sourceMessageIds:[]},
 {id:'withdrawn',kind:'reminder',scope:'general',status:'withdrawn',summary:'撤回的提醒',sourceMessageIds:[]},
 {id:'stale',kind:'understanding',scope:'topic',status:'observed',evidenceStale:true,summary:'依据已变',sourceMessageIds:[]}
 ];memoryPage();`);
 const [main,secondary]=p.root.innerHTML.split('<details class="past-records">');
 assert.match(main,/仍有效的提醒/);
 for(const text of ['只限旧聊天','撤回的提醒','依据已变']){assert.ok(!main.includes(text));assert.ok(secondary.includes(text));}
 assert.match(secondary,/仅这次聊天/);
});
test('an invalid latest activity does not reveal an older activity; empty summary omitted',()=>{
 const p=page();
 p.run(`state.data.conversations=[{id:'c',title:'聊天'}];state.data.messages=[
 {id:'a',role:'assistant',conversationId:'c',text:'旧回答',suggestion:{title:'旧活动',steps:'旧活动步骤'},suggestionEligible:true},
 {id:'b',role:'assistant',conversationId:'c',text:'新回答',suggestion:{title:'新活动',steps:'新活动步骤'},suggestionEligible:false}
 ];`);
 let html=p.run("conversationDetail('c')");
 assert.ok(!html.includes('旧活动步骤')&&!html.includes('新活动步骤')&&!html.includes('探索小结'));
 p.run('state.data.messages[1].suggestionEligible=true');
 html=p.run("conversationDetail('c')");
 assert.ok(html.includes('新活动步骤')&&!html.includes('旧活动步骤'));
});
test('source navigation scrolls after the page reset and keeps legacy URLs usable',()=>{
 const p=page();
 p.run(`state.data.conversations=[{id:'c',title:'聊天'}];state.data.messages=[{id:'u',conversationId:'c',role:'user',text:'孩子原话'}];nav('/memory?conversation=c&source=u');`);
 assert.deepEqual(p.events,['top','source']);
 assert.match(p.root.innerHTML,/source-u/);
 assert.match(p.root.innerHTML,/孩子原话/);
});
test('reading label follows the corresponding message playback state',()=>{
 const p=page();
 p.run("voice.messageId='a'");
 for(const [phase,label] of [['idle','朗读'],['play_ready','朗读'],['playing','停止朗读'],['tts_error','朗读']]){
  p.run(`voice.phase='${phase}'`);
  assert.match(p.run("voiceOutput({id:'a',role:'assistant'})"),new RegExp(`</svg>${label}</button>`));
  assert.match(p.run("voiceOutput({id:'b',role:'assistant'})"),/<\/svg>朗读<\/button>/);
 }
});
