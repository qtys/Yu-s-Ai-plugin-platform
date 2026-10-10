"""Opt-in OpenAI-compatible audio adapter. No recording/text/audio is persisted."""
import base64
import binascii
import logging
import ipaddress
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from urllib.request import getproxies_environment, proxy_bypass

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .database import connect
from .plugins import is_enabled

router = APIRouter(prefix="/api/speech", tags=["speech"])
logger = logging.getLogger("yus_ai.speech")
MASK = "••••••••"
MAX_RECORDING_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MIME_EXTENSIONS = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/wav": "wav", "audio/mpeg": "mp3"}


class SpeechSettingsPatch(BaseModel):
    proxy_mode: Literal["auto", "direct"] | None = None
    stt_base_url: str | None = Field(default=None, max_length=500)
    stt_api_key: str | None = Field(default=None, max_length=2048)
    stt_model: str | None = Field(default=None, max_length=200)
    tts_base_url: str | None = Field(default=None, max_length=500)
    tts_api_key: str | None = Field(default=None, max_length=2048)
    tts_model: str | None = Field(default=None, max_length=200)
    tts_voice: str | None = Field(default=None, max_length=200)
    tts_speed: float | None = Field(default=None, ge=0.25, le=4)


class TranscriptionRequest(BaseModel):
    audio_base64: str = Field(min_length=1, max_length=14 * 1024 * 1024)
    mime_type: str = Field(max_length=100)


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ConnectionTestRequest(BaseModel):
    kind: Literal["stt", "tts"] = "stt"


def settings(mask: bool = True) -> dict:
    with connect() as db:
        result = dict(db.execute("SELECT * FROM speech_settings WHERE id=1").fetchone())
    result.pop("id", None)
    for key in ("stt_base_url", "tts_base_url"):
        result[key] = normalize_url(result[key])
    if mask:
        for key in ("stt_api_key", "tts_api_key"):
            result[key] = MASK if result[key] else ""
    return result


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if value and (parsed.scheme not in {"http", "https"} or not parsed.hostname
                  or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(422, "语音 API 地址须为完整 HTTP/HTTPS 基础地址，不包含账号、查询参数或片段")
    # Accept either a base address or a full known audio endpoint, including
    # addresses saved by old versions. Never duplicate /audio/transcriptions.
    path = parsed.path.rstrip("/")
    while path.endswith(("/audio/transcriptions", "/audio/speech")):
        path = path.rsplit("/audio/", 1)[0].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")) if value else ""


def parse_windows_proxy(value: str) -> dict:
    """Windows protocol keys identify destinations, not the proxy's TLS scheme."""
    result = {}
    for entry in value.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        schemes, address = (entry.split("=", 1) if "=" in entry else ("http,https", entry))
        address = address.strip()
        # A normal Windows HTTPS proxy setting is an HTTP CONNECT proxy.
        # Preserve explicit schemes, including genuinely TLS-enabled proxies.
        if "://" not in address:
            address = "http://" + address
        for scheme in schemes.split(","):
            result[scheme.strip().lower()] = address
    return result


def getproxies() -> dict:
    environment = getproxies_environment()
    if environment:
        return environment
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            if winreg.QueryValueEx(key, "ProxyEnable")[0]:
                return parse_windows_proxy(str(winreg.QueryValueEx(key, "ProxyServer")[0]))
    except (ImportError, OSError, ValueError, TypeError):
        pass
    return {}


def speech_http_options(base_url: str, mode: str = "auto") -> dict:
    """Honor environment/Windows static proxies and bypass rules, not just httpx env.

    urllib reads Windows Internet Settings when no proxy environment is present.
    PAC scripts and SOCKS-only proxies are not executed or silently guessed.
    """
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    local = host.lower() == "localhost" or host.lower().endswith(".localhost")
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    # trust_env=False makes 'direct' actually direct; do not leak localhost
    # requests to a proxy, or silently bypass an explicitly selected route.
    options = {"trust_env": False, "follow_redirects": False}
    if mode == "direct" or local or proxy_bypass(host):
        return options
    proxies = getproxies()
    selected = proxies.get(parsed.scheme) or proxies.get("all")
    if selected:
        proxy = urlsplit(selected)
        if proxy.scheme not in {"http", "https"} or not proxy.hostname:
            raise HTTPException(400, "当前代理格式不支持，请在系统中配置 HTTP/HTTPS 代理，或在语音设置中选择直连")
        options["proxy"] = selected
    return options


def network_failure(exc: httpx.RequestError, kind: str, proxy_used: bool) -> HTTPException:
    if isinstance(exc, httpx.TimeoutException):
        reason = "timeout"
    else:
        # Classify locally, but never print the raw exception: it can contain
        # proxy credentials, service URLs or other private connection details.
        detail = str(exc).lower()
        reason = "tls" if any(word in detail for word in ("ssl", "tls", "eof", "certificate")) else "connection"
    logger.warning("speech_connection_failed kind=%s error_type=%s reason=%s proxy_used=%s",
                   kind, type(exc).__name__, reason, proxy_used)
    label = "语音识别" if kind == "stt" else "语音合成"
    explanation = {"timeout": "请求超时", "tls": "TLS 安全连接失败", "connection": "无法建立网络连接"}[reason]
    route = "已使用代理，请检查代理是否运行" if proxy_used else "当前未使用代理；可选择自动使用系统代理"
    return HTTPException(502, f"{label}服务{explanation}；{route}。请检查 API 地址及网络")


@router.get("/settings")
def get_settings():
    return settings()


@router.patch("/settings")
def patch_settings(payload: SpeechSettingsPatch):
    changes = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    for key, value in list(changes.items()):
        if key.endswith("api_key") and value == MASK:
            del changes[key]
        elif key.endswith("base_url"):
            changes[key] = normalize_url(value)
        elif isinstance(value, str):
            changes[key] = value.strip()
    if changes:
        # Column names are restricted to the Pydantic schema, never caller-supplied SQL.
        with connect() as db:
            db.execute(f"UPDATE speech_settings SET {','.join(f'{key}=?' for key in changes)} WHERE id=1", tuple(changes.values()))
    return settings()


def connection(kind: str, require_model: bool = True) -> tuple[str, dict, dict]:
    with connect() as db:
        if not is_enabled(db, "speech"):
            raise HTTPException(403, "请先在模型配置中启用语音模型")
    config = settings(False)
    if not config[f"{kind}_base_url"] or (require_model and not config[f"{kind}_model"]):
        raise HTTPException(400, "请先配置语音识别 API" if kind == "stt" else "请先配置语音合成 API")
    base = normalize_url(config[f"{kind}_base_url"])
    key = config[f"{kind}_api_key"]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return base, headers, config


@router.post("/connection-test")
async def connection_test(payload: ConnectionTestRequest):
    base, _, config = connection(payload.kind, require_model=False)
    options = speech_http_options(base, config["proxy_mode"])
    path = "audio/transcriptions" if payload.kind == "stt" else "audio/speech"
    try:
        # GET without authorization, recordings or synthesis input: no inference
        # requests, and no automatic retry that might cause duplicate charges.
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), **options) as client:
            response = await client.get(f"{base}/{path}")
        logger.info("speech_connection_test kind=%s status=%s proxy_used=%s",
                    payload.kind, response.status_code, "proxy" in options)
        return {"connected": True, "http_status": response.status_code, "proxy_used": "proxy" in options,
                "detail": "已连接到服务器；此检查不上传录音、不调用识别或合成，不验证密钥、模型或接口可用性"}
    except httpx.RequestError as exc:
        raise network_failure(exc, payload.kind, "proxy" in options) from exc


