import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .database import connect, init_db
from .logging_config import LOG_FILE, configure_logging
from .translation import install_package, package_status, translate_text

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


def compile_character_prompt(character) -> str:
    fields = [
        ("角色名称", character["name"]), ("角色简介", character["description"]),
        ("身份背景", character["background"]), ("性格", character["personality"]),
        ("说话方式", character["speaking_style"]), ("与用户的关系", character["relationship"]),
        ("行为边界", character["boundaries"]), ("示例对话", character["example_dialogue"]),
        ("补充指令", character["system_prompt"]),
    ]
    return "\n\n".join(f"【{label}】\n{value.strip()}" for label, value in fields if value.strip())


class ConversationCreate(BaseModel):
    character_id: int
    title: str = "新对话"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class ChatRequest(BaseModel):
    content: str = Field(min_length=1)


class MessageUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=200000)


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
    logger.info("backend_started version=0.10.0")
    yield
    logger.info("backend_stopped")


app = FastAPI(title="Yu's AI API", version="0.10.0", lifespan=lifespan)
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
            "UPDATE settings SET base_url=?, api_key=?, model=?, temperature=?, max_tokens=?, context_message_limit=?, memory_limit=?, message_display_mode=? WHERE id=1",
            (payload.base_url.rstrip("/"), api_key, payload.model, payload.temperature, payload.max_tokens, payload.context_message_limit, payload.memory_limit, payload.message_display_mode),
        )
    return {"ok": True}


@app.get("/api/pet/state")
def get_pet_state():
    return rows("SELECT position_x, position_y FROM pet_state WHERE id = 1")[0]


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
        await install_package(source, target)
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
        raise HTTPException(409, str(exc)) from exc


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
        cursor = db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "会话不存在")
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
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
        ).fetchall()]
        context_limit = setting["context_message_limit"]
        history = all_history[-context_limit:]
        older = all_history[:-context_limit]
        summary = conversation["summary"]
        if older:
            summary = "\n".join(f"{item['role']}: {item['content']}" for item in older)[-4000:]
            db.execute("UPDATE conversations SET summary=? WHERE id=?", (summary, conversation_id))
        cursor = db.execute("INSERT INTO messages(conversation_id, role, content) VALUES (?, 'user', ?)", (conversation_id, payload.content))
        maybe_store_memory(db, conversation["character_id"], cursor.lastrowid, payload.content)
        memories = relevant_memories(db, conversation["character_id"], payload.content, setting["memory_limit"])
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
    model_messages.extend(history)
    model_messages.append({"role": "user", "content": payload.content})

    async def generate():
        complete = ""
        try:
            headers = {"Authorization": f"Bearer {setting['api_key']}", "Content-Type": "application/json"}
            body = {
                "model": setting["model"], "messages": model_messages, "stream": True,
                "temperature": setting["temperature"], "max_tokens": setting["max_tokens"],
            }
            async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                async with client.stream("POST", f"{setting['base_url'].rstrip('/')}/chat/completions", headers=headers, json=body) as response:
                    if response.status_code >= 400:
                        error = (await response.aread()).decode(errors="replace")
                        logger.warning("model_chat_failed status=%s", response.status_code)
                        yield json.dumps({"error": f"模型服务返回 {response.status_code}: {error[:500]}"}, ensure_ascii=False) + "\n"
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data: ") or line == "data: [DONE]":
                            continue
                        try:
                            data = json.loads(line[6:])
                            token = data["choices"][0]["delta"].get("content", "")
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        if token:
                            complete += token
                            yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
            if complete:
                with connect() as db:
                    db.execute("INSERT INTO messages(conversation_id, role, content) VALUES (?, 'assistant', ?)", (conversation_id, complete))
                    db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
                yield json.dumps({"done": True}, ensure_ascii=False) + "\n"
                logger.info("model_chat_completed conversation_id=%s output_chars=%s", conversation_id, len(complete))
        except httpx.HTTPError as exc:
            logger.warning("model_chat_connection_failed conversation_id=%s error_type=%s detail=%s", conversation_id, type(exc).__name__, str(exc))
            yield json.dumps({"error": f"无法连接模型服务：{exc}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")
