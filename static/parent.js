// Parent-only UI. Calls never enter send(), voice or the child's chat API.
const parentViews = new Map();
function parentView(id=state.id){if(!parentViews.has(id))parentViews.set(id,{data:null,loading:false,error:'',sending:false,timer:null,loadedAt:0});return parentViews.get(id);}
function parentVisible(id){return state.id===id&&path()==='/parent';}
function invalidateParentView(id){const v=parentViews.get(id);if(v)v.loadedAt=0;}
async function loadParent(id=state.id){
 const v=parentView(id);if(v.loading)return;v.loading=true;clearTimeout(v.timer);
 try{v.data=await api('/api/parent/state?childId='+encodeURIComponent(id));v.error='';v.loadedAt=Date.now();}
 catch(e){v.error=e.message;v.loadedAt=Date.now();}
 finally{v.loading=false;if(parentVisible(id)){render();if(v.data?.pending)v.timer=setTimeout(()=>loadParent(id),1500);}}
}
function ensureParent(){const v=parentView();if(!v.loading&&(!v.loadedAt||Date.now()-v.loadedAt>15000))loadParent();return v;}
function parentText(value){return esc(value).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
function parentSources(sources=[]){return sources.length?`<details class="parent-sources"><summary>查看依据（${sources.length}）</summary>${sources.map(s=>`<a href="${esc(s.url)}"><span>${esc(s.label)} · ${timeLabel(s.createdAt)}</span><q>${esc(s.quote)}</q></a>`).join('')}</details>`:'';}
function parentActivity(activity,standalone=false){
 if(!activity)return '';
 return `<section class="${standalone?'panel ':''}parent-activity"><h2>${esc(activity.title)}</h2><dl><dt>需要什么</dt><dd>${esc(activity.materials)}</dd><dt>一起做</dt><dd>${esc(activity.steps)}</dd><dt>大人来做</dt><dd>${esc(activity.adultAction)}</dd></dl>${activity.why?`<p class="muted">${esc(activity.why)}</p>`:''}${standalone?`<a href="${esc(activity.url)}">查看建议与依据</a>`:''}</section>`;
}
function recentActivity(){
 const v=ensureParent(),p=v.data?.activity,c=state.data.suggestion;
 if(p&&(!c||p.createdAt>c.createdAt))return `<div class="recent-activity"><p class="eyebrow">一起观察</p>${parentActivity(p,true)}</div>`;
 if(c)return `<section class="panel parent-activity"><p class="eyebrow">一起观察</p><h2>${esc(c.title)}</h2><p class="suggestion-steps">${esc(c.steps)}</p>${c.why?`<p class="muted">${esc(c.why)}</p>`:''}<a href="/memory?conversation=${encodeURIComponent(c.conversationId)}">查看这次聊天</a></section>`;
 return `<p class="activity-entry"><a href="/parent?view=ask&activity=1">想一个一起做的小活动 →</a></p>`;
}
function parentDraft(message){
 const d=message.draft;if(!d)return '';
 if(d.status==='saved')return `<p class="saved-reminder">提醒或修改已保存。<a href="/memory?memory=${encodeURIComponent(d.savedMemoryId)}">查看记录</a></p>`;
 if(d.status==='saving')return '<p class="request-notice">上次保存尚未确认，请先到孩子档案查看，避免重复添加。</p>';
 const saved=JSON.parse(localStorage.getItem('parent-edit-'+state.id+'-'+message.id)||'null')||d;
 const original=state.data.memories.find(m=>m.id===d.memoryId);
 const scoped=!original||original.kind==='reminder',scope=saved.scope??d.scope??original?.scope??(saved.topic?'topic':'general');
 const conversationId=saved.conversationId??d.conversationId??original?.conversationId??'';
 const scopeFields=scoped?reminderScopeFields({scope,conversationId}):original?.kind==='preference'?`<label>适用范围<select name="scope">${Object.entries(scopeNames).map(([k,v])=>`<option value="${k}" ${scope===k?'selected':''}>${v}</option>`).join('')}</select></label>`:'';
 return `<section class="parent-draft"><h3>${d.action==='edit'?'待保存修改':'待保存提醒'}</h3><form class="parent-draft-form" data-message="${esc(message.id)}" data-memory="${esc(d.memoryId)}" data-version="${esc(saved.expectedVersion??d.expectedVersion??'')}">${original?`<details><summary>查看当前记录</summary><p>${esc(original.summary)}</p><a href="/memory?memory=${encodeURIComponent(original.id)}">打开记录详情</a></details>`:''}${!original||original.kind==='reminder'?`<label>话题名称 <span>（选择“这个话题”时填写）</span><input name="topic" maxlength="60" value="${esc(saved.topic)}"></label>`:''}${scopeFields}<label>内容<textarea name="summary" maxlength="300" rows="3" required>${esc(saved.summary)}</textarea></label><button class="button primary" type="submit">${d.action==='edit'?'保存修改':'保存提醒'}</button> <a class="button" href="/memory">取消</a><p class="form-message" role="status"></p></form></section>`;
}
function parentAskPage(){
 const v=ensureParent(),params=new URLSearchParams(location.search),record=state.data.memories.find(m=>m.id===params.get('record'));
 const saved=localStorage.getItem('parent-input-'+state.id);
 const input=saved??(record?'这条记录是根据哪句话整理的？':params.has('activity')?'今晚可以和孩子一起做什么？':'');
 const pending=v.sending||Boolean(v.data?.pending),last=v.data?.lastRequest;
 shell(`${parentTabs('ask')}<section class="page-heading"><div><h1>家长问答</h1></div></section><section class="panel parent-chat">${record?`<p class="parent-context">正在询问：${esc(record.summary)} <a href="/parent?view=ask">取消选择</a></p>`:''}<div class="parent-examples">${['最近聊什么？','哪里明确说没听懂？','今晚一起做什么？'].map(text=>`<button data-action="parent-example" data-text="${esc(text)}" ${pending?'disabled':''}>${text}</button>`).join('')}</div><div class="parent-log" aria-live="polite">${(v.data?.messages||[]).map(m=>`<article class="parent-message ${m.role}" id="parent-${esc(m.id)}"><p class="parent-speaker">${m.role==='user'?'家长':'好奇心伙伴 · AI'} <time>${timeLabel(m.createdAt)}</time></p><p class="parent-text">${parentText(m.text)}</p>${m.stale?'<p class="request-notice">依据已有更新，这条历史回答不再作为当前建议。</p>':`${m.advice?`<div class="parent-advice"><h3>可以试试</h3><p class="parent-text">${parentText(m.advice)}</p></div>`:''}${parentActivity(m.activity)}${parentSources(m.sources)}`}${parentDraft(m)}</article>`).join('')||(v.loading?'<p class="muted">正在读取家长对话…</p>':'')}</div>${v.error?`<p class="form-message" role="alert">${esc(v.error)} <button data-action="parent-reload">重新连接</button></p>`:''}${!pending&&last&&['failed','cancelled'].includes(last.status)?`<p class="request-notice" role="status">${esc(last.error||(last.status==='cancelled'?'已停止，问题保留了。':'这次未完成。'))} <button data-action="parent-retry">再试一次</button></p>`:''}<form id="parentAskForm"><label class="sr-only" for="parentQuestion">家长的问题</label><textarea id="parentQuestion" name="text" maxlength="1000" rows="3" required placeholder="输入问题…" ${pending?'disabled':''}>${esc(input)}</textarea><div class="parent-compose-footer"><span class="muted" role="status">${pending?'正在查看资料并回答…':''}</span>${pending?'<button class="button" type="button" data-action="parent-stop">停止</button>':'<button class="button primary" type="submit">发送</button>'}</div></form></section>`);
 const log=document.querySelector('.parent-log');
 if(log){let target=log.lastElementChild;const question=target?.previousElementSibling;if(question?.classList.contains('user')&&question.offsetHeight+target.offsetHeight<=log.clientHeight)target=question;if(location.hash.startsWith('#parent-'))target=document.getElementById(location.hash.slice(1))||target;if(target)log.scrollTop+=target.getBoundingClientRect().top-log.getBoundingClientRect().top;}
}
async function parentSend(text){
 const id=state.id,v=parentView(id);text=text.trim();if(!text||v.sending||v.data?.pending)return;
 const key='parent-outbox-'+id,old=JSON.parse(localStorage.getItem(key)||'null');
 const recordId=new URLSearchParams(location.search).get('record')||'';
 const body=old?.childId===id&&old.text===text&&(old.recordId||'')===recordId?old:{childId:id,requestId:'parent_'+crypto.randomUUID().replaceAll('-',''),text,recordId};
 localStorage.setItem(key,JSON.stringify(body));localStorage.setItem('parent-input-'+id,text);v.sending=true;render();
 try{await api('/api/parent/ask',body);localStorage.removeItem(key);localStorage.setItem('parent-input-'+id,'');v.error='';}
 catch(e){v.error=e.message;toast(e.message);}
 finally{v.sending=false;await loadParent(id);}
}
async function parentAction(button){
 const action=button.dataset.action,v=parentView(),id=state.id;
 if(action==='parent-example'){const field=document.querySelector('#parentQuestion');field.value=button.dataset.text;localStorage.setItem('parent-input-'+id,field.value);field.focus();return;}
 if(action==='parent-reload'){await loadParent();return;}
 if(action==='parent-retry'){await parentSend(v.data?.lastRequest?.text||'');return;}
 if(action==='parent-stop'&&!v.data?.pending)return;
 if(action==='parent-stop'){await operate(button,async()=>{await api('/api/parent/cancel',{childId:id,requestId:v.data.pending.id});await loadParent(id);});return;}
 if(action==='parent-refresh-draft'){
  const form=button.closest('form'),draft=Object.fromEntries(new FormData(form));
  const latest=await api('/api/state?childId='+encodeURIComponent(id)),m=latest.memories.find(m=>m.id===form.dataset.memory);
  if(!m||['withdrawn','deleted'].includes(m.status)||state.id!==id){form.querySelector('.form-message').textContent='这条记录已撤回或不可用，草稿仍保留。请先在孩子档案中明确恢复记录。';return;}
  form.dataset.version=m.version;localStorage.setItem('parent-edit-'+id+'-'+form.dataset.message,JSON.stringify({...draft,expectedVersion:m.version}));
  form.querySelector('.form-message').textContent='当前记录：'+m.summary+'。你的草稿保留了，请对照后再保存。';
 }
}
async function parentSave(form){
 const button=form.querySelector('button[type="submit"]');if(button.disabled)return;button.disabled=true;
 const id=state.id,feedback=form.querySelector('.form-message');feedback.textContent='正在保存…';
 try{await api('/api/parent/save',{...Object.fromEntries(new FormData(form)),childId:id,messageId:form.dataset.message,expectedVersion:form.dataset.version||undefined});localStorage.removeItem('parent-edit-'+id+'-'+form.dataset.message);await loadParent(id);if(state.id===id)await sync();toast('已保存。');}
 catch(e){feedback.textContent=e.message;if(e.code==='memory_conflict')feedback.insertAdjacentHTML('beforeend',' <button type="button" class="button small" data-action="parent-refresh-draft">查看最新内容，保留草稿</button>');}
 finally{if(button.isConnected)button.disabled=false;}
}
