(function () {
  'use strict';

  const app = document.getElementById('recordingApp');
  const params = new URLSearchParams(location.search);
  const clampScene = value => Math.max(1, Math.min(8, Number(value) || 1));
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  const state = {
    scene: clampScene(params.get('scene')),
    introLoaded: false,
    continuousPhase: 'idle',
    shadowStep: 0,
    feedOpen: '',
    memoryTab: 'chat',
    skillOpen: false,
    systemTab: 'roles',
    deleteAttempted: false,
    timers: [],
  };

  const scenes = [
    ['入口', '从家长端开始'],
    ['连续陪伴', '一次开始，连续对话'],
    ['个性化回答', '记忆改善沟通'],
    ['家长卡片', '原话、分析与建议'],
    ['记忆组织', '从证据到可用线索'],
    ['策略进化', '同一个问题的变化'],
    ['安全与审计', '可追溯、可纠正'],
    ['三阶段路线', '把好奇心问下去'],
  ];

  const cards = [
    { type: 'curiosity', title: '最近的好奇线索', heading: '太空 / 月亮', body: '最近 24 次提问中，这一主题出现了 8 次。先顺着孩子的问题继续聊，比急着扩展到很多知识点更合适。', suggestion: '今晚可以先问：“你现在最想弄明白月亮的哪一点？”', badge: '观察 · 92%' },
    { type: 'boundary', title: '理解边界', heading: '“光和影子”可以再用自己的话讲一次', body: '孩子能说出下午太阳低、影子变长，但还在追问斜着照为什么会让投影变长。', suggestion: '不要先纠正答案，可以问：“你觉得光和影子是怎么回事？讲给我听听。”', badge: '观察 · 63%' },
    { type: 'preference', title: '沟通方式', heading: '这样讲，孩子更愿意接着说', body: '孩子明确说先看到变化，再听原因会更容易理解；长回答会忘记后半段。', suggestion: '先说一句核心原因，再用先预测、再观察、最后只命名一个概念。', badge: '观察 · 86%' },
    { type: 'association', title: '值得留意的关联', heading: '关于“消失与被记得”的跨期关切，值得家长温和核对', body: '第 2 天因恐龙已经死亡而回避博物馆；第 19 天又问没有人记得是否等于真正消失。两次表达可能共享“消失”主题，但不能据此判断情绪或性格。', suggestion: '可以问：“你说的消失，是看不见了，还是担心再也不能和它有联系？”先听孩子怎么解释。', badge: '待核对的推测 · 61%' },
    { type: 'activity', title: '今天可以一起做', heading: '把一次问答带回真实生活', body: '孩子已经用语言提出问题，下一步适合通过观察或画画留下新的证据，而不是继续增加抽象讲解。', suggestion: '一起选一个熟悉的物体做五分钟观察：先让孩子猜，再一起看，最后请孩子画下变化。', badge: '建议 · 72%' },
  ];

  const quoteRows = [
    ['小满 · 第 2 天', '我不想去恐龙博物馆，因为恐龙都死掉了，看了会难过。', '2026-07-17'],
    ['小满 · 第 19 天', '如果以后没有人记得一只恐龙，它是不是就真的消失了？', '2026-08-03'],
  ];

  function clearTimers() {
    state.timers.forEach(timer => clearTimeout(timer));
    state.timers = [];
  }

  function later(callback, delay) {
    state.timers.push(setTimeout(callback, delay));
  }

  function setScene(scene) {
    clearTimers();
    state.scene = clampScene(scene);
    history.replaceState(null, '', '/video-demo?scene=' + state.scene);
    render();
  }

  function renderDock() {
    return `<div class="recording-dock" aria-label="录制镜头控制">
      <div class="dock-meta"><span class="record-dot"></span><b>录制演示模式</b><small>合成演示数据 · 不写入正式数据库</small></div>
      <div class="dock-scenes">${scenes.map((item, index) => `<button class="dock-scene ${state.scene === index + 1 ? 'active' : ''}" data-scene="${index + 1}"><i>0${index + 1}</i><span>${item[0]}</span></button>`).join('')}</div>
      <div class="dock-actions"><button class="dock-btn" data-action="previous" title="上一镜头">←</button><button class="dock-btn primary" data-action="next">下一镜头 →</button><a class="dock-exit" href="/parent">返回正式 Demo</a></div>
    </div>`;
  }

  function render() {
    app.innerHTML = `<div class="video-shell"><header class="video-topbar"><a class="video-brand" href="/video-demo"><span class="video-logo">✦</span><span><b>好奇心伙伴</b><small>录制专用演示</small></span></a><div class="video-title"><span>镜头 ${state.scene} / 8</span><b>${scenes[state.scene - 1][0]}</b></div><span class="synthetic-label">合成演示数据</span></header><main class="video-main">${sceneMarkup(state.scene)}</main>${renderDock()}</div>`;
    bind();
  }

  function avatarMarkup(mode = '') {
    return `<div class="demo-avatar ${mode}"><span class="demo-eye left"></span><span class="demo-eye right"></span><span class="demo-mouth"></span></div>`;
  }

  function childFrame({ eyebrow, title, transcript, answer, status, actions = '', extra = '' }) {
    return `<section class="record-scene child-scene"><div class="child-scene-head"><div><p class="scene-eyebrow">${eyebrow}</p><h1>${title}</h1></div><span class="child-status"><i></i>${status}</span></div><div class="child-layout"><div class="child-character">${avatarMarkup(status === '正在听' ? 'listening' : status === '正在讲' ? 'speaking' : '')}<b>小满的好奇伙伴</b><small>${status === '聊天进行中' ? '我会一直在这里听你说' : '先听清，再一起找答案'}</small></div><div class="child-conversation"><div class="child-transcript"><small>孩子说</small><p>${transcript || '点击开始，把你的“为什么”说出来。'}</p></div><div class="child-answer"><div class="answer-label"><span>伙伴回答</span>${extra}</div><p>${answer || '你好呀！把你的“为什么”告诉我，我们一起找答案。'}</p></div><div class="child-actions">${actions}</div></div></div></section>`;
  }

  function sceneMarkup(scene) {
    if (scene === 1) return sceneIntro();
    if (scene === 2) return sceneContinuous();
    if (scene === 3) return scenePersonalized();
    if (scene === 4) return sceneParentFeed();
    if (scene === 5) return sceneMemory();
    if (scene === 6) return sceneEvolution();
    if (scene === 7) return sceneSafety();
    return sceneClosing();
  }

  function sceneIntro() {
    return `<section class="record-scene intro-scene"><div class="intro-copy"><p class="scene-eyebrow">好奇心伙伴 · 第一阶段 Demo</p><h1>陪孩子把“为什么”问下去</h1><p class="lede">孩子的问题被认真接住，家长也能看见这些问题怎样变成可核对的陪伴线索。</p><div class="intro-actions">${state.introLoaded ? '<span class="load-success"><i></i>小满 · 使用 24 天 · 1,008 轮问答已载入</span><button class="primary scene-command" data-action="next">进入儿童端</button>' : '<button class="primary scene-command" data-action="load-intro">体验 24 天示例 Case</button>'}</div><p class="scene-note">本页为录制专用前端，内容来自固定合成 Case；正式产品仍从真实的儿童端、家长端和成长记录进入。</p></div><div class="intro-preview"><div class="preview-window"><div class="window-bar"><i></i><i></i><i></i><span>家长端 / 今日陪伴卡</span></div><div class="preview-head"><small>小满最近值得关注的几件事</small><b>5 条近期线索</b></div><div class="preview-card active"><span>01</span><b>太空 / 月亮</b><small>最近的好奇线索 · 92%</small></div><div class="preview-card"><span>02</span><b>光和影子</b><small>理解边界 · 63%</small></div><div class="preview-card"><span>03</span><b>先观察，再解释</b><small>沟通方式 · 86%</small></div></div></div></section>`;
  }

  function continuousContent() {
    const phase = state.continuousPhase;
    const map = {
      idle: { status: '准备好了', transcript: '点击开始，把你的“为什么”说出来。', answer: '你好呀！把你的“为什么”告诉我，我们一起找答案。', actions: '<button class="primary scene-command" data-action="start-continuous">开始聊天</button>' },
      listening1: { status: '正在听', transcript: '为什么月亮好像跟着我走？', answer: '我听到了，先想一想。', actions: '<button class="primary scene-command" data-action="end-continuous">结束聊天</button>' },
      thinking1: { status: '正在理解', transcript: '为什么月亮好像跟着我走？', answer: '我在把问题和小满以前聊过的内容接起来。', actions: '<button class="primary scene-command" data-action="end-continuous">结束聊天</button>' },
      answer1: { status: '正在讲', transcript: '为什么月亮好像跟着我走？', answer: '月亮离我们非常远。你走几步时，近处的树变化很明显，月亮的位置看起来却几乎没变。', actions: '<button class="primary scene-command" data-action="end-continuous">结束聊天</button>' },
      listening2: { status: '正在听', transcript: '那我跑快一点，它也会跑快吗？', answer: '我听到了，继续问吧。', actions: '<button class="primary scene-command" data-action="end-continuous">结束聊天</button>' },
      thinking2: { status: '正在理解', transcript: '那我跑快一点，它也会跑快吗？', answer: '我会继续用刚才的线索来回答。', actions: '<button class="primary scene-command" data-action="end-continuous">结束聊天</button>' },
      answer2: { status: '正在讲', transcript: '那我跑快一点，它也会跑快吗？', answer: '不会。你跑得快，近处的东西会很快换位置；月亮太远了，看起来还是在原来的地方。', actions: '<button class="primary scene-command" data-action="end-continuous">结束聊天</button>' },
      ongoing: { status: '聊天进行中', transcript: '还可以继续说你的下一个问题。', answer: '我会一直听着，直到你按下结束聊天。', actions: '<button class="primary scene-command" data-action="end-continuous">结束聊天</button>' },
      ended: { status: '聊天结束', transcript: '这段聊天已经保存到成长记录。', answer: '好，我们下次再接着问。', actions: '<button class="primary scene-command" data-action="start-continuous">再演示一次</button>' },
    }[phase] || null;
    return childFrame({ eyebrow: '镜头 2 · 连续陪伴', title: '一次开始，连续对话', transcript: map.transcript, answer: map.answer, status: map.status, actions: map.actions, extra: '<span class="answer-source">语音回答</span>' });
  }

  function sceneContinuous() {
    return `<div class="scene-kicker"><span>操作后等待状态自然变化</span><small>录制页按固定节奏播放两轮合成语音转写</small></div>${continuousContent()}<div class="process-strip"><span class="active">听清</span><i>→</i><span class="${['thinking1','thinking2','answer1','answer2','ongoing','ended'].includes(state.continuousPhase) ? 'active' : ''}">理解</span><i>→</i><span class="${['answer1','answer2','ongoing','ended'].includes(state.continuousPhase) ? 'active' : ''}">回答</span><i>→</i><span class="${['ongoing','ended'].includes(state.continuousPhase) ? 'active' : ''}">继续听</span></div>`;
  }

  function scenePersonalized() {
    const step = state.shadowStep;
    const firstAnswer = '下午太阳在天空中的位置比上午低，阳光斜着照向地面，影子就在地面延伸得更远，所以看起来更长。';
    const exampleAnswer = '下午太阳的位置比上午低。就像把手电筒放低，光线斜着照向积木，影子就会铺得更远。';
    const answer = step === 0 ? '点击开始，把这次问题说给伙伴听。' : step === 1 ? firstAnswer : exampleAnswer;
    const actions = step === 0 ? '<button class="primary scene-command" data-action="shadow-ask">影子为什么下午会变长？</button>' : step === 1 ? '<button class="subtle scene-command" data-action="shadow-example">举个例子</button>' : '<button class="primary scene-command" data-action="next">进入家长卡片</button>';
    const chips = step === 2 ? '<div class="memory-chips"><span>认知边界 · 影子仍需核对</span><span>表达偏好 · 先结论</span><span>光影情景 · 手电筒与积木</span><span>已验证 Skill · v3.1</span></div>' : '';
    return `<div class="scene-kicker"><span>固定问题：影子为什么下午会变长？</span><small>先给直接因果，孩子主动要例子后再调用熟悉物</small></div>${childFrame({ eyebrow: '镜头 3 · 记忆改善沟通', title: '同一个问题，回答得越来越像伙伴', transcript: step === 0 ? '准备好听你的问题。' : '影子为什么下午会变长？', answer, status: step === 0 ? '准备好了' : step === 1 ? '回答准备好了' : '回答已更新', actions, extra: step > 0 ? '<span class="answer-source">回答已通过检查</span>' : '' })}${chips}`;
  }

  function feedCard(card, index) {
    const open = state.feedOpen === card.type;
    const evidence = card.type === 'association' && open ? `<div class="feed-evidence-body"><div class="quote-grid">${quoteRows.map(row => `<blockquote><p>“${esc(row[1])}”</p><small>${row[0]} · ${row[2]}</small></blockquote>`).join('')}</div><div class="confidence-row"><b>61%</b><span>待核对的推测</span><small>这不是心理诊断或性格标签</small></div><button class="subtle scene-command" data-action="open-memory">查看完整聊天与记忆证据</button></div>` : '';
    return `<article class="feed-card ${card.type} ${open ? 'open' : ''}"><div class="feed-card-top"><span class="feed-index">0${index + 1}</span><span class="feed-badge">${card.badge}</span></div><p class="feed-label">${card.title}</p><h3>${card.heading}</h3><p class="feed-body">${card.body}</p><div class="feed-suggestion"><small>可以这样陪</small><p>${card.suggestion}</p></div><button class="feed-toggle" data-action="toggle-feed" data-card="${card.type}">${open ? '收起依据' : '为什么推送这张卡'}</button>${evidence}</article>`;
  }

  function sceneParentFeed() {
    return `<section class="record-scene parent-scene"><div class="parent-scene-head"><div><p class="scene-eyebrow">镜头 4 · 家长端</p><h1>每次打开，都刷新一组可核对的陪伴卡</h1><p>卡片来自小满 24 天的合成聊天记忆；观察、推测和建议使用不同颜色区分。</p></div><div class="parent-count"><b>5</b><span>近期线索</span><small>刚刚从记忆库刷新</small></div></div><div class="feed-grid">${cards.map(feedCard).join('')}</div></section>`;
  }

  function memoryTab(tab, label) {
    return `<button class="memory-tab ${state.memoryTab === tab ? 'active' : ''}" data-action="memory-tab" data-tab="${tab}">${label}</button>`;
  }

  function memoryChatView() {
    return `<div class="memory-view chat-view"><div class="view-heading"><div><small>原始对话层</small><h2>完整聊天与两条关联证据</h2></div><span>2,016 条消息 · 1,008 轮问答</span></div><div class="source-banner"><b>已定位卡片证据</b><span>卡片引用的两次表达在同一页面保留上下文，不隐藏来源。</span></div><div class="chat-evidence-grid">${quoteRows.map(row => `<article class="chat-evidence-row"><span class="role child">孩子</span><div><b>${row[0]}</b><p>${row[1]}</p><small>${row[2]} · 关联卡片原话</small></div></article>`).join('')}</div><div class="layer-footer"><span>检索粒度：原子消息</span><span>来源：孩子主动表达</span><span>可回到家长卡片</span></div></div>`;
  }

  function memoryUnderstandingView() {
    const knowledge = [
      ['月亮视运动与距离', 'known', '91%', '第 8 天能说出“月亮很远，所以位置变化很小”。'],
      ['太阳高度与影子长度', 'needs-review', '63%', '能说出下午太阳低、影子长，但还在追问斜着照。'],
      ['植物中的水运输', 'learning', '58%', '已经排除“叶子有嘴吸水”，正在建立根和内部通道。'],
    ];
    return `<div class="memory-view understanding-view"><div class="view-heading"><div><small>原子理解层</small><h2>孩子懂到哪里</h2></div><span>状态来自可核对表达和家长确认</span></div><div class="knowledge-strip">${knowledge.map(item => `<article class="knowledge-chip ${item[1]}"><div><b>${item[0]}</b><span>${item[2]}</span></div><small>${item[1] === 'known' ? '已经会讲' : item[1] === 'learning' ? '还在理解' : '请家长确认'}</small><p>${item[3]}</p></article>`).join('')}</div><div class="memory-layers-row"><span><b>对话</b><small>原始表达与时间</small></span><span><b>原子</b><small>理解与偏好</small></span><span><b>情景</b><small>连续几次互动</small></span><span><b>触发器</b><small>下一次回答要用的线索</small></span></div></div>`;
  }

  function memoryAdaptationView() {
    return `<div class="memory-view adaptation-view"><div class="view-heading"><div><small>检索与刷新层</small><h2>记忆怎样进入下一次回答</h2></div><span>四步链路 · 不展示内部思维链</span></div><div class="agent-flow"><article><b>Constructor</b><small>从聊天中抽取可核对的原子记录</small></article><i>→</i><article><b>Retriever</b><small>按实体、情景和远期触发器召回</small></article><i>→</i><article><b>Judge</b><small>核对相关性、冲突和置信边界</small></article><i>→</i><article><b>Refresher</b><small>更新状态，保留历史并淘汰过时线索</small></article></div><div class="adaptation-note"><b>当前例子</b><p>影子从“刚聊到”进入“请家长确认”；月亮从候选状态更新为“已经会讲”。旧证据仍可追溯，但不会继续冒充当前理解。</p></div></div>`;
  }

  function sceneMemory() {
    const view = state.memoryTab === 'understanding' ? memoryUnderstandingView() : state.memoryTab === 'adaptation' ? memoryAdaptationView() : memoryChatView();
    return `<section class="record-scene memory-scene"><div class="memory-scene-head"><div><p class="scene-eyebrow">镜头 5 · 成长记录</p><h1>从原话，到理解，再到下一次回答</h1><p>家长可以按层级查看，不需要一开始把全部技术细节堆在首页。</p></div><span class="parent-lock">家长端</span></div><div class="memory-tabs">${memoryTab('chat', '详细聊天')}${memoryTab('understanding', '理解档案')}${memoryTab('adaptation', 'Agent 适配')}</div>${view}<p class="boundary-note">合成演示数据用于说明记忆机制；待核对推测不是诊断，策略版本更新不等于基础模型参数训练。</p></section>`;
  }

  function sceneEvolution() {
    return `<section class="record-scene evolution-scene"><div class="evolution-scene-head"><div><p class="scene-eyebrow">镜头 6 · Agent 自进化</p><h1>同一个问题，策略怎样变得更适合小满</h1><p>保留历史证据，在隔离沙箱中回放验证，只有通过门禁的教育 Skill 才会进入下一次回答。</p></div><span class="evo-days"><b>24</b><small>天</small></span></div><div class="fixed-question"><small>固定问题</small><b>影子为什么下午会变长？</b></div><div class="answer-compare"><article><small>Day 1 · 冷启动</small><p>太阳的位置变化会影响影子的长度。你可以观察一下。</p><span>只有年龄和初始档案</span></article><article class="evolved"><small>Day 24 · 长期适配</small><p>因为下午太阳变低了，光会斜着照过来，积木挡出的暗处就会在地面铺得更长。还记得你说先看再解释吗？我们可以和家长用手电筒再看一次，你来猜把灯放低以后影子会怎样。</p><span>认知边界 + 偏好 + 情景 + 已验证 Skill</span></article></div><div class="evo-timeline"><div class="evo-step"><i>06</i><div><b>先结论后解释 · v1</b><p>两次长回答后，孩子主动反馈忘记了后半段。</p></div><span>生成</span></div><div class="evo-step"><i>13</i><div><b>观察-命名-解释 · v2</b><p>先观察时，孩子的复述更完整。</p></div><span>回放验证</span></div><div class="evo-step"><i>21</i><div><b>一次只引入一个概念 · v3.1</b><p>家长确认后进入活跃技能库。</p></div><span>灰度启用</span></div></div><div class="skill-detail"><button class="subtle scene-command" data-action="toggle-skill">${state.skillOpen ? '收起 v3.1 详情' : '查看 v3.1 验证详情'}</button>${state.skillOpen ? '<div class="skill-metrics"><span><b>37 次复用</b><small>光影与植物问答</small></span><span><b>31% → 11%</b><small>困惑反馈率</small></span><span><b>家长确认</b><small>进入活跃技能库</small></span></div><div class="evolution-loop"><span>审计</span><i>→</i><span>生成</span><i>→</i><span>回放验证</span><i>→</i><span>合并</span><i>→</i><span>灰度</span><i>→</i><span>回滚</span></div>' : ''}</div><p class="boundary-note">这里展示的是策略库和儿童理解模型的可追溯更新，不是基础大模型自行完成参数训练，也不构成儿童能力测评。</p></section>`;
  }

  function sceneSafety() {
    const tab = state.systemTab;
    const body = tab === 'trace' ? `<div class="system-view trace-view"><div class="trace-list"><div><i>1</i><b>安全守护员</b><span>通过儿童安全检查，不涉及危险实验或隐私索取。</span></div><div><i>2</i><b>学习规划师</b><span>建立事实、因果和适龄表达计划。</span></div><div><i>3</i><b>儿童讲解员</b><span>按认知边界与已验证 Skill 组织回答。</span></div><div><i>4</i><b>回答检查员</b><span>事实、表达和安全门禁通过。</span></div><div><i>5</i><b>记忆整理员</b><span>只写入通过交付门禁的可核对证据。</span></div></div></div>` : tab === 'data' ? `<div class="system-view data-view"><div class="data-row"><div><b>原始音频</b><span>默认不长期保存</span></div><em>家长可控</em></div><div class="data-row"><div><b>聊天正文与记忆</b><span>查看、纠正、删除都保留在家长端</span></div><em>家长可控</em></div><div class="data-row"><div><b>低置信推测</b><span>显示“待核对”，不会写成心理诊断或性格标签</span></div><em>边界明确</em></div><button class="danger scene-command" data-action="demo-delete">${state.deleteAttempted ? '录制模式不执行删除' : '查看删除入口'}</button>${state.deleteAttempted ? '<p class="inline-notice">正式产品的删除操作只由家长确认后执行；本录制页不会触碰任何真实数据。</p>' : ''}</div>` : `<div class="system-view roles-view"><div class="role-grid"><article><b>安全守护员</b><small>危险、隐私和家长介入边界</small><em>确定性检查</em></article><article><b>学习规划师</b><small>事实核心、认识边界和解释预算</small><em>模型辅助</em></article><article><b>儿童讲解员</b><small>把答案说成孩子听得懂的语言</small><em>模型辅助</em></article><article><b>回答检查员</b><small>事实、适龄、模式和安全门禁</small><em>独立复核</em></article><article><b>记忆整理员</b><small>整理可追溯的对话与理解证据</small><em>回答后整理</em></article><article><b>家长支持员</b><small>把跨期线索整理成家长卡片</small><em>回答后整理</em></article></div><p class="system-boundary">只展示阶段结果和证据摘要，不展示或依赖模型内部思维链。</p></div>`;
    return `<section class="record-scene safety-scene"><div class="safety-scene-head"><div><p class="scene-eyebrow">镜头 7 · 技术与安全</p><h1>每一条建议，都能回到证据和边界</h1><p>家长看到的是可理解的角色分工、回答轨迹和数据控制。</p></div><span class="parent-lock">家长端</span></div><div class="system-tabs"><button class="memory-tab ${tab === 'roles' ? 'active' : ''}" data-action="system-tab" data-tab="roles">角色分工</button><button class="memory-tab ${tab === 'trace' ? 'active' : ''}" data-action="system-tab" data-tab="trace">回答轨迹</button><button class="memory-tab ${tab === 'data' ? 'active' : ''}" data-action="system-tab" data-tab="data">数据管理</button></div>${body}</section>`;
  }

  function sceneClosing() {
    return `<section class="record-scene closing-scene"><div class="closing-copy"><p class="scene-eyebrow">镜头 8 · 结尾</p><h1>陪孩子把“为什么”问下去</h1><p>让每一次好奇都被认真接住，让孩子、家长和 Agent 在长期互动中一起成长。</p></div><div class="roadmap"><article><span>01</span><b>应用</b><p>验证连续陪伴、长期记忆和家长协同。</p></article><i>→</i><article><span>02</span><b>随身语音硬件</b><p>把持续陪伴带到孩子身边。</p></article><i>→</i><article><span>03</span><b>现实世界视觉理解</b><p>让孩子可以指着眼前的世界提问。</p></article></div><div class="closing-badge">孩子的每一个“为什么”，都值得一个耐心的回答。</div></section>`;
  }

  function bind() {
    app.querySelectorAll('[data-scene]').forEach(button => button.onclick = () => setScene(button.dataset.scene));
    app.querySelectorAll('[data-action="previous"]').forEach(button => button.onclick = () => setScene(state.scene - 1));
    app.querySelectorAll('[data-action="next"]').forEach(button => button.onclick = () => setScene(state.scene + 1));
    app.querySelectorAll('[data-action="load-intro"]').forEach(button => button.onclick = () => { state.introLoaded = true; render(); });
    app.querySelectorAll('[data-action="start-continuous"]').forEach(button => button.onclick = startContinuous);
    app.querySelectorAll('[data-action="end-continuous"]').forEach(button => button.onclick = () => { clearTimers(); state.continuousPhase = 'ended'; render(); });
    app.querySelectorAll('[data-action="shadow-ask"]').forEach(button => button.onclick = () => { state.shadowStep = 1; render(); });
    app.querySelectorAll('[data-action="shadow-example"]').forEach(button => button.onclick = () => { state.shadowStep = 2; render(); });
    app.querySelectorAll('[data-action="toggle-feed"]').forEach(button => button.onclick = () => { state.feedOpen = state.feedOpen === button.dataset.card ? '' : button.dataset.card; render(); if (state.feedOpen) requestAnimationFrame(() => app.querySelector('.feed-card.open')?.scrollIntoView({ behavior: 'smooth', block: 'center' })); });
    app.querySelectorAll('[data-action="open-memory"]').forEach(button => button.onclick = () => { state.memoryTab = 'chat'; setScene(5); });
    app.querySelectorAll('[data-action="memory-tab"]').forEach(button => button.onclick = () => { state.memoryTab = button.dataset.tab; render(); });
    app.querySelectorAll('[data-action="toggle-skill"]').forEach(button => button.onclick = () => { state.skillOpen = !state.skillOpen; render(); });
    app.querySelectorAll('[data-action="system-tab"]').forEach(button => button.onclick = () => { state.systemTab = button.dataset.tab; render(); });
    app.querySelectorAll('[data-action="demo-delete"]').forEach(button => button.onclick = () => { state.deleteAttempted = true; render(); });
  }

  function startContinuous() {
    clearTimers();
    state.continuousPhase = 'listening1';
    render();
    later(() => { state.continuousPhase = 'thinking1'; render(); }, 2600);
    later(() => { state.continuousPhase = 'answer1'; render(); }, 4700);
    later(() => { state.continuousPhase = 'listening2'; render(); }, 8000);
    later(() => { state.continuousPhase = 'thinking2'; render(); }, 10600);
    later(() => { state.continuousPhase = 'answer2'; render(); }, 12700);
    later(() => { state.continuousPhase = 'ongoing'; render(); }, 16000);
  }

  window.addEventListener('keydown', event => {
    if (event.key === 'ArrowRight') setScene(state.scene + 1);
    if (event.key === 'ArrowLeft') setScene(state.scene - 1);
  });

  render();
}());
