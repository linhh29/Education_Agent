const $ = (s, r=document)=>r.querySelector(s);
const $$ = (s, r=document)=>[...r.querySelectorAll(s)];
const app = $('#app');
let state = { childId:'child_demo', profile:null, conversationId:null, avatar:'idle', interactionStage:'idle', lastQuestion:'', topicQuestion:'', teachbackPending:false, lastAnswer:'', lastFollow:'', lastExperiment:'', lastContract:[], lastTrace:[], lastTurnTrace:null, lastStrategy:null, lastAgentOrchestration:null, activityMode:'ask', feedbackMode:'normal', activeFeedback:'', parent:null, trendRange:7, isAsking:false, lastLatency:0, thinkingHint:'', voiceSupported:false, interactionMessage:'', interactionTone:'', retryQuestion:'', retryFeedback:'normal', retryOriginalQuestion:'', retryInputMode:'text', recordingSeconds:0, voiceDiagnostics:null, lastTraceId:'' };
const SPEECH_FINALIZATION_GRACE_MS=300;
let currentRec = null;
let currentAudio = null;
let speechSequence = 0;
let listenTimer = null;
let recordingTicker = null;
let tourTimer = null;
const TOUR_SEEN_KEY = 'curiosity-companion-tour-v1';
const INTERACTION_STAGES = {
 idle:{avatar:'idle',label:'准备好了，等你问问题',hint:'等待新的好奇问题'},
 capturing:{avatar:'listening',label:'正在听你说话',hint:'正在听你说：说完请点按钮结束'},
 transcribing:{avatar:'transcribing',label:'正在整理你说的话',hint:'录音已结束，正在确认最后一句话'},
 understanding:{avatar:'thinking',label:'正在理解问题',hint:'先检查安全边界，再读取最新档案和可信记录'},
 composing:{avatar:'thinking',label:'正在组织回答',hint:'按孩子现在的年龄和反馈方式组织完整回答'},
 checking:{avatar:'thinking',label:'正在检查回答',hint:'正在核对事实、语言和儿童安全边界'},
 ready:{avatar:'idle',label:'回答准备好了',hint:'文字回答已经准备好'},
 speaking:{avatar:'speaking',label:'正在讲给你听',hint:'回答好了；朗读时也可以继续提问'},
 reflecting:{avatar:'idle',label:'正在整理本轮记录',hint:'回答已交付，正在整理反馈和理解证据'},
 error:{avatar:'error',label:'这一步没有完成',hint:'可以重试，或者先用文字继续'},
};
const LEGAL_STAGE_TRANSITIONS = {
 idle:['capturing','understanding','speaking','reflecting','error'], capturing:['transcribing','idle','error'], transcribing:['understanding','idle','error'],
 understanding:['composing','checking','ready','idle','error'], composing:['checking','ready','idle','error'], checking:['ready','idle','error'],
 ready:['speaking','reflecting','idle','capturing','understanding','error'], speaking:['reflecting','idle','capturing','understanding','error'],
 reflecting:['idle','capturing','understanding','speaking','error'], error:['idle','capturing','understanding'],
};

function setInteractionStage(next, options={}){
 if(!INTERACTION_STAGES[next]) return false;
 const current=state.interactionStage||'idle';
 if(current!==next&&!LEGAL_STAGE_TRANSITIONS[current]?.includes(next)) console.warn(`[interaction] unexpected transition ${current} -> ${next}`);
 state.interactionStage=next;
 state.avatar=INTERACTION_STAGES[next].avatar;
 state.isAsking=['understanding','composing','checking'].includes(next);
 if(options.hint!==undefined) state.thinkingHint=options.hint;
 else if(['understanding','composing','checking'].includes(next)) state.thinkingHint=INTERACTION_STAGES[next].hint;
 return true;
}

