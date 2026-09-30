#!/usr/bin/env python3
"""好奇心 Agent - zero-dependency local online demo server.

Run: python3 app.py
Open: http://127.0.0.1:8787/setup
"""
from __future__ import annotations

import hashlib
import hmac
import fcntl
import ipaddress
import json
import os
import re
import socket
import threading
import time
import uuid
import base64
import binascii
from difflib import SequenceMatcher
from contextvars import ContextVar
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, build_opener, HTTPHandler, HTTPSHandler
import urllib.error
from urllib.request import HTTPRedirectHandler
from companion import CompanionService, ProductError

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
_db_override = os.environ.get("EDUCATION_AGENT_DB", "").strip()
DB_PATH = (Path(_db_override).expanduser() if _db_override else DATA_DIR / "demo-db.json")
if not DB_PATH.is_absolute():
    DB_PATH = (ROOT / DB_PATH).resolve()
SHOWCASE_DB_PATH = DATA_DIR / "showcase-child-24days.json"
SHOWCASE_CHILD_ID = "child_showcase_xiaoman"
STATIC_DIR = ROOT / "static"
MODEL_CONFIG_PATH = ROOT / "config" / "models.json"
LOG_DIR = ROOT / "logs"
RUNTIME_LOG_PATH = LOG_DIR / "runtime-events.jsonl"
DEFAULT_CHILD_ID = "child_demo"
MAX_BODY = 128 * 1024
MAX_SPEECH_BODY = 6 * 1024 * 1024
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_MODEL_CONFIG_BYTES = 64 * 1024
MAX_USER_TEXT_CHARS = 800
ALLOWED_OPENAI_HOSTS = {"api.openai.com", "dashscope.aliyuncs.com", "open.bigmodel.cn", "api.deepseek.com"}
ALLOWED_AUDIO_MIME_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
}
MAX_CHILD_ANSWER_CHARS = 360
MAX_ANSWER_QUALITY_ATTEMPTS = 10
MIN_REFINEMENT_BUDGET_SECONDS = 1.2
REQUIRED_AI_FIELDS = ["displayText", "speakText", "followUp", "safeExperiment", "avatarState", "introducedConcepts", "knowledgeCardUpdates", "safetyAction", "needsParent"]
VALID_ACTIVITY_MODES = {"ask", "detail", "story", "observe"}
VALID_FEEDBACK_MODES = {"normal", "confused", "simpler", "example", "why", "teachback"}
ACTIVITY_MODE_LABELS = {"ask": "清楚解释", "detail": "详细解答", "story": "讲个故事", "observe": "一起观察"}
AGENT_COMPONENT_FALLBACKS = {
    "question_planner": "parent_analysis",
    "child_tutor": "child_answer",
    "quality_reviewer": "parent_analysis",
    "teachback_reviewer": "parent_analysis",
}
AGENT_REGISTRY = {
    "safety_guardian": {
        "label": "安全守护员",
        "kind": "deterministic",
        "modelComponent": "",
        "purpose": "检查危险、隐私和必须由家长介入的边界。",
        "criticalPath": True,
    },
    "learning_planner": {
        "label": "学习规划师",
        "kind": "llm",
        "modelComponent": "question_planner",
        "purpose": "确定事实核心、认识边界、因果顺序和适龄解释预算。",
        "criticalPath": True,
    },
    "child_tutor": {
        "label": "儿童讲解员",
        "kind": "llm",
        "modelComponent": "child_tutor",
        "purpose": "根据本轮模式和孩子最新档案生成自然、完整的真实模型回答。",
        "criticalPath": True,
    },
    "quality_reviewer": {
        "label": "回答检查员",
        "kind": "hybrid",
        "modelComponent": "quality_reviewer",
        "purpose": "执行事实、适龄、模式结构和安全门禁，复杂回答才增加独立模型审核。",
        "criticalPath": True,
    },
    "teachback_reviewer": {
        "label": "复述观察员",
        "kind": "hybrid",
        "modelComponent": "teachback_reviewer",
        "purpose": "只在孩子用自己的话复述时核对理解证据。",
        "criticalPath": False,
    },
    "memory_steward": {
        "label": "记忆整理员",
        "kind": "deterministic",
        "modelComponent": "",
        "purpose": "只把通过交付门禁的回答和可核对证据写入记忆库。",
        "criticalPath": False,
    },
    "parent_coach": {
        "label": "家长支持员",
        "kind": "aggregate",
        "modelComponent": "",
        "purpose": "聚合聊天、反馈和家长确认，形成可回溯的家长摘要。",
        "criticalPath": False,
    },
}
AGENT_TOOL_REGISTRY = {
    "speech_recognition": {"label": "语音识别", "purpose": "把孩子的录音转成文字。"},
    "speech_synthesis": {"label": "自然语音播报", "purpose": "把已通过门禁的回答朗读出来。"},
    "memory_embedding": {"label": "记忆向量检索", "purpose": "查找与当前问题有关的历史线索。"},
    "memory_rerank": {"label": "记忆重排", "purpose": "对候选记忆进行相关性排序。"},
    "vision_understanding": {"label": "图像理解", "purpose": "在启用时理解孩子提供的图片。"},
}
FEEDBACK_STRATEGIES = {
    "normal": {"id": "explain.direct.v1", "label": "直接解释", "summary": "先回答核心原因，再给一个自然追问。"},
    "confused": {"id": "explain.reframe.v1", "label": "换角度重讲", "summary": "避开原句结构，从另一个因果入口重新解释。"},
    "simpler": {"id": "explain.simplify.v1", "label": "减少概念", "summary": "减少术语和概念数量，但保留真正原因。"},
    "example": {"id": "explain.example.v1", "label": "具体例子", "summary": "使用可观察、可核对的例子，并说明例子与问题的关系。"},
    "why": {"id": "explain.deeper-cause.v1", "label": "深入一层", "summary": "沿已有原因再向下解释一层，禁止只重复结论。"},
    "teachback": {"id": "learn.teachback.v1", "label": "复述验证", "summary": "根据孩子自己的讲法收集理解证据，不用背标准答案。"},
}
EMPTY_TEXT_VALUES = {"", "none", "null", "nil", "n/a", "无", "没有", "无需", "不适用"}
ADULT_TERMS = {"相对运动", "视觉参照系", "瑞利散射", "电磁波", "大气分子", "认知", "机制", "概率", "本质上", "从而", "因此可知"}
SUBJECTIVE_UNKNOWN_MARKERS = {
    "不确定", "不能确定", "无法确定", "不知道", "不能知道", "无法知道", "没法知道",
    "很难知道", "说不准", "我们只能看到",
}
EVIDENCE_BASED_MARKERS = {"科学家认为", "证据显示", "证据表明", "主要因为", "最可能", "可能是", "目前认为"}
UNCERTAINTY_MARKERS = SUBJECTIVE_UNKNOWN_MARKERS | EVIDENCE_BASED_MARKERS | {"可能", "也许"}
ANALOGY_MARKERS = ("就像", "好比", "有点像", "仿佛", "如同")
SCIENTIFIC_METAPHOR_SUBJECTS = ("光", "光线", "水滴", "水汽", "云", "空气", "石头", "影子", "泡泡", "薄膜", "颜色")
SCIENTIFIC_METAPHOR_ACTIONS = (
    "玩耍", "玩游戏", "跳舞", "打架", "比赛", "生气", "害怕", "想要", "想回到", "喜欢", "故意", "调皮", "跑",
    "抱在一起", "拿不住", "抓不住",
)
PLANT_INTENT_SUBJECTS = ("树", "大树", "树木", "植物", "花", "叶子", "树叶")
PLANT_INTENT_ACTIONS = (
    "为了保护自己", "为了保护自已", "保护自己", "保护自已", "想保护", "决定", "故意",
    "想要", "想把", "让树叶掉", "让叶子掉", "把树叶扔", "把叶子扔",
)
TEACHBACK_ACK_MARKERS = ("说对", "说得对", "对了", "关键点", "这个原因对")
TEACHBACK_UNVERIFIED_MARKERS = ("还不能确定", "再一起看看", "再说一次")
SUBJECTIVE_STATE_MARKERS = ("想念", "孤单", "寂寞", "害怕", "难过", "开心", "爱我", "喜欢我", "觉得")
IMAGINATION_QUESTION_MARKERS = ("如果我是", "假装", "魔法", "故事里", "想象")
PLAN_MODES = ("normal", "simpler", "example")
INTERACTION_DEADLINE: ContextVar[Optional[float]] = ContextVar("interaction_deadline", default=None)
RUNTIME_TRACE_ID: ContextVar[str] = ContextVar("runtime_trace_id", default="")
RUNTIME_LOG_LOCK = threading.Lock()
RUNTIME_EVENT_TYPES = {
    "button.click", "navigation.click", "mode.change", "feedback.select",
    "recording.start", "recording.stop", "recording.finish", "recording.error",
    "request.start", "request.finish", "request.error",
    "llm.start", "llm.finish", "llm.error", "llm.skipped",
    "asr.start", "asr.finish", "asr.error",
    "tts.start", "tts.finish", "tts.error", "tts.fallback",
    "agent.start", "agent.finish", "agent.error", "agent.skipped",
    "quality.reject", "quality.accept", "memory.write", "memory.skip",
}
RUNTIME_STATUSES = {"started", "completed", "failed", "skipped"}
RUNTIME_SENSITIVE_KEYS = {
    "authorization", "apikey", "token", "secret", "password",
    "baseurl", "endpoint", "prompt", "system", "messages",
    "userdata", "usertext", "transcript", "text", "audio", "audiodata",
    "displaytext", "speaktext", "previousanswer", "originalquestion",
}

CURATED_FACT_SCAFFOLDS = [
    {
        "id": "rain_water_cycle",
        "version": 1,
        "reviewedAt": "2026-07-25",
        "matches": ("下雨",),
        "conceptLabel": "下雨的原因",
        "truthKernel": "地面的水变成水汽升到空中；水汽遇冷变成小水滴；小水滴聚大变重后落下成为雨。",
        "epistemicStatus": "fact",
        "causalChain": [["地面水变成水汽升空"], ["水汽遇冷变成小水滴"], ["小水滴聚大变重"], ["落下来成为雨"]],
        "childVocabulary": ["看不见的水汽", "变成小水滴", "聚大变重", "落下来成雨"],
    },
    {
        "id": "shadow_length_change",
        "version": 1,
        "reviewedAt": "2026-08-10",
        "matches": ("影子",),
        "intentAny": ("变长", "更长", "很长", "长短", "长度", "下午", "上午", "太阳低", "斜着"),
        "conceptLabel": "太阳高度与影子长度",
        "truthKernel": "下午太阳在天空中的位置比上午低；阳光会更斜地照向地面；同样高的物体挡光后，影子会在地面延伸得更远，所以看起来更长。",
        "epistemicStatus": "fact",
        "causalChain": [
            ["下午太阳位置低", "下午太阳比较低", "太阳的位置比上午低"],
            ["光线斜着照", "阳光斜着照", "阳光更斜地照向地面"],
            ["影子在地面延伸得更远", "影子铺得更远", "影子被拉长", "影子变长"],
        ],
        "childVocabulary": ["下午太阳比较低", "阳光斜着照", "影子铺得更远"],
        "modeCausalChains": {
            "example": [
                ["手电筒放低", "把灯放低"],
                ["光线斜着照", "阳光斜着照"],
                ["积木的影子变长", "影子铺得更远"],
            ],
        },
        "validatedExample": "在家长陪同下用手电筒照积木；把灯放低时，积木的影子会变长。",
        "validatedExampleMarkers": ["手电筒", "积木", "灯放低", "影子会变长"],
    },
    {
        "id": "shadow_follows_body",
        "version": 1,
        "reviewedAt": "2026-07-25",
        "matches": ("影子",),
        "intentAny": ("跟着", "跟", "走", "跑", "移动", "会动"),
        "conceptLabel": "光和影子",
        "truthKernel": "身体挡住一部分光，形成暗的影子；身体移动时，挡光的位置也移动，所以影子跟着移动。",
        "epistemicStatus": "fact",
        "causalChain": [["身体挡住光"], ["形成影子"], ["身体移动"], ["影子也移动"]],
        "childVocabulary": ["挡住光", "暗影子", "你走它也走"],
        "modeCausalChains": {
            "example": [["积木挡住光"], ["形成影子"], ["积木移动"], ["影子也移动"]],
        },
        "validatedExample": "用手电筒照积木，积木移动时影子也移动。",
        "validatedExampleMarkers": ["手电筒", "积木移动", "影子也移动"],
    },
    {
        "id": "dinosaur_extinction_impact",
        "version": 1,
        "reviewedAt": "2026-07-25",
        "matches": ("恐龙", "不见"),
        "conceptLabel": "恐龙灭绝",
        "truthKernel": "证据支持一颗小行星撞击地球，灰尘遮住阳光，植物和食物减少，许多非鸟类恐龙因此灭绝。",
        "epistemicStatus": "evidence_based",
        "causalChain": [["小行星撞地球"], ["灰尘挡住阳光"], ["植物和食物变少"], ["许多恐龙活不下去"]],
        "childVocabulary": ["科学家认为", "大石头撞地球", "天变暗", "植物和食物变少"],
    },
    {
        "id": "soap_bubble_colors",
        "version": 1,
        "reviewedAt": "2026-07-25",
        "matches": ("肥皂泡", "彩色"),
        "conceptLabel": "泡泡颜色",
        "truthKernel": "肥皂泡的膜很薄且厚薄不同；光从膜的前后表面反射并叠加，使不同地方的不同颜色变亮。",
        "epistemicStatus": "fact",
        "causalChain": [["泡泡皮很薄"], ["光在两面反射"], ["厚薄不同"], ["不同颜色变亮"]],
        "childVocabulary": ["泡泡皮很薄", "光在两面反射", "不同颜色会变亮"],
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def normalize_runtime_id(value: Any, prefix: str = "trace") -> str:
    candidate = str(value or "").strip()[:80]
    if candidate and re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
        return candidate
    return new_id(prefix)


def sanitize_runtime_value(value: Any, key: str = "", depth: int = 0) -> Any:
    # COSEC: 运行审计执行字段级脱敏和长度限制，禁止把凭据、聊天正文、提示词或原始音频写入日志。
    normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized_key in RUNTIME_SENSITIVE_KEYS or any(marker in normalized_key for marker in ("apikey", "authorization", "password", "secret", "audiodata")):
        return "[REDACTED]"
    if depth >= 3:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(item_key)[:64]: sanitize_runtime_value(item_value, str(item_key), depth + 1)
            for item_key, item_value in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_runtime_value(item, key, depth + 1) for item in list(value)[:24]]
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:120]


def runtime_error_metadata(exc: BaseException) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"errorType": type(exc).__name__}
    if isinstance(exc, urllib.error.HTTPError):
        metadata["httpStatus"] = int(exc.code)
    return metadata


