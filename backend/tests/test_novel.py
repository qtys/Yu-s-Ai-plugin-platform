import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.novel import NovelSettings, SentenceGate, build_prompt, violations


def test_compact_rules_only_include_enabled_features():
    config = NovelSettings(natural_style=False, character_motivation=False, continuity=False, hard_bans_enabled=False)
    prompt = build_prompt(config)
    assert "第三人称" in prompt
    assert "人物身份与口头习惯" not in prompt
    assert "价值与关系" not in prompt
    assert "硬性禁词" not in prompt
    assert len(prompt) < 700
    assert "核心角色" in build_prompt(NovelSettings())


def test_bans_literal_protected_content_and_contrast():
    config = NovelSettings(banned_terms="坏词\ntoken\n.*")
    assert violations("坏词。", config)
    assert violations("不是猜测，而是结论。", config)
    assert not violations('“坏词”。`token`\n```py\n坏词\n```\nhttps://example.com/坏词\nTOKEN123', config, "原句：坏词")
    assert violations(".*", config)
    assert not violations("其他文字", config)
    assert not violations("坏词", NovelSettings(hard_bans_enabled=False))


def test_sentence_gate_holds_split_terms_and_tail():
    gate = SentenceGate(NovelSettings(banned_terms="坏词"), "")
    assert gate.feed("她点头。坏") == "她点头。"
    assert gate.feed("词。继续。") == ""
    assert gate.blocked
    assert gate.feed("", final=True) == ""
    clean = SentenceGate(NovelSettings(), "")
    assert clean.feed("她点头") == ""
    assert clean.feed("。尾句") == "她点头。"
    assert clean.feed("", final=True) == "尾句"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    with TestClient(app) as value:
        yield value


def test_settings_persistence_validation_and_device_isolation(client):
    assert client.get("/api/plugins/novel_reply/settings").json()["hard_bans_enabled"]
    result = client.patch("/api/plugins/novel_reply/settings", json={"banned_terms": " 一条 \n一条\n二条", "detail_level": "rich"})
    assert result.status_code == 200
    assert result.json()["banned_terms"] == "一条\n二条"
    database.init_db()
    assert client.get("/api/plugins/novel_reply/settings").json()["detail_level"] == "rich"
    assert client.patch("/api/plugins/novel_reply/settings?device_id=phone", json={"detail_level": "light"}).status_code == 200
    assert client.get("/api/plugins/novel_reply/settings").json()["detail_level"] == "rich"
    assert client.get("/api/plugins/novel_reply/settings?device_id=phone").json()["detail_level"] == "light"
    for payload in ({"detail_level": "bad"}, {"unknown": True}, {"banned_terms": "字" * 81}):
        assert client.patch("/api/plugins/novel_reply/settings", json=payload).status_code == 422


def prepare_chat(client):
    settings = client.get("/api/settings").json()
    settings["api_key"] = "mock-key"
    assert client.put("/api/settings", json=settings).status_code == 200
    character = client.post("/api/characters", json={"name": "雨"}).json()["id"]
    conversation = client.post("/api/conversations", json={"character_id": character}).json()["id"]
    client.put("/api/plugins/novel_reply/state", json={"enabled": True})
    client.patch("/api/plugins/novel_reply/settings", json={"banned_terms": "坏词"})
    return character, conversation


@pytest.mark.parametrize("repair", ["她喝了一口水。", "坏词仍在。", None])
def test_hard_bans_repair_once_before_display_and_storage(client, monkeypatch, repair):
    character, conversation = prepare_chat(client)
    requests = []
    async def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) > 1 and repair is None:
            return httpx.Response(503)
        chunks = ["她点头。坏", "词。"] if len(requests) == 1 else [repair]
        sse = "".join("data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": "stop" if index == len(chunks) - 1 else None}]}, ensure_ascii=False) + "\n" for index, text in enumerate(chunks))
        return httpx.Response(200, content=sse + "data: [DONE]\n")
    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    response = client.post(f"/api/conversations/{conversation}/chat", json={"content": "继续故事", "character_id": character})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert len(requests) == 2
    assert not any("坏词" in event.get("token", "") or "坏词" in event.get("replace", "") for event in events)
    stored = client.get(f"/api/conversations/{conversation}/messages").json()
    assistants = [message["content"] for message in stored if message["role"] == "assistant"]
    if repair == "她喝了一口水。":
        assert assistants == [repair]
        assert events[-1]["done"]
    else:
        assert assistants == []
        assert events[-1].get("error")


def test_no_extra_call_when_clean_or_plugin_disabled(client, monkeypatch):
    character, conversation = prepare_chat(client)
    requests = []
    async def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"她点头。尾句"},"finish_reason":"stop"}]}\ndata: [DONE]\n')
    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    route = f"/api/conversations/{conversation}/chat"
    events = [json.loads(line) for line in client.post(route, json={"content": "继续", "character_id": character}).text.splitlines()]
    assert "".join(event.get("token", "") for event in events) == "她点头。尾句"
    assert len(requests) == 1
    client.put("/api/plugins/novel_reply/state", json={"enabled": False})
    client.post(route, json={"content": "继续", "character_id": character})
    assert len(requests) == 2
    assert not any("硬性禁词" in message["content"] for message in requests[-1]["messages"] if message["role"] == "system")