function esc(s=''){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function toast(msg){ const t=document.createElement('div'); t.className='toast'; t.textContent=msg; document.body.appendChild(t); setTimeout(()=>t.remove(),2400) }
async function api(path, opts={}){ const ctl=new AbortController(); const t=setTimeout(()=>ctl.abort(), opts.timeoutMs||15000); try{ const res=await fetch(path,{headers:{'Content-Type':'application/json'},signal:ctl.signal,...opts}); const data=await res.json().catch(()=>({})); if(!res.ok) throw new Error(data.message||data.error||'请求失败'); return data }catch(e){ if(e.name==='AbortError') throw new Error('请求有点慢，请稍后再试'); throw e } finally{ clearTimeout(t) } }
function newTraceId(){ const raw=globalThis.crypto?.randomUUID?.().replaceAll('-','')||`${Date.now()}${Math.random().toString(16).slice(2)}`; return `trace_${raw.slice(0,64)}`; }
function auditUiEvent(eventType,status='completed',details={},traceId=state.lastTraceId,extra={}){ const payload={eventType,status,traceId:traceId||newTraceId(),childId:state.childId,conversationId:state.conversationId||'',activityMode:state.activityMode,feedbackMode:state.feedbackMode,component:'ui',details,...extra}; fetch('/api/runtime/events',{method:'POST',headers:{'Content-Type':'application/json'},keepalive:true,body:JSON.stringify(payload)}).catch(()=>{}); }
async function loadProfile(){ state.profile = await api('/api/profile?childId='+state.childId); return state.profile }
function themeInit(){ const saved=localStorage.getItem('theme'); if(saved==='dark'||(!saved&&matchMedia('(prefers-color-scheme: dark)').matches)) document.documentElement.classList.add('dark') }
themeInit();

function tourSteps(){
 if(location.pathname==='/child') return [
  {element:'[data-tour="child-stage"]',popover:{title:'孩子想问什么，就自然地问',description:'这里就做三件事：说出问题、听回答、告诉我们哪里没听懂。',side:'top',align:'start'}},
  {element:'[data-tour="modes"]',popover:{title:'选择这次怎么解释',description:'切换后会用当前问题重新回答。清楚解释更精炼，详细解答更完整，故事和观察也有各自结构。',side:'bottom',align:'center'}},
  {element:'[data-tour="answer"]',popover:{title:'用孩子现在听得懂的话回答',description:'回答会结合最新年龄、熟悉事物和家长提醒，先说重点，再按需要补例子。',side:'left',align:'center'}},
  {element:'[data-tour="ask"]',popover:{title:'可以说出来，也可以打字',description:'孩子可以直接开口，也可以输入问题。下面的示例问题可以帮助家长快速体验不同场景。',side:'top',align:'center'}},
  {element:'[data-tour="feedback"]',popover:{title:'没听懂，就告诉我们',description:'孩子点“没听懂”“再简单点”“举个例子”，或者自己讲一遍，我们都会把这次反馈记下来。',side:'top',align:'center'}},
  {element:'[data-tour="child-memory"]',popover:{title:'留下有用的线索',description:'右侧会整理本次参考过的记录、孩子的反馈，以及需要家长一起看的提醒。',side:'left',align:'start'}},
 ];
 if(location.pathname==='/parent') return [
  {element:'[data-tour="parent-hero"]',popover:{title:'先看看孩子最近的变化',description:'这里把聊天里的线索整理成好奇方向、理解情况，以及孩子更容易听懂的解释方式。',side:'bottom',align:'start'}},
  {element:'[data-tour="parent-summary"]',popover:{title:'三张卡，串起最近的情况',description:'近况、观察摘要和今天的陪伴建议各有侧重，家长可以很快找到自己关心的部分。',side:'top',align:'center'}},
  {element:'[data-tour="parent-next"]',popover:{title:'今天可以从一件小事开始',description:'陪孩子复述一句、补一个生活里的例子，或者核对记录里的判断是否准确，就已经很有帮助。',side:'left',align:'center'}},
  {element:'[data-tour="parent-growth"]',popover:{title:'看看最近讲得合不合适',description:'这里根据聊天、孩子的反馈和家长确认，看看回答是否适合孩子现在的情况。',side:'top',align:'center'}},
 ];
 if(location.pathname==='/memory') return [
  {element:'[data-tour="memory-hero"]',popover:{title:'记录和来由，都能在这里找到',description:'聊天、理解情况、表达偏好和安全提醒会分层整理，方便家长核对。',side:'bottom',align:'start'}},
  {element:'[data-tour="chat-memory"]',popover:{title:'从一段真实聊天开始看',description:'这里保留孩子问了什么、回答了什么，以及孩子当时有没有听懂。',side:'right',align:'start'}},
  {element:'[data-tour="evolution"]',popover:{title:'重要变化会留下记录',description:'孩子的反馈、复述和家长修正都会保留，方便以后按最新情况组织回答。',side:'right',align:'center'}},
  {element:'[data-tour="memory-graph"]',popover:{title:'下一次回答会参考哪些线索',description:'每次请求都会重新读取最新档案、近期聊天、理解记录和家长提醒。',side:'top',align:'center'}},
  {element:'[data-tour="trace"]',popover:{title:'这次回答是怎么准备的',description:'这里会列出安全检查、相关记录和回答方式，家长发现不对时可以随时修改。',side:'left',align:'start'}},
 ];
 return [
  {element:'[data-tour="setup-hero"]',popover:{title:'先填几项，回答会更合适',description:'年龄、熟悉事物和家长提醒会在每次回答前重新读取，不会沿用过时的个性化答案。',side:'bottom',align:'start'}},
  {element:'[data-tour="profile-preview"]',popover:{title:'先把当前情况写清楚',description:'年龄、兴趣、熟悉事物和喜欢的解释方式，会影响这一次怎样组织答案。',side:'left',align:'center'}},
  {element:'[data-tour="profile-form"]',popover:{title:'家长可以随时更新',description:'孩子长大或偏好改变后，改这里即可；下一次回答会读取最新内容。',side:'right',align:'start'}},
 ];
}

function startProductTour(){
 clearTimeout(tourTimer);
 const createDriver=window.driver?.js?.driver;
 if(!createDriver){ toast('引导组件还没有加载好，请刷新后再试'); return; }
 const tour=createDriver({
  animate:true,
  allowClose:true,
  smoothScroll:true,
  showProgress:true,
  showButtons:['next','previous','close'],
  nextBtnText:'下一步 →',
  prevBtnText:'← 上一步',
  doneBtnText:'开始体验',
  progressText:'第 {{current}} / {{total}} 步',
  overlayColor:'rgba(35, 42, 58, .72)',
  stagePadding:10,
  stageRadius:22,
  popoverClass:'curiosity-tour-popover',
  steps:tourSteps(),
  onDestroyed:()=>localStorage.setItem(TOUR_SEEN_KEY,'seen')
 });
 tour.drive();
}

function bindChrome(){
 const themeButton=$('#themeBtn');
 if(themeButton) themeButton.onclick=()=>{document.documentElement.classList.toggle('dark');localStorage.setItem('theme',document.documentElement.classList.contains('dark')?'dark':'light')};
 const guideButton=$('#guideBtn');
 if(guideButton) guideButton.onclick=startProductTour;
}

function scheduleFirstTour(){
 clearTimeout(tourTimer);
 if(!['/','/setup'].includes(location.pathname)||localStorage.getItem(TOUR_SEEN_KEY)) return;
 tourTimer=setTimeout(startProductTour,700);
}

const statusZh = { unknown:'还没有记录', candidate:'刚聊到', learning:'还在理解', known:'已经会讲', needs_review:'请家长确认', deleted:'已删除' };
const statusTone = { unknown:'还没聊过', candidate:'刚聊到', learning:'还在理解', known:'已经会讲', needs_review:'请家长看看' };
const feedbackZh = { normal:'正常提问', confused:'没听懂', simpler:'再简单点', example:'举个例子', why:'继续追问', teachback:'我来讲讲' };
const safetyZh = { blocked:'已拦截', safe:'安全' };
const modeZh = { ask:'清楚解释', detail:'详细解答', story:'讲个故事', observe:'一起观察' };
const modeHint = { ask:'直接回答，只讲一个核心原因', detail:'按起因、变化、结果逐步讲清楚', story:'从角色视角经历一段真正的小故事', observe:'家长陪同，按步骤观察真实现象' };

function shell(html){ app.innerHTML = `<div class="shell">${nav()}<main>${html}</main></div>`; bindChrome(); scheduleFirstTour(); }

function statusText(){return INTERACTION_STAGES[state.interactionStage||'idle']?.label||'准备好了'}
function micText(){ if(currentRec?.stopping) return '⏳ 正在识别你说的话'; if(currentRec) return `⏹️ 说完了，点这里回答${state.recordingSeconds?` · ${state.recordingSeconds}秒`:''}`; if(state.isAsking) return '⏳ 正在生成回答'; if(state.avatar==='speaking') return '🎙️ 继续提问（会先停朗读）'; return '🎙️ 点一下开始说话' }
function modePicker(){ const disabled=state.isAsking||Boolean(currentRec); return `<div class="mode-picker-wrap" data-tour="modes"><div class="mode-picker-title"><b>这次想怎么听？</b><span>切换后会用当前问题重新回答</span></div><div class="mode-picker" role="list" aria-label="回答方式">${Object.keys(modeZh).map(k=>`<button class="mode ${state.activityMode===k?'active':''}" data-mode="${k}" title="${esc(modeHint[k])}" ${disabled?'disabled':''}><b>${modeZh[k]}</b><span>${esc(modeHint[k])}</span></button>`).join('')}</div></div>` }
function planetMap(cards=[]){ const list=(cards.length?cards:[{concept:'月亮和天空',status:'candidate',confidence:.38},{concept:'影子和光',status:'learning',confidence:.56},{concept:'恐龙与化石',status:'known',confidence:.8}]).slice(0,4); return `<div class="mini planet-panel compact"><div class="mini-title"><p class="eyebrow">好奇星球</p><small>最近聊过的概念</small></div><div class="planets">${list.map((c,i)=>`<div class="planet ${c.status||'candidate'}"><span>${['🌙','🔦','🦕','🌧️'][i%4]}</span><b>${esc(c.concept)}</b><small>${statusTone[c.status]||'刚发现'} · ${Math.round((c.confidence||0)*100)}%</small></div>`).join('')}</div></div>` }
function experimentCard(){ return `<div class="mini experiment compact"><div class="mini-title"><p class="eyebrow">安全小观察</p><small>必须和家长一起</small></div><p>${esc(state.lastExperiment||'回答后会出现一个安全、简单、需要家长陪同的小观察。')}</p></div>` }
function 步骤Mini(){ const order=['capturing','transcribing','understanding','composing','checking','ready','speaking','reflecting']; const current=order.indexOf(state.interactionStage); const step=state.thinkingHint||INTERACTION_STAGES[state.interactionStage||'idle']?.hint||'等待新的好奇问题'; const labels=['听清','理解','组织','检查','讲解']; const activeIndex=state.interactionStage==='capturing'||state.interactionStage==='transcribing'?0:state.interactionStage==='understanding'?1:state.interactionStage==='composing'?2:state.interactionStage==='checking'?3:['ready','speaking','reflecting'].includes(state.interactionStage)?4:-1; return `<div class="mini 步骤-mini compact"><div class="mini-title"><p class="eyebrow">现在到哪一步了</p><small>${state.lastLatency?`上次 ${state.lastLatency} 秒`:''}</small></div><div class="步骤-dots stage-five">${labels.map((label,index)=>`<span class="${index<=activeIndex?'on':''}"><i></i><em>${label}</em></span>`).join('')}</div><p>${esc(step)}</p>${state.lastStrategy?`<small class="strategy-note">本轮策略：${esc(state.lastStrategy.label)} · ${esc(state.lastStrategy.id)}</small>`:''}</div>` }
function guardrailCard(){ const lines=state.lastContract.length?state.lastContract:(state.parent?.companionContract?.child||['危险实验找家长。','不会索要地址、电话、学校或密码。','听不懂就按反馈按钮。']); return `<div class="mini guardrail compact"><div class="mini-title"><p class="eyebrow">使用约定</p><small>遇到这些情况会找大人</small></div>${lines.map(x=>`<p>🌱 ${esc(x)}</p>`).join('')}</div>` }
function traceCard(){ const structured=state.lastTurnTrace?.stages||[]; const labels={safety:'安全检查',understanding:'理解问题',composing:'组织回答',checking:'质量门禁',recording:'整理证据'}; const trace=structured.length?structured:(state.lastTrace.length?state.lastTrace:(state.parent?.latestTrace||[])); return `<div class="mini trace-card compact"><div class="mini-title"><p class="eyebrow">这次回答怎么来的</p><small>${state.lastTurnTrace?.totalDurationMs?`${(state.lastTurnTrace.totalDurationMs/1000).toFixed(1)} 秒`: '家长可以回看'}</small></div>${trace.length?trace.map((x,i)=>`<p><b>${i+1}. ${esc(labels[x.stage]||x.label||x.stage)}</b><span>${esc(x.summary||x.detail)}${Number.isFinite(x.durationMs)&&x.durationMs?` · ${x.durationMs}ms`:''}</span></p>`).join(''):'<p><b>1. 等待提问</b><span>回答后会显示安全检查、回答策略、质量门禁和记录结果。</span></p>'}</div>` }
function agentStatusZh(status){ return ({completed:'完成',blocked:'已拦截',failed:'未通过',skipped:'本轮跳过',deferred:'回答后整理'})[status]||'等待'; }
function agentTeamCard(){ const run=state.lastAgentOrchestration||state.parent?.latestAgentOrchestration; const agents=(run?.agents||[]).filter(agent=>['safety_guardian','learning_planner','child_tutor','quality_reviewer','memory_steward'].includes(agent.id)); return `<div class="mini agent-team-card"><div class="mini-title"><div><p class="eyebrow">回答小队</p><b>${run?'这次由不同伙伴一起完成':'提问后会看到谁来帮忙'}</b></div><small>不展示模型内部思考</small></div><div class="agent-chip-list">${agents.map(agent=>`<span class="agent-chip ${esc(agent.status)}"><i></i><b>${esc(agent.label)}</b><small>${esc(agentStatusZh(agent.status))}</small></span>`).join('')||'<p class="muted">安全守护、学习规划、儿童讲解、回答检查和记忆整理会按需要参与。</p>'}</div></div>`; }
function memoryRail(){ const cards=state.parent?.knowledgeCards||[]; return `<div class="mini demo-qs compact"><div class="mini-title"><p class="eyebrow">演示问题</p><small>点一下试试</small></div><div class="actions"><button class="subtle demoq">为什么月亮好像跟着我走？</button><button class="subtle demoq">影子为什么会变长？</button><button class="subtle demoq">恐龙为什么不见了？</button><button class="subtle voice-demo">🎙️ 模拟语音提问</button><button class="danger demoq">怎么点火做实验？</button></div></div>${步骤Mini()}${planetMap(cards)}<details class="rail-more"><summary>更多陪伴线索</summary>${agentTeamCard()}${traceCard()}${guardrailCard()}${experimentCard()}</details>` }
function canCaptureAudio(){ return Boolean(navigator.mediaDevices?.getUserMedia&&(window.AudioContext||window.webkitAudioContext||window.MediaRecorder)); }
function voiceDiagnosticHtml(){ const value=state.voiceDiagnostics; if(!value) return ''; const level=Math.max(0,Math.min(100,Math.round((value.level||0)*100))); const details=[value.method,value.deviceLabel?`麦克风：${value.deviceLabel}`:'',value.sampleRate?`${value.sampleRate} Hz`:'',value.duration?`${value.duration.toFixed(1)} 秒`:'',value.mime||'',value.bytes?`${Math.round(value.bytes/1024)} KB`:''].filter(Boolean).join(' · '); return `<div class="voice-diagnostic ${value.error?'error':''}" aria-live="polite"><div class="voice-meter"><i style="width:${level}%"></i></div><span>${esc(value.label||'正在检测麦克风音量')}</span><small>${esc(details)}</small></div>`; }
function childHtml(){ const canRecord=canCaptureAudio(); const canCaption=Boolean(window.SpeechRecognition||window.webkitSpeechRecognition); const support=canRecord?'使用浏览器录音并交给云端识别；不会保存原始录音':canCaption?'可以直接说话；当前设备使用浏览器识别':'当前设备暂不支持语音输入，可以直接打字'; const inputHint=state.teachbackPending?'用自己的话讲讲刚才的答案，比如“因为……”':'也可以直接打字问：为什么月亮好像跟着我走？'; const heardHint=state.teachbackPending?'轮到你讲啦：用自己的话说说刚才为什么。':'点麦克风开始，说完后再点一次“结束并回答”。'; const notice=state.interactionMessage?`<div class="interaction-notice ${state.interactionTone==='error'?'error':'info'}" role="status"><span>${esc(state.interactionMessage)}</span></div>`:''; const retryPanel=state.retryQuestion?'<div class="retry-panel"><span>这次没有生成出合适答案，可以重新请模型回答。</span><button id="retryAnswer" class="retry-answer" type="button">↻ 再试一次</button></div>':''; const micDisabled=state.isAsking||Boolean(currentRec?.stopping); const otherDisabled=state.isAsking||Boolean(currentRec); return `<section class="stage child-v2 ${state.avatar}" data-tour="child-stage"><div class="child-main"><div class="stage-head"><div><p class="eyebrow" style="margin-bottom:6px">好奇星球</p><h1>${esc(state.profile?.nickname||'孩子')}，今天想问什么？</h1></div><span class="live-tag"><i></i>${statusText()}</span></div>${modePicker()}<div class="focus-row"><div class="avatar-card"><div class="avatar"><div class="mouth"></div></div><div class="state">${statusText()} · ${modeZh[state.activityMode]}</div><small class="voice-note">${support}</small></div><div class="answer-card" data-tour="answer"><div class="answer-top"><b>回答</b><div class="answer-tools">${state.lastAnswer?'<button id="replayAnswer" class="listen-again" type="button">🔊 再听一遍</button>':''}${state.isAsking?'<span class="tiny-loading">正在整理</span>':state.avatar==='speaking'?'<span class="tiny-loading soft">朗读中</span>':''}</div></div><div id="answerText">${esc(state.lastAnswer||'你好呀！把你的“为什么”告诉我，我们一起找答案。')}</div>${retryPanel}<small id="follow" class="muted">${state.lastFollow?('还可以这样问：'+esc(state.lastFollow)):'回答后，这里会给你一个新的好奇方向。'}</small></div></div><div class="transcript wide"><b>${state.teachbackPending?'轮到我讲':'我听到'}</b><span id="heard">${esc(state.lastQuestion||heardHint)}</span></div>${voiceDiagnosticHtml()}${notice}<div class="child-actions" data-tour="ask"><button id="mic" class="mic ${currentRec?'recording':''}" ${micDisabled?'disabled':''}>${micText()}</button><div class="text-fallback"><input id="text" placeholder="${esc(inputHint)}" ${otherDisabled?'disabled':''}><button id="send" class="primary" ${otherDisabled?'disabled':''}>${state.teachbackPending?'讲讲看':'发送'}</button></div></div><div class="chips feedback-row" data-tour="feedback"><button class="chip ${state.activeFeedback==='confused'?'active':''}" data-fb="confused" ${otherDisabled?'disabled':''}>😵 没听懂</button><button class="chip ${state.activeFeedback==='simpler'?'active':''}" data-fb="simpler" ${otherDisabled?'disabled':''}>🐣 再简单点</button><button class="chip ${state.activeFeedback==='example'?'active':''}" data-fb="example" ${otherDisabled?'disabled':''}>🔦 举个例子</button><button class="chip ${state.activeFeedback==='why'?'active':''}" data-fb="why" ${otherDisabled?'disabled':''}>❓ 继续问为什么</button><button class="chip teach ${state.activeFeedback==='teachback'?'active':''}" data-fb="teachback" ${otherDisabled?'disabled':''}>🎒 我来讲讲</button></div></div><aside class="side" data-tour="child-memory">${memoryRail()}<div class="mini compact"><p class="muted">本次反馈和孩子自己的讲法会保存为记录。下一次回答仍会读取最新年龄、档案和家长提醒，不会直接复用旧答案。</p><a class="pillbtn primary" href="/parent">给家长看 →</a></div></aside></section>` }
async function childPage(){ await loadProfile(); try{ state.parent=await api('/api/parent/cards?childId='+state.childId,{timeoutMs:6000}) }catch{} shell(childHtml()); bindChild(); }
function childPageNoLoad(){ if(location.pathname!=='/child') return; shell(childHtml()); bindChild(); }
function stopPlayback(){ speechSequence+=1; if(currentAudio){ currentAudio._finish?.(); currentAudio.pause(); currentAudio.removeAttribute('src'); currentAudio=null; } if('speechSynthesis' in window) speechSynthesis.cancel(); }
function preferredChineseVoice(){ const voices=('speechSynthesis' in window&&speechSynthesis.getVoices)?speechSynthesis.getVoices():[]; const preferred=['xiaoxiao','ting','mei','yu-shu','yu-sha','natural']; const score=voice=>{const name=(voice.name||'').toLowerCase();const index=preferred.findIndex(item=>name.includes(item));return index<0?999:index}; return voices.filter(v=>/^zh(-|_)/i.test(v.lang||'')).sort((a,b)=>score(a)-score(b))[0]||null; }
function browserSpeak(text){ return new Promise(resolve=>{ if(!('speechSynthesis' in window)||!window.SpeechSynthesisUtterance){ resolve(false); return; } speechSynthesis.cancel(); const utterance=new SpeechSynthesisUtterance(text); utterance.lang='zh-CN'; utterance.voice=preferredChineseVoice(); utterance.rate=Math.max(.86,Math.min(.98,Number(state.profile?.voicePreference?.rate||.92))); utterance.pitch=1.02; utterance.onend=()=>resolve(true); utterance.onerror=()=>resolve(false); speechSynthesis.speak(utterance); }); }
async function speak(text, manual=false){ if(!manual&&!state.profile?.voicePreference?.autoSpeak){ setInteractionStage('reflecting'); childPageNoLoad(); setTimeout(()=>{ if(state.interactionStage==='reflecting'){setInteractionStage('idle');childPageNoLoad();} },350); return; } stopPlayback(); const sequence=++speechSequence; const traceId=state.lastTraceId||newTraceId(); const started=performance.now(); setInteractionStage('speaking'); childPageNoLoad(); try{ const result=await api('/api/speech/synthesize',{method:'POST',timeoutMs:18000,body:JSON.stringify({text,traceId})}); if(sequence!==speechSequence) return; if(!result.audioUrl) throw new Error('没有可播放的自然语音'); const audio=new Audio(result.audioUrl); currentAudio=audio; await new Promise((resolve,reject)=>{ audio._finish=resolve; audio.onended=resolve; audio.onerror=()=>reject(new Error('语音播放失败')); audio.play().catch(reject); }); }catch(error){ if(sequence!==speechSequence) return; const usedFallback=await browserSpeak(text); auditUiEvent('tts.fallback',usedFallback?'completed':'failed',{manual,textChars:String(text||'').length},traceId,{durationMs:Math.round(performance.now()-started),errorType:error?.name||'Error'}); if(!usedFallback&&!manual){ state.interactionMessage='自然语音暂时没有播放，文字回答仍然可以继续看。'; state.interactionTone='info'; } else if(usedFallback){ state.interactionMessage='自然语音暂时不可用，已切换为设备里的中文朗读。'; state.interactionTone='info'; } }finally{ if(sequence===speechSequence){ currentAudio=null; if(state.interactionStage==='speaking'){ setInteractionStage('reflecting'); childPageNoLoad(); setTimeout(()=>{ if(state.interactionStage==='reflecting'){setInteractionStage('idle');childPageNoLoad();} },350); } } } }
async function ask(q, fb='normal', originalQuestion='', inputMode='text', existingTraceId=''){
 if(!q.trim() || state.isAsking) return;
 stopPlayback(); stopListening(true);
 const started=performance.now();
 const traceId=existingTraceId||newTraceId(); state.lastTraceId=traceId;
 const previousAnswer=state.lastAnswer;
 if(fb==='normal') state.topicQuestion=q.trim();
 const feedbackHint={confused:'我换个角度重新讲，不重复刚才的话',simpler:'我把概念变少一点，但保留真正原因',example:'我找一个能看到、能验证的具体例子',why:'我再往下解释一层原因'}[fb]||'';
 state.lastQuestion=q.trim(); state.feedbackMode=fb; state.activeFeedback=fb==='normal'?'':fb; setInteractionStage('understanding',{hint:feedbackHint||INTERACTION_STAGES.understanding.hint}); state.interactionMessage=feedbackHint; state.interactionTone='info'; state.retryQuestion=''; childPageNoLoad();
 auditUiEvent('request.start','started',{inputMode,userTextChars:q.trim().length,trigger:inputMode},traceId);
 const hintTimer=setTimeout(()=>{ if(state.isAsking){ setInteractionStage('composing',{hint:feedbackHint||INTERACTION_STAGES.composing.hint}); const el=$('.步骤-mini p'); if(el) el.textContent=state.thinkingHint; } }, 600);
 const slowHintTimer=setTimeout(()=>{ if(state.isAsking){ setInteractionStage('checking'); const el=$('.步骤-mini p'); if(el) el.textContent=state.thinkingHint; } }, 6500);
 try{
  const res=await api('/api/chat',{method:'POST',timeoutMs:60000,body:JSON.stringify({traceId,childId:state.childId,conversationId:state.conversationId,inputMode,userText:q,transcript:q,originalQuestion,previousAnswer,feedbackMode:fb,activityMode:state.activityMode})});
  clearTimeout(hintTimer); clearTimeout(slowHintTimer);
  const retryableQualityFailure=res.answerSource==='quality_fallback'||res.deliveryDecision?.deliveryValidated===false;
  state.conversationId=res.conversationId; state.lastAnswer=res.assistant.displayText; state.lastFollow=res.assistant.followUp||''; state.lastExperiment=res.assistant.safeExperiment||''; state.lastContract=res.assistant.companionContract||state.lastContract; state.lastTrace=res.assistant.trace||[]; state.lastTurnTrace=res.turnTrace||null; state.lastStrategy=res.strategy||null; state.lastAgentOrchestration=res.agentOrchestration||null; state.lastLatency=((performance.now()-started)/1000).toFixed(1); setInteractionStage(retryableQualityFailure?'error':'ready'); state.teachbackPending=false; state.thinkingHint=''; state.interactionMessage=retryableQualityFailure?'这次回答还没有通过检查，请点“重新生成”再试一次。':''; state.interactionTone=retryableQualityFailure?'error':''; state.retryQuestion=retryableQualityFailure?q.trim():''; state.retryFeedback=fb; state.retryOriginalQuestion=originalQuestion; state.retryInputMode=inputMode;
  childPageNoLoad();
  auditUiEvent('request.finish','completed',{answerChars:String(res.assistant.displayText||'').length,deliveryAccepted:Boolean(res.deliveryDecision?.deliveryValidated),answerSource:res.answerSource||''},traceId,{durationMs:Math.round(performance.now()-started)});
  toast(retryableQualityFailure?'回答未通过检查，可以重新生成':res.safety.needsParent?'这个问题需要家长一起看':'回答好了，相关记录也保存了');
  api('/api/parent/cards?childId='+state.childId,{timeoutMs:6000}).then(d=>{state.parent=d; if(location.pathname==='/child') childPageNoLoad();}).catch(()=>{});
  if(!retryableQualityFailure) speak(res.assistant.speakText||res.assistant.displayText);
 }catch(e){ clearTimeout(hintTimer); clearTimeout(slowHintTimer); auditUiEvent('request.error','failed',{inputMode,userTextChars:q.trim().length},traceId,{durationMs:Math.round(performance.now()-started),errorType:e?.name||'Error'}); setInteractionStage('error'); state.thinkingHint=''; state.interactionMessage='这次回答没有完成：'+(e.message||'请稍后再试'); state.interactionTone='error'; state.retryQuestion=q.trim(); state.retryFeedback=fb; state.retryOriginalQuestion=originalQuestion; state.retryInputMode=inputMode; childPageNoLoad(); toast('回答没有完成，可以点“再试一次”'); }
}
function stopListening(silent=false){
 if(listenTimer){ clearTimeout(listenTimer); listenTimer=null; }
 if(recordingTicker){ clearInterval(recordingTicker); recordingTicker=null; }
 if(currentRec){
  const rec=currentRec;
  if(rec.stopping) return;
  rec.manualStop=true; rec.stopping=true;
  setInteractionStage('transcribing'); state.interactionMessage='录音已结束，正在用云端识别你刚才说的话…'; state.interactionTone='info'; state.retryQuestion='';
  childPageNoLoad();
  try{ rec.recognition?.stop(); }catch{}
  if(rec.mediaRecorder&&rec.mediaRecorder.state!=='inactive'){
   try{ rec.mediaRecorder.stop(); }catch{ rec.finish?.('manual'); }
  }else{
   rec.finishTimer=setTimeout(()=>rec.finish?.('manual'),SPEECH_FINALIZATION_GRACE_MS);
  }
  if(!silent) toast('录音已结束，正在识别你说的话');
 }
}
function blobToDataUrl(blob){ return new Promise((resolve,reject)=>{ const reader=new FileReader(); reader.onload=()=>resolve(String(reader.result||'')); reader.onerror=()=>reject(new Error('录音读取失败')); reader.readAsDataURL(blob); }); }
function recorderMimeType(){ if(!window.MediaRecorder) return ''; return ['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/ogg;codecs=opus'].find(type=>MediaRecorder.isTypeSupported?.(type))||''; }
function mergePcmChunks(chunks){ const length=chunks.reduce((sum,chunk)=>sum+chunk.length,0); const merged=new Float32Array(length); let offset=0; chunks.forEach(chunk=>{merged.set(chunk,offset);offset+=chunk.length}); return merged; }
function downsamplePcm(input,inputRate,outputRate=16000){ if(!input.length||inputRate<=outputRate) return input; const ratio=inputRate/outputRate; const output=new Float32Array(Math.max(1,Math.round(input.length/ratio))); for(let index=0;index<output.length;index++){ const start=Math.floor(index*ratio); const end=Math.min(input.length,Math.max(start+1,Math.floor((index+1)*ratio))); let sum=0; for(let sourceIndex=start;sourceIndex<end;sourceIndex++) sum+=input[sourceIndex]; output[index]=sum/(end-start); } return output; }
function normalizePcmSamples(input){ if(!input.length) return {samples:input,peak:0,rms:0,outputRms:0,gain:1,enhanced:false}; let mean=0; for(const sample of input) mean+=sample; mean/=input.length; const filtered=new Float32Array(input.length); const magnitudes=[]; let previousInput=0,previousOutput=0,peak=0,sum=0; for(let index=0;index<input.length;index++){ const centered=input[index]-mean; const highPassed=centered-previousInput+.97*previousOutput; previousInput=centered; previousOutput=highPassed; filtered[index]=highPassed; const value=Math.abs(highPassed); peak=Math.max(peak,value); sum+=highPassed*highPassed; if(index%4===0) magnitudes.push(value); } magnitudes.sort((left,right)=>left-right); const robustPeak=magnitudes[Math.min(magnitudes.length-1,Math.floor(magnitudes.length*.995))]||peak; const rms=Math.sqrt(sum/input.length); const hasVoice=robustPeak>=.0035||rms>=.0015; const peakGain=.68/Math.max(robustPeak,.0001); const rmsGain=.09/Math.max(rms,.0001); const gain=hasVoice?Math.max(1,Math.min(24,peakGain,rmsGain)):1; const output=new Float32Array(filtered.length); let outputSum=0; for(let index=0;index<filtered.length;index++){ output[index]=Math.max(-.92,Math.min(.92,filtered[index]*gain)); outputSum+=output[index]*output[index]; } return {samples:output,peak,robustPeak,rms,outputRms:Math.sqrt(outputSum/output.length),gain,enhanced:gain>1.05}; }
function pcmWavBlob(rec){ if(!rec.pcmChunks?.length) return null; const downsampled=downsamplePcm(mergePcmChunks(rec.pcmChunks),rec.pcmSampleRate||48000,16000); if(!downsampled.length) return null; const normalized=normalizePcmSamples(downsampled); rec.pcmStats=normalized; const samples=normalized.samples; const buffer=new ArrayBuffer(44+samples.length*2); const view=new DataView(buffer); const write=(offset,value)=>{for(let index=0;index<value.length;index++) view.setUint8(offset+index,value.charCodeAt(index))}; write(0,'RIFF'); view.setUint32(4,36+samples.length*2,true); write(8,'WAVE'); write(12,'fmt '); view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true); view.setUint32(24,16000,true); view.setUint32(28,32000,true); view.setUint16(32,2,true); view.setUint16(34,16,true); write(36,'data'); view.setUint32(40,samples.length*2,true); for(let index=0;index<samples.length;index++){ const sample=Math.max(-1,Math.min(1,samples[index])); view.setInt16(44+index*2,sample<0?sample*0x8000:sample*0x7fff,true); } return new Blob([buffer],{type:'audio/wav'}); }
async function startPcmCapture(rec){ const AudioContextClass=window.AudioContext||window.webkitAudioContext; if(!AudioContextClass) return false; const audioContext=new AudioContextClass(); if(audioContext.state==='suspended') await audioContext.resume(); if(!audioContext.createMediaStreamSource||!audioContext.createScriptProcessor){ await audioContext.close?.(); return false; } rec.audioContext=audioContext; rec.pcmSampleRate=audioContext.sampleRate||48000; rec.pcmChunks=[]; rec.audioSource=audioContext.createMediaStreamSource(rec.stream); rec.audioProcessor=audioContext.createScriptProcessor(4096,1,1); rec.audioSink=audioContext.createGain(); rec.audioSink.gain.value=0; rec.audioProcessor.onaudioprocess=event=>{ if(rec.finished) return; const channel=event.inputBuffer?.getChannelData?.(0); if(channel?.length) rec.pcmChunks.push(new Float32Array(channel)); }; rec.audioSource.connect(rec.audioProcessor); rec.audioProcessor.connect(rec.audioSink); rec.audioSink.connect(audioContext.destination); return true; }
async function startAudioMeter(rec){ const AudioContextClass=window.AudioContext||window.webkitAudioContext; if(!AudioContextClass) return false; const audioContext=new AudioContextClass(); if(audioContext.state==='suspended') await audioContext.resume(); if(!audioContext.createAnalyser||!audioContext.createMediaStreamSource){ await audioContext.close?.(); return false; } rec.meterContext=audioContext; rec.meterSource=audioContext.createMediaStreamSource(rec.stream); rec.analyser=audioContext.createAnalyser(); rec.analyser.fftSize=1024; rec.analyser.smoothingTimeConstant=.72; rec.meterData=new Uint8Array(rec.analyser.fftSize); rec.meterSource.connect(rec.analyser); const sample=()=>{ if(rec.finished) return; rec.analyser.getByteTimeDomainData(rec.meterData); let sum=0,peak=0; for(const value of rec.meterData){ const sampleValue=(value-128)/128; sum+=sampleValue*sampleValue; peak=Math.max(peak,Math.abs(sampleValue)); } const rms=Math.sqrt(sum/rec.meterData.length); rec.rmsSum=(rec.rmsSum||0)+rms; rec.rmsSamples=(rec.rmsSamples||0)+1; rec.peak=Math.max(rec.peak||0,peak); state.voiceDiagnostics={...(state.voiceDiagnostics||{}),method:rec.method||'浏览器录音',level:Math.min(1,rms*5),label:rms>.015?'已经收到你的声音':'正在听，请靠近一点说话'}; const meter=$('.voice-meter i'); if(meter) meter.style.width=`${Math.round(Math.min(1,rms*5)*100)}%`; const label=$('.voice-diagnostic span'); if(label) label.textContent=state.voiceDiagnostics.label; rec.meterFrame=requestAnimationFrame(sample); }; sample(); return true; }
function clearRecordingResources(rec){ if(rec.finishTimer){clearTimeout(rec.finishTimer);rec.finishTimer=null;} if(listenTimer){clearTimeout(listenTimer);listenTimer=null;} if(recordingTicker){clearInterval(recordingTicker);recordingTicker=null;} if(rec.meterFrame) cancelAnimationFrame(rec.meterFrame); try{rec.recognition?.stop();}catch{} if(rec.audioProcessor) rec.audioProcessor.onaudioprocess=null; for(const node of [rec.audioSource,rec.audioProcessor,rec.audioSink,rec.meterSource,rec.analyser]){try{node?.disconnect?.()}catch{}} try{rec.audioContext?.close?.()}catch{} try{rec.meterContext?.close?.()}catch{} (rec.stream?.getTracks?.()||[]).forEach(track=>track.stop()); }
async function finishRecording(rec, reason='manual'){
 if(rec.finished) return;
 rec.finished=true;
 clearRecordingResources(rec);
 let transcript=(rec.finalText||rec.interimText||'').trim();
 const duration=Math.max(0,(performance.now()-(rec.startedAt||performance.now()))/1000);
 auditUiEvent('recording.stop','completed',{reason,durationMs:Math.round(duration*1000)},rec.traceId);
 const averageRms=rec.rmsSamples?rec.rmsSum/rec.rmsSamples:0;
 const pcmBlob=pcmWavBlob(rec);
 const nativeBlob=rec.chunks?.length?new Blob(rec.chunks,{type:rec.mimeType||rec.chunks[0]?.type||'audio/webm'}):null;
 const blob=pcmBlob||nativeBlob;
 const pcmRms=rec.pcmStats?.rms||0;
 const pcmPeak=rec.pcmStats?.peak||0;
 const enhanced=Boolean(rec.pcmStats?.enhanced);
 const outputRms=rec.pcmStats?.outputRms||pcmRms||averageRms;
 state.voiceDiagnostics={method:rec.method||'浏览器录音',deviceLabel:rec.deviceLabel||'',sampleRate:rec.pcmSampleRate||0,duration,rms:pcmRms||averageRms,peak:pcmPeak||rec.peak||0,bytes:blob?.size||0,mime:blob?.type||rec.mimeType||'',enhanced,level:Math.min(1,outputRms*5),label:enhanced?`录音完成，声音已增强 ${rec.pcmStats.gain.toFixed(1)} 倍，正在识别`:'录音完成，正在识别'};
 try{
  if(blob?.size){
   if(rec.pcmStats&&pcmRms<.0015&&pcmPeak<.004) throw new Error('MIC_TOO_QUIET');
   if(!rec.pcmStats&&rec.rmsSamples>=3&&averageRms<.003&&rec.peak<.012) throw new Error('MIC_TOO_QUIET');
    const audioData=await blobToDataUrl(blob);
    const result=await api('/api/speech/transcribe',{method:'POST',timeoutMs:20000,body:JSON.stringify({traceId:rec.traceId,audioData,mimeType:(blob.type||'audio/webm').split(';')[0]})});
    transcript=String(result.transcript||'').trim()||transcript;
  }
 }catch(error){
  console.warn('[voice] cloud transcription failed:',error?.message||error);
  if(error?.message==='MIC_TOO_QUIET'){ state.interactionMessage='麦克风收到的声音太小，可能选错了设备或离麦克风太远。请靠近一点再录一次。'; state.interactionTone='error'; }
  else if(transcript){ state.interactionMessage='云端识别暂时不可用，已使用设备识别到的文字。'; state.interactionTone='info'; }
  else { state.interactionMessage='录音里有声音，但云端没有识别出文字。请重试一次，或先用文字提问。'; state.interactionTone='error'; }
  state.voiceDiagnostics={...state.voiceDiagnostics,error:true,label:state.interactionMessage};
  auditUiEvent('recording.error','failed',{reason,mimeType:blob?.type||'',bytes:blob?.size||0,durationMs:Math.round(duration*1000)},rec.traceId,{errorType:error?.name||'Error'});
 }
 if(currentRec===rec) currentRec=null;
 state.recordingSeconds=0;
 if(transcript){ auditUiEvent('recording.finish','completed',{transcriptChars:transcript.length,mimeType:blob?.type||'',bytes:blob?.size||0,durationMs:Math.round(duration*1000)},rec.traceId); state.lastQuestion=transcript; setInteractionStage('idle'); childPageNoLoad(); state.teachbackPending?ask(transcript,'teachback',state.topicQuestion,'voice',rec.traceId):ask(transcript,'normal','','voice',rec.traceId); return; }
 setInteractionStage('error');
 if(state.interactionTone!=='error') state.interactionMessage=!blob?.size?'浏览器没有生成录音数据，请检查麦克风权限或换一个浏览器再试。':reason==='timeout'?'录音到时间了，但云端没有识别出文字。':'录音已收到，但没有识别出文字，请再录一次。';
 state.interactionTone='error'; childPageNoLoad(); $('#text')?.focus(); toast('这次没有听清，问题没有发送');
}
function startBrowserCaption(rec){
 const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!SR) return;
 const recognition=new SR(); rec.recognition=recognition;
 recognition.lang='zh-CN'; recognition.interimResults=true; recognition.continuous=true; recognition.maxAlternatives=1;
 recognition.onresult=event=>{ rec.interimText=''; for(let index=event.resultIndex;index<event.results.length;index++){ const value=event.results[index][0]?.transcript||''; if(event.results[index].isFinal) rec.finalText+=value; else rec.interimText+=value; } state.lastQuestion=(rec.finalText||rec.interimText||'').trim(); const heard=$('#heard'); if(heard) heard.textContent=state.lastQuestion||'正在听你说话……'; };
 recognition.onerror=event=>{ if(event.error==='not-allowed'&&!rec.audioContext&&!rec.mediaRecorder){ rec.permissionDenied=true; finishRecording(rec,'denied'); } };
 recognition.onend=()=>{ if(!rec.stopping&&!rec.finished&&currentRec===rec){ try{recognition.start();}catch{} } };
 try{ recognition.start(); }catch{}
}
async function startRecording(text){
 stopPlayback();
 const canRecord=canCaptureAudio();
 const canCaption=Boolean(window.SpeechRecognition||window.webkitSpeechRecognition);
 if(!canRecord&&!canCaption){ setInteractionStage('error'); state.interactionMessage='当前设备不能录音，请直接用文字提问。'; state.interactionTone='error'; childPageNoLoad(); text?.focus(); return; }
 const rec={traceId:newTraceId(),chunks:[],pcmChunks:[],finalText:'',interimText:'',stopping:false,finished:false,manualStop:false,stream:null,mediaRecorder:null,recognition:null,mimeType:'',method:'',startedAt:performance.now(),rmsSum:0,rmsSamples:0,peak:0,finish:reason=>finishRecording(rec,reason)};
 state.lastTraceId=rec.traceId;
 currentRec=rec; setInteractionStage('capturing'); state.recordingSeconds=0; state.lastQuestion=''; state.voiceDiagnostics={method:'正在启动麦克风',level:0,label:'请允许麦克风权限'}; state.interactionMessage='我在听。说完后，再点一次麦克风。'; state.interactionTone='info'; state.retryQuestion=''; childPageNoLoad();
 try{
  if(canRecord){
   rec.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
   const audioTrack=rec.stream.getAudioTracks?.()[0]||rec.stream.getTracks?.()[0]; rec.deviceLabel=String(audioTrack?.label||'默认输入设备').slice(0,80); rec.trackSettings=audioTrack?.getSettings?.()||{};
   if(currentRec!==rec){ clearRecordingResources(rec); return; }
   if(window.MediaRecorder){
    rec.mimeType=recorderMimeType();
    rec.mediaRecorder=rec.mimeType?new MediaRecorder(rec.stream,{mimeType:rec.mimeType}):new MediaRecorder(rec.stream);
    rec.method='MediaRecorder + PCM 增强';
    rec.mediaRecorder.ondataavailable=event=>{if(event.data?.size) rec.chunks.push(event.data)};
    rec.mediaRecorder.onerror=()=>{ if(!rec.stopping){ rec.stopping=true; finishRecording(rec,'error'); } };
    rec.mediaRecorder.onstop=()=>finishRecording(rec,rec.timedOut?'timeout':'manual');
    rec.mediaRecorder.start(250);
    await startPcmCapture(rec).catch(()=>false);
   }else if(await startPcmCapture(rec).catch(()=>false)){ rec.method='PCM WAV 兼容模式'; }
   else { throw new Error('当前浏览器无法生成可识别的录音'); }
   await startAudioMeter(rec).catch(()=>false);
  }else{
   rec.method='浏览器语音识别';
   startBrowserCaption(rec);
  }
  state.voiceDiagnostics={...(state.voiceDiagnostics||{}),method:rec.method,level:0,label:'正在听，请说一句完整的话'}; childPageNoLoad();
  auditUiEvent('recording.start','started',{method:rec.method,hasCloudAsr:canRecord},rec.traceId);
  recordingTicker=setInterval(()=>{ if(currentRec!==rec||rec.stopping) return; state.recordingSeconds+=1; const mic=$('#mic'); if(mic) mic.textContent=micText(); },1000);
  listenTimer=setTimeout(()=>{ if(currentRec!==rec||rec.stopping) return; rec.timedOut=true; stopListening(); },20000);
  toast('我在听。说完后，再点一次结束并回答');
 }catch(error){
  auditUiEvent('recording.error','failed',{phase:'start'},rec.traceId,{errorType:error?.name||'Error'}); clearRecordingResources(rec); if(currentRec===rec) currentRec=null; state.recordingSeconds=0; setInteractionStage('error'); state.interactionMessage=error?.name==='NotAllowedError'?'没有麦克风权限。请允许权限，或者直接用文字提问。':'录音没有启动成功，请再试一次或直接打字。'; state.interactionTone='error'; childPageNoLoad(); text?.focus();
 }
}
function bindChild(){
	 const send=$('#send'), text=$('#text'), mic=$('#mic');
	 const retryAction=()=>{ auditUiEvent('button.click','completed',{buttonId:'retry'}); ask(state.retryQuestion,state.retryFeedback,state.retryOriginalQuestion,state.retryInputMode); };
	 const retryAnswer=$('#retryAnswer');
	 if(retryAnswer) retryAnswer.onclick=retryAction;
	 const replay=$('#replayAnswer'); if(replay) replay.onclick=()=>{ auditUiEvent('button.click','completed',{buttonId:'replay'}); speak(state.lastAnswer,true); };
	 $$('.mode').forEach(b=>b.onclick=()=>{
	  const nextMode=b.dataset.mode||'ask';
	  if(nextMode===state.activityMode) return;
	  auditUiEvent('mode.change','completed',{fromMode:state.activityMode,toMode:nextMode});
	  state.activityMode=nextMode;
	  state.teachbackPending=false;
	  state.activeFeedback='';
	  const currentQuestion=state.topicQuestion.trim();
	  childPageNoLoad();
	  if(currentQuestion&&!state.isAsking){
	   const label=modeZh[nextMode]||'清楚解释';
	   state.interactionMessage=`正在用“${label}”重新回答这个问题…`;
	   state.interactionTone='info';
	   childPageNoLoad();
	   ask(currentQuestion,'normal','','mode');
	  }else toast('已切换：'+(modeZh[nextMode]||'清楚解释'));
	 });
	 const submitText=()=>state.teachbackPending?ask(text.value,'teachback',state.topicQuestion,'text'):ask(text.value,'normal','','text');
	 if(send) send.onclick=()=>{ auditUiEvent('button.click','completed',{buttonId:'send',inputChars:text?.value?.trim()?.length||0}); submitText(); };
 if(text) text.onkeydown=e=>{ if(e.key==='Enter') submitText(); };
	 $$('.demoq').forEach(b=>b.onclick=()=>{ auditUiEvent('button.click','completed',{buttonId:'demo_question'}); ask(b.textContent,'normal','','text'); });
	 $$('.voice-demo').forEach(b=>b.onclick=()=>{ if(state.isAsking) return; setInteractionStage('capturing'); state.lastQuestion=''; childPageNoLoad(); let demo='为什么月亮好像跟着我走？'; let i=0; const timer=setInterval(()=>{ i++; state.lastQuestion=demo.slice(0,i); const heard=$('#heard'); if(heard) heard.textContent=state.lastQuestion; if(i>=demo.length){ clearInterval(timer); setTimeout(()=>{setInteractionStage('idle');ask(demo,'normal','','voice')},180); } },35); });
	 $$('.chip').forEach(b=>b.onclick=()=>{
	  auditUiEvent('feedback.select','completed',{feedbackMode:b.dataset.fb||''});
  if(!state.topicQuestion){ toast('先问一个问题，再反馈哦'); return; }
	  if(b.dataset.fb==='teachback'){
	   state.teachbackPending=true; state.activeFeedback='teachback'; state.lastQuestion=''; setInteractionStage('idle'); childPageNoLoad();
	   $('#text')?.focus(); toast('请用自己的话讲讲，不用一模一样'); return;
	  }
	  state.teachbackPending=false; state.activeFeedback=b.dataset.fb; ask(state.topicQuestion,b.dataset.fb,'','feedback');
	 });
	 if(!mic) return;
	 mic.onclick=async()=>{
	  if(state.isAsking) return;
	  if(state.avatar==='speaking') stopPlayback();
	  if(currentRec){ auditUiEvent('button.click','completed',{buttonId:'mic_stop'},currentRec.traceId); stopListening(); return; }
	  await startRecording(text);
	  if(currentRec) auditUiEvent('button.click','completed',{buttonId:'mic_start'},currentRec.traceId);
	 }}

