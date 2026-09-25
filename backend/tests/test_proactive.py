import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
import tempfile

import httpx
from fastapi.testclient import TestClient
from app import database
from app.main import app
from app.proactive import care_window, recent_headlines


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
        return "今天想一起看看星星吗？", "care", [], 123, "2026-09-16:noon", {
            "expression": "happy", "emotion_label": "轻柔地邀请",
            "eyes": "soft", "mouth": "smile", "blush": .25, "gaze": "cursor",
        }

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
            assert config["history_weight"] == 15
            assert config["care_enabled"] is True
            assert config["care_weight"] == 45
            config["enabled"] = True
            config["history_weight"] = 10
            config["care_weight"] = 70
            assert client.put("/api/plugins/proactive", json=config).status_code == 200
            assert client.get("/api/plugins/proactive").json()["history_weight"] == 10
            assert client.get("/api/plugins/proactive").json()["care_weight"] == 70
            with database.connect() as db:
                db.execute("UPDATE proactive_plugin SET next_due=0 WHERE id=1")
            result = client.post("/api/plugins/proactive/generate", json=request).json()
            assert result["total_tokens"] == 123
            assert result["message_id"] > 0
            assert result["pet_motion"]["emotionLabel"] == "轻柔地邀请"
            assert result["pet_motion"]["eyes"] == "soft"
            assert result["pet_motion"]["mouth"] == "smile"
            assert result["pet_motion"]["action"] == "none"
            messages = client.get(f"/api/conversations/{result['conversation_id']}/messages").json()
            assert messages == []
            assert all(item["id"] != result["conversation_id"] for item in client.get("/api/conversations").json())
            with database.connect() as db:
                pending = db.execute("SELECT content,origin FROM messages WHERE id=?", (result["message_id"],)).fetchone()
                assert pending["content"] == result["content"] and pending["origin"] == "proactive"
            assert client.post("/api/plugins/proactive/generate", json=request).json()["reason"] == "cooldown"
            assert len(calls) == 1
            saved = client.get("/api/plugins/proactive").json()
            assert saved["total_tokens"] == 123
            assert saved["last_care_slot"] == "2026-09-16:noon"
            config["interval_minutes"] = 0
            assert client.put("/api/plugins/proactive", json=config).status_code == 422


def test_proactive_can_generate_during_previous_quiet_hours(monkeypatch):
    class FixedDate(datetime):
        current_hour = 2

        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 24, cls.current_hour, 30, tzinfo=timezone.utc)

    monkeypatch.setattr("app.main.datetime", FixedDate)
    generated_at = []

    async def fake_generate(setting, config, prompt, history, now):
        generated_at.append(now)
        return "夜里也可以聊聊。", "daily", [], 10, ""

    monkeypatch.setattr("app.main.generate_proactive", fake_generate)
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "night-proactive.db")
        with TestClient(app) as client:
            character = client.post("/api/characters", json={"name": "夜间角色"}).json()
            settings = client.get("/api/settings").json()
            settings["api_key"] = "mock"
            client.put("/api/settings", json=settings)
            config = client.get("/api/plugins/proactive").json()
            config["enabled"] = True
            client.put("/api/plugins/proactive", json=config)
            for hour in (2, 23):
                FixedDate.current_hour = hour
                with database.connect() as db:
                    db.execute("UPDATE proactive_plugin SET next_due=0 WHERE id=1")
                result = client.post("/api/plugins/proactive/generate", json={"character_id": character["id"]})
                assert result.status_code == 200
                assert result.json()["content"] == "夜里也可以聊聊。"
    assert len(generated_at) == 2


def test_frequency_change_reschedules_cooldown(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "frequency.db")
        with TestClient(app) as client:
            config = client.get("/api/plugins/proactive").json()
            with database.connect() as db:
                db.execute("UPDATE proactive_plugin SET enabled=1,interval_minutes=30,next_due=1600 WHERE id=1")
            monkeypatch.setattr("app.main.time.time", lambda: 1000)
            config.update(enabled=True, interval_minutes=5, randomize_interval=False)
            assert client.put("/api/plugins/proactive", json=config).status_code == 200
            updated = client.get("/api/plugins/proactive").json()
            assert updated["next_due"] == 1300
            assert updated["randomize_interval"] is False
            config["interval_minutes"] = 1
            assert client.put("/api/plugins/proactive", json=config).status_code == 200


