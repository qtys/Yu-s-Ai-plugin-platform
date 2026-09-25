import json
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_deleted_conversation_memory_never_reaches_new_chat(monkeypatch):
    model_requests = []

    async def respond(request: httpx.Request):
        model_requests.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"收到"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "isolated.db")
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings.update(api_key="mock", memory_limit=10)
            assert client.put("/api/settings", json=settings).status_code == 200
            character_id = client.post("/api/characters", json={"name": "隔离测试"}).json()["id"]
            deleted_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            assert client.post(f"/api/conversations/{deleted_id}/chat", json={"content": "我喜欢紫色宇宙飞船"}).status_code == 200
            with database.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM memories WHERE character_id=?", (character_id,)).fetchone()[0] == 1

            assert client.delete(f"/api/conversations/{deleted_id}").status_code == 200
            with database.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM memories WHERE character_id=?", (character_id,)).fetchone()[0] == 0

            new_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            assert client.post(f"/api/conversations/{new_id}/chat", json={"content": "紫色宇宙飞船怎么样？"}).status_code == 200
            sent = model_requests[-1]["messages"]
            assert all("我喜欢紫色宇宙飞船" not in message["content"] for message in sent)
            assert client.get(f"/api/conversations/{new_id}/messages").json()[0]["content"] == "紫色宇宙飞船怎么样？"


def test_startup_removes_memories_orphaned_by_older_deletion(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "old.db")
        with TestClient(app) as client:
            character_id = client.post("/api/characters", json={"name": "旧版数据"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            with database.connect() as db:
                message_id = db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,'user','我喜欢旧话题')", (conversation_id,)).lastrowid
                db.execute("INSERT INTO memories(character_id,content,source_message_id) VALUES (?,?,?)", (character_id, "我喜欢旧话题", message_id))
                db.execute("INSERT INTO memories(character_id,content) VALUES (?,?)", (character_id, "手动保留的角色记忆"))
                db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
                assert db.execute("SELECT COUNT(*) FROM memories WHERE character_id=?", (character_id,)).fetchone()[0] == 2
            database.init_db()
            with database.connect() as db:
                assert [row[0] for row in db.execute("SELECT content FROM memories WHERE character_id=?", (character_id,)).fetchall()] == ["手动保留的角色记忆"]