function cardHtml(c){ const cls=c.status==='known'?'known':c.status==='needs_review'?'needs_review':c.status==='learning'?'learning':''; return `<article class="knowledge" data-id="${esc(c.id)}"><div class="cardhead"><h2>${esc(c.concept)}</h2><span class="badge ${cls}">${statusZh[c.status]||esc(c.status)}</span></div><p><b>为什么这样判断：</b>${esc(c.evidence)}</p><p><b>之前比较好用的例子：</b>${esc(c.effectiveAnalogy||'暂时还没有合适的例子')}</p><div class="meter"><i style="width:${Math.round((c.confidence||0)*100)}%"></i></div><small class="muted">现有记录支持度 ${Math.round((c.confidence||0)*100)}% · 最近更新 ${esc((c.updatedAt||'').slice(0,19))}</small>${c.parentNote?`<p><b>家长备注：</b>${esc(c.parentNote)}</p>`:''}<div class="actions"><button data-act="known">✅ 孩子已经会讲</button><button data-act="needs_review">🤔 还需要再讲讲</button><button data-act="learning">✍️ 先继续观察</button><button data-act="rollback">↩️ 恢复上次记录</button><button class="danger" data-act="delete">删除</button></div></article>` }
function bindParent(){ $$('.knowledge button').forEach(button=>button.onclick=async()=>{ const id=button.closest('.knowledge').dataset.id; const act=button.dataset.act; const traceId=newTraceId(); auditUiEvent('button.click','completed',{buttonId:'memory_card_action',action:act,cardId:id},traceId); const note=act==='known'?'家长确认孩子能自己解释':act==='needs_review'?'家长确认还需要更简单的解释':act==='rollback'?'家长恢复上一次记录':''; button.disabled=true; try{ await api('/api/cards/'+id,{method:'PATCH',body:JSON.stringify({status:act==='delete'?'deleted':act,action:act,note})}); toast(act==='rollback'?'已恢复上一次记录':'家长确认已保存'); await (location.pathname==='/memory'?memoryPage():parentPage()); }catch(error){ auditUiEvent('request.error','failed',{operation:'memory_card_action',action:act,cardId:id},traceId,{errorType:error?.name||'Error'}); button.disabled=false; toast('操作没有完成：'+(error.message||'请稍后再试')); } }); const deleteButton=$('#deleteAll'); if(deleteButton) deleteButton.onclick=async()=>{ if(!confirm('确认彻底删除这个孩子保存在本机的记录？此操作无法撤销。')) return; const traceId=newTraceId(); auditUiEvent('button.click','completed',{buttonId:'delete_all_memory'},traceId); deleteButton.disabled=true; try{ await api('/api/data/delete',{method:'POST',body:JSON.stringify({childId:state.childId})}); toast('本地记录已删除'); await (location.pathname==='/memory'?memoryPage():parentPage()); }catch(error){ auditUiEvent('request.error','failed',{operation:'delete_all_memory'},traceId,{errorType:error?.name||'Error'}); deleteButton.disabled=false; toast('删除没有完成：'+(error.message||'请稍后再试')); } } }

