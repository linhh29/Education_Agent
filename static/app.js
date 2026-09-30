const app = document.querySelector('#app');
const esc = (value='') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const selectionKey = 'curiosity-selected-profile-v2';
const state = {id:localStorage.getItem(selectionKey)||'', profiles:[], data:null, status:null, sending:false, stopping:false, requestId:null, controller:null, timer:null, epoch:0, trash:null, testing:localStorage.getItem('curiosity-test-mode')==='1', connectionError:'', sendError:''};
const kindNames = {confusion:'还想弄明白', understanding:'这次表达的理解', preference:'明确表达的偏好', reminder:'家长提醒'};
const statusNames = {observed:'来自孩子原话 · AI整理', parent_confirmed:'家长已修订', withdrawn:'已撤回 · 回答不再使用', deleted:'已删除'};
const timeLabel = value => value ? new Intl.DateTimeFormat('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '';
const draftKey = () => 'curiosity-draft-'+state.id;
function toast(text){ let el=document.querySelector('#toast'); if(!el){el=document.createElement('div'); el.id='toast'; el.setAttribute('role','status'); document.body.append(el);} el.textContent=text; el.className='toast visible'; clearTimeout(el.timer); el.timer=setTimeout(()=>el.className='toast',4200); }
async function api(path,body,method='POST',signal){
 const controller = signal ? null : new AbortController(); const timer = controller ? setTimeout(()=>controller.abort(),35000) : null;
 try{const response=await fetch(path,{method:body===undefined?'GET':method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:signal||controller.signal}); const data=await response.json(); if(!response.ok){const err=new Error(data.message||'操作未完成，请重试。');err.code=data.error;throw err;}return data;}
 catch(err){if(err.name==='AbortError') throw err; if(err instanceof TypeError) throw new Error('本机服务没有连接上，请检查启动窗口后刷新。');throw err;} finally{clearTimeout(timer);}
}
const activeProfiles = () => state.profiles.filter(p=>!p.archived&&(p.kind!=='test'||state.testing));
const current = () => state.profiles.find(p=>p.id===state.id);
const path = () => location.pathname==='/'?'/child':location.pathname;
function nav(url){history.pushState(null,'',url); render();window.scrollTo({top:0});}
function shell(content){
 const profile=current(); const page=path();
 app.innerHTML=`<header class="site-header"><div class="header-inner"><a href="/child" class="brand" aria-label="好奇心伙伴"><span class="brand-mark" aria-hidden="true">✦</span><span>好奇心伙伴<small>陪孩子把为什么问下去</small></span></a><nav aria-label="主导航"><a href="/child" ${page==='/child'?'aria-current="page"':''}>一起聊</a><a href="/parent" ${page==='/parent'?'aria-current="page"':''}>家长看看</a><a href="/memory" ${page==='/memory'?'aria-current="page"':''}>记录</a></nav><div class="profile-control"><label class="sr-only" for="profileSelect">当前演示档案</label><select id="profileSelect"><option value="" ${!profile?'selected':''}>选择演示档案</option>${activeProfiles().map(p=>`<option value="${esc(p.id)}" ${p.id===state.id?'selected':''}>${esc(p.nickname)} · ${p.age}岁</option>`).join('')}<option value="new">＋ 新建档案</option></select><a href="/setup" class="icon-link" aria-label="编辑档案" title="编辑档案">⚙</a></div></div></header><main id="main">${state.testing?'<div class="test-banner">开发验证空间 · 合成测试资料 <button data-action="exit-test">回普通演示</button></div>':''}${content}</main><footer><span>AI对话伙伴 · 本机演示，请只填写合成资料</span><details class="runtime"><summary>运行说明</summary><div class="runtime-content"><p>当前使用 ${esc(state.status?.model||'指定模型')}，${esc(state.status?.mode||'真实文字')}。AI可能出错，重要问题请家长核对。</p><p>对话保存在本机。问答所需的合成资料会发送到 DashScope。麦克风及云语音未启用；设备朗读为可选辅助，当前环境未验证。</p><p>预算占用 ${state.status?'约 ¥'+Number(state.status.occupiedCny).toFixed(4)+' / ¥'+state.status.limitCny:'尚未读取'}（按官方价估算，未知用量保留上界）。</p><a href="/video-demo" data-native>历史录制示例（预设内容，非本次真实对话）</a></div></details></footer>`;
}
function welcome(){return `<div class="empty welcome"><span class="small-spark" aria-hidden="true">✦</span><h1>一个小小的为什么，<br>也值得认真聊。</h1><p>建一个演示档案，就可以自由提问。<br>没有预设对话，从孩子真正想问的事开始。</p><a class="button primary" href="/setup?new=1">创建演示档案</a></div>`;}
function sessionMessages(){const conv=state.data?.activeConversation;return conv?state.data.messages.filter(m=>m.conversationId===conv.id):[];}
function bubble(message,detail=false){return `<article class="message ${message.role}" id="source-${esc(message.id)}"><div class="speaker">${message.role==='user'?esc(current()?.nickname||'孩子'):'✦ 好奇心伙伴'}${detail?`<time>${timeLabel(message.createdAt)}</time>`:''}</div><div class="message-text">${esc(message.text)}</div>${message.role==='user'&&['failed','cancelled','pending'].includes(message.status)?`<small class="message-state">${{failed:'这次还没回答成功',cancelled:'已停止，问题仍然保留',pending:'正在回答这个问题'}[message.status]}</small>`:''}${detail&&message.usedMemoryEvidence?.length?`<details class="answer-evidence"><summary>这次讲法参考了哪些提醒或原话</summary>${message.usedMemoryEvidence.map(m=>`<p>${esc(kindNames[m.kind]||'线索')}：${esc(m.summary)}</p>`).join('')}<small>这是回答时的资料快照；当前修改、撤回或删除会影响后续回答。</small></details>`:''}${message.needsParent?'<small class="safety-note">这件事需要家长陪你一起处理。</small>':''}${message.role==='assistant'&&'speechSynthesis' in window?`<button class="read-button" data-action="speak" data-message="${esc(message.id)}">本机朗读 <span>（未验证）</span></button>`:''}</article>`;}
function childPage(){
 const data=state.data, profile=current(), messages=sessionMessages();
 const busy=state.sending||Boolean(data.pending), last=data.lastRequest;
 const retryable=last&&['failed','cancelled'].includes(last.status);
 const hasAnswer=messages.some(m=>m.role==='assistant');
 shell(`<section class="page-heading child-heading"><div><p class="eyebrow">一起聊 · ${esc(profile.nickname)}的好奇时间</p><h1>今天，你想弄明白什么？</h1><p>可以接着问，也可以随时换个话题。</p></div><button class="button subtle" data-action="new-session" ${!data.activeConversation||state.stopping?'disabled':''}>结束这次，开始新的</button></section><div class="child-grid"><section class="chat-card" aria-label="对话"><div class="conversation-meta"><span><i class="status-dot"></i>真实 AI 对话</span><span>${data.activeConversation?'这次聊天':'新会话'} · ${esc(profile.age)}岁</span></div><div class="chat-log" role="log" aria-live="polite" aria-relevant="additions text">${messages.length?messages.map(m=>bubble(m)).join(''):`<div class="chat-welcome"><span aria-hidden="true">✦</span><h2>你好，${esc(profile.nickname)}。</h2><p>把你的问题告诉我，我们一起找答案。<br>哪里没听懂，也可以直接说。</p></div>`}${busy?'<div class="thinking" role="status"><span class="status-dot"></span>正在想怎么讲清楚… <small>通常需要几秒，可以随时停止</small></div>':''}</div>${state.sendError?`<div class="request-notice" role="alert">${esc(state.sendError)}</div>`:''}${state.connectionError?`<div class="request-notice" role="alert">${esc(state.connectionError)}<button class="button small" data-action="reload">重新连接</button></div>`:''}${retryable&&!busy?`<div class="request-notice" role="status"><span>${esc(last.error||(last.status==='cancelled'?'已停止这次回答。问题保留了。':'还没有完成这次回答。'))}</span><button class="button small" data-action="retry">再试一次</button></div>`:''}<div class="composer-area">${hasAnswer?`<div class="feedback" aria-label="换个讲法"><button data-action="feedback" data-text="我还是没听懂，可以换个讲法吗？">没听懂</button><button data-action="feedback" data-text="可以讲得简单一点吗？" data-style="simpler">简单一点</button><button data-action="feedback" data-text="可以举一个例子吗？" data-style="example">举个例子</button><button data-action="feedback" data-text="我想听得更详细一点。" data-style="detail">详细一点</button></div>`:''}<form id="chatForm"><label class="sr-only" for="question">你想问什么？</label><textarea id="question" name="question" rows="2" maxlength="800" placeholder="你想问什么？也可以接着刚才说…" ${busy?'disabled':''}>${esc(localStorage.getItem(draftKey())||'')}</textarea><div class="composer-bottom"><span>文字提问 · Enter 发送，Shift + Enter 换行</span>${busy?`<button type="button" class="button stop" data-action="stop" ${state.stopping?'disabled':''}>${state.stopping?'正在停止…':'停止回答'}</button>`:'<button type="submit" class="button primary">发送 <span aria-hidden="true">↗</span></button>'}</div></form></div></section><aside class="child-aside"><div class="aside-note"><p class="eyebrow">小问题，大世界</p><h2>想知道，就问吧。</h2><p>你可以说“为什么”，<br>也可以说“我没听懂”。<br>不用答对什么，慢慢聊就好。</p><div class="orbit-art" aria-hidden="true"><span>?</span><i></i><b>✦</b></div></div><div class="aside-links"><b>每一次好奇，都有来处</b><p>家长可以回看原话，调整记录和提醒。</p><a href="/parent">去家长端看看 <span>→</span></a></div></aside></div>`);
 document.querySelectorAll('.feedback button').forEach(b=>b.disabled=busy);
 const log=document.querySelector('.chat-log');
 log.tabIndex=0;log.setAttribute('aria-label','对话记录，可上下滚动查看');
 const latestQuestion=Array.from(log.querySelectorAll('.message.user')).pop();
 if(busy)log.scrollTop=log.scrollHeight;
 else if(latestQuestion)log.scrollTop+=latestQuestion.getBoundingClientRect().top-log.getBoundingClientRect().top-16;
 const more=document.createElement('button');more.type='button';more.className='read-more';more.dataset.action='read-more';more.textContent='继续往下看 ↓';log.after(more);
 const showMore=()=>more.hidden=log.scrollTop+log.clientHeight>=log.scrollHeight-8;
 log.addEventListener('scroll',showMore);showMore();
}
function memoryCard(memory){return `<article class="memory-card ${memory.status==='withdrawn'?'withdrawn':''}"><div class="item-heading"><span class="tag">${kindNames[memory.kind]||'记录'}</span><time>${timeLabel(memory.updatedAt)}</time></div><h3>${esc(memory.topic||'日常讲法')}</h3><p>${esc(memory.summary)}</p>${memory.kind==='reminder'?'<small>家长填写的提醒</small>':`<blockquote>“${esc(memory.quote)}”</blockquote>`}<div class="item-bottom"><small>${statusNames[memory.status]||'历史记录'}</small><a href="/memory?memory=${encodeURIComponent(memory.id)}">查看与修改 →</a></div></article>`;}
function recentQuestions(){return state.data.messages.filter(m=>m.role==='user').slice(-6).reverse();}
function parentPage(){
 const data=state.data, questions=data.messages.filter(m=>m.role==='user'), recent=recentQuestions();
 const memories=data.memories.filter(m=>m.kind!=='reminder').slice(0,4), reminders=data.memories.filter(m=>m.kind==='reminder');
 const topics=[...new Set(recent.map(m=>m.topic).filter(Boolean))].slice(0,5);
 const suggestion=data.suggestion;
 shell(`<section class="page-heading"><div><p class="eyebrow">家长看看 · ${esc(current().nickname)}</p><h1>好奇心，留下了这些线索。</h1><p>从真实原话了解最近在想什么，判断不合适时可以修改。</p></div><a href="/child" class="button primary">回到一起聊 →</a></section><div class="parent-grid"><section class="panel recent-panel"><div class="section-heading"><div><p class="eyebrow">近期关注</p><h2>最近问了些什么</h2></div><a href="/memory">全部记录 →</a></div>${topics.length?`<div class="topics">${topics.map(t=>`<span>${esc(t)}</span>`).join('')}</div>`:''}<div class="question-list">${recent.length?recent.map(q=>`<a class="question-row" href="/memory?conversation=${encodeURIComponent(q.conversationId)}&source=${encodeURIComponent(q.id)}"><span>${esc(q.text)}</span><small>${timeLabel(q.createdAt)}${q.status==='failed'?' · 尚未回答':''}</small><b aria-hidden="true">↗</b></a>`).join(''):'<div class="empty compact"><p>还没有聊天记录。</p><a href="/child">去问第一个问题 →</a></div>'}</div><p class="section-footnote">共 ${questions.length} 次提问 · ${data.conversations.length} 次会话。以下线索是本机合成演示资料。</p></section><section class="panel suggestion-panel"><p class="eyebrow">一起发现</p><h2>${suggestion?esc(suggestion.title):'等一个合适的观察'}</h2>${suggestion?`<a class="suggestion-source" href="/memory?conversation=${encodeURIComponent(suggestion.conversationId)}">来自「${esc(suggestion.topic)}」的聊天 →</a><p class="suggestion-steps">${esc(suggestion.steps)}</p><p class="muted">${esc(suggestion.why)}</p><small>家长陪同 · AI建议，操作前先确认环境安全</small>`:'<p>有适合当前话题的安全小观察时，这里会留下一项建议。不必每次聊天都做活动。</p>'}</section></div><section class="section-block"><div class="section-heading"><div><p class="eyebrow">原话与理解</p><h2>孩子表达了什么</h2></div><span class="muted">解释过，不等于理解了</span></div><div class="memory-grid">${memories.length?memories.map(memoryCard).join(''):'<div class="panel empty compact"><p>还没有足够的表达依据。</p><small>孩子明确说出了困惑、理解或偏好，才会留下这类记录。</small></div>'}</div></section><section class="section-block"><div class="section-heading"><div><p class="eyebrow">家长的小提醒</p><h2>下一次，可以这样讲</h2></div><a class="button small" href="/memory?newReminder=1">添加提醒 ＋</a></div><p class="muted">写下具体话题和希望采用的讲法。提醒影响表达，不改变科学事实。</p><div class="memory-grid">${reminders.length?reminders.map(memoryCard).join(''):'<div class="panel empty compact"><p>暂时没有提醒，也可以自然聊。</p></div>'}</div></section>`);
}
function sourceMessages(memory){return state.data.messages.filter(m=>memory.sourceMessageIds.includes(m.id));}
function recordDetail(memory){
 const messages=sourceMessages(memory), suggestion=messages.find(m=>m.suggestion)?.suggestion;
 return `<a class="back-link" href="/parent">← 回家长看看</a><section class="page-heading"><div><p class="eyebrow">记录详情 · ${kindNames[memory.kind]||'记录'}</p><h1>${esc(memory.topic||'日常讲法')}</h1><p>${statusNames[memory.status]||'记录已保存'} · ${timeLabel(memory.updatedAt)}</p></div></section><div class="detail-grid"><section class="panel"><h2>${memory.kind==='reminder'?'家长填写的提醒':'判断的原话依据'}</h2>${messages.length?messages.map(m=>bubble(m,true)).join(''):`<blockquote>“${esc(memory.quote)}”</blockquote><p class="muted">这是一项家长提醒，未据此判断孩子的理解。</p>`}${suggestion?`<details class="inline-details"><summary>当时的亲子建议</summary><p>${esc(suggestion.steps)}</p></details>`:''}</section><section class="panel edit-panel"><h2>修改这条${memory.kind==='reminder'?'提醒':'记录'}</h2><p class="muted">${memory.kind==='reminder'?'下一次相关问题会读取最新提醒。':'请根据真实表达修改，不用把孩子标为“已掌握”。家长修订与原始对话分别保留。'}</p><form id="memoryForm" data-memory="${esc(memory.id)}">${memory.kind==='reminder'?`<label for="memoryTopic">适用话题 <span>（留空表示一般讲法）</span></label><input id="memoryTopic" name="topic" maxlength="60" value="${esc(memory.topic)}">`:''}<label for="memorySummary">${memory.kind==='reminder'?'提醒内容':'对这次表达的理解'}</label><textarea id="memorySummary" name="summary" rows="5" maxlength="300" required>${esc(memory.summary)}</textarea><button class="button primary" type="submit">保存修改</button><p class="form-message" role="status"></p></form><div class="record-actions"><button data-action="withdraw-memory" data-memory="${esc(memory.id)}" ${memory.status==='withdrawn'?'disabled':''}>撤回，不再用于回答</button><button data-action="restore-memory" data-memory="${esc(memory.id)}" ${memory.history.length?'':'disabled'}>恢复上个版本</button><button class="text-danger" data-action="delete-memory" data-memory="${esc(memory.id)}">删除记录</button></div><small>删除这条记录后可从回收站恢复，原始对话仍保留。删除来源会话会同时移除相关原话与记忆。</small></section></div>`;
}
function reminderForm(){return `<a class="back-link" href="/parent">← 回家长看看</a><section class="page-heading"><div><p class="eyebrow">家长提醒</p><h1>把一个小提醒，留给下次。</h1><p>具体一点，更容易在相关问题里派上用场。</p></div></section><section class="panel form-panel"><form id="reminderForm"><label for="reminderTopic">适用话题 <span>（可选）</span></label><input id="reminderTopic" name="topic" maxlength="60" placeholder="例如：影子、植物，或留空表示一般讲法"><label for="reminderSummary">提醒内容</label><textarea id="reminderSummary" name="summary" maxlength="300" rows="5" required placeholder="写下希望怎样解释、孩子熟悉的东西，或需要留意的困惑。"></textarea><p class="muted">提醒帮助调整讲法；AI仍需遵守科学事实与儿童安全边界。</p><button class="button primary">保存提醒</button><p class="form-message" role="status"></p></form></section>`;}
function conversationDetail(id,source){
 const conv=state.data.conversations.find(c=>c.id===id), messages=state.data.messages.filter(m=>m.conversationId===id);
 if(!conv)return '<div class="panel empty"><h2>这条会话已删除或不属于当前档案。</h2><a href="/memory">返回记录</a></div>';
 return `<a href="/memory" class="back-link">← 返回全部记录</a><section class="page-heading"><div><p class="eyebrow">完整对话 · ${timeLabel(conv.startedAt)}</p><h1>${esc(conv.title)}</h1><p>${conv.endedAt?'这次会话已结束':'这次会话正在继续'} · 原话与AI回答</p></div><button class="button danger" data-action="delete-conversation" data-conversation="${esc(id)}">删除这次会话</button></section><section class="panel transcript">${messages.map(m=>bubble(m,true)).join('')}</section>`;
}
function memoryPage(){
 const params=new URLSearchParams(location.search), id=params.get('memory'), conversation=params.get('conversation');
 if(params.has('newReminder')){shell(reminderForm());return;}
 if(id){const memory=state.data.memories.find(m=>m.id===id);shell(memory?recordDetail(memory):'<section class="panel empty"><h1>这条记录已删除或不属于当前档案。</h1><a href="/memory?trash=1">查看回收站</a></section>');return;}
 if(conversation){shell(conversationDetail(conversation,params.get('source')));if(params.has('source'))document.getElementById('source-'+params.get('source'))?.scrollIntoView({block:'center'});return;}
 if(params.has('trash')){trashPage();return;}
 const q=(params.get('q')||'').trim(), all=state.data.conversations;
 const sessions=all.filter(c=>!q||state.data.messages.some(m=>m.conversationId===c.id&&m.text.includes(q)));
 shell(`<section class="page-heading"><div><p class="eyebrow">记录 · ${esc(current().nickname)}</p><h1>每个问题，都能回到原话。</h1><p>会话、理解线索和家长提醒保存在本机，刷新或重启仍然保留。</p></div><a class="button small" href="/memory?trash=1">回收站</a></section><div class="records-grid"><section class="panel"><div class="section-heading"><h2>完整会话</h2><span class="muted">${all.length} 次</span></div><form id="searchForm" class="search-form"><label class="sr-only" for="search">搜索对话</label><input id="search" name="q" value="${esc(q)}" placeholder="搜索孩子问题或回答"><button class="button small">搜索</button></form>${sessions.length?sessions.map(c=>`<a class="session-row" href="/memory?conversation=${encodeURIComponent(c.id)}"><span><b>${esc(c.title||'一次好奇对话')}</b><small>${timeLabel(c.startedAt)} · ${c.endedAt?'已结束':'正在继续'}</small></span><span>→</span></a>`).join(''):`<div class="empty compact"><p>${q?'没有找到相关对话。':'还没有聊天记录。'}</p><a href="${q?'/memory':'/child'}">${q?'查看全部记录':'去问一个问题'}</a></div>`}</section><section><div class="section-heading"><h2>原话与提醒</h2><a href="/memory?newReminder=1">添加提醒 ＋</a></div><div class="memory-stack">${state.data.memories.length?state.data.memories.map(memoryCard).join(''):'<div class="panel empty compact"><p>还没有记录或提醒。</p><small>只留下有原话依据的少量线索。</small></div>'}</div></section></div>`);
}
async function trashPage(){
 shell('<section class="panel empty"><p>正在查看回收站…</p></section>');
 const epoch=++state.epoch;
 try{const data=await api('/api/trash?childId='+encodeURIComponent(state.id));if(epoch!==state.epoch||!location.search.includes('trash'))return;
 shell(`<a href="/memory" class="back-link">← 返回记录</a><section class="page-heading"><div><p class="eyebrow">回收站</p><h1>删去的记录，可以恢复。</h1><p>这里的内容不会进入下一次回答。</p></div></section><section class="panel">${data.conversations.map(c=>`<div class="trash-row"><span>会话：${esc(c.title)}</span><button class="button small" data-action="restore-conversation" data-conversation="${esc(c.id)}">恢复会话</button></div>`).join('')}${data.memories.map(m=>`<div class="trash-row"><span>${esc(m.summary)}</span><button class="button small" data-action="restore-memory" data-memory="${esc(m.id)}">恢复记录</button></div>`).join('')}${!data.conversations.length&&!data.memories.length?'<p class="muted">回收站里没有记录。</p>':''}</section>`);
 }catch(err){toast(err.message);}
}
function setupPage(){
 const isNew=new URLSearchParams(location.search).has('new')||!current(), p=isNew?{nickname:'',age:5,interests:[],familiarItems:[],explanationPreference:''}:current();
 const archived=state.profiles.filter(p=>p.archived);
 shell(`<section class="page-heading"><div><p class="eyebrow">演示档案</p><h1>${isNew?'从一个空白档案开始。':'陪伴，也可以随时调整。'}</h1><p>面向4–7岁儿童的本机演示。请使用合成昵称与资料，不填写真实隐私。</p></div>${!isNew?'<a class="button small" href="/setup?new=1">新建档案 ＋</a>':''}</section><div class="setup-grid"><section class="panel"><form id="profileForm" data-profile="${isNew?'':esc(p.id)}"><label for="profileKind">资料用途</label><select id="profileKind" name="kind"><option value="demo" ${p.kind!=='test'?'selected':''}>普通合成演示</option><option value="test" ${p.kind==='test'?'selected':''}>开发验证（单独展示）</option></select><div class="form-two"><div><label for="nickname">昵称</label><input id="nickname" name="nickname" maxlength="20" required value="${esc(p.nickname)}" placeholder="给演示孩子起一个昵称"></div><div><label for="age">年龄</label><select id="age" name="age">${[4,5,6,7].map(n=>`<option value="${n}" ${n===p.age?'selected':''}>${n}岁</option>`).join('')}</select></div></div><label for="interests">最近喜欢什么 <span>（可选，用逗号分开）</span></label><input id="interests" name="interests" maxlength="200" value="${esc((p.interests||[]).join('，'))}" placeholder="例如：积木、月亮"><label for="familiarItems">熟悉的东西 <span>（可选）</span></label><input id="familiarItems" name="familiarItems" maxlength="200" value="${esc((p.familiarItems||[]).join('，'))}" placeholder="生活里经常见到的物品"><label for="explanationPreference">希望怎样讲 <span>（可选）</span></label><textarea id="explanationPreference" name="explanationPreference" rows="3" maxlength="160" placeholder="自然回答，按需要举例">${esc(p.explanationPreference||'')}</textarea><div class="form-footer"><button class="button primary">${isNew?'创建，开始聊':'保存档案'}</button><p class="form-message" role="status"></p></div></form></section><aside class="panel setup-note"><p class="eyebrow">自己的好奇时间</p><h2>每个档案，<br>各自记住。</h2><p>聊天、理解线索与家长提醒按档案分别保存。</p><p>年龄、熟悉事物和讲法更新后，下一次回答会读取最新资料。</p><p class="muted">档案不是测评。AI提供解释与陪伴，不能代替家长照看。</p></aside></div>${!isNew?`<details class="data-management"><summary>档案管理</summary><p>删除档案会将它和记录一起移到已删除档案，之后可恢复。</p><button class="button danger" data-action="archive-profile">删除这个演示档案</button></details>`:''}<details class="data-management"><summary>开发验证资料</summary><p>独立于普通演示的测试档案，只在开发验证空间显示。</p><button class="button small" data-action="enter-test">进入开发验证空间</button></details>${archived.length?`<details class="data-management"><summary>已删除档案（${archived.length}）</summary>${archived.map(p=>`<div class="trash-row"><span>${esc(p.nickname)} · ${p.age}岁</span><button class="button small" data-action="restore-profile" data-profile="${esc(p.id)}">恢复档案</button></div>`).join('')}</details>`:''}`);
}
function render(){
 clearTimeout(state.timer);
 if(path()==='/setup'){setupPage();return;}
 if(!current()){shell(welcome());return;}
 if(!state.data){shell(`<section class="panel empty"><h1>${state.connectionError?'档案暂时没有读取成功。':'正在读取这个档案…'}</h1>${state.connectionError?`<p>${esc(state.connectionError)}</p><button class="button primary" data-action="reload">重新连接</button>`:'<p>聊天与提醒会随档案一起切换。</p>'}</section>`);return;}
 if(path()==='/child')childPage();else if(path()==='/parent')parentPage();else memoryPage();
 if(state.data.pending&&!state.sending)state.timer=setTimeout(()=>sync().catch(e=>toast(e.message)),1800);
}
async function sync(){
 const epoch=++state.epoch,id=state.id;
 const [profiles,status,data]=await Promise.all([api('/api/profiles'),api('/api/status'),id?api('/api/state?childId='+encodeURIComponent(id)):Promise.resolve(null)]);
 if(epoch!==state.epoch||id!==state.id)return;
 state.profiles=profiles.profiles;state.status=status;state.data=data;state.connectionError='';render();
}
async function initialize(){
 try{const [profiles,status]=await Promise.all([api('/api/profiles'),api('/api/status')]);state.profiles=profiles.profiles;state.status=status;
 if(!activeProfiles().some(p=>p.id===state.id)){state.id=activeProfiles()[0]?.id||'';localStorage.setItem(selectionKey,state.id);}
 if(state.id)state.data=await api('/api/state?childId='+encodeURIComponent(state.id));render();
 }catch(err){shell(`<div class="panel empty"><h1>本机服务暂时没有接通。</h1><p>${esc(err.message)}</p><button class="button primary" data-action="reload">重新连接</button></div>`);}
}
async function send(text,style='natural',retryOf=null){
 text=text.trim();if(!text||!state.data||!current()||state.sending||state.data.pending)return;
 if('speechSynthesis' in window)window.speechSynthesis.cancel();
 const childId=state.id, requestId='request_'+crypto.randomUUID().replaceAll('-','');
 state.sending=true;state.requestId=requestId;state.controller=new AbortController();
 const body={childId,requestId,userText:text,style,conversationId:state.data.activeConversation?.id||'',retryOf};
 localStorage.removeItem(draftKey());
 // Show the real question immediately; the server stores it before model I/O.
 const previous=state.data.messages,previousActive=state.data.activeConversation;state.connectionError='';state.sendError='';
 if(!retryOf)state.data.messages=[...previous,{id:'sending',role:'user',text,status:'pending',createdAt:new Date().toISOString(),conversationId:body.conversationId||'sending'}];
 else state.data.messages=previous.map(m=>m.id===state.data.lastRequest?.userMessageId?{...m,status:'pending'}:m);
 if(!state.data.activeConversation)state.data.activeConversation={id:'sending',title:text};
 render();
 let failure='';const limit=setTimeout(()=>state.controller?.abort(),35000);
 try{const result=await api('/api/chat',body,'POST',state.controller.signal);if(state.id===childId&&result.snapshot)state.data=result.snapshot;}
 catch(err){if(err.name!=='AbortError'){failure=err.message;toast(err.message);}}
 finally{clearTimeout(limit);state.sending=false;state.controller=null;state.requestId=null;if(state.id===childId){try{await sync();if(!state.data.messages.some(m=>m.requestId===requestId)){localStorage.setItem(draftKey(),text);state.sendError=(failure||'问题尚未保存。')+' 文字已回到输入框，可以重新发送。';render();}}catch(err){state.data.messages=previous;state.data.activeConversation=previousActive;state.connectionError=err.message;localStorage.setItem(draftKey(),text);render();toast(err.message);}}}
}
async function stop(){
 if(state.stopping)return;
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
async function memoryAction(id,action){await api('/api/cards/'+encodeURIComponent(id),{childId:state.id,action},'PATCH');await sync();toast({withdraw:'提醒或记录已撤回，下次回答不再使用。',delete:'记录已删除，可从回收站恢复。',restore:'已恢复之前的版本。'}[action]||'已保存。');}
app.addEventListener('input',e=>{if(e.target.id==='question')localStorage.setItem(draftKey(),e.target.value);});
app.addEventListener('keydown',e=>{if(e.target.id==='question'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();e.target.closest('form').requestSubmit();}});
app.addEventListener('change',async e=>{if(e.target.id!=='profileSelect')return;const next=e.target.value;if(next==='new'){nav('/setup?new=1');return;}if(next===state.id)return; if(state.sending||state.data?.pending){await stop();if(state.data?.pending){e.target.value=state.id;return;}}state.id=next;state.data=null;state.connectionError='';state.sendError='';localStorage.setItem(selectionKey,next);render();try{await sync();toast('已切换档案。');}catch(err){state.connectionError=err.message;toast(err.message);render();}});
app.addEventListener('click',async e=>{
 const a=e.target.closest('a[href^="/"]');if(a&&!a.hasAttribute('data-native')){if(e.metaKey||e.ctrlKey||e.shiftKey||e.button!==0)return;e.preventDefault();nav(a.getAttribute('href'));return;}
 const button=e.target.closest('[data-action]');if(!button)return;
 const action=button.dataset.action;
 if(action==='read-more'){const log=document.querySelector('.chat-log');log.scrollBy({top:log.clientHeight*.8,behavior:'auto'});return;}
 if(action==='feedback'){await send(button.dataset.text,button.dataset.style);return;}
 if(action==='retry'){const r=state.data.lastRequest;await send(r.text,r.style,r.id);return;}
 if(action==='stop'){await stop();return;}
 if(action==='speak'){const message=state.data.messages.find(m=>m.id===button.dataset.message);if(!message)return;const voice=window.speechSynthesis.getVoices().find(v=>v.localService&&v.lang.startsWith('zh'));if(!voice){toast('没有配置本机中文朗读，可以继续看文字。');return;}window.speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(message.text);utterance.voice=voice;utterance.lang=voice.lang;utterance.rate=.92;utterance.onerror=()=>toast('当前设备朗读不可用，可以继续看文字。');window.speechSynthesis.speak(utterance);return;}
 if(action==='reload'){state.connectionError='';await initialize();return;}
 if(action==='enter-test'||action==='exit-test'){state.testing=action==='enter-test';localStorage.setItem('curiosity-test-mode',state.testing?'1':'0');if(!activeProfiles().some(p=>p.id===state.id)){state.id=activeProfiles()[0]?.id||'';localStorage.setItem(selectionKey,state.id);}await sync();nav(state.testing?'/setup':'/child');return;}
 await operate(button,async()=>{
  if(action==='new-session'){if(state.sending||state.data.pending)await stop();if(state.data.pending)return;await api('/api/conversations/end',{childId:state.id,conversationId:state.data.activeConversation.id});await sync();toast('这次聊天已保存。可以问一个新的问题了。');}
  else if(action.endsWith('-memory'))await memoryAction(button.dataset.memory,{'withdraw-memory':'withdraw','delete-memory':'delete','restore-memory':'restore'}[action]);
  else if(action==='delete-conversation'||action==='restore-conversation'){await api('/api/conversations/delete',{childId:state.id,conversationId:button.dataset.conversation,restore:action==='restore-conversation'});await sync();nav(action==='delete-conversation'?'/memory':'/memory?conversation='+encodeURIComponent(button.dataset.conversation));toast(action==='delete-conversation'?'会话与关联记忆已删除，可从回收站恢复。':'会话已恢复。');}
  else if(action==='archive-profile'){if(state.data?.pending||state.sending)await stop();await api('/api/profiles/archive',{childId:state.id});state.id='';state.data=null;localStorage.removeItem(selectionKey);await initialize();nav('/setup?new=1');toast('档案已删除，可以从已删除档案恢复。');}
  else if(action==='restore-profile'){await api('/api/profiles/archive',{childId:button.dataset.profile,restore:true});state.id=button.dataset.profile;state.testing=state.profiles.find(p=>p.id===state.id)?.kind==='test';localStorage.setItem('curiosity-test-mode',state.testing?'1':'0');localStorage.setItem(selectionKey,state.id);await sync();nav('/setup');toast('档案和记录已恢复。');}
 });
});
app.addEventListener('submit',async e=>{
 e.preventDefault();const form=e.target;const data=Object.fromEntries(new FormData(form));
 if(form.id==='chatForm'){await send(data.question);return;}
 if(form.id==='searchForm'){nav('/memory'+(data.q?'?q='+encodeURIComponent(data.q):''));return;}
 const button=form.querySelector('button[type="submit"],button');if(button.disabled)return;button.disabled=true;
 const feedback=form.querySelector('.form-message');if(feedback)feedback.textContent='正在保存…';
 try{
  if(form.id==='profileForm'){const profile=await api('/api/profiles',{...data,id:form.dataset.profile||undefined});state.id=profile.id;state.testing=profile.kind==='test';localStorage.setItem('curiosity-test-mode',state.testing?'1':'0');localStorage.setItem(selectionKey,state.id);await sync();if(!form.dataset.profile)nav('/child');toast('档案已保存。下一次回答会读取最新资料。');}
  else if(form.id==='memoryForm'){await api('/api/cards/'+encodeURIComponent(form.dataset.memory),{...data,childId:state.id,action:'edit'},'PATCH');await sync();toast('修改已保存，下一次相关回答会读取最新记录。');}
  else if(form.id==='reminderForm'){const memory=await api('/api/reminders',{...data,childId:state.id});await sync();nav('/memory?memory='+encodeURIComponent(memory.id));toast('提醒已保存。');}
 }catch(err){if(feedback)feedback.textContent=err.message;toast(err.message);}finally{if(button.isConnected)button.disabled=false;}
});
window.addEventListener('popstate',render);
initialize();
