import json
import asyncio
import logging
import time
import random
import zipfile
import re
import hashlib
import os
import shutil
import sqlite3
import uuid
from html import unescape
from urllib.parse import unquote
from datetime import datetime
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import database
from .database import connect, init_db
from .logging_config import LOG_FILE, configure_logging
from .translation import install_package, package_status, translate_text
from .proactive import EmptyProactiveReply, generate_proactive
from .documents import DOCUMENT_DIR, chunk_pages, decode_document, extract_pages, extract_visuals, relevant_chunks, remove_original, save_original

MODEL_GENERATION_LOCK = asyncio.Lock()
SYSTEM_OPERATION_LOCK = asyncio.Lock()
UPDATE_RELEASE_LOCK = asyncio.Lock()
UPDATE_DOWNLOAD_LOCK = asyncio.Lock()
UPDATE_RELEASE_CACHE: tuple[float, dict] | None = None
PROACTIVE_GENERATION_TASK: asyncio.Task | None = None

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/qtys/Yu-s-Ai-plugin-platform/releases/latest"
GITHUB_RELEASES_LATEST_URL = "https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/latest"
BACKUP_FORMAT_VERSION = 1

logger = logging.getLogger("yus_ai.api")

PET_ACTION_OPEN = "<pet_action>"
PET_ACTION_CLOSE = "</pet_action>"
PET_ACTION_PROMPT = """【桌宠动作导演工具】
你可以为蓝色史莱姆创作符合角色性格、本次语气和情绪的表演。若接口提供 perform_pet_action 工具，请在正常正文后调用它一次，且不要再输出动作标签；若没有该工具，才另起一行追加一个 <pet_action> JSON 标签。工具调用和标签都不会展示给用户，不要在正文中解释它们。
你不是在选择动画菜单，也不是把既有动作换序组合，而是在创造一个此前不存在的软体动作。先想象情绪如何变成重心变化、受力、迟疑、惯性和回弹，再从零写出运动曲线。除非回复完全中性、适合静止，否则优先使用 action=\"custom\"；预设 none、bounce、celebrate、lean_left、lean_right、peek、shy、squish、wiggle、frontflip、backflip 只作为低调回退。
自定义表演可独立编排三层，所有关键帧第一帧 at=0、末帧 at=1，且 at 严格递增：
- body_keyframes（必填，2~7 帧）：at(0~1)、x(-18~18)、y(-40~16)、rotate(-540~540)、scale_x/scale_y(0.72~1.3)。负责重心、受力、迟疑、惯性、落地与果冻形变；不要先选“跳/摇/翻”等动作名称再套模板。
- face_keyframes（可选，2~7 帧）：at、x(-10~10)、y(-10~10)、rotate(-20~20)、scale_x/scale_y(0.75~1.25)。负责表情的迟疑、追视、后知后觉和反应延迟。
- crest_keyframes（可选，2~7 帧）：at、x(-5~5)、y(-7~7)、rotate(-45~45)、scale_x/scale_y(0.7~1.35)。负责头顶水滴的惯性、甩动和弹性跟随。
可选 effect：none、heart、sparkle、question、sweat、star、music；特效必须服务于情绪，不能每次都出现。请设计有起承转合的动作，如“先缩成一团蓄力→斜跳→脸慢半拍跟上→水滴回弹”“听不懂时身体停住→脸探出去→问号浮起”，不要只做整只上下摇晃，也不要每次都翻滚。
表情参数：expression 只能是 idle、happy、shy、surprised、sleepy、confused；eyes 可选 normal、wide、soft、closed、wink_left、wink_right；mouth 可选 neutral、smile、grin、open、o、pout；blush 为 0~1；emotion_label 用不超过 24 字概括表演意图。
节奏参数：easing 可选 linear、ease、ease_in、ease_out、ease_in_out、spring；repeat 为 1~3；duration_ms 为 600~3500；intensity 为 0.3~1。视线 gaze 只能是 cursor、none、center、left、right、up、down。桌面移动 movement 只能是 stay、left、right、toward_cursor、away_cursor、wander，move_distance 为 0~120，通常保持 stay。
自定义示例：<pet_action>{\"expression\":\"confused\",\"emotion_label\":\"身体定住，脸探头求解\",\"action\":\"custom\",\"eyes\":\"wide\",\"mouth\":\"o\",\"blush\":0.2,\"effect\":\"question\",\"gaze\":\"cursor\",\"movement\":\"stay\",\"intensity\":0.75,\"duration_ms\":1800,\"easing\":\"spring\",\"repeat\":1,\"body_keyframes\":[{\"at\":0,\"x\":0,\"y\":0,\"rotate\":0,\"scale_x\":1,\"scale_y\":1},{\"at\":0.3,\"x\":-4,\"y\":2,\"rotate\":-7,\"scale_x\":1.05,\"scale_y\":0.95},{\"at\":0.68,\"x\":1,\"y\":-3,\"rotate\":2,\"scale_x\":0.97,\"scale_y\":1.04},{\"at\":1,\"x\":0,\"y\":0,\"rotate\":0,\"scale_x\":1,\"scale_y\":1}],\"face_keyframes\":[{\"at\":0,\"x\":0,\"y\":0,\"rotate\":0,\"scale_x\":1,\"scale_y\":1},{\"at\":0.45,\"x\":6,\"y\":-2,\"rotate\":5,\"scale_x\":1.05,\"scale_y\":1.05},{\"at\":1,\"x\":0,\"y\":0,\"rotate\":0,\"scale_x\":1,\"scale_y\":1}],\"crest_keyframes\":[{\"at\":0,\"x\":0,\"y\":0,\"rotate\":0,\"scale_x\":1,\"scale_y\":1},{\"at\":0.4,\"x\":-2,\"y\":1,\"rotate\":-22,\"scale_x\":0.9,\"scale_y\":1.12},{\"at\":1,\"x\":0,\"y\":0,\"rotate\":0,\"scale_x\":1,\"scale_y\":1}]}</pet_action>
只调用一次动作工具或输出一个动作标签，不得输出 CSS、JavaScript 或正文中的动作说明。"""

ALICE_ACTION_PROMPT = """【桌宠动作导演工具】
当前桌宠外观是《刀剑神域》爱丽丝的 Q 版骑士。桌宠外观只决定动作，不改变角色卡中的身份、性格或说话方式。正常回答后可调用 perform_pet_action 一次；接口不支持工具时，另起一行输出一个 <pet_action> JSON 标签。动作信息不会展示给用户。
为这个小骑士设计符合本次回复情绪的短表演：轻盈移动重心、抬头、侧身、微微屈膝、眨眼、视线停留，头发和发饰比身体慢半拍跟随。优先使用 action="custom"，用 body_keyframes、face_keyframes、crest_keyframes 分别控制身体、五官和发饰。不要把她当作软体史莱姆，不要大幅拉伸脸和服装，也不要频繁翻滚或挥舞不存在的武器。
关键帧每层 2~7 帧，首帧 at=0、末帧 at=1，at 严格递增；每帧含 x、y、rotate、scale_x、scale_y。body 范围 x(-18~18)、y(-40~16)、rotate(-540~540)、scale(0.72~1.3)；face 范围 x/y(-10~10)、rotate(-20~20)、scale(0.75~1.25)；crest 范围 x(-5~5)、y(-7~7)、rotate(-45~45)、scale(0.7~1.35)。程序会把幅度限制到适合人物的范围。
expression 只能是 idle、happy、shy、surprised、sleepy、confused；eyes 可选 normal、wide、soft、closed、wink_left、wink_right；mouth 可选 neutral、smile、grin、open、o、pout；blush 为 0~1。effect 可选 none、heart、sparkle、question、sweat、star、music。action 可选 none、bounce、celebrate、lean_left、lean_right、peek、shy、squish、wiggle、frontflip、backflip、custom。gaze 可选 cursor、none、center、left、right、up、down；movement 可选 stay、left、right、toward_cursor、away_cursor、wander。duration_ms 为 600~3500；intensity 为 0.3~1；repeat 为 1~3；easing 可选 linear、ease、ease_in、ease_out、ease_in_out、spring。emotion_label 不超过 24 字。
只输出一次动作工具调用或一个动作标签，不输出 CSS、JavaScript，也不要在正文解释动作。"""

PET_MOTION_FRAME_SCHEMA = {
    "type": "object",
    "properties": {
        "at": {"type": "number"}, "x": {"type": "number"}, "y": {"type": "number"},
        "rotate": {"type": "number"}, "scale_x": {"type": "number"}, "scale_y": {"type": "number"},
    },
    "required": ["at", "x", "y", "rotate", "scale_x", "scale_y"],
    "additionalProperties": False,
}
PET_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "perform_pet_action",
        "description": "为蓝色史莱姆桌宠从受力、惯性和软体形变开始创造全新的分层短表演，而非组合已有动作。优先使用 custom 并分别设计身体、脸和头顶水滴。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "enum": ["idle", "happy", "shy", "surprised", "sleepy", "confused"]},
                "emotion_label": {"type": "string"},
                "action": {"type": "string", "enum": ["none", "bounce", "celebrate", "lean_left", "lean_right", "peek", "shy", "squish", "wiggle", "frontflip", "backflip", "custom"]},
                "eyes": {"type": "string", "enum": ["normal", "wide", "soft", "closed", "wink_left", "wink_right"]},
                "mouth": {"type": "string", "enum": ["neutral", "smile", "grin", "open", "o", "pout"]},
                "blush": {"type": "number"},
                "effect": {"type": "string", "enum": ["none", "heart", "sparkle", "question", "sweat", "star", "music"]},
                "gaze": {"type": "string", "enum": ["cursor", "none", "center", "left", "right", "up", "down"]},
                "movement": {"type": "string", "enum": ["stay", "left", "right", "toward_cursor", "away_cursor", "wander"]},
                "move_distance": {"type": "number"}, "intensity": {"type": "number"}, "duration_ms": {"type": "integer"},
                "easing": {"type": "string", "enum": ["linear", "ease", "ease_in", "ease_out", "ease_in_out", "spring"]},
                "repeat": {"type": "integer"},
                "body_keyframes": {"type": "array", "minItems": 2, "maxItems": 7, "items": PET_MOTION_FRAME_SCHEMA},
                "face_keyframes": {"type": "array", "minItems": 2, "maxItems": 7, "items": PET_MOTION_FRAME_SCHEMA},
                "crest_keyframes": {"type": "array", "minItems": 2, "maxItems": 7, "items": PET_MOTION_FRAME_SCHEMA},
            },
            "required": ["expression", "emotion_label", "action", "gaze", "movement", "intensity", "duration_ms"],
            "additionalProperties": False,
        },
    },
}

