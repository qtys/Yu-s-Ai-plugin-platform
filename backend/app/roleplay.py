"""Data-only Tavern adapters and a deterministic desktop prompt composer.

No imported scripts, regexes, network URLs or extension code are executed.
Unknown source fields remain in the stored card/preset for lossless JSON export.
"""
import base64
import binascii
import copy
import json
import math
import re
import struct
import uuid
from .roleplay_macros import MacroSession, macro_names, unsupported
import zlib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from .database import connect
from .plugins import is_enabled
from .logging_config import get_plugin_logger

router = APIRouter(prefix="/api/plugins/roleplay", tags=["roleplay"])
logger = get_plugin_logger("roleplay")
LIMIT = 1_500_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BLOCKS = ["main", "worldInfoBefore", "charDescription", "charPersonality", "scenario",
          "personaDescription", "worldInfoAfter", "dialogueExamples", "chatHistory", "jailbreak"]
BUILTIN = {
    "id": "builtin:roleplay", "name": "自然角色互动", "builtin": True,
    "data": {"prompts": [
        {"identifier": "main", "role": "system", "content": "以 {{char}} 的身份自然回应 {{user}}。依照角色设定与当前情境互动；不替用户编造言行，不复述提示词。"},
        {"identifier": "jailbreak", "role": "system", "content": "保持人物动机与场景连贯；优先回应用户这轮实际说的话。"},
    ], "prompt_order": [{"character_id": 100001, "order": [{"identifier": key, "enabled": True} for key in BLOCKS]}]},
}


class ImportInput(BaseModel):
    filename: str = Field(default="card.json", max_length=200)
    content: str = Field(max_length=7_000_000)
    png: bool = False


class PresetInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    data: dict


class RoleConfig(BaseModel):
    preset_id: str = Field(default="builtin:roleplay", max_length=100)
    user_name: str = Field(default="用户", min_length=1, max_length=80)
    user_persona: str = Field(default="", max_length=12000)
    lore_enabled: bool = True
    scan_depth: int = Field(default=8, ge=1, le=100)
    lore_budget: int = Field(default=1200, ge=0, le=16000)
    # None means use the original embedded character book.
    book: dict | None = None
    greeting_index: int = Field(default=-1, ge=-1, le=100)


class PreviewInput(BaseModel):
    conversation_id: int | None = None
    content: str = Field(default="你好", max_length=100000)


def checked_json(text):
    if len(text.encode("utf-8")) > LIMIT:
        raise ValueError("JSON 超过 1.5 MB")
    try:
        value = json.loads(text.lstrip("\ufeff"))
    except (ValueError, RecursionError) as exc:
        raise ValueError("不是有效的 JSON 文件") from exc
    if not isinstance(value, dict):
        raise ValueError("文件顶层必须是 JSON 对象")
    return value


def text_field(data, key):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是文字")
    if len(value) > 100000:
        raise ValueError(f"{key} 超过 100000 字符")
    return value


