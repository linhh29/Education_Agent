# 运行审计日志

本目录用于保存教育 Agent 的运行时审计记录。服务启动后会按 JSON Lines 格式追加写入 `runtime-events.jsonl`，每行是一条独立 JSON 事件。

## 与聊天记忆的区别

- `logs/runtime-events.jsonl`：记录按钮、导航、录音、ASR、前后端请求、LLM、Agent、质量门禁、TTS、记忆写入状态、错误类型和耗时。
- `data/demo-db.json`：保存孩子与模型实际聊天正文、反馈、复述、知识记忆、家长确认和安全事件。

运行审计用于排障和性能分析，不作为孩子画像或回答事实依据；聊天正文只进入记忆库，不复制到运行审计日志。

## Trace 串联

同一轮交互使用相同的 `traceId`。语音提问时，可以依次看到：

1. `button.click`
2. `recording.start` / `recording.stop` / `recording.finish`
3. `asr.start` / `asr.finish`
4. 前端与后端的 `request.start`
5. `agent.*`、`llm.*`、`quality.*`
6. `memory.write` 或 `memory.skip`
7. 前端与后端的 `request.finish`
8. `tts.start` / `tts.finish`，失败时可能出现 `tts.fallback`

## 安全边界

运行日志不会保存：

- API Key、Authorization、密码或其他密钥
- Base URL、endpoint
- 系统提示词、消息 payload、Chain of Thought
- 孩子提问正文、模型回答正文、ASR 转写正文
- 原始录音或 Base64 音频

日志只保存必要的结构化元数据，例如模型 ID、字符数、字节数、状态、错误类型、HTTP 状态和耗时。字段在写入前会执行脱敏和长度限制。

## 本地与生产

当前演示版没有自动轮转、压缩和集中检索。正式环境应增加：

- 按天或按大小轮转
- 访问权限和审计查询权限
- 数据保留期限与自动删除
- 集中日志平台、告警和 trace 检索
- 对高频错误率、ASR 空结果、质量门禁拒绝率和延迟分位数的监控

`runtime-events.jsonl` 已加入 `.gitignore`，不应提交到版本库。