WEEKDAY_LABELS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def build_local_time_context(now: datetime | None = None) -> str:
    current = now if now is not None else datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    offset_seconds = int((current.utcoffset() or current - current).total_seconds())
    offset_sign = "+" if offset_seconds >= 0 else "-"
    offset_minutes = abs(offset_seconds) // 60
    offset_text = f"UTC{offset_sign}{offset_minutes // 60:02d}:{offset_minutes % 60:02d}"
    hour = current.hour
    if hour < 5:
        period = "凌晨"
    elif hour < 9:
        period = "早晨"
    elif hour < 12:
        period = "上午"
    elif hour < 14:
        period = "中午"
    elif hour < 18:
        period = "下午"
    elif hour < 21:
        period = "晚上"
    else:
        period = "深夜"
    return (
        "【当前本机时间（本次请求实时读取，作为时间问题的权威基准）】\n"
        f"本地日期：{current:%Y年%m月%d日}\n"
        f"星期：{WEEKDAY_LABELS[current.weekday()]}\n"
        f"本地时间：{current:%H:%M:%S}（{period}）\n"
        f"时区：{current.tzname() or '本地时区'}（{offset_text}）\n"
        f"ISO 时间：{current.isoformat(timespec='seconds')}\n"
        "规则：当用户询问或谈到现在、今天、日期、星期、早晚、相对时间、倒计时或截止时间时，"
        "必须以以上本机时间为基准理解和计算，并直接回答；不要声称无法获取当前时间。"
        "在与时间无关的问题中无需刻意报时。"
    )


def normalize_pet_action(payload: dict) -> dict | None:
    expressions = {"idle", "happy", "shy", "surprised", "sleepy", "confused"}
    actions = {"none", "bounce", "celebrate", "lean_left", "lean_right", "peek", "shy", "squish", "wiggle", "frontflip", "backflip", "custom"}
    movements = {"stay", "left", "right", "toward_cursor", "away_cursor", "wander"}
    eye_poses = {"normal", "wide", "soft", "closed", "wink_left", "wink_right"}
    mouth_poses = {"neutral", "smile", "grin", "open", "o", "pout"}
    easings = {"linear", "ease", "ease_in", "ease_out", "ease_in_out", "spring"}
    effects = {"none", "heart", "sparkle", "question", "sweat", "star", "music"}
    gazes = {
        "cursor": ("cursor", 0.0, 0.0), "none": ("none", 0.0, 0.0), "center": ("directed", 0.0, 0.0),
        "left": ("directed", -0.9, 0.0), "right": ("directed", 0.9, 0.0),
        "up": ("directed", 0.0, -0.85), "down": ("directed", 0.0, 0.85),
    }
    expression = str(payload.get("expression", "idle")).lower()
    action = str(payload.get("action", "none")).lower()
    gaze = str(payload.get("gaze", "cursor")).lower()
    movement = str(payload.get("movement", "stay")).lower()
    if expression not in expressions or action not in actions or gaze not in gazes or movement not in movements:
        return None
    try:
        intensity = max(0.3, min(1.0, float(payload.get("intensity", 0.7))))
        duration = max(600, min(3500, int(payload.get("duration_ms", 1500))))
        offset_x = max(-6.0, min(6.0, float(payload.get("offset_x", 0))))
        offset_y = max(-6.0, min(6.0, float(payload.get("offset_y", 0))))
        move_distance = max(0.0, min(120.0, float(payload.get("move_distance", 0))))
    except (TypeError, ValueError):
        return None
    gaze_mode, look_x, look_y = gazes[gaze]
    result = {
        "expression": expression,
        "action": action.replace("_", "-"),
        "gazeMode": gaze_mode,
        "lookX": look_x,
        "lookY": look_y,
        "intensity": round(intensity, 2),
        "duration": duration,
        "offsetX": round(offset_x, 2),
        "offsetY": round(offset_y, 2),
        "movement": movement.replace("_", "-"),
        "moveDistance": round(move_distance, 1),
    }
    emotion_label = str(payload.get("emotion_label", "")).strip()[:24]
    eyes = str(payload.get("eyes", "")).lower()
    mouth = str(payload.get("mouth", "")).lower()
    easing = str(payload.get("easing", "ease_in_out")).lower()
    effect = str(payload.get("effect", "none")).lower()
    if eyes and eyes not in eye_poses:
        return None
    if mouth and mouth not in mouth_poses:
        return None
    if easing not in easings or effect not in effects:
        return None
    try:
        blush = max(0.0, min(1.0, float(payload.get("blush", 0.0))))
        repeat = max(1, min(3, int(payload.get("repeat", 1))))
    except (TypeError, ValueError):
        return None
    if emotion_label:
        result["emotionLabel"] = emotion_label
    if eyes:
        result["eyes"] = eyes.replace("_", "-")
    if mouth:
        result["mouth"] = mouth
    if "blush" in payload:
        result["blush"] = round(blush, 2)
    if effect != "none":
        result["effect"] = effect
    if action == "custom":
        def normalize_frames(name: str, x_limit: float, y_limit: float, rotate_limit: float, scale_min: float, scale_max: float, required: bool = False):
            raw_frames = payload.get(name)
            if raw_frames is None and not required:
                return None
            if not isinstance(raw_frames, list) or not 2 <= len(raw_frames) <= 7:
                raise ValueError("invalid keyframes")
            frames = []
            for frame in raw_frames:
                if not isinstance(frame, dict):
                    raise ValueError("invalid frame")
                frames.append({
                    "at": round(max(0.0, min(1.0, float(frame.get("at", 0)))), 3),
                    "x": round(max(-x_limit, min(x_limit, float(frame.get("x", 0)))), 2),
                    "y": round(max(-y_limit, min(y_limit, float(frame.get("y", 0)))), 2),
                    "rotate": round(max(-rotate_limit, min(rotate_limit, float(frame.get("rotate", 0)))), 2),
                    "scaleX": round(max(scale_min, min(scale_max, float(frame.get("scale_x", 1)))), 3),
                    "scaleY": round(max(scale_min, min(scale_max, float(frame.get("scale_y", 1)))), 3),
                })
            frames.sort(key=lambda frame: frame["at"])
            frames[0]["at"] = 0.0
            frames[-1]["at"] = 1.0
            if any(frames[index]["at"] <= frames[index - 1]["at"] for index in range(1, len(frames))):
                raise ValueError("unordered keyframes")
            return frames
        try:
            frames = normalize_frames("body_keyframes", 18, 40, 540, 0.72, 1.3, True)
            face_frames = normalize_frames("face_keyframes", 10, 10, 20, 0.75, 1.25)
            crest_frames = normalize_frames("crest_keyframes", 5, 7, 45, 0.7, 1.35)
        except (TypeError, ValueError):
            return None
        result["easing"] = easing.replace("_", "-")
        result["repeat"] = repeat
        result["keyframes"] = frames
        generated_layers = []
        if not face_frames:
            face_frames = [{
                "at": frame["at"],
                "x": round(max(-10.0, min(10.0, -frame["x"] * 0.22)), 2),
                "y": round(max(-10.0, min(10.0, frame["y"] * 0.08)), 2),
                "rotate": round(max(-20.0, min(20.0, -frame["rotate"] * 0.08)), 2),
                "scaleX": round(max(0.75, min(1.25, 2 - frame["scaleX"])), 3),
                "scaleY": round(max(0.75, min(1.25, 2 - frame["scaleY"])), 3),
            } for frame in frames]
            generated_layers.append("face")
        if not crest_frames:
            crest_frames = [{
                "at": frame["at"],
                "x": round(max(-5.0, min(5.0, -frame["x"] * 0.28)), 2),
                "y": round(max(-7.0, min(7.0, -frame["y"] * 0.12)), 2),
                "rotate": round(max(-45.0, min(45.0, -frame["rotate"] * 0.14 - frame["x"] * 1.1)), 2),
                "scaleX": round(max(0.7, min(1.35, 2 - frame["scaleX"])), 3),
                "scaleY": round(max(0.7, min(1.35, 2 - frame["scaleY"])), 3),
            } for frame in frames]
            generated_layers.append("crest")
        if face_frames:
            result["faceKeyframes"] = face_frames
        if crest_frames:
            result["crestKeyframes"] = crest_frames
        result["generatedLayers"] = generated_layers
        result["motionQuality"] = min(100, 45 + len(frames) * 5 + (0 if "face" in generated_layers else 15) + (0 if "crest" in generated_layers else 15) + (5 if effect != "none" else 0))
    return result


