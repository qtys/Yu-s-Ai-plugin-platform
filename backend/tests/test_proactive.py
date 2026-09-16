import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
import tempfile

import httpx
from fastapi.testclient import TestClient
from app import database
from app.main import app
from app.proactive import recent_headlines


def test_proactive_opt_in_cooldown_and_saved_history(monkeypatch):
    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("app.main.datetime", FixedDate)
    calls = []

    async def fake_generate(setting, config, prompt, history, now):
        assert "爱好天文" in prompt and "温柔" in prompt
        assert "2026-09-16" in now
        assert config["max_tokens"] == 1024
        calls.append(now)
        return "今天想一起看看星星吗？", "question", [], 123

    monkeypatch.setattr("app.main.generate_proactive", fake_generate)
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "proactive.db")
        with TestClient(app) as client:
            character = client.post("/api/characters", json={"name": "星星", "description": "爱好天文", "personality": "温柔"}).json()
            request = {"character_id": character["id"], "conversation_id": 9999}
            assert client.post("/api/plugins/proactive/generate", json=request).json()["skipped"]
            settings = client.get("/api/settings").json()
            settings["api_key"] = "test-key-not-used"
            client.put("/api/settings", json=settings)
            config = client.get("/api/plugins/proactive").json()
            config["enabled"] = True
            assert client.put("/api/plugins/proactive", json=config).status_code == 200
            result = client.post("/api/plugins/proactive/generate", json=request).json()
            assert result["total_tokens"] == 123
            messages = client.get(f"/api/conversations/{result['conversation_id']}/messages").json()
            assert messages[-1]["role"] == "assistant"
            assert messages[-1]["content"] == result["content"]
            assert client.post("/api/plugins/proactive/generate", json=request).json()["reason"] == "cooldown"
            assert len(calls) == 1
            assert client.get("/api/plugins/proactive").json()["total_tokens"] == 123
            config["interval_minutes"] = 0
            assert client.put("/api/plugins/proactive", json=config).status_code == 422


def test_frequency_change_rescales_remaining_cooldown(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "frequency.db")
        with TestClient(app) as client:
            config = client.get("/api/plugins/proactive").json()
            with database.connect() as db:
                db.execute("UPDATE proactive_plugin SET interval_minutes=30,next_due=2800 WHERE id=1")
            monkeypatch.setattr("app.main.time.time", lambda: 1000)
            config.update(interval_minutes=5, randomize_interval=False)
            assert client.put("/api/plugins/proactive", json=config).status_code == 200
            updated = client.get("/api/plugins/proactive").json()
            assert updated["next_due"] == 1300
            assert updated["randomize_interval"] is False
            config["interval_minutes"] = 1
            assert client.put("/api/plugins/proactive", json=config).status_code == 200


def test_actual_model_request_keeps_character_identity(monkeypatch):
    from app.proactive import generate_proactive
    import json

    prompt = "【角色名称】星星\n【性格】严肃\n【说话方式】短句\n【行为边界】不讲笑话\n【身份背景】天文学者"
    history = [{"role": "user", "content": "刚刚聊到猎户座"}]
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        assert body["messages"][0] == {"role": "system", "content": prompt}
        assert body["messages"][2] == history[0]
        instruction = body["messages"][1]["content"]
        assert "人设优先" in instruction and "不凭空添加" in instruction
        assert "未指定语言" in instruction
        assert "你是桌宠主动互动插件" not in instruction
        return httpx.Response(200, json={"choices": [{"message": {"content": "今晚想观测猎户座吗？"}}], "usage": {"total_tokens": 99}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.proactive.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    setting = {"base_url": "https://model.example/v1", "api_key": "test", "model": "mock", "temperature": 0.8}
    config = {"news_enabled": False, "last_content": "", "max_tokens": 160}
    result = asyncio.run(generate_proactive(setting, config, prompt, history, "2026-09-16T20:00:00+08:00"))
    assert result[0] == "今晚想观测猎户座吗？"
    assert result[3] == 99 and len(captured) == 1


def test_news_requires_recent_dated_sources():
    date = format_datetime(datetime.now(timezone.utc))
    xml = f'<rss><channel><item><title>Recent headline</title><link>https://example.com/news</link><pubDate>{date}</pubDate></item><item><title>Undated</title><link>https://example.com/old</link></item></channel></rss>'

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml))) as client:
            return await recent_headlines(client, "https://example.com/feed")

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0]["title"] == "Recent headline"


def test_empty_reply_tracks_tokens_and_backs_off(monkeypatch):
    from app.proactive import EmptyProactiveReply

    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("app.main.datetime", FixedDate)
    monkeypatch.setattr("app.main.time.time", lambda: 1000)

    async def empty(*args):
        raise EmptyProactiveReply(80, "length")

    monkeypatch.setattr("app.main.generate_proactive", empty)
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "failure.db")
        with TestClient(app) as client:
            character = client.post("/api/characters", json={"name": "Test"}).json()
            config = client.get("/api/plugins/proactive").json()
            config.update(enabled=True, interval_minutes=1)
            client.put("/api/plugins/proactive", json=config)
            settings = client.get("/api/settings").json()
            settings["api_key"] = "mock"
            client.put("/api/settings", json=settings)
            for attempt in range(3):
                with database.connect() as db:
                    db.execute("UPDATE proactive_plugin SET next_due=0 WHERE id=1")
                response = client.post("/api/plugins/proactive/generate", json={"character_id": character["id"], "conversation_id": 9999})
                assert response.status_code == 502
                assert "token" in response.json()["detail"]
                state = client.get("/api/plugins/proactive").json()
                assert state["next_due"] == (1060 if attempt < 2 else 1300)
                assert state["total_tokens"] == 80 * (attempt + 1)
                assert client.get("/api/conversations").json() == []


def test_model_empty_body_is_not_replaced_with_reasoning(monkeypatch):
    from app.proactive import EmptyProactiveReply, generate_proactive
    import pytest

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": None, "reasoning_content": "private reasoning"}, "finish_reason": "length"}],
        "usage": {"total_tokens": 160},
    }))
    monkeypatch.setattr("app.proactive.httpx.AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(EmptyProactiveReply) as caught:
        asyncio.run(generate_proactive({"base_url": "https://model.example/v1", "api_key": "mock", "model": "mock", "temperature": 0.8},
                                     {"news_enabled": False, "last_content": "", "max_tokens": 1024}, "persona", [], "now"))
    assert caught.value.usage == 160
    assert caught.value.finish_reason == "length"
