import base64
import io
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import database, image_generation as images
from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "images.db")
    monkeypatch.setattr("app.speech.getproxies", lambda: {})
    monkeypatch.setattr("app.speech.proxy_bypass", lambda host: False)
    with TestClient(app) as value:
        yield value


def png():
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(output, "PNG")
    return output.getvalue()


def configure(client, **patch):
    assert client.put("/api/plugins/image_generation/state", json={"enabled": True}).status_code == 200
    assert client.patch("/api/images/settings", json={"base_url": "https://images.example/v1/images/generations/", "api_key": "secret-image-key", "model": "demo-image", **patch}).status_code == 200


def mock(monkeypatch, respond):
    original = httpx.AsyncClient
    monkeypatch.setattr(images.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))


def test_defaults_validation_mask_migration(client):
    assert client.post("/api/images/generate", json={"prompt": "hello"}).status_code == 403
    assert client.post("/api/images/generate", json={"prompt": "   "}).status_code == 422
    configure(client)
    value = client.get("/api/images/settings").json()
    assert value["base_url"] == "https://images.example/v1"
    assert value["api_key"] == images.MASK
    assert client.patch("/api/images/settings", json={"api_key": images.MASK}).status_code == 200
    assert images.settings(False)["api_key"] == "secret-image-key"
    for patch in ({"base_url": "https://user:secret@example.org/v1"}, {"base_url": "file:///x"}, {"size": "invalid"}, {"model_profile_id": 999999}):
        assert client.patch("/api/images/settings", json=patch).status_code == 422
    database.init_db()
    assert images.settings(False)["model"] == "demo-image"


def test_generate_store_serve_no_chat_context(client, monkeypatch, caplog):
    configure(client)
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == "https://images.example/v1/images/generations"
        assert request.headers["authorization"] == "Bearer secret-image-key"
        assert json.loads(request.content) == {"prompt": "blue spirit", "model": "demo-image", "n": 1, "size": "1024x1024"}
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    mock(monkeypatch, respond)
    response = client.post("/api/images/generate", json={"prompt": "blue spirit"})
    assert response.status_code == 200, response.text
    value = response.json()
    assert client.get(value["image_url"]).content == png()
    assert "attachment" in client.get(value["image_url"] + "?download=true").headers["content-disposition"]
    assert client.get("/api/images").json() == [value]
    assert len(calls) == 1
    assert "secret-image-key" not in caplog.text and "blue spirit" not in caplog.text
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert client.get("/api/images/../../settings/file").status_code != 200


def test_reuse_profile_does_not_switch_chat_model(client, monkeypatch):
    profile = client.post("/api/model-profiles", json={"name": "image service", "base_url": "https://profile.example/v1", "api_key": "profile-key", "model": "text-only"}).json()
    configure(client, model_profile_id=profile["id"], quality="high", response_format="b64_json")
    previous = client.get("/api/settings").json()["model"]
    def respond(request):
        assert request.headers["authorization"] == "Bearer profile-key"
        assert request.url.host == "profile.example"
        assert json.loads(request.content)["quality"] == "high"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    mock(monkeypatch, respond)
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 200
    assert client.get("/api/settings").json()["model"] == previous
    assert client.patch("/api/images/settings", json={"model_profile_id": None}).json()["model_profile_id"] is None


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_error_does_not_retry_or_expose_upstream(client, monkeypatch, status):
    configure(client)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "secret-image-key"})
    mock(monkeypatch, respond)
    response = client.post("/api/images/generate", json={"prompt": "draw"})
    assert response.status_code == 502
    assert "secret-image-key" not in response.text and "未自动重试" in response.text
    assert len(calls) == 1 and client.get("/api/images").json() == []


@pytest.mark.parametrize("data", [[], {}, {"data": []}, {"data": ["not an object"]}, {"data": [{"b64_json": "wrong!"}]}, {"data": [{"b64_json": base64.b64encode(b"not image").decode()}]}])
def test_malformed_response(client, monkeypatch, data):
    configure(client)
    mock(monkeypatch, lambda _: httpx.Response(200, json=data))
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 502
    assert client.get("/api/images").json() == []


