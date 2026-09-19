import json
import asyncio
import logging
import time
import random
import zipfile
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

from .database import connect, init_db
from .logging_config import LOG_FILE, configure_logging
from .translation import install_package, package_status, translate_text
from .proactive import EmptyProactiveReply, generate_proactive
from .documents import DOCUMENT_DIR, chunk_pages, decode_document, extract_pages, extract_visuals, relevant_chunks, remove_original, save_original

MODEL_GENERATION_LOCK = asyncio.Lock()
PROACTIVE_GENERATION_TASK: asyncio.Task | None = None

logger = logging.getLogger("yus_ai.api")


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


def rows(query: str, params: tuple = ()) -> list[dict]:
    with connect() as db:
        return [dict(row) for row in db.execute(query, params).fetchall()]


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    init_db()
    logger.info("backend_started version=0.14.0")
    yield
    logger.info("backend_stopped")


app = FastAPI(title="Yu's AI API", version="0.14.0", lifespan=lifespan)
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


@app.get("/api/settings")
def get_settings():
    setting = rows("SELECT * FROM settings WHERE id = 1")[0]
    setting["api_key"] = "" if not setting["api_key"] else "••••••••"
    return setting


@app.put("/api/settings")
def update_settings(payload: SettingsUpdate):
    with connect() as db:
        current_key = db.execute("SELECT api_key FROM settings WHERE id = 1").fetchone()[0]
        api_key = current_key if payload.api_key == "••••••••" else payload.api_key
        db.execute(
            "UPDATE settings SET base_url=?, api_key=?, model=?, temperature=?, max_tokens=?, context_message_limit=?, memory_limit=?, message_display_mode=?, translation_mirror_url=?, vision_model=?, document_analysis_mode=? WHERE id=1",
            (payload.base_url.rstrip("/"), api_key, payload.model, payload.temperature, payload.max_tokens, payload.context_message_limit, payload.memory_limit, payload.message_display_mode, payload.translation_mirror_url.strip().rstrip("/"), payload.vision_model.strip(), payload.document_analysis_mode),
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
        db.execute("UPDATE proactive_plugin SET enabled=?, interval_minutes=?, max_tokens=?, news_enabled=?, rss_url=?, randomize_interval=?,random_min_minutes=?,random_max_minutes=?,next_due=? WHERE id=1",
                   (payload.enabled, payload.interval_minutes, payload.max_tokens, payload.news_enabled, payload.rss_url, payload.randomize_interval, payload.random_min_minutes, payload.random_max_minutes, next_due))
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
                text, kind, sources, usage = await generate_proactive(setting, config, prompt, history, now.isoformat(timespec="seconds"))
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
        db.execute("UPDATE proactive_plugin SET last_content=?,total_tokens=total_tokens+?,failure_count=0,last_error='' WHERE id=1", (text, usage))
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
    model_messages.extend(history)
    model_messages.append({"role": "user", "content": payload.content})

    await MODEL_GENERATION_LOCK.acquire()

    async def generate():
        complete = ""
        saved = False
        max_continuations = 3

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
                                token = choice["delta"].get("content", "")
                                finish_reason = choice.get("finish_reason") or finish_reason
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                            if token:
                                complete += token
                                yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
                    if finish_reason != "length":
                        break
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
            if complete:
                save_complete_reply()
                done_event = {"done": True}
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
