"""Explicit, opt-in Images API adapter. No chat context or automatic paid retries."""
import asyncio
import base64
import binascii
import io
import ipaddress
import json
import re
import socket
import uuid
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from . import database
from .database import connect
from .plugins import is_enabled
from .speech import MASK, speech_http_options
from .logging_config import get_plugin_logger

router = APIRouter(prefix="/api/images", tags=["images"])
logger = get_plugin_logger("image_generation")
GENERATION_LOCK = asyncio.Lock()
MAX_BYTES = 30 * 1024 * 1024


class ImageSettingsPatch(BaseModel):
    model_profile_id: int | None = Field(default=None, gt=0)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=200)
    size: str | None = Field(default=None, pattern=r"^(auto|[1-9][0-9]{1,4}x[1-9][0-9]{1,4})$")
    quality: Literal["default", "auto", "standard", "hd", "low", "medium", "high"] | None = None
    response_format: Literal["auto", "b64_json", "url"] | None = None
    proxy_mode: Literal["auto", "direct"] | None = None


class GenerateRequest(BaseModel):
    prompt: str = Field(default="", max_length=12000)
    conversation_id: int | None = Field(default=None, gt=0)
    source_message_id: int | None = Field(default=None, gt=0)
    source_excerpt: str | None = Field(default=None, min_length=1, max_length=12000)
    extra_instructions: str = Field(default="", max_length=4000)


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if value and (parsed.scheme not in {"http", "https"} or not parsed.hostname
                  or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(422, "绘图 API 地址须为完整 HTTP/HTTPS 基础地址，不包含账号或查询参数")
    path = parsed.path.rstrip("/")
    while path.endswith("/images/generations"):
        path = path[:-len("/images/generations")].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")) if value else ""


def settings(mask=True):
    with connect() as db:
        value = dict(db.execute("SELECT * FROM image_settings WHERE id=1").fetchone())
    value.pop("id")
    if mask:
        value["api_key"] = MASK if value["api_key"] else ""
    return value


@router.get("/settings")
def get_settings():
    return settings()


@router.patch("/settings")
def patch_settings(payload: ImageSettingsPatch):
    changes = payload.model_dump(exclude_unset=True)
    for key, value in list(changes.items()):
        if (value is None and key != "model_profile_id") or (key == "api_key" and value == MASK):
            del changes[key]
        elif key == "base_url":
            changes[key] = normalize_url(value)
        elif isinstance(value, str):
            changes[key] = value.strip()
    with connect() as db:
        if changes.get("model_profile_id") and not db.execute("SELECT 1 FROM model_profiles WHERE id=?", (changes["model_profile_id"],)).fetchone():
            raise HTTPException(422, "选择的模型连接不存在")
        if changes:
            db.execute(f"UPDATE image_settings SET {','.join(f'{key}=?' for key in changes)} WHERE id=1", tuple(changes.values()))
    return settings()


async def bounded_body(response: httpx.Response, limit: int) -> bytes:
    data = bytearray()
    async for chunk in response.aiter_bytes():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(502, "绘图服务返回的数据过大，未保存")
    return bytes(data)


async def download_image(url: str) -> bytes:
    """Fetch public CDN images with no API key; pin DNS to prevent rebinding."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise HTTPException(502, "绘图服务返回的图片地址不是安全的 HTTPS 地址")
    addresses = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(entry[4][0] for entry in addresses))
    public_ips = [ip for ip in ips if ipaddress.ip_address(ip).is_global
                  and not ipaddress.ip_address(ip).is_multicast
                  and not ipaddress.ip_address(ip).is_reserved]
    fake_ip = any(ipaddress.ip_address(ip) in ipaddress.ip_network("198.18.0.0/15")
                  for ip in ips if ipaddress.ip_address(ip).version == 4)
    logger.info("image_download_dns host=%s resolved_count=%s public_count=%s blocked_count=%s fake_ip_range=%s",
                parsed.hostname, len(ips), len(public_ips), len(ips) - len(public_ips), fake_ip)
    if not public_ips:
        raise HTTPException(502, "图片下载域名未解析到公网地址，已拒绝访问内网或 Fake-IP；请检查代理/DNS，使用真实公网解析，或将返回格式改为 b64_json（需服务支持）。图片可能已生成，请勿重复提交")
    # Mixed DNS answers are safe when only the validated public address is pinned.
    address = public_ips[0]
    host = f"[{address}]" if ":" in address else address
    pinned_url = urlunsplit(("https", host, parsed.path, parsed.query, ""))
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        async with client.stream("GET", pinned_url, headers={"Host": parsed.hostname},
                                 extensions={"sni_hostname": parsed.hostname}) as response:
            if response.status_code != 200:
                logger.warning("image_download_failed status=%s host=%s", response.status_code, parsed.hostname)
                raise HTTPException(502, "图片已生成，但图片链接下载失败；未自动重试生成")
            return await bounded_body(response, MAX_BYTES)


def as_png(data: bytes) -> bytes:
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(502, "生成图片为空或超过 30 MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 25_000_000:
                raise HTTPException(502, "图片格式或尺寸不支持")
            image.load()
            result = io.BytesIO()
            image.convert("RGBA" if "A" in image.getbands() else "RGB").save(result, format="PNG")
            if result.tell() > MAX_BYTES:
                raise HTTPException(502, "图片解码后超过 30 MB")
            return result.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(502, "绘图服务返回了无法解析的图片") from exc


def image_record(row):
    return {**dict(row), "image_url": f"/api/images/{row['id']}/file"}


@router.get("")
def list_images():
    with connect() as db:
        return [image_record(row) for row in db.execute("SELECT * FROM generated_images ORDER BY created_at DESC,rowid DESC LIMIT 24").fetchall()]


@router.get("/{image_id}/file")
def image_file(image_id: uuid.UUID, download: bool = False):
    with connect() as db:
        if not db.execute("SELECT 1 FROM generated_images WHERE id=?", (str(image_id),)).fetchone():
            raise HTTPException(404, "图片不存在")
    path = database.DATA_DIR / "generated-images" / f"{image_id}.png"
    if not path.is_file():
        raise HTTPException(404, "图片文件已丢失")
    return FileResponse(path, media_type="image/png", filename=f"yus-ai-{image_id}.png" if download else None)


@router.post("/generate")
async def generate(payload: GenerateRequest):
    prompt = payload.prompt.strip()
    with connect() as db:
        if payload.conversation_id and not db.execute("SELECT 1 FROM conversations WHERE id=?", (payload.conversation_id,)).fetchone():
            raise HTTPException(404, "对话不存在，请重新选择")
        if payload.source_message_id:
            source = db.execute("SELECT content FROM messages WHERE id=? AND conversation_id=? AND origin!='proactive'", (payload.source_message_id, payload.conversation_id)).fetchone()
            if not source:
                raise HTTPException(404, "指定内容不在当前对话中，或已撤回")
            prompt = source["content"]
            if payload.source_excerpt:
                if payload.source_excerpt not in prompt:
                    raise HTTPException(409, "指定内容已被修改，请重新选择")
                prompt = payload.source_excerpt
        elif payload.source_excerpt:
            raise HTTPException(422, "选中文本需要指定来源消息")
        if not prompt:
            raise HTTPException(422, "请先描述想生成的图片")
        if len(prompt) > 12000:
            raise HTTPException(422, "指定内容过长，请选取不超过 12000 字的片段")
        if payload.extra_instructions.strip():
            prompt += "\n\n【额外绘图指令】\n" + payload.extra_instructions.strip()
        if not is_enabled(db, "image_generation"):
            raise HTTPException(403, "请先在模型配置中启用绘图模型")
        config = settings(False)
        if config["model_profile_id"]:
            profile = db.execute("SELECT base_url,api_key FROM model_profiles WHERE id=?", (config["model_profile_id"],)).fetchone()
            if not profile:
                raise HTTPException(400, "绘图模型连接已被删除，请重新选择")
            config.update(dict(profile))
    base = normalize_url(config["base_url"])
    if not base or not config["model"]:
        raise HTTPException(400, "请在模型配置的绘图模型中填写 API 地址和模型名称")
    if GENERATION_LOCK.locked():
        raise HTTPException(409, "已有图片正在生成，请勿重复提交")
    async with GENERATION_LOCK:
        operation_id = uuid.uuid4().hex[:12]
        stage = "submit"
        logger.info("image_generation_started operation_id=%s", operation_id)
        body = {"model": config["model"], "prompt": prompt, "n": 1, "size": config["size"]}
        if config["quality"] != "default":
            body["quality"] = config["quality"]
        if config["response_format"] != "auto":
            body["response_format"] = config["response_format"]
        headers = {"Authorization": f"Bearer {config['api_key']}"} if config["api_key"] else {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=15), **speech_http_options(base, config["proxy_mode"])) as client:
                async with client.stream("POST", f"{base}/images/generations", headers=headers, json=body) as response:
                    if response.status_code != 200:
                        # Read bounded metadata only; never log the response body, prompt or key.
                        error_code = "unknown"
                        try:
                            error_raw = await bounded_body(response, 16 * 1024)
                            error = json.loads(error_raw).get("error", {})
                            code = error.get("code", "") if isinstance(error, dict) else ""
                            if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,119}", code) and not (config["api_key"] and config["api_key"] in code):
                                error_code = code
                        except (ValueError, AttributeError, HTTPException):
                            pass
                        request_id = response.headers.get("x-request-id", "")
                        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", request_id) or config["api_key"] and config["api_key"] in request_id:
                            request_id = "unknown"
                        logger.warning("image_upstream_failed operation_id=%s status=%s code=%s request_id=%s", operation_id, response.status_code, error_code, request_id)
                        detail = {401: "绘图 API 密钥无效", 403: "绘图 API 无访问权限", 404: "未找到绘图接口或模型", 429: "绘图 API 配额不足或请求过于频繁"}.get(response.status_code, "请检查绘图模型、尺寸、质量和返回格式是否受服务支持")
                        raise HTTPException(502, f"{detail}（HTTP {response.status_code}）；未自动重试")
                    raw = await bounded_body(response, MAX_BYTES * 2)
            stage = "parse_response"
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get("data"), list) or not result["data"] or not isinstance(result["data"][0], dict):
                raise ValueError("invalid image envelope")
            item = result["data"][0]
            if item.get("b64_json"):
                stage = "decode_base64"
                data = base64.b64decode(item["b64_json"], validate=True)
            elif item.get("url"):
                stage = "download_image"
                data = await download_image(item["url"])
            else:
                raise ValueError("missing image")
            stage = "decode_image"
            png = await asyncio.to_thread(as_png, data)
        except HTTPException as exc:
            logger.warning("image_generation_rejected operation_id=%s stage=%s status=%s reason=%s", operation_id, stage, exc.status_code, exc.detail)
            raise
        except httpx.RequestError as exc:
            logger.warning("image_generation_connection_failed operation_id=%s stage=%s error_type=%s", operation_id, stage, type(exc).__name__)
            raise HTTPException(502, "绘图服务连接或下载超时，请检查地址、代理和网络；未自动重试，服务端可能已扣费") from exc
        except (ValueError, KeyError, IndexError, TypeError, binascii.Error) as exc:
            logger.warning("image_generation_invalid_response operation_id=%s stage=%s error_type=%s", operation_id, stage, type(exc).__name__)
            raise HTTPException(502, "返回内容不符合 Images API 格式，请确认服务支持 data[].b64_json 或 data[].url；未自动重试") from exc
        except OSError as exc:
            logger.warning("image_generation_os_failed operation_id=%s stage=%s error_type=%s", operation_id, stage, type(exc).__name__)
            raise HTTPException(502, "图片下载失败；未自动重试生成") from exc
        image_id = str(uuid.uuid4())
        directory = database.DATA_DIR / "generated-images"
        path = directory / f"{image_id}.png"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, png)
            with connect() as db:
                db.execute("INSERT INTO generated_images(id,prompt,model) VALUES (?,?,?)", (image_id, prompt, config["model"]))
                record = image_record(db.execute("SELECT * FROM generated_images WHERE id=?", (image_id,)).fetchone())
                # Save only to the original conversation; never redirect into a newly selected chat.
                attached = payload.conversation_id and db.execute("SELECT 1 FROM conversations WHERE id=?", (payload.conversation_id,)).fetchone()
                if attached:
                    db.execute("INSERT INTO messages(conversation_id,role,content,origin) VALUES (?,'user',?,'image_generation')",
                               (payload.conversation_id, "请根据以下内容生成图片：\n" + prompt))
                    message_id = db.execute("INSERT INTO messages(conversation_id,role,content,origin,image_id) VALUES (?,'assistant',?,'image_generation',?)",
                                            (payload.conversation_id, "已生成图片。绘图描述如下（仅描述，不代表聊天模型看到了图片）：\n" + prompt, image_id)).lastrowid
                    db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (payload.conversation_id,))
                    record.update(conversation_id=payload.conversation_id, message_id=message_id)
                elif payload.conversation_id:
                    record.update(conversation_id=None, message_id=None)
        except Exception as exc:
            logger.error("image_generation_save_failed operation_id=%s error_type=%s", operation_id, type(exc).__name__)
            path.unlink(missing_ok=True)
            raise
        logger.info("image_generated operation_id=%s image_id=%s bytes=%s", operation_id, image_id, len(png))
        return record