def write_runtime_event(
    event_type: str,
    source: str,
    status: str,
    *,
    trace_id: str = "",
    child_id: str = "",
    conversation_id: str = "",
    message_id: str = "",
    activity_mode: str = "",
    feedback_mode: str = "",
    component: str = "",
    duration_ms: Optional[int] = None,
    error_type: str = "",
    error_code: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    if event_type not in RUNTIME_EVENT_TYPES or status not in RUNTIME_STATUSES:
        return
    event = {
        "schemaVersion": "1.0",
        "eventId": new_id("evt"),
        "traceId": normalize_runtime_id(trace_id or RUNTIME_TRACE_ID.get()),
        "timestamp": now_iso(),
        "source": str(source or "backend")[:24],
        "eventType": event_type,
        "childId": normalize_runtime_id(child_id, "child") if child_id else "",
        "conversationId": normalize_runtime_id(conversation_id, "conv") if conversation_id else "",
        "messageId": normalize_runtime_id(message_id, "msg") if message_id else "",
        "activityMode": normalize_activity_mode(activity_mode) if activity_mode else "",
        "feedbackMode": feedback_mode if feedback_mode in VALID_FEEDBACK_MODES else "",
        "component": str(component or "")[:64],
        "status": status,
        "durationMs": max(0, int(duration_ms or 0)),
        "errorType": str(error_type or "")[:80],
        "errorCode": str(error_code or "")[:80],
        "details": sanitize_runtime_value(details or {}),
    }
    try:
        LOG_DIR.mkdir(exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with RUNTIME_LOG_LOCK:
            with RUNTIME_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(line)
                log_file.flush()
    except OSError as exc:
        print(f"[runtime-audit] write failed: {type(exc).__name__}")


def safe_json_load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return seed_db()
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: Dict[str, Any]) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    tmp.replace(DB_PATH)


def load_model_config() -> Dict[str, Any]:
    """Load the local model registry without exposing credentials to the client."""
    if not MODEL_CONFIG_PATH.exists():
        return {}
    try:
        # COSEC: 配置路径固定在项目 config 目录，且限制文件大小，避免任意路径读取和异常大文件占用内存。
        if MODEL_CONFIG_PATH.stat().st_size > MAX_MODEL_CONFIG_BYTES:
            raise ValueError("model config is too large")
        with MODEL_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
        if not isinstance(config, dict):
            raise ValueError("model config root must be an object")
        return config
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[config] models.json unavailable: {exc}")
        return {}


def model_component_config(component: str) -> Dict[str, Any]:
    config = load_model_config()
    provider = config.get("provider") if isinstance(config.get("provider"), dict) else {}
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    fallback_component = AGENT_COMPONENT_FALLBACKS.get(component, "")
    fallback_model = models.get(fallback_component) if fallback_component and isinstance(models.get(fallback_component), dict) else {}
    explicit_model = models.get(component) if isinstance(models.get(component), dict) else {}
    inherited_component = explicit_model.get("inherit") if isinstance(explicit_model.get("inherit"), str) else ""
    inherited_model = models.get(inherited_component) if inherited_component and isinstance(models.get(inherited_component), dict) else {}
    model = {**fallback_model, **inherited_model, **explicit_model}
    return {"provider": provider, "model": model}


def pin_hash(pin: str) -> str:
    return hashlib.sha256((pin or "0000").encode("utf-8")).hexdigest()


def seed_db() -> Dict[str, Any]:
    t = now_iso()
    return {
        "profiles": {
            DEFAULT_CHILD_ID: {
                "id": DEFAULT_CHILD_ID,
                "nickname": "豆豆",
                "age": 5,
                "kind": "demo",
                "interests": ["恐龙", "太空", "积木"],
                "familiarItems": ["手电筒", "积木", "浴缸", "远处的大山"],
                "explanationPreference": "自然回答当前问题，按需要举例",
                "voicePreference": {"enabled": False, "autoSpeak": False, "rate": 0.92},
                "parentPinHash": pin_hash("1234"),
                "createdAt": t,
                "updatedAt": t,
            }
        },
        "conversations": {},
        "messages": {},
        "memoryItems": {},
        "parentFeedback": {},
        "safetyEvents": {},
        "showcases": {},
    }


def load_db() -> Dict[str, Any]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        db = seed_db()
        save_db(db)
        return db
    return safe_json_load(DB_PATH)


def profile_for(db: Dict[str, Any], child_id: str) -> Dict[str, Any]:
    existing = db.get("profiles", {}).get(child_id)
    if existing:
        return existing
    base = seed_db()["profiles"][DEFAULT_CHILD_ID]
    return {**base, "id": child_id}


def import_showcase_child(db: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotently replace the isolated showcase child with the bundled fixture."""
    if not SHOWCASE_DB_PATH.exists():
        raise FileNotFoundError("示例数据库文件不存在，请先运行样例生成器。")
    fixture = safe_json_load(SHOWCASE_DB_PATH)
    source_profile = fixture.get("profiles", {}).get(DEFAULT_CHILD_ID)
    if not isinstance(source_profile, dict):
        raise ValueError("示例数据库缺少孩子档案")
    for collection in ["profiles", "conversations", "messages", "memoryItems", "parentFeedback", "safetyEvents"]:
        db.setdefault(collection, {})
    db.setdefault("showcases", {})
    db["profiles"].pop(SHOWCASE_CHILD_ID, None)
    for collection in ["conversations", "messages", "memoryItems", "parentFeedback", "safetyEvents"]:
        db[collection] = {
            key: value for key, value in db[collection].items()
            if value.get("childId") != SHOWCASE_CHILD_ID
        }

    profile = json.loads(json.dumps(source_profile, ensure_ascii=False))
    profile["id"] = SHOWCASE_CHILD_ID
    profile["showcase"] = True
    db["profiles"][SHOWCASE_CHILD_ID] = profile
    copied_counts: Dict[str, int] = {}
    for collection in ["conversations", "messages", "memoryItems", "parentFeedback", "safetyEvents"]:
        copied = 0
        for key, value in fixture.get(collection, {}).items():
            if value.get("childId") != DEFAULT_CHILD_ID:
                continue
            cloned = json.loads(json.dumps(value, ensure_ascii=False))
            cloned["childId"] = SHOWCASE_CHILD_ID
            db[collection][key] = cloned
            copied += 1
        copied_counts[collection] = copied
    metadata = json.loads(json.dumps(fixture.get("showcase", {}), ensure_ascii=False))
    metadata.update({"childId": SHOWCASE_CHILD_ID, "importedAt": now_iso(), "source": "bundled_showcase_fixture"})
    db["showcases"][SHOWCASE_CHILD_ID] = metadata
    return {"childId": SHOWCASE_CHILD_ID, "profile": profile, "counts": copied_counts, "showcase": metadata}


def normalize_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip()[:40] for x in value if str(x).strip()][:24]
    if isinstance(value, str):
        return [x.strip()[:40] for x in re.split(r"[,，/、\n]+", value) if x.strip()][:24]
    return []


def normalize_review_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip()[:240] for item in value if str(item).strip()][:24]
    if isinstance(value, str):
        return [item.strip()[:240] for item in re.split(r"[\n]+", value) if item.strip()][:24]
    return []


def parse_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "开启"}
    if value is None:
        return default
    return bool(value)


def normalize_activity_mode(value: Any) -> str:
    mode = str(value or "ask").strip().lower()
    return mode if mode in VALID_ACTIVITY_MODES else "ask"


def activity_mode_strategy(mode: str) -> str:
    return {
        "ask": "用三到四个自然完整句直接回答，只保留一个核心因果链；不讲故事，也不布置观察。",
        "detail": "用五到七个自然完整句，按结论、起因、中间变化、结果和总结逐层解释；不写故事角色或观察任务。",
        "story": "真正讲一个有角色、有场景、有互动和连续事件的小故事；前两句先写纯情节，结尾再用一到两句单独说明完整真实原因。",
        "observe": "displayText只做简短解释和观察邀请；把家长陪同、准备、动作、现象、家长提问和安全边界完整写入safeExperiment。",
    }.get(mode, "用三到四个自然完整句，先回答结论，再把关键原因讲清楚。")


def child_level_alignment(profile: Dict[str, Any], feedback_count: int = 0, avg_confidence: float = 0.0, feedback: str = "normal", related_cards: Optional[List[Dict[str, Any]]] = None, activity_mode: Optional[str] = None) -> Dict[str, Any]:
    age = int(profile.get("age", 5) or 5)
    anchors = normalize_list(profile.get("familiarItems"))[:3] or normalize_list(profile.get("interests"))[:3] or ["积木", "手电筒", "小汽车"]
    simpler = feedback in {"confused", "simpler"} or feedback_count >= 2
    mode = normalize_activity_mode(activity_mode)
    enforce_mode_contract = activity_mode is not None and feedback == "normal"
    mode_budgets = {
        "ask": {"minAnswerChars": 42, "maxAnswerChars": 190 if age <= 5 else 250, "minSentences": 3, "maxSentences": 5, "maxSentenceChars": 42 if age <= 5 else 50, "causalSteps": 2},
        "detail": {"minAnswerChars": 90, "maxAnswerChars": 280 if age <= 5 else 340, "minSentences": 5, "maxSentences": 8, "maxSentenceChars": 50 if age <= 5 else 58, "causalSteps": 3},
        "story": {"minAnswerChars": 85, "maxAnswerChars": 280 if age <= 5 else 340, "minSentences": 5, "maxSentences": 8, "maxSentenceChars": 50 if age <= 5 else 58, "causalSteps": 2},
        "observe": {"minAnswerChars": 55, "maxAnswerChars": 230 if age <= 5 else 290, "minSentences": 3, "maxSentences": 6, "maxSentenceChars": 46 if age <= 5 else 54, "causalSteps": 2},
    }
    budget = mode_budgets[mode]
    max_sentence_chars = 28 if simpler else budget["maxSentenceChars"]
    max_answer_chars = 140 if simpler else budget["maxAnswerChars"]
    max_sentences = 4 if simpler else budget["maxSentences"]
    min_answer_chars = 0 if not enforce_mode_contract else budget["minAnswerChars"]
    min_sentences = 0 if not enforce_mode_contract else budget["minSentences"]
    concept_limit = 1 if simpler or age <= 5 else 2
    trusted_cards = [card for card in (related_cards or []) if memory_card_is_trusted(card)]
    known_concepts = [str(card.get("concept", ""))[:40] for card in trusted_cards[:2] if card.get("concept")]
    return {
        "title": "这次会按孩子现在的情况回答",
        "ageBand": "4-5" if age <= 5 else "6-7",
        "maxSentenceChars": max_sentence_chars,
        "minAnswerChars": min_answer_chars,
        "maxAnswerChars": max_answer_chars,
        "minSentences": min_sentences,
        "maxSentences": max_sentences,
        "conceptLimit": concept_limit,
        "causalSteps": 1 if simpler else budget["causalSteps"],
        "activityMode": mode,
        "activityLabel": ACTIVITY_MODE_LABELS[mode],
        "enforceModeContract": enforce_mode_contract,
        "modeInstruction": activity_mode_strategy(mode),
        "sentenceBudget": f"尽量使用不超过 {max_sentence_chars} 个字的自然完整句，不要为了短而机械断句",
        "conceptBudget": f"一次最多讲 {concept_limit} 个重点",
        "anchors": anchors,
        "knownConcepts": known_concepts,
        "explainRule": "先把问题直接讲清楚。只有孩子主动想听例子，而且例子经过确认时，才会使用比喻。",
        "avoidRule": "不会因为孩子喜欢某样东西就硬套比喻，也不会把事物说得像人或突然考孩子。",
    }


def companion_trace(activity_mode: str, safety: Dict[str, Any], related_cards: List[Dict[str, Any]], changed_cards: List[Dict[str, Any]], ai: Dict[str, Any], workflow: Optional[Dict[str, Any]] = None, memory_evidence: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, str]]:
    workflow = workflow or {}
    safety_text = "危险/隐私边界已拦截，转家长一起处理。" if safety.get("level") != "safe" else "通过儿童安全检查，不涉及危险实验或隐私索取。"
    memory_text = f"找到 {len(related_cards)} 条相关记录，本次新增或更新 {len(changed_cards)} 条。"
    answer_text = "这个问题需要家长一起看，本次不会提供具体做法。" if ai.get("needsParent") else "事实、表达和安全检查都已完成。"
    plan = workflow.get("questionPlan") if isinstance(workflow.get("questionPlan"), dict) else {}
    return [
        {"key": "safety", "label": "先看是否安全", "detail": safety_text},
        {"key": "plan", "label": "把事实弄清楚", "detail": normalize_optional_text(plan.get("truthKernel"), 100) or "危险问题不会继续生成具体做法。"},
        {"key": "mode", "label": "选择回答方式", "detail": f"这次使用「{ACTIVITY_MODE_LABELS.get(activity_mode, '问一问')}」：{activity_mode_strategy(activity_mode)}"},
        {"key": "memory", "label": "看看以前聊过什么", "detail": memory_text},
        {"key": "answer", "label": "回答前再检查一遍", "detail": answer_text},
    ]


def turn_strategy(activity_mode: str, feedback: str) -> Dict[str, str]:
    feedback_strategy = FEEDBACK_STRATEGIES.get(feedback, FEEDBACK_STRATEGIES["normal"])
    return {
        "id": feedback_strategy["id"],
        "version": "1",
        "label": feedback_strategy["label"],
        "summary": feedback_strategy["summary"],
        "activityMode": activity_mode,
        "activityLabel": ACTIVITY_MODE_LABELS.get(activity_mode, ACTIVITY_MODE_LABELS["ask"]),
        "validation": "local_and_model_quality_gate",
        "scope": "public_explanation_strategy",
    }


def structured_turn_trace(
    request_latency: Dict[str, float],
    workflow: Dict[str, Any],
    safety: Dict[str, Any],
    strategy: Dict[str, str],
    related_cards: List[Dict[str, Any]],
    changed_cards: List[Dict[str, Any]],
) -> Dict[str, Any]:
    workflow_latency = workflow.get("latencyBreakdownSeconds") if isinstance(workflow.get("latencyBreakdownSeconds"), dict) else {}
    quality = workflow.get("quality") if isinstance(workflow.get("quality"), dict) else {}

    def milliseconds(*values: Any) -> int:
        total = 0.0
        for value in values:
            try:
                total += max(0.0, float(value or 0))
            except (TypeError, ValueError):
                continue
        return round(total * 1000)

    blocked = safety.get("level") != "safe"
    delivered = bool((workflow.get("deliveryDecision") or {}).get("deliveryValidated")) or blocked
    stages = [
        {
            "stage": "safety",
            "status": "blocked" if blocked else "completed",
            "durationMs": milliseconds(request_latency.get("requestPreparation")),
            "source": "local_guardrail",
            "summary": "危险或隐私问题已转交家长。" if blocked else "已通过儿童安全边界检查。",
            "failureCode": safety.get("category") if blocked else "",
        },
        {
            "stage": "understanding",
            "status": "skipped" if blocked else "completed",
            "durationMs": milliseconds(workflow_latency.get("planning"), workflow_latency.get("teachbackReview")),
            "source": "question_plan",
            "summary": "已确认问题重点、事实边界和孩子当前表达预算。" if not blocked else "安全边界已拦截，无需继续规划。",
            "failureCode": "",
        },
        {
            "stage": "composing",
            "status": "skipped" if blocked else "completed",
            "durationMs": milliseconds(workflow_latency.get("answerPreparation"), workflow_latency.get("repair")),
            "source": strategy.get("id", "explain.direct.v1"),
            "summary": f"采用「{strategy.get('label', '直接解释')}」策略组织本轮回答。",
            "failureCode": "",
        },
        {
            "stage": "checking",
            "status": "completed" if delivered else "failed",
            "durationMs": milliseconds(workflow_latency.get("initialAnswerReview"), workflow_latency.get("fallbackReview")),
            "source": "child_quality_gate",
            "summary": "事实、语言和安全门禁已通过。" if delivered else "候选回答未通过交付门禁。",
            "failureCode": "" if delivered else "delivery_rejected",
        },
        {
            "stage": "recording",
            "status": "completed",
            "durationMs": milliseconds(request_latency.get("memoryAndTrace"), request_latency.get("persistence")),
            "source": "local_evidence_store",
            "summary": f"参考 {len(related_cards)} 条可信线索，更新 {len(changed_cards)} 条理解证据；未保存原始录音。",
            "failureCode": "",
        },
    ]
    return {
        "schemaVersion": "1.0",
        "strategy": strategy,
        "stages": stages,
        "totalDurationMs": milliseconds(request_latency.get("serverTotal")),
        "qualityPassed": bool(quality.get("passed")) or blocked,
        "containsChainOfThought": False,
    }


def public_agent_system() -> Dict[str, Any]:
    agents = [
        {
            "id": agent_id,
            "label": definition["label"],
            "kind": definition["kind"],
            "purpose": definition["purpose"],
            "criticalPath": bool(definition["criticalPath"]),
            "modelComponent": definition["modelComponent"],
        }
        for agent_id, definition in AGENT_REGISTRY.items()
    ]
    tools = [
        {"id": tool_id, "label": definition["label"], "purpose": definition["purpose"]}
        for tool_id, definition in AGENT_TOOL_REGISTRY.items()
    ]
    return {
        "schemaVersion": "1.0",
        "architecture": "supervisor_routed_multi_agent",
        "policy": "quality_first_latency_bounded",
        "agents": agents,
        "tools": tools,
        "containsChainOfThought": False,
    }


def agent_orchestration(
    workflow: Dict[str, Any],
    safety: Dict[str, Any],
    activity_mode: str,
    feedback: str,
    changed_cards: List[Dict[str, Any]],
) -> Dict[str, Any]:
    latency = workflow.get("latencyBreakdownSeconds") if isinstance(workflow.get("latencyBreakdownSeconds"), dict) else {}
    plan = workflow.get("questionPlan") if isinstance(workflow.get("questionPlan"), dict) else {}
    quality = workflow.get("quality") if isinstance(workflow.get("quality"), dict) else {}
    delivery = workflow.get("deliveryDecision") if isinstance(workflow.get("deliveryDecision"), dict) else {}
    teachback = workflow.get("teachbackAssessment") if isinstance(workflow.get("teachbackAssessment"), dict) else {}
    blocked = safety.get("level") != "safe"
    delivered = bool(delivery.get("deliveryValidated"))

    def duration_ms(*keys: str) -> int:
        total = 0.0
        for key in keys:
            try:
                total += max(0.0, float(latency.get(key, 0) or 0))
            except (TypeError, ValueError):
                continue
        return round(total * 1000)

    planner_ready = plan.get("planStatus") == "ready"
    answer_is_real = workflow.get("answerSource") == "real"
    teachback_active = feedback == "teachback"
    agents = [
        {
            "id": "safety_guardian",
            "label": AGENT_REGISTRY["safety_guardian"]["label"],
            "status": "blocked" if blocked else "completed",
            "source": "local_rules",
            "criticalPath": True,
            "durationMs": 0,
            "outcome": "已拦截危险或隐私边界，请家长一起处理。" if blocked else "儿童安全边界检查通过。",
        },
        {
            "id": "learning_planner",
            "label": AGENT_REGISTRY["learning_planner"]["label"],
            "status": "skipped" if blocked else "completed" if planner_ready else "failed",
            "source": "question_planner",
            "criticalPath": True,
            "durationMs": duration_ms("planning"),
            "outcome": "安全拦截后不再规划。" if blocked else "已建立事实、因果和适龄表达计划。" if planner_ready else "事实规划未通过门禁，本轮不生成伪答案。",
        },
        {
            "id": "child_tutor",
            "label": AGENT_REGISTRY["child_tutor"]["label"],
            "status": "skipped" if blocked else "completed" if answer_is_real and delivered else "failed",
            "source": "child_tutor",
            "criticalPath": True,
            "durationMs": duration_ms("answerPreparation", "repair"),
            "outcome": "安全拦截后不生成具体做法。" if blocked else "真实模型回答已生成并通过交付。" if answer_is_real and delivered else "真实模型回答未通过，未用 Mock 或模板替代。",
        },
        {
            "id": "quality_reviewer",
            "label": AGENT_REGISTRY["quality_reviewer"]["label"],
            "status": "skipped" if blocked else "completed" if delivered else "failed",
            "source": normalize_optional_text(quality.get("reviewSource"), 60) or "local_quality_gate",
            "criticalPath": True,
            "durationMs": duration_ms("initialAnswerReview", "fallbackReview"),
            "outcome": "安全回复由本地守护规则负责。" if blocked else "事实、适龄、模式和安全门禁通过。" if delivered else "回答未通过交付门禁。",
        },
        {
            "id": "teachback_reviewer",
            "label": AGENT_REGISTRY["teachback_reviewer"]["label"],
            "status": "skipped" if not teachback_active or blocked else "completed" if teachback.get("assessmentAvailable") else "failed",
            "source": normalize_optional_text(teachback.get("source"), 60) or "not_requested",
            "criticalPath": False,
            "durationMs": duration_ms("teachbackReview"),
            "outcome": "本轮不是孩子复述，无需启用。" if not teachback_active else "已核对孩子自己的讲法。" if teachback.get("assessmentAvailable") else "复述证据不足，未认定为掌握。",
        },
        {
            "id": "memory_steward",
            "label": AGENT_REGISTRY["memory_steward"]["label"],
            "status": "skipped" if blocked or not delivered else "completed",
            "source": "local_evidence_store",
            "criticalPath": False,
            "durationMs": 0,
            "outcome": "只有通过交付门禁的回答才写入认知记忆。" if not delivered else f"已按证据规则更新 {len(changed_cards)} 条记忆。",
        },
        {
            "id": "parent_coach",
            "label": AGENT_REGISTRY["parent_coach"]["label"],
            "status": "deferred",
            "source": "local_aggregation",
            "criticalPath": False,
            "durationMs": 0,
            "outcome": "家长摘要在回答后聚合，不占用孩子等待时间。",
        },
    ]
    return {
        "schemaVersion": "1.0",
        "architecture": "supervisor_routed_multi_agent",
        "policy": "quality_first_latency_bounded",
        "mode": {
            "id": activity_mode,
            "label": ACTIVITY_MODE_LABELS.get(activity_mode, ACTIVITY_MODE_LABELS["ask"]),
            "contract": activity_mode_strategy(activity_mode),
        },
        "feedback": feedback,
        "agents": agents,
        "containsChainOfThought": False,
    }


def clamp_float(value: Any, default: float, min_value: float, max_value: float) -> float:
    try:
        return max(min_value, min(max_value, float(value)))
    except (TypeError, ValueError):
        return default


DANGER_PATTERNS: List[Tuple[str, str]] = [
    ("self_harm", r"自杀|自残|不想活|伤害自己|割腕"),
    ("violence", r"杀人|打死|砍|枪|炸弹|爆炸|毒死|伤害别人"),
    ("sexual", r"色情|黄色|裸照|性|隐私部位"),
    ("drug", r"毒品|吸毒|大麻|冰毒|安眠药|吃很多药"),
    ("stranger", r"陌生人|网友见面|加微信|私聊|离家出走"),
    ("privacy", r"地址|电话|学校|身份证|银行卡|密码|验证码"),
    ("dangerous_experiment", r"点火|打火机|火柴|煤气|开水|插座|电线|刀|农药|漂白水"),
]
DANGEROUS_EXPERIMENT_TERMS = r"点火|打火机|火柴|煤气|开水|插座|电线|刀|农药|漂白水"


def safety_check(text: str) -> Dict[str, Any]:
    for category, pat in DANGER_PATTERNS:
        if re.search(pat, text or "", re.I):
            return {"level": "blocked", "needsParent": True, "action": "ask_parent", "category": category}
    return {"level": "safe", "needsParent": False, "action": "none", "category": "none"}


def generated_answer_safety_check(ai: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    display_text = normalize_optional_text(ai.get("displayText", ""))
    speak_text = normalize_optional_text(ai.get("speakText", ""))
    follow_up = normalize_optional_text(ai.get("followUp", ""))
    safe_experiment = normalize_safe_experiment(ai.get("safeExperiment", ""))
    combined = " ".join([display_text, speak_text, follow_up, safe_experiment])
    result = safety_check(combined)
    if result["level"] == "safe" or result.get("category") != "dangerous_experiment":
        return result
    review_plan = plan if isinstance(plan, dict) else {}
    validated_example = normalize_optional_text(review_plan.get("validatedExample"), 180)
    validated_markers = normalize_list(review_plan.get("validatedExampleMarkers"))
    vetted_observation = bool(
        review_plan.get("feedbackMode") == "example"
        and review_plan.get("validatedExampleType") == "observation"
        and validated_example
        and any(marker in display_text for marker in validated_markers)
    )
    dangerous_terms = set(re.findall(DANGEROUS_EXPERIMENT_TERMS, display_text))
    allowed_terms = set(re.findall(DANGEROUS_EXPERIMENT_TERMS, validated_example))
    dangerous_instruction = bool(re.search(
        rf"(?:你|我们|请|一起|可以|试着|试试|去|拿|打开|使用|倒入|让家长)(?:[^。！？!?]{{0,18}})(?:{DANGEROUS_EXPERIMENT_TERMS})"
        rf"|(?:{DANGEROUS_EXPERIMENT_TERMS})(?:[^。！？!?]{{0,12}})(?:试试|看看|摸摸|打开|倒入|拿来|一起做)",
        display_text,
    ))
    unsafe_extra_fields = bool(re.search(DANGEROUS_EXPERIMENT_TERMS, " ".join([follow_up, safe_experiment])))
    # COSEC: 只放行经事实计划审核的描述性观察，不放行任何要求儿童操作危险物的步骤。
    if vetted_observation and dangerous_terms and dangerous_terms <= allowed_terms and not dangerous_instruction and not unsafe_extra_fields:
        return {"level": "safe", "needsParent": False, "action": "none", "category": "vetted_observation"}
    return result


def child_companion_contract(profile: Dict[str, Any]) -> Dict[str, Any]:
    name = profile.get("nickname", "孩子")
    return {
        "child": [
            f"{name}可以问很多为什么，但危险实验一定找家长。",
            "我不会要地址、电话、学校、密码或验证码。",
            "一次只讲一个小重点，听不懂就按反馈按钮。",
        ],
        "parent": [
            "儿童端默认不保存原始音频，只保存转写文本用于本地记录。",
            "真实模型回答会再经过规则安全校验，不合格会转家长。",
            "家长留下的提醒、删除和历史版本恢复会优先处理。",
        ],
    }


def safe_ai_fallback() -> Dict[str, Any]:
    return {
        "displayText": "这个问题需要大人一起看。我先不讲具体做法，请把家长叫来，我们一起换成安全的观察。",
        "speakText": "这个问题需要家长一起看。请找家长来帮忙。",
        "followUp": "要不要问一个安全的问题，比如月亮为什么会亮？",
        "safeExperiment": "安全第一：需要家长陪同。",
        "avatarState": "speaking",
        "introducedConcepts": [],
        "knowledgeCardUpdates": [],
        "safetyAction": "ask_parent",
        "needsParent": True,
    }


def normalize_optional_text(value: Any, max_chars: int = MAX_CHILD_ANSWER_CHARS) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in EMPTY_TEXT_VALUES else text[:max_chars]


def normalize_safe_experiment(value: Any, max_chars: int = 480) -> str:
    if not isinstance(value, dict):
        return normalize_optional_text(value, max_chars)
    labels = {
        "adultCompanion": "家长陪同",
        "preparation": "准备",
        "action": "一起做",
        "phenomenon": "重点观察",
        "parentQuestion": "家长可以问",
        "safetyBoundary": "安全提醒",
    }
    parts = []
    for key in labels:
        text = normalize_optional_text(value.get(key), 120)
        if text:
            parts.append(f"{labels[key]}：{text}")
    if not parts:
        parts = [normalize_optional_text(item, 120) for item in value.values()]
        parts = [item for item in parts if item]
    return "；".join(parts)[:max_chars]


def normalize_ai_response(ai: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # COSEC: LLM 输出后置安全校验——即使真实模型返回 JSON，也要限制字段、长度并二次检查危险内容。
    if not isinstance(ai, dict) or not all(k in ai for k in REQUIRED_AI_FIELDS):
        return safe_ai_fallback()
    cleaned = dict(ai)
    for key in ["displayText", "speakText", "followUp", "avatarState", "safetyAction"]:
        cleaned[key] = normalize_optional_text(cleaned.get(key, ""))
    cleaned["safeExperiment"] = normalize_safe_experiment(cleaned.get("safeExperiment", ""))
    if not cleaned["speakText"]:
        cleaned["speakText"] = cleaned["displayText"]
    cleaned["avatarState"] = cleaned["avatarState"] or "speaking"
    cleaned["safetyAction"] = cleaned["safetyAction"] or "none"
    cleaned["introducedConcepts"] = normalize_list(cleaned.get("introducedConcepts"))[:4]
    cleaned["knowledgeCardUpdates"] = []
    cleaned["needsParent"] = bool(cleaned.get("needsParent"))
    cleaned["answerType"] = normalize_optional_text(cleaned.get("answerType"), 32) or "factual"
    cleaned["truthKernel"] = normalize_optional_text(cleaned.get("truthKernel"), 240)
    cleaned["epistemicStatus"] = normalize_optional_text(cleaned.get("epistemicStatus"), 24) or "fact"
    cleaned["analogy"] = normalize_optional_text(cleaned.get("analogy"), 160)
    if generated_answer_safety_check(cleaned, plan)["level"] != "safe":
        return safe_ai_fallback()
    return cleaned


def curated_fact_scaffold(question: str) -> Dict[str, Any]:
    text = question or ""
    for scaffold in CURATED_FACT_SCAFFOLDS:
        intent_any = scaffold.get("intentAny", ())
        intent_none = scaffold.get("intentNone", ())
        if (
            all(marker in text for marker in scaffold["matches"])
            and (not intent_any or any(marker in text for marker in intent_any))
            and not any(marker in text for marker in intent_none)
        ):
            return {
                "id": scaffold["id"],
                "version": scaffold.get("version", 1),
                "reviewedAt": scaffold.get("reviewedAt", ""),
                "source": "curated_science_anchor",
                "conceptLabel": scaffold.get("conceptLabel", ""),
                "truthKernel": scaffold["truthKernel"],
                "epistemicStatus": scaffold["epistemicStatus"],
                "causalChain": scaffold["causalChain"],
                "childVocabulary": scaffold["childVocabulary"],
                "modeCausalChains": scaffold.get("modeCausalChains", {}),
                "validatedExample": scaffold.get("validatedExample", ""),
                "validatedExampleMarkers": scaffold.get("validatedExampleMarkers", []),
            }
    return {}


def apply_curated_fact_scaffold(plan: Dict[str, Any]) -> Dict[str, Any]:
    scaffold = plan.get("factScaffold") if isinstance(plan.get("factScaffold"), dict) else {}
    chain = normalize_idea_groups(scaffold.get("causalChain"))
    if not scaffold or not chain:
        return plan
    aligned = dict(plan)
    aligned["truthKernel"] = normalize_optional_text(scaffold.get("truthKernel"), 240)
    aligned["epistemicStatus"] = normalize_epistemic_status(scaffold.get("epistemicStatus"))
    current_groups = normalize_idea_groups(aligned.get("requiredIdeaGroups"))
    current_mode_groups = normalize_mode_groups(aligned.get("requiredIdeaGroupsByMode"))
    groups_to_check = current_groups + [
        group
        for mode_groups in current_mode_groups.values()
        for group in mode_groups
    ]
    groups_need_scaffold = not current_groups or any(
        len(normalize_fact_match_text(term)) > 16
        or any(marker in term for marker in ANALOGY_MARKERS)
        for group in groups_to_check
        for term in group
    )
    if groups_need_scaffold:
        aligned["requiredIdeaGroups"] = chain
        example_chain = normalize_idea_groups((scaffold.get("modeCausalChains") or {}).get("example")) or chain[-3:]
        aligned["requiredIdeaGroupsByMode"] = {
            "normal": chain,
            "simpler": chain[-2:],
            "example": example_chain[-3:],
        }
        aligned["causalChainByMode"] = dict(aligned["requiredIdeaGroupsByMode"])
    concept_label = normalize_optional_text(scaffold.get("conceptLabel"), 80)
    if concept_label:
        aligned["conceptLabels"] = [concept_label]
    aligned["factBoundarySource"] = "curated_science_anchor"
    return aligned


def heuristic_question_plan(question: str, activity_mode: str = "ask") -> Dict[str, Any]:
    text = question or ""
    plan: Dict[str, Any] = {
        "questionType": "causal" if any(word in text for word in ["为什么", "怎么会", "为什么会"]) else "factual",
        "childIntent": "想知道眼前现象背后的一个简单原因",
        "truthKernel": "先准确回答孩子的问题；不确定时明确说不知道或可能。",
        "epistemicStatus": "fact",
        "requiredIdeaGroups": [],
        "requiredIdeaGroupsByMode": {},
        "causalChainByMode": {},
        "avoidClaims": ["把比喻当成事实", "假装知道无法确定的感受"],
        "analogyPolicy": "omit",
        "validatedAnalogy": "",
        "validatedAnalogyMarkers": [],
        "validatedExample": "",
        "validatedExampleMarkers": [],
        "validatedExampleType": "observation",
        "childSafeAnswerNormal": "",
        "childSafeAnswerSimpler": "",
        "childSafeAnswerExample": "",
        "childSafeAnswerWhy": "",
        "childSafeTeachbackCorrect": "",
        "childSafeTeachbackIncorrect": "",
        "teachbackCorrectionMarkers": [],
        "conceptLabels": [],
        "activityMode": activity_mode,
        "planStatus": "needs_planning",
        "planSource": "heuristic",
    }
    scaffold = curated_fact_scaffold(text)
    if scaffold:
        plan["factScaffold"] = scaffold
        plan["factScaffoldSource"] = scaffold["source"]
    if "月亮" in text and any(word in text for word in ["跟", "走", "追", "陪"]):
        plan.update({
            "childIntent": "想知道自己走动时月亮为什么看起来仍在同一方向",
            "truthKernel": "月亮离我们非常远；人走几步造成的观察方向变化很小，所以它看起来像在跟着走。",
            "requiredIdeaGroups": [["月亮", "很远", "太远"], ["方向变化很小", "位置变化很小", "看起来几乎没变", "看起来没变", "没怎么变", "差不多的位置", "看不出位置在变"]],
            "avoidClaims": ["月亮没动", "月亮真的跟着孩子", "只说陪着走而不解释距离"],
            "analogyPolicy": "allow_vetted",
            "validatedAnalogy": "坐车时，远处的大山也像跟着你。",
            "validatedAnalogyMarkers": ["远处的大山", "远山"],
            "validatedExample": "坐车时看看远处的大山，它也像跟着你。",
            "validatedExampleMarkers": ["远处的大山", "远山"],
            "validatedExampleType": "analogy",
            "teachbackCorrectionMarkers": ["不是月亮在追", "没有在追", "不是真的跟着"],
            "conceptLabels": ["远近和看起来的位置"],
            "planStatus": "ready",
        })
    elif "天空" in text and "蓝" in text:
        plan.update({
            "childIntent": "想知道白天看到的天空为什么呈蓝色",
            "truthKernel": "阳光里有很多颜色；进入空气后，蓝色的光更容易向四周散开，所以我们看到蓝天。",
            "requiredIdeaGroups": [["阳光", "太阳光", "光里"], ["蓝光", "蓝色光", "蓝色的光"], ["散开", "散到四周", "向四周跑"]],
            "avoidClaims": ["蓝色积木真的在天空乱跑", "天空本身涂了蓝色", "只给比喻不说蓝光"],
            "analogyPolicy": "omit",
            "validatedExample": "比如抬头看四周，到处都能看到蓝色，因为蓝光散到了各处。",
            "validatedExampleMarkers": ["抬头看四周", "散到了各处"],
            "validatedExampleType": "observation",
            "teachbackCorrectionMarkers": ["不是天空涂了蓝色", "天空不是涂蓝的"],
            "conceptLabels": ["蓝光在空气里散开"],
            "planStatus": "ready",
        })
    elif any(word in text for word in ["想我", "想它", "想念", "难过", "爱我"]) and any(word in text for word in ["鸟", "猫", "狗", "动物", "妈妈"]):
        plan.update({
            "questionType": "emotional",
            "childIntent": "在关心动物之间有没有像人的想念和牵挂",
            "truthKernel": "我们不能确定动物是否像人一样想念；可以确定的是，许多动物会照顾、寻找或呼唤幼崽。",
            "epistemicStatus": "subjective_unknown",
            "requiredIdeaGroups": [["不能确定", "不知道", "很难知道"], ["照顾", "寻找", "去找", "找它", "呼唤", "喂"]],
            "avoidClaims": ["动物一定像人一样想念", "把温柔想象说成科学事实"],
            "analogyPolicy": "omit",
            "validatedExample": "我们能看到鸟妈妈找它、喂它。",
            "validatedExampleMarkers": ["找它", "喂它"],
            "validatedExampleType": "observation",
            "teachbackCorrectionMarkers": ["不能说它一定会想念", "不是一定会想念"],
            "conceptLabels": ["动物感受和可观察行为"],
            "planStatus": "ready",
        })
    elif any(word in text for word in ["故事", "假装", "如果我是", "魔法"]):
        plan.update({
            "questionType": "imaginative",
            "childIntent": "希望一起进行安全想象",
            "truthKernel": "这是想象，不把故事说成现实事实。",
            "epistemicStatus": "imaginative",
            "validatedExample": "比如可以假装自己飞过云朵，再回到地面。",
            "validatedExampleMarkers": ["假装"],
            "validatedExampleType": "imaginative",
            "teachbackCorrectionMarkers": ["不是现实里真的发生", "不是真的发生"],
            "conceptLabels": ["想象和现实"],
            "planStatus": "ready",
        })
    return plan


def answer_uses_analogy(ai: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> bool:
    answer = normalize_optional_text(ai.get("displayText"), 360)
    analogy = normalize_optional_text(ai.get("analogy"), 160)
    if analogy:
        return True
    activity_mode = normalize_activity_mode((plan or {}).get("activityMode"))
    if plan and plan.get("validatedExampleType") == "observation" and plan.get("feedbackMode") == "example":
        validated_example = normalize_optional_text(plan.get("validatedExample"), 180)
        markers = normalize_list(plan.get("validatedExampleMarkers"))
        matched_markers = [marker for marker in markers if marker in answer]
        if validated_example and matched_markers:
            # A generated observation can paraphrase the reviewed example and still use “就像” to
            # introduce that concrete scene. Exclude only sentences carrying a reviewed marker;
            # analogy wording elsewhere remains detectable.
            answer = "。".join(
                sentence
                for sentence in split_sentences(answer)
                if not any(marker in sentence for marker in matched_markers)
            )
    comparison_free = answer.replace("像人一样", "").replace("像我们一样", "")
    if any(marker in comparison_free for marker in ANALOGY_MARKERS):
        return True
    if activity_mode == "story":
        return False
    return bool(re.search(r"像[^。！？!?；;\n]{1,28}一样", comparison_free))


def scientific_metaphor_hits(text: str) -> List[str]:
    hits = []
    for subject in SCIENTIFIC_METAPHOR_SUBJECTS:
        for action in SCIENTIFIC_METAPHOR_ACTIONS:
            if re.search(rf"{re.escape(subject)}[^。！？!?；;\n]{{0,10}}{re.escape(action)}", text or ""):
                hits.append(f"{subject}{action}")
    for subject in PLANT_INTENT_SUBJECTS:
        for action in PLANT_INTENT_ACTIONS:
            if re.search(rf"{re.escape(subject)}[^。！？!?；;\n]{{0,16}}{re.escape(action)}", text or ""):
                hits.append(f"{subject}{action}")
    return list(dict.fromkeys(hits))


def memory_card_is_trusted(card: Dict[str, Any]) -> bool:
    return (
        card.get("status") == "known"
        and (
            bool(card.get("parentVerified"))
            or int(card.get("reviewedTeachbackCorrectCount", 0) or 0) >= 2
        )
    )


def normalize_idea_groups(value: Any, max_groups: int = 4) -> List[List[str]]:
    groups = []
    if not isinstance(value, list):
        return groups
    for group in value:
        terms = normalize_list(group)[:6] if isinstance(group, (list, str)) else []
        if terms:
            groups.append(terms)
    return groups[:max_groups]


def normalize_mode_groups(value: Any, max_groups: int = 4) -> Dict[str, List[List[str]]]:
    raw = value if isinstance(value, dict) else {}
    return {
        mode: normalize_idea_groups(raw.get(mode), max_groups)
        for mode in PLAN_MODES
        if normalize_idea_groups(raw.get(mode), max_groups)
    }


def active_required_idea_groups(plan: Dict[str, Any], feedback_mode: str = "normal") -> List[List[str]]:
    by_mode = plan.get("requiredIdeaGroupsByMode") if isinstance(plan.get("requiredIdeaGroupsByMode"), dict) else {}
    mode = "simpler" if feedback_mode in {"confused", "simpler"} else "example" if feedback_mode == "example" else "normal"
    mode_groups = normalize_idea_groups(by_mode.get(mode))
    groups = mode_groups or normalize_idea_groups(plan.get("requiredIdeaGroups"))
    if feedback_mode in {"confused", "simpler"}:
        return groups[-2:]
    if feedback_mode == "example":
        return groups[-3:]
    return groups


def active_causal_chain(plan: Dict[str, Any], feedback_mode: str = "normal") -> List[List[str]]:
    if normalize_epistemic_status(plan.get("epistemicStatus")) in {"subjective_unknown", "imaginative"}:
        return []
    by_mode = normalize_mode_groups(plan.get("causalChainByMode"))
    mode = "simpler" if feedback_mode in {"confused", "simpler"} else "example" if feedback_mode == "example" else "normal"
    chain = by_mode.get(mode) or []
    if feedback_mode in {"confused", "simpler"}:
        return chain[-2:]
    if feedback_mode == "example":
        return chain[-3:]
    return chain


def quality_attempt_limit(feedback_mode: str) -> int:
    return 4 if feedback_mode in {"confused", "simpler", "example", "why"} else MAX_ANSWER_QUALITY_ATTEMPTS


def ordered_idea_groups_present(text: str, groups: List[List[str]]) -> Tuple[bool, List[int]]:
    cursor = 0
    positions: List[int] = []
    for group in groups:
        matches = [span for term in group if (span := fact_term_span(text, term, cursor)) is not None]
        if not matches:
            return False, positions
        start, end = min(matches, key=lambda span: (span[0], span[1]))
        positions.append(start)
        cursor = max(start + 1, end)
    return True, positions


def split_sentences(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"[。！？!?；;\n]+", text or "") if part.strip()]


def fit_child_reference_sentences(text: str, max_sentence_chars: int) -> str:
    sentences = split_sentences(text)
    # 保留自然中文里的逗号关系；句长预算不应把完整因果句切成碎片。
    return "。".join(sentences) + ("。" if sentences else "")


CHILD_REFERENCE_COMPACTIONS = (
    (r"你的身体", "身体"),
    (r"自己的身体", "身体"),
    (r"身体把太阳光挡住了", "身体挡住光"),
    (r"身体挡住了太阳光", "身体挡住光"),
    (r"身体把光挡住了", "身体挡住光"),
    (r"身体挡住了光", "身体挡住光"),
    (r"地面上就会出现一个黑黑的影子", "地上就有影子"),
    (r"地上就会出现一个黑黑的影子", "地上就有影子"),
    (r"地上就有个黑黑的影子", "地上就有影子"),
    (r"脚边有个黑黑的影子", "脚边有影子"),
    (r"你往前走一步", "你走一步"),
    (r"你往前走", "你走"),
    (r"那个黑影也会跟着你一起往前走", "影子也跟着走"),
    (r"那个黑影也跟着往前挪一步", "影子也跟着走"),
    (r"影子也会跟着你一起往前走", "影子也跟着走"),
    (r"影子也会跟着你往前走", "影子也跟着走"),
    (r"影子也会跟着你走", "影子也跟着走"),
    (r"你走动的时候", "你走时"),
    (r"身体移动的时候", "身体移动时"),
    (r"这块暗的地方", "影子"),
)


EXAMPLE_MARKER_CONTEXT_TERMS = (
    "阳光", "影子", "水滴", "水珠", "远山", "大山", "四周", "鸟", "找", "喂",
    "假装", "手电筒", "地上", "脚边", "天空", "月亮", "泡泡",
)


def select_minimal_example_markers(text: str, markers: Optional[List[str]], max_markers: int = 1) -> List[str]:
    candidates = [
        marker for marker in normalize_list(markers)
        if marker in text and len(marker) <= 18
    ]
    ranked = sorted(
        dict.fromkeys(candidates),
        key=lambda marker: (
            0 if any(term in marker for term in EXAMPLE_MARKER_CONTEXT_TERMS) else 1,
            len(marker),
            candidates.index(marker),
        ),
    )
    return ranked[:max(1, max_markers)]


def compact_child_reference_language(text: str, protected_markers: Optional[List[str]] = None) -> str:
    compacted = normalize_optional_text(text, 360)
    placeholders: Dict[str, str] = {}
    for index, marker in enumerate(normalize_list(protected_markers)):
        if marker not in compacted:
            continue
        placeholder = f"ZXQMARKER{index}QXZ"
        compacted = compacted.replace(marker, placeholder)
        placeholders[placeholder] = marker
    for pattern, replacement in CHILD_REFERENCE_COMPACTIONS:
        compacted = re.sub(pattern, replacement, compacted)
    for placeholder, marker in placeholders.items():
        compacted = compacted.replace(placeholder, marker)
    return compacted


def compile_child_reference_to_contract(
    text: str,
    contract: Dict[str, Any],
    protected_markers: Optional[List[str]] = None,
) -> str:
    max_sentence_chars = int(contract.get("maxSentenceChars", 18) or 18)
    max_answer_chars = int(contract.get("maxAnswerChars", 96) or 96)
    max_sentences = int(contract.get("maxSentences", 3) or 3)
    compacted = compact_child_reference_language(text, protected_markers)
    compiled = fit_child_reference_sentences(compacted, max_sentence_chars)
    sentences = split_sentences(compiled)
    budgeted_sentences: List[str] = []
    for sentence in sentences:
        clauses = [part.strip() for part in re.split(r"[，,]+", sentence) if part.strip()]
        if len(sentence) > max_sentence_chars and len(clauses) > 1 and all(len(part) <= max_sentence_chars for part in clauses):
            budgeted_sentences.extend(clauses)
        else:
            budgeted_sentences.append(sentence)
    sentences = budgeted_sentences
    protected = [marker for marker in normalize_list(protected_markers) if marker in compiled]

    def render(items: List[str]) -> str:
        return "。".join(items) + ("。" if items else "")

    while len(sentences) > max_sentences:
        candidates: List[Tuple[int, int, str, List[str]]] = []
        for index in range(len(sentences) - 1):
            left = sentences[index].rstrip("，,")
            right = sentences[index + 1].lstrip("，,")
            right_variants = [right]
            for prefix in ("那个", "这个", "那块", "这块"):
                if right.startswith(prefix) and len(right) > len(prefix):
                    right_variants.append(right[len(prefix):])
            for right_variant in list(dict.fromkeys(right_variants)):
                merged = f"{left}，{right_variant}"
                if len(merged) > max_sentence_chars:
                    continue
                candidate_sentences = sentences[:index] + [merged] + sentences[index + 2:]
                candidate_text = render(candidate_sentences)
                if len(candidate_text) > max_answer_chars:
                    continue
                if any(marker not in candidate_text for marker in protected):
                    continue
                candidates.append((len(merged), index, merged, candidate_sentences))
        if not candidates:
            break
        _, _, _, sentences = min(candidates, key=lambda item: (item[0], item[1]))
    return render(sentences)


def approved_answer_anchors(text: str, max_groups: int = 4) -> List[List[str]]:
    return [[sentence] for sentence in split_sentences(text)[:max_groups] if sentence]


def compact_fact_text(text: Any) -> str:
    return re.sub(r"[\s，。！？!?；;、：:（）()‘’“”\-—]", "", str(text or ""))


FACT_MATCH_FILLERS = frozenset("的了也就会啦呢呀啊嘛吧")

FACT_MATCH_EQUIVALENTS = (
    ("这样", ""),
    ("天气慢慢变冷", "天气变冷"),
    ("树叶和树枝连不紧", "树叶树枝连接断开"),
    ("树叶与树枝连不紧", "树叶树枝连接断开"),
    ("树叶和树枝连接变松", "树叶树枝连接断开"),
    ("树叶与树枝连接变松", "树叶树枝连接断开"),
    ("树叶连在树枝上小地方断开", "树叶树枝连接断开"),
    ("树叶连在树枝上地方断开", "树叶树枝连接断开"),
    ("大树让树叶和树枝断开", "树叶树枝连接断开"),
    ("大树切断和树叶连接", "树叶树枝连接断开"),
    ("大树切断树叶连接", "树叶树枝连接断开"),
    ("叶子落下", "叶子掉下来"),
    ("树叶落下", "树叶掉下来"),
)

FACT_MATCH_REGEX_EQUIVALENTS = (
    (r"秋天.{0,14}天气变冷", "秋天天气变冷"),
    (r"树叶(?:和树枝)?连(?:在树枝上)?(?:小)?地方不结实", "树叶连接不紧"),
    (r"树叶(?:和树枝)?连接不牢", "树叶连接不紧"),
    (r"(?<!树)叶子", "树叶"),
    (r"掉到地上", "掉下来"),
)


def normalize_fact_match_text(text: Any) -> str:
    compacted = compact_fact_text(text)
    normalized = "".join(character for character in compacted if character not in FACT_MATCH_FILLERS)
    for source, target in FACT_MATCH_EQUIVALENTS:
        normalized = normalized.replace(source, target)
    for pattern, replacement in FACT_MATCH_REGEX_EQUIVALENTS:
        normalized = re.sub(pattern, replacement, normalized)
    return normalized


def safe_experiment_has_adult_companion(text: Any) -> bool:
    experiment = normalize_optional_text(text, 240)
    return bool(experiment) and any(marker in experiment for marker in ("家长", "大人", "爸爸", "妈妈", "监护人"))


def observation_structure_evidence(text: Any) -> Dict[str, bool]:
    experiment = normalize_safe_experiment(text, 480)
    return {
        "adultCompanion": safe_experiment_has_adult_companion(experiment),
        "preparation": any(marker in experiment for marker in ("准备", "找一", "拿一", "选一", "需要")),
        "action": any(marker in experiment for marker in ("一起", "站在", "坐在", "走到", "看一看", "观察", "数一数", "比较", "记录", "指一指")),
        "phenomenon": any(marker in experiment for marker in ("重点观察", "看看", "看见", "留意", "会不会", "有什么", "哪里", "哪一", "快慢", "变化", "现象", "发现", "水滴", "水珠", "雾气", "位置")),
        "parentQuestion": any(marker in experiment for marker in ("家长可以问", "大人可以问", "爸爸可以问", "妈妈可以问", "问孩子", "可以问：", "可以问孩子")),
        "safetyBoundary": any(marker in experiment for marker in ("不要", "不能", "只在", "远离", "注意安全", "待在室内", "隔着窗", "不碰", "不打开")),
    }


def observable_example_evidence(text: Any) -> Dict[str, bool]:
    answer = normalize_optional_text(text, MAX_CHILD_ANSWER_CHARS)
    intro = bool(re.search(r"(?:比如|例如|举个例子|想一想|你可以想象)", answer))
    concrete_scene = any(marker in answer for marker in (
        "杯子", "窗户", "路上", "地面", "水里", "水面", "雨滴", "云里", "手电筒", "影子",
        "小球", "纸片", "冰块", "太阳", "树叶", "水滴", "浴缸", "积木", "车窗", "远山",
    ))
    observable_action = any(marker in answer for marker in (
        "看见", "看到", "会看到", "拿", "放", "倒", "滴", "照", "走", "站", "等一会", "变成", "落下",
    ))
    principle_connection = any(marker in answer for marker in (
        "这说明", "所以你会看到", "它告诉我们", "这个例子说明", "这就是", "也就是说", "原来",
    ))
    return {
        "intro": intro,
        "concreteScene": concrete_scene,
        "observableAction": observable_action,
        "principleConnection": principle_connection,
        "complete": intro and concrete_scene and observable_action and principle_connection,
    }


def ordered_subsequence_span(text: str, term: str, start: int = 0) -> Optional[Tuple[int, int]]:
    if not term:
        return None
    first = text.find(term[0], start)
    while first >= 0:
        cursor = first + 1
        matched = True
        for character in term[1:]:
            cursor = text.find(character, cursor)
            if cursor < 0:
                matched = False
                break
            cursor += 1
        if matched:
            span_length = cursor - first
            allowed_span = max(len(term) + 2, int(len(term) * 1.55))
            if span_length <= allowed_span:
                return first, cursor
        first = text.find(term[0], first + 1)
    return None


def fact_clause_span(normalized_text: str, clause: str, start: int = 0) -> Optional[Tuple[int, int]]:
    normalized_clause = normalize_fact_match_text(clause)
    if not normalized_clause:
        return None
    exact = normalized_text.find(normalized_clause, start)
    if exact >= 0:
        return exact, exact + len(normalized_clause)
    if len(normalized_clause) < 5:
        return None
    return ordered_subsequence_span(normalized_text, normalized_clause, start)


def fact_term_span(text: str, term: Any, start: int = 0) -> Optional[Tuple[int, int]]:
    normalized_text = normalize_fact_match_text(text)
    clauses = [
        clause.strip()
        for clause in re.split(r"[，。！？!?；;、：:\n]+", str(term or ""))
        if normalize_fact_match_text(clause)
    ]
    if not normalized_text or not clauses:
        return None
    first_position: Optional[int] = None
    cursor = start
    for clause in clauses:
        span = fact_clause_span(normalized_text, clause, cursor)
        if span is None:
            return None
        if first_position is None:
            first_position = span[0]
        cursor = span[1]
    return first_position, cursor


def idea_group_present(text: str, group: Any) -> bool:
    terms = group if isinstance(group, list) else [group]
    return any(fact_term_span(text, term) is not None for term in terms if normalize_fact_match_text(term))


def question_requires_uncertainty(question: str) -> bool:
    text = normalize_optional_text(question, 800)
    return any(marker in text for marker in SUBJECTIVE_STATE_MARKERS)


def question_requires_imagination(question: str) -> bool:
    text = normalize_optional_text(question, 800)
    return any(marker in text for marker in IMAGINATION_QUESTION_MARKERS)


def normalize_epistemic_status(value: Any, question: str = "") -> str:
    status = normalize_optional_text(value, 32).lower()
    if status == "uncertain":
        return "subjective_unknown" if question_requires_uncertainty(question) else "evidence_based"
    if status in {"fact", "evidence_based", "subjective_unknown", "imaginative"}:
        return status
    if question_requires_imagination(question):
        return "imaginative"
    if question_requires_uncertainty(question):
        return "subjective_unknown"
    return "evidence_based"


def epistemic_marker_present(answer: str, status: str) -> bool:
    if status == "evidence_based":
        return any(marker in answer for marker in EVIDENCE_BASED_MARKERS)
    if status == "subjective_unknown":
        return bool(epistemic_marker_matches(answer, status))
    if status == "imaginative":
        return bool(epistemic_marker_matches(answer, status))
    return True


def epistemic_marker_matches(answer: str, status: str) -> List[str]:
    text = normalize_optional_text(answer, 360)
    if status == "evidence_based":
        return [marker for marker in EVIDENCE_BASED_MARKERS if marker in text]
    if status == "subjective_unknown":
        matches = [marker for marker in SUBJECTIVE_UNKNOWN_MARKERS if marker in text]
        patterns = (
            r"(?:我们|人们|谁也|现在)?(?:没法|无法|不能|很难)(?:直接)?(?:知道|确定)",
            r"(?:我们|人们|谁也)?(?:说不准|不知道)",
        )
        matches.extend(match.group(0) for pattern in patterns for match in re.finditer(pattern, text))
        return list(dict.fromkeys(matches))
    if status == "imaginative":
        return [marker for marker in ("想象", "故事", "假装", "不是真的") if marker in text]
    return []


def story_structure_evidence(
    answer: str,
    required_groups: List[List[str]],
    causal_chain: List[List[str]],
) -> Dict[str, Any]:
    sentences = split_sentences(answer)
    opening_text = "。".join(sentences[:2])
    ending_text = "。".join(sentences[-2:])
    narrative_markers = ("故事", "从前", "很久以前", "有一天", "一天", "这天")
    character_markers = ("小雨滴", "小水滴", "小云朵", "小月亮", "小影子", "小种子", "小树叶", "小恐龙", "小朋友", "宝宝", "小兔", "小熊", "小猫", "小狗")
    event_markers = ("走", "看", "问", "发现", "来到", "遇到", "停下", "回家", "出发", "抬头", "飘", "落", "旅行", "穿过", "经过", "碰到", "变成", "聚在一起")
    scene_markers = ("草地", "湖面", "水面", "河边", "湖边", "池塘", "云里", "云朵里", "云层", "天空", "空中", "窗边", "窗台", "玻璃", "树叶", "花园", "屋顶", "山坡", "路上", "家里")
    interaction_markers = ("看见", "听见", "遇到", "碰到", "伙伴", "朋友", "打招呼", "问", "回答", "一起", "停下来", "抬头")
    mechanism_markers = ("因为", "所以", "变成", "遇冷", "冷空气", "聚大", "变重", "落下来", "真正原因", "这就是")
    truth_markers = ("其实", "原来", "所以", "这就是", "因为", "说明", "真正")
    first_person_character_present = bool(re.search(r"我(?:是|叫|变成|住在)[^。！？!?；;\n]{0,12}(?:小|一颗|一片|一朵|一粒)", answer))
    first_person_narration_present = bool(
        re.search(r"(?:我|我们)(?:是|叫|住在|飘|走|来到|遇到|发现|看见|跟着|变成|落到)", "。".join(sentences[:3]))
    )
    generic_character_matches = re.findall(r"(?:小[\u4e00-\u9fff]{1,4}|[\u4e00-\u9fff]{1,4}宝宝)", opening_text)
    non_character_phrases = {"小故事", "小问题", "小实验", "小知识", "小例子", "小原因", "小变化"}
    named_character_present = any(marker in answer for marker in character_markers) or any(
        match not in non_character_phrases for match in generic_character_matches
    )
    opening_character_present = named_character_present or first_person_character_present or first_person_narration_present
    # COSEC: “小水滴聚在一起”等正常科学表述不能被误判为故事。
    # 故事开场必须有明确叙事提示，或由角色以第一人称进入情节。
    opening_present = any(marker in opening_text for marker in narrative_markers) or (
        (first_person_character_present or first_person_narration_present)
        and any(marker in opening_text for marker in event_markers)
    )
    ending_groups = causal_chain[-1:] or required_groups
    ending_fact_present = any(idea_group_present(ending_text, group) for group in ending_groups)
    truth_marker_present = any(marker in ending_text for marker in truth_markers)
    character_present = first_person_narration_present or first_person_character_present or (
        any(marker in opening_text for marker in narrative_markers) and named_character_present
    )
    narrative_event_count = sum(1 for sentence in sentences if any(marker in sentence for marker in event_markers))
    ending_start = max(0, len(sentences) - 2)
    truth_start = next(
        (index for index in range(ending_start, len(sentences)) if any(marker in sentences[index] for marker in truth_markers)),
        len(sentences),
    )
    story_sentences = sentences[:truth_start]
    non_fact_narrative_sentences = [
        sentence for sentence in story_sentences
        if any(marker in sentence for marker in scene_markers + interaction_markers)
        and not any(marker in sentence for marker in mechanism_markers)
    ]
    story_scene_present = any(marker in "。".join(story_sentences) for marker in scene_markers)
    character_interaction_present = any(marker in "。".join(story_sentences) for marker in interaction_markers)
    narrative_sentence_ratio = round(truth_start / max(1, len(sentences)), 2)
    # COSEC: 故事模式必须同时有叙事情境和结尾事实回归，不能只靠出现“故事”二字通过。
    truth_return_present = truth_marker_present and (ending_fact_present or not ending_groups)
    return {
        "openingPresent": opening_present,
        "characterPerspectivePresent": character_present,
        "narrativeEventCount": narrative_event_count,
        "narrativeSentenceRatio": narrative_sentence_ratio,
        "storyScenePresent": story_scene_present,
        "characterInteractionPresent": character_interaction_present,
        "nonFactNarrativeSentenceCount": len(non_fact_narrative_sentences),
        "storyBodyPresent": (
            character_present
            and narrative_event_count >= 2
            and narrative_sentence_ratio >= 0.5
            and story_scene_present
            and character_interaction_present
            and len(non_fact_narrative_sentences) >= 1
        ),
        "truthReturnPresent": truth_return_present,
        "endingFactPresent": ending_fact_present,
        "truthMarkerPresent": truth_marker_present,
    }


def local_quality_review(ai: Dict[str, Any], plan: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    answer = normalize_optional_text(ai.get("displayText"))
    sentences = split_sentences(answer)
    feedback_mode = normalize_optional_text(plan.get("feedbackMode"), 24) or "normal"
    required_groups = active_required_idea_groups(plan, feedback_mode)
    causal_chain = active_causal_chain(plan, feedback_mode)
    teachback = plan.get("teachbackAssessment") if isinstance(plan.get("teachbackAssessment"), dict) else None
    group_matches = [idea_group_present(answer, group) for group in required_groups]
    matched = sum(1 for present in group_matches if present)
    causal_order_passed, causal_positions = ordered_idea_groups_present(answer, causal_chain) if causal_chain else (True, [])
    reviewed_observation_present = bool(
        feedback_mode == "example"
        and plan.get("validatedExampleType") == "observation"
        and any(marker in answer for marker in normalize_list(plan.get("validatedExampleMarkers")))
    )
    if reviewed_observation_present and required_groups and matched == len(required_groups):
        causal_order_passed = True
    teachback_outcome = normalize_optional_text((teachback or {}).get("outcome"), 24)
    if teachback and (teachback.get("correct") or teachback_outcome in {"insufficient", "unverified"}):
        factual_score = 1.0
    else:
        factual_score = 1.0 if not required_groups else matched / max(1, len(required_groups))
    avoid_hits = [claim for claim in normalize_list(plan.get("avoidClaims")) if claim and claim in answer]
    metaphor_hits = scientific_metaphor_hits(answer)
    if avoid_hits:
        factual_score = min(factual_score, 0.4)
    if metaphor_hits:
        factual_score = 0.0
    if not causal_order_passed:
        factual_score = min(factual_score, 0.5)
    max_sentence = int(contract.get("maxSentenceChars", 18) or 18)
    min_answer = int(contract.get("minAnswerChars", 0) or 0)
    max_answer = int(contract.get("maxAnswerChars", 96) or 96)
    min_sentences = int(contract.get("minSentences", 0) or 0)
    activity_mode = normalize_activity_mode(contract.get("activityMode"))
    enforce_mode_contract = bool(contract.get("enforceModeContract"))
    longest_sentence = max((len(sentence) for sentence in sentences), default=0)
    fragment_sentences = [sentence for sentence in sentences if len(compact_fact_text(sentence)) <= 4]
    fragment_run = 0
    longest_fragment_run = 0
    for sentence in sentences:
        fragment_run = fragment_run + 1 if len(compact_fact_text(sentence)) <= 4 else 0
        longest_fragment_run = max(longest_fragment_run, fragment_run)
    consecutive_fragments = longest_fragment_run >= 3
    sentence_score = 1.0 if longest_sentence <= max_sentence and len(answer) <= max_answer else 0.82 if longest_sentence <= max_sentence + 12 and len(answer) <= max_answer + 48 else 0.45
    if consecutive_fragments:
        sentence_score = min(sentence_score, 0.45)
    sentence_count_score = 1.0 if len(sentences) <= int(contract.get("maxSentences", 5) or 5) else 0.72
    effective_min_answer = max(0, int(min_answer * 0.9))
    minimum_explanation_passed = not enforce_mode_contract or (
        len(answer) >= effective_min_answer and len(sentences) >= min_sentences
    )
    story_evidence = story_structure_evidence(answer, required_groups, causal_chain)
    story_opening_present = bool(story_evidence["openingPresent"])
    story_body_present = bool(story_evidence["storyBodyPresent"])
    story_truth_return_present = bool(story_evidence["truthReturnPresent"])
    safe_experiment = normalize_safe_experiment(ai.get("safeExperiment"), 480)
    observation_evidence = observation_structure_evidence(safe_experiment)
    safe_experiment_has_adult = observation_evidence["adultCompanion"]
    observation_structure_complete = all(observation_evidence.values())
    no_unrequested_activity = not safe_experiment
    unrequested_observation_instruction = activity_mode in {"ask", "detail"} and bool(re.search(
        r"(?:你可以|我们来|请你|试着|一起|去)(?:[^。！？!?；;\n]{0,16})(?:看一看|看看|摸一摸|摸摸|观察|试一试|试试|数一数|听一听|闻一闻)",
        answer,
    ))
    no_unrequested_observation_instruction = not unrequested_observation_instruction
    no_story_frame = not story_opening_present
    if not enforce_mode_contract:
        mode_fit = True
    elif activity_mode == "story":
        mode_fit = minimum_explanation_passed and story_opening_present and story_body_present and story_truth_return_present and no_unrequested_activity
    elif activity_mode == "observe":
        mode_fit = minimum_explanation_passed and no_story_frame and observation_structure_complete
    elif activity_mode in {"ask", "detail"}:
        mode_fit = minimum_explanation_passed and no_story_frame and no_unrequested_activity and no_unrequested_observation_instruction
    else:
        mode_fit = minimum_explanation_passed
    mode_score = 1.0 if mode_fit else 0.0
    concept_labels = normalize_list(plan.get("conceptLabels"))
    concept_limit = int(contract.get("conceptLimit", 1) or 1)
    concept_score = 1.0 if len(concept_labels) <= concept_limit else 0.0
    adult_hits = [term for term in ADULT_TERMS if term in answer]
    age_score = max(0.0, min(sentence_score, 1.0 - len(adult_hits) * 0.2))
    epistemic_score = 1.0
    epistemic_status = normalize_epistemic_status(plan.get("epistemicStatus"))
    epistemic_matches = epistemic_marker_matches(answer, epistemic_status)
    if epistemic_status != "fact" and not epistemic_matches:
        epistemic_score = 0.0
    analogy_used = answer_uses_analogy(ai, plan)
    analogy_policy = normalize_optional_text(plan.get("analogyPolicy"), 32) or "omit"
    vetted_markers = normalize_list(plan.get("validatedAnalogyMarkers"))
    vetted_analogy = not analogy_used or (
        analogy_policy == "allow_vetted"
        and bool(vetted_markers)
        and any(marker in answer for marker in vetted_markers)
        and factual_score == 1.0
    )
    analogy_score = 1.0 if vetted_analogy else 0.0
    example_markers = normalize_list(plan.get("validatedExampleMarkers"))
    example_required = feedback_mode == "example"
    example_evidence = observable_example_evidence(answer)
    requested_example_present = (
        (bool(example_markers) and any(marker in answer for marker in example_markers))
        or example_evidence["complete"]
    )
    exact_example = normalize_optional_text(plan.get("validatedExample"), 180)
    matched_example_markers = [marker for marker in example_markers if marker in answer]
    unrequested_example = activity_mode != "story" and epistemic_status != "imaginative" and feedback_mode not in {"example"} and bool(
        (exact_example and compact_fact_text(exact_example) in compact_fact_text(answer))
        or (matched_example_markers and any(prefix in answer for prefix in ("比如", "例如", "举个例子")))
        or (len(example_markers) >= 2 and len(matched_example_markers) == len(example_markers))
    )
    example_present = (not example_required or requested_example_present) and not unrequested_example
    previous_answer = normalize_optional_text(plan.get("previousAnswer"), MAX_CHILD_ANSWER_CHARS)
    repetition_ratio = answer_repetition_ratio(answer, previous_answer) if feedback_mode in {"confused", "simpler", "example", "why"} else 0.0
    repetition_limit = 0.66 if feedback_mode == "why" else 0.72
    rephrase_score = 1.0 if not previous_answer or repetition_ratio < repetition_limit else 0.0
    example_score = 1.0 if example_present and rephrase_score == 1.0 else 0.0
    correction_markers = normalize_list(plan.get("teachbackCorrectionMarkers"))
    teachback_ack_required = bool(teachback and teachback.get("correct"))
    teachback_ack_present = not teachback_ack_required or any(marker in answer for marker in TEACHBACK_ACK_MARKERS)
    teachback_correction_required = bool(teachback and teachback_outcome == "contradicted")
    teachback_correction_present = not teachback_correction_required or (
        bool(correction_markers) and any(marker in answer for marker in correction_markers)
    )
    teachback_unverified_required = bool(teachback and teachback_outcome in {"insufficient", "unverified"})
    teachback_unverified_present = not teachback_unverified_required or any(marker in answer for marker in TEACHBACK_UNVERIFIED_MARKERS)
    teachback_score = 1.0 if teachback_ack_present and teachback_correction_present and teachback_unverified_present else 0.0
    safety_score = 1.0 if generated_answer_safety_check(ai, plan)["level"] == "safe" else 0.0
    schema_score = 1.0 if answer and normalize_optional_text(ai.get("speakText")) else 0.0
    plan_score = 1.0 if plan.get("planStatus", "ready") in {"ready", "safety_fallback"} else 0.0
    scores = {
        "planGrounding": round(plan_score, 2),
        "factualCore": round(factual_score, 2),
        "epistemicClarity": round(epistemic_score, 2),
        "ageLanguage": round(age_score, 2),
        "cognitiveLoad": round(min(sentence_count_score, concept_score), 2),
        "analogyBounded": round(analogy_score, 2),
        "feedbackFit": round(example_score, 2),
        "teachbackFit": round(teachback_score, 2),
        "modeFit": round(mode_score, 2),
        "safety": round(safety_score, 2),
        "schema": round(schema_score, 2),
    }
    violations = []
    if plan_score < 1:
        violations.append("问题计划尚未通过事实与儿童表达校验")
    missing_indexes = [str(index + 1) for index, present in enumerate(group_matches) if not present]
    if missing_indexes:
        violations.append(f"没有保留全部事实原子；缺少第 {'、'.join(missing_indexes)} 组")
    if avoid_hits:
        violations.append(f"出现应避免的断言：{'、'.join(avoid_hits)}")
    if metaphor_hits:
        violations.append(f"科学机制被拟人动作替代：{'、'.join(metaphor_hits)}")
    if not causal_order_passed:
        violations.append("因果链缺少步骤或顺序错误，不能只保留开头和结果")
    if epistemic_score < 1:
        if epistemic_status == "evidence_based":
            violations.append("有证据支持但仍有范围限制的解释，缺少‘科学家认为/证据显示/主要因为’等证据措辞")
        elif epistemic_status == "subjective_unknown":
            violations.append("无法直接验证的主观感受没有明确说不知道或不能确定")
        else:
            violations.append("想象回答没有明确区分想象与现实")
    if age_score < 0.7:
        violations.append(f"句子或词汇超过本轮年龄预算；成人术语：{'、'.join(adult_hits) or '无'}")
    if consecutive_fragments:
        violations.append("回答包含连续碎片句，需要合并为自然完整的中文句子")
    if enforce_mode_contract and not minimum_explanation_passed:
        violations.append(f"回答过短，尚未达到“{ACTIVITY_MODE_LABELS[activity_mode]}”需要的解释量")
    if enforce_mode_contract and activity_mode == "story" and (not story_opening_present or not story_body_present or not story_truth_return_present):
        violations.append("故事模式必须有角色视角、至少两个连续事件，并在结尾单独回到真实原因")
    if enforce_mode_contract and activity_mode == "story" and not no_unrequested_activity:
        violations.append("故事模式不应另外布置观察任务，safeExperiment必须留空")
    if enforce_mode_contract and activity_mode == "observe" and not observation_structure_complete:
        missing_observation_parts = [
            label for key, label in (
                ("adultCompanion", "家长陪同"),
                ("preparation", "准备物品或地点"),
                ("action", "具体观察动作"),
                ("phenomenon", "要看的现象"),
                ("parentQuestion", "家长可问的问题"),
                ("safetyBoundary", "安全边界"),
            ) if not observation_evidence[key]
        ]
        violations.append(f"观察模式活动不完整，缺少：{'、'.join(missing_observation_parts)}")
    if enforce_mode_contract and activity_mode == "observe" and not no_story_frame:
        violations.append("观察模式应聚焦现实观察，不应使用故事开场")
    if enforce_mode_contract and activity_mode in {"ask", "detail"} and not no_story_frame:
        violations.append(f"{ACTIVITY_MODE_LABELS[activity_mode]}不应使用故事开场或故事角色结构")
    if enforce_mode_contract and activity_mode in {"ask", "detail"} and not no_unrequested_activity:
        violations.append(f"{ACTIVITY_MODE_LABELS[activity_mode]}不应布置观察任务，safeExperiment必须留空")
    if enforce_mode_contract and activity_mode in {"ask", "detail"} and not no_unrequested_observation_instruction:
        violations.append(f"{ACTIVITY_MODE_LABELS[activity_mode]}只解释原因，不应在displayText中布置观察、触摸或尝试任务")
    if sentence_count_score < 1:
        violations.append("一次讲了太多句子或概念")
    if concept_score < 1:
        violations.append(f"计划引入 {len(concept_labels)} 个概念，超过本轮 {concept_limit} 个概念预算")
    if analogy_score < 0.7:
        violations.append("使用了未验证类比，或类比替代了真实解释")
    if example_score < 1:
        if rephrase_score < 1:
            violations.append("反馈重答与上一轮过于相似，没有真正换一种说法")
        else:
            violations.append("举例反馈不匹配：要求举例时缺少已验证例子，或未要求时擅自增加例子")
    if teachback_score < 1:
        violations.append("复述反馈不匹配：正确复述未明确确认，或错误复述未明确否定具体误解")
    if safety_score < 1:
        violations.append("回答或观察活动触发儿童安全边界")
    overall = round(sum(scores.values()) / len(scores), 2)
    passed = plan_score == 1 and safety_score == 1 and schema_score == 1 and factual_score == 1 and epistemic_score == 1 and age_score >= 0.7 and sentence_count_score == 1 and concept_score == 1 and analogy_score == 1 and example_score == 1 and teachback_score == 1 and mode_score == 1 and overall >= 0.82
    return {
        "passed": passed,
        "overall": overall,
        "scores": scores,
        "violations": violations,
        "atomicCoverage": {"matched": matched, "required": len(required_groups), "allPresent": matched == len(required_groups)},
        "causalCoverage": {"required": len(causal_chain), "ordered": causal_order_passed, "positions": causal_positions},
        "analogyUsed": analogy_used,
        "analogyPolicy": analogy_policy,
        "epistemicEvidence": {"status": epistemic_status, "markers": epistemic_matches},
        "languageEvidence": {
            "answerChars": len(answer),
            "minAnswerChars": min_answer,
            "effectiveMinAnswerChars": effective_min_answer,
            "sentenceCount": len(sentences),
            "minSentences": min_sentences,
            "sentenceChars": [len(sentence) for sentence in sentences],
            "maxAnswerChars": max_answer,
            "maxSentenceChars": max_sentence,
            "maxSentences": int(contract.get("maxSentences", 3) or 3),
            "adultTerms": adult_hits,
            "conceptLabels": concept_labels,
            "conceptLimit": concept_limit,
            "previousAnswerSimilarity": round(repetition_ratio, 3),
            "fragmentSentences": fragment_sentences,
            "consecutiveFragments": consecutive_fragments,
            "activityMode": activity_mode,
            "modeFit": mode_fit,
            "storyStructure": story_evidence,
            "safeExperimentHasAdultCompanion": safe_experiment_has_adult,
            "observationStructure": observation_evidence,
            "observationStructureComplete": observation_structure_complete,
            "unrequestedObservationInstruction": unrequested_observation_instruction,
            "observableExample": example_evidence,
        },
        "reviewSource": "local",
    }


def feedback_instruction(feedback: str) -> str:
    return {
        "confused": "先说‘刚才可能没讲清楚’，再从另一个角度重新解释。不要重复上一轮句式；用一个具体、可观察的过程讲清原因，最后问孩子现在听懂哪一点。",
        "simpler": "保留正确原因，但减少概念和术语，用自然完整的话重新讲。不是机械删字，也不要重复上一轮原句。",
        "example": "先用一句话回应孩子，再使用questionPlan.validatedExample中的已验证观察例子。例子后要明确指出它说明了什么；没有已验证例子时不要现编科学类比。",
        "why": "承接上一轮答案，多补一层原因，说明‘这个原因为什么会发生’。不要从头重复整段答案。",
        "teachback": "孩子正在用自己的话说。请温柔鼓励，并判断相关内容是不是更明白了。",
    }.get(feedback or "normal", "正常适龄解释。")


def answer_repetition_ratio(answer: str, previous_answer: str) -> float:
    current = compact_fact_text(answer)
    previous = compact_fact_text(previous_answer)
    if not current or not previous:
        return 0.0
    return SequenceMatcher(None, current, previous).ratio()


def extract_concepts(text: str, answer: str) -> List[Dict[str, Any]]:
    raw = text + " " + answer
    candidates = [
        ("月亮和天空", ["月亮", "天空", "星星"], "远处的大山"),
        ("远近与视觉位置", ["跟着", "远", "近", "看起来", "位置"], "车窗外远山"),
        ("影子和光", ["影子", "光", "太阳", "手电筒"], "手电筒照积木"),
        ("恐龙与化石", ["恐龙", "化石", "灭绝"], "恐龙脚印"),
        ("水和浮沉", ["水", "浮", "沉", "浴缸", "船"], "浴缸里的小船"),
        ("天气和云", ["雨", "云", "雷", "天气"], "海绵挤水"),
        ("植物成长", ["树", "花", "种子", "长大"], "小朋友吃饭长高"),
        ("孩子复述表达", ["我觉得", "我知道", "我来讲", "因为"], "用自己的话讲一遍"),
    ]
    hits = []
    for concept, keys, analogy in candidates:
        if any(k in raw for k in keys):
            hits.append({
                "concept": concept,
                "status": "candidate",
                "confidence": 0.42,
                "evidence": text[:80],
                "effectiveAnalogy": analogy,
            })
    if not hits:
        hits.append({"concept": "新的好奇问题", "status": "candidate", "confidence": 0.28, "evidence": text[:80], "effectiveAnalogy": "积木一步步搭起来"})
    return hits[:2]


def is_safe_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
    except ValueError:
        return False


def validate_outbound_url(raw_url: str) -> bool:
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("https",):
        return False
    if parsed.hostname not in ALLOWED_OPENAI_HOSTS:
        return False
    try:
        return all(is_safe_ip(sockaddr[0]) for *_, sockaddr in socket.getaddrinfo(parsed.hostname, None))
    except OSError:
        return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # COSEC: SSRF 防护——OpenAI-compatible 调用不跟随重定向，避免跳转到内网/非白名单地址。
        raise urllib.error.HTTPError(req.full_url, code, "Redirect blocked", headers, fp)


def model_timeout_seconds(component: str, model_config: Dict[str, Any]) -> float:
    parent_components = {"parent_analysis", "question_planner", "quality_reviewer", "teachback_reviewer"}
    component_env = "PARENT_ANALYSIS_TIMEOUT_SECONDS" if component in parent_components else "CHILD_ANSWER_TIMEOUT_SECONDS"
    default_timeout = 28 if component in parent_components else 10
    configured = (
        os.environ.get(component_env)
        or os.environ.get("LLM_TIMEOUT_SECONDS")
        or os.environ.get("OPENAI_TIMEOUT_SECONDS")
        or model_config.get("timeout_seconds")
        or default_timeout
    )
    return clamp_float(configured, default_timeout, 1.2, 30)


def interaction_budget_seconds() -> float:
    return clamp_float(os.environ.get("INTERACTION_BUDGET_SECONDS"), 45.0, 10.0, 120.0)


def remaining_interaction_seconds() -> Optional[float]:
    deadline = INTERACTION_DEADLINE.get()
    return max(0.0, deadline - time.perf_counter()) if deadline is not None else None


def transient_model_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, socket.timeout, ConnectionError))
    return isinstance(exc, ConnectionError)


def call_model_json(component: str, system: str, user_payload: Dict[str, Any], temperature: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Historical alignment helpers are available to offline regressions only.

    Live text calls use CompanionService's single budgeted ModelClient. Prevent
    old scripts from making unbounded calls or selecting a different model.
    """
    raise RuntimeError("旧规划链的真实调用已停用；请通过正常页面使用当前模型链路。")


def speech_runtime(component: str) -> Tuple[Dict[str, Any], str, str]:
    runtime = model_component_config(component)
    provider_config = runtime["provider"]
    model_config = runtime["model"]
    if model_config.get("enabled") is False:
        raise ValueError("speech component is disabled")
    api_key = model_config.get("api_key") or provider_config.get("api_key")
    base_value = model_config.get("base_url") or provider_config.get("base_url")
    if not isinstance(api_key, str) or not api_key.strip() or not isinstance(base_value, str) or not base_value.strip():
        raise ValueError("speech component is not configured")
    return model_config, api_key.strip(), base_value.strip().rstrip("/")


def decode_audio_data(audio_data: Any, declared_mime: Any) -> Tuple[str, str]:
    if not isinstance(audio_data, str) or not audio_data.startswith("data:"):
        raise ValueError("invalid audio data")
    header, separator, encoded = audio_data.partition(",")
    if not separator or ";base64" not in header.lower():
        raise ValueError("audio data must be base64")
    embedded_mime = header[5:].split(";", 1)[0].strip().lower()
    mime_type = str(declared_mime or embedded_mime).split(";", 1)[0].strip().lower()
    if embedded_mime != mime_type or mime_type not in ALLOWED_AUDIO_MIME_TYPES:
        raise ValueError("unsupported audio format")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid audio encoding") from exc
    if not decoded or len(decoded) > MAX_AUDIO_BYTES:
        raise ValueError("audio is empty or too large")
    return mime_type, encoded


def transcribe_speech(audio_data: Any, mime_type: Any) -> str:
    model_config, api_key, base = speech_runtime("speech_recognition")
    mime, encoded = decode_audio_data(audio_data, mime_type)
    model = normalize_optional_text(model_config.get("http_model_id"), 120)
    if not model:
        model = normalize_optional_text(model_config.get("model_id"), 120).replace("-realtime", "")
    endpoint = f"{base}/chat/completions"
    if not model or not validate_outbound_url(endpoint):
        raise ValueError("speech recognition endpoint is not allowed")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {
                    "data": f"data:{mime};base64,{encoded}",
                },
            }],
        }],
        "stream": False,
        "asr_options": {"language": "zh", "enable_itn": True},
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    # COSEC: ASR 仅访问配置中的 HTTPS 白名单服务，DNS 校验后禁止任何重定向。
    opener = build_opener(NoRedirect, HTTPSHandler, HTTPHandler)
    with opener.open(request, timeout=clamp_float(model_config.get("timeout_seconds"), 12, 3, 20)) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("speech recognition response is too large")
    data = json.loads(raw.decode("utf-8"))
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        text = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    else:
        text = str(content or "")
    text = text.strip()[:800]
    if not text:
        raise ValueError("no speech recognized")
    return text