def extract_pet_action(text: str) -> tuple[str, dict | None, str]:
    match = re.search(r"<pet_action>\s*(\{.*?\})\s*</pet_action>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return text, None, ""
    raw = match.group(1).strip()
    clean = (text[:match.start()] + text[match.end():]).rstrip()
    try:
        motion = normalize_pet_action(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        motion = None
    return clean, motion, raw


class SettingsUpdate(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = Field(0.8, ge=0, le=2)
    max_tokens: int = Field(2048, ge=1, le=128000)
    context_message_limit: int = Field(20, ge=2, le=200)
    memory_limit: int = Field(5, ge=0, le=50)
    message_display_mode: Literal["markdown", "plain", "raw"] = "markdown"
    translation_mirror_url: str = ""
    vision_model: str = Field(default="", max_length=200)
    document_analysis_mode: Literal["fast", "deep"] = "fast"
    include_local_time: bool = True
    include_location_context: bool = False
    location_context: str = Field(default="", max_length=200)


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    system_prompt: str = ""
    avatar_data: str = Field(default="", max_length=2_000_000)
    greeting: str = ""
    background: str = ""
    personality: str = ""
    speaking_style: str = ""
    relationship: str = ""
    boundaries: str = ""
    example_dialogue: str = ""


class CharacterUpdate(CharacterCreate):
    pass


class ProactiveConfig(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(30, ge=1, le=1440)
    randomize_interval: bool = True
    random_min_minutes: int = Field(15, ge=1, le=1440)
    random_max_minutes: int = Field(60, ge=1, le=1440)
    history_weight: int = Field(15, ge=0, le=50)
    care_enabled: bool = True
    care_weight: int = Field(45, ge=0, le=100)
    max_tokens: int = Field(1024, ge=64, le=8192)
    news_enabled: bool = False
    rss_url: str = Field("https://www.chinanews.com.cn/rss/scroll-news.xml", max_length=1000, pattern=r"^https://")


class ProactiveRequest(BaseModel):
    character_id: int
    conversation_id: int | None = None


def compile_character_prompt(character) -> str:
    fields = [
        ("角色名称", character["name"]), ("角色简介", character["description"]),
        ("身份背景", character["background"]), ("性格", character["personality"]),
        ("说话方式", character["speaking_style"]), ("与用户的关系", character["relationship"]),
        ("行为边界", character["boundaries"]), ("示例对话", character["example_dialogue"]),
        ("补充指令", character["system_prompt"]),
    ]
    return "\n\n".join(f"【{label}】\n{value.strip()}" for label, value in fields if value.strip())


def proactive_delay_seconds(config) -> float:
    if config["randomize_interval"]:
        return random.uniform(config["random_min_minutes"], config["random_max_minutes"]) * 60
    return config["interval_minutes"] * 60


def reschedule_proactive_from_now() -> None:
    with connect() as db:
        config = db.execute("SELECT enabled,interval_minutes,randomize_interval,random_min_minutes,random_max_minutes FROM proactive_plugin WHERE id=1").fetchone()
        if config and config["enabled"]:
            db.execute("UPDATE proactive_plugin SET next_due=? WHERE id=1", (time.time() + proactive_delay_seconds(config),))


async def cancel_active_proactive_generation() -> None:
    task = PROACTIVE_GENERATION_TASK
    if task is None or task.done() or task is asyncio.current_task():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class ConversationCreate(BaseModel):
    character_id: int
    title: str = "新对话"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class ChatRequest(BaseModel):
    content: str = Field(min_length=1)
    pet_motion_enabled: bool = False
    pet_model: Literal["slime", "alice"] = "slime"
    recent_pet_motions: list[str] = Field(default_factory=list, max_length=5)


class MessageUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=200000)


class DocumentUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str


class PetStateUpdate(BaseModel):
    position_x: float
    position_y: float


class TranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    source: Literal["zh", "en"]
    target: Literal["zh", "en"]


class DiagnosticCommand(BaseModel):
    command: Literal["database_check", "database_checkpoint", "model_connection_test", "log_marker"]
    marker: str | None = Field(default=None, max_length=120)


class BackupRestoreRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)


class BackupCreateRequest(BaseModel):
    preferences: dict[str, str] = Field(default_factory=dict)


def rows(query: str, params: tuple = ()) -> list[dict]:
    with connect() as db:
        return [dict(row) for row in db.execute(query, params).fetchall()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value.lstrip("vV").split("-", 1)[0])
    return tuple(int(number) for number in numbers[:4]) or (0,)


def _content_range_total(value: str | None) -> int:
    match = re.search(r"/(\d+)$", (value or "").strip())
    return int(match.group(1)) if match else 0


def _content_range_start(value: str | None) -> int | None:
    match = re.match(r"bytes (\d+)-\d+/(?:\d+|\*)$", (value or "").strip())
    return int(match.group(1)) if match else None


def _create_backup_archive(prefix: str, app_version: str, preferences: dict[str, str] | None = None) -> dict:
    data_dir = database.DATA_DIR.resolve()
    backup_dir = data_dir / "backups"
    temp_dir = data_dir / "temp"
    backup_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "-", prefix)[:48] or "Yus-AI-backup"
    output = backup_dir / f"{safe_prefix}-{timestamp}.yus-backup"
    snapshot_db = temp_dir / f"backup-{uuid.uuid4().hex}.db"
    source = sqlite3.connect(database.DB_PATH)
    target = sqlite3.connect(snapshot_db)
    try:
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"数据库完整性检查失败：{integrity}")
        counts = {
            "characters": target.execute("SELECT COUNT(*) FROM characters").fetchone()[0],
            "conversations": target.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
            "messages": target.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "documents": target.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        }
    finally:
        target.close()
        source.close()
    manifest = {
        "format": "yus-ai-backup",
        "format_version": BACKUP_FORMAT_VERSION,
        "app_version": app_version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "includes": ["database", "documents", "character-exports", "desktop-preferences"],
        "excludes": ["api-logs", "temporary-files", "translation-models"],
        "counts": counts,
    }
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            safe_preferences = {
                key: value[:4000] for key, value in (preferences or {}).items()
                if key.startswith("yus-ai-") and len(key) <= 120 and isinstance(value, str)
            }
            archive.writestr("preferences.json", json.dumps(safe_preferences, ensure_ascii=False, indent=2))
            archive.write(snapshot_db, "database/yus_ai.db")
            for folder_name in ("documents", "character-exports"):
                folder = data_dir / folder_name
                if not folder.exists():
                    continue
                for item in folder.rglob("*"):
                    if item.is_file():
                        archive.write(item, (Path(folder_name) / item.relative_to(folder)).as_posix())
        return {
            "path": str(output), "filename": output.name, "size": output.stat().st_size,
            "sha256": _sha256(output), "created_at": manifest["created_at"], "counts": counts,
        }
    finally:
        snapshot_db.unlink(missing_ok=True)


def _restore_backup_archive(source_path: str, app_version: str) -> dict:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".yus-backup", ".zip"}:
        raise ValueError("请选择有效的 .yus-backup 备份文件")
    data_dir = database.DATA_DIR.resolve()
    staging = data_dir / "temp" / f"restore-{uuid.uuid4().hex}"
    rollback = data_dir / "temp" / f"restore-rollback-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > 10000 or sum(info.file_size for info in infos) > 2 * 1024 * 1024 * 1024:
                raise ValueError("备份文件内容过大或条目过多")
            names = {info.filename for info in infos}
            if "manifest.json" not in names or "database/yus_ai.db" not in names:
                raise ValueError("备份缺少清单或数据库")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "yus-ai-backup" or int(manifest.get("format_version", 0)) > BACKUP_FORMAT_VERSION:
                raise ValueError("备份格式不兼容，请升级软件后再恢复")
            for info in infos:
                normalized = Path(info.filename.replace("\\", "/"))
                parts = normalized.parts
                allowed = info.filename in {"manifest.json", "preferences.json", "database/yus_ai.db"} or (parts and parts[0] in {"documents", "character-exports"})
                if not allowed or normalized.is_absolute() or ".." in parts:
                    raise ValueError(f"备份包含不安全路径：{info.filename}")
                if info.is_dir() or info.filename in {"manifest.json", "preferences.json"}:
                    continue
                destination = staging / normalized
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as input_file, destination.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file, 1024 * 1024)
        restored_db = staging / "database" / "yus_ai.db"
        check = sqlite3.connect(restored_db)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_errors = check.execute("PRAGMA foreign_key_check").fetchmany(1)
            if integrity != "ok" or foreign_errors:
                raise ValueError("备份数据库完整性检查未通过")
        finally:
            check.close()
        if "preferences.json" in names:
            with zipfile.ZipFile(source) as preferences_archive:
                preferences = json.loads(preferences_archive.read("preferences.json"))
        else:
            preferences = {}
        if not isinstance(preferences, dict):
            preferences = {}
        preferences = {key: value for key, value in preferences.items() if isinstance(key, str) and key.startswith("yus-ai-") and isinstance(value, str)}
        safety_backup = _create_backup_archive("Yus-AI-pre-restore", app_version)
        rollback.mkdir(parents=True, exist_ok=False)
        for folder_name in ("documents", "character-exports"):
            current = data_dir / folder_name
            replacement = staging / folder_name
            old = rollback / folder_name
            if current.exists():
                os.replace(current, old)
                moved.append((old, current))
            if replacement.exists():
                os.replace(replacement, current)
                installed.append(current)
        for suffix in ("-wal", "-shm"):
            Path(f"{database.DB_PATH}{suffix}").unlink(missing_ok=True)
        os.replace(restored_db, database.DB_PATH)
        init_db()
        shutil.rmtree(rollback, ignore_errors=True)
        return {
            "ok": True, "requires_restart": True, "source": str(source),
            "safety_backup": safety_backup["path"], "manifest": manifest, "preferences": preferences,
        }
    except Exception:
        for current in installed:
            if current.exists():
                shutil.rmtree(current, ignore_errors=True)
        for old, current in reversed(moved):
            if old.exists() and not current.exists():
                os.replace(old, current)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(rollback, ignore_errors=True)


def _release_from_github_page(page_url: str, page_html: str, assets_html: str) -> dict:
    tag_match = re.search(r"/releases/tag/([^/?#\"'\s<>]+)", page_url + "\n" + page_html)
    if not tag_match:
        raise ValueError("GitHub 发布页缺少版本号")
    tag = unquote(tag_match.group(1))
    asset_match = re.search(
        r'<li\b[^>]*>(?:(?!</li>).)*?href="(?P<url>/qtys/Yu-s-Ai-plugin-platform/releases/download/[^\"]*?x64[^\"]*?setup\.exe)"'
        r'(?:(?!</li>).)*?sha256:(?P<digest>[0-9a-fA-F]{64})'
        r'(?:(?!</li>).)*?>(?P<size>[0-9.]+)\s*(?P<unit>[KMGT]?B)</span>'
        r'(?:(?!</li>).)*?datetime="(?P<published>[^"]+)"',
        assets_html, re.IGNORECASE | re.DOTALL,
    )
    if not asset_match:
        raise ValueError("GitHub 发布页没有可校验的 Windows x64 安装包")
    units = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
    asset_path = unescape(asset_match.group("url"))
    asset_name = Path(unquote(asset_path.rsplit("/", 1)[-1])).name
    title_match = re.search(r"<title>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
    title = unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else tag
    title = title.split(" · ", 1)[0]
    return {
        "version": tag.lstrip("vV"),
        "name": title,
        "notes": "GitHub API 当前不可用，已通过官方发布页完成安全检查。完整更新说明请打开发布页查看。",
        "published_at": asset_match.group("published"),
        "release_url": f"https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/tag/{tag}",
        "asset": {
            "name": asset_name,
            "size": int(float(asset_match.group("size")) * units[asset_match.group("unit").upper()]),
            "size_exact": False,
            "url": f"https://github.com{asset_path}",
            "digest": f"sha256:{asset_match.group('digest').lower()}",
        },
    }


async def _github_get_with_retry(client: httpx.AsyncClient, url: str, headers: dict, attempts: int = 3) -> httpx.Response:
    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url, headers=headers)
            if response.status_code in {500, 502, 503, 504} and attempt < attempts:
                logger.info("update_metadata_retry attempt=%s/%s status=%s", attempt, attempts, response.status_code)
            else:
                response.raise_for_status()
                return response
        except httpx.RequestError as exc:
            if attempt >= attempts:
                raise
            logger.info("update_metadata_retry attempt=%s/%s error_type=%s", attempt, attempts, type(exc).__name__)
        await asyncio.sleep(0.6 * attempt)
    raise RuntimeError("更新信息请求重试异常结束")