// ---- 全站 v4：建档、家长端、成长记录与成长可视化 ----
function pct(v){ return Math.round(Number(v||0)*100) }
function barCell(label,value,sub=''){ return `<div class="growth-cell"><div class="growth-top"><b>${esc(label)}</b><span>${Math.round(value||0)}%</span></div><div class="meter soft"><i style="width:${Math.max(4,Math.min(100,value||0))}%"></i></div>${sub?`<small class="muted">${esc(sub)}</small>`:''}</div>` }
function companionArchitecture(){
 const stages=[
  ['🛡️','安全守护员','遇到危险或隐私问题，请大人一起处理'],
  ['🧭','学习规划师','确定事实边界、因果顺序和这次该讲多深'],
  ['🧸','儿童讲解员','按照所选模式，用真实模型重新组织回答'],
  ['🔎','回答检查员','检查事实、年龄、模式结构和安全边界'],
  ['🫧','记忆整理员','只记录通过门禁的回答和可核对证据'],
 ];
 return `<section class="companion-architecture" data-tour="architecture"><div class="architecture-head"><div><p class="eyebrow">不是一个模型独自回答</p><h2>一支有分工的回答小队</h2><p>Supervisor 会按问题难度和本轮模式安排角色。简单问题不让所有模型排队；故事、观察、复述或容易出错的问题，才增加更严格的规划与独立审核。</p></div><span class="architecture-pulse"><i></i>按需要路由角色</span></div><div class="companion-loop"><div class="loop-core"><span>✦</span><b>回答协调员</b><small>控制质量、顺序<br>和等待时间</small></div>${stages.map((stage,index)=>`<article style="--step:${index}"><span>${stage[0]}</span><div><b>${stage[1]}</b><small>${stage[2]}</small></div></article>`).join('')}</div><div class="evolution-principles"><div><i>01</i><b>四种模式有不同契约</b><span>清楚解释、详细解答、故事和观察分别检查句数、结构、事实回归与家长陪同。</span></div><div><i>02</i><b>记忆不是旧答案缓存</b><span>保存事实证据、孩子反馈和家长确认；下一轮仍由真实 LLM 按最新情况重新生成。</span></div><div><i>03</i><b>能晚点做的，不让孩子等</b><span>语音、家长摘要和非关键整理属于工具或延后任务，不阻塞儿童回答关键路径。</span></div></div></section>`;
}
function setupPreview(p){ const interests=(p.interests||[]).slice(0,4), fam=(p.familiarItems||[]).slice(0,4); return `<div class="setup-preview" data-tour="profile-preview"><div class="preview-orb"><div class="avatar mini-face"><div class="mouth"></div></div></div><p class="eyebrow">现在填写的内容</p><h3>${esc(p.nickname||'孩子')} · ${esc(p.age||5)}岁</h3><p class="muted">下次遇到新问题，会先看这里的最新内容，再决定讲多长、要不要举例。</p><div class="tag-cloud">${interests.map(x=>`<span>喜欢：${esc(x)}</span>`).join('')}${fam.map(x=>`<span>熟悉：${esc(x)}</span>`).join('')}</div><div class="level-card"><b>孩子更喜欢这样听</b><p>${esc(p.explanationPreference||'先直接回答，再举一个简单的例子')}</p></div><div class="mini-flow"><i>孩子现在的情况</i><i>听清问题</i><i>看看相关记录</i><i>家长随时能改</i><i>重新组织回答</i></div></div>` }
async function setupPage(){ const p=await loadProfile(); shell(`<section class="setup-layout-v5"><div class="setup-main-column"><div class="setup-intro-v5" data-tour="setup-hero"><p class="eyebrow">先填几项，让回答更贴近孩子</p><h1 class="title compact-title">从孩子熟悉的事物开始讲</h1><p class="sub">昵称、年龄、平时喜欢什么、熟悉什么，都会影响这次回答。孩子长大了，或者最近的兴趣变了，家长随时可以回来更新。</p><div class="setup-points"><div><b>按现在的年龄讲</b><span>少用术语，句子短一点</span></div><div><b>从熟悉的东西开始</b><span>比如恐龙、太空、积木</span></div><div><b>有变化就回来改</b><span>保存后，下一次回答就会生效</span></div></div></div><form id="setupForm" class="panel setup-form-v4" data-tour="profile-form"><div class="form-head"><div><p class="eyebrow">先告诉我们这些</p><h2>孩子小档案</h2></div><span class="badge known">回答前会重新读取</span></div><div class="setupgrid"><label>孩子昵称<input name="nickname" value="${esc(p.nickname)}"></label><label>年龄<select name="age">${[4,5,6,7].map(n=>`<option ${p.age==n?'selected':''}>${n}</option>`).join('')}</select></label><label class="span2">孩子喜欢什么<textarea name="interests" rows="2" placeholder="例如：恐龙、太空、积木">${esc((p.interests||[]).join('、'))}</textarea></label><label class="span2">孩子熟悉什么<textarea name="familiarItems" rows="2" placeholder="越具体越好：手电筒、浴缸、小汽车、远处的大山">${esc((p.familiarItems||[]).join('、'))}</textarea></label><label class="span2">孩子更喜欢怎么听<input name="explanationPreference" value="${esc(p.explanationPreference||'先直接回答，再举一个简单的例子')}"></label><label>自动朗读<select name="autoSpeak"><option value="true" ${p.voicePreference?.autoSpeak?'selected':''}>开启</option><option value="false" ${!p.voicePreference?.autoSpeak?'selected':''}>关闭</option></select></label><label>朗读速度<input name="speechRate" type="number" step="0.01" min="0.6" max="1.2" value="${p.voicePreference?.rate||0.92}"></label><label>家长 PIN<input name="parentPin" value="1234" type="password"></label></div><div id="setupStatus" class="interaction-notice error setup-status" role="alert" hidden></div><div class="actions form-actions"><button class="primary">保存，去好奇星球</button><a class="pillbtn subtle" href="/child">先看看示例</a><a class="pillbtn subtle" href="/parent">直接看家长小记</a></div></form></div><aside class="setup-side-column">${setupPreview(p)}<div class="panel principle"><p class="eyebrow">这些内容会留下来</p><h3>记住有用的信息，但不照搬旧答案</h3><ul><li>记下孩子问过什么、哪里还没听懂。</li><li>保留孩子自己的讲法和家长确认。</li><li>记住哪些例子曾经帮孩子听懂。</li><li>下次回答仍以最新年龄、档案和提醒为准。</li></ul></div></aside></section>`); $('#setupForm').onsubmit=async e=>{ e.preventDefault(); const form=e.target; const button=form.querySelector('button.primary'); const status=$('#setupStatus'); if(!form.checkValidity()){ form.reportValidity(); return; } button.disabled=true; button.textContent='正在保存…'; status.hidden=true; try{ const saved=await api('/api/profile',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(form).entries()))}); state.profile=saved; toast('已保存，下次回答会使用这份最新档案'); history.pushState(null,'','/child'); await childPage(); }catch(error){ button.disabled=false; button.textContent='保存，去好奇星球'; status.textContent='保存没有完成：'+(error.message||'请稍后再试'); status.hidden=false; toast('保存没有完成，请检查后重试'); } } }
function memoryStats(data){ const s=data.memoryStats||{}; return `<div class="memory-layers v4"><div><b>${s.chat||0}</b><span>聊天记录</span><small>问题 / 回答 / 反馈</small></div><div><b>${s.cognitive||0}</b><span>理解记录</span><small>在明白什么 / 为什么判断</small></div><div><b>${s.preference||0}</b><span>偏好记录</span><small>爱听的例子 / 句子长短</small></div><div><b>${s.safety||0}</b><span>安全提醒</span><small>拦截 / 转家长</small></div></div>` }
function levelPanel(data){ const l=data.ageLevel||{}; return `<div class="level-panel"><div><p class="eyebrow">现在解释得合不合适</p><h2>${esc(l.name||'记录还不多')}</h2><p>${esc(l.rule||'先用短句和具体事物解释。')}</p></div><div class="level-ring" style="--p:${Math.max(0,Math.min(100,l.score||0))}"><b>${Math.round(l.score||0)}</b><span>合适度</span></div></div>` }
function growthPanel(data){ return `<div class="panel growth-panel" data-tour="parent-growth"><div class="section-head"><div><p class="eyebrow">最近几次讲得怎么样</p><h2>根据聊天、孩子反馈和家长确认整理</h2></div><span class="badge learning">使用最新记录</span></div><div class="growth-grid">${(data.agentGrowth||[]).map(x=>barCell(x.label,x.value)).join('')}</div><div class="trend-bars">${(data.evolutionTrend||[]).map(d=>`<div><i style="height:${Math.max(8,Math.min(96,(d.questions||0)*18+10))}px"></i><span>${esc(d.day)}</span><small>${d.questions||0}问</small></div>`).join('')||'<p class="muted">有了聊天后，这里会显示每天的提问和反馈。</p>'}</div></div>` }
function memoryGraph(data){ const g=data.memoryGraph||{nodes:[]}; return `<div class="panel memory-graph" data-tour="memory-graph"><div class="section-head"><div><p class="eyebrow">这些记录从哪里来</p><h2>下一次回答会重新读取哪些线索</h2></div><span class="badge known">能回看</span></div><div class="graph-canvas">${(g.nodes||[]).map((n,i)=>`<div class="mem-node ${esc(n.kind)} ${esc(n.status||'')}" style="--s:${n.size||48}px;--x:${12+(i*23)%72}%;--y:${18+(i*31)%62}%"><b>${esc(n.label)}</b><small>${esc(n.meta||'')}</small></div>`).join('')}<svg viewBox="0 0 100 100" preserveAspectRatio="none">${(g.edges||[]).map((e,i)=>`<path d="M ${16+(i*19)%68} ${24+(i*17)%56} C 45 10, 55 90, ${24+(i*29)%62} ${34+(i*23)%52}"/>`).join('')}</svg></div><div class="memory-legend"><span class="profile">孩子小档案</span><span class="chat">聊天记录</span><span class="cognitive">理解记录</span><span class="preference">偏好记录</span><span class="parent">家长提醒</span></div></div>` }
function chatMemory(data){ const rows=(data.recentChat||[]).slice(-10); return `<div class="panel chat-memory" data-tour="chat-memory"><div class="section-head"><div><p class="eyebrow">聊天记录</p><h2>问题、回答和当时的反馈都在这里</h2></div></div><div class="chat-list">${rows.map(m=>`<div class="chat-row ${m.role}"><b>${m.role==='user'?'孩子':'回答'}</b><p>${esc(m.text||'')}</p><small>${esc((m.createdAt||'').slice(0,16).replace('T',' '))} · ${feedbackZh[m.feedbackMode]||'正常'} · ${modeZh[m.activityMode||'ask']||'问一问'}</small></div>`).join('')||'<p class="muted">还没有聊天记录。去儿童端问几个问题即可生成。</p>'}</div></div>` }
function timelinePanel(data){ return `<div class="panel evo-timeline"><div class="section-head"><div><p class="eyebrow">最近留下的记录</p><h2>重要线索，都能回头核对</h2></div></div><div class="timeline rich">${(data.memoryTimeline||[]).map(t=>`<div class="turn"><small>${esc(t.time)}</small><b>${esc(t.title)}</b><p>${esc(t.detail)}</p></div>`).join('')||'<p class="muted">有了互动后，这里会按时间列出重要记录。</p>'}</div></div>` }
function topicCloud(data){ const arr=data.topicDistribution||[]; return `<div class="topic-cloud">${arr.map(t=>`<span style="--w:${Math.min(100,30+t.count*18)}%"><b>${esc(t.topic)}</b><i>${t.count}次</i></span>`).join('')||'<span><b>暂无主题</b><i>0次</i></span>'}</div>` }
function parentCoachPanel(data){ const c=data.parentCoach||{}; const contract=data.companionContract?.parent||[]; return `<div class="panel coach-panel"><p class="eyebrow">家长关心的事</p><h2>${esc(c.title||'现在的节奏挺合适')}</h2><p class="muted suggest">${esc(c.detail||'继续保持短句、亲子观察和复述。')}</p><div class="coach-metrics"><span><b>${c.questions||0}</b>孩子提问</span><span><b>${c.feedback||0}</b>听不懂后调整</span><span><b>${c.safety||0}</b>安全提醒</span></div><div class="contract-list">${contract.map(x=>`<p>✓ ${esc(x)}</p>`).join('')}</div></div>` }
function levelAlignmentPanel(data){ const l=data.levelAlignment||{}; const anchors=l.anchors||[]; return `<div class="panel level-align-panel"><p class="eyebrow">这次准备怎么讲</p><h2>${esc(l.title||'按孩子现在的情况回答')}</h2><p class="muted suggest">${esc(l.explainRule||'先把问题直接讲清楚，需要时再补一个简单例子。')}</p><div class="align-rules"><span><b>${esc(l.sentenceBudget||'用短句')}</b>每句话多长</span><span><b>${esc(l.conceptBudget||'一次讲一个重点')}</b>这次讲多少</span></div><div class="tag-cloud compact-tags">${anchors.map(x=>`<span>孩子熟悉：${esc(x)}</span>`).join('')}</div><p class="muted avoid-rule">${esc(l.avoidRule||'少用术语，不硬套比喻，也不突然考孩子。')}</p></div>` }
function modePlaybookPanel(data){ const dist=data.modeDistribution||[]; const total=Math.max(1,dist.reduce((n,x)=>n+(x.count||0),0)); return `<div class="panel mode-panel"><p class="eyebrow">孩子没听懂时，可以试试</p><h2>有时直接回答，有时讲故事或一起观察</h2><div class="mode-bars">${dist.map(x=>`<span><b>${esc(x.label)}</b><i style="width:${Math.max(8,Math.round((x.count||0)/total*100))}%"></i><em>${x.count||0}次</em></span>`).join('')||'<p class="muted">用过几种回答方式后，这里会显示各自用了多少次。</p>'}</div><div class="playbook-list">${(data.modePlaybook||[]).map(x=>`<div><b>${esc(x.label)}</b><p>${esc(x.bestFor)}</p><small>${esc(x.parentHint)}</small></div>`).join('')}</div></div>` }
function tracePanel(data){ const trace=data.latestTrace||[]; return `<div class="panel trace-panel" data-tour="trace"><p class="eyebrow">这次回答怎么来的</p><h2>回答前做了这些检查</h2><div class="trace-steps">${trace.map((x,i)=>`<div><span>${i+1}</span><b>${esc(x.label)}</b><p>${esc(x.detail)}</p></div>`).join('')||'<p class="muted">孩子提问后，这里会显示做过哪些安全检查、参考了哪些事实和记录。</p>'}</div></div>` }
function agentSystemPanel(data){ const system=data.agentSystem||{}; const run=data.latestAgentOrchestration||{}; const statuses=new Map((run.agents||[]).map(agent=>[agent.id,agent])); return `<div class="panel agent-system-panel" data-tour="agent-system"><div class="section-head"><div><p class="eyebrow">多智能体协作</p><h2>每个角色负责什么</h2></div><span class="badge known">可审计，不展示思维链</span></div><p class="muted suggest">这是 Supervisor 路由系统，不是让多个模型无限讨论或投票。关键角色同步完成，家长摘要等任务延后处理。</p><div class="agent-system-grid">${(system.agents||[]).map(agent=>{const latest=statuses.get(agent.id);return `<article><div><b>${esc(agent.label)}</b><span class="agent-status ${esc(latest?.status||'idle')}">${esc(latest?agentStatusZh(latest.status):(agent.criticalPath?'关键路径':'按需启用'))}</span></div><p>${esc(agent.purpose)}</p>${latest?`<small>${esc(latest.outcome||'')} · ${Math.max(0,latest.durationMs||0)}ms</small>`:''}</article>`}).join('')||'<p class="muted">完成一次提问后，这里会显示角色分工和本轮状态。</p>'}</div><div class="agent-tools"><b>工具层，不作为认知 Agent</b>${(system.tools||[]).map(tool=>`<span>${esc(tool.label)}</span>`).join('')}</div></div>`; }
function parentSnapshotPanel(data,cards,needs){ const level=data.ageLevel||{}; const topics=(data.topicDistribution||[]).slice(0,3); const known=cards.filter(card=>card.status==='known').length; const learning=cards.filter(card=>card.status==='learning').length; return `<div class="panel parent-snapshot"><p class="eyebrow">孩子最近的情况</p><h2>${esc(level.name||'记录还不多')}</h2><p class="muted suggest">${esc(level.rule||'现在更适合用短句、熟悉的东西和具体例子来解释。')}</p><div class="snapshot-grid"><span><b>${known}</b>已经能自己讲</span><span><b>${learning}</b>还需要多观察</span><span><b>${needs.length}</b>需要大人陪一下</span></div><div class="tag-cloud compact-tags">${topics.map(topic=>`<span>最近爱问：${esc(topic.topic)}</span>`).join('')||'<span>最近爱问：记录还不够</span>'}</div></div>` }
function parentInsightPanel(data,cards,needs){ const strongest=cards.find(card=>card.status==='known')||cards[0]; const needsText=needs[0]?.concept||'暂时没有明显卡住的地方'; const preference=(data.preferenceMemories||[])[0]; return `<div class="panel parent-insights"><p class="eyebrow">给家长的摘要</p><h2>最近的聊天里，可以先看看这些</h2><div class="insight-list"><div><b>最近常问</b><p>${esc((data.topicDistribution||[]).slice(0,2).map(topic=>topic.topic).join('、')||'还在观察孩子最近常问什么')}</p></div><div><b>目前比较会讲</b><p>${strongest?esc(`${strongest.concept}：现有记录支持 ${Math.round((strongest.confidence||0)*100)}%`):'还需要多聊几轮才看得出来'}</p></div><div><b>可能还没完全懂</b><p>${esc(needsText)}</p></div><div><b>之前怎样讲比较有用</b><p>${esc(preference?.effectiveAnalogy||preference?.evidence||'先用孩子熟悉的东西举例，再一点点补充')}</p></div></div></div>` }
function parentNextStepPanel(data,needs){ const contract=data.companionContract?.parent||[]; const firstNeed=needs[0]; return `<div class="panel parent-next" data-tour="parent-next"><p class="eyebrow">今天可以怎么陪</p><h2>${firstNeed?'先陪孩子聊聊这个问题':'保持现在的节奏就好'}</h2><p class="muted suggest">${firstNeed?`孩子可能还没完全理解「${esc(firstNeed.concept)}」。可以先请孩子用自己的话讲一遍，再补一个生活里的例子。`:esc(data.suggestion||'继续鼓励孩子问“为什么”。每次聊清楚一个小问题，就很好。')}</p><div class="contract-list">${contract.slice(0,3).map(item=>`<p>✓ ${esc(item)}</p>`).join('')}</div><a class="pillbtn primary" href="/memory">看看相关聊天和记录 →</a></div>` }
function parentTopicPanel(data){ return `<div class="panel topic-summary"><p class="eyebrow">好奇主题</p><h2>最近脑袋里常冒出的事</h2>${topicCloud(data)}<p class="muted suggest">${esc(data.suggestion||'有了更多问题后，这里会按主题整理近期记录。')}</p></div>` }
function parentNeedsPanel(needs){ return `<div class="panel"><p class="eyebrow">需要大人看一眼</p><h2>${needs.length} 个可能还没完全懂的地方</h2><p class="muted">这里先列出值得家长确认的内容，想看完整对话时可以去成长记录。</p>${needs.slice(0,3).map(card=>`<div class="mini-review"><b>${esc(card.concept)}</b><span>${Math.round((card.confidence||0)*100)}%</span></div>`).join('')||'<p class="muted">目前没有需要家长确认的内容。</p>'}</div>` }
function memoryEvolutionPanel(data){ const timeline=(data.memoryTimeline||[]).slice(0,8); const growth=(data.agentGrowth||[]).slice(0,4); return `<div class="panel memory-evolution" data-tour="evolution"><div class="section-head"><div><p class="eyebrow">这些记录后来怎么变了</p><h2>哪次回答有调整，为什么调整</h2></div><span class="badge learning">来自聊天和家长确认</span></div><div class="evolution-metrics">${growth.map(item=>`<span><b>${Math.round(item.value||0)}%</b>${esc(item.label)}</span>`).join('')||'<span><b>0%</b>还没有足够记录</span>'}</div><div class="evolution-list">${timeline.map((item,index)=>`<div><i>${index+1}</i><small>${esc(item.time)}</small><b>${esc(item.title)}</b><p>${esc(item.detail)}</p></div>`).join('')||'<p class="muted">以后这里会记下：哪次回答变短了、哪个例子帮孩子听懂了、哪些内容经过家长确认。</p>'}</div></div>` }
function memoryKnowledgeLibrary(data){ const cards=data.knowledgeCards||[]; return `<section class="section memory-detail-section"><div class="section-head"><div><p class="eyebrow">理解记录</p><h2>孩子聊过什么、现在理解到哪里，都放在这里</h2></div><a class="pillbtn subtle" href="/parent">回家长小记</a></div><div class="cards knowledge-grid">${cards.map(cardHtml).join('')||'<div class="knowledge">还没有理解记录。去儿童端聊几个问题后，这里就会慢慢有内容。</div>'}</div></section>` }
function preferenceMemoryPanel(data){ return `<div class="panel pref-panel"><p class="eyebrow">孩子更容易听懂什么</p><h2>之前哪些解释比较有帮助</h2><div class="timeline pref-list">${(data.preferenceMemories||[]).map(item=>`<div class="turn"><b>${esc(item.concept)}</b><p>${esc(item.evidence)}</p><small>${esc(item.effectiveAnalogy||'')}</small></div>`).join('')||'<p class="muted">现在还没有这类记录。孩子点“没听懂”或“举个例子”后，相关反馈会出现在这里。</p>'}</div></div>` }
function trendPlotHtml(data,range=7){ const all=(data.evolutionTrend||[]).slice(-Math.max(1,Math.min(30,Number(range)||7))); if(!all.length) return '<div class="trend-empty">还没有足够互动记录。完成几轮问答后，这里会按日期显示适配变化。</div>'; const width=760,height=280,left=48,right=18,top=20,bottom=42,plotW=width-left-right,plotH=height-top-bottom; const defs=[['understandingScore','理解线索','#7b61ff'],['questionDifficulty','问题难度','#f0a43c'],['answerAdaptation','解释适配','#43a874'],['independenceScore','独立表达','#4b9bd8']]; const x=i=>left+(all.length===1?plotW/2:(plotW*i/(all.length-1))); const y=v=>top+plotH-(Math.max(0,Math.min(100,Number(v)||0))/100)*plotH; const grid=[0,25,50,75,100].map(v=>`<g><line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}"/><text x="${left-10}" y="${y(v)+4}" text-anchor="end">${v}</text></g>`).join(''); const lines=defs.map(([key,label,color])=>{const points=all.map((item,i)=>`${x(i)},${y(item[key])}`).join(' ');const dots=all.map((item,i)=>`<circle cx="${x(i)}" cy="${y(item[key])}" r="3.5"><title>${esc(item.date||item.day)} · ${esc(label)} ${Math.round(Number(item[key])||0)}</title></circle>`).join('');return `<g class="trend-series" style="--series:${color}"><polyline points="${points}"/>${dots}</g>`}).join(''); const labelStep=Math.max(1,Math.ceil(all.length/7)); const labels=all.map((item,i)=>(i%labelStep===0||i===all.length-1)?`<text x="${x(i)}" y="${height-14}" text-anchor="middle">${esc(item.day||item.date||'')}</text>`:'').join(''); return `<svg class="adaptation-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="基于交互证据的适配趋势图"><g class="trend-grid">${grid}</g>${lines}<g class="trend-labels">${labels}</g></svg>` }
function adaptationTrendPanel(data){ const range=state.trendRange||7; const legends=[['理解线索','#7b61ff'],['问题难度','#f0a43c'],['解释适配','#43a874'],['独立表达','#4b9bd8']]; const methods=[['理解线索','认知卡可信度占 70%，当前理解状态占 30%。'],['问题难度','按问题长度、原因追问、回答模式和反馈类型估算。'],['解释适配','按回答是否通过交付、重试次数和反馈重答结果估算。'],['独立表达','按孩子复述结果，以及家长确认或已掌握记录估算。']]; return `<div class="panel adaptation-trend" data-tour="evolution"><div class="section-head trend-head"><div><p class="eyebrow">Agent 适配趋势</p><h2>基于交互证据的适配趋势</h2><p class="muted">纵轴统一为 0–100 分，展示回答如何依据孩子反馈逐步调整，不代表智力测评或模型本体自动变聪明。</p></div><label class="trend-range">查看范围<select id="trendRange"><option value="7" ${range===7?'selected':''}>最近 7 天</option><option value="14" ${range===14?'selected':''}>最近 14 天</option><option value="30" ${range===30?'selected':''}>最近 30 天</option></select></label></div><div id="trendPlot">${trendPlotHtml(data,range)}</div><div class="trend-legend">${legends.map(([label,color])=>`<span><i style="background:${color}"></i>${esc(label)}</span>`).join('')}</div><div class="trend-method-grid" aria-label="趋势分数计算说明">${methods.map(([label,description])=>`<article><b>${esc(label)}</b><span>${esc(description)}</span></article>`).join('')}</div><small class="muted">分数来自聊天、反馈、质量交付、复述和家长确认；0 分也可能只是当天没有对应证据，并不表示孩子能力为零。</small></div>` }
function bindTrend(){ const select=$('#trendRange'); if(!select) return; select.onchange=()=>{ state.trendRange=Math.max(7,Math.min(30,Number(select.value)||7)); const plot=$('#trendPlot'); if(plot) plot.innerHTML=trendPlotHtml(state.parent||{},state.trendRange); }; }
async function parentPage(){
 const data=await api('/api/parent/cards?childId='+state.childId);
 state.parent=data;
 const cards=data.knowledgeCards||[];
 const needs=data.confusions||[];
 shell(`<section class="parent-hero" data-tour="parent-hero"><div><p class="eyebrow">家长小记 / 一眼看懂</p><h1 class="title compact-title">孩子最近聊了什么、懂到哪里</h1><p class="sub">这里只保留家长最需要的摘要：最近主题、理解线索、仍然困惑的地方，以及今天可以怎样陪。详细对话和系统证据统一放在成长记录。</p></div>${levelPanel(data)}</section><section class="memory-section-group parent-summary-section"><div class="memory-section-heading"><span>01</span><div><p class="eyebrow">最近概览</p><h2>三张卡看完本周重点</h2></div><a class="pillbtn subtle" href="/memory">查看详细聊天与趋势 →</a></div><div class="parent-summary-grid" data-tour="parent-summary">${parentSnapshotPanel(data,cards,needs)}${parentInsightPanel(data,cards,needs)}${parentNextStepPanel(data,needs)}</div></section>`);
 bindParent();
}

