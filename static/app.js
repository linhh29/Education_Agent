const app = document.querySelector('#app');
const esc = (value='') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const selectionKey = 'curiosity-selected-profile-v2';
const state = {id:localStorage.getItem(selectionKey)||'', profiles:[], data:null, status:null, sending:false, stopping:false, requestId:null, controller:null, timer:null, epoch:0, trash:null, testing:new URLSearchParams(location.search).get('dev')==='1', connectionError:'', sendError:''};
const kindNames = {confusion:'还想弄明白', understanding:'这次表达的理解', preference:'明确表达的偏好', reminder:'家长提醒'};
const scopeNames = {conversation:'仅这段聊天',topic:'这个话题',general:'一般讲解偏好'};
const statusNames = {observed:'来自孩子原话 · AI整理', parent_confirmed:'家长已修订', withdrawn:'已撤回 · 回答不再使用', deleted:'已删除'};
const timeLabel = value => value ? new Intl.DateTimeFormat('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '';
const draftKey = () => 'curiosity-draft-'+state.id;
const transcriptKey = () => 'curiosity-transcript-'+state.id;
const voice = new CuriosityVoice({api,context:()=>({childId:state.id,conversationId:state.data?.activeConversation?.id||'',maxSeconds:state.status?.speech?.maxRecordingSeconds||60}),changed:voiceChanged,notice:toast,transcript:(text,id,inputKind)=>{
 const before=localStorage.getItem(draftKey())||'';
 localStorage.setItem(draftKey(),before.trim()?before+'\n'+text:text);
 localStorage.setItem(transcriptKey(),JSON.stringify({id,inputKind}));render();document.querySelector('#question')?.focus();
}});
function voiceInput(){
 if(!state.status?.speech?.enabled)return '';
 const phase=voice.phase,busy=voice.inputBusy(),recording=phase==='recording';
 const labels={permission:'请在浏览器提示中允许麦克风；也可以取消后打字。',recording:'正在录音，讲完后点“结束录音”。',converting:'正在准备这段音频…',recognizing:'正在识别，还没有发送问题…'};
 const pending=localStorage.getItem(voice.taskKey());
 return `<div class="voice-actions"><button type="button" class="voice-record ${recording?'recording':''}" data-action="voice-record" ${state.sending||state.data?.pending||(busy&&!recording)?'disabled':''}>${icon(recording?'stop':'mic')}${recording?'结束录音':'说话'}</button>${busy?'<button type="button" class="voice-secondary" data-action="voice-stop">取消</button>':`<button type="button" class="voice-secondary" data-action="voice-file" ${state.sending||state.data?.pending?'disabled':''}>选择音频</button>`}<input id="voiceFile" type="file" accept="audio/*,.wav,.webm,.m4a,.mp3" hidden>${recording?'<small data-record-seconds></small>':''}</div>${labels[phase]?`<p class="voice-status" role="status">${labels[phase]}</p>`:''}${phase==='asr_error'?`<p class="voice-error" role="alert">${esc(voice.error)}</p>${voice.unknown&&voice.job?'<button type="button" class="voice-secondary" data-action="voice-recover">查看识别状态</button>':voice.raw?'<button type="button" class="voice-secondary" data-action="voice-retry">再识别一次</button>':''}`:''}${pending&&!busy&&!voice.job?'<p class="voice-status">有一段识别结果尚未查看。<button type="button" class="voice-secondary" data-action="voice-recover">继续查看</button><button type="button" class="voice-secondary" data-action="voice-dismiss">忽略这次</button></p>':''}${localStorage.getItem(transcriptKey())?'<p class="voice-status">语音已转成文字，请核对或修改后发送。<button type="button" class="voice-secondary" data-action="voice-text">改为文字输入</button></p>':''}`;
}
function voiceOutput(message){
 if(message.role!=='assistant'||!state.status?.speech?.enabled)return '';
 const active=voice.messageId===message.id,phase=active?voice.phase:'idle';
 return `<div class="voice-output" data-message="${esc(message.id)}"><button type="button" class="read-button" data-action="voice-speak" data-message="${esc(message.id)}" ${voice.inputBusy()||phase==='tts_loading'?'disabled':''}>${icon(phase==='playing'?'stop':'speaker')}${phase==='playing'?'停止朗读':phase==='tts_loading'?'正在准备朗读…':'朗读'}</button>${phase==='tts_loading'?'<button type="button" class="read-button" data-action="voice-stop">取消</button>':''}${active&&voice.error&&['tts_error','play_ready'].includes(phase)?`<small role="status">${esc(voice.error)}</small>`:''}${active&&phase==='tts_error'&&voice.unknown?'<button type="button" class="read-button" data-action="voice-recover">查看朗读状态</button>':''}</div>`;
}
function voiceChanged(){
 const input=document.querySelector('.voice-input');
 if(input){const key=voice.phase+voice.error+Boolean(voice.job)+Boolean(voice.raw);if(input.dataset.state!==key){input.innerHTML=voiceInput();input.dataset.state=key;}const timer=input.querySelector('[data-record-seconds]');if(timer)timer.textContent=Math.floor((performance.now()-voice.started)/1000)+' / 60秒';}
 document.querySelectorAll('.voice-output').forEach(el=>{const m=state.data?.messages.find(m=>m.id===el.dataset.message);if(m)el.outerHTML=voiceOutput(m);});
 const textarea=document.querySelector('#question');if(textarea)textarea.disabled=voice.inputBusy()||state.sending||Boolean(state.data?.pending);
 const sendButton=document.querySelector('#chatForm button[type="submit"]');if(sendButton)sendButton.disabled=voice.inputBusy();
}

function toast(text){ let el=document.querySelector('#toast'); if(!el){el=document.createElement('div'); el.id='toast'; el.setAttribute('role','status'); document.body.append(el);} el.textContent=text; el.className='toast visible'; clearTimeout(el.timer); el.timer=setTimeout(()=>el.className='toast',4200); }
async function api(path,body,method='POST',signal){
 const controller = signal ? null : new AbortController(); const timer = controller ? setTimeout(()=>controller.abort(),35000) : null;
 try{const response=await fetch(path,{method:body===undefined?'GET':method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:signal||controller.signal}); const data=await response.json(); if(!response.ok){const err=new Error(data.message||'操作未完成，请重试。');err.code=data.error;err.status=response.status;throw err;}return data;}
 catch(err){if(err.name==='AbortError') throw err; if(err instanceof TypeError) throw new Error('本机服务没有连接上，请检查启动窗口后刷新。');throw err;} finally{clearTimeout(timer);}
}
const activeProfiles = () => state.profiles.filter(p=>!p.archived&&(p.kind!=='test'||state.testing));
const current = () => state.profiles.find(p=>p.id===state.id);
// Display-only labels; keep the original profile and model context intact.
const profileName = p => (p?.nickname||'孩子').replace(/[（(](?:合成演示|合成彩排|合成|演示)[）)]/g,'');
const icon = name => `<svg class="control-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${name==='mic'?'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 11v1a6 6 0 0 0 12 0v-1M12 18v3M9 21h6"/>':name==='stop'?'<rect x="6" y="6" width="12" height="12" rx="2"/>':'<path d="M11 5 5 9H2v6h3l6 4V5ZM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>'}</svg>`;
const path = () => location.pathname==='/'?'/child':location.pathname;
function nav(url){voice.stop();history.pushState(null,'',url);window.scrollTo({top:0});render();}
function shell(content){
 const profile=current(),page=path();
 app.innerHTML=`<header class="site-header"><div class="header-inner"><a href="/child" class="brand" aria-label="好奇心伙伴"><span class="brand-mark" aria-hidden="true">✦</span><span>好奇心伙伴</span><small class="ai-label">AI</small></a><nav class="main-nav" aria-label="主导航"><a href="/child" ${page==='/child'?'aria-current="page"':''}>一起聊</a><a href="/parent" ${['/parent','/memory'].includes(page)?'aria-current="page"':''}>家长</a></nav><div class="profile-control"><label class="sr-only" for="profileSelect">当前档案</label><select id="profileSelect"><option value="" ${!profile?'selected':''}>选择档案</option>${activeProfiles().map(p=>`<option value="${esc(p.id)}" ${p.id===state.id?'selected':''}>${esc(profileName(p))} · ${p.kind==='test'?'测试':'示例'}</option>`).join('')}<option value="new">＋ 新建档案</option></select><a href="/setup" class="settings-link">档案设置</a></div></div></header><main id="main">${state.testing?`<details class="test-banner"><summary>开发验证空间 · 运行信息</summary><p>${esc(state.status?.model||'未连接')} · ${esc(state.status?.mode||'')} · ${state.status?.occupiedCny==null?'预算尚未读取':'预算占用约 ¥'+Number(state.status.occupiedCny).toFixed(4)}</p><button data-action="exit-test">回到普通页面</button></details>`:''}${content}</main>`;
}

function welcome(){return `<div class="empty welcome"><h1>好奇心伙伴</h1><p>选择或创建一个档案，开始聊天。</p><a class="button primary" href="/setup?new=1">创建档案</a></div>`;}

function sessionMessages(){const conv=state.data?.activeConversation;return conv?state.data.messages.filter(m=>m.conversationId===conv.id):[];}
function bubble(message,detail=false){return `<article class="message ${message.role}" id="source-${esc(message.id)}"><div class="speaker">${message.role==='user'?esc(profileName(current())):'好奇心伙伴'}${detail?`<time>${timeLabel(message.createdAt)}</time>`:''}</div><div class="message-text">${esc(message.text)}</div>${message.role==='user'&&['failed','cancelled','pending'].includes(message.status)?`<small class="message-state">${{failed:'这次还没回答成功',cancelled:'已停止，问题仍然保留',pending:'正在回答这个问题'}[message.status]}</small>`:''}${state.testing&&detail&&message.usedMemoryEvidence?.length?`<details class="answer-evidence"><summary>模型报告参考了哪些资料</summary>${message.usedMemoryEvidence.map(m=>`<p>${esc(kindNames[m.kind]||'线索')}：${esc(m.summary)}</p>`).join('')}<small>这是模型自报的参考快照，不代表已经证明遵循。当前修订影响后续回答。</small></details>`:''}${message.retrieval?.status==='unavailable'?'<small class="safety-note">这次历史资料未完整可用，回答可能未采用家长提醒。原记录仍保留，可以稍后重试。</small>':''}${message.needsParent?'<small class="safety-note">这件事需要家长陪你一起处理。</small>':''}${message.inputSource?.type==='asr'?`<small class="input-origin">${message.inputSource.inputKind==='audio_file'?'音频转写':'语音转写'}${message.inputSource.edited?' · 编辑后发送':''}</small>${detail?`<details class="answer-evidence"><summary>原始识别文字</summary><p>${esc(message.inputSource.transcript)}</p><small>自动识别可能出错；对话保留实际提交的文字。</small></details>`:''}`:''}${voiceOutput(message)}</article>`;}
function childPage(){
 const data=state.data, profile=current(), messages=sessionMessages();
 const busy=state.sending||Boolean(data.pending), last=data.lastRequest;
 const retryable=last&&['failed','cancelled'].includes(last.status);
 const hasAnswer=messages.some(m=>m.role==='assistant');
 shell(`<div class="child-grid"><section class="chat-card" aria-label="对话"><div class="conversation-meta"><h1>一起聊</h1><button class="button small subtle" data-action="new-session" ${!data.activeConversation||state.stopping?'disabled':''}>开启新对话</button></div><div class="chat-log" role="log" aria-live="polite" aria-relevant="additions text">${messages.length?messages.map(m=>bubble(m)).join(''):`<div class="chat-welcome"><span aria-hidden="true">✦</span><h2>你好，${esc(profileName(profile))}。</h2><p>你想问什么？</p></div>`}${busy?'<div class="thinking" role="status"><span class="status-dot"></span>正在想怎么讲清楚…</div>':''}</div>${state.sendError?`<div class="request-notice" role="alert">${esc(state.sendError)}</div>`:''}${state.connectionError?`<div class="request-notice" role="alert">${esc(state.connectionError)}<button class="button small" data-action="reload">重新连接</button></div>`:''}${retryable&&!busy?`<div class="request-notice" role="status"><span>${esc(last.error||(last.status==='cancelled'?'已停止这次回答。问题保留了。':'还没有完成这次回答。'))}</span><button class="button small" data-action="retry">再试一次</button></div>`:''}<div class="composer-area">${hasAnswer?`<div class="feedback" aria-label="换个讲法"><button data-action="feedback" data-feedback="clarify" data-text="我还是没听懂，可以换个讲法吗？">没听懂</button><button data-action="feedback" data-feedback="simpler" data-text="可以讲得简单一点吗？" data-style="simpler">简单一点</button><button data-action="feedback" data-feedback="example" data-text="可以举一个例子吗？" data-style="example">举个例子</button><button data-action="feedback" data-feedback="detail" data-text="我想听得更详细一点。" data-style="detail">详细一点</button></div>`:''}<form id="chatForm"><div class="voice-input">${voiceInput()}</div><label class="sr-only" for="question">你想问什么？</label><textarea id="question" name="question" rows="2" maxlength="800" placeholder="你想问什么？也可以接着刚才说…" ${busy?'disabled':''}>${esc(localStorage.getItem(draftKey())||'')}</textarea><div class="composer-bottom"><span>Enter 发送，Shift + Enter 换行</span>${busy?`<button type="button" class="button stop" data-action="stop" ${state.stopping?'disabled':''}>${state.stopping?'正在停止…':'停止回答'}</button>`:'<button type="submit" class="button primary">发送 <span aria-hidden="true">↗</span></button>'}</div></form></div></section></div>`);
 document.querySelectorAll('.feedback button').forEach(b=>b.disabled=busy||messages[messages.length-1]?.role!=='assistant');
 const log=document.querySelector('.chat-log');
 log.tabIndex=0;log.setAttribute('aria-label','对话记录，可上下滚动查看');
 const latestQuestion=Array.from(log.querySelectorAll('.message.user')).pop();
 if(busy)log.scrollTop=log.scrollHeight;
 else if(latestQuestion)log.scrollTop+=latestQuestion.getBoundingClientRect().top-log.getBoundingClientRect().top-16;
 const more=document.createElement('button');more.type='button';more.className='read-more';more.dataset.action='read-more';more.textContent='继续往下看 ↓';log.after(more);
 const showMore=()=>more.hidden=log.scrollTop+log.clientHeight>=log.scrollHeight-8;
 log.addEventListener('scroll',showMore);showMore();voiceChanged();
}
function memoryState(memory){return memory.evidenceStale?'依据已更新 · 暂不使用':statusNames[memory.status]||'历史记录';}
function sourceLink(message,label='查看原话'){return `<a href="/memory?conversation=${encodeURIComponent(message.conversationId)}&source=${encodeURIComponent(message.id)}">${label}</a>`;}
function memoryOrigin(memory){return memory.kind==='reminder'?'家长添加':`来自对话 · ${memory.parentEdited?'家长已修订':'AI整理'}`;}
function memoryRow(memory){
 return `<article class="record-row"><a class="record-body" href="/memory?memory=${encodeURIComponent(memory.id)}">${esc(memory.summary)}</a><a class="record-edit" href="/memory?memory=${encodeURIComponent(memory.id)}">编辑</a></article>`;
}
function parentTabs(selected){return `<nav class="parent-tabs" aria-label="家长导航"><a href="/parent" ${selected==='chats'?'aria-current="page"':''}>聊天记录</a><a href="/parent?view=ask" ${selected==='ask'?'aria-current="page"':''}>家长问答</a><a href="/memory" ${selected==='memories'?'aria-current="page"':''}>孩子档案</a></nav>`;}
function conversationTitle(conv){return conv.exploration?.status==='ready'?conv.exploration.topic:conv.title||'一次聊天';}
function explorationDetail(conv){
 const e=conv.exploration;
 if(!e)return '';
 if(e.status!=='ready')return `<p class="request-notice" role="status">${esc(e.status==='pending'?'正在整理聊天小结…':e.status==='stale'?'依据已更新，可在聊天操作中重新整理小结。':e.error||'小结暂未完成，可在聊天操作中重试。')}</p>`;
 const point=p=>{const source=state.data.messages.find(m=>m.id===p.sourceMessageIds?.[0]);return `<li>${esc(p.quote||p.text)} ${source?sourceLink(source):''}</li>`;};
 const groups=[['difficulties','当时的困惑'],['attempts','尝试过的讲法'],['openQuestions','聊到的追问']].filter(([key])=>e[key]?.length);
 if(!e.focus?.text&&!groups.length)return '';
 return `<section class="panel exploration-panel"><h2>探索小结</h2>${e.focus?.text?`<p>${esc(e.focus.text)}</p>`:''}<div class="exploration-columns">${groups.map(([key,label])=>`<div><h3>${label}</h3><ul>${e[key].map(point).join('')}</ul></div>`).join('')}</div>${e.partial?'<small class="muted">小结涵盖最近15轮。</small>':''}</section>`;
}
function parentPage(){
 const params=new URLSearchParams(location.search),q=(params.get('q')||'').trim();
 if(params.get('view')==='ask'){parentAskPage();return;}
 const all=state.data.conversations.filter(c=>!q||state.data.messages.some(m=>m.conversationId===c.id&&m.text.includes(q))),sessions=q||params.has('all')?all:all.slice(0,6);
 shell(`${parentTabs('chats')}<section class="page-heading"><h1>聊天记录</h1><a class="secondary-link" href="/memory?trash=1">回收站</a></section>${!q&&!params.has('all')?recentActivity():''}<section class="panel session-list"><form id="searchForm" class="search-form"><label class="sr-only" for="search">搜索对话</label><input id="search" name="q" value="${esc(q)}" placeholder="搜索问题或回答"><button class="button small">搜索</button></form>${sessions.length?sessions.map(c=>`<a class="session-row" href="/memory?conversation=${encodeURIComponent(c.id)}"><div><h2>${esc(conversationTitle(c))}</h2><small>${timeLabel(c.startedAt)} · ${c.endedAt?'已结束':'正在继续'}</small>${c.exploration?.status==='ready'&&c.exploration.focus?.text?`<p>${esc(c.exploration.focus.text)}</p>`:''}</div><span aria-hidden="true">→</span></a>`).join(''):`<div class="empty compact"><p>${q?'没有找到相关对话。':'还没有聊天记录。'}</p><a href="${q?'/parent':'/child'}">${q?'查看全部记录':'开始聊天'}</a></div>`}</section>${!q&&!params.has('all')&&all.length>6?'<a class="secondary-link" href="/parent?all=1">查看完整历史 →</a>':''}`);
}

function sourceMessages(memory){return state.data.messages.filter(m=>memory.sourceMessageIds.includes(m.id));}
function recordDetail(memory){
 const messages=sourceMessages(memory).filter(m=>m.role==='user');
 return `${parentTabs('memories')}<a class="back-link" href="/memory">← 孩子档案</a><section class="page-heading"><div><p class="eyebrow">${kindNames[memory.kind]||'记录'}${memory.kind==='reminder'?'':' · '+memoryOrigin(memory)}</p><h1>${esc(memory.topic||'日常讲法')}</h1><p>${scopeNames[memory.effectiveScope||memory.scope]||'相关话题'} · ${timeLabel(memory.updatedAt)}</p></div></section><div class="detail-grid"><section class="panel"><h2>当前记录</h2><p class="record-summary">${esc(memory.summary)}</p><a class="secondary-link" href="/parent?view=ask&record=${encodeURIComponent(memory.id)}">询问这条记录的依据</a>${memory.evidenceStale||memory.status==='withdrawn'?`<p class="safety-note">${memoryState(memory)}</p>`:''}${memory.kind!=='reminder'&&memory.quote?`<h3>孩子的原话</h3><blockquote class="source-quote">${esc(memory.quote)}</blockquote><div class="source-links">${messages.map((m,i)=>sourceLink(m,messages.length>1?'查看原话 '+(i+1):'查看原话')).join('')}</div>`:''}</section><section class="panel edit-panel"><h2>修改${memory.kind==='reminder'?'提醒':'记录'}</h2><form id="memoryForm" data-memory="${esc(memory.id)}" data-version="${esc(memory.version)}">${memory.kind==='reminder'?`<label for="memoryTopic">话题名称 <span>（选择“这个话题”时填写）</span></label><input id="memoryTopic" name="topic" maxlength="60" value="${esc(memory.topic)}">${reminderScopeFields(memory)}`:''}${memory.kind==='preference'?`<label for="memoryScope">适用范围</label><select id="memoryScope" name="scope">${Object.entries(scopeNames).map(([key,label])=>`<option value="${key}" ${(memory.effectiveScope||memory.scope)===key?'selected':''}>${label}</option>`).join('')}</select>`:''}<label for="memorySummary">${memory.kind==='reminder'?'提醒内容':'对这次表达的理解'}</label><textarea id="memorySummary" name="summary" rows="5" maxlength="300" required>${esc(memory.summary)}</textarea><button class="button primary" type="submit">保存修改</button> <a class="button" href="/memory">取消</a><p class="form-message" role="status"></p></form><details class="inline-details"><summary>撤回与恢复</summary><div class="record-actions"><button data-action="withdraw-memory" data-memory="${esc(memory.id)}" data-version="${esc(memory.version)}" ${memory.status==='withdrawn'?'disabled':''}>撤回记录</button>${memory.status==='withdrawn'?`<button data-action="restore-memory" data-memory="${esc(memory.id)}" data-version="${esc(memory.version)}">恢复使用</button>`:''}<button class="text-danger" data-action="delete-memory" data-memory="${esc(memory.id)}" data-version="${esc(memory.version)}">移到回收站</button></div><p class="muted">原始聊天仍保留。</p></details>${memory.history.length?`<details class="inline-details revision-history"><summary>修改历史</summary>${memory.history.map((h,index)=>({h,index})).reverse().map(({h,index})=>`<p>${esc(h.summary)}<br><small>${statusNames[h.status]||'历史版本'} · ${scopeNames[h.scope]||''}</small><br><button type="button" class="button small" data-action="restore-version-memory" data-memory="${esc(memory.id)}" data-version="${esc(memory.version)}" data-history-index="${index}">恢复到这个版本</button></p>`).join('')}</details>`:''}</section></div>`;
}

function reminderScopeFields(value={}){
 const scope=value.scope||'',conversationId=value.conversationId||'';
 const choices=state.data?.conversations||[];
 return `<label>适用范围<select name="scope">${!scope?'<option value="" selected>按适用话题决定</option>':''}${Object.entries(scopeNames).map(([key,label])=>`<option value="${key}" ${scope===key?'selected':''}>${label}</option>`).join('')}</select></label>${!scope?'<p class="muted">填写话题则用于这个话题，留空则用于一般讲解。</p>':''}<label data-conversation-field ${scope==='conversation'?'':'hidden'}>这条提醒对应哪次聊天？<select name="conversationId" ${scope==='conversation'?'':'disabled'}><option value="">请选择聊天</option>${choices.map(c=>`<option value="${esc(c.id)}" ${c.id===conversationId?'selected':''}>${esc(conversationTitle(c))} · ${timeLabel(c.startedAt)}${c.endedAt?'（已结束）':''}</option>`).join('')}</select></label>`;
}
function reminderForm(){return `${parentTabs('memories')}<a class="back-link" href="/memory">← 孩子档案</a><section class="page-heading"><h1>添加讲解提醒</h1></section><section class="panel form-panel"><form id="reminderForm"><label for="reminderTopic">话题名称 <span>（选择“这个话题”时填写）</span></label><input id="reminderTopic" name="topic" maxlength="60">${reminderScopeFields()}<label for="reminderSummary">提醒内容</label><textarea id="reminderSummary" name="summary" maxlength="300" rows="5" required placeholder="希望怎样解释，或有什么需要留意？"></textarea><button class="button primary">保存提醒</button> <a class="button" href="/memory">取消</a><p class="form-message" role="status"></p></form></section>`;}

function conversationDetail(id){
 const conv=state.data.conversations.find(c=>c.id===id),messages=state.data.messages.filter(m=>m.conversationId===id);
 if(!conv)return '<div class="panel empty"><h2>这次聊天已删除或不属于当前档案。</h2><a href="/parent">返回聊天记录</a></div>';
 // Use the newest suggestion in this conversation. A stale one must not reveal an older one.
 const suggested=messages.slice().reverse().find(m=>m.role==='assistant'&&m.suggestion),suggestion=suggested?.suggestionEligible?suggested.suggestion:null;
 return `${parentTabs('chats')}<a href="/parent" class="back-link">← 聊天记录</a><section class="page-heading"><div><h1>${esc(conversationTitle(conv))}</h1><p>${timeLabel(conv.startedAt)} · ${conv.endedAt?'已结束':'正在继续'}</p></div><details class="page-actions"><summary>聊天操作</summary><div><button class="button small" data-action="summarize" data-conversation="${esc(id)}" ${conv.exploration?.status==='pending'?'disabled':''}>${conv.exploration?.status==='pending'?'正在整理':'整理小结'}</button><button class="button small danger" data-action="delete-conversation" data-conversation="${esc(id)}">删除聊天及相关记录</button><p class="muted">删除后可从回收站恢复。</p></div></details></section>${explorationDetail(conv)}${suggestion?`<section class="panel suggestion-panel"><p class="eyebrow">亲子观察 · 家长陪同</p><h2>${esc(suggestion.title)}</h2><p class="suggestion-steps">${esc(suggestion.steps)}</p>${suggestion.why?`<p class="muted">${esc(suggestion.why)}</p>`:''}</section>`:''}<section class="panel transcript"><h2>对话原文</h2>${messages.map(m=>bubble(m,true)).join('')}</section>`;
}
function memoryPage(){
 const params=new URLSearchParams(location.search),id=params.get('memory'),conversation=params.get('conversation');
 if(params.has('newReminder')){shell(reminderForm());return;}
 if(id){const memory=state.data.memories.find(m=>m.id===id);shell(memory?recordDetail(memory):'<section class="panel empty"><h1>这条记录已删除或不属于当前档案。</h1><a href="/memory?trash=1">查看回收站</a></section>');return;}
 if(conversation){shell(conversationDetail(conversation));if(params.has('source')){const source=document.getElementById('source-'+params.get('source'));if(source){source.classList.add('source-highlight');source.tabIndex=-1;source.focus({preventScroll:true});source.scrollIntoView({block:'center'});}}return;}
 if(params.has('trash')){trashPage();return;}
 // Keep old conversation search URLs usable after separating the two lists.
 if(params.has('q')){parentPage();return;}
 const eligible=m=>['observed','parent_confirmed'].includes(m.status)&&!m.evidenceStale&&((m.effectiveScope||m.scope)!=='conversation'||m.conversationId===state.data.activeConversation?.id);
 const live=state.data.memories.filter(eligible),other=state.data.memories.filter(m=>!eligible(m));
 shell(`${parentTabs('memories')}<section class="page-heading"><h1>孩子档案</h1><a class="button small" href="/memory?newReminder=1">添加讲解提醒</a></section><div class="record-list">${live.length?live.map(memoryRow).join(''):'<div class="empty compact"><p>还没有可供参考的记录。</p></div>'}</div>${other.length?`<details class="past-records"><summary>其他记录（${other.length}）</summary><p class="muted">仅用于旧聊天、已撤回或依据已更新的记录。</p><div class="record-list">${other.map(memoryRow).join('')}</div></details>`:''}<a class="secondary-link recycle-link" href="/memory?trash=1">回收站</a>`);
}

async function trashPage(){
 shell('<section class="panel empty"><p>正在查看回收站…</p></section>');
 const epoch=++state.epoch;
 try{const data=await api('/api/trash?childId='+encodeURIComponent(state.id));if(epoch!==state.epoch||!location.search.includes('trash'))return;
 shell(`${parentTabs()}<a href="/memory" class="back-link">← 孩子档案</a><section class="page-heading"><h1>回收站</h1></section><section class="panel">${data.conversations.map(c=>`<div class="trash-row"><span>聊天：${esc(c.title)}</span><button class="button small" data-action="restore-conversation" data-conversation="${esc(c.id)}">恢复聊天</button></div>`).join('')}${data.memories.map(m=>`<div class="trash-row"><span>${esc(m.summary)}</span><button class="button small" data-action="restore-memory" data-memory="${esc(m.id)}" data-version="${esc(m.version)}">放回档案</button></div>`).join('')}${!data.conversations.length&&!data.memories.length?'<p class="muted">回收站里没有记录。</p>':''}</section>`);
 }catch(err){toast(err.message);}
}
function setupPage(){
 const isNew=new URLSearchParams(location.search).has('new')||!current(), p=isNew?{nickname:'',age:5,interests:[],familiarItems:[],explanationPreference:''}:current();
 const archived=state.profiles.filter(p=>p.archived&&(state.testing||p.kind!=='test'));
 shell(`<section class="page-heading"><h1>${isNew?'创建档案':'档案设置'}</h1>${!isNew?'<a class="button small" href="/setup?new=1">新建档案</a>':''}</section><div class="setup-grid"><section class="panel"><form id="profileForm" data-profile="${isNew?'':esc(p.id)}">${state.testing?`<label for="profileKind">资料用途</label><select id="profileKind" name="kind"><option value="demo" ${p.kind!=='test'?'selected':''}>普通合成演示</option><option value="test" ${p.kind==='test'?'selected':''}>开发验证（单独展示）</option></select>`:'<input type="hidden" name="kind" value="demo">'}<div class="form-two"><div><label for="nickname">昵称</label><input id="nickname" name="nickname" maxlength="20" required value="${esc(p.nickname)}" placeholder="输入昵称"></div><div><label for="age">年龄</label><select id="age" name="age">${[4,5,6,7].map(n=>`<option value="${n}" ${n===p.age?'selected':''}>${n}岁</option>`).join('')}</select></div></div><label for="interests">最近喜欢什么 <span>（可选，用逗号分开）</span></label><input id="interests" name="interests" maxlength="200" value="${esc((p.interests||[]).join('，'))}" placeholder="例如：积木、月亮"><label for="familiarItems">熟悉的东西 <span>（可选）</span></label><input id="familiarItems" name="familiarItems" maxlength="200" value="${esc((p.familiarItems||[]).join('，'))}" placeholder="生活里经常见到的物品"><label for="explanationPreference">希望怎样讲 <span>（可选）</span></label><textarea id="explanationPreference" name="explanationPreference" rows="3" maxlength="160" placeholder="自然回答，按需要举例">${esc(p.explanationPreference||'')}</textarea><div class="form-footer"><button class="button primary">${isNew?'创建，开始聊':'保存档案'}</button><p class="form-message" role="status"></p></div></form></section></div>${!isNew?`<details class="data-management"><summary>档案管理</summary><p>删除档案会将它和记录一起移到已删除档案，之后可恢复。</p><button class="button danger" data-action="archive-profile">删除这个档案</button></details>`:''}${archived.length?`<details class="data-management"><summary>已删除档案（${archived.length}）</summary>${archived.map(p=>`<div class="trash-row"><span>${esc(p.nickname)} · ${p.age}岁</span><button class="button small" data-action="restore-profile" data-profile="${esc(p.id)}">恢复档案</button></div>`).join('')}</details>`:''}`);
}
function render(){
 clearTimeout(state.timer);
 if(path()==='/setup'){setupPage();return;}
 if(!current()){shell(welcome());return;}
 if(!state.data){shell(`<section class="panel empty"><h1>${state.connectionError?'档案暂时没有读取成功。':'正在读取这个档案…'}</h1>${state.connectionError?`<p>${esc(state.connectionError)}</p><button class="button primary" data-action="reload">重新连接</button>`:'<p>聊天与提醒会随档案一起切换。</p>'}</section>`);return;}
 if(path()==='/child')childPage();else if(path()==='/parent')parentPage();else memoryPage();
 const editing=Boolean(document.querySelector('#memoryForm,#reminderForm,#profileForm'));
 const viewingSummary=path()==='/parent'||(path()==='/memory'&&new URLSearchParams(location.search).has('conversation'));
 if(!editing&&!state.sending&&(state.data.pending||(viewingSummary&&state.data.conversations.some(c=>c.exploration?.status==='pending'))))state.timer=setTimeout(()=>sync().catch(e=>toast(e.message)),1800);
}
async function sync(){
 const epoch=++state.epoch,id=state.id;
 const [profiles,status,data]=await Promise.all([api('/api/profiles'),api('/api/status'),id?api('/api/state?childId='+encodeURIComponent(id)):Promise.resolve(null)]);
 if(epoch!==state.epoch||id!==state.id)return;
 state.profiles=profiles.profiles;state.status=status;state.data=data;state.connectionError='';invalidateParentView(id);render();
}
async function initialize(){
 try{const [profiles,status]=await Promise.all([api('/api/profiles'),api('/api/status')]);state.profiles=profiles.profiles;state.status=status;
 if(!activeProfiles().some(p=>p.id===state.id)){state.id=activeProfiles()[0]?.id||'';localStorage.setItem(selectionKey,state.id);}
 if(state.id)state.data=await api('/api/state?childId='+encodeURIComponent(state.id));render();
 }catch(err){shell(`<div class="panel empty"><h1>本机服务暂时没有接通。</h1><p>${esc(err.message)}</p><button class="button primary" data-action="reload">重新连接</button></div>`);}
}
async function send(text,style='natural',retryOf=null,feedback=null,feedbackFor=null){
 text=text.trim();if(!text||!state.data||!current()||state.sending||state.data.pending||voice.inputBusy())return;
 voice.stop();
 if('speechSynthesis' in window)window.speechSynthesis.cancel();
 const childId=state.id, requestId='request_'+crypto.randomUUID().replaceAll('-','');
 state.sending=true;state.requestId=requestId;state.controller=new AbortController();
 const origin=!feedback&&!retryOf?JSON.parse(localStorage.getItem(transcriptKey())||'null'):null;
 const body={childId,requestId,userText:text,style,conversationId:state.data.activeConversation?.id||'',retryOf,feedback,feedbackFor,transcriptId:origin?.id};
 localStorage.removeItem(draftKey());
 // Show the real question immediately; the server stores it before model I/O.
 const previous=state.data.messages,previousActive=state.data.activeConversation;state.connectionError='';state.sendError='';
 if(!retryOf)state.data.messages=[...previous,{id:'sending',role:'user',text,status:'pending',createdAt:new Date().toISOString(),conversationId:body.conversationId||'sending'}];
 else state.data.messages=previous.map(m=>m.id===state.data.lastRequest?.userMessageId?{...m,status:'pending'}:m);
 if(!state.data.activeConversation)state.data.activeConversation={id:'sending',title:text};
 render();
 let failure='';const limit=setTimeout(()=>state.controller?.abort(),45000);
 try{const result=await api('/api/chat',body,'POST',state.controller.signal);if(state.id===childId&&result.snapshot){state.data=result.snapshot;localStorage.removeItem(transcriptKey());}}
 catch(err){if(err.name!=='AbortError'){failure=err.message;toast(err.message);}}
 finally{clearTimeout(limit);state.sending=false;state.controller=null;state.requestId=null;if(state.id===childId){try{await sync();if(!state.data.messages.some(m=>m.requestId===requestId)){localStorage.setItem(draftKey(),text);state.sendError=(failure||'问题尚未保存。')+' 文字已回到输入框，可以重新发送。';render();}}catch(err){state.data.messages=previous;state.data.activeConversation=previousActive;state.connectionError=err.message;localStorage.setItem(draftKey(),text);render();toast(err.message);}}}
}
async function stop(){
 voice.stop();if(state.stopping)return;
 const id=state.data?.pending?.id||state.requestId;if(!id)return;
 state.stopping=true;render();
 try{const result=await api('/api/chat/cancel',{childId:state.id,requestId:id});state.controller?.abort();toast(result.status==='completed'?'回答已经完成并保存。':'已停止。问题保留了，之后可以再试。');await sync();}
 catch(err){toast('停止尚未确认：'+err.message);}
 finally{state.stopping=false;render();}
}
async function operate(button,fn){
 if(button.disabled)return;button.disabled=true;
 try{await fn();}catch(err){toast(err.message);}finally{if(button.isConnected)button.disabled=false;}
}
function confirmArchive(nickname){
 const dialog=document.createElement('dialog');dialog.className='confirm-dialog';
 dialog.setAttribute('aria-labelledby','archive-title');
 dialog.innerHTML=`<h2 id="archive-title">删除“${esc(nickname)}”这个档案？</h2><p>聊天和记录会一起移到已删除档案，之后可以恢复。</p><form method="dialog"><button class="button" value="cancel" autofocus>取消</button><button class="button danger" value="confirm">确认删除</button></form>`;
 document.body.append(dialog);
 return new Promise(resolve=>{dialog.addEventListener('close',()=>{const confirmed=dialog.returnValue==='confirm';dialog.remove();resolve(confirmed);},{once:true});dialog.showModal();});
}
async function memoryAction(id,action,expectedVersion,historyIndex){
 const childId=state.id,restoring=action==='restore'||action==='restore_version';
 // Deleted records are absent from the active list: use the version on the clicked record.
 const currentVersion=expectedVersion??(restoring?undefined:state.data.memories.find(m=>m.id===id)?.version);
 if(!/^[1-9]\d*$/.test(String(currentVersion)))throw new Error('请先刷新并查看这条记录的最新版本，再操作。');
 const body={childId,action,expectedVersion:String(currentVersion)};
 if(action==='restore_version'){
  if(!/^\d+$/.test(String(historyIndex)))throw new Error('请先选择要恢复的历史版本。');
  body.historyIndex=Number(historyIndex);
 }
 const key='memory-restore-'+childId+'-'+id,identity=JSON.stringify({id,...body});
 let request=body;
 if(restoring){
  let old;try{old=JSON.parse(localStorage.getItem(key)||'null');}catch{}
  request=old?.identity===identity?old.body:{...body,requestId:'memory_'+crypto.randomUUID().replaceAll('-','')};
  localStorage.setItem(key,JSON.stringify({identity,body:request}));
 }
 const clearPending=()=>{
  if(!restoring)return;
  let saved;try{saved=JSON.parse(localStorage.getItem(key)||'null');}catch{}
  if(saved?.body?.requestId===request.requestId)localStorage.removeItem(key);
 };
 try{
  await api('/api/cards/'+encodeURIComponent(id),request,'PATCH');
  if(state.id===childId)await sync();
  clearPending();
  toast({withdraw:'提醒或记录已撤回，下次回答不再使用。',delete:'记录已删除，可从回收站恢复。',restore:'已恢复记录。',restore_version:'已恢复选定版本。'}[action]||'已保存。');
 }catch(err){
  // Only an unknown outcome needs the same request ID. Definite rejection may be corrected.
  if(err.status>=400&&err.status<500&&![408,429].includes(err.status))clearPending();
  if(err.code==='memory_conflict'&&state.id===childId){try{await sync();}catch{}}
  throw err;
 }
}
app.addEventListener('input',e=>{if(e.target.id==='parentQuestion')localStorage.setItem('parent-input-'+state.id,e.target.value);const parentForm=e.target.closest('.parent-draft-form');if(parentForm)localStorage.setItem('parent-edit-'+state.id+'-'+parentForm.dataset.message,JSON.stringify({...Object.fromEntries(new FormData(parentForm)),expectedVersion:parentForm.dataset.version}));if(e.target.id==='question')localStorage.setItem(draftKey(),e.target.value);});
app.addEventListener('keydown',e=>{if(e.target.id==='question'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();e.target.closest('form').requestSubmit();}});
app.addEventListener('change',async e=>{if(e.target.name==='scope'){const field=e.target.form?.querySelector('[data-conversation-field]');if(field){field.hidden=e.target.value!=='conversation';field.querySelector('select').disabled=field.hidden;}}if(e.target.id==='voiceFile'){await voice.file(e.target.files?.[0]);return;}if(e.target.id!=='profileSelect')return;voice.stop();const next=e.target.value;if(next==='new'){nav('/setup?new=1');return;}if(next===state.id)return; if(state.sending||state.data?.pending){await stop();if(state.data?.pending){e.target.value=state.id;return;}}state.id=next;state.data=null;state.connectionError='';state.sendError='';const target=new URL(location.href);['memory','conversation','source','newReminder','new','record','activity'].forEach(key=>target.searchParams.delete(key));history.replaceState(null,'',target.pathname+target.search);localStorage.setItem(selectionKey,next);render();try{await sync();toast('已切换档案。');}catch(err){state.connectionError=err.message;toast(err.message);render();}});
app.addEventListener('click',async e=>{
 const a=e.target.closest('a[href^="/"]');if(a&&!a.hasAttribute('data-native')){if(e.metaKey||e.ctrlKey||e.shiftKey||e.button!==0)return;e.preventDefault();nav(a.getAttribute('href'));return;}
 const button=e.target.closest('[data-action]');if(!button||e.detail>1)return;
 const action=button.dataset.action;
 if(action.startsWith('parent-')){await parentAction(button);return;}
 if(action==='memory-refresh'){const form=document.querySelector('#memoryForm');const draft=Object.fromEntries(new FormData(form));await sync();const next=document.querySelector('#memoryForm');if(next){for(const [key,value] of Object.entries(draft)){const field=next.elements.namedItem(key);if(field)field.value=value;}next.querySelector('.form-message').textContent='已读取最新内容。草稿已保留，请与当前记录对照后保存。';}return;}
 if(action==='voice-record'){await voice.record();return;}
 if(action==='voice-file'){if(state.sending||state.data?.pending)return;document.querySelector('#voiceFile')?.click();return;}
 if(action==='voice-stop'){voice.stop();return;}
 if(action==='voice-speak'){await voice.speak(button.dataset.message);return;}
 if(action==='voice-recover'){await voice.recover();return;}
 if(action==='voice-retry'){await voice.recognize();return;}
 if(action==='voice-dismiss'){localStorage.removeItem(voice.taskKey());voice.stop();render();return;}
 if(action==='voice-text'){localStorage.removeItem(transcriptKey());voice.stop();render();return;}

 if(action==='read-more'){const log=document.querySelector('.chat-log');log.scrollBy({top:log.clientHeight*.8,behavior:'auto'});return;}
 if(action==='feedback'){const last=sessionMessages().slice(-1)[0];await send(button.dataset.text,button.dataset.style,null,button.dataset.feedback,last?.id);return;}
 if(action==='retry'){const r=state.data.lastRequest;await send(r.text,r.style,r.id,r.feedback,r.feedbackFor);return;}
 if(action==='stop'){await stop();return;}
 if(action==='reload'){state.connectionError='';await initialize();return;}
 if(action==='enter-test'||action==='exit-test'){state.testing=action==='enter-test';localStorage.setItem('curiosity-test-mode',state.testing?'1':'0');if(!activeProfiles().some(p=>p.id===state.id)){state.id=activeProfiles()[0]?.id||'';localStorage.setItem(selectionKey,state.id);}await sync();nav(state.testing?'/setup':'/child');return;}
 await operate(button,async()=>{
  if(action==='new-session'){voice.stop();if(state.sending||state.data.pending)await stop();if(state.data.pending)return;await api('/api/conversations/end',{childId:state.id,conversationId:state.data.activeConversation.id});await sync();toast('这次聊天已保存。可以问一个新的问题了。');}
  else if(action==='summarize'){const result=await api('/api/conversations/summary',{childId:state.id,conversationId:button.dataset.conversation});await sync();toast(result.message||(result.status==='pending'?'正在整理，完成后会出现在这里。':'探索记录已经是当前版本。'));}
  else if(action.endsWith('-memory'))await memoryAction(button.dataset.memory,{'withdraw-memory':'withdraw','delete-memory':'delete','restore-memory':'restore','restore-version-memory':'restore_version'}[action],button.dataset.version,button.dataset.historyIndex);
  else if(action==='delete-conversation'||action==='restore-conversation'){await api('/api/conversations/delete',{childId:state.id,conversationId:button.dataset.conversation,restore:action==='restore-conversation'});await sync();nav(action==='delete-conversation'?'/parent':'/memory?conversation='+encodeURIComponent(button.dataset.conversation));toast(action==='delete-conversation'?'聊天和相关记录已删除，可从回收站恢复。':'会话已恢复。');}
  else if(action==='archive-profile'){const childId=state.id;if(!await confirmArchive(current().nickname)||state.id!==childId)return;if(state.data?.pending||state.sending)await stop();await api('/api/profiles/archive',{childId});state.id='';state.data=null;localStorage.removeItem(selectionKey);await initialize();nav('/setup?new=1');toast('档案已删除，可以从已删除档案恢复。');}
  else if(action==='restore-profile'){await api('/api/profiles/archive',{childId:button.dataset.profile,restore:true});state.id=button.dataset.profile;state.testing=state.profiles.find(p=>p.id===state.id)?.kind==='test';localStorage.setItem('curiosity-test-mode',state.testing?'1':'0');localStorage.setItem(selectionKey,state.id);await sync();nav('/setup');toast('档案和记录已恢复。');}
 });
});
app.addEventListener('submit',async e=>{
 e.preventDefault();const form=e.target;const data=Object.fromEntries(new FormData(form));
 if(form.id==='parentAskForm'){await parentSend(data.text);return;}
 if(form.classList.contains('parent-draft-form')){await parentSave(form);return;}
 if(form.id==='chatForm'){await send(data.question);return;}
 if(form.id==='searchForm'){nav('/parent'+(data.q?'?q='+encodeURIComponent(data.q):''));return;}
 const button=form.querySelector('button[type="submit"],button');if(button.disabled)return;button.disabled=true;
 const feedback=form.querySelector('.form-message');if(feedback)feedback.textContent='正在保存…';
 try{
  if(form.id==='profileForm'){const profile=await api('/api/profiles',{...data,id:form.dataset.profile||undefined});state.id=profile.id;state.testing=profile.kind==='test';localStorage.setItem('curiosity-test-mode',state.testing?'1':'0');localStorage.setItem(selectionKey,state.id);await sync();if(!form.dataset.profile)nav('/child');toast('档案已保存。下一次回答会读取最新资料。');}
  else if(form.id==='memoryForm'){await api('/api/cards/'+encodeURIComponent(form.dataset.memory),{...data,childId:state.id,action:'edit',expectedVersion:form.dataset.version},'PATCH');await sync();toast('修改已保存，将按适用范围参考这条记录。');}
  else if(form.id==='reminderForm'){const memory=await api('/api/reminders',{...data,childId:state.id});await sync();nav('/memory?memory='+encodeURIComponent(memory.id));toast('提醒已保存。');}
 }catch(err){if(feedback){feedback.textContent=err.message;if(err.code==='memory_conflict')feedback.insertAdjacentHTML('beforeend',' <button type="button" class="button small" data-action="memory-refresh">查看最新内容，保留草稿</button>');}toast(err.message);}finally{if(button.isConnected)button.disabled=false;}
});
window.addEventListener('popstate',()=>{voice.stop();render();});
window.addEventListener('beforeunload',()=>voice.stop(false));
initialize();
