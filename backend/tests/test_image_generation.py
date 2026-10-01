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


def test_private_url_blocked(client, monkeypatch):
    configure(client)
    async def resolve(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr("asyncio.BaseEventLoop.getaddrinfo", resolve)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://local.example/pic"}]})
    mock(monkeypatch, respond)
    assert client.post("/api/images/generate", json={"prompt": "draw"}).status_code == 502
    assert len(calls) == 1


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