def validate_generated_audio_url(raw_url: Any) -> str:
    if not isinstance(raw_url, str):
        return ""
    parsed = urlparse(raw_url.strip())
    hostname = (parsed.hostname or "").lower()
    if not hostname or not (hostname == "aliyuncs.com" or hostname.endswith(".aliyuncs.com")):
        return ""
    if parsed.username or parsed.password:
        return ""
    # COSEC: 百炼可能返回可信 OSS 主机的 HTTP 临时地址；只对固定阿里云域名原位升级 HTTPS。
    if parsed.scheme == "http":
        parsed = parsed._replace(scheme="https")
    if parsed.scheme != "https":
        return ""
    return parsed.geturl()


def extract_synthesized_audio(data: Dict[str, Any]) -> str:
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    audio = output.get("audio")
    candidates: List[Any] = []
    if isinstance(audio, dict):
        candidates.extend([audio.get("url"), audio.get("data"), audio.get("audio")])
    else:
        candidates.append(audio)
    candidates.extend([output.get("audio_url"), output.get("url")])
    for candidate in candidates:
        safe_url = validate_generated_audio_url(candidate)
        if safe_url:
            return safe_url
        if not isinstance(candidate, str):
            continue
        value = candidate.strip()
        if value.startswith("data:audio/"):
            try:
                mime, encoded = decode_audio_data(value, value[5:].split(";", 1)[0])
            except ValueError:
                continue
            return f"data:{mime};base64,{encoded}"
        if value and len(value) <= MAX_AUDIO_BYTES * 2:
            try:
                decoded = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                continue
            if decoded and len(decoded) <= MAX_AUDIO_BYTES:
                mime = normalize_optional_text((audio or {}).get("mime_type") if isinstance(audio, dict) else "", 48) or "audio/mpeg"
                if mime in ALLOWED_AUDIO_MIME_TYPES:
                    return f"data:{mime};base64,{value}"
    return ""


