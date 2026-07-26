# 好奇心伙伴

一个面向 4–7 岁儿童的本地语音问答演示。孩子可以说出或输入问题，系统会先检查安全边界，再结合最新年龄、档案、近期记录和家长提醒组织回答。家长可以回看聊天、理解情况、表达偏好和安全提醒，也可以修正、恢复历史版本或删除记录。

项目使用 Python 标准库运行，不需要 Node.js。页面、API、模型配置、真实模型调用、本地降级和数据记录都放在当前仓库中。

## 本地启动

```bash
cd /Users/hehailin/Desktop/VibeCoding/child-agent
python3 app.py
```

默认地址：

- 建档：`http://127.0.0.1:8787/setup`
- 儿童端：`http://127.0.0.1:8787/child`
- 家长小记：`http://127.0.0.1:8787/parent`
- 成长记录：`http://127.0.0.1:8787/memory`

如果端口被占用：

```bash
PORT=8791 python3 app.py
```

## 主要页面

### 建档 `/setup`

家长填写昵称、年龄、兴趣、熟悉事物、解释偏好和朗读设置。孩子长大或情况变化后，可以随时更新；下一次回答会读取最新内容。

### 儿童端 `/child`

支持语音输入、文字输入、自动朗读和四种回答方式：

- 清楚解释
- 详细解答
- 讲个故事
- 一起观察

孩子可以反馈“没听懂”“再简单点”“举个例子”“继续问为什么”，或点击“我来讲讲”用自己的话复述。危险、隐私和不适合儿童自行操作的问题会转给家长。

### 家长小记 `/parent`

汇总近期问题、理解情况、表达偏好、家长确认和需要陪同处理的内容。页面展示的是记录与推断，不表示系统永久“了解”孩子。

### 成长记录 `/memory`

保存可回看的聊天、孩子的复述、哪里已经明白、哪里还需要再讲、表达偏好、家长提醒和安全事件。这些内容用于后续查找和家长总结，不会被当成固定答案直接照搬。

## 回答与记录如何工作

一次请求的主要流程：

1. 接收最终转写或文字问题。
2. 检查危险、隐私和不适合儿童自行操作的内容。
3. 读取最新孩子档案、相关可信记录和家长提醒。
4. 确认公共事实边界，由真实儿童回答模型按年龄和所选模式重新生成回答。
5. 进行本地规则检查；不合格时要求真实模型重写，高风险内容再交给独立模型审核。
6. 返回文字，前端立即开始朗读。
7. 保存本轮聊天与证据，再更新家长侧派生视图。

### 不缓存个性化答案

孩子会长大，年龄、兴趣、理解情况和家长提醒都会变化。因此项目不跨请求缓存某个孩子的完整回答。每次都会按最新情况重新生成。

可以版本化复用的内容包括：

- 系统安全规则
- 输出结构
- 少量人工复核的公共事实边界
- 不含孩子信息的稳定提示词前缀

需要每次读取最新值的内容包括：

- 年龄和孩子档案
- 家长提醒
- 当前反馈模式
- 相关理解证据和复述结果
- 近期聊天上下文

### 成长记录会保存什么

- 孩子问过的问题和回答
- “没听懂”“再简单点”等反馈
- 孩子的复述及其审核结果
- 对不同概念的理解情况
- 曾经有效的句式或例子
- 家长确认、备注、历史版本恢复和删除记录
- 安全事件

只有经过家长确认，或满足审核条件的正确复述，才会作为可信理解证据进入后续提示。未经验证的旧回答不会成为事实依据。

## 延迟设计

历史普通问题的真实模型回归平均约 4.64 秒，近似 P95 为 5.84 秒。2026-07-26 对“举个例子”和“再简单点”的真实反馈请求约为 11–13 秒，主要时间消耗在规划模型，而不是质量门禁重试。当前仍需继续精简或并行化反馈请求的规划路径。

当前已经避免阻塞的工作：

- 浏览器语音识别在录音过程中实时进行。
- 文字返回后，浏览器朗读不阻塞服务器。
- 回答显示后刷新家长卡片，不阻塞儿童端。
- 安全检查和最新资料准备可在没有数据依赖时并行。

