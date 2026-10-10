import json
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app, chat, ChatRequest, compile_character_prompt
from app.role_initialization import render_initial_prompt


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "roles.db")
    with TestClient(app) as result:
        yield result


def configure(client, **changes):
    client.put("/api/plugins/roleplay/state", json={"enabled": True})
    settings = client.get("/api/settings").json()
    settings["api_key"] = "mock-key"
    client.put("/api/settings", json=settings)
    character = client.post("/api/characters", json={"name": "小雨", "greeting": "你好", "initial_prompt_enabled": True, "initial_prompt": "首轮标记：{{角色名称}}", **changes}).json()
    conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
    return character, conversation


def mock_client(monkeypatch, responder):
    original = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(responder), **kwargs))


def response(text="自然回复。", finish="stop"):
    return httpx.Response(200, content="data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": finish}]}, ensure_ascii=False) + "\ndata: [DONE]\n")


def test_builtins_ship_without_private_template_database(client):
    with database.connect() as db:
        db.execute("INSERT INTO prompt_templates(name,content) VALUES ('私人内容','不得发布的个人指令')")
    templates = client.get("/api/role-initialization/templates").json()
    assert len(templates) == 4 and all(item["source"] == "builtin" for item in templates)
    immersive = next(item for item in templates if item["id"] == "builtin-immersive")
    assert immersive["name"] == "沉浸式角色开篇"
    assert "小雨" in render_initial_prompt(immersive["content"], "小雨")
    assert "开始续写" in immersive["content"]
    assert "私人内容" not in str(templates)
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM role_initialization_templates").fetchone()[0] == 0
    assert client.put("/api/role-initialization/templates/builtin-natural", json={"name": "改", "content": "改"}).status_code == 409
    assert client.delete("/api/role-initialization/templates/builtin-natural").status_code == 409


def test_custom_crud_does_not_change_role_snapshot(client):
    template = client.post("/api/role-initialization/templates", json={"name": "自定义", "content": "独立副本"}).json()
    role, _ = configure(client, initial_prompt=template["content"])
    route = "/api/role-initialization/templates/" + template["id"]
    assert client.put(route, json={"name": "更新", "content": "新内容"}).status_code == 200
    database.init_db()
    assert any(item["content"] == "新内容" for item in client.get("/api/role-initialization/templates").json())
    assert client.delete(route).status_code == 200
    stored = next(item for item in client.get("/api/characters").json() if item["id"] == role["id"])
    assert stored["initial_prompt"] == "独立副本"
    assert "独立副本" not in compile_character_prompt(stored)
    assert client.post("/api/role-initialization/templates", json={"name": " ", "content": " "}).status_code == 422
    assert client.put(f"/api/characters/{role['id']}", json={"name": "旧客户端更新"}).json()["initial_prompt"] == "独立副本"


def test_first_turn_only_not_greeting_or_reopen_and_new_chat_is_independent(client, monkeypatch):
    role, conversation = configure(client)
    requests = []
    async def respond(request):
        requests.append(json.loads(request.content))
        return response()
    mock_client(monkeypatch, respond)
    route = f"/api/conversations/{conversation['id']}/chat"
    assert not conversation["initial_prompt_applied"]
    for text in ("第一轮", "第二轮"):
        events = [json.loads(line) for line in client.post(route, json={"content": text}).text.splitlines()]
        assert events[-1]["initial_prompt_applied"]
    init = lambda body: [message["content"] for message in body["messages"] if message["role"] == "system" and "首轮角色初始化" in message["content"]]
    assert "首轮标记：小雨" in init(requests[0])[0]
    assert init(requests[1]) == []
    database.init_db()
    client.post(route, json={"content": "重开后第三轮"})
    assert init(requests[-1]) == []
    second = client.post("/api/conversations", json={"character_id": role["id"]}).json()
    client.post(f"/api/conversations/{second['id']}/chat", json={"content": "新对话"})
    assert init(requests[-1])


