import base64
import json

import httpx
import pytest
from app.speech import normalize_url, speech_http_options, parse_windows_proxy
from fastapi.testclient import TestClient

from app import database
from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.speech.getproxies", lambda: {})
    monkeypatch.setattr("app.speech.proxy_bypass", lambda host: False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "speech.db")
    with TestClient(app) as test_client:
        yield test_client


def configure(client):
    client.put("/api/plugins/speech/state", json={"enabled": True})
    assert client.patch("/api/speech/settings", json={
        "stt_base_url": "https://stt.example/v1/", "stt_api_key": "stt-secret", "stt_model": "recognition",
        "tts_base_url": "https://tts.example/v1", "tts_api_key": "tts-secret", "tts_model": "synthesis",
        "tts_voice": "voice-id", "tts_speed": 1.2,
    }).status_code == 200


def mock_upstream(monkeypatch, callback):
    original = httpx.AsyncClient
    monkeypatch.setattr("app.speech.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(callback), **kwargs))


def recording(**changes):
    return {"audio_base64": base64.b64encode(b"test-audio").decode(), "mime_type": "audio/webm;codecs=opus", **changes}


@pytest.mark.parametrize("suffix", ["/audio/transcriptions", "/audio/speech", "/audio/transcriptions/audio/transcriptions/"])
def test_full_endpoint_normalization(client, suffix):
    assert normalize_url("https://example.com/custom/v1" + suffix) == "https://example.com/custom/v1"
    configure(client)
    result = client.patch("/api/speech/settings", json={"stt_base_url": "https://example.com/v1" + suffix}).json()
    assert result["stt_base_url"] == "https://example.com/v1"
    with database.connect() as db:
        db.execute("UPDATE speech_settings SET stt_base_url=?", ("https://example.com/v1" + suffix,))
    assert client.get("/api/speech/settings").json()["stt_base_url"] == "https://example.com/v1"


def test_proxy_routing(client, monkeypatch):
    monkeypatch.setattr("app.speech.getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    assert speech_http_options("https://example.com/v1")["proxy"] == "http://127.0.0.1:7897"
    for url in ("http://localhost:8000", "https://127.0.0.1", "http://[::1]", "http://demo.localhost"):
        assert "proxy" not in speech_http_options(url)
    assert "proxy" not in speech_http_options("https://example.com", "direct")
    monkeypatch.setattr("app.speech.proxy_bypass", lambda host: True)
    assert "proxy" not in speech_http_options("https://example.com")


def test_windows_https_destination_uses_http_connect():
    assert parse_windows_proxy("127.0.0.1:7897") == {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
    assert parse_windows_proxy("https=127.0.0.1:7897")["https"] == "http://127.0.0.1:7897"
    assert parse_windows_proxy("https=https://proxy.example:443")["https"] == "https://proxy.example:443"


def test_connection_check_without_credentials_or_inference(client, monkeypatch):
    configure(client)
    options_seen = []
    original = httpx.AsyncClient
    monkeypatch.setattr("app.speech.getproxies", lambda: {"https": "http://127.0.0.1:7897"})

    def respond(request):
        assert request.method == "GET" and not request.content
        assert "authorization" not in request.headers
        return httpx.Response(404)

    def factory(**kwargs):
        options_seen.append(kwargs.copy())
        kwargs.pop("proxy", None)
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr("app.speech.httpx.AsyncClient", factory)
    result = client.post("/api/speech/connection-test", json={"kind": "stt"}).json()
    assert result["connected"] and result["http_status"] == 404 and result["proxy_used"]
    assert options_seen[0]["proxy"] == "http://127.0.0.1:7897"
    assert client.patch("/api/speech/settings", json={"proxy_mode": "direct"}).json()["proxy_mode"] == "direct"
    assert not client.post("/api/speech/connection-test", json={"kind": "tts"}).json()["proxy_used"]


def test_proxy_migration_preserves_settings(client):
    configure(client)
    with database.connect() as db:
        db.execute("ALTER TABLE speech_settings DROP COLUMN proxy_mode")
    database.init_db()
    with database.connect() as db:
        value = dict(db.execute("SELECT * FROM speech_settings").fetchone())
    assert value["proxy_mode"] == "auto" and value["stt_api_key"] == "stt-secret"


def test_proxy_applies_to_both_audio_apis(client, monkeypatch):
    configure(client)
    original = httpx.AsyncClient
    seen = []
    monkeypatch.setattr("app.speech.getproxies", lambda: {"all": "http://127.0.0.1:7897"})

    def respond(request):
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(200, json={"text": "测试"})
        return httpx.Response(200, content=b"audio", headers={"Content-Type": "audio/mpeg"})

    def factory(**kwargs):
        seen.append(kwargs.pop("proxy", None))
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr("app.speech.httpx.AsyncClient", factory)
    assert client.post("/api/speech/transcribe", json=recording()).status_code == 200
    assert client.post("/api/speech/synthesize", json={"text": "测试"}).status_code == 200
    assert seen == ["http://127.0.0.1:7897"] * 2


def test_network_error_is_sanitized(client, monkeypatch, caplog):
    configure(client)

    def fail(request):
        raise httpx.ConnectError("SSL EOF private-key-and-private-url", request=request)

    mock_upstream(monkeypatch, fail)
    result = client.post("/api/speech/connection-test", json={"kind": "stt"})
    assert result.status_code == 502 and "TLS" in result.json()["detail"]
    assert "private-key" not in result.text and "private-key" not in caplog.text
    assert "reason=tls" in caplog.text


def test_default_off_and_missing_configuration(client):
    assert client.post("/api/speech/transcribe", json=recording()).status_code == 403
    assert client.post("/api/speech/synthesize", json={"text": "你好"}).status_code == 403
    client.put("/api/plugins/speech/state", json={"enabled": True})
    assert client.post("/api/speech/transcribe", json=recording()).status_code == 400
    assert client.post("/api/speech/synthesize", json={"text": "你好"}).status_code == 400


def test_partial_autosave_masks_keys_and_preserves_other_settings(client):
    configure(client)
    result = client.get("/api/speech/settings").json()
    assert result["stt_api_key"] == result["tts_api_key"] == "••••••••"
    result = client.patch("/api/speech/settings", json={"stt_api_key": "••••••••", "tts_voice": "new-voice"}).json()
    assert result["stt_model"] == "recognition" and result["tts_voice"] == "new-voice"
    with database.connect() as db:
        assert db.execute("SELECT stt_api_key FROM speech_settings").fetchone()[0] == "stt-secret"
    client.patch("/api/speech/settings", json={"tts_api_key": ""})
    assert client.get("/api/speech/settings").json()["tts_api_key"] == ""
    database.init_db()
    assert client.get("/api/speech/settings").json()["tts_voice"] == "new-voice"


@pytest.mark.parametrize("url", ["file:///secret", "https://user:secret@example.com", "https://example.com/v1?token=secret", "https://example.com/#secret"])
def test_reject_invalid_urls(client, url):
    assert client.patch("/api/speech/settings", json={"stt_base_url": url}).status_code == 422


def test_transcription_uses_separate_api_and_does_not_create_messages(client, monkeypatch):
    configure(client)
    sent = []

    def respond(request):
        sent.append(request)
        assert request.url == "https://stt.example/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer stt-secret"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'recording.webm' in request.content and b'test-audio' in request.content
        assert b'recognition' in request.content
        return httpx.Response(200, json={"text": "  我想问个问题  "})

    mock_upstream(monkeypatch, respond)
    assert client.post("/api/speech/transcribe", json=recording()).json() == {"text": "我想问个问题"}
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert len(sent) == 1


def test_synthesis_returns_audio_without_caching(client, monkeypatch):
    configure(client)

    def respond(request):
        assert request.url == "https://tts.example/v1/audio/speech"
        assert request.headers["authorization"] == "Bearer tts-secret"
        assert json.loads(request.content) == {"model": "synthesis", "input": "你好", "voice": "voice-id", "speed": 1.2, "response_format": "mp3"}
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    mock_upstream(monkeypatch, respond)
    result = client.post("/api/speech/synthesize", json={"text": "你好"})
    assert result.status_code == 200 and result.content == b"ID3audio"
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_provider_errors_are_sanitized(client, monkeypatch, status):
    configure(client)
    mock_upstream(monkeypatch, lambda request: httpx.Response(status, text="tts-secret private-text"))
    for path, payload in [("transcribe", recording()), ("synthesize", {"text": "你好"})]:
        result = client.post(f"/api/speech/{path}", json=payload)
        assert result.status_code == 502
        assert "tts-secret" not in result.text and "private-text" not in result.text


def test_validation_and_malformed_provider_payload(client, monkeypatch):
    configure(client)
    assert client.post("/api/speech/transcribe", json=recording(audio_base64="bad!")).status_code == 422
    assert client.post("/api/speech/transcribe", json=recording(mime_type="image/png")).status_code == 422
    assert client.post("/api/speech/synthesize", json={"text": " "}).status_code == 422
    assert client.post("/api/speech/synthesize", json={"text": "a" * 4001}).status_code == 422
    assert client.patch("/api/speech/settings", json={"tts_speed": 0}).status_code == 422
    mock_upstream(monkeypatch, lambda request: httpx.Response(200, json={"not_text": "unexpected"}))
    assert client.post("/api/speech/transcribe", json=recording()).status_code == 502
    assert client.post("/api/speech/synthesize", json={"text": "你好"}).status_code == 502


def test_network_failures_are_sanitized(client, monkeypatch):
    configure(client)

    def respond(request):
        raise httpx.ConnectError("secret-provider-info", request=request)

    mock_upstream(monkeypatch, respond)
    result = client.post("/api/speech/transcribe", json=recording())
    assert result.status_code == 502 and "secret-provider-info" not in result.text


def test_audio_size_limits(client, monkeypatch):
    configure(client)
    monkeypatch.setattr("app.speech.MAX_RECORDING_BYTES", 4)
    assert client.post("/api/speech/transcribe", json=recording()).status_code == 413
    monkeypatch.setattr("app.speech.MAX_AUDIO_BYTES", 4)
    mock_upstream(monkeypatch, lambda request: httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"}))
    assert client.post("/api/speech/synthesize", json={"text": "你好"}).status_code == 502


def test_empty_audio_and_non_json_transcription_are_rejected(client, monkeypatch):
    configure(client)
    mock_upstream(monkeypatch, lambda request: httpx.Response(200, content=b"", headers={"Content-Type": "audio/mpeg"}))
    assert client.post("/api/speech/synthesize", json={"text": "你好"}).status_code == 502
    assert client.post("/api/speech/transcribe", json=recording()).status_code == 502