后续适合放到后台的工作：

- 家长日报和跨天总结
- 主题统计、趋势图和记忆图谱
- 旧记录向量化与摘要压缩
- 非关键指标与日志

当前数据保存在单个 JSON 文件中，不适合直接增加并发写入。正式异步化前，应先迁移到 SQLite、PostgreSQL 或其他支持事务的存储，并加入幂等键、失败重试和顺序保证。当前架构、延迟和后续计划统一记录在 `EDUCATION_AGENT_REPORT.md`。

## 模型配置

项目从 `config/models.json` 读取配置。无密钥模板位于 `config/models.example.json`。

填写顶层供应商配置后，各组件默认继承：

```json
{
  "provider": {
    "type": "openai_compatible",
    "api_key": "填写 API Key",
    "base_url": "填写 OpenAI-compatible Base URL"
  }
}
```

当前运行链路会使用：

- `models.child_answer`：儿童回答、定向修复
- `models.parent_analysis`：事实规划、独立审核、回答复核

配置文件还预留了 ASR、TTS、Embedding、视觉理解和重排序组件；这些组件尚未全部接入当前运行链路。

也可以用环境变量临时覆盖儿童回答模型：

```bash
export LLM_API_KEY="..."
export LLM_BASE_URL="..."
export LLM_MODEL="qwen3.6-flash-2026-04-16"
export LLM_TIMEOUT_SECONDS="8"
python3 app.py
```

优先级：项目专用 `LLM_*` 环境变量 → 当前组件配置 → 顶层 `provider` → 兼容环境变量。

模型配置只在服务端读取，不会返回前端。服务端要求 HTTPS，并限制 Base URL 主机，避免把服务端变成任意网络代理。不要提交含真实密钥的 `config/models.json`。

没有完整真实模型配置时，服务会明确返回模型不可用提示，不会生成或展示 Mock 答案。测试代码可以模拟模型返回值，但生产运行链路不会调用预写回答函数。

## API

- `POST /api/chat`
- `GET /api/parent/cards?childId=child_demo`
- `PATCH /api/cards/:id`
- `POST /api/memory/extract`
- `POST /api/data/delete`
- `GET /api/profile`
- `POST /api/profile`

本地数据文件：`data/demo-db.json`。

## 测试

语法检查：

```bash
PYTHONPYCACHEPREFIX=/tmp/child-agent-pycache \
  python3 -m py_compile app.py tests/test_alignment.py tests/test_regression_helpers.py
```

核心单元测试：

```bash
python3 -m unittest tests.test_alignment tests.test_regression_helpers
```

真实模型回归：

```bash
python3 tests/real_llm_regression.py
```

浏览器交互检查：

```bash
python3 tests/browser_interaction_smoke.py
```

当前完整测试结论、真实模型回归结果和延迟记录见 `EDUCATION_AGENT_REPORT.md`。浏览器冒烟测试的 JSON 和截图默认写入 `/tmp/child-agent-browser-smoke`，可通过 `CHILD_AGENT_REPORT_DIR` 修改输出目录。

## 隐私与安全边界

- 默认不保存原始音频，只保存转写文本。
- 危险实验、自伤、伤害、色情、药物、陌生人联系和隐私索取会被拦截。
- 家长可以修改、恢复历史版本或删除记录。
- 数据删除接口会清理指定孩子的本地数据。
- 公共事实脚手架只提供稳定事实边界，不保存某个孩子的固定答案。
- 动物主观感受等无法直接确认的问题，会明确区分观察、推测和事实。
- 想象与现实会明确区分。

## 项目结构

```text
app.py                         Python Web/API 服务、模型工作流与本地数据层
config/models.example.json     无密钥模型配置模板
config/models.json             本地模型配置，不应提交真实密钥
data/demo-db.json              本地体验数据
static/index.html              单页应用入口
static/app.js                  页面、语音、交互与 API 调用
static/styles.css              页面样式
static/vendor/driverjs         本地页面导览组件
tests/                          单元、真实模型和浏览器测试
docs/                           设计与测试文档
EDUCATION_AGENT_REPORT.md       当前架构、测试、延迟和运行报告
```