def test_failed_request_does_not_consume_and_disabled_first_turn_does_consume(client, monkeypatch):
    role, conversation = configure(client)
    requests = []
    async def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(503) if len(requests) == 1 else response()
    mock_client(monkeypatch, respond)
    route = f"/api/conversations/{conversation['id']}/chat"
    client.post(route, json={"content": "失败"})
    with database.connect() as db:
        assert db.execute("SELECT initial_prompt_applied FROM conversations WHERE id=?", (conversation["id"],)).fetchone()[0] == 0
    client.post(route, json={"content": "重试"})
    assert all(any("首轮角色初始化" in message["content"] for message in body["messages"] if message["role"] == "system") for body in requests)
    disabled, chat = configure(client, initial_prompt_enabled=False)
    client.post(f"/api/conversations/{chat['id']}/chat", json={"content": "普通第一轮"})
    client.put(f"/api/characters/{disabled['id']}", json={**disabled, "initial_prompt_enabled": True})
    client.post(f"/api/conversations/{chat['id']}/chat", json={"content": "中途开启不能重新初始化"})
    assert not any("首轮角色初始化" in message["content"] for message in requests[-1]["messages"] if message["role"] == "system")


def test_partial_connection_failure_keeps_pending(client, monkeypatch):
    _, conversation = configure(client)
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'
            raise httpx.ReadError("interrupted")
    async def respond(request):
        return httpx.Response(200, stream=BrokenStream())
    mock_client(monkeypatch, respond)
    client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "测试中断"})
    with database.connect() as db:
        assert db.execute("SELECT initial_prompt_applied FROM conversations WHERE id=?", (conversation["id"],)).fetchone()[0] == 0


def test_unfilled_variables_rejected_before_storing_user_and_rendering(client):
    assert render_initial_prompt("无变量", "雨") == "无变量"
    assert render_initial_prompt("{{ 角色名称 }}", "雨") == "雨"
    with pytest.raises(ValueError):
        render_initial_prompt("{{场景}}", "雨")
    _, conversation = configure(client, initial_prompt="{{场景}}")
    result = client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "还没填写"})
    assert result.status_code == 422
    assert not any(item["role"] == "user" for item in client.get(f"/api/conversations/{conversation['id']}/messages").json())


def test_legacy_conversations_are_not_reinitialized(client):
    _, conversation = configure(client)
    with database.connect() as db:
        db.execute("ALTER TABLE conversations DROP COLUMN initial_prompt_applied")
    database.init_db()
    with database.connect() as db:
        assert db.execute("SELECT initial_prompt_applied FROM conversations WHERE id=?", (conversation["id"],)).fetchone()[0] == 1


def test_cancelled_output_does_not_consume(client, monkeypatch):
    _, conversation = configure(client)
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'
            await asyncio.Event().wait()
    async def respond(request):
        return httpx.Response(200, stream=SlowStream())
    mock_client(monkeypatch, respond)
    async def cancel():
        stream = (await chat(conversation["id"], ChatRequest(content="中断测试"))).body_iterator
        await stream.__anext__()
        task = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())
    with database.connect() as db:
        assert db.execute("SELECT initial_prompt_applied FROM conversations WHERE id=?", (conversation["id"],)).fetchone()[0] == 0


def test_final_truncation_does_not_consume(client, monkeypatch):
    _, conversation = configure(client)
    async def respond(request):
        return response("未完的回复", "length")
    mock_client(monkeypatch, respond)
    events = [json.loads(line) for line in client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "长回复"}).text.splitlines()]
    assert events[-1]["truncated"] and not events[-1]["initial_prompt_applied"]


def test_novel_ban_repair_failure_does_not_consume(client, monkeypatch):
    _, conversation = configure(client)
    client.put("/api/plugins/novel_reply/state", json={"enabled": True})
    client.patch("/api/plugins/novel_reply/settings", json={"banned_terms": "禁用词"})
    async def respond(request):
        return response("禁用词。")
    mock_client(monkeypatch, respond)
    events = [json.loads(line) for line in client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "测试禁词"}).text.splitlines()]
    assert events[-1].get("error")
    with database.connect() as db:
        assert db.execute("SELECT initial_prompt_applied FROM conversations WHERE id=?", (conversation["id"],)).fetchone()[0] == 0
