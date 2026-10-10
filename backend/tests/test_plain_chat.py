import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app import database
from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "plain.db")
    with TestClient(app) as result:
        yield result


def test_plain_conversation_without_any_role_and_storage_isolation(client):
    first = client.post("/api/conversations", json={}).json()
    second = client.post("/api/conversations", json={"character_id": None}).json()
    assert first["character_id"] == second["character_id"]
    assert client.get("/api/characters").json() == []
    assert client.get(f"/api/conversations/{first['id']}/messages").json() == []
    role = client.post("/api/characters", json={"name": "角色"}).json()
    conversation = client.post("/api/conversations", json={"character_id": role["id"]}).json()
    assert {item["id"] for item in client.get("/api/conversations?plain=true").json()} == {first["id"], second["id"]}
    assert [item["id"] for item in client.get(f"/api/conversations?character_id={role['id']}").json()] == [conversation["id"]]
    database.init_db()
    assert [item["id"] for item in client.get("/api/characters").json()] == [role["id"]]


def test_disabled_plugin_does_not_send_role_data_and_does_not_consume_initialization(client, monkeypatch):
    requests = []
    original = httpx.AsyncClient
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"收到"},"finish_reason":"stop"}]}\ndata: [DONE]\n')
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    settings = client.get("/api/settings").json()
    settings["api_key"] = "mock-only"
    client.put("/api/settings", json=settings)
    role = client.post("/api/characters", json={"name": "私有角色名", "background": "私有背景", "greeting": "旧角色问候", "initial_prompt_enabled": True, "initial_prompt": "私有初始化"}).json()
    old = client.post("/api/conversations", json={"character_id": role["id"]}).json()
    client.post("/api/instructions", json={"character_id": role["id"], "content": "私有指令"})
    plain = client.post("/api/conversations", json={}).json()
    assert client.post(f"/api/conversations/{plain['id']}/chat", json={"content": "你好"}).status_code == 200
    assert not any(word in json.dumps(requests[-1], ensure_ascii=False) for word in ("私有", "旧角色问候"))
    # Switching off must not consume a role's one-time initialization.
    client.post(f"/api/conversations/{old['id']}/chat", json={"content": "测试"})
    with database.connect() as db:
        assert not db.execute("SELECT initial_prompt_applied FROM conversations WHERE id=?", (old["id"],)).fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    client.put("/api/plugins/roleplay/state", json={"enabled": True})
    client.post(f"/api/conversations/{old['id']}/chat", json={"content": "继续"})
    assert "私有初始化" in json.dumps(requests[-1], ensure_ascii=False)
    assert "私有背景" in json.dumps(requests[-1], ensure_ascii=False)
    # Plain chats remain plain even when the plugin is enabled again.
    client.post(f"/api/conversations/{plain['id']}/chat", json={"content": "还是普通对话"})
    assert "私有" not in json.dumps(requests[-1], ensure_ascii=False)


def test_role_proactive_pauses_when_role_plugin_is_off(client):
    role = client.post("/api/characters", json={"name": "测试角色"}).json()
    config = client.get("/api/plugins/proactive").json()
    config["enabled"] = True
    assert client.put("/api/plugins/proactive", json=config).status_code == 200
    with database.connect() as db:
        db.execute("UPDATE proactive_plugin SET next_due=0 WHERE id=1")
    assert client.post("/api/plugins/proactive/generate", json={"character_id": role["id"]}).json() == {"skipped": True, "reason": "roleplay_disabled"}
