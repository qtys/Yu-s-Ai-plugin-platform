import json
import asyncio
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import database
from app.instruction_review import length_issues
from app.main import app


def test_length_rules_are_checked_without_relying_on_model():
    assert length_issues("你好", ["每次回复 8 字到 20 字"]) == ["第 1 条：要求 8–20 字，实际 2 字"]
    assert length_issues("你好，老师", ["不少于4字", "不超过10字"]) == []


def test_semantic_instruction_revision_replaces_streamed_draft_and_stored_reply(monkeypatch):
    requests = []

    async def respond(request: httpx.Request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream"):
            if "请完整重写上一条回复" in body["messages"][-1]["content"]:
                return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"老师，"},"finish_reason":null}]}\ndata: {"choices":[{"delta":{"content":"您好。"},"finish_reason":"stop"}]}\ndata: [DONE]\n')
            return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"你好。"},"finish_reason":"stop"}]}\ndata: [DONE]\n')
        violations = ["第 1 条：没有称呼用户为老师"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"violations": violations}, ensure_ascii=False)}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "review.db")
        with TestClient(app) as client:
            setting = client.get("/api/settings").json()
            setting["api_key"] = "mock"
            assert client.put("/api/settings", json=setting).status_code == 200
            character_id = client.post("/api/characters", json={"name": "甲"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            assert client.post("/api/instructions", json={"character_id": character_id, "conversation_id": conversation_id, "content": "每次都称呼我为老师"}).status_code == 201
            assert client.put("/api/plugins/instruction_review/state", json={"enabled": True}).status_code == 200
            response = client.post(f"/api/conversations/{conversation_id}/chat", json={"content": "你好"})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert any(event.get("token") == "你好。" for event in events)
            assert [event.get("phase") for event in events if event.get("phase")] == ["reviewing", "revising"]
            assert "".join(event.get("revision_token", "") for event in events) == "老师，您好。"
            assert events[-1].get("done") is True
            assert client.get(f"/api/conversations/{conversation_id}/messages").json()[-1]["content"] == "老师，您好。"
            assert len(requests) == 3  # draft, review, revision; no final model review


def test_review_switch_off_skips_remote_review_even_with_saved_rules(monkeypatch):
    requests = []

    async def respond(request: httpx.Request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"你好。"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "review_off.db")
        with TestClient(app) as client:
            setting = client.get("/api/settings").json()
            setting["api_key"] = "mock"
            client.put("/api/settings", json=setting)
            character_id = client.post("/api/characters", json={"name": "甲"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            client.post("/api/instructions", json={"character_id": character_id, "conversation_id": conversation_id, "content": "每次都称呼我为老师"})
            response = client.post(f"/api/conversations/{conversation_id}/chat", json={"content": "你好"})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert [event.get("token") for event in events if event.get("token")] == ["你好。"]
            assert events[-1].get("done") is True
            assert len(requests) == 1


def test_review_timeout_keeps_draft_and_finishes(monkeypatch):
    requests = []

    async def respond(request: httpx.Request):
        body = json.loads(request.content)
        requests.append(body)
        if not body.get("stream"):
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"violations": []}'}}]})
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"你好。"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr("app.main.INSTRUCTION_REVIEW_DEADLINE_SECONDS", 0.01)
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "review_timeout.db")
        with TestClient(app) as client:
            setting = client.get("/api/settings").json()
            setting["api_key"] = "mock"
            client.put("/api/settings", json=setting)
            character_id = client.post("/api/characters", json={"name": "甲"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            client.post("/api/instructions", json={"character_id": character_id, "conversation_id": conversation_id, "content": "每次都称呼我为老师"})
            client.put("/api/plugins/instruction_review/state", json={"enabled": True})
            response = client.post(f"/api/conversations/{conversation_id}/chat", json={"content": "你好"})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1].get("done") is True
            assert not any(event.get("revision_token") for event in events)
            assert client.get(f"/api/conversations/{conversation_id}/messages").json()[-1]["content"] == "你好。"
            assert len(requests) == 2


def test_partial_revision_timeout_restores_original_reply(monkeypatch):
    async def respond(request: httpx.Request):
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"你好。"},"finish_reason":"stop"}]}\ndata: [DONE]\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"violations":["未称呼老师"]}'}}]})

    async def slow_revision(*_args):
        yield "老师，"
        await asyncio.sleep(0.05)
        yield "您好。"

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr("app.main.revise_reply", slow_revision)
    monkeypatch.setattr("app.main.INSTRUCTION_REVIEW_DEADLINE_SECONDS", 0.01)
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "partial_revision.db")
        with TestClient(app) as client:
            setting = client.get("/api/settings").json()
            setting["api_key"] = "mock"
            client.put("/api/settings", json=setting)
            character_id = client.post("/api/characters", json={"name": "甲"}).json()["id"]
            conversation_id = client.post("/api/conversations", json={"character_id": character_id}).json()["id"]
            client.post("/api/instructions", json={"character_id": character_id, "conversation_id": conversation_id, "content": "每次都称呼我为老师"})
            client.put("/api/plugins/instruction_review/state", json={"enabled": True})
            response = client.post(f"/api/conversations/{conversation_id}/chat", json={"content": "你好"})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert any(event.get("revision_token") == "老师，" for event in events)
            assert any(event.get("replace") == "你好。" for event in events)
            assert events[-1].get("done") is True
            assert client.get(f"/api/conversations/{conversation_id}/messages").json()[-1]["content"] == "你好。"
