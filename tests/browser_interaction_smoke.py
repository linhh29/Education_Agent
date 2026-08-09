#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = Path(os.environ.get("CHILD_AGENT_REPORT_DIR", "/tmp/child-agent-browser-smoke"))
CDP_HTTP = os.environ.get("CHILD_AGENT_CDP", "http://127.0.0.1:9223")
APP_URL = os.environ.get("CHILD_AGENT_URL", "http://127.0.0.1:8787/child")


class CDP:
    def __init__(self, websocket_url: str) -> None:
        parsed = urlparse(websocket_url)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        websocket_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        request = (
            f"GET {websocket_path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._recv_until(b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket upgrade failed: {response[:300]!r}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if expected.lower().encode("ascii") not in response.lower():
            raise RuntimeError("WebSocket accept key mismatch")
        self.next_id = 1

    def _recv_until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("Connection closed")
            data += chunk
        return data

    def _recv_exact(self, length: int) -> bytes:
        data = b""
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise RuntimeError("Connection closed")
            data += chunk
        return data

    def _send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_text(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            masked = second & 0x80
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x9:
                self.sock.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if opcode == 0x8:
                raise RuntimeError("WebSocket closed")
            if opcode == 0x1:
                return payload.decode("utf-8")

    def call(self, method: str, params: dict | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self._send_text(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._recv_text())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})

    def evaluate(self, expression: str, await_promise: bool = False):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        remote = result.get("result", {})
        if remote.get("subtype") == "error":
            raise RuntimeError(remote.get("description", "JavaScript evaluation failed"))
        return remote.get("value")


INJECT_SCRIPT = r"""
(() => {
  localStorage.removeItem('curiosity-active-child-v1');
  window.__chatCalls = [];
  window.__profileCalls = [];
  window.__memoryCalls = [];
  window.__deleteCalls = [];
  window.__browserErrors = [];
  window.__speechCalls = [];
  window.__recognitionStarts = 0;
  window.__ttsCalls = [];
  window.__audioPlays = [];
  window.__speechMode = 'delayed-final';
  window.__failNextChat = false;
  window.__qualityFallbackNext = false;
  window.__failNextMemory = false;
  window.__dataDeleted = false;
  window.__fakeKnowledgeCard = {
    id: 'browser-smoke-card',
    childId: 'child_demo',
    concept: '月亮看起来跟着人走',
    status: 'candidate',
    confidence: 0.45,
    evidence: '浏览器回归测试记录',
    effectiveAnalogy: '远处的月亮位置变化不明显',
    parentVerified: false,
    parentNote: '',
    history: [],
    updatedAt: '2026-07-25T00:00:00Z'
  };
  window.addEventListener('error', event => window.__browserErrors.push(String(event.error?.stack || event.message || 'window error')));
  window.addEventListener('unhandledrejection', event => window.__browserErrors.push(String(event.reason?.stack || event.reason || 'unhandled rejection')));
  class FakeSpeechRecognition {
    start() {
      window.__recognitionStarts += 1;
      if (window.__speechMode === 'denied') {
        setTimeout(() => this.onerror?.({error: 'not-allowed'}), 30);
      } else if (window.__speechMode === 'caption-denied') {
        setTimeout(() => this.onerror?.({error: 'not-allowed'}), 30);
      }
    }
    stop() {
      const mode = window.__speechMode;
      if (mode === 'delayed-final') {
        setTimeout(() => {
          const result = [{transcript: '为什么月亮好像跟着我走？'}];
          result.isFinal = true;
          this.onresult?.({resultIndex: 0, results: [result]});
        }, 100);
      }
      setTimeout(() => this.onend?.(), 180);
    }
  }
  class FakeMediaRecorder {
    static isTypeSupported(type) { return type.startsWith('audio/webm'); }
    constructor(stream, options = {}) {
      this.stream = stream;
      this.mimeType = options.mimeType || 'audio/webm';
      this.state = 'inactive';
    }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      const bytes = window.__speechMode === 'empty' ? [] : new Array(4096).fill(7);
      if (bytes.length) this.ondataavailable?.({data: new Blob([new Uint8Array(bytes)], {type: this.mimeType})});
      setTimeout(() => this.onstop?.(), 20);
    }
  }
  class FakeAudioContext {
    constructor() {
      this.sampleRate = 48000;
      this.state = 'running';
      this.destination = {};
    }
    resume() { return Promise.resolve(); }
    close() { return Promise.resolve(); }
    createScriptProcessor() {
      return {onaudioprocess: null, connect(){}, disconnect(){}};
    }
    createGain() {
      return {gain: {value: 1}, connect(){}, disconnect(){}};
    }
    createAnalyser() {
      return {
        fftSize: 1024,
        smoothingTimeConstant: 0,
        getByteTimeDomainData(buffer) {
          const quiet = window.__speechMode === 'quiet';
          for (let index = 0; index < buffer.length; index++) buffer[index] = quiet ? 128 : 128 + Math.round(Math.sin(index / 12) * 24);
        },
        disconnect(){}
      };
    }
    createMediaStreamSource() {
      return {
        connect(processor) {
          setTimeout(() => {
            if (window.__speechMode === 'empty') return;
            const samples = new Float32Array(4800);
            const amplitude = window.__speechMode === 'quiet' ? 0.0002 : window.__speechMode === 'spiky-quiet' ? 0.002 : 0.02;
            for (let index = 0; index < samples.length; index++) samples[index] = Math.sin(index / 12) * amplitude;
            if (window.__speechMode === 'spiky-quiet') samples[100] = 0.25;
            processor.onaudioprocess?.({inputBuffer: {getChannelData(){ return samples; }}});
          }, 20);
        },
        disconnect(){}
      };
    }
  }
  const fakeTrack = {label: '测试麦克风', stop(){}, getSettings(){ return {sampleRate: 48000, channelCount: 1}; }};
  Object.defineProperty(navigator, 'mediaDevices', {value: {
    async getUserMedia() {
      if (window.__speechMode === 'denied') throw new DOMException('Permission denied', 'NotAllowedError');
      return {getTracks(){ return [fakeTrack]; }, getAudioTracks(){ return [fakeTrack]; }};
    }
  }, configurable: true});
  Object.defineProperty(window, 'MediaRecorder', {value: FakeMediaRecorder, configurable: true});
  Object.defineProperty(window, 'AudioContext', {value: FakeAudioContext, configurable: true});
  Object.defineProperty(window, 'SpeechRecognition', {value: FakeSpeechRecognition, configurable: true});
  Object.defineProperty(window, 'webkitSpeechRecognition', {value: FakeSpeechRecognition, configurable: true});
  Object.defineProperty(window, 'speechSynthesis', {value: {
    cancel(){},
    getVoices(){ return [{name: 'Microsoft Xiaoxiao Natural', lang: 'zh-CN'}]; },
    speak(utterance){setTimeout(() => utterance.onend?.(), 30);}
  }, configurable: true});
  window.SpeechSynthesisUtterance = class { constructor(text){ this.text = text; } };
  window.Audio = class {
    constructor(url) { this.url = url; }
    play() { window.__audioPlays.push(this.url); setTimeout(() => this.onended?.(), 30); return Promise.resolve(); }
    pause() {}
    removeAttribute() {}
  };
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const request = args[0];
    const path = typeof request === 'string' ? request : request.url;
    if (path === '/api/chat') {
      const body = JSON.parse(args[1]?.body || '{}');
      window.__chatCalls.push(body);
      await new Promise(resolve => setTimeout(resolve, 90));
      if (window.__failNextChat) {
        window.__failNextChat = false;
        return new Response(JSON.stringify({message: '模拟模型暂时不可用'}), {status: 503, headers: {'Content-Type': 'application/json'}});
      }
      const qualityFallback = window.__qualityFallbackNext;
      window.__qualityFallbackNext = false;
      const strategies = {
        normal: {id: 'explain.direct.v1', label: '直接解释'},
        confused: {id: 'explain.reframe.v1', label: '换角度重讲'},
        simpler: {id: 'explain.simplify.v1', label: '减少概念'},
        example: {id: 'explain.example.v1', label: '具体例子'},
        why: {id: 'explain.deeper-cause.v1', label: '深入一层'},
        teachback: {id: 'learn.teachback.v1', label: '复述验证'}
      };
      const strategy = {...(strategies[body.feedbackMode] || strategies.normal), version: '1', scope: 'public_explanation_strategy'};
      return new Response(JSON.stringify({
        conversationId: 'browser_smoke_conversation',
        answerSource: qualityFallback ? 'quality_fallback' : 'real',
        deliveryDecision: {deliveryValidated: !qualityFallback},
        agentOrchestration: {
          architecture: 'supervisor_routed_multi_agent',
          containsChainOfThought: false,
          mode: {id: body.activityMode || 'ask', label: '浏览器回归模式'},
          agents: [
            {id: 'safety_guardian', label: '安全守护员', status: 'completed', outcome: '安全检查完成', durationMs: 2},
            {id: 'learning_planner', label: '学习规划师', status: 'completed', outcome: '解释计划完成', durationMs: 18},
            {id: 'child_tutor', label: '儿童讲解员', status: 'completed', outcome: '真实回答已生成', durationMs: 90},
            {id: 'quality_reviewer', label: '回答检查员', status: 'completed', outcome: '质量门禁通过', durationMs: 25},
            {id: 'memory_steward', label: '记忆整理员', status: 'completed', outcome: '理解证据已记录', durationMs: 5}
          ]
        },
        strategy,
        turnTrace: {
          schemaVersion: '1.0',
          strategy,
          totalDurationMs: 140,
          qualityPassed: true,
          containsChainOfThought: false,
          stages: [
            {stage: 'safety', status: 'completed', durationMs: 2, source: 'local_guardrail', summary: '已通过儿童安全边界检查。', failureCode: ''},
            {stage: 'understanding', status: 'completed', durationMs: 18, source: 'question_planner', summary: '已读取最新档案并理解问题。', failureCode: ''},
            {stage: 'composing', status: 'completed', durationMs: 90, source: 'answer_model', summary: '已按当前策略组织回答。', failureCode: ''},
            {stage: 'checking', status: 'completed', durationMs: 25, source: 'child_quality_gate', summary: '事实、表达和安全门禁已通过。', failureCode: ''},
            {stage: 'recording', status: 'completed', durationMs: 5, source: 'private_memory_store', summary: '只记录理解证据，不保存原始录音。', failureCode: ''}
          ]
        },
        assistant: {
          displayText: qualityFallback ? '刚才没有成功生成新的回答，请重新生成。' : `测试回答：${body.userText}`,
          speakText: qualityFallback ? '刚才没有成功生成新的回答，请重新生成。' : `测试回答：${body.userText}`,
          followUp: qualityFallback ? '可以点重新生成，再请模型回答。' : '你还想继续问什么？',
          safeExperiment: qualityFallback ? '' : '和家长一起走几步，看看远处的物体。',
          companionContract: ['危险实验找家长。'],
          trace: [{label: '浏览器回归', detail: '已收到真实点击事件'}]
        },
        safety: {needsParent: String(body.userText || '').includes('点火')}
      }), {status: 200, headers: {'Content-Type': 'application/json'}});
    }
    if (path === '/api/speech/transcribe') {
      const body = JSON.parse(args[1]?.body || '{}');
      window.__speechCalls.push(body);
      await new Promise(resolve => setTimeout(resolve, 50));
      if (window.__speechMode === 'empty') {
        return new Response(JSON.stringify({message: '没有识别到内容'}), {status: 400, headers: {'Content-Type': 'application/json'}});
      }
      return new Response(JSON.stringify({transcript: '为什么月亮好像跟着我走？'}), {status: 200, headers: {'Content-Type': 'application/json'}});
    }
    if (path === '/api/speech/synthesize') {
      const body = JSON.parse(args[1]?.body || '{}');
      window.__ttsCalls.push(body);
      return new Response(JSON.stringify({audioUrl: 'https://example.aliyuncs.com/child-voice.wav', voice: 'Cherry'}), {status: 200, headers: {'Content-Type': 'application/json'}});
    }
    if (path.startsWith('/api/parent/cards?')) {
      const response = await nativeFetch(...args);
      const data = await response.json();
      if (window.__dataDeleted) {
        data.knowledgeCards = [];
        data.confusions = [];
        data.recentChat = [];
        data.memoryTimeline = [];
        data.preferenceMemories = [];
        data.memoryStats = {chat: 0, cognitive: 0, preference: 0, safety: 0};
        return new Response(JSON.stringify(data), {status: response.status, headers: {'Content-Type': 'application/json'}});
      }
      const realCards = (data.knowledgeCards || []).filter(card => card.id !== window.__fakeKnowledgeCard.id);
      data.knowledgeCards = window.__fakeKnowledgeCard.status === 'deleted' ? realCards : [window.__fakeKnowledgeCard, ...realCards];
      return new Response(JSON.stringify(data), {status: response.status, headers: {'Content-Type': 'application/json'}});
    }
    if (path === '/api/profile' && args[1]?.method === 'POST') {
      const body = JSON.parse(args[1]?.body || '{}');
      window.__profileCalls.push(body);
      return new Response(JSON.stringify({...body, id: body.id || 'child_demo'}), {status: 200, headers: {'Content-Type': 'application/json'}});
    }
    if (path.startsWith('/api/cards/') && args[1]?.method === 'PATCH') {
      const body = JSON.parse(args[1]?.body || '{}');
      window.__memoryCalls.push({path, body});
      if (window.__failNextMemory) {
        window.__failNextMemory = false;
        return new Response(JSON.stringify({message: '模拟记忆更新失败'}), {status: 503, headers: {'Content-Type': 'application/json'}});
      }
      if (path.endsWith('/' + window.__fakeKnowledgeCard.id)) {
        const card = window.__fakeKnowledgeCard;
        if (body.action === 'rollback') {
          const previous = card.history.pop();
          if (previous) Object.assign(card, previous);
        } else if (body.action === 'delete') {
          card.status = 'deleted';
        } else {
          card.history.push({status: card.status, confidence: card.confidence, parentVerified: card.parentVerified, parentNote: card.parentNote});
          card.status = body.status;
        }
        card.updatedAt = new Date().toISOString();
        return new Response(JSON.stringify(card), {status: 200, headers: {'Content-Type': 'application/json'}});
      }
    }
    if (path === '/api/data/delete' && args[1]?.method === 'POST') {
      window.__deleteCalls.push(JSON.parse(args[1]?.body || '{}'));
      window.__dataDeleted = true;
      return new Response(JSON.stringify({ok: true}), {status: 200, headers: {'Content-Type': 'application/json'}});
    }
    return nativeFetch(...args);
  };
})();
"""


def target_websocket() -> str:
    with urlopen(f"{CDP_HTTP}/json/list", timeout=10) as response:
        targets = json.load(response)
    page = next((target for target in targets if target.get("type") == "page"), None)
    if not page:
        raise RuntimeError("No Chrome page target found")
    return page["webSocketDebuggerUrl"]


def wait_for(cdp: CDP, expression: str, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp.evaluate(expression):
            return
        time.sleep(0.08)
    raise AssertionError(f"Timed out waiting for: {expression}")


def click(cdp: CDP, selector: str) -> None:
    clicked = cdp.evaluate(f"(() => {{ const element = document.querySelector({json.dumps(selector)}); if (!element) return false; element.click(); return true; }})()")
    if not clicked:
        raise AssertionError(f"Could not click {selector}")


def text(cdp: CDP, selector: str) -> str:
    return cdp.evaluate(f"document.querySelector({json.dumps(selector)})?.textContent?.trim() || ''")


def set_input(cdp: CDP, selector: str, value: str) -> None:
    expression = f"""
    (() => {{
      const input = document.querySelector({json.dumps(selector)});
      if (!input) return false;
      input.value = {json.dumps(value)};
      input.dispatchEvent(new Event('input', {{bubbles: true}}));
      return true;
    }})()
    """
    if not cdp.evaluate(expression):
        raise AssertionError(f"Could not set {selector}")


def run() -> list[dict]:
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            raise AssertionError(f"{name}: {detail}")

    cdp = CDP(target_websocket())
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": INJECT_SCRIPT})
    cdp.call("Page.navigate", {"url": APP_URL})
    wait_for(cdp, "document.readyState === 'complete' && Boolean(document.querySelector('#mic'))", 12)

    check("静态资源版本", "child-v56" in cdp.evaluate("document.documentElement.innerHTML"), "页面已加载最新资源版本")
    check("儿童端独立导航", cdp.evaluate("Boolean(document.querySelector('.child-topbar')) && !document.querySelector('.parent-nav')"), "儿童端只保留品牌与家长入口")
    check("儿童端聚焦交互", cdp.evaluate("Boolean(document.querySelector('.child-focus')) && !document.querySelector('.child-v2 .side')"), "儿童端只显示角色、回答、提问与反馈")
    check("儿童端不展示技术信息", cdp.evaluate("!document.querySelector('.mode-picker') && !document.querySelector('.trace-card') && !document.querySelector('.agent-team-card')"), "回答模式和运行证据已移出儿童端")

    click(cdp, "#mic")
    wait_for(cdp, "document.querySelector('#mic')?.classList.contains('recording')")
    check("连续聊天已启动", cdp.evaluate("state.continuousConversation === true && document.querySelector('#mic')?.textContent.includes('结束聊天')"), text(cdp, "#mic"))
    check("录音状态机", "正在听你说话" in text(cdp, ".live-tag"), text(cdp, ".live-tag"))
    time.sleep(0.08)
    cdp.evaluate("stopListening(true, 'vad')")
    check("转写状态机", "正在整理你说的话" in text(cdp, ".live-tag"), text(cdp, ".live-tag"))
    wait_for(cdp, "window.__chatCalls.length === 1", 5)
    wait_for(cdp, "document.querySelector('#answerText')?.textContent.includes('为什么月亮')", 5)
    check("云端录音转写", cdp.evaluate("window.__speechCalls.length") == 1, "浏览器录音已发送到云端转写接口")
    check("云端兼容 WAV 主链", cdp.evaluate("window.__speechCalls[0].mimeType === 'audio/wav' && window.__speechCalls[0].audioData.startsWith('data:audio/wav')"), "提交官方示例兼容的 16kHz 单声道 WAV")
    check("真实录音不抢麦克风", cdp.evaluate("window.__recognitionStarts") == 0, "MediaRecorder 录音时未并行启动 SpeechRecognition")
    check("录音停止后最终转写", cdp.evaluate("window.__chatCalls.length") == 1, "停止后只提交一次聊天请求")
    check("语音输入来源", cdp.evaluate("window.__chatCalls[0].inputMode") == "voice", "语音问题标记为 voice")
    check("录音回答可见", "测试回答" in text(cdp, "#answerText"), text(cdp, "#answerText"))
    check("轨迹不含思维链", cdp.evaluate("!JSON.stringify(window.__chatCalls).includes('chainOfThought') && !document.documentElement.innerHTML.includes('chainOfThought')"), "仅展示阶段结果，不展示模型内部思维链")
    wait_for(cdp, "window.__audioPlays.length >= 1", 5)
    check("自然语音播放", cdp.evaluate("window.__ttsCalls.length >= 1 && window.__audioPlays.at(-1).includes('child-voice.wav')"), "回答异步生成并播放自然语音")
    wait_for(cdp, "state.continuousConversation === true && Boolean(currentRec)", 5)
    check("回答后自动续听", "结束聊天" in text(cdp, "#mic"), text(cdp, "#mic"))
    click(cdp, "#mic")
    wait_for(cdp, "state.continuousConversation === false && !currentRec", 5)
    check("孩子主动结束整段聊天", "开始聊天" in text(cdp, "#mic"), text(cdp, "#mic"))

    before = cdp.evaluate("window.__chatCalls.length")
    cdp.evaluate("window.__speechMode = 'empty'")
    click(cdp, "#mic")
    time.sleep(0.12)
    cdp.evaluate("stopListening(true, 'vad')")
    wait_for(cdp, "document.querySelector('.interaction-notice.error')?.textContent.includes('没有生成录音数据')", 5)
    check("空录音不误提交", cdp.evaluate("window.__chatCalls.length") == before, text(cdp, ".interaction-notice"))

    cdp.evaluate("window.__speechMode = 'quiet'")
    click(cdp, "#mic")
    time.sleep(0.16)
    cdp.evaluate("stopListening(true, 'vad')")
    wait_for(cdp, "document.querySelector('.interaction-notice.error')?.textContent.includes('声音太小')", 5)
    check("低音量不浪费 ASR", cdp.evaluate("window.__chatCalls.length") == before, text(cdp, ".interaction-notice"))

    cdp.evaluate("window.__speechMode = 'spiky-quiet'")
    click(cdp, "#mic")
    time.sleep(0.16)
    cdp.evaluate("stopListening(true, 'vad')")
    wait_for(cdp, f"window.__chatCalls.length === {before + 1}", 5)
    check("瞬时尖峰不干扰增益", cdp.evaluate("window.__chatCalls.at(-1).inputMode") == "voice", "增强后的音频继续进入语音提问主链")
    wait_for(cdp, "state.continuousConversation === true && Boolean(currentRec)", 5)
    click(cdp, "#mic")
    wait_for(cdp, "state.continuousConversation === false && !currentRec", 5)
    before = cdp.evaluate("window.__chatCalls.length")

    cdp.evaluate("window.__speechMode = 'denied'")
    click(cdp, "#mic")
    wait_for(cdp, "document.querySelector('.interaction-notice.error')?.textContent.includes('麦克风权限')", 5)
    check("权限拒绝反馈", "直接用文字" in text(cdp, ".interaction-notice"), text(cdp, ".interaction-notice"))
    cdp.evaluate("window.__speechMode = 'cloud-only'; delete window.SpeechRecognition; delete window.webkitSpeechRecognition")
    before = cdp.evaluate("window.__chatCalls.length")
    click(cdp, "#mic")
    time.sleep(0.08)
    cdp.evaluate("stopListening(true, 'vad')")
    wait_for(cdp, f"window.__chatCalls.length === {before + 1}", 5)
    check("无浏览器字幕仍可录音", cdp.evaluate("window.__chatCalls.at(-1).inputMode") == "voice", "仅靠 MediaRecorder 录音和云端 ASR 也能提问")
    wait_for(cdp, "state.continuousConversation === true && Boolean(currentRec)", 5)
    click(cdp, "#mic")
    wait_for(cdp, "state.continuousConversation === false && !currentRec", 5)

    previous = cdp.evaluate("window.__chatCalls.length")
    set_input(cdp, "#text", "为什么天空是蓝色的？")
    click(cdp, "#send")
    wait_for(cdp, f"window.__chatCalls.length === {previous + 1}", 5)
    wait_for(cdp, "!document.querySelector('#send')?.disabled", 5)
    check("文字发送", cdp.evaluate("window.__chatCalls.at(-1).userText") == "为什么天空是蓝色的？", "文字内容进入聊天请求")
    check("文字输入来源", cdp.evaluate("window.__chatCalls.at(-1).inputMode") == "text", "文字问题标记为 text")

    for feedback in ("confused", "simpler", "example", "why"):
        previous = cdp.evaluate("window.__chatCalls.length")
        click(cdp, f'[data-fb="{feedback}"]')
        wait_for(cdp, f"window.__chatCalls.length === {previous + 1}", 5)
        wait_for(cdp, "!document.querySelector('#send')?.disabled", 5)
        check(f"反馈 {feedback}", cdp.evaluate("window.__chatCalls.at(-1).feedbackMode") == feedback, "反馈类型正确进入请求")
        check(f"反馈上下文 {feedback}", cdp.evaluate("Boolean(window.__chatCalls.at(-1).previousAnswer) && window.__chatCalls.at(-1).inputMode === 'feedback'"), "反馈请求携带上一轮答案并标记为 feedback")
    click(cdp, '[data-fb="teachback"]')
    wait_for(cdp, "Boolean(document.querySelector('#text')?.placeholder.includes('自己的话'))")
    set_input(cdp, "#text", "因为月亮很远，所以看起来没有怎么移动。")
    click(cdp, "#send")
    wait_for(cdp, "window.__chatCalls.at(-1).feedbackMode === 'teachback'", 5)
    wait_for(cdp, "!document.querySelector('#send')?.disabled", 5)
    check("孩子复述", cdp.evaluate("window.__chatCalls.at(-1).originalQuestion.includes('天空')"), "复述保留当前问题上下文")

    cdp.evaluate("window.__failNextChat = true")
    set_input(cdp, "#text", "请测试失败后的重试")
    click(cdp, "#send")
    wait_for(cdp, "Boolean(document.querySelector('#retryAnswer'))", 5)
    check("持久错误提示", "模拟模型暂时不可用" in text(cdp, ".interaction-notice"), text(cdp, ".interaction-notice"))
    previous = cdp.evaluate("window.__chatCalls.length")
    click(cdp, "#retryAnswer")
    wait_for(cdp, f"window.__chatCalls.length === {previous + 1}", 5)
    wait_for(cdp, "document.querySelector('#answerText')?.textContent.includes('请测试失败后的重试')", 5)
    wait_for(cdp, "!document.querySelector('#send')?.disabled", 5)
    check("失败后重试", "请测试失败后的重试" in text(cdp, "#answerText"), text(cdp, "#answerText"))

    cdp.evaluate("window.__qualityFallbackNext = true")
    set_input(cdp, "#text", "请测试门禁失败后的重试")
    click(cdp, "#send")
    wait_for(cdp, "Boolean(document.querySelector('#retryAnswer'))", 5)
    check("门禁失败提示", "还没有通过检查" in text(cdp, ".interaction-notice"), text(cdp, ".interaction-notice"))
    check("回答区重试入口", "再试一次" in text(cdp, "#retryAnswer"), text(cdp, ".answer-tools"))
    previous = cdp.evaluate("window.__chatCalls.length")
    click(cdp, "#retryAnswer")
    wait_for(cdp, f"window.__chatCalls.length === {previous + 1}", 5)
    wait_for(cdp, "document.querySelector('#answerText')?.textContent.includes('请测试门禁失败后的重试')", 5)
    check("门禁失败后重新生成", not cdp.evaluate("Boolean(document.querySelector('#retryAnswer'))"), text(cdp, "#answerText"))

    click(cdp, 'a[href="/parent"]')
    wait_for(cdp, "location.pathname === '/parent' && Boolean(document.querySelector('.parent-home-hero'))", 10)
    check("家长端独立导航", cdp.evaluate("Boolean(document.querySelector('.parent-nav')) && !document.querySelector('.child-topbar')"), "首页、孩子设置和成长记录集中在家长端")
    check("普通空间默认空白", cdp.evaluate("state.childId === 'child_live' && Boolean(document.querySelector('[data-load-showcase]'))"), "正常进入不自动混入预设用户数据")
    click(cdp, "[data-load-showcase]")
    wait_for(cdp, "state.childId === 'child_showcase_xiaoman' && state.isShowcase === true && Boolean(document.querySelector('.showcase-active-band'))", 15)
    wait_for(cdp, "document.querySelectorAll('.parent-feed-card').length >= 4", 10)
    check("首页载入示例 Case", cdp.evaluate("document.querySelector('.parent-feed-intro')?.textContent.includes('小满') && Boolean(document.querySelector('[data-exit-showcase]'))"), "点击后进入独立的小满示例空间")
    check("家长卡片推送流", cdp.evaluate("document.querySelectorAll('.parent-feed-card').length >= 1 && Boolean(document.querySelector('.parent-feed-side'))"), "首页按当前记忆生成卡片并保留分层入口")
    check("家长端隐藏技术面板", not cdp.evaluate("Boolean(document.querySelector('.agent-system-panel'))"), "多智能体运行证据仅在记忆库展示")
    click(cdp, ".feed-evidence summary")
    check("卡片依据按需展开", cdp.evaluate("Boolean(document.querySelector('.feed-evidence[open] .memory-basis'))"), "孩子原话、记忆类型和置信度按需展开")
    click(cdp, ".parent-feed-card.type-association .feed-evidence summary")
    click(cdp, ".parent-feed-card.type-association .evidence-primary")
    wait_for(cdp, "location.pathname === '/memory' && Boolean(document.querySelector('#memory-chat[open]'))", 15)
    wait_for(cdp, "document.querySelectorAll('.chat-row.source-evidence').length >= 2", 10)
    check("卡片深链定位原话", cdp.evaluate("document.querySelectorAll('.chat-row.source-evidence').length >= 2 && document.querySelector('.full-chat-browser')?.textContent.includes('已定位')"), "自动展开聊天证据并高亮两条来源原话及上下文")
    check("成长记录导航", cdp.evaluate("Boolean(document.querySelector('[data-tour=\"memory-hero\"]'))"), "成长记录主要区块已渲染")
    click(cdp, 'a[href="/memory?section=chat"]')
    wait_for(cdp, "document.querySelector('.full-chat-browser h2')?.textContent.includes('2,016')", 10)
    check("完整聊天可分页", cdp.evaluate("Boolean(document.querySelector('.chat-pagination')) && document.querySelectorAll('.chat-row').length === 30"), "家长可浏览全部 2,016 条消息，不再只看最近 12 条")
    set_input(cdp, "#chatSearchInput", "打火机")
    click(cdp, "#chatSearchForm button")
    wait_for(cdp, "document.querySelector('.chat-list')?.textContent.includes('打火机')", 10)
    check("完整聊天可搜索", True, "关键词可以定位历史安全对话")
    click(cdp, 'a[href="/memory?section=safety"]')
    wait_for(cdp, "Boolean(document.querySelector('#memory-chat[open] #safety-records'))", 10)
    check("安全提醒可查看", cdp.evaluate("document.querySelectorAll('.safety-event-list article').length === 3"), "展示触发原话和系统处理结果")
    click(cdp, 'a[href="/memory?section=understanding"]')
    wait_for(cdp, "Boolean(document.querySelector('#memory-understanding[open]'))", 10)
    check("理解卡片库", cdp.evaluate("document.querySelectorAll('.knowledge').length") >= 18, "家长可查看完整知识认知库")
    check("偏好与关联记忆", cdp.evaluate("document.querySelectorAll('.pref-list .turn').length === 6 && document.querySelectorAll('.association-list article').length === 2"), "表达偏好、情景和跨期关联均可查看")
    click(cdp, ".memory-disclosure:nth-child(3) summary")
    check("记忆库趋势区", cdp.evaluate("Boolean(document.querySelector('#trendRange')) && Boolean(document.querySelector('.adaptation-chart, .trend-empty'))"), "可切换 7/14/30 天的真实交互证据趋势")
    check("趋势计算说明", cdp.evaluate("document.querySelectorAll('.trend-method-grid article').length === 4"), "四个维度均展示简短计算口径")
    click(cdp, ".memory-disclosure:nth-child(4) summary")
    check("记忆库系统证据", cdp.evaluate("Boolean(document.querySelector('.memory-disclosure:nth-child(4)[open] .agent-system-panel'))"), "技术和多智能体证据集中在独立分层")
    card_id = cdp.evaluate("document.querySelector('.knowledge[data-id]')?.dataset.id")
    card_selector = f'.knowledge[data-id="{card_id}"]'

    cdp.evaluate("window.__failNextMemory = true")
    click(cdp, f'{card_selector} button[data-act="known"]')
    wait_for(cdp, "[...document.querySelectorAll('.toast')].some(item => item.textContent.includes('模拟记忆更新失败'))", 5)
    known_button_selector = card_selector + ' button[data-act="known"]'
    wait_for(cdp, f"!document.querySelector({json.dumps(known_button_selector)})?.disabled", 5)
    check("家长操作失败可见", True, "接口失败时显示原因并恢复按钮，可立即重试")

    click(cdp, f'{card_selector} button[data-act="known"]')
    wait_for(cdp, f"document.querySelector({json.dumps(card_selector + ' .badge')})?.textContent.includes('已经会讲')", 5)
    check("家长确认卡片", cdp.evaluate("window.__memoryCalls.at(-1).body.action") == "known", "状态已更新为已经会讲")

    click(cdp, f'{card_selector} button[data-act="needs_review"]')
    wait_for(cdp, f"document.querySelector({json.dumps(card_selector + ' .badge')})?.textContent.includes('请家长确认')", 5)
    check("家长标记未理解", cdp.evaluate("window.__memoryCalls.at(-1).body.action") == "needs_review", "状态已更新为请家长确认")

    click(cdp, f'{card_selector} button[data-act="learning"]')
    wait_for(cdp, f"document.querySelector({json.dumps(card_selector + ' .badge')})?.textContent.includes('还在理解')", 5)
    check("家长标记学习中", cdp.evaluate("window.__memoryCalls.at(-1).body.action") == "learning", "状态已更新为还在理解")

    click(cdp, f'{card_selector} button[data-act="rollback"]')
    wait_for(cdp, f"document.querySelector({json.dumps(card_selector + ' .badge')})?.textContent.includes('请家长确认')", 5)
    check("家长恢复卡片", cdp.evaluate("window.__memoryCalls.at(-1).body.action") == "rollback", "成功恢复到上一次记录")

    click(cdp, f'{card_selector} button[data-act="delete"]')
    wait_for(cdp, f"!document.querySelector({json.dumps(card_selector)})", 5)
    check("家长删除卡片", cdp.evaluate("window.__memoryCalls.at(-1).body.action") == "delete", "指定理解卡片已从页面移除")

    theme_before = cdp.evaluate("document.documentElement.classList.contains('dark')")
    click(cdp, "#themeBtn")
    theme_after = cdp.evaluate("document.documentElement.classList.contains('dark')")
    check("主题切换", theme_after != theme_before, "明暗主题状态已切换")
    click(cdp, "#guideBtn")
    wait_for(cdp, "Boolean(document.querySelector('.driver-popover'))", 5)
    check("页面引导", True, "新手引导弹层可见")
    cdp.evaluate("document.querySelector('.driver-popover-close-btn')?.click()")

    cdp.evaluate("window.confirm = () => true")
    click(cdp, "#deleteAll")
    wait_for(cdp, "window.__deleteCalls.length === 1", 5)
    wait_for(cdp, "document.querySelector('.memory-stats, .memory-layers')?.textContent.includes('0')", 5)
    check("彻底删除本地记录", cdp.evaluate("window.__deleteCalls[0].childId") == "child_showcase_xiaoman", "确认后只删除当前示例空间的数据")

    click(cdp, 'a[href="/setup"]')
    wait_for(cdp, "location.pathname === '/setup' && Boolean(document.querySelector('#setupForm'))", 10)
    wait_for(cdp, "typeof document.querySelector('#setupForm')?.onsubmit === 'function'", 5)
    cdp.evaluate("document.querySelector('.driver-popover-close-btn')?.click()")
    set_input(cdp, 'input[name="nickname"]', "豆豆")
    check("建档表单有效", cdp.evaluate("document.querySelector('#setupForm').checkValidity()"), "默认朗读速度符合浏览器数值步长约束")
    click(cdp, "#setupForm button.primary")
    wait_for(cdp, "window.__profileCalls.length === 1", 5)
    wait_for(cdp, "location.pathname === '/child' && Boolean(document.querySelector('#mic'))", 10)
    check("建档请求已发送", cdp.evaluate("window.__profileCalls[0].nickname === '豆豆' && window.__profileCalls[0].id === 'child_showcase_xiaoman'"), "设置只写入当前示例空间")
    check("建档保存", cdp.evaluate("location.pathname === '/child' && Boolean(document.querySelector('.stage-head h1'))"), text(cdp, ".stage-head h1"))
    click(cdp, "[data-exit-showcase]")
    wait_for(cdp, "location.pathname === '/parent' && state.childId === 'child_live' && state.isShowcase === false", 10)
    check("退出示例回到普通空间", cdp.evaluate("Boolean(document.querySelector('[data-load-showcase]')) && !document.querySelector('.showcase-active-band')"), "示例状态不会污染普通体验")
    check("浏览器无未处理异常", cdp.evaluate("window.__browserErrors.length") == 0, json.dumps(cdp.evaluate("window.__browserErrors"), ensure_ascii=False))

    screenshot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "browser-interaction-smoke.png").write_bytes(base64.b64decode(screenshot["data"]))
    return checks


def main() -> int:
    started = time.time()
    try:
        checks = run()
        payload = {"passed": True, "durationSeconds": round(time.time() - started, 2), "checks": checks}
        result = json.dumps(payload, ensure_ascii=False, indent=2)
        REPORT_DIR.mkdir(exist_ok=True)
        (REPORT_DIR / "browser-interaction-smoke.json").write_text(result + "\n", encoding="utf-8")
        print(result)
        return 0
    except Exception as exc:
        payload = {"passed": False, "durationSeconds": round(time.time() - started, 2), "error": str(exc)}
        result = json.dumps(payload, ensure_ascii=False, indent=2)
        REPORT_DIR.mkdir(exist_ok=True)
        (REPORT_DIR / "browser-interaction-smoke.json").write_text(result + "\n", encoding="utf-8")
        print(result)
        return 1


if __name__ == "__main__":
    sys.exit(main())
