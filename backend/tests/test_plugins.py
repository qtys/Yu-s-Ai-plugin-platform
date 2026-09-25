import json
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.plugins import PluginManifest, PluginRegistry


def test_builtin_plugin_registry_rejects_duplicate_ids():
    registry = PluginRegistry()
    manifest = PluginManifest("demo", "演示", "说明", "1.0.0", (), ())
    registry.register(manifest)
    try:
        registry.register(manifest)
    except ValueError as error:
        assert "Duplicate" in str(error)
    else:
        raise AssertionError("Duplicate plugin ID was accepted")


def test_plugin_enablement_persists_and_gates_runtime(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.DATA_DIR / "plugins.db")
        with TestClient(app) as client:
            installed = {item["id"]: item for item in client.get("/api/plugins").json()}
            assert set(installed) == {"translation", "message_display", "conversation_environment", "proactive", "novel_reply", "instruction_review"}
            assert all(item["enabled"] and item["source"] == "builtin" for item in installed.values() if item["id"] not in {"novel_reply", "instruction_review"})
            assert installed["novel_reply"]["enabled"] is False
            assert installed["instruction_review"]["enabled"] is False
            assert installed["novel_reply"]["permissions"] == ["model_api"]
            assert installed["translation"]["permissions"] == ["local_storage", "network_download"]
            assert installed["conversation_environment"]["enabled"] is True
            assert client.put("/api/plugins/missing/state", json={"enabled": False}).status_code == 404

            response = client.put("/api/plugins/translation/state", json={"enabled": False})
            assert response.json() == {"id": "translation", "enabled": False}
            assert client.get("/api/translation/packages").status_code == 409
            assert client.post("/api/translation", json={"text": "hello", "source": "en", "target": "en"}).status_code == 409
            assert client.post("/api/translation/packages/zh/en/stream").status_code == 409
            assert client.put("/api/plugins/message_display/state", json={"enabled": False}).status_code == 200
            assert client.put("/api/plugins/proactive/state", json={"enabled": False}).status_code == 200
            assert client.put("/api/plugins/novel_reply/state", json={"enabled": True}).status_code == 200
            assert client.put("/api/plugins/instruction_review/state", json={"enabled": True}).status_code == 200
            assert client.get("/api/plugins/proactive").json()["plugin_enabled"] is False
            assert client.post("/api/plugins/proactive/generate", json={"character_id": 1}).json() == {"skipped": True, "reason": "plugin_disabled"}

        # The startup migration must not reset existing choices.
        with TestClient(app) as client:
            persisted = {item["id"]: item["enabled"] for item in client.get("/api/plugins").json()}
            assert persisted == {"translation": False, "message_display": False, "conversation_environment": True, "proactive": False, "novel_reply": True, "instruction_review": True}
            assert client.put("/api/plugins/novel_reply/state", json={"enabled": False}).status_code == 200
            assert client.put("/api/plugins/translation/state", json={"enabled": True}).status_code == 200
            assert client.post("/api/translation", json={"text": "hello", "source": "en", "target": "en"}).json() == {"translation": "hello"}


def test_novel_reply_toggle_changes_chat_prompt_without_rewriting_messages(monkeypatch):
    uploaded = []

    async def respond(request: httpx.Request):
        uploaded.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"你好"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.DATA_DIR / "novel.db")
        with TestClient(app) as client:
            setting = client.get("/api/settings").json()
            setting["api_key"] = "mock-key"
            assert client.put("/api/settings", json=setting).status_code == 200
            character_id = client.post("/api/characters", json={"name": "雨", "personality": "温柔"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            route = f"/api/conversations/{conversation_id}/chat"
            assert client.post(route, json={"content": "第一句", "character_id": character_id}).status_code == 200
            assert not any("小说式回复插件" in str(message) for message in uploaded[-1]["messages"])
            assert client.put("/api/plugins/novel_reply/state", json={"enabled": True}).status_code == 200
            assert client.post(route, json={"content": "第二句", "character_id": character_id}).status_code == 200
            messages = [request for request in uploaded if request.get("stream")][-1]["messages"]
            style = [message["content"] for message in messages if message["role"] == "system" and "小说式回复插件" in message["content"]]
            assert len(style) == 1 and "第三人称" in style[0] and "用户本轮明确要求" in style[0]
            assert messages[-1] == {"role": "user", "content": "第二句"}
            assert client.post(route, json={"content": "桌宠也用小说风格", "character_id": character_id, "pet_motion_enabled": True}).status_code == 200
            assert any("小说式回复插件" in str(message) for message in [request for request in uploaded if request.get("stream")][-1]["messages"])
            assert client.put("/api/plugins/novel_reply/state", json={"enabled": False}).status_code == 200
            assert client.post(route, json={"content": "恢复普通回复", "character_id": character_id}).status_code == 200
            assert not any("小说式回复插件" in str(message) for message in [request for request in uploaded if request.get("stream")][-1]["messages"])
            stored = client.get(f"/api/conversations/{conversation_id}/messages").json()
            assert [message["content"] for message in stored if message["role"] == "user"] == ["第一句", "第二句", "桌宠也用小说风格", "恢复普通回复"]


def test_plugin_table_is_added_to_existing_database(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.DATA_DIR / "older.db")
        database.init_db()
        with database.connect() as db:
            db.execute("DROP TABLE plugin_states")
        database.init_db()
        with database.connect() as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE name='plugin_states'").fetchone() is not None
            assert db.execute("SELECT name FROM sqlite_master WHERE name='plugin_device_states'").fetchone() is not None


def test_mobile_plugin_switches_are_device_scoped_and_gate_chat(monkeypatch):
    uploaded = []

    async def respond(request: httpx.Request):
        uploaded.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"你好"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    device_id = "android_test_device_001"
    other_device = "android_test_device_002"
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.DATA_DIR / "mobile_plugins.db")
        with TestClient(app) as client:
            assert client.get("/api/plugins?platform=android").status_code == 422
            mobile = {item["id"]: item for item in client.get(f"/api/plugins?platform=android&device_id={device_id}").json()}
            assert mobile["novel_reply"]["supported"] is True
            assert mobile["translation"]["supported"] is False
            assert mobile["proactive"]["supported"] is False
            assert client.put("/api/plugins/translation/state", json={"enabled": False, "platform": "android", "device_id": device_id}).status_code == 409
            assert client.put("/api/plugins/novel_reply/state", json={"enabled": True, "platform": "android", "device_id": device_id}).status_code == 200
            assert next(item for item in client.get("/api/plugins").json() if item["id"] == "novel_reply")["enabled"] is False
            assert next(item for item in client.get(f"/api/plugins?platform=android&device_id={other_device}").json() if item["id"] == "novel_reply")["enabled"] is False
            setting = client.get("/api/settings").json()
            setting["api_key"] = "mock-key"
            assert client.put("/api/settings", json=setting).status_code == 200
            character_id = client.post("/api/characters", json={"name": "雨"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            route = f"/api/conversations/{conversation_id}/chat"
            assert client.post(route, json={"content": "手机端", "character_id": character_id, "client_device_id": device_id}).status_code == 200
            assert any("小说式回复插件" in str(message) for message in uploaded[-1]["messages"])
            assert client.post(route, json={"content": "桌面端", "character_id": character_id}).status_code == 200
            assert not any("小说式回复插件" in str(message) for message in uploaded[-1]["messages"])