def test_url_download_pins_public_address_and_never_sends_key(client, monkeypatch):
    configure(client)
    async def resolve(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr("asyncio.BaseEventLoop.getaddrinfo", resolve)
    calls = []
    def respond(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/result.png?signature=test"}]})
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "cdn.example"
        assert request.extensions["sni_hostname"] == "cdn.example"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=png())
    mock(monkeypatch, respond)
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "198.18.0.2", "224.0.0.1", "::1", "fc00::1", "fe80::1", "ff02::1"])
def test_private_url_blocked(client, monkeypatch, ip):
    configure(client)
    async def resolve(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
    monkeypatch.setattr("asyncio.BaseEventLoop.getaddrinfo", resolve)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://local.example/pic"}]})
    mock(monkeypatch, respond)
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 502
    assert len(calls) == 1


def test_mixed_dns_uses_only_public_ip(client, monkeypatch):
    configure(client)
    async def resolve(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
                for ip in ("127.0.0.1", "198.18.0.2", "93.184.216.34")]
    monkeypatch.setattr("asyncio.BaseEventLoop.getaddrinfo", resolve)
    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/pic?signature=secret"}]})
        assert request.url.host == "93.184.216.34"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=png())
    mock(monkeypatch, respond)
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 200


def test_fake_ip_rejection_has_stage_and_no_signed_url(client, monkeypatch, caplog):
    configure(client)
    async def resolve(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.2", 443))]
    monkeypatch.setattr("asyncio.BaseEventLoop.getaddrinfo", resolve)
    mock(monkeypatch, lambda _: httpx.Response(200, json={"data": [{"url": "https://cdn.example/pic?signature=secret"}]}))
    response = client.post("/api/images/generate", json={"prompt": "private prompt"})
    assert response.status_code == 502 and "Fake-IP" in response.json()["detail"]
    assert "stage=download_image" in caplog.text and "fake_ip_range=True" in caplog.text
    assert "signature=secret" not in caplog.text and "private prompt" not in caplog.text


def test_upstream_code_without_sensitive_message(client, monkeypatch, caplog):
    configure(client)
    mock(monkeypatch, lambda _: httpx.Response(429, headers={"x-request-id": "req-123"},
         json={"error": {"code": "SetLimitExceeded", "message": "secret-image-key private prompt"}}))
    assert client.post("/api/images/generate", json={"prompt": "private prompt"}).status_code == 502
    assert "code=SetLimitExceeded" in caplog.text and "request_id=req-123" in caplog.text
    assert "secret-image-key" not in caplog.text and "private prompt" not in caplog.text


def test_plugin_log_diagnostic_rejects_path_traversal(client):
    assert client.get("/api/diagnostics/logs", params={"plugin_id": "../../yus-ai"}).status_code == 404
    assert client.get("/api/diagnostics/logs", params={"plugin_id": "image_generation"}).status_code == 200


def test_busy_and_response_size_limit(client, monkeypatch):
    configure(client)
    class Busy:
        def locked(self): return True
    monkeypatch.setattr(images, "GENERATION_LOCK", Busy())
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 409


def test_oversized_response_rejected(client, monkeypatch):
    configure(client)
    monkeypatch.setattr(images, "MAX_BYTES", 32)
    mock(monkeypatch, lambda _: httpx.Response(200, content=b"a" * 65))
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 502
    assert client.get("/api/images").json() == []


def test_timeout_releases_lock_without_retry(client, monkeypatch):
    configure(client)
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret-image-key", request=request)
    mock(monkeypatch, respond)
    response = client.post("/api/images/generate", json={"prompt": "draw"})
    assert response.status_code == 502
    assert "secret-image-key" not in response.text
    assert len(calls) == 1 and not images.GENERATION_LOCK.locked()


def test_deleted_profile_requires_reconfiguration(client, monkeypatch):
    profile = client.post("/api/model-profiles", json={"name": "drawing", "base_url": "https://profile.example/v1", "api_key": "profile-key", "model": "text-only"}).json()
    configure(client, model_profile_id=profile["id"])
    assert client.delete(f"/api/model-profiles/{profile['id']}").status_code == 200
    value = images.settings(False)
    assert value["model_profile_id"] is None and value["base_url"] == "" and value["api_key"] == ""
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 400


def test_image_backup_restore_preserves_files(client, monkeypatch):
    configure(client)
    mock(monkeypatch, lambda _: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}))
    image = client.post("/api/images/generate", json={"prompt": "draw"}).json()
    backup = client.post("/api/system/backups", json={"preferences": {}}).json()
    path = database.DATA_DIR / "generated-images" / f"{image['id']}.png"
    path.write_bytes(b"changed")
    with database.connect() as db:
        db.execute("DELETE FROM generated_images")
    assert client.post("/api/system/backups/restore", json={"path": backup["path"]}).status_code == 200
    assert client.get(image["image_url"]).content == png()