def test_random_range_is_validated_and_used(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "random-frequency.db")
        with TestClient(app) as client:
            config = client.get("/api/plugins/proactive").json()
            config.update(enabled=True, randomize_interval=True, random_min_minutes=10, random_max_minutes=20)
            monkeypatch.setattr("app.main.time.time", lambda: 1000)
            monkeypatch.setattr("app.main.random.uniform", lambda minimum, maximum: 12.5)
            assert client.put("/api/plugins/proactive", json=config).status_code == 200
            assert client.get("/api/plugins/proactive").json()["next_due"] == 1750
            config.update(random_min_minutes=30, random_max_minutes=20)
            assert client.put("/api/plugins/proactive", json=config).status_code == 422


def test_manual_chat_can_cancel_active_proactive_task():
    import app.main as main

    async def run():
        started = asyncio.Event()

        async def active_generation():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(active_generation())
        main.PROACTIVE_GENERATION_TASK = task
        await started.wait()
        await main.cancel_active_proactive_generation()
        assert task.cancelled()
        assert task.done()
        main.PROACTIVE_GENERATION_TASK = None

    asyncio.run(run())


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
        assert body["messages"][3] == history[0]
        instruction = body["messages"][1]["content"]
        assert "人设优先" in instruction and "不凭空添加" in instruction
        assert "未指定语言" in instruction
        assert "你是桌宠主动互动插件" not in instruction
        return httpx.Response(200, json={"choices": [{"message": {"content": "今晚想观测猎户座吗？"}}], "usage": {"total_tokens": 99}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.proactive.random.random", lambda: 0.0)
    monkeypatch.setattr("app.proactive.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    setting = {"base_url": "https://model.example/v1", "api_key": "test", "model": "mock", "temperature": 0.8}
    config = {"news_enabled": False, "history_weight": 15, "care_enabled": False, "last_content": "", "max_tokens": 160}
    result = asyncio.run(generate_proactive(setting, config, prompt, history, "2026-09-16T20:00:00+08:00"))
    assert result[0] == "今晚想观测猎户座吗？"
    assert result[3] == 99 and len(captured) == 1


def test_proactive_model_tool_returns_speech_and_facial_expression(monkeypatch):
    from app.proactive import generate_proactive
    import json

    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None, "tool_calls": [{
                "function": {"name": "speak_with_pet_emotion", "arguments": json.dumps({
                    "content": "你在忙吗？我有个小发现。",
                    "expression": "curious", "emotion_label": "好奇地等回应",
                    "eyes": "wide", "mouth": "o", "blush": 0.2,
                    "effect": "question", "gaze": "cursor", "duration_ms": 2300,
                }, ensure_ascii=False)},
            }]}}],
            "usage": {"total_tokens": 78},
        })

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.proactive.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(generate_proactive(
        {"base_url": "https://api.deepseek.com/v1", "api_key": "mock", "model": "mock", "temperature": .8},
        {"news_enabled": False, "care_enabled": False, "last_content": "", "max_tokens": 1024, "_pet_motion_enabled": True},
        "角色卡", [], "2026-09-24T12:00:00+08:00",
    ))
    assert result[0] == "你在忙吗？我有个小发现。"
    assert result[5]["eyes"] == "wide"
    assert result[5]["mouth"] == "o"
    assert requests[0]["tools"][0]["function"]["name"] == "speak_with_pet_emotion"
    assert "桌宠情绪" in requests[0]["messages"][1]["content"]