def synthesize_speech(text: Any) -> Dict[str, str]:
    model_config, api_key, base = speech_runtime("speech_synthesis")
    speech_text = normalize_optional_text(text, 600)
    if not speech_text:
        raise ValueError("speech text is empty")
    parsed_base = urlparse(base)
    endpoint = f"https://{parsed_base.hostname}/api/v1/services/aigc/multimodal-generation/generation"
    if not validate_outbound_url(endpoint):
        raise ValueError("speech synthesis endpoint is not allowed")
    model = normalize_optional_text(model_config.get("http_model_id"), 120) or "qwen3-tts-instruct-flash"
    voice = normalize_optional_text(model_config.get("voice"), 48) or "Serena"
    instructions = normalize_optional_text(
        model_config.get("instructions"),
        240,
    ) or "用温暖、自然、有耐心的语气，像亲切的幼儿园老师一样讲给孩子听。语速稍慢，不要夸张。"
    payload = {
        "model": model,
        "input": {
            "text": speech_text,
            "voice": voice,
            "language_type": "Chinese",
            "instructions": instructions,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    # COSEC: TTS 请求固定到配置服务的 HTTPS 主机并禁用重定向，返回音频仅接受阿里云 HTTPS 地址。
    opener = build_opener(NoRedirect, HTTPSHandler, HTTPHandler)
    with opener.open(request, timeout=clamp_float(model_config.get("timeout_seconds"), 12, 3, 20)) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("speech synthesis response is too large")
    data = json.loads(raw.decode("utf-8"))
    audio_url = extract_synthesized_audio(data)
    if not audio_url:
        raise ValueError("speech synthesis returned no usable audio")
    return {"audioUrl": audio_url, "voice": voice}


def reference_answer_candidate(text: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_ai_response({
        "displayText": text,
        "speakText": text,
        "followUp": "",
        "safeExperiment": "",
        "avatarState": "speaking",
        "introducedConcepts": [],
        "knowledgeCardUpdates": [],
        "safetyAction": "none",
        "needsParent": False,
        "answerType": plan.get("questionType", "factual"),
        "truthKernel": normalize_optional_text(plan.get("truthKernel"), 240),
        "epistemicStatus": plan.get("epistemicStatus", "fact"),
        "analogy": "",
    })


def normalize_question_plan(raw_plan: Dict[str, Any], heuristic: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    result = dict(heuristic)
    for key in [
        "questionType", "childIntent", "truthKernel", "epistemicStatus", "requiredIdeaGroups", "requiredIdeaGroupsByMode", "causalChainByMode", "avoidClaims",
        "analogyPolicy", "validatedAnalogy", "validatedAnalogyMarkers", "validatedExample", "validatedExampleMarkers", "validatedExampleType",
        "childSafeAnswerNormal", "childSafeAnswerSimpler", "childSafeAnswerExample",
        "childSafeTeachbackCorrect", "childSafeTeachbackIncorrect", "teachbackCorrectionMarkers", "conceptLabels",
    ]:
        if key in raw_plan:
            result[key] = raw_plan[key]
    result["requiredIdeaGroups"] = normalize_idea_groups(result.get("requiredIdeaGroups"))
    result["requiredIdeaGroupsByMode"] = normalize_mode_groups(result.get("requiredIdeaGroupsByMode"))
    result["causalChainByMode"] = normalize_mode_groups(result.get("causalChainByMode"))
    result["avoidClaims"] = normalize_list(result.get("avoidClaims"))[:6]
    result["analogyPolicy"] = "allow_vetted" if result.get("analogyPolicy") == "allow_vetted" and normalize_optional_text(result.get("validatedAnalogy"), 160) else "omit"
    result["validatedAnalogyMarkers"] = normalize_list(result.get("validatedAnalogyMarkers"))[:4] if result["analogyPolicy"] == "allow_vetted" else []
    result["validatedAnalogy"] = normalize_optional_text(result.get("validatedAnalogy"), 160) if result["analogyPolicy"] == "allow_vetted" else ""
    result["validatedExample"] = normalize_optional_text(result.get("validatedExample"), 180)
    result["validatedExampleMarkers"] = normalize_list(result.get("validatedExampleMarkers"))[:4]
    example_type = normalize_optional_text(result.get("validatedExampleType"), 24)
    result["validatedExampleType"] = example_type if example_type in {"observation", "analogy", "imaginative"} else "observation"
    result["teachbackCorrectionMarkers"] = normalize_list(result.get("teachbackCorrectionMarkers"))[:4]
    result["conceptLabels"] = normalize_list(result.get("conceptLabels"))[:3]
    for key in [
        "questionType", "childIntent", "truthKernel", "epistemicStatus",
        "childSafeAnswerNormal", "childSafeAnswerSimpler", "childSafeAnswerExample",
        "childSafeTeachbackCorrect", "childSafeTeachbackIncorrect",
    ]:
        result[key] = normalize_optional_text(result.get(key), 240)
    result["epistemicStatus"] = normalize_epistemic_status(result["epistemicStatus"], question)
    return result


def fit_plan_metadata_to_contract(plan: Dict[str, Any], profile: Dict[str, Any], feedback: str = "normal") -> Dict[str, Any]:
    fitted = dict(plan)
    contract = child_level_alignment(profile, feedback=feedback)
    concept_limit = int(contract.get("conceptLimit", 1) or 1)
    fitted["conceptLabels"] = normalize_list(fitted.get("conceptLabels"))[:concept_limit]
    return fitted


def validate_question_plan(
    plan: Dict[str, Any],
    profile: Dict[str, Any],
    required_feedback: str = "normal",
    question: str = "",
    strict_atomic_alignment: bool = True,
    validate_reference_quality: bool = True,
    require_reference_answers: bool = True,
) -> Dict[str, Any]:
    violations: List[str] = []
    epistemic_status = plan.get("epistemicStatus")
    required_groups = active_required_idea_groups(plan, "normal")
    if not normalize_optional_text(plan.get("truthKernel"), 240):
        violations.append("缺少事实核心 truthKernel")
    if epistemic_status not in {"fact", "evidence_based", "subjective_unknown", "imaginative"}:
        violations.append("认识状态必须是 fact、evidence_based、subjective_unknown 或 imaginative")
    if question_requires_uncertainty(question) and epistemic_status != "subjective_unknown":
        violations.append("问题询问不可直接验证的主观感受，认识状态必须是 subjective_unknown")
    if question_requires_imagination(question) and epistemic_status != "imaginative":
        violations.append("假设或想象问题必须明确标记为 imaginative")
    if epistemic_status != "imaginative" and not required_groups:
        violations.append("事实问题缺少不可丢失的事实原子")
    base_max_groups = 3 if int(profile.get("age", 5) or 5) <= 5 else 4
    max_causal_steps = 4
    causal_by_mode = normalize_mode_groups(plan.get("causalChainByMode"))

    def mode_group_limit(mode: str) -> int:
        causal_steps = len(causal_by_mode.get(mode, []))
        return min(max_causal_steps, max(base_max_groups, causal_steps))

    normal_group_limit = mode_group_limit("normal")
    if len(required_groups) > normal_group_limit or any(not isinstance(group, list) or not group for group in required_groups):
        violations.append(f"事实原子必须是 1-{normal_group_limit} 组非空近义短语")
    by_mode = plan.get("requiredIdeaGroupsByMode") if isinstance(plan.get("requiredIdeaGroupsByMode"), dict) else {}
    requested_mode = "simpler" if required_feedback in {"confused", "simpler"} else "example" if required_feedback == "example" else "normal"
    relevant_modes = {"normal", requested_mode}
    for mode, groups in by_mode.items():
        normalized_groups = normalize_idea_groups(groups)
        if mode not in set(PLAN_MODES) or not normalized_groups:
            violations.append(f"分模式事实原子只能包含 normal、simpler、example，且不能为空")
        elif mode in relevant_modes and len(normalized_groups) > mode_group_limit(mode):
            violations.append(
                f"{mode} 模式事实原子必须是 1-{mode_group_limit(mode)} 组；"
                "只有不可省略的有序因果桥可以使用第 4 组"
            )
    for mode, chain in causal_by_mode.items():
        if len(chain) > max_causal_steps:
            violations.append(f"{mode} 因果链超过允许的 {max_causal_steps} 步")
    canonical_groups = ["|".join(sorted(set(normalize_list(group)))) for group in required_groups]
    if len(canonical_groups) != len(set(canonical_groups)):
        violations.append("事实原子组存在重复")
    concept_labels = normalize_list(plan.get("conceptLabels"))
    if not concept_labels or len(concept_labels) > 2:
        violations.append("必须提供 1-2 个儿童层级概念标签")
    if plan.get("analogyPolicy") == "allow_vetted" and (
        not normalize_optional_text(plan.get("validatedAnalogy"), 160)
        or not normalize_list(plan.get("validatedAnalogyMarkers"))
    ):
        violations.append("允许类比时必须给出唯一已验证类比及可检测标记")
    if required_feedback == "example" and (
        not normalize_optional_text(plan.get("validatedExample"), 180)
        or not normalize_list(plan.get("validatedExampleMarkers"))
    ):
        violations.append("必须提供一个可观察、非虚构的已验证例子及标记")
    if required_feedback == "example" and plan.get("validatedExampleType") not in {"observation", "analogy", "imaginative"}:
        violations.append("已验证例子必须明确标记 observation、analogy 或 imaginative")
    plan_metaphors = scientific_metaphor_hits("。".join([
        normalize_optional_text(plan.get("truthKernel"), 240),
        "。".join(term for group in required_groups for term in normalize_list(group)),
        normalize_optional_text(plan.get("childSafeAnswerNormal"), 240),
        normalize_optional_text(plan.get("childSafeAnswerSimpler"), 240),
        normalize_optional_text(plan.get("childSafeAnswerExample"), 240),
    ]))
    if plan_metaphors:
        violations.append(f"计划用拟人动作代替科学机制：{'、'.join(plan_metaphors)}")

    reference_checks: Dict[str, Dict[str, Any]] = {}
    modes = [("normal", "childSafeAnswerNormal", None)] if require_reference_answers else []
    if require_reference_answers and required_feedback in {"confused", "simpler"}:
        modes.append(("simpler", "childSafeAnswerSimpler", None))
    elif require_reference_answers and required_feedback == "example":
        modes.append(("example", "childSafeAnswerExample", None))
    elif require_reference_answers and required_feedback == "teachback":
        modes.extend([
            ("teachback_correct", "childSafeTeachbackCorrect", {"correct": True}),
            ("teachback_incorrect", "childSafeTeachbackIncorrect", {"correct": False}),
        ])
    for mode, key, teachback in modes:
        text = normalize_optional_text(plan.get(key), 240)
        if not text:
            violations.append(f"缺少已验证儿童参考答案：{key}")
            reference_checks[mode] = {"passed": False, "violations": ["字段为空"]}
            continue
        feedback = "teachback" if teachback is not None else mode
        contract = child_level_alignment(profile, feedback=feedback)
        review_plan = {**plan, "planStatus": "ready", "feedbackMode": feedback}
        if not strict_atomic_alignment:
            review_plan["requiredIdeaGroups"] = []
            review_plan["requiredIdeaGroupsByMode"] = {}
        if teachback is not None:
            review_plan["teachbackAssessment"] = teachback
        candidate = reference_answer_candidate(text, plan)
        review = local_quality_review(candidate, review_plan, contract) if validate_reference_quality else {
            "passed": True,
            "violations": [],
            "atomicCoverage": {"matched": 0, "required": 0, "allPresent": True},
            "languageEvidence": {
                "answerChars": len(text),
                "sentenceCount": len(split_sentences(text)),
                "sentenceChars": [len(sentence) for sentence in split_sentences(text)],
            },
        }
        reference_checks[mode] = {
            "passed": bool(review.get("passed")),
            "violations": review.get("violations", []),
            "atomicCoverage": review.get("atomicCoverage", {}),
            "languageEvidence": review.get("languageEvidence", {}),
        }
        if not review.get("passed"):
            violations.append(f"{key} 未通过儿童质量门禁：{'；'.join(review.get('violations', []))}")
    return {
        "valid": not violations,
        "violations": list(dict.fromkeys(violations)),
        "referenceChecks": reference_checks,
        "strictAtomicAlignment": strict_atomic_alignment,
        "referenceQualityChecked": validate_reference_quality,
        "referenceAnswersRequired": require_reference_answers,
    }


def trusted_plan_semantic_review(source: str = "curated_reference") -> Dict[str, Any]:
    return {
        "passed": True,
        "factuallyGrounded": True,
        "epistemicAppropriate": True,
        "childReferencesAccurate": True,
        "minimalTruthSufficient": True,
        "materialOmission": False,
        "canonicalIdeaGroups": [],
        "canonicalIdeaGroupsByMode": {},
        "canonicalCausalChainByMode": {},
        "approvedChildAnswersByMode": {},
        "referenceRepairProvided": False,
        "optionalAdvancedDetails": [],
        "violations": [],
        "repairInstructions": [],
        "reviewSource": source,
    }


def compiled_plan_semantic_review(structural_validation: Dict[str, Any]) -> Dict[str, Any]:
    passed = bool(structural_validation.get("valid"))
    violations = [] if passed else normalize_review_list(structural_validation.get("violations"))[:8]
    return {
        "passed": passed,
        "factuallyGrounded": passed,
        "epistemicAppropriate": passed,
        "childReferencesAccurate": passed,
        "minimalTruthSufficient": passed,
        "materialOmission": False,
        "canonicalIdeaGroups": [],
        "canonicalIdeaGroupsByMode": {},
        "canonicalCausalChainByMode": {},
        "approvedChildAnswersByMode": {},
        "referenceRepairProvided": False,
        "optionalAdvancedDetails": [],
        "violations": violations,
        "repairInstructions": [],
        "reviewSource": "parent_analysis_compiled",
        "independentReview": False,
    }


def compile_single_pass_plan_references(plan: Dict[str, Any], profile: Dict[str, Any], required_feedback: str) -> Dict[str, Any]:
    compiled = dict(plan)
    answer_keys = {
        "normal": "childSafeAnswerNormal",
        "simpler": "childSafeAnswerSimpler",
        "example": "childSafeAnswerExample",
    }
    compiled_groups: Dict[str, List[List[str]]] = {}
    for mode, key in answer_keys.items():
        answer = normalize_optional_text(compiled.get(key), 240)
        if not answer:
            continue
        feedback = "simpler" if mode == "simpler" else "example" if mode == "example" else "normal"
        protected_markers = []
        if mode == "example":
            protected_markers = select_minimal_example_markers(
                answer,
                normalize_list(compiled.get("validatedExampleMarkers")),
            )
        fitted_answer = compile_child_reference_to_contract(
            answer,
            child_level_alignment(profile, feedback=feedback),
            protected_markers,
        )
        compiled[key] = fitted_answer
        anchors = approved_answer_anchors(fitted_answer)
        if anchors:
            compiled_groups[mode] = anchors
        if mode == "example":
            compiled["validatedExampleMarkers"] = [marker for marker in protected_markers if marker in fitted_answer]
    if compiled_groups:
        compiled["requiredIdeaGroupsByMode"] = compiled_groups
        if compiled_groups.get("normal"):
            compiled["requiredIdeaGroups"] = compiled_groups["normal"]
        if normalize_epistemic_status(compiled.get("epistemicStatus")) in {"subjective_unknown", "imaginative"}:
            compiled["causalChainByMode"] = {}
        else:
            compiled["causalChainByMode"] = {
                mode: groups
                for mode, groups in compiled_groups.items()
                if mode == "normal" or mode == "simpler" or (mode == "example" and required_feedback == "example")
            }
        compiled["ideaGroupSource"] = "parent_analysis_compiled_answer_anchors"
        compiled["childExpressionSource"] = "parent_analysis_compiled"
    return compiled


def review_question_plan_with_model(question: str, plan: Dict[str, Any], profile: Dict[str, Any], required_feedback: str) -> Dict[str, Any]:
    system = (
        "你是独立的儿童科学事实与认识边界审核器，不是原规划器，也不要替原规划器辩护。"
        "必须独立判断candidatePlan是否真实、是否把未知主观体验说成事实、是否用误导性简化换取儿童化。"
        "不要只检查字段是否自洽；要挑战truthKernel、epistemicStatus、requiredIdeaGroups和儿童参考答案本身。"
        "认识状态分四类：fact是可直接稳定陈述；evidence_based是科学证据支持的最佳解释，儿童答案用‘科学家认为/证据显示/主要因为’；"
        "subjective_unknown是无法直接知道的内心体验，儿童答案第一句必须原样使用‘我们不知道’或‘我们不能确定’；imaginative是明确的安全想象。"
        "询问动物、植物、灭绝生物或他者内心感受时，除非有可直接验证证据，否则epistemicStatus必须是subjective_unknown，"
        "儿童答案要先说不知道或不能确定，再说明为什么无法知道，或说明能观察到的证据；没有可观察行为时不得强行编造。想象问题必须明确区分现实与想象。"
        "审核目标是‘该年龄的最小充分真相’，不是教材完整度。只有遗漏会让直接答案变错、因果方向反转或形成明显误解时，materialOmission才为true。"
        "凝结核、浮力阈值、专业术语、完整微观机制等超出本轮年龄和概念预算的细节属于optionalAdvancedDetails，不得仅因省略它们判失败。"
        "类比和拟人化不能替代事实；不要接受‘没有心所以没有感受’等误导说法。"
        "严禁把光、水滴、水汽、云、空气、石头、影子、泡泡等说成会玩耍、跳舞、打架、生气、想要或故意做事；这些只能是故事，不能当科学机制。"
        "‘水汽抱在一起’‘水滴或云太重拿不住/抓不住’也属于误导性拟人化，必须改成‘水汽聚在一起’‘小水滴聚大、变重、落下来’。"
        "植物也不能被写成有意志地决定自然变化；‘大树为了保护自己/想保护自己/决定让叶子掉落’必须判为事实错误并要求改写。"
        "你还负责把事实计划编译成儿童表达。approvedChildAnswersByMode可返回normal、simpler、example的审核后短句；"
        "如果原参考答案事实正确但太长、太成人化或事实短语不易检测，必须提供替代答案，而不是直接失败。"
        "至少提供normal和requestedFeedback对应模式的审核后答案与原文事实短语。passed应按应用这些替代答案后的计划判断；"
        "如果替代答案能完整修复表达问题，childReferencesAccurate可为false，但passed应为true且violations应为空。"
        "canonicalIdeaGroups是normal审核后答案的最小事实原子；canonicalIdeaGroupsByMode可分别给normal、simpler、example。"
        "每组必须列出approvedChildAnswersByMode对应答案中实际出现的准确原文短语。canonicalCausalChainByMode按答案出现顺序列出因果步骤。"
        "4-5岁通常最多3个事实组；但若删除中间一步会形成错误直觉，可保留最多4个有序必要因果步骤。"
        "6-7岁遇到多步因果问题最多可保留4步；不得从起因直接跳到结果。simpler可以少于normal，但只能省略不会让直接答案变错的上游细节，"
        "必须保留对孩子当前问题最直接的原因和结果。不得为了过门禁删除关键事实。"
        "只输出JSON：passed,factuallyGrounded,epistemicAppropriate,childReferencesAccurate,minimalTruthSufficient,"
        "materialOmission,canonicalIdeaGroups,canonicalIdeaGroupsByMode,canonicalCausalChainByMode,approvedChildAnswersByMode,"
        "optionalAdvancedDetails,violations,repairInstructions。"
        "violations只写会阻止交付的关键错误；可选进阶内容只能写入optionalAdvancedDetails。不要输出思维过程。"
    )
    try:
        review = call_model_json("quality_reviewer", system, {
            "question": question,
            "candidatePlan": plan,
            "childAge": profile.get("age", 5),
            "requestedFeedback": required_feedback,
            "reviewRule": "先独立核对最小充分真相与认识状态，再按儿童预算检查参考答案；不得依据candidatePlan自证。",
            "factScaffold": plan.get("factScaffold", {}),
        }, 0.0)
    except Exception as exc:
        print(f"[alignment] plan semantic reviewer unavailable: {type(exc).__name__}")
        review = None
    if not isinstance(review, dict):
        return {
            "passed": False,
            "factuallyGrounded": False,
            "epistemicAppropriate": False,
            "childReferencesAccurate": False,
            "minimalTruthSufficient": False,
            "materialOmission": True,
            "canonicalIdeaGroups": [],
            "canonicalIdeaGroupsByMode": {},
            "canonicalCausalChainByMode": {},
            "approvedChildAnswersByMode": {},
            "referenceRepairProvided": False,
            "optionalAdvancedDetails": [],
            "violations": ["独立语义计划审核不可用"],
            "repairInstructions": ["不要向孩子交付未经独立事实审核的通用问题计划"],
            "reviewSource": "parent_analysis_unavailable",
        }
    factually_grounded = bool(review.get("factuallyGrounded"))
    epistemic_appropriate = bool(review.get("epistemicAppropriate"))
    child_references_accurate = bool(review.get("childReferencesAccurate"))
    minimal_truth_sufficient = bool(review.get("minimalTruthSufficient", factually_grounded))
    material_omission = bool(review.get("materialOmission", False))
    canonical_groups = []
    for group in review.get("canonicalIdeaGroups", []):
        terms = normalize_list(group)[:6] if isinstance(group, (list, str)) else []
        if terms:
            canonical_groups.append(terms)
    canonical_groups = canonical_groups[:4]
    raw_by_mode = review.get("canonicalIdeaGroupsByMode") if isinstance(review.get("canonicalIdeaGroupsByMode"), dict) else {}
    canonical_by_mode = {
        mode: normalize_idea_groups(raw_by_mode.get(mode))
        for mode in ("normal", "simpler", "example")
        if normalize_idea_groups(raw_by_mode.get(mode))
    }
    canonical_causal_by_mode = normalize_mode_groups(review.get("canonicalCausalChainByMode"))
    raw_approved_answers = review.get("approvedChildAnswersByMode") if isinstance(review.get("approvedChildAnswersByMode"), dict) else {}
    approved_answers = {}
    compiled_example_markers: List[str] = []
    for mode in PLAN_MODES:
        approved = normalize_optional_text(raw_approved_answers.get(mode), 240)
        if not approved:
            continue
        feedback = "simpler" if mode == "simpler" else "example" if mode == "example" else "normal"
        contract = child_level_alignment(profile, feedback=feedback)
        protected_markers = select_minimal_example_markers(
            approved,
            normalize_list(plan.get("validatedExampleMarkers")),
        ) if mode == "example" else []
        approved_answers[mode] = compile_child_reference_to_contract(
            approved,
            contract,
            protected_markers,
        )
        if mode == "example":
            compiled_example_markers = [
                marker for marker in protected_markers
                if marker in approved_answers[mode]
            ]
    requested_mode = "simpler" if required_feedback in {"confused", "simpler"} else "example" if required_feedback == "example" else "normal"
    required_modes = {"normal", requested_mode}
    epistemic_status = normalize_epistemic_status(plan.get("epistemicStatus"), question)
    approved_epistemic_failures = [
        mode for mode, answer in approved_answers.items()
        if mode in required_modes and not epistemic_marker_present(answer, epistemic_status)
    ] if epistemic_status != "fact" else []
    approved_contract_failures = []
    for mode, answer in approved_answers.items():
        if mode not in required_modes:
            continue
        feedback = "simpler" if mode == "simpler" else "example" if mode == "example" else "normal"
        contract = child_level_alignment(profile, feedback=feedback)
        sentences = split_sentences(answer)
        if (
            len(answer) > int(contract.get("maxAnswerChars", 96) or 96)
            or len(sentences) > int(contract.get("maxSentences", 3) or 3)
            or any(len(sentence) > int(contract.get("maxSentenceChars", 18) or 18) for sentence in sentences)
        ):
            approved_contract_failures.append(mode)
    reference_repair_provided = bool(
        approved_answers
        and canonical_by_mode
        and required_modes.issubset(approved_answers)
        and required_modes.issubset(canonical_by_mode)
        and not approved_epistemic_failures
        and not approved_contract_failures
    )
    optional_details = normalize_review_list(review.get("optionalAdvancedDetails"))[:8]
    violations = normalize_review_list(review.get("violations"))[:8]
    if approved_epistemic_failures:
        violations.append(
            "审核后儿童答案缺少明确认识边界：" + "、".join(approved_epistemic_failures)
        )
    if approved_contract_failures:
        violations.append(
            "审核后儿童答案仍超过年龄表达预算：" + "、".join(approved_contract_failures)
        )
    instructions = normalize_review_list(review.get("repairInstructions"))[:8]
    references_acceptable = child_references_accurate or reference_repair_provided
    passed = bool(review.get("passed")) and factually_grounded and epistemic_appropriate and references_acceptable and minimal_truth_sufficient and not material_omission and not violations
    return {
        "passed": passed,
        "factuallyGrounded": factually_grounded,
        "epistemicAppropriate": epistemic_appropriate,
        "childReferencesAccurate": child_references_accurate,
        "minimalTruthSufficient": minimal_truth_sufficient,
        "materialOmission": material_omission,
        "canonicalIdeaGroups": canonical_groups,
        "canonicalIdeaGroupsByMode": canonical_by_mode,
        "canonicalCausalChainByMode": canonical_causal_by_mode,
        "approvedChildAnswersByMode": approved_answers,
        "compiledExampleMarkers": compiled_example_markers,
        "referenceRepairProvided": reference_repair_provided,
        "optionalAdvancedDetails": optional_details,
        "violations": violations,
        "repairInstructions": instructions,
        "reviewSource": "parent_analysis_independent",
    }


def apply_semantic_idea_groups(plan: Dict[str, Any], semantic_review: Dict[str, Any]) -> Dict[str, Any]:
    canonical_groups = semantic_review.get("canonicalIdeaGroups")
    canonical_by_mode = semantic_review.get("canonicalIdeaGroupsByMode") if isinstance(semantic_review.get("canonicalIdeaGroupsByMode"), dict) else {}
    canonical_causal_by_mode = semantic_review.get("canonicalCausalChainByMode") if isinstance(semantic_review.get("canonicalCausalChainByMode"), dict) else {}
    approved_answers = semantic_review.get("approvedChildAnswersByMode") if isinstance(semantic_review.get("approvedChildAnswersByMode"), dict) else {}
    compiled_example_markers = normalize_list(semantic_review.get("compiledExampleMarkers"))
    if (not isinstance(canonical_groups, list) or not canonical_groups) and not canonical_by_mode and not approved_answers:
        return plan
    aligned = dict(plan)
    if isinstance(canonical_groups, list) and canonical_groups:
        aligned["requiredIdeaGroups"] = canonical_groups
    if canonical_by_mode:
        aligned["requiredIdeaGroupsByMode"] = canonical_by_mode
        if canonical_by_mode.get("normal"):
            aligned["requiredIdeaGroups"] = canonical_by_mode["normal"]
    if canonical_causal_by_mode:
        aligned["causalChainByMode"] = canonical_causal_by_mode
    answer_keys = {"normal": "childSafeAnswerNormal", "simpler": "childSafeAnswerSimpler", "example": "childSafeAnswerExample"}
    for mode, key in answer_keys.items():
        approved = normalize_optional_text(approved_answers.get(mode), 240)
        if approved:
            aligned[key] = approved
    if compiled_example_markers:
        aligned["validatedExampleMarkers"] = compiled_example_markers
    if approved_answers:
        compiled_by_mode = {
            mode: approved_answer_anchors(answer)
            for mode, answer in approved_answers.items()
            if approved_answer_anchors(answer)
        }
        if compiled_by_mode:
            aligned["requiredIdeaGroupsByMode"] = compiled_by_mode
            if compiled_by_mode.get("normal"):
                aligned["requiredIdeaGroups"] = compiled_by_mode["normal"]
            if canonical_causal_by_mode:
                aligned["causalChainByMode"] = {
                    mode: groups
                    for mode, groups in compiled_by_mode.items()
                    if mode in canonical_causal_by_mode
                }
    if normalize_epistemic_status(aligned.get("epistemicStatus")) in {"subjective_unknown", "imaginative"}:
        aligned["causalChainByMode"] = {}
    aligned["ideaGroupSource"] = "independent_semantic_review"
    aligned["childExpressionSource"] = "independent_semantic_review_compiled" if approved_answers else aligned.get("childExpressionSource", "planner")
    return aligned


def plan_question_with_model(question: str, heuristic: Dict[str, Any], profile: Optional[Dict[str, Any]] = None, required_feedback: str = "normal") -> Dict[str, Any]:
    planning_profile = profile or seed_db()["profiles"][DEFAULT_CHILD_ID]
    if heuristic.get("planStatus") == "ready":
        result = fit_plan_metadata_to_contract(heuristic, planning_profile, required_feedback)
        validation = validate_question_plan(
            result,
            planning_profile,
            required_feedback,
            question,
            require_reference_answers=False,
        )
        semantic_review = trusted_plan_semantic_review("curated_fact_boundary_without_answer")
        result["planValidation"] = validation
        result["planStructuralValidation"] = validation
        result["planSemanticReview"] = semantic_review
        result["planRepairCount"] = 0
        result["planStatus"] = "ready" if validation["valid"] and semantic_review["passed"] else "invalid"
        return result
    system = (
        "你是儿童问答的事实规划器兼首轮事实自检器，不直接对孩子说话。为4-7岁回答建立最小事实核心。"
        "你必须在一次输出前自行核对事实、认识边界、年龄语言和拟人化风险；不确定时宁可明确不知道，不得编造。"
        "只输出JSON：questionType,childIntent,truthKernel,epistemicStatus,requiredIdeaGroups,requiredIdeaGroupsByMode,causalChainByMode,avoidClaims,"
        "analogyPolicy,validatedAnalogy,validatedAnalogyMarkers,validatedExample,validatedExampleMarkers,validatedExampleType,"
        "childSafeAnswerNormal,childSafeAnswerSimpler,childSafeAnswerExample,childSafeTeachbackCorrect,"
        "childSafeTeachbackIncorrect,teachbackCorrectionMarkers,conceptLabels。"
        "requiredIdeaGroups是普通版不可丢失的事实原子：4-5岁通常最多3组，6-7岁最多4组；"
        "若为什么问题省略中间一步会形成错误直觉，4-5岁可保留最多4个按序因果步骤。"
        "requiredIdeaGroupsByMode可为normal、simpler、example分别设置事实原子。"
        "causalChainByMode用于为什么类问题，按儿童答案中的出现顺序列出因果步骤，并与对应事实原子使用相同短语。"
        "不能只保留起因和最终结果；若省略中间一步会让孩子形成错误直觉，就必须保留该桥梁。"
        "普通版保留完整的年龄适配最小真相；simpler只保留回答当前问题最直接的原因和结果，可省略不影响正确性的上游过程。"
        "每个模式的儿童参考答案必须原样包含该模式每组至少一个短语，不能让比喻代替事实。"
        "epistemicStatus只能是fact、evidence_based、subjective_unknown、imaginative。"
        "有证据支持但仍是最佳解释的科学结论用evidence_based，并写‘科学家认为/证据显示/主要因为’，不能强迫回答说不知道。"
        "询问任何生物是否想念、孤单、害怕、开心或觉得怎样时，不能直接知道其主观体验，必须标记subjective_unknown；"
        "对应儿童答案第一句固定使用‘我们不知道……’或‘我们不能确定……’，再说明为什么无法知道，或写可以观察到的行为或证据；没有证据时不得编造。"
        "subjective_unknown不是因果科学解释，causalChainByMode必须为空。想象问题使用imaginative。不要输出思维过程。"
        "analogyPolicy默认必须是omit。只有你能给出不歪曲事实的具体类比时才可为allow_vetted，并填写唯一允许的validatedAnalogy及标记。"
        "validatedExample必须标记validatedExampleType：observation表示真实可观察例子，analogy表示已审核类比，imaginative只用于明确想象。"
        "举例版至少原样包含一个最短且可辨识的检测标记；检测标记用于确认例子来源，不要求把所有冗长标记都塞进答案。"
        "observation不是类比，不要写‘好像某个东西’来冒充观察。"
        "childSafeAnswer必须用4-7岁短句覆盖本模式事实原子；普通版不得擅自举例，举例版只能使用validatedExample。"
        "严禁把光、水滴、水汽、云、空气、石头、影子、泡泡说成会玩耍、跳舞、打架、生气、想要、喜欢或故意做事。"
        "植物变化也不能写成有意志的决定：禁止用‘大树为了保护自己/想保护自己/决定让叶子掉落’解释落叶；"
        "应描述天气、水分、叶柄与树枝连接处发生的变化，不能把功能结果写成树木主动计划。"
        "禁止写‘水汽抱在一起’或‘水滴/云太重拿不住、抓不住’；下雨应写‘水汽聚在一起变成小水滴，小水滴聚大变重后落下来’。"
        "儿童化只能换词和拆句，不能把物理过程改写成有意志的角色行为。"
        "正确复述版必须明确说孩子这次说对了；错误复述版必须用不是或没有明确否定误解并覆盖全部事实原子。"
        "teachbackCorrectionMarkers提供错误复述版中确实出现的否定短语。4-5岁conceptLabels只给1个，6-7岁最多2个。"
        "若输入提供factScaffold，它是经过人工策划的事实边界：不得与其冲突或删掉其中关键因果桥梁，但仍需按年龄重写。"
        "只需优先保证requestedFeedback当前会用到的参考答案，但普通版始终必须合格。"
    )
    try:
        planned = call_model_json("question_planner", system, {
            "question": question,
            "heuristic": heuristic,
            "childProfile": {"age": planning_profile.get("age", 5)},
            "planningContract": child_level_alignment(planning_profile),
            "requestedFeedback": required_feedback,
            "factScaffold": heuristic.get("factScaffold", {}),
        }, 0.1)
    except Exception as exc:
        print(f"[alignment] planner unavailable: {type(exc).__name__}")
        result = dict(heuristic)
        result.update({
            "planSource": "planner_unavailable",
            "planStatus": "invalid",
            "planValidation": {"valid": False, "violations": ["规划模型不可用，禁止使用本地预写答案兜底"], "referenceChecks": {}},
            "planSemanticReview": {"passed": False, "violations": ["规划不可用，未交付问题答案"], "reviewSource": "not_run"},
            "planRepairCount": 0,
        })
        return result
    if not isinstance(planned, dict):
        result = dict(heuristic)
        result.update({
            "planSource": "planner_unavailable",
            "planStatus": "invalid",
            "planValidation": {"valid": False, "violations": ["规划模型没有返回可用 JSON"], "referenceChecks": {}},
            "planSemanticReview": {"passed": False, "violations": ["规划不存在，未执行语义审核"], "reviewSource": "not_run"},
            "planRepairCount": 0,
        })
        return result
    result = fit_plan_metadata_to_contract(
        normalize_question_plan(planned, heuristic, question),
        planning_profile,
        required_feedback,
    )
    result = compile_single_pass_plan_references(result, planning_profile, required_feedback)
    result = apply_curated_fact_scaffold(result)
    result["planSource"] = "parent_analysis_compiled"
    structural_validation = validate_question_plan(
        result,
        planning_profile,
        required_feedback,
        question,
        strict_atomic_alignment=False,
        validate_reference_quality=False,
    )
    semantic_review = compiled_plan_semantic_review(structural_validation)
    validation = validate_question_plan(result, planning_profile, required_feedback, question)
    initial_violations = list(validation["violations"]) + list(semantic_review.get("violations", []))
    repair_count = 0
    repair_system = system + (
        "这是规划修复轮。必须逐项修复planFailures和semanticFailures，不得删除、弱化或改写掉真实事实。"
        "如果当前模式事实原子未命中儿童答案，只能二选一：重写对应儿童答案以原样包含每组一个准确短语；"
        "或把答案中已经出现的准确近义短语加入对应组。不得为了过门禁删除事实原子。"
        "如果simpler因长度预算失败，重建requiredIdeaGroupsByMode.simpler，只保留直接回答所必需的原因和结果；"
        "不要强塞完整上游过程，也不要删除会让直接答案变错的关键环节。"
        "若独立审核指出认识状态错误，必须修正epistemicStatus、truthKernel、事实原子和所有当前会用到的儿童参考答案。"
        "若出现光在玩耍、跳舞等拟人机制，必须改成反射、散开、来回反射或不同颜色变亮等可验证表述。"
        "若出现‘水汽抱在一起’或‘水滴/云拿不住、抓不住’，必须改成‘水汽聚在一起’和‘小水滴聚大变重后落下来’。"
        "若出现‘大树为了保护自己/想保护自己/决定让叶子掉落’，必须删除目的和意志表达，改写为可观察的天气、水分与连接变化。"
        "为什么类问题必须同步修正causalChainByMode；6-7岁允许4步，不能用更短但断裂的因果链换取过门禁。"
        "当前requestedFeedback对应答案必须符合年龄预算。允许只返回需要修正的字段。"
    )
    while repair_count < 1 and not (validation["valid"] and semantic_review.get("passed")) and (remaining_interaction_seconds() or 0) >= 5.0:
        repair_count += 1
        try:
            repaired = call_model_json("question_planner", repair_system, {
                "question": question,
                "candidatePlan": result,
                "planFailures": validation["violations"],
                "referenceChecks": validation["referenceChecks"],
                "semanticFailures": semantic_review.get("violations", []),
                "semanticRepairInstructions": semantic_review.get("repairInstructions", []),
                "requestedFeedback": required_feedback,
            }, 0.0)
        except Exception as exc:
            print(f"[alignment] planner repair unavailable: {type(exc).__name__}")
            repaired = None
        if isinstance(repaired, dict):
            result = fit_plan_metadata_to_contract(
                normalize_question_plan(repaired, result, question),
                planning_profile,
                required_feedback,
            )
            result = compile_single_pass_plan_references(result, planning_profile, required_feedback)
            result = apply_curated_fact_scaffold(result)
            result["planSource"] = "parent_analysis_compiled_repaired"
            structural_validation = validate_question_plan(
                result,
                planning_profile,
                required_feedback,
                question,
                strict_atomic_alignment=False,
                validate_reference_quality=False,
            )
            semantic_review = compiled_plan_semantic_review(structural_validation)
            validation = validate_question_plan(result, planning_profile, required_feedback, question)
        else:
            break
    result["planRepairAttempted"] = repair_count > 0
    result["planRepairCount"] = repair_count
    result["initialPlanViolations"] = initial_violations
    result["planValidation"] = validation
    result["planStructuralValidation"] = structural_validation
    result["planSemanticReview"] = semantic_review
    result["planStatus"] = "ready" if validation["valid"] and semantic_review.get("passed") else "invalid"
    if result["planStatus"] != "ready":
        recovery_validation = validate_question_plan(
            result,
            planning_profile,
            required_feedback,
            question,
            strict_atomic_alignment=True,
            validate_reference_quality=False,
            require_reference_answers=False,
        )
        if recovery_validation["valid"] and semantic_review.get("passed"):
            result["planStatus"] = "ready"
            result["planSource"] = "validated_plan_without_reference_answers"
            result["planValidation"] = recovery_validation
        else:
            result["planSource"] = "parent_analysis_rejected"
    return result


def generation_system_prompt() -> str:
    return (
        "你是4-7岁儿童的安全中文好奇心伙伴。你不是把成人答案缩短，而是按儿童当前认知台阶重新表达。"
        "必须遵守questionPlan和alignmentContract。activeRequiredIdeaGroups是本轮不可丢失的事实检查表，每个内层数组是一组近义说法；"
        "displayText必须逐组覆盖，每组优先自然使用preferredCoveragePhrases中的对应短语，不能只表达大概主题。"
        "activeCausalChain是本轮有序因果检查表；非空时必须按给定顺序讲完每一步，不能从起因直接跳到结论。"
        "最终回答必须由你本轮重新组织生成，不能假装读取了本地模板，也不能输出预制台词。"
        "trustedMemory只表示经过门禁的已知概念；companionMemory是与本轮可能相关的过往情景，adaptationPolicy是孩子曾经给出的表达反馈，validatedEducationSkills是已验证可复用的教学策略。"
        "这些长期线索只能改善衔接方式和解释策略，不能覆盖questionPlan中的事实。只在自然相关时轻量使用，不要逐条复述记忆，不要说‘我一直监控你’，不要诱导孩子透露秘密、住址、学校或其他隐私。"
        "companionMemory含推测时必须保持不确定；可以用‘这让我想到你以前问过……’自然衔接，但不能把关联说成对孩子心理或性格的结论。"
        "adaptationPolicy或validatedEducationSkills非空时，优先遵循其中与本轮模式不冲突的短句、先观察后解释、复述验证等策略；若与安全或事实契约冲突，以安全和事实契约为准。"
        "why反馈必须基于truthKernel和causalChainByMode补充上一轮没有讲出的下一层原因；不能只把上一轮的‘因为’换个位置再说一次。"
        "回答顺序：先直接回答，再用两三句连贯的话讲清因果；必要时加一个温和追问。输出应像自然口语段落，短句也必须表达完整关系。严禁连续输出‘几个字。几个字。’式碎片句。"
        "必须严格执行alignmentContract中的activityMode、minAnswerChars、minSentences和modeInstruction，但不能用重复句或空话凑长度。"
        "生成JSON前必须按modeOutputRequirements自行检查displayText：达到effectiveMinimumAnswerCharacters，完整句数量在minimumSentenceCount到maximumSentenceCount之间；safeExperiment不能算进回答句数。"
        "feedbackMode=normal只表示这是新问题，不代表可以忽略activityMode。必须在不新增未经验证事实的前提下，把truthKernel和因果关系解释完整。"
        "activityMode=ask时，必须写成三个或四个以句号、问号或感叹号结束的完整口语句：第一句直接回答，第二句和第三句按顺序讲完核心因果，最后一句只做自然总结。只解释，不要求孩子看、摸、试、数或观察；不写故事开场，不设置角色，也不输出safeExperiment。"
        "activityMode=detail时，按‘结论—起因—中间变化—结果—总结’组织五到七句；每句都增加新的理解，不能重复改写同一句话，不写故事角色，也不输出safeExperiment。"
        "activityMode=story时，开头必须明确进入故事，用六到七句真正讲述角色在具体场景中经历的连续事件。优先从角色第一人称开始，例如第一句使用‘我是小雨滴……’这种结构。第一句和第二句必须是纯情节：明确写出湖面、水面、云朵里、天空、树叶、窗台等具体地点，并让角色遇见伙伴、打招呼、说话或做一件与科学机制无关的小事；这两句禁止出现‘因为、所以、变成、冷空气、聚大、变重、真正原因、这就是’。"
        "故事中段再让角色经历与问题有关的变化。至少一半句子必须属于角色看见、来到、遇到、说话、感受或旅行的情节，而不是换着句式复述原理。前半段至少有两句不承担科学因果解释的纯情节，不能让每一句都逐项复述activeCausalChain。"
        "故事角色可以看见、来到、遇到、旅行、说话和感受，但角色的想法不能成为自然现象发生的原因。倒数第二句必须以‘故事里的真正原因是’开头，并自然覆盖activeCausalChain前面的步骤；最后一句覆盖activeCausalChain的结果并用‘这就是……’收束。结尾两句必须使用preferredCoveragePhrases对应的事实短语，不能只说‘原来是这样’；safeExperiment必须留空。"
        "例如可以写小雨滴在云里的旅行，但不能写‘小雨滴因为想回家所以落下’；应在结尾说明小水滴聚大变重后落下来。也不能写‘大树觉得冷、想保暖、为了保护自己才让叶子掉落’。"
        "任何模式都禁止把‘大树为了保护自己、想保护自己、决定让叶子掉落’当作科学原因；植物变化要写可观察的条件与连接变化。"
        "activityMode=observe时，displayText只用三到五句给出简短真实解释和观察邀请，不讲故事。主要内容写入safeExperiment，并严格使用六个可见标题：‘家长陪同：’‘准备：’‘一起做：’‘重点观察：’‘家长可以问：’‘安全提醒：’。每个标题后都要有具体内容，尤其‘重点观察’必须写清孩子要看见的现象或变化。safeExperiment必须是一个连贯的自然中文字符串，不能返回JSON对象。不能只写‘一起看看’。"
        "previousAnswer非空时，说明孩子对上一轮有反馈。必须直接回应feedbackInstruction，并避免重复上一轮相同句式和段落结构。"
        "analogyPolicy=omit时，禁止使用类比、拟人化解释或childContext中的熟悉物。"
        "严禁用‘光在玩耍/跳舞/打架’‘水滴想要落下’‘云生气’等角色动作解释科学机制。"
        "也禁止‘水汽抱在一起’和‘水滴/云太重拿不住、抓不住’；必须使用‘聚在一起’‘聚大变重’‘落下来’等物理描述。"
        "只有analogyPolicy=allow_vetted且反馈要求举例时，才可原样使用validatedAnalogy，禁止另造类比。"
        "反馈要求举例时，只能使用validatedExample；若为空，保留事实解释并给可观察的事实例子，不得现编科学类比。"
        "feedbackInstruction要求举例且validatedExample非空时，只能围绕该例子重新组织解释，并保留validatedExampleMarkers中的至少一个原词。"
        "feedback不是example时，禁止使用validatedExample，但仍要用完整自然的话讲清当前反馈要求。"
        "事实核心不能被比喻替代。epistemicStatus=evidence_based时要用‘科学家认为/证据显示/主要因为’表达证据边界，不要说成完全确定，也不要无端说不知道。"
        "epistemicStatus=subjective_unknown时第一句必须原样使用‘我们不知道’或‘我们不能确定’，再说明为什么无法知道，或说能观察到什么；没有证据时不得编造。"
        "epistemicStatus=imaginative时，第一句必须明确这是想象或不是真的；再给一句贴合孩子问题的安全想象，不要改讲无关故事。"
        "如果childStatement非空，孩子正在复述：先判断复述中正确的一点，再用一句话纠正或补充；不要假装孩子已经掌握，也不要重新长篇回答原问题。"
        "teachbackAssessment.correct为true时，可以肯定这次复述说对了，但不能说已经完全学会。"
        "teachbackAssessment.outcome=contradicted时，必须温柔指出具体错误，并在displayText中给出truthKernel的儿童短句版本。"
        "teachbackAssessment.outcome为insufficient或unverified时，不得说孩子正确或错误；要说‘还不能确定’，邀请孩子再说一次或一起看。"
        "错误复述时必须依据truthKernel重新生成纠正说明，并明确说出‘不是/没有’，不能只重复正确事实。"
        "不要使用成人术语，不要夸奖孩子已经学会，不要自行判断孩子掌握程度。短不等于碎：可以略微超过句长预算，也不要破坏完整的因果关系。"
        "危险、隐私、伤害、自伤、色情、药物、陌生人联系必须needsParent=true且不提供步骤。"
        "只输出JSON，不输出思维过程。字段必须包含：displayText,speakText,followUp,safeExperiment,avatarState,"
        "introducedConcepts,knowledgeCardUpdates,safetyAction,needsParent,answerType,truthKernel,epistemicStatus,analogy。"
        "knowledgeCardUpdates固定输出空数组；可选空字段输出空字符串，禁止输出字符串None或null。"
    )


def answer_generation_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    allowed_fields = (
        "questionType", "childIntent", "truthKernel", "epistemicStatus", "requiredIdeaGroups",
        "requiredIdeaGroupsByMode", "causalChainByMode", "avoidClaims", "analogyPolicy",
        "validatedAnalogy", "validatedAnalogyMarkers", "validatedExample", "validatedExampleMarkers",
        "validatedExampleType", "teachbackCorrectionMarkers", "conceptLabels", "activityMode",
    )
    return {key: plan.get(key) for key in allowed_fields if key in plan}


def review_answer_with_model(ai: Dict[str, Any], plan: Dict[str, Any], contract: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    system = (
        "你是独立的儿童回答质量评审器。只评审，不替生成器辩护。"
        "检查事实核心、事实与猜测区分、年龄语言、认知负荷、比喻是否替代事实、安全和结构。"
        "如果光、水滴、水汽、云、空气、石头、影子或泡泡被写成会玩耍、跳舞、打架、生气、想要或故意行动，factualCore必须低于0.7。"
        "‘水汽抱在一起’‘水滴或云太重拿不住/抓不住’同样是用角色动作代替机制，factualCore必须低于0.7；‘水汽聚在一起’是中性物理表达，不应因此扣分。"
        "类比是可选项：不得因为回答没有类比而扣分；只有回答实际使用类比时，才检查它是否贴切。"
        "只输出JSON：passed,scores,violations,repairInstructions。scores每项0到1，包含"
        "factualCore,epistemicClarity,ageLanguage,cognitiveLoad,analogyBounded,safety,schema。不要输出思维过程。"
    )
    try:
        review = call_model_json("quality_reviewer", system, {"candidate": ai, "questionPlan": plan, "alignmentContract": contract}, 0.0)
    except Exception as exc:
        print(f"[alignment] reviewer unavailable: {type(exc).__name__}")
        return None
    if not isinstance(review, dict) or not isinstance(review.get("scores"), dict):
        return None
    scores = {key: round(clamp_float(review["scores"].get(key), 0.5, 0, 1), 2) for key in ["factualCore", "epistemicClarity", "ageLanguage", "cognitiveLoad", "analogyBounded", "safety", "schema"]}
    violations = normalize_review_list(review.get("violations"))[:8]
    instructions = normalize_review_list(review.get("repairInstructions"))[:8]
    passed = bool(review.get("passed")) and scores["factualCore"] >= 0.7 and scores["epistemicClarity"] >= 0.7 and scores["ageLanguage"] >= 0.7 and scores["safety"] == 1
    return {"passed": passed, "scores": scores, "violations": violations, "repairInstructions": instructions, "reviewSource": "parent_analysis"}


def answer_requires_model_review(ai: Dict[str, Any], plan: Dict[str, Any]) -> bool:
    epistemic_status = normalize_epistemic_status(plan.get("epistemicStatus"))
    teachback = plan.get("teachbackAssessment")
    return bool(
        epistemic_status in {"subjective_unknown", "imaginative"}
        or isinstance(teachback, dict)
        or answer_uses_analogy(ai, plan)
    )


def answer_requires_fail_closed_model_review(plan: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(plan, dict):
        return True
    epistemic_status = normalize_epistemic_status(plan.get("epistemicStatus"))
    return bool(
        epistemic_status in {"subjective_unknown", "imaginative"}
        or isinstance(plan.get("teachbackAssessment"), dict)
    )


def quality_has_only_soft_failures(review: Dict[str, Any]) -> bool:
    scores = review.get("scores") if isinstance(review.get("scores"), dict) else {}
    hard_score_keys = ("planGrounding", "factualCore", "epistemicClarity", "teachbackFit", "safety", "schema")
    if any(float(scores.get(key, 0)) < 1 for key in hard_score_keys):
        return False
    atomic_coverage = review.get("atomicCoverage") if isinstance(review.get("atomicCoverage"), dict) else {}
    causal_coverage = review.get("causalCoverage") if isinstance(review.get("causalCoverage"), dict) else {}
    if atomic_coverage and not atomic_coverage.get("allPresent", False):
        return False
    if causal_coverage and not causal_coverage.get("ordered", False):
        return False
    hard_markers = (
        "事实原子", "应避免的断言", "拟人动作", "因果链", "证据措辞", "主观感受",
        "想象与现实", "复述反馈", "儿童安全边界", "完整JSON", "问题计划",
    )
    return not any(
        marker in violation
        for violation in normalize_review_list(review.get("violations"))
        for marker in hard_markers
    )


def accept_soft_quality_warnings(review: Dict[str, Any]) -> Dict[str, Any]:
    warnings = normalize_review_list(review.get("violations"))[:8]
    return {
        **review,
        "passed": True,
        "softPassed": True,
        "warnings": warnings,
        "violations": [],
        "repairInstructions": [],
        "reviewSource": "local_soft_pass",
    }


def merge_quality_reviews(
    local_review: Dict[str, Any],
    model_review: Optional[Dict[str, Any]],
    ai: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
    require_model_review: bool = False,
) -> Dict[str, Any]:
    if not model_review:
        if require_model_review:
            if not answer_requires_fail_closed_model_review(plan) and local_review.get("passed"):
                return {
                    **local_review,
                    "softPassed": True,
                    "warnings": ["独立类比审核暂时不可用，已依据本地事实与安全门禁交付"],
                    "reviewSource": "local_soft_pass",
                }
            return {
                **local_review,
                "passed": False,
                "violations": list(dict.fromkeys(local_review.get("violations", []) + ["独立回答审核不可用，禁止作为已对齐答案交付"])),
                "repairInstructions": ["等待独立回答审核可用后重新生成或审核"],
                "reviewSource": "local+parent_analysis_unavailable",
            }
        return local_review
    scores = dict(local_review["scores"])
    violations = list(dict.fromkeys(local_review.get("violations", []) + model_review.get("violations", [])))[:10]
    overall = round(sum(scores.values()) / len(scores), 2)
    model_scores = model_review.get("scores", {})
    epistemic_required = (plan or {}).get("epistemicStatus") in {"evidence_based", "subjective_unknown"}
    incorrect_teachback = isinstance((plan or {}).get("teachbackAssessment"), dict) and not (plan or {})["teachbackAssessment"].get("correct")
    analogy_used = answer_uses_analogy(ai or {}, plan)
    critical_model_failure = (
        float(model_scores.get("safety", 1)) < 1
        or float(model_scores.get("factualCore", 1)) < 0.7
        or (epistemic_required and float(model_scores.get("epistemicClarity", 1)) < 0.7)
        or (incorrect_teachback and (float(model_scores.get("epistemicClarity", 1)) < 0.7 or float(model_scores.get("schema", 1)) < 0.7))
        or (analogy_used and float(model_scores.get("analogyBounded", 1)) < 0.7)
    )
    passed = bool(local_review.get("passed")) and not critical_model_failure and overall >= 0.78
    return {
        **local_review,
        "passed": passed,
        "overall": overall,
        "scores": scores,
        "advisoryScores": model_scores,
        "violations": violations,
        "repairInstructions": model_review.get("repairInstructions", []),
        "reviewSource": "local+parent_analysis",
    }


def alignment_failure_fallback(plan: Dict[str, Any]) -> Dict[str, Any]:
    truth = normalize_optional_text(plan.get("truthKernel"), 120)
    text = "刚才没有成功生成新的回答。我不会拿预先写好的答案代替，请再试一次，或者请大人帮忙看看。"
    return {
        "displayText": text,
        "speakText": text,
        "followUp": "可以点“再试一次”，重新请模型回答。",
        "safeExperiment": "",
        "avatarState": "speaking",
        "introducedConcepts": [],
        "knowledgeCardUpdates": [],
        "safetyAction": "ask_parent",
        "needsParent": True,
        "answerType": plan.get("questionType", "factual"),
        "truthKernel": truth,
        "epistemicStatus": plan.get("epistemicStatus", "evidence_based"),
        "analogy": "",
    }


def log_rejected_model_candidate(stage: str, candidate: Dict[str, Any], quality: Dict[str, Any]) -> None:
    text = normalize_optional_text(candidate.get("displayText"), MAX_CHILD_ANSWER_CHARS)
    safe_experiment = normalize_optional_text(candidate.get("safeExperiment"), 240)
    violations = normalize_review_list(quality.get("violations"))[:8]
    language_evidence = quality.get("languageEvidence") if isinstance(quality.get("languageEvidence"), dict) else {}
    mode_evidence = {
        "activityMode": language_evidence.get("activityMode", ""),
        "sentenceCount": language_evidence.get("sentenceCount", 0),
        "storyOpening": bool((language_evidence.get("storyStructure") or {}).get("openingPresent")),
        "storyScene": bool((language_evidence.get("storyStructure") or {}).get("storyScenePresent")),
        "storyInteraction": bool((language_evidence.get("storyStructure") or {}).get("characterInteractionPresent")),
        "storyNonFactSentences": int((language_evidence.get("storyStructure") or {}).get("nonFactNarrativeSentenceCount", 0) or 0),
        "storyTruthReturn": bool((language_evidence.get("storyStructure") or {}).get("truthReturnPresent")),
        "observeAdult": bool((language_evidence.get("observationStructure") or {}).get("adultCompanion")),
        "observePreparation": bool((language_evidence.get("observationStructure") or {}).get("preparation")),
        "observeAction": bool((language_evidence.get("observationStructure") or {}).get("action")),
        "observePhenomenon": bool((language_evidence.get("observationStructure") or {}).get("phenomenon")),
        "observeParentQuestion": bool((language_evidence.get("observationStructure") or {}).get("parentQuestion")),
        "observeSafety": bool((language_evidence.get("observationStructure") or {}).get("safetyBoundary")),
    }
    write_runtime_event(
        "quality.reject", "agent", "failed", component="quality_reviewer",
        details={"stage": stage, "answerChars": len(text), "safeExperimentChars": len(safe_experiment), "violations": violations, "modeEvidence": mode_evidence},
    )
    print(
        "[alignment] rejected "
        + json.dumps(
            {
                "stage": stage,
                "answerChars": len(text),
                "safeExperimentChars": len(safe_experiment),
                "violations": violations,
                "modeEvidence": mode_evidence,
            },
            ensure_ascii=False,
        )
    )


def _run_child_alignment_workflow(profile: Dict[str, Any], user_text: str, feedback: str, related_cards: List[Dict[str, Any]], activity_mode: str = "ask", original_question: str = "", previous_answer: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    workflow_started = time.perf_counter()
    max_quality_attempts = quality_attempt_limit(feedback)
    stage_latency: Dict[str, float] = {}
    reasoning_question = original_question if feedback == "teachback" and original_question else user_text
    heuristic = heuristic_question_plan(reasoning_question, activity_mode)
    stage_started = time.perf_counter()
    write_runtime_event("agent.start", "agent", "started", component="learning_planner", activity_mode=activity_mode, feedback_mode=feedback)
    plan = plan_question_with_model(reasoning_question, heuristic, profile, feedback)
    stage_latency["planning"] = round(time.perf_counter() - stage_started, 3)
    write_runtime_event("agent.finish", "agent", "completed", component="learning_planner", activity_mode=activity_mode, feedback_mode=feedback, duration_ms=round(stage_latency["planning"] * 1000), details={"planStatus": plan.get("planStatus", "")})
    stage_started = time.perf_counter()
    if feedback == "teachback":
        write_runtime_event("agent.start", "agent", "started", component="teachback_reviewer", activity_mode=activity_mode, feedback_mode=feedback)
        local_teachback = teachback_evidence(user_text, plan)
        teachback_assessment = review_teachback_with_model(user_text, plan, local_teachback) if plan.get("planStatus") == "ready" else {
            **local_teachback,
            "correct": False,
            "outcome": "unverified",
            "assessmentAvailable": False,
            "source": "plan_not_ready",
        }
    else:
        teachback_assessment = None
    stage_latency["teachbackReview"] = round(time.perf_counter() - stage_started, 3)
    write_runtime_event(
        "agent.finish" if feedback == "teachback" else "agent.skipped", "agent", "completed" if feedback == "teachback" else "skipped",
        component="teachback_reviewer", activity_mode=activity_mode, feedback_mode=feedback,
        duration_ms=round(stage_latency["teachbackReview"] * 1000), details={"outcome": (teachback_assessment or {}).get("outcome", "not_requested")},
    )
    previous_answer = normalize_optional_text(previous_answer, MAX_CHILD_ANSWER_CHARS)
    review_plan = {**plan, "feedbackMode": feedback, "teachbackAssessment": teachback_assessment, "previousAnswer": previous_answer}
    active_groups = active_required_idea_groups(review_plan, feedback)
    active_chain = active_causal_chain(review_plan, feedback)
    contract = child_level_alignment(profile, feedback=feedback, related_cards=related_cards, activity_mode=activity_mode)
    model_contract = {key: value for key, value in contract.items() if key != "anchors"}
    trusted_memory = [card for card in related_cards if memory_card_is_trusted(card)]
    preference_memory = [card for card in related_cards if card.get("type") == "preference"]
    associative_memory = [card for card in related_cards if card.get("type") in {"episode", "association", "inference"}]
    education_skills = [card for card in related_cards if card.get("type") == "education_skill" and card.get("status") in {"active", "known", "validated"}]
    request_payload = {
        "childContext": {
            "age": profile.get("age", 5),
            "interests": normalize_list(profile.get("interests"))[:6],
            "explanationPreference": normalize_optional_text(profile.get("explanationPreference"), 120),
            "contextRule": "兴趣只能用于语气和追问，不能自动变成科学类比。",
        },
        "question": reasoning_question,
        "previousAnswer": previous_answer,
        "childStatement": user_text if feedback == "teachback" else "",
        "teachbackAssessment": teachback_assessment or {},
        "feedbackInstruction": feedback_instruction(feedback),
        "activityMode": activity_mode,
        "modeStrategy": activity_mode_strategy(activity_mode),
        "questionPlan": answer_generation_plan(plan),
        "activeRequiredIdeaGroups": active_groups,
        "preferredCoveragePhrases": [min(group, key=len) for group in active_groups if group],
        "activeCausalChain": active_chain,
        "modeOutputRequirements": {
            "activityMode": activity_mode,
            "minimumAnswerCharacters": contract.get("minAnswerChars", 0),
            "effectiveMinimumAnswerCharacters": int(int(contract.get("minAnswerChars", 0) or 0) * 0.9),
            "minimumSentenceCount": contract.get("minSentences", 0),
            "maximumSentenceCount": contract.get("maxSentences", 0),
            "sentenceCountingRule": "displayText中以句号、问号或感叹号结束的完整句才计数；safeExperiment不计入displayText句数。",
            "exampleAllowed": feedback == "example" or activity_mode == "story",
            "sentencePlan": (
                "三到四句：直接答案、一个核心因果链、自然收束；禁止故事开场、角色叙事和观察任务。"
                if activity_mode == "ask" else
                "五到七句：结论、起因、中间变化、结果、总结；每句增加新信息，禁止故事角色和观察任务。"
                if activity_mode == "detail" else
                "六到七句：前两句只写具体场景、角色互动和纯情节，中段写连续经历，倒数第二句以‘故事里的真正原因是’开头，最后一句用‘这就是’说明结果；safeExperiment留空。"
                if activity_mode == "story" else
                "displayText只做简短解释和邀请；safeExperiment写全家长陪同、准备、动作、现象、家长提问、安全边界。"
            ),
            "safeExperimentMustNameAdultCompanion": activity_mode == "observe",
            "safeExperimentRequiredParts": (
                ["adultCompanion", "preparation", "action", "phenomenon", "parentQuestion", "safetyBoundary"]
                if activity_mode == "observe" else []
            ),
            "safeExperimentMustBeEmpty": activity_mode in {"ask", "detail", "story"},
            "storyRequirements": (
                {"characterPerspective": True, "concreteScene": True, "characterInteraction": True, "minimumNarrativeEvents": 2, "minimumNonFactNarrativeSentences": 1, "minimumNarrativeSentenceRatio": 0.5, "truthReturnAtEnd": True}
                if activity_mode == "story" else {}
            ),
        },
        "alignmentContract": model_contract,
        "trustedMemory": [{"concept": card.get("concept"), "status": card.get("status"), "parentVerified": bool(card.get("parentVerified"))} for card in trusted_memory[:4]],
        "companionMemory": [{"concept": card.get("concept"), "evidence": normalize_optional_text(card.get("evidence"), 180), "relevanceRule": "只在与本轮问题自然相关时使用，不向孩子宣称系统掌握了隐私。"} for card in associative_memory[:2]],
        "adaptationPolicy": [{"preference": card.get("concept"), "evidence": normalize_optional_text(card.get("evidence"), 160), "strategy": normalize_optional_text(card.get("strategy") or card.get("effectiveAnalogy"), 160)} for card in preference_memory[:2]],
        "validatedEducationSkills": [{"name": card.get("concept"), "version": card.get("version", "1"), "instruction": normalize_optional_text(card.get("strategy"), 200), "validation": normalize_optional_text(card.get("validationSummary"), 120)} for card in education_skills[:2]],
    }
    answer_model_attempts = 0
    stage_started = time.perf_counter()
    generated = None
    write_runtime_event("agent.start", "agent", "started", component="child_tutor", activity_mode=activity_mode, feedback_mode=feedback)
    if plan.get("planStatus") == "ready":
        for attempt in range(2):
            answer_model_attempts += 1
            retry_payload = dict(request_payload)
            retry_prompt = generation_system_prompt()
            if attempt:
                retry_payload["schemaRetry"] = True
                retry_prompt += "上一次没有返回完整JSON。本次必须重新生成完整回答，并严格包含全部必填字段；不能引用或复用本地模板答案。"
            try:
                generated = call_model_json("child_tutor", retry_prompt, retry_payload, 0.35 if attempt == 0 else 0.15)
            except Exception as exc:
                print(f"[alignment] child model unavailable: {type(exc).__name__}")
                generated = None
            if isinstance(generated, dict) and all(key in generated for key in REQUIRED_AI_FIELDS):
                break
    stage_latency["answerPreparation"] = round(time.perf_counter() - stage_started, 3)
    answer_source = "real"
    if plan.get("planStatus") != "ready":
        generated = alignment_failure_fallback(plan)
        answer_source = "quality_fallback"
    elif not isinstance(generated, dict) or not all(key in generated for key in REQUIRED_AI_FIELDS):
        generated = alignment_failure_fallback(plan)
        answer_source = "model_schema_failure"
    write_runtime_event(
        "agent.finish" if answer_source == "real" else "agent.error", "agent", "completed" if answer_source == "real" else "failed",
        component="child_tutor", activity_mode=activity_mode, feedback_mode=feedback,
        duration_ms=round(stage_latency["answerPreparation"] * 1000), error_type="" if answer_source == "real" else answer_source,
        details={"attempts": answer_model_attempts, "answerSource": answer_source},
    )
    candidate = normalize_ai_response(generated, review_plan)
    candidate["answerType"] = candidate.get("answerType") or plan.get("questionType", "factual")
    candidate["truthKernel"] = plan.get("truthKernel", "")
    candidate["epistemicStatus"] = plan.get("epistemicStatus", "fact")
    stage_started = time.perf_counter()
    write_runtime_event("agent.start", "agent", "started", component="quality_reviewer", activity_mode=activity_mode, feedback_mode=feedback, details={"stage": "initial"})
    local_review = local_quality_review(candidate, review_plan, contract)
    require_model_review = answer_source == "real" and answer_requires_model_review(candidate, review_plan)
    model_review = review_answer_with_model(candidate, review_plan, contract) if require_model_review and local_review["passed"] else None
    quality = merge_quality_reviews(local_review, model_review, candidate, review_plan, require_model_review=require_model_review)
    stage_latency["initialAnswerReview"] = round(time.perf_counter() - stage_started, 3)
    write_runtime_event(
        "quality.accept" if quality.get("passed") else "quality.reject", "agent", "completed" if quality.get("passed") else "failed",
        component="quality_reviewer", activity_mode=activity_mode, feedback_mode=feedback,
        duration_ms=round(stage_latency["initialAnswerReview"] * 1000),
        details={"stage": "initial", "localPassed": bool(local_review.get("passed")), "modelReviewRequired": require_model_review, "reviewSource": quality.get("reviewSource", ""), "violations": quality.get("violations", [])[:8]},
    )
    initial_source = answer_source
    initial_quality = {
        "passed": bool(quality.get("passed")),
        "overall": quality.get("overall", 0),
        "scores": quality.get("scores", {}),
        "violations": quality.get("violations", []),
    }
    if answer_source == "real" and not quality["passed"]:
        log_rejected_model_candidate("initial", candidate, quality)
    repair_count = 0
    quality_attempt_count = 1 if answer_source == "real" else 0
    quality_attempt_history = [{
        "attempt": quality_attempt_count,
        "passed": bool(quality.get("passed")),
        "overall": quality.get("overall", 0),
        "violations": normalize_review_list(quality.get("violations"))[:8],
    }] if quality_attempt_count else []
    stage_started = time.perf_counter()
    while answer_source == "real" and not quality["passed"] and quality_attempt_count < max_quality_attempts:
        remaining_budget = remaining_interaction_seconds()
        if remaining_budget is not None and remaining_budget < MIN_REFINEMENT_BUDGET_SECONDS:
            write_runtime_event(
                "agent.skipped", "agent", "skipped", component="child_tutor_repair",
                activity_mode=activity_mode, feedback_mode=feedback,
                details={"reason": "interaction_budget_exhausted", "nextAttempt": quality_attempt_count + 1},
            )
            break
        repair_count += 1
        quality_attempt_count += 1
        repair_started = time.perf_counter()
        write_runtime_event(
            "agent.start", "agent", "started", component="child_tutor_repair",
            activity_mode=activity_mode, feedback_mode=feedback,
            details={"attempt": quality_attempt_count, "maxAttempts": max_quality_attempts},
        )
        reference_answer = normalize_optional_text(plan.get("truthKernel"), 220)
        quality_failure_text = "；".join(normalize_review_list(quality.get("violations")))
        reference_named_in_failures = bool(reference_answer) and any(
            phrase and phrase in quality_failure_text
            for phrase in split_sentences(reference_answer)
        )
        current_answer_chars = len(normalize_optional_text(candidate.get("displayText"), MAX_CHILD_ANSWER_CHARS))
        required_answer_chars = max(0, int(int(contract.get("minAnswerChars", 0) or 0) * 0.9))
        repeated_failure_count = sum(
            1
            for attempt_record in quality_attempt_history
            if normalize_review_list(attempt_record.get("violations")) == normalize_review_list(quality.get("violations"))
        )
        target_answer_chars = min(
            int(contract.get("maxAnswerChars", MAX_CHILD_ANSWER_CHARS) or MAX_CHILD_ANSWER_CHARS),
            max(required_answer_chars + 20, current_answer_chars + 30),
        )
        repair_payload = dict(request_payload)
        repair_payload.update({
            "candidateToRepair": candidate,
            "qualityFailures": quality.get("violations", []),
            "modeEvidence": (quality.get("languageEvidence") or {}).get(
                "storyStructure" if activity_mode == "story" else "observationStructure",
                {},
            ),
            "repairInstructions": quality.get("repairInstructions", []),
            "qualityAttempt": quality_attempt_count,
            "maximumQualityAttempts": max_quality_attempts,
            "previousQualityAttempts": quality_attempt_history[-4:],
            "repairRules": {
                "requiredIdeaGroupsMustAllAppear": active_groups,
                "causalChainMustAppearInOrder": active_chain,
                "preferredCoveragePhrases": [min(group, key=len) for group in active_groups if group],
                "observeSafeExperimentRule": (
                    "观察模式必须在safeExperiment依次写出‘家长陪同：’‘准备：’‘一起做：’‘重点观察：’‘家长可以问：’‘安全提醒：’六个标题及具体内容。重点观察必须明确写出能看见的现象或变化。"
                    if activity_mode == "observe" else ""
                ),
                "storyStructureRule": (
                    "故事模式必须写六到七句。前两句必须写具体地点和角色互动的纯情节，并且不能出现因为、所以、变成、冷空气、聚大、变重等机制词；中段写至少两个连续事件；倒数第二句必须以‘故事里的真正原因是’开头并覆盖因果前序，最后一句用‘这就是’覆盖结果；safeExperiment留空。"
                    if activity_mode == "story" else ""
                ),
                "nonActivitySafeExperimentRule": (
                    "当前模式不是一起观察，safeExperiment必须留空。"
                    if activity_mode != "observe" else ""
                ),
                "analogyPolicy": plan.get("analogyPolicy", "omit"),
                "onlyAllowedAnalogy": plan.get("validatedAnalogy", ""),
                "onlyAllowedExample": plan.get("validatedExample", ""),
                "exampleRule": (
                    "当前是举例反馈或故事模式，可以使用questionPlan中唯一已验证的例子或类比。"
                    if feedback == "example" or activity_mode == "story"
                    else "当前没有要求举例，displayText和analogy都不得新增例子、类比或‘就像……’表达；应通过补充原因和自然总结达到长度要求。"
                ),
                "referenceChildSafeAnswer": reference_answer,
                "referenceNamedInFailures": reference_named_in_failures,
                "currentAnswerCharacters": current_answer_chars,
                "requiredMinimumAnswerCharacters": required_answer_chars,
                "targetAnswerCharacters": target_answer_chars,
                "additionalCharactersNeeded": max(0, target_answer_chars - current_answer_chars),
                "sameFailureRepeatedCount": repeated_failure_count,
                "referenceRule": (
                    "这是反馈重答。truthKernel只提供事实边界，禁止逐字复制；必须执行feedbackInstruction并明显换一种表达。"
                    if feedback in {"confused", "simpler", "example", "why"}
                    else
                    "truthKernel只提供事实边界。必须保留事实与因果顺序，并扩写到alignmentContract要求的回答方式、最少句数和解释量；禁止逐字复制。"
                    if reference_answer and not reference_named_in_failures
                    else "只替换失败项指出的问题短语，同时保留全部事实原子和因果顺序，并满足回答方式的最少解释量。"
                ),
                "lengthRule": (
                    f"displayText必须有{contract.get('minSentences', 0)}到{contract.get('maxSentences', 0)}个完整句，"
                    f"且至少{required_answer_chars}个字符，本轮目标为{target_answer_chars}个字符左右。"
                    "safeExperiment是独立字段，不计入displayText的句数或长度。每句必须增加新的解释信息，禁止重复凑字。"
                ),
            },
        })
        try:
            repaired = call_model_json(
                "child_tutor",
                generation_system_prompt()
                + "这是逐轮改进，不要输出思维过程。必须根据candidateToRepair、qualityFailures和previousQualityAttempts生成一份新的完整答案。"
                + "必须逐项修正当前qualityFailures，不能丢失truthKernel；不要原样返回上一轮答案。"
                + "先读取repairRules.lengthRule，并完整重写displayText；如果失败项包含回答过短，不能只改一个短语或只增加一句。"
                + "必须对照repairRules.currentAnswerCharacters、requiredMinimumAnswerCharacters和targetAnswerCharacters；若同一失败重复出现，必须改用更完整的段落结构，而不是再次返回相近长度和相同句式。"
                + "必须遵守repairRules.exampleRule；普通回答过短时只能补充解释和总结，不能擅自添加例子或类比。"
                + ("反馈重答中，truthKernel只作为事实边界；必须换说法并完成feedbackInstruction。" if feedback in {"confused", "simpler", "example", "why"} else "即使referenceNamedInFailures=false，也必须根据truthKernel重新组织完整回答，并达到alignmentContract规定的最少句数和解释量。")
                + "如果referenceNamedInFailures=true，只改掉被指出的问题短语，不得删除任何事实原子或打乱因果顺序。",
                repair_payload,
                min(0.6, 0.2 + repair_count * 0.04),
            )
        except Exception as exc:
            print(f"[alignment] repair unavailable: {type(exc).__name__}")
            repaired = None
        if isinstance(repaired, dict) and all(key in repaired for key in REQUIRED_AI_FIELDS):
            repaired_candidate = normalize_ai_response(repaired, review_plan)
            repaired_candidate["truthKernel"] = plan.get("truthKernel", "")
            repaired_candidate["epistemicStatus"] = plan.get("epistemicStatus", "fact")
            repaired_local = local_quality_review(repaired_candidate, review_plan, contract)
            repaired_requires_model_review = answer_requires_model_review(repaired_candidate, review_plan)
            repaired_model = review_answer_with_model(repaired_candidate, review_plan, contract) if repaired_requires_model_review and repaired_local["passed"] else None
            repaired_quality = merge_quality_reviews(
                repaired_local,
                repaired_model,
                repaired_candidate,
                review_plan,
                require_model_review=repaired_requires_model_review,
            )
            if not repaired_quality.get("passed") and quality_has_only_soft_failures(repaired_quality):
                repaired_quality = accept_soft_quality_warnings(repaired_quality)
            candidate, quality = repaired_candidate, repaired_quality
            quality_attempt_history.append({
                "attempt": quality_attempt_count,
                "passed": bool(quality.get("passed")),
                "overall": quality.get("overall", 0),
                "violations": normalize_review_list(quality.get("violations"))[:8],
            })
            if repaired_quality["passed"]:
                write_runtime_event(
                    "quality.accept", "agent", "completed", component="quality_reviewer",
                    activity_mode=activity_mode, feedback_mode=feedback,
                    details={"stage": "repair", "attempt": quality_attempt_count, "reviewSource": repaired_quality.get("reviewSource", "")},
                )
            else:
                log_rejected_model_candidate(f"repair_{quality_attempt_count}", repaired_candidate, repaired_quality)
        else:
            quality = {
                "passed": False,
                "overall": 0,
                "scores": {},
                "violations": ["模型没有返回完整JSON结构"],
                "repairInstructions": ["返回完整JSON，并包含所有必填回答字段"],
                "reviewSource": "model_schema_failure",
            }
            quality_attempt_history.append({
                "attempt": quality_attempt_count,
                "passed": False,
                "overall": 0,
                "violations": ["模型没有返回完整JSON结构"],
            })
        write_runtime_event(
            "agent.finish" if quality.get("passed") else "agent.error",
            "agent", "completed" if quality.get("passed") else "failed",
            component="child_tutor_repair", activity_mode=activity_mode, feedback_mode=feedback,
            duration_ms=round((time.perf_counter() - repair_started) * 1000),
            error_type="" if quality.get("passed") else "quality_rejected",
            details={"attempt": quality_attempt_count, "maxAttempts": max_quality_attempts},
        )
    stage_latency["repair"] = round(time.perf_counter() - stage_started, 3)
    stage_started = time.perf_counter()
    if not quality["passed"]:
        candidate = normalize_ai_response(alignment_failure_fallback(plan))
        answer_source = "quality_fallback"
        fallback_diagnostics = local_quality_review(candidate, {**plan, "planStatus": "safety_fallback", "requiredIdeaGroups": [], "feedbackMode": "normal"}, contract)
        quality = {
            **fallback_diagnostics,
            "passed": False,
            "alignedAnswer": False,
            "fallbackSafe": True,
            "deliveryValidated": False,
            "violations": ["真实模型回答在生成或修复后仍未通过质量门禁，未使用预写答案替代"],
            "reviewSource": "local_model_failure_fallback",
        }
    stage_latency["fallbackReview"] = round(time.perf_counter() - stage_started, 3)
    delivery_validated = answer_source == "real" and bool(quality.get("passed"))
    quality["alignedAnswer"] = delivery_validated
    quality["fallbackSafe"] = answer_source == "quality_fallback"
    quality["deliveryValidated"] = delivery_validated
    metadata = {
        "answerSource": answer_source,
        "generationSource": "child_answer_model" if answer_source == "real" else "child_answer_model_failed",
        "questionPlan": plan,
        "teachbackAssessment": teachback_assessment,
        "learningEvidence": teachback_assessment,
        "alignmentContract": contract,
        "quality": {
            **quality,
            "repairCount": repair_count,
            "qualityAttemptCount": quality_attempt_count,
            "maximumQualityAttempts": max_quality_attempts,
            "qualityAttemptHistory": quality_attempt_history,
            "answerModelAttempts": answer_model_attempts + repair_count,
        },
        "deliveryDecision": {
            "initialSource": initial_source,
            "initialPassed": initial_quality["passed"],
            "initialOverall": initial_quality["overall"],
            "initialViolations": initial_quality["violations"],
            "repairAttempted": repair_count > 0,
            "qualityAttemptCount": quality_attempt_count,
            "maximumQualityAttempts": max_quality_attempts,
            "fallbackUsed": answer_source == "quality_fallback",
            "deliveredSource": answer_source,
            "deliveryValidated": delivery_validated,
            "fallbackSafe": answer_source == "quality_fallback",
        },
        "latencyBreakdownSeconds": {
            **stage_latency,
            "workflowTotal": round(time.perf_counter() - workflow_started, 3),
        },
    }
    write_runtime_event(
        "agent.finish" if delivery_validated else "agent.error", "agent", "completed" if delivery_validated else "failed",
        component="education_workflow", activity_mode=activity_mode, feedback_mode=feedback,
        duration_ms=round((time.perf_counter() - workflow_started) * 1000), error_type="" if delivery_validated else "delivery_rejected",
        details={
            "answerSource": answer_source,
            "deliveryValidated": delivery_validated,
            "repairCount": repair_count,
            "qualityAttemptCount": quality_attempt_count,
        },
    )
    return candidate, metadata


def run_child_alignment_workflow(profile: Dict[str, Any], user_text: str, feedback: str, related_cards: List[Dict[str, Any]], activity_mode: str = "ask", original_question: str = "", previous_answer: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    started = time.perf_counter()
    deadline_token = INTERACTION_DEADLINE.set(started + interaction_budget_seconds())
    try:
        answer, metadata = _run_child_alignment_workflow(profile, user_text, feedback, related_cards, activity_mode, original_question, previous_answer)
        metadata["interactionLatencySeconds"] = round(time.perf_counter() - started, 3)
        metadata["interactionBudgetSeconds"] = interaction_budget_seconds()
        return answer, metadata
    finally:
        INTERACTION_DEADLINE.reset(deadline_token)


def call_openai_compatible(profile: Dict[str, Any], user_text: str, feedback: str, related_cards: List[Dict[str, Any]], activity_mode: str = "ask") -> Optional[Dict[str, Any]]:
    answer, _ = run_child_alignment_workflow(profile, user_text, feedback, related_cards, activity_mode)
    return answer


def teachback_contradiction_hits(statement: str, plan: Dict[str, Any]) -> List[str]:
    hits = [
        claim for claim in normalize_list(plan.get("avoidClaims"))
        if claim and compact_fact_text(claim) in compact_fact_text(statement)
    ]
    contradiction_patterns = (
        ("把月亮说成真的追随", r"月亮[^。！？!?]{0,12}(?:喜欢|爱)[^。！？!?]{0,8}(?:追|跟着)"),
        ("把天空说成被涂蓝", r"天空[^。！？!?]{0,8}(?:涂|刷)[^。！？!?]{0,4}蓝"),
        ("把水汽说成拥抱", r"水汽[^。！？!?]{0,8}抱在一起"),
        ("把云或水滴说成拿不住", r"(?:云|水滴)[^。！？!?]{0,8}(?:拿不住|抓不住)"),
    )
    hits.extend(label for label, pattern in contradiction_patterns if re.search(pattern, statement or ""))
    hits.extend(scientific_metaphor_hits(statement))
    return list(dict.fromkeys(hits))


def teachback_evidence(statement: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    by_mode = plan.get("requiredIdeaGroupsByMode") if isinstance(plan.get("requiredIdeaGroupsByMode"), dict) else {}
    groups = normalize_idea_groups(by_mode.get("simpler")) or normalize_idea_groups(by_mode.get("normal")) or normalize_idea_groups(plan.get("requiredIdeaGroups"))
    group_matches = [idea_group_present(statement, group) for group in groups]
    matched = sum(1 for present in group_matches if present)
    coverage_passed = bool(groups) and matched == len(groups)
    causal_by_mode = normalize_mode_groups(plan.get("causalChainByMode"))
    causal_chain = causal_by_mode.get("simpler") or causal_by_mode.get("normal") or []
    causal_order_correct, causal_positions = ordered_idea_groups_present(statement, causal_chain) if causal_chain else (True, [])
    contradiction_hits = teachback_contradiction_hits(statement, plan)
    local_passed = coverage_passed and causal_order_correct and not contradiction_hits
    if contradiction_hits:
        outcome = "contradicted"
    elif not local_passed:
        outcome = "insufficient"
    else:
        outcome = "candidate_mastery"
    return {
        "type": "mastery",
        "correct": local_passed,
        "outcome": outcome,
        "localPassed": local_passed,
        "reviewed": False,
        "reviewPassed": False,
        "assessmentAvailable": True,
        "source": "local_teachback_gate",
        "matchedIdeaGroups": matched,
        "requiredIdeaGroups": len(groups),
        "groupMatches": group_matches,
        "coveragePassed": coverage_passed,
        "contradiction": bool(contradiction_hits),
        "contradictionHits": contradiction_hits,
        "causalOrderCorrect": causal_order_correct,
        "causalPositions": causal_positions,
        "statement": statement[:160],
    }


def review_teachback_with_model(statement: str, plan: Dict[str, Any], local_assessment: Dict[str, Any]) -> Dict[str, Any]:
    if not local_assessment.get("localPassed"):
        return local_assessment
    system = (
        "你是独立的儿童理解证据审核器。只判断孩子的复述是否足以作为掌握证据，不生成给孩子的回答。"
        "必须独立核对：是否覆盖minimumIdeaGroups；是否与truthKernel一致；是否包含自相矛盾、avoidClaims、拟人化科学解释；"
        "若有causalChain，步骤是否完整且顺序正确。两个正确片段加一个错误结论仍然不合格。"
        "语言不需要和参考答案逐字相同，但含义必须明确。证据不足时宁可判false，不要猜测孩子已经理解。"
        "只输出JSON：passed,sufficientUnderstanding,contradiction,causalOrderCorrect,coveredIdeaGroups,violations。不要输出思维过程。"
    )
    try:
        review = call_model_json("teachback_reviewer", system, {
            "childStatement": statement[:240],
            "truthKernel": normalize_optional_text(plan.get("truthKernel"), 240),
            "minimumIdeaGroups": (
                normalize_idea_groups((plan.get("requiredIdeaGroupsByMode") or {}).get("simpler"))
                or active_required_idea_groups(plan, "normal")
            ),
            "causalChain": (
                normalize_mode_groups(plan.get("causalChainByMode")).get("simpler")
                or normalize_mode_groups(plan.get("causalChainByMode")).get("normal")
                or []
            ),
            "avoidClaims": normalize_list(plan.get("avoidClaims")),
            "localEvidence": local_assessment,
        }, 0.0)
    except Exception as exc:
        print(f"[alignment] teachback reviewer unavailable: {type(exc).__name__}")
        review = None
    if not isinstance(review, dict):
        return {
            **local_assessment,
            "correct": False,
            "outcome": "unverified",
            "reviewed": False,
            "reviewPassed": False,
            "assessmentAvailable": False,
            "source": "parent_analysis_unavailable",
            "reviewViolations": ["独立复述审核不可用，禁止产生正向掌握证据"],
        }
    contradiction = bool(review.get("contradiction"))
    sufficient = bool(review.get("sufficientUnderstanding"))
    causal_order = bool(review.get("causalOrderCorrect", True))
    review_passed = bool(review.get("passed")) and sufficient and not contradiction and causal_order
    return {
        **local_assessment,
        "correct": review_passed,
        "outcome": "mastered" if review_passed else "contradicted" if contradiction else "insufficient",
        "reviewed": True,
        "reviewPassed": review_passed,
        "assessmentAvailable": True,
        "source": "local+parent_analysis",
        "reviewSufficientUnderstanding": sufficient,
        "reviewContradiction": contradiction,
        "reviewCausalOrderCorrect": causal_order,
        "reviewCoveredIdeaGroups": normalize_review_list(review.get("coveredIdeaGroups"))[:8],
        "reviewViolations": normalize_review_list(review.get("violations"))[:8],
    }


def derive_memory_updates(user_text: str, ai: Dict[str, Any], plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    concept_labels = normalize_list(plan.get("conceptLabels"))
    updates = [
        {
            "concept": concept,
            "status": "candidate",
            "confidence": 0.28,
            "evidence": user_text[:80],
            "effectiveAnalogy": "",
        }
        for concept in concept_labels
    ] or extract_concepts(user_text, ai.get("displayText", ""))
    truth = normalize_optional_text(plan.get("truthKernel"), 160)
    analogy = normalize_optional_text(ai.get("analogy"), 160)
    trusted_analogy = analogy if plan.get("analogyPolicy") == "allow_vetted" and analogy == normalize_optional_text(plan.get("validatedAnalogy"), 160) else ""
    for update in updates:
        update["evidence"] = user_text[:120]
        update["truthKernel"] = truth
        update["effectiveAnalogy"] = trusted_analogy
    return updates


def upsert_preference_memory(db: Dict[str, Any], child_id: str, source_ids: List[str], feedback: str) -> None:
    mapping = {
        "confused": ("解释偏好：降低认知负荷", "孩子表示没听懂", "更短句、一个概念、先直接回答"),
        "simpler": ("解释偏好：更简单", "孩子要求再简单一点", "减少术语和因果步骤"),
        "example": ("解释偏好：生活例子", "孩子要求举例", "先保留事实，再使用已验证的观察例子；不自动套熟悉物类比"),
    }
    if feedback not in mapping:
        return
    concept, evidence, strategy = mapping[feedback]
    existing = next((item for item in db["memoryItems"].values() if item.get("childId") == child_id and item.get("type") == "preference" and item.get("concept") == concept and item.get("status") != "deleted"), None)
    if existing:
        existing["evidenceCount"] = int(existing.get("evidenceCount", 1) or 1) + 1
        existing["confidence"] = round(min(0.9, float(existing.get("confidence", 0.55)) + 0.05), 2)
        existing["evidence"] = evidence
        existing["effectiveAnalogy"] = strategy
        existing["sourceMessageIds"] = list(dict.fromkeys(existing.get("sourceMessageIds", []) + source_ids))[-12:]
        existing["updatedAt"] = now_iso()
        return
    item = {"id": new_id("mem"), "childId": child_id, "type": "preference", "concept": concept, "status": "learning", "confidence": 0.58, "evidence": evidence, "effectiveAnalogy": strategy, "sourceMessageIds": source_ids, "evidenceCount": 1, "history": [], "parentVerified": False, "parentNote": "", "updatedAt": now_iso()}
    db["memoryItems"][item["id"]] = item


def upsert_memory(
    db: Dict[str, Any],
    child_id: str,
    updates: List[Dict[str, Any]],
    source_ids: List[str],
    feedback: str,
    user_text: str = "",
    question_plan: Optional[Dict[str, Any]] = None,
    mastery_assessment: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    changed = []
    evidence_log = []
    assessment = mastery_assessment if feedback == "teachback" and isinstance(mastery_assessment, dict) else None
    assessment_outcome = normalize_optional_text((assessment or {}).get("outcome"), 32)
    assessment_source = normalize_optional_text((assessment or {}).get("source"), 64)
    reviewed_positive = bool(
        assessment
        and assessment.get("correct") is True
        and assessment.get("reviewed") is True
        and assessment.get("reviewPassed") is True
        and assessment_source == "local+parent_analysis"
    )
    contradicted = bool(assessment and assessment_outcome == "contradicted")
    reviewed_contradiction = bool(contradicted and assessment.get("reviewed") is True)
    neutral_teachback = feedback == "teachback" and not reviewed_positive and not contradicted
    for upd in updates:
        concept = normalize_optional_text(upd.get("concept"), 80) or "新的好奇问题"
        existing = next((m for m in db["memoryItems"].values() if m.get("childId") == child_id and m.get("type") == "cognitive" and m.get("concept") == concept and m.get("status") != "deleted"), None)
        if reviewed_positive:
            evidence_type = "mastery_correct"
        elif contradicted:
            evidence_type = "mastery_incorrect"
        elif neutral_teachback and assessment_outcome == "insufficient":
            evidence_type = "teachback_insufficient"
        elif neutral_teachback:
            evidence_type = "mastery_unverified"
        elif feedback in {"confused", "simpler"}:
            evidence_type = "confusion"
        else:
            evidence_type = "exposure"
        if existing:
            existing["exposureCount"] = int(existing.get("exposureCount", 0) or 0) + (0 if feedback == "teachback" else 1)
            existing["confusionCount"] = int(existing.get("confusionCount", 0) or 0) + (1 if feedback in {"confused", "simpler"} else 0)
            existing["teachbackCorrectCount"] = int(existing.get("teachbackCorrectCount", 0) or 0) + (1 if reviewed_positive else 0)
            existing["teachbackIncorrectCount"] = int(existing.get("teachbackIncorrectCount", 0) or 0) + (1 if contradicted else 0)
            existing["reviewedTeachbackCorrectCount"] = int(existing.get("reviewedTeachbackCorrectCount", 0) or 0) + (1 if reviewed_positive else 0)
            existing["reviewedTeachbackIncorrectCount"] = int(existing.get("reviewedTeachbackIncorrectCount", 0) or 0) + (1 if reviewed_contradiction else 0)
            if existing.get("parentVerified"):
                new_conf = float(existing.get("confidence", 0.5))
            elif reviewed_positive:
                new_conf = min(0.9, float(existing.get("confidence", 0.35)) + 0.12)
            elif contradicted:
                new_conf = max(0.12, float(existing.get("confidence", 0.35)) - 0.1)
            elif feedback in {"confused", "simpler"}:
                new_conf = max(0.12, float(existing.get("confidence", 0.35)) - 0.06)
            elif neutral_teachback:
                new_conf = float(existing.get("confidence", 0.35))
            else:
                new_conf = min(0.62, float(existing.get("confidence", 0.35)) + 0.02)
            if existing.get("parentVerified"):
                new_status = existing.get("status", "learning")
            elif existing["reviewedTeachbackCorrectCount"] >= 2 and new_conf >= 0.72:
                new_status = "known"
            elif contradicted or existing["confusionCount"] >= 2:
                new_status = "needs_review"
            elif reviewed_positive or new_conf >= 0.5:
                new_status = "learning"
            elif neutral_teachback:
                new_status = existing.get("status", "candidate")
            else:
                new_status = "candidate"
            existing.update({"status": new_status, "confidence": round(new_conf, 2), "evidence": user_text[:160] if feedback == "teachback" else upd.get("evidence") or existing.get("evidence"), "effectiveAnalogy": upd.get("effectiveAnalogy") or existing.get("effectiveAnalogy"), "truthKernel": upd.get("truthKernel") or existing.get("truthKernel", ""), "lastEvidenceType": evidence_type, "sourceMessageIds": list(dict.fromkeys(existing.get("sourceMessageIds", []) + source_ids))[-12:], "updatedAt": now_iso()})
            changed.append(existing)
        else:
            confidence = 0.6 if reviewed_positive else 0.2 if contradicted else 0.28
            status = "learning" if reviewed_positive else "needs_review" if contradicted else "candidate"
            item = {"id": new_id("mem"), "childId": child_id, "type": "cognitive", "concept": concept, "status": status, "confidence": confidence, "evidence": user_text[:160] if feedback == "teachback" else upd.get("evidence", ""), "effectiveAnalogy": upd.get("effectiveAnalogy", ""), "truthKernel": upd.get("truthKernel", ""), "lastEvidenceType": evidence_type, "exposureCount": 0 if feedback == "teachback" else 1, "confusionCount": 1 if feedback in {"confused", "simpler"} else 0, "teachbackCorrectCount": 1 if reviewed_positive else 0, "teachbackIncorrectCount": 1 if contradicted else 0, "reviewedTeachbackCorrectCount": 1 if reviewed_positive else 0, "reviewedTeachbackIncorrectCount": 1 if reviewed_contradiction else 0, "sourceMessageIds": source_ids, "history": [], "parentVerified": False, "parentNote": "", "updatedAt": now_iso()}
            db["memoryItems"][item["id"]] = item
            changed.append(item)
        evidence_log.append({
            "concept": concept,
            "evidenceType": evidence_type,
            "masteryCorrect": reviewed_positive if feedback == "teachback" else None,
            "assessmentOutcome": assessment_outcome or None,
            "assessmentSource": assessment_source or None,
            "assessmentReviewed": bool((assessment or {}).get("reviewed")) if assessment else False,
            "assessmentReviewPassed": bool((assessment or {}).get("reviewPassed")) if assessment else False,
            "status": changed[-1].get("status"),
            "confidence": changed[-1].get("confidence"),
        })
    upsert_preference_memory(db, child_id, source_ids, feedback)
    return changed, evidence_log


def memory_terms(text: str) -> set[str]:
    normalized = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
    terms = {normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))}
    terms.update(re.findall(r"[A-Za-z0-9]{2,}", text or ""))
    return {term for term in terms if term}


def related_memory_cards(db: Dict[str, Any], child_id: str, question: str) -> List[Dict[str, Any]]:
    question_terms = memory_terms(question)
    scored = []
    for item in db["memoryItems"].values():
        if item.get("childId") != child_id or item.get("status") == "deleted":
            continue
        memory_type = str(item.get("type", ""))
        triggers = normalize_list(item.get("retrievalTriggers")) + normalize_list(item.get("bridgeTriggers")) + normalize_list(item.get("horizonTriggers")) + normalize_list(item.get("applicableTopics"))
        searchable = " ".join([
            str(item.get("concept", "")), str(item.get("evidence", "")), str(item.get("truthKernel", "")),
            str(item.get("strategy", "")), " ".join(triggers),
        ])
        overlap = len(question_terms.intersection(memory_terms(searchable)))
        if memory_type == "cognitive":
            trust_bonus = 3 if memory_card_is_trusted(item) and item.get("parentVerified") else 2 if memory_card_is_trusted(item) else 0
            if overlap or trust_bonus:
                scored.append((overlap * 3 + trust_bonus + float(item.get("confidence", 0)), item))
        elif memory_type == "preference":
            if overlap or item.get("parentVerified") or float(item.get("confidence", 0) or 0) >= 0.65:
                scored.append((overlap * 2 + 1.5 + float(item.get("confidence", 0)), item))
        elif memory_type in {"episode", "association", "inference"}:
            if overlap:
                scored.append((overlap * 3 + float(item.get("confidence", 0)), item))
        elif memory_type == "education_skill" and item.get("status") in {"active", "known", "validated"}:
            if overlap:
                scored.append((overlap * 2.5 + float(item.get("reuseCount", 0) or 0) / 100 + 1, item))
    ranked = sorted(scored, key=lambda pair: pair[0], reverse=True)
    selected: List[Dict[str, Any]] = []
    type_counts: Dict[str, int] = {}
    for _, item in ranked:
        memory_type = str(item.get("type", ""))
        limit = 4 if memory_type == "cognitive" else 2
        if type_counts.get(memory_type, 0) >= limit:
            continue
        selected.append(item)
        type_counts[memory_type] = type_counts.get(memory_type, 0) + 1
        if len(selected) >= 8:
            break
    return selected


def chat_memory_page(
    db: Dict[str, Any],
    child_id: str,
    page: int = 1,
    page_size: int = 30,
    query: str = "",
    source_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    messages = sorted(
        [item for item in db.get("messages", {}).values() if item.get("childId") == child_id],
        key=lambda item: item.get("createdAt", ""),
    )
    conversations = {
        str(item.get("id", "")): item
        for item in db.get("conversations", {}).values()
        if item.get("childId") == child_id
    }
    source_ids = [
        str(value).strip()[:80] for value in (source_ids or [])
        if re.fullmatch(r"[A-Za-z0-9_-]+", str(value).strip()[:80])
    ][:12]
    source_set = set(source_ids)
    query = normalize_optional_text(query, 80).lower()
    evidence_mode = bool(source_set)

    if evidence_mode:
        selected_indexes: set[int] = set()
        for index, message in enumerate(messages):
            if str(message.get("id", "")) not in source_set:
                continue
            conversation_id = message.get("conversationId")
            same_conversation = [
                position for position, candidate in enumerate(messages)
                if candidate.get("conversationId") == conversation_id
            ]
            if index in same_conversation:
                local_index = same_conversation.index(index)
                selected_indexes.update(same_conversation[max(0, local_index - 2):local_index + 3])
        filtered = [message for index, message in enumerate(messages) if index in selected_indexes]
    else:
        filtered = [message for message in messages if not query or query in str(message.get("text", "")).lower()]

    page_size = max(10, min(50, int(page_size or 30)))
    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(total_pages, int(page or 1)))
    if evidence_mode:
        page_items = filtered[:50]
        page = 1
        total_pages = 1
    else:
        newest_first = list(reversed(filtered))
        start = (page - 1) * page_size
        page_items = list(reversed(newest_first[start:start + page_size]))

    items = []
    for message in page_items:
        conversation = conversations.get(str(message.get("conversationId", "")), {})
        items.append({
            **message,
            "conversationTitle": normalize_optional_text(conversation.get("title"), 80) or "连续对话",
            "isSourceEvidence": str(message.get("id", "")) in source_set,
        })
    matched = [source_id for source_id in source_ids if any(str(item.get("id", "")) == source_id for item in messages)]
    return {
        "items": items,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": total_pages,
        "query": query,
        "evidenceMode": evidence_mode,
        "sourceIds": source_ids,
        "matchedSourceIds": matched,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CuriosityAgentDemo/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")

    def send_json(self, obj: Any, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self, max_body: int = MAX_BODY) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length", "0"))
        if n < 0 or n > max_body:
            raise ValueError("body too large")
        if n == 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def product_api(self, parsed, method="GET") -> None:
        try:
            # A localhost demo has no account auth; reject cross-origin writes
            # and untrusted Host headers instead of exposing a local-key proxy.
            host = self.headers.get("Host", "").split(":")[0]
            if host not in {"127.0.0.1", "localhost"}:
                raise ProductError("请通过本机地址打开产品。", 403, "local_only")
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                raise ProductError("只允许从本机产品页面操作。", 403, "origin_mismatch")
            if method == "GET":
                result = PRODUCT.get(parsed.path, parse_qs(parsed.query))
            else:
                data = self.read_json()
                if not isinstance(data, dict):
                    raise ProductError("请求内容格式不正确。")
                result = PRODUCT.mutate(parsed.path, data, method)
            self.send_json(result)
        except ProductError as exc:
            self.send_json({"error": exc.code, "message": str(exc)}, exc.status)
        except (ValueError, TypeError):
            self.send_json({"error": "invalid_request", "message": "请求内容格式不正确，请重新填写。"}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            print("[product] local operation failed:", type(exc).__name__)
            self.send_json({"error": "local_error", "message": "本机暂时没有完成操作，请重试。"}, 500)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self.product_api(parsed)
        if parsed.path == "/video-demo":
            return self.serve_file(STATIC_DIR / "video-demo.html", "text/html; charset=utf-8")
        if parsed.path in ("/", "/setup", "/child", "/parent", "/memory"):
            return self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if parsed.path.startswith("/static/"):
            target = (STATIC_DIR / parsed.path[len("/static/"):]).resolve()
            try:
                target.relative_to(STATIC_DIR.resolve())
            except ValueError:
                return self.send_error(404)
            if not target.is_file():
                return self.send_error(404)
            ctype = "text/css" if target.suffix == ".css" else "application/javascript" if target.suffix == ".js" else "application/octet-stream"
            return self.serve_file(target, ctype)
        self.send_error(404)

    def serve_file(self, path: Path, ctype: str) -> None:
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        return self.product_api(urlparse(self.path), "POST")

    def do_PATCH(self) -> None:
        return self.product_api(urlparse(self.path), "PATCH")

    def do_DELETE(self) -> None:
        return self.product_api(urlparse(self.path), "DELETE")



def iso_day(value: str) -> str:
    try:
        return value[:10] if value else datetime.now(timezone.utc).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _parent_quote(message: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if not message:
        return None
    text = normalize_optional_text(message.get("text"), 160)
    if not text:
        return None
    return {
        "text": text,
        "date": iso_day(str(message.get("createdAt", ""))),
        "sourceId": str(message.get("id", ""))[:80],
    }


def question_topic(text: str) -> str:
    if "月亮" in text or "星" in text:
        return "太空/月亮"
    if "影" in text or "光" in text:
        return "光与影"
    if "恐龙" in text:
        return "恐龙"
    return "其他好奇问题"


def build_parent_feed_cards(db: Dict[str, Any], child_id: str) -> List[Dict[str, Any]]:
    """Build a fresh, evidence-linked parent feed from the current memory store."""
    messages = sorted(
        [m for m in db.get("messages", {}).values() if m.get("childId") == child_id],
        key=lambda item: item.get("createdAt", ""),
    )
    user_messages = [m for m in messages if m.get("role") == "user" and normalize_optional_text(m.get("text"), 160)]
    memory_items = [
        item for item in db.get("memoryItems", {}).values()
        if item.get("childId") == child_id and item.get("status") != "deleted"
    ]
    cognitive = sorted(
        [item for item in memory_items if item.get("type") == "cognitive"],
        key=lambda item: item.get("updatedAt", ""),
        reverse=True,
    )
    preferences = sorted(
        [item for item in memory_items if item.get("type") == "preference"],
        key=lambda item: item.get("updatedAt", ""),
        reverse=True,
    )
    associations = sorted(
        [item for item in memory_items if item.get("type") in {"episode", "association", "inference"}],
        key=lambda item: item.get("updatedAt", ""),
        reverse=True,
    )
    message_index = {str(message.get("id", "")): message for message in user_messages}

    def quotes_for(item: Optional[Dict[str, Any]], fallback: int = 1) -> List[Dict[str, str]]:
        source_ids = item.get("sourceMessageIds", []) if item else []
        selected = [_parent_quote(message_index.get(str(source_id))) for source_id in source_ids]
        quotes = [quote for quote in selected if quote]
        if not quotes:
            quotes = [quote for quote in (_parent_quote(message) for message in user_messages[-fallback:]) if quote]
        return quotes[-2:]

    generated_at = now_iso()
    latest = user_messages[-1] if user_messages else None
    recent_questions = user_messages[-24:]
    topic_cards = summarize_topics(recent_questions)
    strongest_topic = max(topic_cards, key=lambda item: item.get("count", 0), default=None)
    cards: List[Dict[str, Any]] = []

    if latest:
        topic = strongest_topic.get("topic", "最近的新问题") if strongest_topic else "最近的新问题"
        count = int(strongest_topic.get("count", 1)) if strongest_topic else 1
        topic_messages = [
            message for message in recent_questions
            if question_topic(str(message.get("text", ""))) == topic
        ]
        topic_quotes = [quote for quote in (_parent_quote(message) for message in topic_messages[-2:]) if quote]
        cards.append({
            "id": "feed_recent_curiosity",
            "type": "curiosity",
            "eyebrow": "最近的好奇线索",
            "title": f"最近在持续探索：{topic}",
            "summary": f"最近 {len(recent_questions)} 次提问中，这一主题出现了 {count} 次。先顺着孩子的问题继续聊，比急着扩展到很多知识点更合适。",
            "suggestion": "今晚可以先问：‘你现在最想弄明白这里的哪一点？’让孩子自己选择下一步。",
            "sourceQuotes": topic_quotes,
            "confidence": min(0.92, 0.55 + count * 0.07),
            "memoryBasis": ["原始对话", "近期主题统计"],
            "evidenceLevel": "观察",
            "disclaimer": "这是对近期提问的整理，不代表固定兴趣或能力判断。",
            "updatedAt": str(latest.get("createdAt") or generated_at),
        })

    learning = next((item for item in cognitive if item.get("status") in {"needs_review", "learning", "candidate"}), None)
    if learning:
        concept = normalize_optional_text(learning.get("concept"), 60) or "这个概念"
        evidence = normalize_optional_text(learning.get("evidence"), 180)
        cards.append({
            "id": f"feed_learning_{str(learning.get('id', 'memory'))[:48]}",
            "memoryId": str(learning.get("id", ""))[:80],
            "type": "learning",
            "eyebrow": "理解边界",
            "title": f"「{concept}」可以再用自己的话讲一次",
            "summary": evidence or "现有互动证据显示，这个概念还处在形成中的阶段，需要更多表达证据才能确认。",
            "suggestion": f"不要先纠正答案，可以问：‘你觉得{concept}是怎么回事？讲给我听听。’再根据孩子的原话补一个小信息。",
            "sourceQuotes": quotes_for(learning, 1),
            "confidence": max(0.2, min(0.9, float(learning.get("confidence", 0.45) or 0.45))),
            "memoryBasis": ["理解记录", "孩子原话", "家长确认" if learning.get("parentVerified") else "待家长确认"],
            "evidenceLevel": "观察",
            "disclaimer": "理解状态会随复述和家长确认更新，不是测评结论。",
            "updatedAt": str(learning.get("updatedAt") or generated_at),
        })

    preference = preferences[0] if preferences else None
    if preference:
        preference_text = normalize_optional_text(preference.get("evidence"), 180)
        analogy = normalize_optional_text(preference.get("effectiveAnalogy"), 120)
        cards.append({
            "id": f"feed_preference_{str(preference.get('id', 'memory'))[:48]}",
            "memoryId": str(preference.get("id", ""))[:80],
            "type": "communication",
            "eyebrow": "沟通方式",
            "title": "这样讲，孩子更愿意接着说",
            "summary": preference_text or "孩子对解释方式给过明确反馈，后续回答会把这条偏好作为适配线索。",
            "suggestion": f"家长也可以试试：先说一句核心原因，再用{analogy or '孩子熟悉的物品'}举一个例子，最后停下来等孩子追问。",
            "sourceQuotes": quotes_for(preference, 1),
            "confidence": max(0.2, min(0.9, float(preference.get("confidence", 0.6) or 0.6))),
            "memoryBasis": ["表达偏好", "反馈记录"],
            "evidenceLevel": "观察",
            "disclaimer": "偏好不是固定标签；如果孩子近期反馈变化，旧记录会被刷新。",
            "updatedAt": str(preference.get("updatedAt") or generated_at),
        })

    association = next((item for item in associations if item.get("type") in {"association", "inference"}), associations[0] if associations else None)
    if association and len(quotes_for(association, 2)) >= 2:
        title = normalize_optional_text(association.get("concept") or association.get("title"), 80) or "两次相隔较远的表达可能有关联"
        cards.append({
            "id": f"feed_association_{str(association.get('id', 'memory'))[:48]}",
            "memoryId": str(association.get("id", ""))[:80],
            "type": "association",
            "eyebrow": "值得留意的关联",
            "title": title,
            "summary": normalize_optional_text(association.get("evidence"), 220) or "系统发现两段对话之间可能有共同关切，建议家长用开放问题核对。",
            "suggestion": normalize_optional_text(association.get("parentSuggestion"), 180) or "可以温和地问：‘你刚才想到这件事时，心里在担心什么吗？’不要替孩子下结论。",
            "sourceQuotes": quotes_for(association, 2),
            "confidence": max(0.2, min(0.78, float(association.get("confidence", 0.55) or 0.55))),
            "memoryBasis": ["情景记忆", "跨期关联触发", "孩子原话"],
            "evidenceLevel": "待核对的推测",
            "disclaimer": "这是供家长核对的低风险假设，不是心理诊断或性格标签。",
            "updatedAt": str(association.get("updatedAt") or generated_at),
        })

    if latest:
        latest_text = normalize_optional_text(latest.get("text"), 160)
        if any(marker in latest_text for marker in ("月亮", "影子", "植物", "树叶", "恐龙")):
            activity = "一起选一个熟悉的物体做五分钟观察：先让孩子猜，再一起看，最后请孩子画下变化。"
        else:
            activity = "一起做一张‘今天的为什么’小卡：孩子画问题，家长只写下孩子自己的解释，明天再回来补一笔。"
        cards.append({
            "id": "feed_parent_activity",
            "type": "activity",
            "eyebrow": "今天可以一起做",
            "title": "把一次问答带回真实生活",
            "summary": "孩子已经用语言提出问题，下一步适合通过观察或画画留下新的证据，而不是继续增加抽象讲解。",
            "suggestion": activity,
            "sourceQuotes": quotes_for(None, 1),
            "confidence": 0.72,
            "memoryBasis": ["最近一轮对话", "理解记录"],
            "evidenceLevel": "建议",
            "disclaimer": "活动应由家长陪同，并避开火、电、药品、尖锐物和陌生环境。",
            "updatedAt": str(latest.get("createdAt") or generated_at),
        })

    if not cards:
        cards.append({
            "id": "feed_cold_start",
            "type": "onboarding",
            "eyebrow": "还没有足够记录",
            "title": "先听孩子聊三个真正想问的问题",
            "summary": "卡片会根据聊天、孩子反馈和家长确认自动刷新。记录不足时，系统不会猜测孩子的兴趣或能力。",
            "suggestion": "可以从‘今天有没有一件奇怪的事？’开始，让孩子决定聊什么。",
            "sourceQuotes": [],
            "confidence": 1.0,
            "memoryBasis": ["冷启动状态"],
            "evidenceLevel": "说明",
            "disclaimer": "有了真实互动证据后，这张卡会被新的摘要替换。",
            "updatedAt": generated_at,
        })
    return cards[:5]


def memory_visualization(db: Dict[str, Any], child_id: str) -> Dict[str, Any]:
    profile = profile_for(db, child_id)
    messages = sorted([m for m in db["messages"].values() if m.get("childId") == child_id], key=lambda x: x.get("createdAt", ""))
    memories = [m for m in db["memoryItems"].values() if m.get("childId") == child_id and m.get("status") != "deleted"]
    cognitive = [m for m in memories if m.get("type") == "cognitive"]
    preferences = [m for m in memories if m.get("type") == "preference"]
    safety_events = sorted([e for e in db["safetyEvents"].values() if e.get("childId") == child_id], key=lambda x: x.get("createdAt", ""))
    parent_feedback = sorted([f for f in db["parentFeedback"].values() if f.get("childId") == child_id], key=lambda x: x.get("createdAt", ""))

    topic_counts: Dict[str, int] = {}
    mode_counts: Dict[str, int] = {}
    for m in messages:
        if m.get("role") != "user":
            continue
        txt = m.get("text", "")
        key = "太空与月亮" if "月亮" in txt or "星" in txt else "光影观察" if "影" in txt or "光" in txt else "恐龙与生命" if "恐龙" in txt else "自然与生活"
        topic_counts[key] = topic_counts.get(key, 0) + 1
        mode = normalize_activity_mode(m.get("activityMode"))
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    topics = sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

    avg_conf = round(sum(float(c.get("confidence", 0)) for c in cognitive) / max(1, len(cognitive)), 2)

    evidence_days = sorted({
        iso_day(item.get("createdAt") or item.get("updatedAt", ""))
        for item in messages + cognitive
        if item.get("createdAt") or item.get("updatedAt")
    })[-30:]
    status_weight = {"known": 1.0, "learning": 0.68, "candidate": 0.48, "needs_review": 0.28}
    recent_days = []
    for day in evidence_days:
        day_messages = [m for m in messages if iso_day(m.get("createdAt", "")) == day]
        day_questions = [m for m in day_messages if m.get("role") == "user"]
        day_answers = [m for m in day_messages if m.get("role") == "assistant"]
        day_feedback = [m for m in day_questions if m.get("feedbackMode") and m.get("feedbackMode") != "normal"]
        known_by_day = [c for c in cognitive if iso_day(c.get("updatedAt") or c.get("createdAt", "")) <= day]

        understanding_values = [
            max(0.0, min(1.0, float(card.get("confidence", 0) or 0))) * 70
            + status_weight.get(card.get("status"), 0.4) * 30
            for card in known_by_day
        ]
        understanding_score = round(sum(understanding_values) / len(understanding_values)) if understanding_values else 0

        difficulty_values = []
        for message in day_questions:
            text = normalize_optional_text(message.get("text"), MAX_USER_TEXT_CHARS)
            score = min(55, 12 + len(text) * 1.25)
            if any(marker in text for marker in ("为什么", "怎么会", "为什么会", "为什么不")):
                score += 18
            if normalize_activity_mode(message.get("activityMode")) in {"detail", "story", "observe"}:
                score += 10
            if message.get("feedbackMode") in {"why", "example", "confused"}:
                score += 10
            difficulty_values.append(min(100, score))
        question_difficulty = round(sum(difficulty_values) / len(difficulty_values)) if difficulty_values else 0

        adaptation_values = []
        independence_values = []
        for message in day_answers:
            delivery = message.get("deliveryDecision") if isinstance(message.get("deliveryDecision"), dict) else {}
            quality = message.get("quality") if isinstance(message.get("quality"), dict) else {}
            delivered = bool(delivery.get("deliveryValidated"))
            attempts = max(1, int(quality.get("qualityAttemptCount", 1) or 1))
            adaptation = (62 if delivered else 12) + max(0, 28 - (attempts - 1) * 7)
            if message.get("feedbackMode") in {"confused", "simpler", "example", "why"} and delivered:
                adaptation += 10
            adaptation_values.append(min(100, adaptation))

            teachback = message.get("teachbackAssessment") if isinstance(message.get("teachbackAssessment"), dict) else {}
            if teachback:
                if teachback.get("correct") or teachback.get("outcome") == "confirmed":
                    independence_values.append(92)
                elif teachback.get("outcome") in {"insufficient", "unverified"}:
                    independence_values.append(42)
                else:
                    independence_values.append(22)
        verified_by_day = [card for card in known_by_day if card.get("parentVerified") or card.get("status") == "known"]
        if verified_by_day:
            independence_values.append(min(88, 45 + len(verified_by_day) * 8))

        recent_days.append({
            "day": day[5:],
            "date": day,
            "understandingScore": max(0, min(100, understanding_score)),
            "questionDifficulty": max(0, min(100, question_difficulty)),
            "answerAdaptation": max(0, min(100, round(sum(adaptation_values) / len(adaptation_values)) if adaptation_values else 0)),
            "independenceScore": max(0, min(100, round(sum(independence_values) / len(independence_values)) if independence_values else 0)),
            "questions": len(day_questions),
            "answers": len(day_answers),
            "feedback": len(day_feedback),
        })

    known = len([c for c in cognitive if c.get("status") == "known"])
    learning = len([c for c in cognitive if c.get("status") in {"candidate", "learning"}])
    review = len([c for c in cognitive if c.get("status") == "needs_review"])
    child_questions = len([m for m in messages if m.get("role") == "user"])
    feedback_count = len([m for m in messages if m.get("role") == "user" and m.get("feedbackMode") and m.get("feedbackMode") != "normal"])
    level_score = min(100, int((avg_conf * 58) + known * 8 + len(parent_feedback) * 3 + len(preferences) * 2))
    age = int(profile.get("age", 5) or 5)
    if level_score < 42:
        level_name, language_rule = "记录还不多", "每次只讲一个概念，多用孩子熟悉的物品。"
    elif level_score < 72:
        level_name, language_rule = "已有一些可参考记录", "可以加入一个为什么，但仍然保持短句和生活例子。"
    else:
        level_name, language_rule = "参考信息比较完整", "优先参考家长确认过的内容，再邀请孩子用自己的话说一遍。"

    nodes = [
        {"id": "profile", "label": "孩子小档案", "kind": "profile", "size": 70, "meta": f"{profile.get('nickname','孩子')} · {age}岁"},
        {"id": "chat", "label": "聊天记录", "kind": "chat", "size": min(96, 36 + len([m for m in messages if m.get('role') == 'user']) * 3), "meta": f"{len(messages)} 条消息"},
        {"id": "pref", "label": "表达偏好", "kind": "preference", "size": min(86, 38 + len(preferences) * 6), "meta": f"{len(preferences)} 条偏好"},
        {"id": "parent", "label": "家长提醒", "kind": "parent", "size": min(88, 38 + len(parent_feedback) * 7), "meta": f"{len(parent_feedback)} 次反馈"},
    ]
    for c in cognitive[:8]:
        nodes.append({"id": c.get("id"), "label": c.get("concept", "概念"), "kind": "cognitive", "size": 32 + int(float(c.get("confidence", 0.2)) * 52), "status": c.get("status"), "confidence": c.get("confidence", 0), "meta": c.get("effectiveAnalogy", "")})
    edges = [{"from": "profile", "to": "chat"}, {"from": "chat", "to": "pref"}, {"from": "parent", "to": "profile"}]
    for c in cognitive[:8]:
        edges.append({"from": "chat", "to": c.get("id")})
        if c.get("parentVerified"):
            edges.append({"from": "parent", "to": c.get("id")})

    timeline = []
    timeline.append({"time": profile.get("createdAt", "")[:10], "title": "建好孩子小档案", "detail": f"年龄、兴趣、熟悉事物会在以后回答时被优先参考。"})
    for m in messages[-8:]:
        if m.get("role") == "user":
            timeline.append({"time": m.get("createdAt", "")[:16].replace("T", " "), "title": "孩子提出新问题", "detail": m.get("text", "")[:80]})
    status_labels = {
        "candidate": "刚聊到",
        "learning": "还在理解",
        "known": "已经会讲",
        "needs_review": "请家长确认",
    }
    for c in sorted(cognitive, key=lambda x: x.get("updatedAt", ""))[-5:]:
        timeline.append({"time": c.get("updatedAt", "")[:16].replace("T", " "), "title": f"更新了这条理解记录：{c.get('concept','概念')}", "detail": f"{status_labels.get(c.get('status'), '记录有更新')} · 现有记录支持度 {round(float(c.get('confidence', 0))*100)}%"})
    if parent_feedback:
        for f in parent_feedback[-3:]:
            timeline.append({"time": f.get("createdAt", "")[:16].replace("T", " "), "title": "家长提醒已保存", "detail": f.get("note") or f.get("action", "")})
    timeline = sorted(timeline, key=lambda x: x.get("time", ""))[-12:]

    recent_chat = messages[-12:]
    latest_trace = next((m.get("trace", []) for m in reversed(messages) if m.get("role") == "assistant" and isinstance(m.get("trace"), list)), [])
    latest_agent_orchestration = next((m.get("agentOrchestration", {}) for m in reversed(messages) if m.get("role") == "assistant" and isinstance(m.get("agentOrchestration"), dict)), {})
    agent_growth = [
        {"label": "档案填写情况", "value": min(100, 45 + len(profile.get("interests", []))*8 + len(profile.get("familiarItems", []))*5)},
        {"label": "可参考的例子", "value": min(100, 25 + len(preferences)*9 + len(profile.get("familiarItems", []))*6)},
        {"label": "回答合适度", "value": min(100, level_score)},
        {"label": "家长已确认", "value": min(100, 20 + len(parent_feedback)*15)},
    ]
    mode_playbook = [
        {"mode": "ask", "label": ACTIVITY_MODE_LABELS["ask"], "bestFor": "孩子突然问为什么", "parentHint": "先允许发散，再收束到一个小概念。"},
        {"mode": "detail", "label": ACTIVITY_MODE_LABELS["detail"], "bestFor": "孩子想把原因一步步弄明白", "parentHint": "按因果顺序逐步解释，每一步只增加一个新信息。"},
        {"mode": "story", "label": ACTIVITY_MODE_LABELS["story"], "bestFor": "睡前或情绪需要安定", "parentHint": "用 3-4 句小故事，把科学道理轻轻讲出来。"},
        {"mode": "observe", "label": ACTIVITY_MODE_LABELS["observe"], "bestFor": "想把问答带回现实世界", "parentHint": "选择低风险的观察，避开火、电、化学品和尖锐物。"},
    ]
    if safety_events:
        coach_title, coach_detail = "先处理安全问题", "最近出现过危险/隐私类提问，建议家长先解释边界，再一起换成安全观察。"
    elif feedback_count >= 3:
        coach_title, coach_detail = "需要更慢一点", "孩子多次反馈没听懂或要例子，下一轮建议每次只讲一个概念，并让孩子复述一句。"
    elif child_questions >= 6 and not parent_feedback:
        coach_title, coach_detail = "建议家长抽空看看", "孩子已经连续问了不少问题。家长可以确认几条理解记录，帮助后面的回答更贴近孩子。"
    else:
        coach_title, coach_detail = "现在的节奏挺合适", "可以继续用短句聊一个小问题，再做一次安全观察，睡前让孩子用自己的话讲一句。"
    return {
        "ageLevel": {"score": level_score, "name": level_name, "rule": language_rule, "age": age, "avgConfidence": avg_conf, "known": known, "learning": learning, "review": review},
        "levelAlignment": child_level_alignment(profile, feedback_count, avg_conf),
        "memoryGraph": {"nodes": nodes, "edges": edges},
        "evolutionTrend": recent_days,
        "agentGrowth": agent_growth,
        "modeDistribution": [{"mode": k, "label": ACTIVITY_MODE_LABELS.get(k, k), "count": v} for k, v in sorted(mode_counts.items(), key=lambda kv: kv[1], reverse=True)],
        "modePlaybook": mode_playbook,
        "parentCoach": {"title": coach_title, "detail": coach_detail, "questions": child_questions, "feedback": feedback_count, "safety": len(safety_events)},
        "agentSystem": public_agent_system(),
        "latestAgentOrchestration": latest_agent_orchestration,
        "latestTrace": latest_trace,
        "memoryTimeline": timeline,
        "recentChat": recent_chat,
        "topicDistribution": [{"topic": k, "count": v} for k, v in topics],
    }

def summarize_topics(qs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    topics: Dict[str, int] = {}
    for q in qs:
        key = question_topic(str(q.get("text", "")))
        topics[key] = topics.get(key, 0) + 1
    return [{"topic": k, "count": v, "tone": "持续追问" if v > 1 else "首次探索"} for k, v in topics.items()]


PRODUCT = None


def main() -> None:
    global PRODUCT
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    if host != "127.0.0.1":
        raise ValueError("本轮本机演示只绑定 127.0.0.1")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # One running owner per database; a second launch cannot fail live requests.
    with DB_PATH.with_suffix(DB_PATH.suffix + ".server.lock").open("a") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("这份演示数据已在运行，请使用已打开的页面，或为另一进程指定独立 EDUCATION_AGENT_DB。")
        with ThreadingHTTPServer((host, port), Handler) as server:
            PRODUCT = CompanionService(ROOT, DB_PATH, seed_db)
            print(f"好奇心伙伴 running at http://{host}:{port}/setup", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("本机服务已停止，记录仍保留。", flush=True)
            finally:
                PRODUCT.close()


if __name__ == "__main__":
    main()
