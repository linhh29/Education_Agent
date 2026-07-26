# 云玦参考架构的儿童 Agent 适配方案

日期：2026-07-25

## 目标

本项目不再把语音、回答、记忆和家长端当作彼此独立的小功能堆叠，而是参考云玦公开方案中的显式编排、Tool/Skill 分层、Trace-first 和可审计演化思想，迁移适合儿童陪伴场景的部分。

参考资料：

- `https://www.yunjuetech.com/tech`
- `https://github.com/YunjueTech/Yunjue-Agent`
- `/Users/hehailin/Downloads/YunjueTech.github.io-main.zip`
- `https://arxiv.org/abs/2601.18226`

这不是代码照搬。儿童场景必须额外保留安全门禁、家长控制、证据门槛、隐私最小化和不可直接复用旧答案等约束。

## 迁移对照

| 云玦组件或原则 | 当前项目原有实现 | 主要差距 | 本项目适配方式 | 优先级 |
| --- | --- | --- | --- | --- |
| 显式流程编排 | 依赖 `avatar`、`isAsking`、`currentRec` 等布尔状态 | 状态可互相冲突，界面难以准确说明卡在哪一步 | 建立 `capturing → transcribing → understanding → composing → checking → ready → speaking → reflecting` 状态机 | P0，已完成第一版 |
| Trace-first | 回答结束后拼装五条说明 | 缺少阶段状态、耗时、来源和失败代码 | 新增统一 `turnTrace`，记录阶段、状态、耗时、来源、摘要和失败代码，不记录思维链 | P0，已完成第一版 |
| Tool / Skill 分离 | 反馈按钮通过字符串提示词区分 | 策略没有稳定 ID、版本或适用范围 | 将“换角度、再简单、举例、深入原因、复述验证”登记为版本化公共解释策略 | P0，已完成第一版 |
| Tool-first | 事实脚手架和本地门禁分散存在 | 已审核知识仍经过模型二次改写，带来语言漂移和额外延迟 | 对通过 `curated_reference` 审核的事实路径直接编译儿童答案；未知问题继续走真实模型和独立审核 | P0，已完成第一版 |
| 路径复用 | 没有正式策略注册表 | 容易把“复用流程”误解为“缓存旧答案” | 只复用已验证的公共策略与安全路径；孩子旧答案不进入公共路径缓存 | P0，规则已明确 |
| 在线与离线分层 | `/api/chat` 同步完成回答、记忆和 JSON 持久化 | 非关键派生工作会继续拉长主链路 | 在线只保留安全、规划、回答、审核；迁移事务存储后，再异步做家长摘要、趋势和图谱 | P1，受当前 JSON 存储限制 |
| 可审计演化 | 有消息、记忆卡和家长反馈 | 缺少“候选策略 → 验证 → 家长确认 → 晋升”的生命周期 | 新增策略版本、验证状态和 Trace；后续增加家长可见的候选偏好卡 | P1 |
| 私有记忆与共享能力分离 | 已区分儿童档案、记忆卡和事实脚手架 | 数据模型中尚未明确作用域 | 公共策略标记 `public_explanation_strategy`；儿童证据继续按 `childId` 隔离 | P0，已完成第一版 |

## 当前交互编排

```text
语音路径
idle
  → capturing
  → transcribing
  → understanding
  → composing
  → checking
  → ready
  → speaking
  → reflecting
  → idle

文字路径
idle
  → understanding
  → composing
  → checking
  → ready
  → speaking / reflecting
  → idle
```

阶段转换集中由前端 `setInteractionStage()` 管理。页面显示“听清、理解、组织、检查、讲解”五个儿童可理解的步骤，而内部保留更细的 `ready` 和 `reflecting` 状态。

## Turn Trace 契约

服务端每轮返回：

```json
{
  "schemaVersion": "1.0",
  "strategy": {
    "id": "explain.reframe.v1",
    "scope": "public_explanation_strategy"
  },
  "stages": [
    {
      "stage": "checking",
      "status": "completed",
      "durationMs": 120,
      "source": "child_quality_gate",
      "summary": "事实、语言和安全门禁已通过。",
      "failureCode": ""
    }
  ],
  "containsChainOfThought": false
}
```