def image_conversation(client, content="少女站在雨中"):
    character_id = client.post("/api/characters", json={"name": "绘图测试"}).json()["id"]
    conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
    with database.connect() as db:
        message_id = db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,'assistant',?)", (conversation_id, content)).lastrowid
    return conversation_id, message_id


def test_source_image_with_extra_instructions_is_saved_in_own_chat(client, monkeypatch):
    configure(client)
    conversation_id, source_id = image_conversation(client)
    calls = []
    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["prompt"] == "少女站在雨中\n\n【额外绘图指令】\n水彩画，不要文字"
        assert "conversation_id" not in body and "source_message_id" not in body
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    mock(monkeypatch, respond)
    result = client.post("/api/images/generate", json={"conversation_id": conversation_id, "source_message_id": source_id, "extra_instructions": "水彩画，不要文字"}).json()
    assert result["conversation_id"] == conversation_id
    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert len(messages) == 3 and messages[-1]["image_id"] == result["id"]
    assert messages[-1]["id"] == result["message_id"] and messages[-2]["role"] == "user"
    assert "水彩画" in messages[-1]["content"] and len(calls) == 1
    assert client.get(f"/api/images/{result['id']}/file").content == png()
    database.init_db()  # Migration is repeatable and persisted messages survive.
    assert client.get(f"/api/conversations/{conversation_id}/messages").json()[-1]["image_id"] == result["id"]
    backup = client.post("/api/system/backups", json={"preferences": {}}).json()
    assert client.delete(f"/api/messages/{messages[-2]['id']}").status_code == 200
    assert len(client.get(f"/api/conversations/{conversation_id}/messages").json()) == 1
    assert client.get(f"/api/images/{result['id']}/file").status_code == 200  # Gallery survives withdrawal.
    assert client.post("/api/system/backups/restore", json={"path": backup["path"]}).status_code == 200
    assert client.get(f"/api/conversations/{conversation_id}/messages").json()[-1]["image_id"] == result["id"]


def test_source_validation_prevents_cross_chat_and_deleted_sources(client, monkeypatch):
    configure(client)
    conversation_id, source_id = image_conversation(client)
    other_id, _ = image_conversation(client)
    calls = []
    mock(monkeypatch, lambda request: calls.append(request) or httpx.Response(500))
    for payload, status in [
        ({"conversation_id": other_id, "source_message_id": source_id}, 404),
        ({"source_message_id": source_id}, 404),
        ({"conversation_id": conversation_id, "source_message_id": source_id, "source_excerpt": "旧的内容"}, 409),
        ({"conversation_id": 999999, "prompt": "draw"}, 404),
    ]:
        assert client.post("/api/images/generate", json=payload).status_code == status
    client.delete(f"/api/messages/{source_id}")
    assert client.post("/api/images/generate", json={"conversation_id": conversation_id, "source_message_id": source_id}).status_code == 404
    assert calls == []


def test_selected_excerpt_only_is_sent(client, monkeypatch):
    configure(client)
    conversation_id, source_id = image_conversation(client, "私人前文。少女站在雨中。私人后文。")
    def respond(request):
        assert json.loads(request.content)["prompt"] == "少女站在雨中"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    mock(monkeypatch, respond)
    assert client.post("/api/images/generate", json={"conversation_id": conversation_id, "source_message_id": source_id, "source_excerpt": "少女站在雨中"}).status_code == 200


def test_deleted_conversation_during_generation_keeps_gallery_without_cross_attach(client, monkeypatch):
    configure(client)
    conversation_id, _ = image_conversation(client)
    other_id, _ = image_conversation(client)
    def respond(request):
        with database.connect() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    mock(monkeypatch, respond)
    response = client.post("/api/images/generate", json={"conversation_id": conversation_id, "prompt": "draw"})
    assert response.status_code == 200 and response.json()["conversation_id"] is None
    assert len(client.get(f"/api/conversations/{other_id}/messages").json()) == 1
    assert len(client.get("/api/images").json()) == 1