async def _latest_release() -> dict:
    headers = {
        "Accept": "application/vnd.github+json", "User-Agent": "Yus-AI-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # GitHub traffic may be routed through a desktop tunnel (for example Meta),
    # whose synthetic DNS/proxy environment is not usable from the packaged
    # Python sidecar.  The model clients already bypass that inherited
    # environment; keep the updater on the same reliable direct route.
    timeout = httpx.Timeout(connect=15, read=25, write=15, pool=15)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        try:
            response = await _github_get_with_retry(client, GITHUB_LATEST_RELEASE_URL, headers, attempts=2)
            release = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as api_error:
            logger.info("update_api_unavailable fallback=github_page error_type=%s", type(api_error).__name__)
            page = await _github_get_with_retry(client, GITHUB_RELEASES_LATEST_URL, {"User-Agent": "Yus-AI-Updater"})
            tag_match = re.search(r"/releases/tag/([^/?#\"'\s<>]+)", str(page.url) + "\n" + page.text)
            if not tag_match:
                raise ValueError("GitHub 发布页缺少版本号") from api_error
            tag = unquote(tag_match.group(1))
            assets = await _github_get_with_retry(client,
                f"https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/expanded_assets/{tag}",
                headers={"User-Agent": "Yus-AI-Updater"},
            )
            return _release_from_github_page(str(page.url), page.text, assets.text)
    assets = release.get("assets") or []
    asset = next((item for item in assets if item.get("name", "").lower().endswith("setup.exe") and "x64" in item.get("name", "").lower()), None)
    if not asset:
        raise ValueError("最新版本没有 Windows x64 安装包")
    return {
        "version": str(release.get("tag_name", "")).lstrip("vV"),
        "name": release.get("name") or release.get("tag_name") or "最新版本",
        "notes": str(release.get("body") or "")[:12000],
        "published_at": release.get("published_at"),
        "release_url": release.get("html_url"),
        "asset": {
            "name": Path(str(asset.get("name", "setup.exe"))).name,
            "size": int(asset.get("size") or 0),
            "size_exact": True,
            "url": asset.get("browser_download_url"),
            "digest": asset.get("digest") or "",
        },
    }


async def _release_for_update(max_age_seconds: float) -> dict:
    global UPDATE_RELEASE_CACHE
    async with UPDATE_RELEASE_LOCK:
        if UPDATE_RELEASE_CACHE and time.monotonic() - UPDATE_RELEASE_CACHE[0] < max_age_seconds:
            return UPDATE_RELEASE_CACHE[1]
        release = await _latest_release()
        asset = release.get("asset") or {}
        url = str(asset.get("url") or "")
        name = str(asset.get("name") or "")
        digest = str(asset.get("digest") or "")
        if (not url.startswith("https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/download/")
                or not name.lower().endswith("x64-setup.exe") or Path(name).name != name
                or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
            raise ValueError("GitHub 发布信息缺少可信的安装包地址或 SHA-256 摘要")
        UPDATE_RELEASE_CACHE = (time.monotonic(), release)
        return release


class _UpdateIncompleteError(ValueError):
    pass


class _UpdateOversizeError(ValueError):
    pass


def _update_error_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "连接 GitHub 超时"
    if isinstance(exc, httpx.ConnectError):
        return "无法连接 GitHub"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GitHub 返回 HTTP {exc.response.status_code}"
    return str(exc) or type(exc).__name__


@asynccontextmanager
async def lifespan(_: FastAPI):
    global UPDATE_RELEASE_CACHE
    UPDATE_RELEASE_CACHE = None
    configure_logging()
    init_db()
    logger.info("backend_started version=0.15.12")
    yield
    logger.info("backend_stopped")


app = FastAPI(title="Yu's AI API", version="0.15.12", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://tauri.localhost", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_log(request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed = (time.perf_counter() - started) * 1000
        logger.info("request method=%s path=%s status=%s duration_ms=%.1f", request.method, request.url.path, response.status_code, elapsed)
        return response
    except Exception:
        logger.exception("request_failed method=%s path=%s", request.method, request.url.path)
        raise


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/diagnostics/status")
def diagnostic_status():
    with connect() as db:
        character_count = db.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
        conversation_count = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        message_count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    return {
        "status": "ok",
        "version": app.version,
        "database": {"characters": character_count, "conversations": conversation_count, "messages": message_count},
        "log_file": str(LOG_FILE),
    }


@app.get("/api/diagnostics/logs")
def diagnostic_logs(lines: int = 200):
    line_count = max(1, min(lines, 1000))
    if not LOG_FILE.exists():
        return {"lines": [], "count": 0}
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as file:
        content = list(deque(file, maxlen=line_count))
    return {"lines": [line.rstrip("\r\n") for line in content], "count": len(content)}


@app.post("/api/diagnostics/commands")
async def diagnostic_command(payload: DiagnosticCommand):
    logger.info("diagnostic_command command=%s", payload.command)
    if payload.command == "database_check":
        with connect() as db:
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
        return {"ok": result == "ok", "result": result}
    if payload.command == "database_checkpoint":
        with connect() as db:
            result = list(db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
        return {"ok": True, "result": result}
    if payload.command == "log_marker":
        marker = (payload.marker or "manual diagnostic marker").replace("\n", " ").replace("\r", " ")
        logger.warning("diagnostic_marker marker=%s", marker)
        return {"ok": True, "result": "marker_written"}

    with connect() as db:
        setting = db.execute("SELECT base_url, api_key FROM settings WHERE id=1").fetchone()
    if not setting["api_key"]:
        raise HTTPException(400, "尚未配置 API Key")
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(
                f"{setting['base_url'].rstrip('/')}/models",
                headers={"Authorization": f"Bearer {setting['api_key']}"},
            )
        logger.info("model_connection_test status=%s", response.status_code)
        return {"ok": response.is_success, "status_code": response.status_code}
    except httpx.HTTPError as exc:
        logger.warning("model_connection_test_failed error_type=%s", type(exc).__name__)
        return {"ok": False, "error": type(exc).__name__}


@app.get("/api/system/update")
async def check_system_update():
    try:
        release = await _release_for_update(60)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "update_check_failed error_type=%s detail=%r cause=%r",
            type(exc).__name__, str(exc), exc.__cause__,
        )
        if isinstance(exc, httpx.ConnectError):
            detail = "无法连接 GitHub，请检查网络或代理设置"
        elif isinstance(exc, httpx.TimeoutException):
            detail = "连接 GitHub 超时，请稍后重试"
        else:
            detail = f"无法检查更新：{str(exc) or type(exc).__name__}"
        raise HTTPException(502, detail) from exc
    release["current_version"] = app.version
    release["available"] = _version_tuple(release["version"]) > _version_tuple(app.version)
    logger.info("update_checked current=%s latest=%s available=%s", app.version, release["version"], release["available"])
    return release


@app.post("/api/system/update/download")
async def download_system_update():
    async def generate():
        async with UPDATE_DOWNLOAD_LOCK:
            phase = "获取发布信息"
            try:
                # Reuse the server-validated result from the check button. A second
                # GitHub metadata request was the cause of intermittent failures.
                release = await _release_for_update(30 * 60)
                if _version_tuple(release["version"]) <= _version_tuple(app.version):
                    yield json.dumps({"error": "当前已经是最新版本"}, ensure_ascii=False) + "\n"
                    return
                asset = release["asset"]
                url = asset["url"]
                update_dir = database.DATA_DIR / "updates"
                update_dir.mkdir(parents=True, exist_ok=True)
                destination = update_dir / asset["name"]
                partial = destination.with_suffix(destination.suffix + ".part")
                expected_size = int(asset.get("size") or 0) if asset.get("size_exact", True) else 0
                release_size = expected_size
                expected_digest = asset["digest"].split(":", 1)[1].lower()

                if destination.exists() and _sha256(destination).lower() == expected_digest:
                    yield json.dumps({"stage": "complete", "percent": 100, "path": str(destination), "sha256": expected_digest, "version": release["version"]}, ensure_ascii=False) + "\n"
                    return
                if partial.exists():
                    if _sha256(partial).lower() == expected_digest:
                        os.replace(partial, destination)
                        yield json.dumps({"stage": "complete", "percent": 100, "path": str(destination), "sha256": expected_digest, "version": release["version"]}, ensure_ascii=False) + "\n"
                        return
                    if expected_size and partial.stat().st_size >= expected_size:
                        partial.unlink()

                phase = "下载安装包"
                timeout = httpx.Timeout(connect=20, read=45, write=30, pool=20)
                max_attempts = 3
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                    for attempt in range(1, max_attempts + 1):
                        resume_at = partial.stat().st_size if partial.exists() else 0
                        headers = {"User-Agent": "Yus-AI-Updater"}
                        if resume_at:
                            headers["Range"] = f"bytes={resume_at}-"
                        try:
                            async with client.stream("GET", url, headers=headers) as response:
                                range_header = response.headers.get("Content-Range")
                                range_total = _content_range_total(range_header)
                                if response.status_code == 416:
                                    if resume_at and range_total == resume_at:
                                        expected_size = range_total
                                        logger.info("update_partial_already_complete bytes=%s", resume_at)
                                        break
                                    partial.unlink(missing_ok=True)
                                    raise _UpdateIncompleteError("断点位置与服务器不一致，已从头重试")
                                response.raise_for_status()
                                if response.status_code not in {200, 206}:
                                    raise ValueError(f"安装包服务器返回意外状态 {response.status_code}")
                                if response.status_code == 206 and _content_range_start(range_header) != resume_at:
                                    partial.unlink(missing_ok=True)
                                    raise _UpdateIncompleteError("服务器返回的断点位置不一致，已从头重试")
                                append = resume_at > 0 and response.status_code == 206
                                if range_total:
                                    if release_size and range_total != release_size:
                                        raise ValueError("服务器文件大小与 GitHub 发布信息不一致")
                                    expected_size = range_total
                                elif response.status_code == 200 and response.headers.get("Content-Length", "").isdigit():
                                    if release_size and int(response.headers["Content-Length"]) != release_size:
                                        raise ValueError("服务器文件大小与 GitHub 发布信息不一致")
                                    expected_size = int(response.headers["Content-Length"])
                                downloaded = resume_at if append else 0
                                with partial.open("ab" if append else "wb") as file:
                                    async for chunk in response.aiter_bytes(256 * 1024):
                                        if not chunk:
                                            continue
                                        file.write(chunk)
                                        downloaded += len(chunk)
                                        if expected_size and downloaded > expected_size:
                                            raise _UpdateOversizeError("安装包大小超过发布信息，已中止下载")
                                        percent = min(99, int(downloaded * 100 / expected_size)) if expected_size else 0
                                        yield json.dumps({"stage": "downloading", "percent": percent, "downloaded": downloaded, "total": expected_size, "resumed": append}, ensure_ascii=False) + "\n"
                            if expected_size and partial.stat().st_size != expected_size:
                                raise _UpdateIncompleteError("连接提前结束，安装包尚未下载完整")
                            break
                        except (httpx.RequestError, httpx.HTTPStatusError, _UpdateIncompleteError) as exc:
                            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {429, 500, 502, 503, 504}
                            if not retryable or attempt >= max_attempts:
                                raise
                            downloaded = partial.stat().st_size if partial.exists() else 0
                            logger.warning("update_download_retry attempt=%s/%s bytes=%s error_type=%s", attempt, max_attempts, downloaded, type(exc).__name__)
                            yield json.dumps({"stage": "retrying", "percent": min(99, int(downloaded * 100 / expected_size)) if expected_size else 0, "downloaded": downloaded, "total": expected_size, "resumed": bool(downloaded), "attempt": attempt, "max_attempts": max_attempts, "reason": _update_error_detail(exc)}, ensure_ascii=False) + "\n"
                            await asyncio.sleep(0.8 * attempt)

                digest = _sha256(partial)
                if digest.lower() != expected_digest:
                    partial.unlink(missing_ok=True)
                    raise ValueError("安装包 SHA-256 校验失败，文件已删除")
                os.replace(partial, destination)
                logger.info("update_downloaded version=%s bytes=%s sha256=%s", release["version"], destination.stat().st_size, digest)
                yield json.dumps({"stage": "complete", "percent": 100, "path": str(destination), "sha256": digest, "version": release["version"]}, ensure_ascii=False) + "\n"
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, _UpdateOversizeError):
                    partial.unlink(missing_ok=True)
                logger.warning("update_download_failed phase=%s error_type=%s detail=%s", phase, type(exc).__name__, str(exc))
                suffix = "；已保留下载进度，可重试" if phase == "下载安装包" and isinstance(exc, (httpx.RequestError, _UpdateIncompleteError)) else ""
                yield json.dumps({"error": f"{phase}失败：{_update_error_detail(exc)}{suffix}"}, ensure_ascii=False) + "\n"
    return StreamingResponse(generate(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache, no-transform"})


@app.post("/api/system/backups")
async def create_system_backup(payload: BackupCreateRequest | None = None):
    async with SYSTEM_OPERATION_LOCK:
        try:
            preferences = payload.preferences if payload else {}
            if len(preferences) > 100:
                raise ValueError("桌面偏好条目过多")
            result = await asyncio.to_thread(_create_backup_archive, "Yus-AI-backup", app.version, preferences)
        except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
            logger.warning("backup_create_failed error_type=%s detail=%s", type(exc).__name__, str(exc))
            raise HTTPException(500, f"创建备份失败：{exc}") from exc
    logger.info("backup_created filename=%s bytes=%s", result["filename"], result["size"])
    return result


@app.post("/api/system/backups/restore")
async def restore_system_backup(payload: BackupRestoreRequest):
    if MODEL_GENERATION_LOCK.locked():
        raise HTTPException(409, "模型正在生成内容，请等待对话结束后再恢复")
    async with SYSTEM_OPERATION_LOCK:
        try:
            result = await asyncio.to_thread(_restore_backup_archive, payload.path, app.version)
        except (OSError, sqlite3.Error, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            logger.warning("backup_restore_failed error_type=%s detail=%s", type(exc).__name__, str(exc))
            raise HTTPException(400, f"恢复备份失败：{exc}") from exc
    logger.info("backup_restored source=%s safety_backup=%s", result["source"], result["safety_backup"])
    return result


@app.get("/api/settings")
def get_settings():
    setting = rows("SELECT * FROM settings WHERE id = 1")[0]
    setting["api_key"] = "" if not setting["api_key"] else "••••••••"
    setting["include_local_time"] = bool(setting["include_local_time"])
    setting["include_location_context"] = bool(setting["include_location_context"])
    return setting


@app.put("/api/settings")
def update_settings(payload: SettingsUpdate):
    with connect() as db:
        current_key = db.execute("SELECT api_key FROM settings WHERE id = 1").fetchone()[0]
        api_key = current_key if payload.api_key == "••••••••" else payload.api_key
        db.execute(
            "UPDATE settings SET base_url=?, api_key=?, model=?, temperature=?, max_tokens=?, context_message_limit=?, memory_limit=?, message_display_mode=?, translation_mirror_url=?, vision_model=?, document_analysis_mode=?, include_local_time=?, include_location_context=?, location_context=? WHERE id=1",
            (payload.base_url.rstrip("/"), api_key, payload.model, payload.temperature, payload.max_tokens, payload.context_message_limit, payload.memory_limit, payload.message_display_mode, payload.translation_mirror_url.strip().rstrip("/"), payload.vision_model.strip(), payload.document_analysis_mode, payload.include_local_time, payload.include_location_context, payload.location_context.strip()),
        )
    return {"ok": True}


@app.get("/api/pet/state")
def get_pet_state():
    return rows("SELECT position_x, position_y FROM pet_state WHERE id = 1")[0]


@app.get("/api/plugins/proactive")
def get_proactive_config():
    config = rows("SELECT * FROM proactive_plugin WHERE id=1")[0]
    config["enabled"] = bool(config["enabled"])
    config["news_enabled"] = bool(config["news_enabled"])
    config["randomize_interval"] = bool(config["randomize_interval"])
    config["care_enabled"] = bool(config["care_enabled"])
    return config


@app.put("/api/plugins/proactive")
def update_proactive_config(payload: ProactiveConfig):
    if payload.random_min_minutes > payload.random_max_minutes:
        raise HTTPException(422, "随机发言的最短等待不能大于最长等待")
    with connect() as db:
        current = db.execute("SELECT enabled,interval_minutes,randomize_interval,random_min_minutes,random_max_minutes,next_due FROM proactive_plugin WHERE id=1").fetchone()
        now = time.time()
        next_due = current["next_due"]
        timing_changed = (payload.interval_minutes != current["interval_minutes"] or
                          payload.randomize_interval != bool(current["randomize_interval"]) or
                          payload.random_min_minutes != current["random_min_minutes"] or
                          payload.random_max_minutes != current["random_max_minutes"])
        if payload.enabled and (not current["enabled"] or timing_changed):
            next_due = now + proactive_delay_seconds(payload.model_dump())
        elif not payload.enabled:
            next_due = 0
        db.execute("UPDATE proactive_plugin SET enabled=?, interval_minutes=?, max_tokens=?, news_enabled=?, rss_url=?, randomize_interval=?,random_min_minutes=?,random_max_minutes=?,history_weight=?,care_enabled=?,care_weight=?,next_due=? WHERE id=1",
                   (payload.enabled, payload.interval_minutes, payload.max_tokens, payload.news_enabled, payload.rss_url, payload.randomize_interval, payload.random_min_minutes, payload.random_max_minutes, payload.history_weight, payload.care_enabled, payload.care_weight, next_due))
    return {"ok": True}


@app.post("/api/plugins/proactive/generate")
async def proactive_generate(payload: ProactiveRequest):
    global PROACTIVE_GENERATION_TASK
    if MODEL_GENERATION_LOCK.locked():
        return {"skipped": True, "reason": "chat_busy"}
    now = datetime.now().astimezone()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        config = dict(db.execute("SELECT * FROM proactive_plugin WHERE id=1").fetchone())
        if not config["enabled"] or now.hour < 7 or now.hour >= 23:
            return {"skipped": True, "reason": "disabled_or_quiet_hours"}
        if time.time() < config["next_due"]:
            return {"skipped": True, "reason": "cooldown"}
        character = db.execute("SELECT * FROM characters WHERE id=?", (payload.character_id,)).fetchone()
        if not character:
            raise HTTPException(404, "角色不存在")
        conversation_id = payload.conversation_id
        if conversation_id is not None and not db.execute("SELECT id FROM conversations WHERE id=? AND character_id=?", (conversation_id, payload.character_id)).fetchone():
            conversation_id = None  # Deleted/stale selection must not permanently block pet speech.
        setting = dict(db.execute("SELECT * FROM settings WHERE id=1").fetchone())
        if not setting["api_key"]:
            return {"skipped": True, "reason": "missing_api_key"}
        history = [dict(row) for row in db.execute("SELECT role,content FROM messages WHERE conversation_id=? AND origin!='proactive' ORDER BY id DESC LIMIT 6", (conversation_id,)).fetchall()][::-1]
        # Reserve cooldown before network I/O so repeated requests cannot spend extra tokens.
        db.execute("UPDATE proactive_plugin SET next_due=? WHERE id=1", (time.time() + proactive_delay_seconds(config),))
        prompt = compile_character_prompt(character)
    try:
        async with MODEL_GENERATION_LOCK:
            PROACTIVE_GENERATION_TASK = asyncio.current_task()
            try:
                text, kind, sources, usage, care_slot = await generate_proactive(setting, config, prompt, history, now.isoformat(timespec="seconds"))
            finally:
                PROACTIVE_GENERATION_TASK = None
    except asyncio.CancelledError:
        logger.info("proactive_cancelled reason=manual_chat_started character_id=%s", payload.character_id)
        return {"skipped": True, "reason": "manual_chat_started"}
    except (httpx.HTTPError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        failure_count = config["failure_count"] + 1
        delay = 60 if failure_count <= 2 else max(300, config["interval_minutes"] * 60)
        detail = "模型未返回正文，请提高主动发言 token 上限（推理也可能占用额度）" if isinstance(exc, EmptyProactiveReply) else "主动发言请求失败，请检查模型服务和配置"
        logger.warning("proactive_model_failed error_type=%s finish_reason=%s retry_seconds=%s", type(exc).__name__, getattr(exc, "finish_reason", "unknown"), delay)
        with connect() as db:
            db.execute("UPDATE proactive_plugin SET failure_count=?,last_error=?,next_due=?,total_tokens=total_tokens+? WHERE id=1", (failure_count, detail, time.time() + delay, getattr(exc, "usage", 0)))
        raise HTTPException(502, f"{detail}；约 {delay} 秒后自动重试，连续失败会降低重试频率") from exc
    with connect() as db:
        if not db.execute("SELECT id FROM characters WHERE id=?", (payload.character_id,)).fetchone():
            return {"skipped": True, "reason": "character_deleted"}
        if conversation_id is None:
            conversation_id = db.execute("INSERT INTO conversations(character_id,title) VALUES (?,?)", (payload.character_id, "桌宠主动互动")).lastrowid
        elif not db.execute("SELECT id FROM conversations WHERE id=?", (conversation_id,)).fetchone():
            return {"skipped": True, "reason": "conversation_deleted"}
        source_text = "\n\n" + "\n".join(f"来源：{source['title']} {source['url']}" for source in sources) if sources else ""
        db.execute("INSERT INTO messages(conversation_id,role,content,origin) VALUES (?,'assistant',?,'proactive')", (conversation_id, text + source_text))
        db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
        db.execute("UPDATE proactive_plugin SET last_content=?,total_tokens=total_tokens+?,failure_count=0,last_error='',last_care_slot=CASE WHEN ?='' THEN last_care_slot ELSE ? END WHERE id=1", (text, usage, care_slot, care_slot))
    logger.info("proactive_generated character_id=%s kind=%s total_tokens=%s", payload.character_id, kind, usage)
    return {"content": text, "kind": kind, "sources": sources, "total_tokens": usage, "conversation_id": conversation_id}


@app.put("/api/pet/state")
def update_pet_state(payload: PetStateUpdate):
    with connect() as db:
        db.execute(
            "UPDATE pet_state SET position_x=?, position_y=? WHERE id=1",
            (payload.position_x, payload.position_y),
        )
    return {"ok": True}


@app.get("/api/translation/packages")
def translation_packages():
    return package_status()


@app.post("/api/translation/packages/{source}/{target}")
async def download_translation_package(source: str, target: str):
    try:
        mirror = rows("SELECT translation_mirror_url FROM settings WHERE id=1")[0]["translation_mirror_url"]
        await install_package(source, target, mirror_url=mirror)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("translation_model_download_failed pair=%s-%s error=%s", source, target, type(exc).__name__)
        raise HTTPException(502, f"语言包下载失败：{type(exc).__name__}") from exc
    return {"ok": True}


@app.post("/api/translation")
def translate(payload: TranslationRequest):
    if payload.source == payload.target:
        return {"translation": payload.text}
    try:
        return {"translation": translate_text(payload.text, payload.source, payload.target)}
    except LookupError as exc:
        logger.warning("translation_package_missing pair=%s-%s", payload.source, payload.target)
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("translation_failed pair=%s-%s error=%s", payload.source, payload.target, type(exc).__name__)
        raise HTTPException(500, f"本地翻译运行失败：{type(exc).__name__}") from exc


@app.get("/api/characters")
def list_characters():
    return rows("SELECT * FROM characters ORDER BY id DESC")


@app.post("/api/characters", status_code=201)
def create_character(payload: CharacterCreate):
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO characters(name, description, system_prompt, avatar_data, greeting, background,
            personality, speaking_style, relationship, boundaries, example_dialogue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(getattr(payload, key) for key in ("name", "description", "system_prompt", "avatar_data", "greeting", "background", "personality", "speaking_style", "relationship", "boundaries", "example_dialogue")),
        )
        character_id = cursor.lastrowid
        return dict(db.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone())


@app.put("/api/characters/{character_id}")
def update_character(character_id: int, payload: CharacterUpdate):
    keys = ("name", "description", "system_prompt", "avatar_data", "greeting", "background", "personality", "speaking_style", "relationship", "boundaries", "example_dialogue")
    with connect() as db:
        cursor = db.execute(f"UPDATE characters SET {', '.join(f'{key}=?' for key in keys)} WHERE id=?", tuple(getattr(payload, key) for key in keys) + (character_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "角色不存在")
        return dict(db.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone())


@app.delete("/api/characters/{character_id}")
def delete_character(character_id: int):
    with connect() as db:
        cursor = db.execute("DELETE FROM characters WHERE id=?", (character_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "角色不存在")
    return {"ok": True}


@app.post("/api/translation/packages/{source}/{target}/stream")
async def stream_translation_package(source: str, target: str):
    async def events():
        queue: asyncio.Queue[dict] = asyncio.Queue()
        mirror = rows("SELECT translation_mirror_url FROM settings WHERE id=1")[0]["translation_mirror_url"]
        task = asyncio.create_task(install_package(source, target, queue.put_nowait, mirror_url=mirror))
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                yield json.dumps(event, ensure_ascii=False) + "\n"
            except asyncio.TimeoutError:
                continue
        try:
            await task
        except ValueError as exc:
            yield json.dumps({"stage": "error", "error": str(exc)}, ensure_ascii=False) + "\n"
        except httpx.HTTPError as exc:
            logger.warning("translation_model_download_failed pair=%s-%s error=%s", source, target, type(exc).__name__)
            yield json.dumps({"stage": "error", "error": f"语言包下载失败：{type(exc).__name__}"}, ensure_ascii=False) + "\n"
        except Exception as exc:
            logger.exception("translation_model_install_failed pair=%s-%s", source, target)
            yield json.dumps({"stage": "error", "error": f"语言包安装失败：{type(exc).__name__}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/api/conversations")
def list_conversations(character_id: int | None = None):
    query = "SELECT c.*, ch.name AS character_name FROM conversations c JOIN characters ch ON ch.id=c.character_id"
    params: tuple = ()
    if character_id is not None:
        query += " WHERE c.character_id=?"
        params = (character_id,)
    return rows(query + " ORDER BY c.updated_at DESC, c.id DESC", params)


@app.post("/api/conversations", status_code=201)
def create_conversation(payload: ConversationCreate):
    with connect() as db:
        if not db.execute("SELECT 1 FROM characters WHERE id=?", (payload.character_id,)).fetchone():
            raise HTTPException(404, "角色不存在")
        cursor = db.execute(
            "INSERT INTO conversations(character_id, title) VALUES (?, ?)",
            (payload.character_id, payload.title),
        )
        conversation_id = cursor.lastrowid
        greeting = db.execute("SELECT greeting FROM characters WHERE id=?", (payload.character_id,)).fetchone()[0]
        if greeting.strip():
            db.execute("INSERT INTO messages(conversation_id, role, content) VALUES (?, 'assistant', ?)", (conversation_id, greeting.strip()))
        return dict(db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone())


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: int):
    with connect() as db:
        stored_documents = [row["stored_name"] for row in db.execute("SELECT stored_name FROM documents WHERE conversation_id=?", (conversation_id,)).fetchall()]
        cursor = db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "会话不存在")
    for stored_name in stored_documents:
        remove_original(stored_name)
    return {"ok": True}


@app.put("/api/conversations/{conversation_id}/title")
def rename_conversation(conversation_id: int, payload: ConversationRename):
    with connect() as db:
        cursor = db.execute("UPDATE conversations SET title=? WHERE id=?", (payload.title.strip(), conversation_id))
        if cursor.rowcount == 0:
            raise HTTPException(404, "会话不存在")
    return {"ok": True, "title": payload.title.strip()}


def relevant_memories(db, character_id: int, query: str, limit: int) -> list[str]:
    if limit <= 0:
        return []
    candidates = db.execute("SELECT content FROM memories WHERE character_id=? ORDER BY id DESC LIMIT 200", (character_id,)).fetchall()
    query_chars = set(query.lower().replace(" ", ""))
    ranked = sorted(candidates, key=lambda row: len(query_chars & set(row["content"].lower().replace(" ", ""))), reverse=True)
    return [row["content"] for row in ranked[:limit]]


def maybe_store_memory(db, character_id: int, message_id: int, content: str) -> None:
    markers = ("请记住", "记住我", "我叫", "我是", "我喜欢", "我不喜欢", "我的生日", "我的工作", "我住在")
    if any(marker in content for marker in markers) and len(content) <= 500:
        db.execute("INSERT OR IGNORE INTO memories(character_id, content, source_message_id) VALUES (?, ?, ?)", (character_id, content.strip(), message_id))


@app.get("/api/conversations/{conversation_id}/messages")
def list_messages(conversation_id: int):
    return rows("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))


@app.get("/api/conversations/{conversation_id}/documents")
def list_documents(conversation_id: int):
    return rows("SELECT id,conversation_id,filename,char_count,image_count,summary,status,analysis_mode,analysis_stage,progress_current,progress_total,created_at FROM documents WHERE conversation_id=? ORDER BY id DESC", (conversation_id,))


@app.post("/api/conversations/{conversation_id}/documents", status_code=201)
def upload_document(conversation_id: int, payload: DocumentUpload):
    try:
        raw, suffix = decode_document(payload.filename, payload.content_base64)
        pages = extract_pages(raw, suffix)
        chunks = chunk_pages(pages)
        visuals = extract_visuals(raw, suffix)
    except (ValueError, OSError, UnicodeError, KeyError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, str(exc) or "文档解析失败") from exc
    if not chunks and not visuals:
        raise HTTPException(422, "文档中没有可读取的文字或受支持的图片")
    stored_name = save_original(payload.filename, raw, suffix)
    try:
        with connect() as db:
            if not db.execute("SELECT id FROM conversations WHERE id=?", (conversation_id,)).fetchone():
                raise HTTPException(404, "会话不存在")
            cursor = db.execute(
                "INSERT INTO documents(conversation_id,filename,stored_name,char_count,image_count,status) VALUES (?,?,?,?,?, 'ready')",
                (conversation_id, payload.filename, stored_name, sum(len(item["content"]) for item in chunks), len(visuals)),
            )
            document_id = cursor.lastrowid
            db.executemany(
                "INSERT INTO document_chunks(document_id,chunk_index,page_number,content) VALUES (?,?,?,?)",
                [(document_id, index, item["page_number"], item["content"]) for index, item in enumerate(chunks)],
            )
            return dict(db.execute("SELECT id,conversation_id,filename,char_count,image_count,summary,status,analysis_mode,analysis_stage,progress_current,progress_total,created_at FROM documents WHERE id=?", (document_id,)).fetchone())
    except Exception:
        remove_original(stored_name)
        raise


async def model_summary(
    setting,
    filename: str,
    text: str,
    instruction: str,
    visuals: list[dict] | None = None,
    allow_reasoning_retry: bool = True,
) -> str:
    headers = {"Authorization": f"Bearer {setting['api_key']}", "Content-Type": "application/json"}
    user_content: str | list[dict] = f"文档：{filename}\n{instruction}\n\n{text}"
    if visuals:
        parts: list[dict] = [{"type": "text", "text": user_content}]
        for item in visuals:
            page = f"（PDF 第 {item['page_number']} 页）" if item["page_number"] else ""
            parts.extend([
                {"type": "text", "text": f"图片 {item['name']}{page}"},
                {"type": "image_url", "image_url": {"url": item["data_url"], "detail": "high"}},
            ])
        user_content = parts
    model = setting.get("vision_model", "").strip() if visuals else setting["model"]
    if visuals and not model:
        model = "deepseek-flash" if "api.deepseek.com" in setting["base_url"].lower() else setting["model"]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": "你负责忠实理解文档。不得补充文档中没有的信息。"},
                     {"role": "user", "content": user_content}],
        "temperature": 0.2,
        # Reasoning models can spend several thousand tokens before emitting
        # either a text or visual document summary.
        # DeepSeek currently accepts up to 384K completion tokens.  Visual
        # document analysis deliberately uses the provider ceiling rather
        # than imposing a smaller application-side limit.
        "max_tokens": (
            (8192 if setting.get("document_analysis_mode") == "fast" else 393216)
            if visuals else min(max(setting["max_tokens"], 8192), 65536)
        ),
    }
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        response = await client.post(f"{setting['base_url'].rstrip('/')}/chat/completions", headers=headers, json=body)
        if response.status_code >= 400:
            detail = response.text[:1000]
            logger.warning("document_model_failed status=%s model=%s visual=%s detail=%s", response.status_code, model, bool(visuals), detail)
            raise httpx.HTTPStatusError(f"模型服务返回 {response.status_code}: {detail}", request=response.request, response=response)
        result = response.json()
        choice = result["choices"][0]
        message = choice["message"]
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        if not content and reasoning and allow_reasoning_retry:
            logger.warning(
                "document_content_empty model=%s visual=%s finish_reason=%s reasoning_chars=%s; summarizing reasoning",
                model, bool(visuals), choice.get("finish_reason"), len(reasoning),
            )
            return await model_summary(
                setting,
                filename,
                reasoning,
                "上一次文档分析没有生成最终正文。请把以上分析过程整理成简洁、可靠的文档结论；保留元件、网络、页码和不确定项，不要描述推理过程。",
                allow_reasoning_retry=False,
            )
        if not content:
            logger.warning(
                "document_model_content_empty model=%s visual=%s finish_reason=%s response_keys=%s",
                model, bool(visuals), choice.get("finish_reason"), list(message.keys()),
            )
            raise ValueError("模型返回成功，但没有生成最终正文")
        return content.strip()


@app.post("/api/documents/{document_id}/analyze")
async def analyze_document(document_id: int, mode: str | None = None):
    if mode is None:
        mode = rows("SELECT document_analysis_mode FROM settings WHERE id=1")[0]["document_analysis_mode"]
    if mode not in {"fast", "deep"}:
        raise HTTPException(422, "分析模式必须是 fast 或 deep")
    with connect() as db:
        document = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            raise HTTPException(404, "文档不存在")
        setting = dict(db.execute("SELECT * FROM settings WHERE id=1").fetchone())
        chunks = [dict(row) for row in db.execute("SELECT page_number,content FROM document_chunks WHERE document_id=? ORDER BY chunk_index", (document_id,)).fetchall()]
        db.execute(
            "UPDATE documents SET status='analyzing',analysis_mode=?,analysis_stage='preparing',progress_current=0,progress_total=0 WHERE id=?",
            (mode, document_id),
        )
    if not setting["api_key"]:
        with connect() as db:
            db.execute("UPDATE documents SET status='ready' WHERE id=?", (document_id,))
        raise HTTPException(400, "请先配置模型 API Key")
    visuals: list[dict] = []
    try:
        original = (DOCUMENT_DIR / Path(document["stored_name"]).name).read_bytes()
        visuals = extract_visuals(original, Path(document["filename"]).suffix.lower())
        if mode == "fast" and Path(document["filename"]).suffix.lower() == ".pdf":
            visuals = [visual for visual in visuals if "overview" in visual["name"]]
        setting["document_analysis_mode"] = mode
        groups: list[str] = []
        current = ""
        for item in chunks:
            label = f"[第 {item['page_number']} 页]\n" if item["page_number"] else ""
            part = label + item["content"]
            if current and len(current) + len(part) > 14000:
                groups.append(current)
                current = ""
            current += part + "\n\n"
        if current:
            groups.append(current)
        with connect() as db:
            db.execute("UPDATE documents SET analysis_stage='text' WHERE id=?", (document_id,))
        async with MODEL_GENERATION_LOCK:
            section_summaries = []
            for index, group in enumerate(groups):
                section_summaries.append(await model_summary(setting, document["filename"], group, f"这是文档的第 {index + 1}/{len(groups)} 部分。总结重要事实、论点、结构、术语和结论。"))
            visual_groups: list[list[dict]] = []
            for visual in visuals:
                if visual["page_number"] is not None:
                    existing = next((group for group in visual_groups if group[0]["page_number"] == visual["page_number"]), None)
                    if existing is None:
                        visual_groups.append([visual])
                    else:
                        existing.append(visual)
                elif not visual_groups or len(visual_groups[-1]) >= 4:
                    visual_groups.append([visual])
                else:
                    visual_groups[-1].append(visual)
            schematic = document["filename"].upper().startswith(("SCH_", "SCHEMATIC"))
            visual_instruction = (
                "这是硬件原理图页面及其四象限高清切片。结合整页预览和局部图，识别元件位号、型号、引脚号、网络名、连接关系、电源域、接口、上下拉与去耦；"
                "区分明确连接、跨页网络标签和不确定推断，列出值得硬件工程师复核的风险。"
                if schematic else
                "阅读这些文档页面或图片。识别图中文字，并说明图表、截图、示意图、空间关系及其与文档主题有关的信息。"
            )
            with connect() as db:
                db.execute("UPDATE documents SET analysis_stage='visual',progress_current=0,progress_total=? WHERE id=?", (len(visual_groups), document_id))
            for visual_index, group in enumerate(visual_groups, start=1):
                with connect() as db:
                    db.execute("UPDATE documents SET progress_current=? WHERE id=?", (visual_index, document_id))
                visual_summary = await model_summary(
                    setting, document["filename"], "",
                    visual_instruction, group,
                )
                section_summaries.append(f"【图片内容】\n{visual_summary}")
            if not section_summaries:
                raise ValueError("文档没有可分析的文字或图片")
            combined = "\n\n".join(f"【部分 {index + 1}】\n{value}" for index, value in enumerate(section_summaries))
            with connect() as db:
                db.execute("UPDATE documents SET analysis_stage='summary' WHERE id=?", (document_id,))
            summary = section_summaries[0] if len(section_summaries) == 1 else await model_summary(
                setting, document["filename"], combined, "根据各部分摘要形成全文总览，保留整体结构、关键事实、结论以及各部分之间的关系。"
            )
    except (httpx.HTTPError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        with connect() as db:
            db.execute("UPDATE documents SET status='error',analysis_stage='error' WHERE id=?", (document_id,))
        detail = f"全文理解失败：{exc}"
        if visuals:
            detail += "。如果接口拒绝 image_url，请切换到支持图片识别的多模态模型后重试"
        raise HTTPException(502, detail) from exc
    with connect() as db:
        db.execute("UPDATE documents SET summary=?,status='analyzed',analysis_stage='done',progress_current=progress_total WHERE id=?", (summary, document_id))
    return {"ok": True, "summary": summary}


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: int):
    with connect() as db:
        document = db.execute("SELECT stored_name FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            raise HTTPException(404, "文档不存在")
        db.execute("DELETE FROM documents WHERE id=?", (document_id,))
    remove_original(document["stored_name"])
    return {"ok": True}


@app.put("/api/messages/{message_id}")
def update_message(message_id: int, payload: MessageUpdate):
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "消息内容不能为空")
    with connect() as db:
        message = db.execute(
            """SELECT m.*, c.character_id FROM messages m
            JOIN conversations c ON c.id=m.conversation_id WHERE m.id=?""",
            (message_id,),
        ).fetchone()
        if not message:
            raise HTTPException(404, "消息不存在")
        db.execute("UPDATE messages SET content=? WHERE id=?", (content, message_id))
        db.execute(
            "UPDATE conversations SET summary='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (message["conversation_id"],),
        )
        if message["role"] == "user":
            db.execute("DELETE FROM memories WHERE source_message_id=?", (message_id,))
            maybe_store_memory(db, message["character_id"], message_id, content)
        return dict(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())


@app.post("/api/conversations/{conversation_id}/chat")
async def chat(conversation_id: int, payload: ChatRequest):
    await cancel_active_proactive_generation()
    with connect() as db:
        conversation = db.execute(
            "SELECT c.*, ch.* FROM conversations c JOIN characters ch ON ch.id=c.character_id WHERE c.id=?",
            (conversation_id,),
        ).fetchone()
        if not conversation:
            raise HTTPException(404, "会话不存在")
        setting = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if not setting["api_key"]:
            raise HTTPException(400, "请先在设置中填写 API Key")
        all_history = [dict(row) for row in db.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? AND origin!='proactive' ORDER BY id", (conversation_id,)
        ).fetchall()]
        context_limit = setting["context_message_limit"]
        history = all_history[-context_limit:]
        older = all_history[:-context_limit]
        summary = "\n".join(f"{item['role']}: {item['content']}" for item in older)[-4000:] if older else ""
        if summary != conversation["summary"]:
            db.execute("UPDATE conversations SET summary=? WHERE id=?", (summary, conversation_id))
        cursor = db.execute("INSERT INTO messages(conversation_id, role, content) VALUES (?, 'user', ?)", (conversation_id, payload.content))
        maybe_store_memory(db, conversation["character_id"], cursor.lastrowid, payload.content)
        memories = relevant_memories(db, conversation["character_id"], payload.content, setting["memory_limit"])
        documents = [dict(row) for row in db.execute("SELECT id,filename,summary FROM documents WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()]
        document_context = []
        for document in documents:
            if document["summary"]:
                document_context.append(f"【文档全文总览：{document['filename']}】\n{document['summary']}")
            chunks = [dict(row) for row in db.execute("SELECT page_number,content FROM document_chunks WHERE document_id=?", (document["id"],)).fetchall()]
            for chunk in relevant_chunks(chunks, payload.content, 3):
                page = f"，第 {chunk['page_number']} 页" if chunk["page_number"] else ""
                document_context.append(f"【原文依据：{document['filename']}{page}】\n{chunk['content']}")
        if not any(message["role"] == "user" for message in all_history):
            title = payload.content.strip().replace("\n", " ")[:30] or "新对话"
            db.execute("UPDATE conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (title, conversation_id))

    model_messages = []
    prompt = compile_character_prompt(conversation)
    if prompt:
        model_messages.append({"role": "system", "content": prompt})
    if summary:
        model_messages.append({"role": "system", "content": f"【较早对话摘要】\n{summary}"})
    if memories:
        model_messages.append({"role": "system", "content": "【与当前话题相关的长期记忆】\n- " + "\n- ".join(memories)})
    if document_context:
        document_text = "\n\n".join(document_context)[:30000]
        model_messages.append({"role": "system", "content": "以下是用户在本次对话中提供的文档资料。优先依据资料回答；资料不足时明确说明。\n\n" + document_text})
    environment_context = []
    if setting["include_local_time"]:
        environment_context.append(build_local_time_context())
    if setting["include_location_context"] and setting["location_context"].strip():
        environment_context.append(f"用户设置的位置/地区：{setting['location_context'].strip()}。这是用户提供的概略地区信息；仅在对问题有帮助时参考，不要推断更精确的位置，也不必主动提及。")
    if environment_context:
        model_messages.append({"role": "system", "content": "【本次对话的实时环境信息】\n" + "\n".join(environment_context)})
    if payload.pet_motion_enabled:
        action_prompt = ALICE_ACTION_PROMPT if payload.pet_model == "alice" else PET_ACTION_PROMPT
        recent_motions = [item.strip()[:40] for item in payload.recent_pet_motions if item.strip()][:5]
        if recent_motions:
            action_prompt += "\n最近已经演过这些动作：" + "、".join(recent_motions) + "。本次请换一个不同的构思、节奏或分层组合，不要重复。"
        model_messages.append({"role": "system", "content": action_prompt})
    model_messages.extend(history)
    model_messages.append({"role": "user", "content": payload.content})

    await MODEL_GENERATION_LOCK.acquire()

    async def generate():
        complete = ""
        raw_complete = ""
        stream_buffer = ""
        action_started = False
        tool_call_arguments: dict[int, str] = {}
        saved = False
        max_continuations = 3
        provider_url = setting["base_url"].lower()
        tool_call_enabled = payload.pet_motion_enabled and any(domain in provider_url for domain in ("api.openai.com", "api.deepseek.com"))

        def save_complete_reply():
            nonlocal saved
            if not complete or saved:
                return
            with connect() as db:
                db.execute("INSERT INTO messages(conversation_id, role, content) VALUES (?, 'assistant', ?)", (conversation_id, complete))
                db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
            saved = True

        try:
            headers = {"Authorization": f"Bearer {setting['api_key']}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                request_messages = list(model_messages)
                for continuation in range(max_continuations + 1):
                    finish_reason = None
                    body = {
                        "model": setting["model"], "messages": request_messages, "stream": True,
                        "temperature": setting["temperature"], "max_tokens": setting["max_tokens"],
                    }
                    if tool_call_enabled:
                        tool = PET_ACTION_TOOL if payload.pet_model == "slime" else {
                            **PET_ACTION_TOOL,
                            "function": {**PET_ACTION_TOOL["function"], "description": "为 Q 版爱丽丝桌宠创作轻盈的分层短表演，分别设计身体、五官和头发发饰的运动。"},
                        }
                        body["tools"] = [tool]
                        body["tool_choice"] = "auto"
                    async with client.stream("POST", f"{setting['base_url'].rstrip('/')}/chat/completions", headers=headers, json=body) as response:
                        if response.status_code >= 400:
                            error = (await response.aread()).decode(errors="replace")
                            logger.warning("model_chat_failed status=%s", response.status_code)
                            yield json.dumps({"error": f"模型服务返回 {response.status_code}: {error[:500]}"}, ensure_ascii=False) + "\n"
                            return
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload_text = line[5:].lstrip()
                            if payload_text == "[DONE]":
                                break
                            try:
                                data = json.loads(payload_text)
                                choice = data["choices"][0]
                                delta = choice["delta"]
                                token = delta.get("content", "") or ""
                                for tool_call in delta.get("tool_calls") or []:
                                    index = int(tool_call.get("index", 0))
                                    function = tool_call.get("function", {})
                                    if function.get("name") in (None, "", "perform_pet_action"):
                                        tool_call_arguments[index] = tool_call_arguments.get(index, "") + (function.get("arguments") or "")
                                finish_reason = choice.get("finish_reason") or finish_reason
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                            if token:
                                raw_complete += token
                                if not payload.pet_motion_enabled:
                                    complete += token
                                    yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
                                elif not action_started:
                                    stream_buffer += token
                                    marker_at = stream_buffer.lower().find(PET_ACTION_OPEN)
                                    if marker_at >= 0:
                                        visible = stream_buffer[:marker_at]
                                        action_started = True
                                        stream_buffer = ""
                                        if visible:
                                            complete += visible
                                            yield json.dumps({"token": visible}, ensure_ascii=False) + "\n"
                                    else:
                                        safe_length = max(0, len(stream_buffer) - len(PET_ACTION_OPEN) + 1)
                                        if safe_length:
                                            visible = stream_buffer[:safe_length]
                                            stream_buffer = stream_buffer[safe_length:]
                                            complete += visible
                                            yield json.dumps({"token": visible}, ensure_ascii=False) + "\n"
                    if finish_reason != "length":
                        break
                    if payload.pet_motion_enabled and not action_started and stream_buffer:
                        complete += stream_buffer
                        yield json.dumps({"token": stream_buffer}, ensure_ascii=False) + "\n"
                        stream_buffer = ""
                    if continuation >= max_continuations:
                        logger.warning(
                            "model_chat_still_truncated conversation_id=%s continuations=%s output_chars=%s",
                            conversation_id, continuation, len(complete),
                        )
                        break
                    logger.info(
                        "model_chat_auto_continue conversation_id=%s round=%s output_chars=%s",
                        conversation_id, continuation + 1, len(complete),
                    )
                    yield json.dumps({"continuing": True, "round": continuation + 1}, ensure_ascii=False) + "\n"
                    request_messages = list(model_messages) + [
                        {"role": "assistant", "content": complete},
                        {"role": "user", "content": "上一个回答因长度限制被截断。请直接从中断处继续完成回答，不要重复已经输出的内容。"},
                    ]
            if payload.pet_motion_enabled and not action_started and stream_buffer:
                complete += stream_buffer
                yield json.dumps({"token": stream_buffer}, ensure_ascii=False) + "\n"
            pet_motion = None
            pet_motion_raw = ""
            if payload.pet_motion_enabled:
                for arguments in tool_call_arguments.values():
                    try:
                        candidate = normalize_pet_action(json.loads(arguments))
                    except (json.JSONDecodeError, TypeError):
                        candidate = None
                    if candidate:
                        pet_motion = candidate
                        pet_motion_raw = arguments
                        break
                if not pet_motion:
                    _, pet_motion, pet_motion_raw = extract_pet_action(raw_complete)
                if pet_motion:
                    logger.info(
                        "pet_motion_selected conversation_id=%s mode=%s action=%s emotion=%s body_frames=%s face_frames=%s crest_frames=%s effect=%s quality=%s generated_layers=%s",
                        conversation_id,
                        "tool_call" if tool_call_arguments else "tag",
                        pet_motion.get("action"),
                        pet_motion.get("emotionLabel", ""),
                        len(pet_motion.get("keyframes", [])),
                        len(pet_motion.get("faceKeyframes", [])),
                        len(pet_motion.get("crestKeyframes", [])),
                        pet_motion.get("effect", "none"),
                        pet_motion.get("motionQuality", 0),
                        ",".join(pet_motion.get("generatedLayers", [])) or "none",
                    )
                else:
                    logger.info("pet_motion_missing conversation_id=%s raw_present=%s", conversation_id, bool(pet_motion_raw))
            complete = complete.rstrip()
            if complete:
                save_complete_reply()
                done_event = {"done": True}
                if pet_motion:
                    done_event["pet_motion"] = pet_motion
                    done_event["pet_motion_raw"] = pet_motion_raw
                if finish_reason == "length":
                    done_event["truncated"] = True
                yield json.dumps(done_event, ensure_ascii=False) + "\n"
                logger.info("model_chat_completed conversation_id=%s output_chars=%s", conversation_id, len(complete))
            else:
                yield json.dumps({"error": "模型没有返回可显示的正文，请提高回复 token 上限或检查模型设置"}, ensure_ascii=False) + "\n"
        except httpx.HTTPError as exc:
            save_complete_reply()
            logger.warning("model_chat_connection_failed conversation_id=%s error_type=%s detail=%s", conversation_id, type(exc).__name__, str(exc))
            yield json.dumps({"error": f"无法连接模型服务：{exc}"}, ensure_ascii=False) + "\n"
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            save_complete_reply()
            logger.warning("model_chat_response_failed conversation_id=%s error_type=%s", conversation_id, type(exc).__name__)
            yield json.dumps({"error": "模型返回格式异常，请检查接口兼容性"}, ensure_ascii=False) + "\n"
        except asyncio.CancelledError:
            save_complete_reply()
            raise
        finally:
            reschedule_proactive_from_now()
            MODEL_GENERATION_LOCK.release()

    return StreamingResponse(generate(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