def png_card(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("PNG 编码无效") from exc
    if len(raw) > 5_000_000 or not raw.startswith(PNG_SIGNATURE):
        raise ValueError("请选择 5 MB 以内的 PNG 角色卡")
    offset, cards, image_chunks = 8, {}, []
    has_header, has_pixels = False, False
    ended = False
    while offset + 12 <= len(raw):
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        end = offset + length + 12
        if end > len(raw):
            raise ValueError("PNG 数据不完整")
        data = raw[offset + 8:end - 4]
        if zlib.crc32(kind + data) & 0xffffffff != struct.unpack(">I", raw[end - 4:end])[0]:
            raise ValueError("PNG 校验失败")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG 缺少图像头")
            width, height = struct.unpack(">II", data[:8])
            if not (0 < width <= 12000 and 0 < height <= 12000 and width * height <= 40_000_000):
                raise ValueError("PNG 图像尺寸无效或过大")
            has_header = True
        if kind == b"IDAT":
            has_pixels = True
        if kind == b"tEXt":
            key, _, value = data.partition(b"\0")
            if key in (b"chara", b"ccv3"):
                try:
                    cards[key] = checked_json(base64.b64decode(value, validate=True).decode("utf-8"))
                except (ValueError, UnicodeError, binascii.Error) as exc:
                    raise ValueError("PNG 内的角色卡元数据无效") from exc
        # Remove all text metadata from displayed avatar; original JSON is retained separately.
        if kind not in (b"tEXt", b"zTXt", b"iTXt"):
            image_chunks.append(raw[offset:end])
        offset = end
        if kind == b"IEND":
            ended = True
            break
    if not ended or not has_header or not has_pixels or not cards:
        raise ValueError("PNG 没有有效的 chara/ccv3 角色卡数据；普通图片不能作为酒馆卡导入")
    avatar = PNG_SIGNATURE + b"".join(image_chunks)
    return cards.get(b"chara", cards.get(b"ccv3")), ("data:image/png;base64," + base64.b64encode(avatar).decode() if len(avatar) < 1_400_000 else "")


def validate_book(book):
    if not isinstance(book, dict) or not isinstance(book.get("entries", []), list):
        raise ValueError("世界书需要 entries 数组")
    if len(book.get("entries", [])) > 300:
        raise ValueError("每本世界书最多 300 条")
    for entry in book.get("entries", []):
        if not isinstance(entry, dict):
            raise ValueError("世界书条目必须是对象")
        text_field(entry, "content")
        for field in ("keys", "secondary_keys"):
            keys = entry.get(field, [])
            if not isinstance(keys, list) or len(keys) > 100 or any(not isinstance(k, str) or len(k) > 200 for k in keys):
                raise ValueError("世界书关键词必须是文字数组，每条最多 100 个")


def normalize_book(book):
    """Also accept standalone SillyTavern World Info JSON (entries keyed by UID)."""
    if not isinstance(book, dict):
        raise ValueError("世界书必须是对象")
    result = copy.deepcopy(book)
    result.setdefault("entries", [])
    if isinstance(result.get("entries"), dict):
        entries = []
        for uid, entry in result["entries"].items():
            if not isinstance(entry, dict):
                raise ValueError("世界书条目必须是对象")
            entries.append({**entry, "id": entry.get("uid", uid), "keys": entry.get("key", []),
                            "secondary_keys": entry.get("keysecondary", []), "enabled": not entry.get("disable", False),
                            "insertion_order": entry.get("order", 100), "case_sensitive": entry.get("caseSensitive", False)})
        result["entries"] = entries
    validate_book(result)
    return result


def validate_source(card):
    if not card:
        return
    data = card.get("data")
    if not isinstance(data, dict):
        raise ValueError("角色卡 data 必须是对象")
    for key in ("name", "description", "personality", "scenario", "first_mes", "mes_example", "system_prompt", "post_history_instructions", "creator", "creator_notes", "character_version"):
        text_field(data, key)
    if not isinstance(data.get("extensions", {}), dict):
        raise ValueError("角色卡 extensions 必须是对象")
    if not isinstance(data.get("tags", []), list) or any(not isinstance(tag, str) for tag in data.get("tags", [])):
        raise ValueError("角色卡 tags 必须是文字数组")
    greetings = data.get("alternate_greetings", [])
    if not isinstance(greetings, list) or len(greetings) > 100 or any(not isinstance(x, str) or len(x) > 100000 for x in greetings):
        raise ValueError("备用开场白需要文字数组，最多 100 条")
    if data.get("character_book") is not None:
        validate_book(data["character_book"])


def validate_native_config(config):
    parsed = RoleConfig.model_validate(config)
    if parsed.book is not None:
        parsed.book = normalize_book(parsed.book)
    return parsed


def normalize_card(payload):
    source, avatar = png_card(payload.content) if payload.png else (checked_json(payload.content), "")
    warnings = []
    if source.get("format") == "yus-ai-character":
        native = source.get("character")
        if not isinstance(native, dict):
            raise ValueError("Yu's AI 角色卡格式无效")
        # A native export may carry the preserved Tavern data and per-role binding.
        original = source.get("tavern_card", {})
        if not isinstance(original, dict):
            raise ValueError("tavern_card 必须是对象")
        validate_source(original)
        return native, original, warnings, source.get("roleplay_config", {})
    if source.get("spec"):
        if source["spec"] not in ("chara_card_v2", "chara_card_v3") or not isinstance(source.get("data"), dict):
            raise ValueError("目前支持 Character Card V2 / V3 的基础字段")
        data = source["data"]
        if source["spec"] == "chara_card_v3":
            warnings.append("V3 基础字段兼容；资源包、群组开场和扩展功能仅保留，不执行")
    elif "name" in source and ("first_mes" in source or "mes_example" in source or "scenario" in source):
        data = source
        source = {"spec": "chara_card_v2", "spec_version": "2.0", "data": copy.deepcopy(data)}
    elif "name" in source:
        return source, {}, ["按普通 Yu's AI JSON 角色卡导入"], {}
    else:
        raise ValueError("未找到角色名称或角色卡字段")
    native = {"name": text_field(data, "name"), "description": "", "background": text_field(data, "description"),
              "personality": text_field(data, "personality"), "greeting": text_field(data, "first_mes"),
              "system_prompt": text_field(data, "system_prompt"), "example_dialogue": text_field(data, "mes_example"), "avatar_data": avatar}
    text_field(data, "scenario")
    text_field(data, "post_history_instructions")
    validate_source(source)
    if data.get("character_book"):
        validate_book(data["character_book"])
    if data.get("extensions"):
        warnings.append("扩展字段已保留；酒馆脚本、正则、第三方扩展不会执行")
    if data.get("alternate_greetings"):
        warnings.append("备用开场白已保留；新会话默认使用 first_mes")
    book = data.get("character_book") or {}
    config = {}
    for source_key, target, maximum in [("scan_depth", "scan_depth", 100), ("token_budget", "lore_budget", 16000)]:
        if isinstance(book.get(source_key), int):
            config[target] = max(1 if target == "scan_depth" else 0, min(maximum, book[source_key]))
    return native, source, warnings, config


def validate_preset(data):
    checked_json(json.dumps(data, ensure_ascii=False))
    prompts = data.get("prompts")
    if not isinstance(prompts, list) or not prompts or len(prompts) > 500:
        raise ValueError("需要包含 prompts 数组的 Chat Completion 预设（最多 500 段），不支持 Text Completion 格式")
    seen = set()
    warnings = []
    for prompt in prompts:
        if not isinstance(prompt, dict) or not isinstance(prompt.get("identifier"), str):
            raise ValueError("每段提示词需要 identifier")
        key = prompt["identifier"]
        if key in seen:
            raise ValueError("提示词 identifier 重复")
        seen.add(key)
        text_field(prompt, "content")
        if "enabled" in prompt and not isinstance(prompt["enabled"], bool):
            raise ValueError("提示词 enabled 必须为布尔值")
        if prompt.get("role", "system") not in ("system", "user", "assistant"):
            raise ValueError("提示词 role 只能为 system / user / assistant")
        if prompt.get("injection_position", 0) != 0:
            warnings.append(f"{key}：绝对深度注入暂不支持，保留但不发送")
    orders = data.get("prompt_order", [])
    if not isinstance(orders, list):
        raise ValueError("prompt_order 必须是数组")
    if len(orders) > 100:
        raise ValueError("预设顺序组最多 100 个")
    for group in orders:
        if not isinstance(group, dict) or not isinstance(group.get("order"), list):
            raise ValueError("prompt_order 组需要 order 数组")
        if len(group["order"]) > 1000:
            raise ValueError("每组顺序最多 1000 项")
        ids = set()
        for item in group["order"]:
            if not isinstance(item, dict) or not isinstance(item.get("identifier"), str) or item["identifier"] in ids:
                raise ValueError("预设顺序标识无效或重复")
            ids.add(item["identifier"])
            if "enabled" in item and not isinstance(item["enabled"], bool):
                raise ValueError("顺序 enabled 必须为布尔值")
        if not any(item["identifier"] == "chatHistory" and item.get("enabled", True) for item in group["order"]):
            raise ValueError("预设顺序必须保留启用的 chatHistory，不能丢失当前用户消息")
    warnings.append("仅使用提示词顺序与内容；温度、token 上限及模型连接仍使用模型设置")
    return warnings


def preset_diagnostics(data):
    warnings = validate_preset(data)
    prompts = {p["identifier"]: p for p in data["prompts"]}
    orders = data.get("prompt_order", [])
    selected = next((o for o in orders if o.get("character_id") == 100001), orders[0] if orders else None)
    order = selected["order"] if selected else [{"identifier": k, "enabled": True} for k in BLOCKS + [k for k in prompts if k not in BLOCKS]]
    positions = {p["identifier"]: p for p in order}
    blocks = []
    for key in dict.fromkeys([p["identifier"] for p in order] + list(prompts)):
        p = prompts.get(key, {})
        text = p.get("content", "")
        missing = unsupported(text)
        status = "ready"
        if key not in positions or not positions[key].get("enabled", True) or p.get("enabled") is False:
            status = "disabled"
        elif p.get("injection_position", 0) != 0 or missing:
            status = "unsupported"
        elif key in BLOCKS:
            status = "dynamic"
        elif not text.strip():
            status = "empty"
        blocks.append({"identifier": key, "name": p.get("name", key), "status": status,
                       "macros": macro_names(text), "unsupported_macros": missing,
                       "estimated_tokens": estimate_tokens(text)})
    if data.get("extensions"):
        warnings.append("预设扩展字段仅保留，不执行扩展脚本或正则")
    warnings.append("变量仅在本次请求中有效，不持久化；不支持全局变量、条件块、作用域块、pick、时间宏及扩展宏。含未支持宏的模块整段跳过。")
    return {"blocks": blocks, "counts": {s: sum(b["status"] == s for b in blocks) for s in ("ready", "dynamic", "disabled", "unsupported", "empty")},
            "estimated_tokens": sum(b["estimated_tokens"] for b in blocks if b["status"] in ("ready", "dynamic")),
            "warnings": warnings, "note": "静态诊断：角色覆盖和宏展开后的实际发送内容请查看请求预览；token 仅为粗略估算。"}


@router.post("/presets/inspect")
def inspect_preset(payload: PresetInput):
    try:
        return preset_diagnostics(payload.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def require_character(db, character_id):
    row = db.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
    if not row:
        raise HTTPException(404, "角色不存在")
    return dict(row)


def state(db, character_id):
    require_character(db, character_id)
    row = db.execute("SELECT card,config FROM roleplay_characters WHERE character_id=?", (character_id,)).fetchone()
    return (json.loads(row["card"]), RoleConfig.model_validate_json(row["config"])) if row else ({}, RoleConfig())


def get_preset(db, preset_id):
    if preset_id == BUILTIN["id"]:
        return copy.deepcopy(BUILTIN)
    row = db.execute("SELECT * FROM roleplay_presets WHERE id=?", (preset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "预设不存在")
    return {"id": row["id"], "name": row["name"], "builtin": False, "data": json.loads(row["data"])}


def store_config(db, character_id, config):
    db.execute("INSERT INTO roleplay_characters(character_id,config) VALUES (?,?) ON CONFLICT(character_id) DO UPDATE SET config=excluded.config",
               (character_id, config.model_dump_json()))


@router.post("/cards/inspect")
def inspect_card(payload: ImportInput):
    try:
        native, card, warnings, config = normalize_card(payload)
        # Use the application's own field validation, without introducing an import cycle at startup.
        from .main import CharacterCreate
        value = CharacterCreate.model_validate(native)
        validate_native_config(config)
        return {"character": value.model_dump(), "warnings": warnings, "tavern": bool(card)}
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/cards/import", status_code=201)
def import_card(payload: ImportInput):
    try:
        native, card, warnings, config = normalize_card(payload)
        from .main import CharacterCreate
        value = CharacterCreate.model_validate(native).model_dump()
        config = validate_native_config(config)
        with connect() as db:
            if config.preset_id != BUILTIN["id"] and not db.execute("SELECT 1 FROM roleplay_presets WHERE id=?", (config.preset_id,)).fetchone():
                config.preset_id = BUILTIN["id"]
                warnings.append("原绑定预设不存在，已使用内置预设；可重新导入并绑定")
            columns = list(value)
            cursor = db.execute(f"INSERT INTO characters({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(value.values()))
            character_id = cursor.lastrowid
            db.execute("INSERT INTO roleplay_characters(character_id,card,config) VALUES (?,?,?)", (character_id, json.dumps(card, ensure_ascii=False), config.model_dump_json()))
            result = require_character(db, character_id)
        logger.info("card_imported character_id=%s tavern=%s", character_id, bool(card))
        return {"character": result, "warnings": warnings}
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/characters/{character_id}/export")
def export_card(character_id: int, format: str = "tavern"):
    with connect() as db:
        character = require_character(db, character_id)
        card, config = state(db, character_id)
    if format == "native":
        return {"format": "yus-ai-character", "version": 1, "character": character, "tavern_card": card, "roleplay_config": config.model_dump()}
    if format != "tavern":
        raise HTTPException(422, "format 只能为 native 或 tavern")
    card = copy.deepcopy(card) or {"spec": "chara_card_v2", "spec_version": "2.0", "data": {}}
    data = card.setdefault("data", {})
    # Source metadata remains intact while user edits to ordinary fields are reflected.
    data.update({"name": character["name"], "description": character["background"] or character["description"],
                 "personality": character["personality"], "first_mes": character["greeting"],
                 "mes_example": character["example_dialogue"], "system_prompt": character["system_prompt"]})
    for key, default in {"scenario": "", "post_history_instructions": "", "creator_notes": "", "alternate_greetings": [], "tags": [], "creator": "", "character_version": "", "extensions": {}}.items():
        data.setdefault(key, default)
    if config.book is not None:
        data["character_book"] = config.book
    return card


@router.get("/presets")
def presets():
    with connect() as db:
        return [copy.deepcopy(BUILTIN)] + [get_preset(db, row[0]) for row in db.execute("SELECT id FROM roleplay_presets ORDER BY rowid DESC")]


@router.post("/worldbooks/inspect")
def inspect_worldbook(payload: ImportInput):
    try:
        return normalize_book(checked_json(payload.content))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/presets", status_code=201)
def create_preset(payload: PresetInput):
    try:
        warnings = validate_preset(payload.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    preset_id = uuid.uuid4().hex
    with connect() as db:
        db.execute("INSERT INTO roleplay_presets VALUES (?,?,?)", (preset_id, payload.name, json.dumps(payload.data, ensure_ascii=False)))
        return {**get_preset(db, preset_id), "warnings": warnings}


@router.put("/presets/{preset_id}")
def update_preset(preset_id: str, payload: PresetInput):
    if preset_id.startswith("builtin:"):
        raise HTTPException(409, "内置预设不可覆盖，请复制为自定义预设")
    try:
        warnings = validate_preset(payload.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connect() as db:
        get_preset(db, preset_id)
        db.execute("UPDATE roleplay_presets SET name=?,data=? WHERE id=?", (payload.name, json.dumps(payload.data, ensure_ascii=False), preset_id))
    return {"ok": True, "warnings": warnings}


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: str):
    if preset_id.startswith("builtin:"):
        raise HTTPException(409, "不能删除内置预设")
    with connect() as db:
        get_preset(db, preset_id)
        bound = 0
        for row in db.execute("SELECT character_id,config FROM roleplay_characters").fetchall():
            config = RoleConfig.model_validate_json(row["config"])
            if config.preset_id == preset_id:
                config.preset_id = BUILTIN["id"]
                store_config(db, row["character_id"], config)
                bound += 1
        db.execute("DELETE FROM roleplay_presets WHERE id=?", (preset_id,))
    return {"ok": True, "reset_bindings": bound}


@router.get("/characters/{character_id}")
def character_settings(character_id: int):
    with connect() as db:
        card, config = state(db, character_id)
        data = card.get("data", {})
        return {"config": config.model_dump(), "book": normalize_book(config.book if config.book is not None else data.get("character_book") or {}),
                "alternate_greetings": data.get("alternate_greetings", []),
                "scenario": data.get("scenario", ""), "post_history_instructions": data.get("post_history_instructions", ""),
                "source": {"creator": data.get("creator", ""), "notes": data.get("creator_notes", ""), "tags": data.get("tags", [])}}


@router.patch("/characters/{character_id}")
def change_settings(character_id: int, payload: dict):
    try:
        with connect() as db:
            card, config = state(db, character_id)
            unknown = set(payload) - {"config", "scenario", "post_history_instructions"}
            if unknown:
                raise ValueError("未知设置字段")
            patch = payload.get("config", {})
            if not isinstance(patch, dict) or set(patch) - set(RoleConfig.model_fields):
                raise ValueError("角色扮演配置字段无效")
            config = RoleConfig.model_validate({**config.model_dump(), **patch})
            get_preset(db, config.preset_id)
            if config.book is not None:
                config.book = normalize_book(config.book)
            for key in ("scenario", "post_history_instructions"):
                if key in payload:
                    text_field(payload, key)
                    if not card:
                        card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {}}
                    card.setdefault("data", {})[key] = payload[key]
            checked_json(json.dumps({"card": card, "config": config.model_dump()}, ensure_ascii=False))
            store_config(db, character_id, config)
            db.execute("UPDATE roleplay_characters SET card=? WHERE character_id=?", (json.dumps(card, ensure_ascii=False), character_id))
        return character_settings(character_id)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc


def render(text, values, warnings):
    session = values if isinstance(values, MacroSession) else MacroSession(values)
    return session.render(text, warnings)


def estimate_tokens(text):
    cjk = sum(ord(c) > 255 for c in text)
    return cjk + math.ceil((len(text) - cjk) / 4)


def select_lore(book, config, messages, warnings, values=None):
    if not config.lore_enabled or not config.lore_budget:
        return [], []
    if book.get("recursive_scanning"):
        warnings.append("递归世界书扫描未启用；仅扫描当前及最近消息")
    haystack = "\n".join(m["content"] for m in messages[-config.scan_depth:])
    candidates = []
    for index, entry in enumerate(book.get("entries", [])):
        if not entry.get("enabled", True):
            continue
        ext = entry.get("extensions", {})
        ext = ext if isinstance(ext, dict) else {}
        if entry.get("use_regex") or ext.get("use_regex") or entry.get("position", "before_char") not in ("before_char", "after_char", 0, 1) or ext.get("position", 0) not in (0, 1):
            warnings.append(f"世界书 {index + 1} 的正则/深度位置暂不支持，未发送")
            continue
        probability = entry.get("probability", ext.get("probability", 100))
        if entry.get("selectiveLogic", ext.get("selectiveLogic", 0)) not in (None, 0) or (entry.get("useProbability", ext.get("useProbability", False)) and probability != 100):
            warnings.append(f"世界书 {index + 1} 的概率/高级选择逻辑暂不支持，未发送")
            continue
        sensitive = entry.get("case_sensitive", False)
        target = haystack if sensitive else haystack.casefold()
        def matches(keys):
            return any(k and (k if sensitive else k.casefold()) in target for k in keys)
        active = entry.get("constant", False) or (matches(entry.get("keys", [])) and
                 (not entry.get("selective", False) or matches(entry.get("secondary_keys", []))))
        if active:
            candidates.append((index, entry))
    used, included, excluded = 0, [], []
    # Priority decides budget allocation; insertion_order decides final presentation.
    def number(value, default):
        return value if isinstance(value, (int, float)) and math.isfinite(value) else default
    candidates.sort(key=lambda item: (-number(item[1].get("priority"), 0), number(item[1].get("insertion_order"), item[0])))
    for index, entry in candidates:
        content = render(entry.get("content", ""), values or {}, warnings)
        cost = estimate_tokens(content)
        if used + cost > config.lore_budget:
            excluded.append(index + 1)
            continue
        used += cost
        included.append({"index": index + 1, "content": content, "position": entry.get("position", "before_char"), "cost": cost,
                         "order": number(entry.get("insertion_order"), index)})
    included.sort(key=lambda item: item["order"])
    return included, excluded


def compose(db, character, history, latest, legacy_prompt):
    card, config = state(db, character["character_id"] if "character_id" in character.keys() else character["id"])
    data = card.get("data", {})
    preset = get_preset(db, config.preset_id)
    warnings = validate_preset(preset["data"])
    values = {"char": character["name"], "user": config.user_name,
              "description": character["background"] or character["description"], "personality": character["personality"],
              "scenario": data.get("scenario", ""), "persona": config.user_persona, "original": ""}
    values["lastusermessage"] = latest
    session = MacroSession(values)
    # Worldbook variables are deliberately independent of preset block evaluation.
    lore, excluded = select_lore(config.book if config.book is not None else data.get("character_book", {}), config, history + [{"content": latest}], warnings, values)
    prompts = {p["identifier"]: p for p in preset["data"]["prompts"]}
    orders = preset["data"].get("prompt_order", [])
    selected_order = next((o for o in orders if o.get("character_id") == 100001), orders[0] if orders else None)
    order = selected_order["order"] if selected_order else [{"identifier": k, "enabled": True} for k in BLOCKS + [k for k in prompts if k not in BLOCKS]]
    fields = {"charDescription": values["description"], "charPersonality": values["personality"], "scenario": values["scenario"], "personaDescription": config.user_persona,
              "worldInfoBefore": "\n\n".join(e["content"] for e in lore if e["position"] in ("before_char", 0)),
              "worldInfoAfter": "\n\n".join(e["content"] for e in lore if e["position"] in ("after_char", 1))}
    # Native fields absent in Tavern still belong to this role; never silently drop them.
    extras = "\n\n".join(f"【{label}】\n{character[key]}" for key, label in [("speaking_style", "说话方式"), ("relationship", "关系"), ("boundaries", "边界")] if character[key])
    fields["charDescription"] = "\n\n".join(filter(None, [fields["charDescription"], character["description"] if character["background"] else "", extras]))
    for key, setting_key, marker in [("scenario", "scenario_format", "{{scenario}}"), ("charPersonality", "personality_format", "{{personality}}"),
                                     ("worldInfoBefore", "wi_format", "{0}"), ("worldInfoAfter", "wi_format", "{0}")]:
        wrapper = preset["data"].get(setting_key)
        if fields[key] and isinstance(wrapper, str) and wrapper:
            fields[key] = wrapper.replace(marker, fields[key])
    before, after, active_blocks, at_history = [], [], [], False
    evaluated = []
    for item in order:
        key = item["identifier"]
        if not item.get("enabled", True):
            evaluated.append({"identifier": key, "status": "disabled"})
            continue
        p = prompts.get(key, {})
        if p.get("injection_position", 0) != 0 or p.get("enabled") is False:
            evaluated.append({"identifier": key, "status": "unsupported" if p.get("injection_position", 0) != 0 else "disabled"})
            continue
        if key == "chatHistory":
            new_chat = preset["data"].get("new_chat_prompt", "")
            if isinstance(new_chat, str) and new_chat.strip():
                rendered = render(new_chat, session, warnings)
                if rendered.strip():
                    before.append({"role": "system", "content": rendered})
            at_history = True
            active_blocks.append(key)
            evaluated.append({"identifier": key, "status": "history"})
            continue
        dest = after if at_history else before
        if key == "dialogueExamples":
            examples = render(character["example_dialogue"], session, warnings)
            # Keep examples as real user/assistant turns, never count them as saved history.
            chunks = re.split(r"(?m)^\s*<START>\s*$", examples)
            for chunk in chunks:
                example_start = preset["data"].get("new_example_chat_prompt", "")
                if chunk.strip() and isinstance(example_start, str) and example_start.strip():
                    rendered = render(example_start, session, warnings)
                    if rendered.strip():
                        dest.append({"role": "system", "content": rendered})
                pattern = rf"(?m)^({re.escape(character['name'])}|{re.escape(config.user_name)}):\s*"
                parts = re.split(pattern, chunk)
                for i in range(1, len(parts) - 1, 2):
                    dest.append({"role": "assistant" if parts[i] == character["name"] else "user", "content": parts[i + 1].strip()})
                if len(parts) == 1 and chunk.strip():
                    dest.append({"role": "system", "content": "【示例对话，非真实历史】\n" + chunk.strip()})
            if examples.strip():
                active_blocks.append(key)
            evaluated.append({"identifier": key, "status": "sent" if examples.strip() else "empty_or_unsupported"})
            continue
        if key in fields:
            content = fields[key]
        else:
            content = p.get("content", "")
            if key == "main" and character["system_prompt"].strip():
                content = character["system_prompt"].replace("{{original}}", content)
            if key == "jailbreak" and data.get("post_history_instructions", "").strip():
                content = data["post_history_instructions"].replace("{{original}}", content)
        warning_count = len(warnings)
        content = render(content, session, warnings).strip()
        evaluated.append({"identifier": key, "status": "sent" if content else "unsupported" if len(warnings) > warning_count else "empty_or_variable", "estimated_tokens": estimate_tokens(content)})
        if content:
            dest.append({"role": p.get("role", "system"), "content": content})
            active_blocks.append(key)
    if not at_history:
        raise HTTPException(422, "预设必须包含启用的 chatHistory")
    return {"before": before, "after": after, "preset_id": preset["id"], "preset_name": preset["name"], "blocks": active_blocks,
            "lore": [{"index": e["index"], "cost": e["cost"]} for e in lore], "excluded_lore": excluded,
            "warnings": list(dict.fromkeys(warnings)), "diagnostics": evaluated,
            "macro_scope": "单次请求；世界书条目独立；随机宏预览与实际请求可能不同"}


def opening_message(db, character):
    card, config = state(db, character["id"])
    alternate = card.get("data", {}).get("alternate_greetings", [])
    greeting = alternate[config.greeting_index] if 0 <= config.greeting_index < len(alternate) else character["greeting"]
    return render(greeting, {"char": character["name"], "user": config.user_name}, [])


@router.post("/characters/{character_id}/preview")
def preview(character_id: int, payload: PreviewInput):
    with connect() as db:
        character = require_character(db, character_id)
        history = []
        if payload.conversation_id is not None:
            conv = db.execute("SELECT character_id FROM conversations WHERE id=?", (payload.conversation_id,)).fetchone()
            if not conv or conv[0] != character_id:
                raise HTTPException(409, "会话不属于所选角色")
            limit = db.execute("SELECT context_message_limit FROM settings WHERE id=1").fetchone()[0]
            history = [dict(r) for r in db.execute("SELECT role,content FROM messages WHERE conversation_id=? AND origin!='proactive' ORDER BY id DESC LIMIT ?", (payload.conversation_id, limit))][::-1]
        from .main import compile_character_prompt
        plan = compose(db, character, history, payload.content, compile_character_prompt(character))
        enabled = is_enabled(db, "roleplay")
    return {**plan, "enabled": enabled, "messages": plan["before"] + history + [{"role": "user", "content": payload.content}] + plan["after"],
            "note": "角色扮演层预览，不发起模型请求。实际请求还会包含已启用的小说、环境、指令、文档等内容；插件关闭时本层不会注入。"}


def record_request(db, conversation_id, plan, body):
    # Local only: excludes credentials and service URL. Overwrites latest snapshot, not every turn.
    snapshot = {"plan": plan, "model": body["model"], "messages": body["messages"], "temperature": body["temperature"], "max_tokens": body["max_tokens"]}
    db.execute("INSERT INTO roleplay_requests(conversation_id,data) VALUES (?,?) ON CONFLICT(conversation_id) DO UPDATE SET data=excluded.data,created_at=CURRENT_TIMESTAMP",
               (conversation_id, json.dumps(snapshot, ensure_ascii=False)))
    logger.info("request_compiled conversation_id=%s blocks=%s lore=%s", conversation_id, len(plan["blocks"]), len(plan["lore"]))


@router.get("/requests/{conversation_id}")
def last_request(conversation_id: int):
    with connect() as db:
        row = db.execute("SELECT data,created_at FROM roleplay_requests WHERE conversation_id=?", (conversation_id,)).fetchone()
    if not row:
        raise HTTPException(404, "这段会话还没有启用角色扮演插件的实际请求")
    return {**json.loads(row["data"]), "created_at": row["created_at"], "note": "最近一次启用插件的主请求；不含 API Key。继续生成和二次审核不在此快照中。"}
