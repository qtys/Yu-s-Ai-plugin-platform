import json
import asyncio
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "withdraw.db")
    with TestClient(app) as test_client:
        test_client.put("/api/plugins/roleplay/state", json={"enabled": True})
        yield test_client


def seed(client):
    character_id = client.post("/api/characters", json={"name": "撤回测试"}).json()["id"]
    conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
    with database.connect() as db:
        ids = []
        for role, content, origin in [
            ("assistant", "开场白", "chat"),
            ("user", "我喜欢旧的秘密话题", "chat"),
            ("assistant", "旧话题的回复", "chat"),
            ("assistant", "未承接主动发言", "proactive"),
            ("assistant", "旧话题的追加回复", "chat"),
            ("user", "另一轮问题", "chat"),
            ("assistant", "另一轮回复", "chat"),
        ]:
            ids.append(db.execute("INSERT INTO messages(conversation_id,role,content,origin) VALUES (?,?,?,?)", (conversation_id, role, content, origin)).lastrowid)
        db.execute("INSERT INTO memories(character_id,content,source_message_id) VALUES (?,?,?)", (character_id, "我喜欢旧的秘密话题", ids[1]))
        db.execute("INSERT INTO memories(character_id,content) VALUES (?,?)", (character_id, "独立手动记忆"))
        db.execute("INSERT INTO saved_instructions(character_id,conversation_id,content,enabled) VALUES (?,?,?,1)", (character_id, conversation_id, "回答简洁"))
        db.execute("UPDATE conversations SET summary='旧的秘密话题摘要' WHERE id=?", (conversation_id,))
    return character_id, conversation_id, ids


def test_withdraw_user_removes_own_turn_and_generated_memory_only(client):
    character_id, conversation_id, ids = seed(client)
    other_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
    with database.connect() as db:
        other_message = db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,'assistant','其他对话')", (other_id,)).lastrowid
    response = client.delete(f"/api/messages/{ids[1]}")
    assert response.status_code == 200
    assert response.json()["deleted_ids"] == [ids[1], ids[2], ids[4]]
    assert response.json()["conversation_id"] == conversation_id
    with database.connect() as db:
        remaining = [row[0] for row in db.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))]
        assert remaining == [ids[0], ids[3], ids[5], ids[6]]
        assert db.execute("SELECT id FROM messages WHERE id=?", (other_message,)).fetchone()
        assert [row[0] for row in db.execute("SELECT content FROM memories")] == ["独立手动记忆"]
        assert db.execute("SELECT summary FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0] == ""
        assert db.execute("SELECT COUNT(*) FROM saved_instructions").fetchone()[0] == 1
    assert client.delete(f"/api/messages/{ids[1]}").status_code == 404


def test_withdraw_assistant_keeps_user_and_later_messages(client):
    _, conversation_id, ids = seed(client)
    assert client.delete(f"/api/messages/{ids[2]}").json()["deleted_ids"] == [ids[2]]
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0] == 6
        assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2
    assert client.delete("/api/messages/999999").status_code == 404


def test_withdraw_latest_user_without_next_turn(client):
    _, conversation_id, ids = seed(client)
    assert client.delete(f"/api/messages/{ids[5]}").json()["deleted_ids"] == [ids[5], ids[6]]
    assert [row["id"] for row in client.get(f"/api/conversations/{conversation_id}/messages").json()] == [ids[0], ids[1], ids[2], ids[4]]


def test_cancelled_stream_keeps_ids_and_can_withdraw_partial_turn(client, monkeypatch):
    from app.main import ChatRequest, chat, MODEL_GENERATION_LOCK
    character_id = client.post("/api/characters", json={"name": "中断测试"}).json()["id"]
    conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
    settings = client.get("/api/settings").json()
    settings["api_key"] = "test-only"
    client.put("/api/settings", json=settings)
    original = httpx.AsyncClient

    async def respond(request):
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"部分回复"},"finish_reason":null}]}\ndata: [DONE]\n')

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))

    async def cancel_after_first_token():
        response = await chat(conversation_id, ChatRequest(content="请继续"))
        iterator = response.body_iterator
        event = json.loads(await anext(iterator))
        assert event["token"] == "部分回复"
        with pytest.raises(asyncio.CancelledError):
            await iterator.athrow(asyncio.CancelledError())
        assert not MODEL_GENERATION_LOCK.locked()

    asyncio.run(cancel_after_first_token())
    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    user = next(message for message in messages if message["role"] == "user")
    assert user["id"]
    assert messages[-1]["content"] == "部分回复"
    response = client.delete(f"/api/messages/{user['id']}")
    assert response.status_code == 200
    assert user["id"] in response.json()["deleted_ids"]
    assert messages[-1]["id"] in response.json()["deleted_ids"]


def test_generation_blocks_withdrawal_without_mutation(client, monkeypatch):
    _, _, ids = seed(client)
    class BusyLock:
        def locked(self):
            return True
    monkeypatch.setattr("app.main.MODEL_GENERATION_LOCK", BusyLock())
    assert client.delete(f"/api/messages/{ids[1]}").status_code == 409
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 7


def test_database_failure_rolls_back_message_and_memory_deletion(client):
    _, conversation_id, ids = seed(client)
    with database.connect() as db:
        db.execute("CREATE TRIGGER prevent_test_delete BEFORE DELETE ON messages BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
    with pytest.raises(sqlite3.IntegrityError):
        client.delete(f"/api/messages/{ids[1]}")
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 7
        assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2
        assert db.execute("SELECT summary FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0] == "旧的秘密话题摘要"


def test_withdrawn_content_never_enters_next_model_request(client, monkeypatch):
    _, conversation_id, ids = seed(client)
    captured = []
    async def reply(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"新的回答"},"finish_reason":"stop"}]}\ndata: [DONE]\n')
    original = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs))
    settings = client.get("/api/settings").json()
    settings.update(api_key="mock", memory_limit=10, context_message_limit=2)
    assert client.put("/api/settings", json=settings).status_code == 200
    assert client.delete(f"/api/messages/{ids[1]}").status_code == 200
    assert client.post(f"/api/conversations/{conversation_id}/chat", json={"content": "秘密话题怎么样"}).status_code == 200
    prompt = json.dumps(captured[-1]["messages"], ensure_ascii=False)
    assert "我喜欢旧的秘密话题" not in prompt
    assert "旧话题的回复" not in prompt
    assert "旧话题的追加回复" not in prompt
    assert "旧的秘密话题摘要" not in prompt
    assert "回答简洁" in prompt