def upstream_error(status: int) -> HTTPException:
    detail = {
        401: "语音 API 密钥无效或已过期", 403: "语音 API 无访问权限",
        404: "未找到语音接口或模型，请确认服务支持 audio/transcriptions 或 audio/speech",
        429: "语音 API 配额不足或请求过于频繁",
    }.get(status, f"语音 API 请求失败（HTTP {status}），请检查模型和音色配置")
    logger.warning("speech_upstream_failed status=%s", status)
    return HTTPException(502, detail)


@router.post("/transcribe")
async def transcribe(payload: TranscriptionRequest):
    base, headers, config = connection("stt")
    mime = payload.mime_type.split(";", 1)[0].strip().lower()
    if mime not in MIME_EXTENSIONS:
        raise HTTPException(422, "录音格式不支持，请使用 WebM、OGG、MP4、WAV 或 MP3")
    try:
        audio = base64.b64decode(payload.audio_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "录音数据无效") from exc
    if not audio:
        raise HTTPException(422, "录音为空")
    if len(audio) > MAX_RECORDING_BYTES:
        raise HTTPException(413, "录音超过 10 MB，请分段录制")
    options = speech_http_options(base, config["proxy_mode"])
    logger.info("speech_transport kind=stt proxy_used=%s", "proxy" in options)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15), **options) as client:
            response = await client.post(f"{base}/audio/transcriptions", headers=headers,
                files={"file": (f"recording.{MIME_EXTENSIONS[mime]}", audio, mime)},
                data={"model": config["stt_model"], "response_format": "json"})
        if not response.is_success:
            raise upstream_error(response.status_code)
        result = response.json()
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str) or len(text) > 100_000:
            raise HTTPException(502, "语音识别接口未返回有效的 text 字段")
        logger.info("speech_transcribed recording_bytes=%s", len(audio))
        return {"text": text.strip()}
    except httpx.RequestError as exc:
        raise network_failure(exc, "stt", "proxy" in options) from exc
    except ValueError as exc:
        raise HTTPException(502, "语音识别接口返回了非 JSON 数据") from exc


@router.post("/synthesize")
async def synthesize(payload: SynthesisRequest):
    base, headers, config = connection("tts")
    if not payload.text.strip():
        raise HTTPException(422, "朗读内容不能为空")
    if not config["tts_voice"]:
        raise HTTPException(400, "请填写语音合成音色")
    options = speech_http_options(base, config["proxy_mode"])
    logger.info("speech_transport kind=tts proxy_used=%s", "proxy" in options)
    try:
        audio = bytearray()
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15), **options) as client:
            async with client.stream("POST", f"{base}/audio/speech", headers=headers, json={
                "model": config["tts_model"], "input": payload.text, "voice": config["tts_voice"],
                "speed": config["tts_speed"], "response_format": "mp3",
            }) as response:
                if not response.is_success:
                    raise upstream_error(response.status_code)
                if response.headers.get("content-type", "").split(";", 1)[0] not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                    raise HTTPException(502, "语音合成接口没有返回 MP3 音频，请确认接口兼容")
                async for chunk in response.aiter_bytes():
                    audio.extend(chunk)
                    if len(audio) > MAX_AUDIO_BYTES:
                        raise HTTPException(502, "语音接口返回的音频过大")
        if not audio:
            raise HTTPException(502, "语音合成接口返回了空音频")
        logger.info("speech_synthesized audio_bytes=%s", len(audio))
        return Response(bytes(audio), media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
    except httpx.RequestError as exc:
        raise network_failure(exc, "tts", "proxy" in options) from exc