def test_proactive_fallback_tag_is_hidden_from_speech(monkeypatch):
    from app.proactive import generate_proactive
    import json

    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '愿意听我说个小秘密吗？<pet_emotion>{"expression":"shy","emotion_label":"害羞又期待","eyes":"soft","mouth":"smile","blush":0.7,"gaze":"down"}</pet_emotion>'
        }}], "usage": {"total_tokens": 33}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.proactive.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(generate_proactive(
        {"base_url": "https://model.example/v1", "api_key": "mock", "model": "mock", "temperature": .8},
        {"news_enabled": False, "care_enabled": False, "last_content": "", "max_tokens": 1024, "_pet_motion_enabled": True},
        "角色卡", [], "2026-09-24T12:00:00+08:00",
    ))
    assert result[0] == "愿意听我说个小秘密吗？"
    assert result[5]["expression"] == "shy"
    assert "tools" not in captured[0]


def test_non_conversation_topics_do_not_receive_chat_history():
    from app.proactive import build_proactive_messages

    history = [
        {"role": "user", "content": "旧话题A"},
        {"role": "assistant", "content": "旧回答A"},
        {"role": "user", "content": "旧话题B"},
    ]
    messages = build_proactive_messages("角色卡", history, "现在", "daily", [], "上次主动内容")
    combined = "\n".join(message["content"] for message in messages)
    assert "旧话题A" not in combined
    assert "旧回答A" not in combined
    assert "旧话题B" not in combined
    assert "人自然会换话题" in combined


def test_care_windows_are_time_specific_and_have_daily_keys():
    morning_slot, morning_guidance = care_window(datetime.fromisoformat("2026-09-23T08:15:00+08:00"))
    afternoon_slot, afternoon_guidance = care_window(datetime.fromisoformat("2026-09-23T16:00:00+08:00"))
    quiet_slot, _ = care_window(datetime.fromisoformat("2026-09-23T10:30:00+08:00"))
    assert morning_slot == "2026-09-23:morning"
    assert "早餐" in morning_guidance
    assert afternoon_slot == "2026-09-23:afternoon"
    assert "眼睛" in afternoon_guidance
    assert quiet_slot == ""


def test_care_is_selected_at_most_once_per_time_window(monkeypatch):
    from app.proactive import generate_proactive

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": "记得让眼睛休息一下。"}}],
        "usage": {"total_tokens": 12},
    }))
    monkeypatch.setattr("app.proactive.random.random", lambda: 0.0)
    monkeypatch.setattr("app.proactive.httpx.AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    setting = {"base_url": "https://model.example/v1", "api_key": "mock", "model": "mock", "temperature": 0.8}
    config = {"news_enabled": False, "history_weight": 0, "care_enabled": True, "care_weight": 100, "last_content": "", "last_care_slot": "", "max_tokens": 160}
    first = asyncio.run(generate_proactive(setting, config, "角色卡", [], "2026-09-23T16:00:00+08:00"))
    assert first[1] == "care"
    assert first[4] == "2026-09-23:afternoon"
    config["last_care_slot"] = first[4]
    second = asyncio.run(generate_proactive(setting, config, "角色卡", [], "2026-09-23T16:30:00+08:00"))
    assert second[1] != "care"
    assert second[4] == ""


def test_conversation_topic_uses_only_two_recent_messages_as_light_context():
    from app.proactive import build_proactive_messages

    history = [
        {"role": "user", "content": "太早的内容"},
        {"role": "assistant", "content": "最近回答"},
        {"role": "user", "content": "最近问题"},
    ]
    messages = build_proactive_messages("角色卡", history, "现在", "conversation", [], "")
    combined = "\n".join(message["content"] for message in messages)
    assert "太早的内容" not in combined
    assert "最近回答" in combined
    assert "最近问题" in combined
    assert "只允许轻微承接" in combined


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
                                     {"news_enabled": False, "care_enabled": False, "last_content": "", "max_tokens": 1024}, "persona", [], "2026-09-16T20:00:00+08:00"))
    assert caught.value.usage == 160
    assert caught.value.finish_reason == "length"


def test_proactive_skips_while_manual_chat_owns_model_lock():
    from app.main import MODEL_GENERATION_LOCK, ProactiveRequest, proactive_generate

    async def run():
        async with MODEL_GENERATION_LOCK:
            return await proactive_generate(ProactiveRequest(character_id=1))

    assert asyncio.run(run()) == {"skipped": True, "reason": "chat_busy"}
