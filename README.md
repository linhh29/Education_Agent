# 好奇心伙伴 · 本机测试版

这是需要在自己电脑上启动的网页应用，**不是在线网址**。面向4–7岁孩子及家长，由成人陪同试用；先使用合成资料。网页在本机运行，回答、语音识别和朗读会调用阿里云百炼，调用费用由配置Key的账号承担。

## 环境与安装

本轮验收系统：macOS 15.7.4、Python 3.9.6。应用仅依赖Python标准库，不需要pip包、Node、Docker或全局Web框架。需要Git、Python 3.9+和现代浏览器。Windows、Linux、真实手机与其他Python版本未在本轮实际运行验证；代码使用`fcntl`，当前不支持Windows原生Python直接启动。

取得已发布的测试分支（发布前维护者会先提供验收记录，未推送时GitHub还没有此分支）：

```bash
git clone --branch wei-dev --single-branch https://github.com/linhh29/Education_Agent.git
cd Education_Agent
python3 -m venv .venv
source .venv/bin/activate
```

维护者若给了固定commit，在这个克隆中执行`git checkout <收到的完整commit>`。用`git rev-parse HEAD`记录自己的版本。无需安装依赖包。

## 配置自己的Key

需要百炼北京地域的API Key，以及以下模型的调用权限与可用额度：

- 文字：`qwen3.8-max-0902`，非思考模式。
- 语音识别：`qwen3-asr-flash-2026-02-10`。
- 朗读：`qwen3-tts-flash-2025-11-27`，Serena音色。

