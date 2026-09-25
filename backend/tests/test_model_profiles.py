import json
import sqlite3
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_existing_single_connection_is_migrated_without_losing_key(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "old.db")
        with sqlite3.connect(database.DB_PATH) as db:
            db.execute("""CREATE TABLE settings(id INTEGER PRIMARY KEY,base_url TEXT,api_key TEXT,model TEXT,
                temperature REAL,max_tokens INTEGER)""")
            db.execute("INSERT INTO settings VALUES (1,'https://api.deepseek.com/v1','old-secret','deepseek-v4-flash',0.8,2048)")
        database.init_db()
        with database.connect() as db:
            profile = db.execute("SELECT * FROM model_profiles").fetchone()
            setting = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
            assert profile["api_key"] == "old-secret"
            assert profile["model"] == "deepseek-v4-flash"
            assert setting["active_model_profile_id"] == profile["id"]
        database.init_db()
        with database.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM model_profiles").fetchone()[0] == 1


def test_profile_switch_edit_delete_and_model_requests(monkeypatch):
    sent = []

    async def respond(request: httpx.Request):
        sent.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)["model"]))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"你好"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "profiles.db")
        with TestClient(app) as client:
            initial = client.get("/api/model-profiles").json()
            assert len(initial) == 1 and initial[0]["active"]
            first_id = initial[0]["id"]
            other = client.post("/api/model-profiles", json={
                "name": "第二个接口", "base_url": "https://second.example/v1/", "api_key": "second-secret", "model": "second-model"
            }).json()
            assert other["api_key"] == "••••••••" and not other["active"]
            second_id = other["id"]
            assert client.post(f"/api/model-profiles/{second_id}/activate").status_code == 200
            active = client.get("/api/settings").json()
            assert active["active_model_profile_id"] == second_id
            assert active["api_key"] == "••••••••" and active["model"] == "second-model"

            character_id = client.post("/api/characters", json={"name": "模型切换测试"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            assert client.post(f"/api/conversations/{conversation_id}/chat", json={"content": "你好"}).status_code == 200
            assert sent[-1] == ("https://second.example/v1/chat/completions", "Bearer second-secret", "second-model")

            active.update(model="renamed-model", api_key="••••••••")
            assert client.put("/api/settings", json=active).status_code == 200
            assert client.get("/api/model-profiles").json()[1]["model"] == "renamed-model"
            renamed = client.patch(f"/api/model-profiles/{second_id}/name", json={"name": "自动保存名称"}).json()
            assert renamed["name"] == "自动保存名称"
            assert client.get("/api/settings").json()["model"] == "renamed-model"
            assert client.patch(f"/api/model-profiles/{second_id}/name", json={"name": " "}).status_code == 422
            updated = client.put(f"/api/model-profiles/{second_id}", json={
                "name": "已编辑", "base_url": "https://third.example/v1", "api_key": "••••••••", "model": "third-model"
            }).json()
            assert updated["name"] == "已编辑"
            assert client.get("/api/settings").json()["base_url"] == "https://third.example/v1"
            assert client.delete(f"/api/model-profiles/{second_id}").status_code == 200
            assert client.get("/api/settings").json()["active_model_profile_id"] == first_id
            assert client.delete(f"/api/model-profiles/{first_id}").status_code == 409
            assert client.post(f"/api/model-profiles/{second_id}/activate").status_code == 404