轨迹只保存可审计结果，不保存模型内部思维链、API Key、Base URL、原始音频或 Base64 音频。

## 解释策略注册表

| 策略 ID | 用户动作 | 可验证目标 |
| --- | --- | --- |
| `explain.direct.v1` | 正常提问 | 直接回答核心原因 |
| `explain.reframe.v1` | 没听懂 | 与上一答案明显不同，并换一个因果入口 |
| `explain.simplify.v1` | 再简单点 | 减少概念和术语，不删除真正原因 |
| `explain.example.v1` | 举个例子 | 使用可观察例子，并解释例子说明什么 |
| `explain.deeper-cause.v1` | 继续问为什么 | 比上一答案多一层原因，禁止浅层复述 |
| `learn.teachback.v1` | 我来讲讲 | 收集理解证据，不要求背标准答案 |

这些策略可以版本化、测试和逐步改进，但不会保存某个孩子的完整旧回答作为固定模板。

## Tool-first 已落地

当前实现复用公共事实边界和质量工具，但所有正常儿童答案仍由真实 LLM 生成：

```text
已人工审核且版本化的公共事实路径
  → 读取事实边界与禁说内容
  → 真实 LLM 组织适龄回答
  → 质量门禁
  → 通过后交付

未知问题或未通过事实审核的路径
  → 真实 LLM 规划
  → 真实 LLM 组织回答
  → 独立质量审核
  → 通过后交付；失败只显示明确错误
```

有效回答统一通过 `answerSource=real` 和 `generationSource=child_answer_model` 标识，并且必须满足 `deliveryValidated=true`。模型或审核失败时使用失败来源标识，只显示明确错误提示，不编译或复用本地儿童答案。来源字段同时进入 API 响应和消息记录，方便后续按路径统计质量与延迟。

这项迁移复用的是经过验证的公共能力，而不是缓存孩子以前说过的话：

- 公共事实边界、解释策略和安全门禁可以版本化复用，但不能直接作为最终答案展示。
- 孩子的提问、反馈、复述和家长确认继续作为私有理解证据保存。
- 某个孩子的完整旧答案不会晋升为其他孩子可用的公共答案。
- 年龄增长只会影响档案、表达预算和策略选择，不会被旧答案锁死。

2026-07-25 最新实测中，月亮问题调用真实回答模型约 `3.795s`。模型连续两次返回过短答案，质量门禁拒绝交付；系统显示明确失败提示，没有使用本地答案补齐，也没有把该轮写入认知记忆。

## 在线与延后任务边界

当前 JSON 单文件数据库不支持安全的后台并发写入，因此本轮没有用线程强行异步化记忆写入。第一阶段保持一次原子保存，避免覆盖或丢失数据。

迁移到 SQLite 或 PostgreSQL 后，建议拆成：

```text
孩子等待路径：ASR → 安全 → 规划/组织 → 质量门禁 → 返回文字 → TTS

延后记录路径：追加 Turn Event → 整理理解证据 → 家长摘要 → 趋势/图谱 → 策略候选
```

延后路径需要：

- 幂等键：`conversationId + chatTurnId + taskType`
- 事务或顺序事件日志
- 失败重试和最大次数
- 家长可见的失败状态
- 不保存原始录音

## 不直接照搬的部分

- 不允许模型自动生成并执行任意 Python 工具。
- 不允许未经验证的个人经验晋升为共享策略。
- 不允许系统在没有家长确认或充分证据时自动改变儿童档案结论。
- 不把个性化旧答案当作路径缓存。
- 不为了降低延迟而取消最终质量和安全门禁。

## 下一阶段

1. 将 JSON 存储迁移到事务数据库。
2. 增加幂等的 `deferred reflection` 接口与任务状态。
3. 家长端增加“本轮调整了什么、依据是什么、是否需要确认”的反思卡。
4. 为公共策略增加验证样例、通过率、版本历史和回滚能力。
5. 记录线上 ASR、LLM、审核、TTS 的 P50/P95/P99，而不是只看单次体验。