文字使用`https://dashscope.aliyuncs.com/compatible-mode/v1`。不同地域的Key与接入域名不能混用；创建及授权方法见[百炼官方Key说明](https://help.aliyun.com/zh/model-studio/get-api-key/)和[地域说明](https://help.aliyun.com/zh/model-studio/regions)。只有文字权限时先用打字，语音是否可调用仍取决于账号授权。应用不会自动换模型或用预写知识答案替代失败。

Key只由后端从私有文件读取。已有私有Key文件时，直接把下面的环境变量设为它的绝对路径，跳过创建步骤。不要把Key发给维护者或填写到网页里。

没有私有文件时，可在终端创建（输入不回显，文件放在代码仓库之外）：

```bash
export EDUCATION_AGENT_KEY_FILE="$HOME/.config/curiosity-companion/dashscope_api_key"
python - <<'PYKEY'
import getpass, os
from pathlib import Path
p = Path(os.environ["EDUCATION_AGENT_KEY_FILE"]).expanduser()
if p.exists():
    raise SystemExit("文件已存在，未覆盖；可直接使用，或自行选择另一私有路径。")
p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
key = getpass.getpass("粘贴你自己的百炼Key（不回显）：").strip()
if not key:
    raise SystemExit("未输入Key，没有保存。")
with p.open("x", encoding="utf-8") as f:
    os.chmod(p, 0o600)
    f.write(key + "\n")
PYKEY
```

`EDUCATION_AGENT_KEY_FILE`是文件路径，不是Key内容。路径支持`~`；相对路径按项目目录解释。环境变量只对当前终端有效，每次新开终端需要重新设置。仓库中的`config/runtime.json`是无密钥默认配置；正常使用不需要编辑它。

## 启动、停止与再次打开

```bash
python app.py
```

在浏览器打开 **http://127.0.0.1:8788/setup**，创建第一个空白档案后开始聊。新安装不自动导入任何旧聊天、提醒、示例儿童或费用历史。不配置Key也可以创建档案、查看资料，发送时会明确提示缺少配置。

终端保持运行；`Ctrl+C`正常停止。再次使用时回到同一个项目目录：

```bash
source .venv/bin/activate
export EDUCATION_AGENT_KEY_FILE="$HOME/.config/curiosity-companion/dashscope_api_key"
python app.py
```

如果使用了自己的其他Key路径，替换上面路径。应用默认只绑定`127.0.0.1`，不开放公网。若端口被占用，使用原有页面，或选择独立端口：

```bash
PORT=8791 python app.py
```

此时打开`http://127.0.0.1:8791/setup`。同一数据库不能同时运行两个实例；不要用批量结束进程解决占用。

## 使用与本地数据

孩子可以自由提问、继续追问、说没听懂或换话题。家长从“聊天记录”查看原话，在“家长问答”询问或讨论活动，在“孩子档案”修改记录、添加讲解提醒。草稿需要明确保存才生效。结束聊天后可以开启新会话；刷新或正常重启不会清空资料。

- `data/local-db.json`：自己的档案、聊天、提醒、修订与请求状态，首次启动自动创建。
- `data/local-db-speech-cache/`：朗读缓存，按既有有效期自动清理；识别用原音频不持久保存。
- `logs/llm-usage.json`：自己的本地用量账本，首次为0。不要删除账本来规避限额。供应商账单为最终收费依据。
- `.venv/`、私有配置、数据及运行日志已忽略，不应提交Git。Key建议始终放在仓库外。

默认本地累计上限50元，自动调用保留5元，语音子限额5元；未知用量保留预留金额，不按0计。共享同一Key的其他应用消费不在本地自动统计内，应同时设置供应商侧预算。若需要记录额外已知消费或降低上限，可复制`config/runtime.json`为忽略的`config/runtime.local.json`，调整`budget`，不要修改模型、价格或清除既有占用来绕过控制。

高级路径配置（一般试用者无需设置）：`EDUCATION_AGENT_DB`选择独立数据文件；`EDUCATION_AGENT_LEDGER`选择账本；`EDUCATION_AGENT_CONFIG`选择完整运行配置。相对路径都以项目目录为准。数据与账本是独立概念：更换资料不应清除同一测试预算。维护者的真实验收使用单独合成数据库，但显式连接原授权账本，未重置累计消费。

仓库还保留旧架构文档、合成展示数据及演示代码，作为来源与历史参考；不是首次启动的依赖，也不代表真实家庭成果。正常使用以上述入口与本README为准。

## 已知限制与反馈

- 解释仍可能过度概括，适合成人陪同测试，不能保证每次科学因果或记忆判断都正确。既有“越细总是越快”问题（RC19）未解决，RC04继续暂缓；没有为发布重新判为通过。
- 仅推迟活动时保留方案；回顾的时间措辞仍可能有歧义。提醒影响讲法，但不保证每次使用指定例子。
- 本机没有家长登录认证；同机使用者能切换档案。云端会收到必要对话和音频，请先使用合成资料，避免私人信息。逻辑删除不等于安全擦除或删除供应商保存的数据。
- 合成音频ASR/TTS验证和浏览器播放事件不等于真实麦克风、扬声器听感、儿童口音或长期儿童使用验证。麦克风只在本人明确操作和授权时使用。已发出的供应商请求可能无法取消或免除费用。
- Key缺失时设置私有文件路径后重启；401/403/模型不可用时检查地域、模型权限与额度。网络失败可在原页面重试；应用不会自动反复收费重试。目录不可写时选择有写权限的项目/数据目录，不要用管理员权限绕过。

反馈时提供`git rev-parse HEAD`、系统/Python版本、操作步骤和脱敏截图；不要上传Key、私有配置、原始录音或含私人资料的数据库/日志。费用信息可只给错误类型和必要统计。

## 开发验收与来源

应用运行不依赖测试工具。开发者另装Node后可运行前端回归（本轮Node 19.2.0）；不需要npm安装。离线测试使用受控模型/语音依赖，不代表真实云服务质量：

```bash
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.cjs
```

请在干净克隆、未配置真实Key或共享账本的终端运行离线测试，避免把测试资料混入正在使用的实例。真实云服务验收另行受预算限制。

原项目来源：[linhh29/Education_Agent](https://github.com/linhh29/Education_Agent)，原作者和提交历史保留。`static/vendor/driverjs/`的原MIT许可与版权声明保留。仓库目前没有项目级许可证，本次未自行添加或更换；历史资料与许可的分发确认见维护者发布前记录。