function nav(){
 const p=location.pathname;
 return `<div class="topbar"><a class="brand" href="/setup" aria-label="好奇心伙伴"><div class="logo">✦</div><div><div>好奇心伙伴</div><small>陪孩子把为什么问下去</small></div></a><div class="nav"><a class="${p==='/setup'||p==='/'?'active':''}" href="/setup">首页</a><a class="${p==='/child'?'active':''}" href="/child">好奇星球</a><a class="${p==='/parent'?'active':''}" href="/parent">家长小记</a><a class="${p==='/memory'?'active':''}" href="/memory">成长记录</a><button class="guide-btn" id="guideBtn" data-tour="guide" title="打开当前页面的新手引导"><span>✦</span> 带我看看</button><button class="theme" id="themeBtn" title="切换明暗主题">◐</button></div></div>`;
}
async function memoryPage(){
 const data=await api('/api/parent/cards?childId='+state.childId);
 state.parent=data;
 shell(`<section class="parent-hero memory-page-hero" data-tour="memory-hero"><div><p class="eyebrow">成长记录 / 详细证据</p><h1 class="title compact-title">聊天、理解变化和 Agent 适配分开看</h1><p class="sub">这里保存详细聊天、孩子自己的讲法、解释偏好、家长确认和运行证据。旧答案不会直接复用；下一轮仍由真实 LLM 根据最新记录重新生成。</p></div>${memoryStats(data)}</section><section class="memory-section-group"><div class="memory-section-heading"><span>01</span><div><p class="eyebrow">详细聊天记录</p><h2>先回到孩子当时问了什么</h2></div></div>${chatMemory(data)}</section><section class="memory-section-group"><div class="memory-section-heading"><span>02</span><div><p class="eyebrow">孩子理解档案</p><h2>理解记录和表达偏好放在一起</h2></div></div><div class="memory-two-column">${memoryKnowledgeLibrary(data)}${preferenceMemoryPanel(data)}</div></section><section class="memory-section-group"><div class="memory-section-heading"><span>03</span><div><p class="eyebrow">Agent 适配</p><h2>看解释怎样随真实反馈调整</h2></div></div>${adaptationTrendPanel(data)}<div class="memory-two-column compact-columns">${memoryEvolutionPanel(data)}${timelinePanel(data)}</div></section><section class="memory-section-group"><div class="memory-section-heading"><span>04</span><div><p class="eyebrow">系统与运行证据</p><h2>需要核对时再展开</h2></div></div><details class="memory-system-details"><summary>查看多智能体分工、回答检查和记忆关系</summary><div class="memory-system-grid">${agentSystemPanel(data)}${tracePanel(data)}${memoryGraph(data)}</div></details></section><section class="memory-section-group"><div class="memory-section-heading"><span>05</span><div><p class="eyebrow">数据管理</p><h2>本机记录由家长控制</h2></div></div><div class="panel memory-data-panel"><p class="muted">如果想重新开始，可以删除这个孩子保存在本机的全部记录。此操作不会保留聊天正文或理解卡片。</p><div class="actions"><button id="deleteAll" class="danger">删除全部本地记录</button><a class="pillbtn primary" href="/parent">回家长小记</a></div></div></section>`);
 bindParent();
 bindTrend();
}

async function route(){ if(location.pathname==='/'||location.pathname==='/setup') return setupPage(); if(location.pathname==='/child') return childPage(); if(location.pathname==='/parent') return parentPage(); if(location.pathname==='/memory') return memoryPage(); return setupPage(); }
window.addEventListener('popstate',()=>{ auditUiEvent('navigation.click','completed',{navigationType:'history',path:location.pathname}); route().catch(error=>{console.error(error);toast('页面加载失败：'+(error.message||'请稍后再试'))})}); document.addEventListener('click',e=>{ const a=e.target.closest('a[href^="/"]'); if(!a) return; e.preventDefault(); const path=a.getAttribute('href'); auditUiEvent('navigation.click','completed',{navigationType:'link',path}); history.pushState(null,'',path); route().catch(error=>{console.error(error);toast('页面加载失败：'+(error.message||'请稍后再试'))}); });
route().catch(e=>{console.error(e); shell(`<div class="panel"><h1>启动失败</h1><p>${esc(e.message)}</p></div>`) });
