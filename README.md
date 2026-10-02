# 好奇心伙伴

一个给4–7岁孩子和家长一起用的AI问答Demo。孩子可以自由提问、接着追问，家长也能回看聊过什么，了解孩子最近关注的事。

## 能做什么

- 用文字或语音提问，继续聊感兴趣的话题。
- 说“没听懂”或“换个例子”，让回答换一种讲法。
- 记录少量有原话依据的讲解偏好和理解情况。
- 家长查看聊天原话，补充或修改讲解提醒。
- 根据最近聊天，讨论简单的亲子观察活动。

## 快速开始

需要Git、Python 3.9+和浏览器。应用只用Python标准库，无需安装额外依赖。

已验证环境：**macOS 15.7.4 / Python 3.9.6**。Windows当前不支持，Linux尚未验证。

```sh
git clone --branch wei-dev --single-branch https://github.com/linhh29/Education_Agent.git
cd Education_Agent
python3 -m venv .venv
source .venv/bin/activate
```

按下一节配置Key，然后运行：

```sh
python app.py
```

打开 [http://127.0.0.1:8000/setup](http://127.0.0.1:8000/setup)。

## 配置DashScope Key

使用自己的DashScope账号和Key，云调用费用由该账号承担。账号需要有以下模型的调用权限：

- 文字问答：`qwen3.8-max-0902`
- 语音识别：`qwen3-asr-flash-2026-02-10`
- 朗读：`qwen3-tts-flash-2025-11-27`

Key保存在仓库外，程序运行时从本机读取。下面的命令会创建私有Key文件，输入不回显，也不会覆盖已有文件：

```sh
python - <<'PYKEY'
from pathlib import Path
from getpass import getpass
import os
p = Path.home() / '.config' / 'curiosity-companion' / 'api-key'
p.parent.mkdir(parents=True, exist_ok=True)
key = getpass('DashScope API Key: ').strip()
if not key:
    raise SystemExit('未输入Key，未创建文件。')
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(key)
print('已保存私有Key文件。')
PYKEY
export EDUCATION_AGENT_KEY_FILE="$HOME/.config/curiosity-companion/api-key"
```

`EDUCATION_AGENT_KEY_FILE`填的是文件路径，不是Key内容。已有私有Key文件时，跳过创建步骤，直接把这个变量指向它即可。

## 使用

首次打开先创建一个档案。在“一起聊”里提问和追问，回答可以朗读。语音转写后可以先修改文字再发送，也可以选择自己的WAV音频文件测试语音识别。

“家长”中可以查看聊天原话、进行家长问答，或讨论亲子活动。讲解提醒可以添加、修改，明确保存后才生效。

## 本地数据

`data/local-db.json`保存档案、聊天和相关记录。按`Ctrl+C`停止程序，下次启动会继续使用原来的数据。

重新打开终端时，在项目目录激活`.venv`、设置Key文件路径，再运行`python app.py`。

应用默认只在本机运行，绑定`127.0.0.1`，不要直接暴露到公网。

需要调整配置时，可把`config/runtime.json`复制为`config/runtime.local.json`再修改。也可以用`EDUCATION_AGENT_CONFIG`指定其他配置文件，用`EDUCATION_AGENT_DB`指定数据文件。

## 当前限制

- AI生成的回答可能有错误，重要或涉及安全的信息建议由成人核对。
- 当前Demo供成人协助试用，尚未验证真实儿童长期独立使用。
- 真实麦克风、儿童口音和不同设备上的体验还没有系统测试。
- 家长问答偶尔可能生成失败，遇到时重新发送即可。
- 试用时建议不要录入儿童的私人或敏感信息。

## 开发与测试

Python测试：

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

前端测试需要Node.js 18+：

```sh
node --test tests/*.cjs
```

这些测试不会调用云API。

## 许可与使用

目前未提供项目级开源许可。第三方Driver.js的版权和许可见 `static/vendor/driverjs/LICENSE`。

本项目目前用于试用和评估。

Copyright © 2026. All rights reserved.
