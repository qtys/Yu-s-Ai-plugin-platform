import os
import tempfile
import base64
import asyncio
import json
import io
import zipfile
import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from fastapi.testclient import TestClient

from app import database
from app.main import app, build_local_time_context, normalize_pet_action, _latest_release
from app.documents import extract_visuals


def test_docx_embedded_image_is_extracted_for_multimodal_analysis():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\nmock")
    visuals = extract_visuals(payload.getvalue(), ".docx")
    assert len(visuals) == 1
    assert visuals[0]["name"] == "image1.png"
    assert visuals[0]["data_url"].startswith("data:image/png;base64,")


def test_deepseek_document_visuals_use_flash_model(monkeypatch):
    from app.main import model_summary

    captured = {}

    def handler(request: httpx.Request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "已读图"}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(model_summary(
        {"base_url": "https://api.deepseek.com", "api_key": "test", "model": "deepseek-v4-flash", "vision_model": "", "max_tokens": 2048},
        "SCH_test.pdf", "", "分析原理图", [{"name": "page.jpg", "page_number": 1, "data_url": "data:image/jpeg;base64,eA=="}],
    ))
    assert result == "已读图"
    assert captured["model"] == "deepseek-flash"
    assert captured["max_tokens"] == 393216
    assert captured["messages"][1]["content"][2]["type"] == "image_url"


def test_visual_reasoning_is_summarized_when_final_content_is_empty(monkeypatch):
    from app.main import model_summary

    requests = []

    def handler(request: httpx.Request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning_content": "识别到 U1 与 +3V3 网络相连。"},
                }],
            })
        return httpx.Response(200, json={"choices": [{"message": {"content": "U1 连接 +3V3。"}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(model_summary(
        {"base_url": "https://api.deepseek.com", "api_key": "test", "model": "deepseek-v4-flash", "vision_model": "", "max_tokens": 2048},
        "SCH_test.pdf", "", "分析原理图", [{"name": "page.jpg", "page_number": 1, "data_url": "data:image/jpeg;base64,eA=="}],
    ))
    assert result == "U1 连接 +3V3。"
    assert requests[0]["model"] == "deepseek-flash"
    assert requests[1]["model"] == "deepseek-v4-flash"
    assert "识别到 U1" in requests[1]["messages"][1]["content"]


def test_character_conversation_and_messages():
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "test.db"
        with TestClient(app) as client:
            assert client.get("/api/health").json() == {"status": "ok"}
            settings = client.get("/api/settings").json()
            assert settings["message_display_mode"] == "markdown"
            assert settings["translation_mirror_url"] == ""
            settings["message_display_mode"] = "plain"
            assert client.put("/api/settings", json=settings).json() == {"ok": True}
            assert client.get("/api/settings").json()["message_display_mode"] == "plain"
            character = client.post("/api/characters", json={"name": "测试角色", "greeting": "你好呀", "personality": "温柔"}).json()
            assert character["personality"] == "温柔"
            character = client.put(f"/api/characters/{character['id']}", json={**character, "speaking_style": "简洁"}).json()
            assert character["speaking_style"] == "简洁"
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            greeting = client.get(f"/api/conversations/{conversation['id']}/messages").json()[0]
            assert greeting["content"] == "你好呀"
            edited = client.put(f"/api/messages/{greeting['id']}", json={"content": "修改后的问候"})
            assert edited.status_code == 200
            assert edited.json()["content"] == "修改后的问候"
            assert client.get(f"/api/conversations/{conversation['id']}/messages").json()[0]["content"] == "修改后的问候"
            assert client.put("/api/messages/999999", json={"content": "不存在"}).status_code == 404
            assert client.put(f"/api/messages/{greeting['id']}", json={"content": "   "}).status_code == 422
            assert client.delete(f"/api/conversations/{conversation['id']}").json() == {"ok": True}
            assert client.get(f"/api/conversations/{conversation['id']}/messages").json() == []
            assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 404
            status = client.get("/api/diagnostics/status").json()
            assert status["database"]["characters"] == 1
            check = client.post("/api/diagnostics/commands", json={"command": "database_check"}).json()
            assert check == {"ok": True, "result": "ok"}
            marker = client.post(
                "/api/diagnostics/commands",
                json={"command": "log_marker", "marker": "automated-test"},
            ).json()
            assert marker["ok"] is True
            assert client.get("/api/diagnostics/logs?lines=20").status_code == 200
            assert client.post(
                "/api/translation",
                json={"text": "保持原文", "source": "zh", "target": "zh"},
            ).json() == {"translation": "保持原文"}


def test_translation_package_progress(monkeypatch):
    async def fake_install(source, target, progress=None, mirror_url=""):
        assert (source, target) == ("zh", "en")
        assert mirror_url == "https://mirror.example.com/argos"
        progress({"stage": "testing", "percent": 0, "tested": 0, "total_sources": 3})
        progress({"stage": "retrying", "percent": 0, "attempt": 2, "max_attempts": 3})
        progress({"stage": "downloading", "percent": 0, "downloaded": 0, "total": 100})
        progress({"stage": "downloading", "percent": 46, "downloaded": 50, "total": 100})
        progress({"stage": "installing", "percent": 96, "downloaded": 100, "total": 100})
        progress({"stage": "complete", "percent": 100, "downloaded": 100, "total": 100})

    monkeypatch.setattr("app.main.install_package", fake_install)
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "test-progress.db"
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings["translation_mirror_url"] = "https://mirror.example.com/argos/"
            assert client.put("/api/settings", json=settings).status_code == 200
            response = client.post("/api/translation/packages/zh/en/stream")
            events = [json.loads(line) for line in response.text.splitlines()]
            assert [event["stage"] for event in events] == ["testing", "retrying", "downloading", "downloading", "installing", "complete"]
            assert events[-1]["stage"] == "complete"


def test_translation_sources_include_mirror_and_official_fallbacks():
    from app.translation import PACKAGES, _candidate_urls

    urls = _candidate_urls(PACKAGES[("zh", "en")], "https://mirror.example.com/argos/")
    assert urls[0].startswith("https://mirror.example.com/argos/")
    assert any("hf-mirror.com" in url for url in urls)
    assert any("data.argosopentech.com" in url for url in urls)
    assert any("argos-net.com" in url for url in urls)
    assert len(urls) == len(set(urls))


def test_translation_download_resumes_partial_file():
    from app.translation import _download_source

    payload = b"abcdefghij"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=4-"
        return httpx.Response(206, headers={"Content-Range": "bytes 4-9/10"}, content=payload[4:])

    with tempfile.TemporaryDirectory() as directory:
        partial = database.Path(directory) / "model.argosmodel.part"
        marker = database.Path(directory) / "model.argosmodel.part.source"
        partial.write_bytes(payload[:4])
        marker.write_text("https://mirror.example/model.argosmodel", encoding="utf-8")
        events = []

        async def run_download():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await _download_source(client, marker.read_text(encoding="utf-8"), partial, marker, events.append)

        downloaded, total = asyncio.run(run_download())
        assert (downloaded, total) == (10, 10)
        assert partial.read_bytes() == payload
        assert events[-1]["resumed"] is True


def test_chat_streams_visible_tokens_and_saves_reply(monkeypatch):
    chunks = [
        b'data:{"choices":[{"delta":{"content":"\xe4\xbd\xa0\xe5\xa5\xbd"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"\xef\xbc\x8c\xe4\xb8\x96\xe7\x95\x8c"}}]}\n',
        b'data: [DONE]\n',
    ]

    uploaded_requests = []

    async def stream(request):
        uploaded_requests.append(json.loads(request.content))
        return httpx.Response(200, content=b"".join(chunks))

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(stream), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "chat-stream.db"
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings["api_key"] = "mock"
            client.put("/api/settings", json=settings)
            character = client.post("/api/characters", json={"name": "流式角色"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            document_text = "项目规定主动发言必须在用户空闲时进行，不能打断手动对话。"
            uploaded = client.post(f"/api/conversations/{conversation['id']}/documents", json={
                "filename": "设计说明.txt",
                "content_base64": base64.b64encode(document_text.encode()).decode(),
            })
            assert uploaded.status_code == 201
            with database.connect() as db:
                db.execute("UPDATE documents SET summary=? WHERE id=?", ("全文结论：用户对话优先。", uploaded.json()["id"]))
            with database.connect() as db:
                db.execute("INSERT INTO messages(conversation_id,role,content,origin) VALUES (?,'assistant',?,'proactive')", (conversation["id"], "这条主动发言只能留在本地"))
            proactive = client.get("/api/plugins/proactive").json()
            proactive.update(enabled=True, interval_minutes=5, randomize_interval=False)
            client.put("/api/plugins/proactive", json=proactive)
            before_chat_due = client.get("/api/plugins/proactive").json()["next_due"]
            response = client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "测试"})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert [event.get("token") for event in events if event.get("token")] == ["你好", "，世界"]
            assert events[-1] == {"done": True}
            messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
            assert messages[-1]["content"] == "你好，世界"
            assert messages[-3]["origin"] == "proactive"
            assert all(message["content"] != "这条主动发言只能留在本地" for message in uploaded_requests[0]["messages"])
            document_prompt = "\n".join(message["content"] for message in uploaded_requests[0]["messages"] if message["role"] == "system")
            assert "全文结论：用户对话优先" in document_prompt
            assert "不能打断手动对话" in document_prompt
            assert client.delete(f"/api/documents/{uploaded.json()['id']}").status_code == 200
            assert client.get("/api/plugins/proactive").json()["next_due"] >= before_chat_due


def test_chat_hides_and_validates_model_pet_action(monkeypatch):
    chunks = [
        'data: {"choices":[{"delta":{"content":"别担心，我在这里。\\n<pet_"},"finish_reason":null}]}\n',
        'data: {"choices":[{"delta":{"content":"action>{\\"expression\\":\\"shy\\",\\"action\\":\\"lean_left\\",\\"gaze\\":\\"none\\",\\"intensity\\":2,\\"duration_ms\\":9000,\\"offset_x\\":-9,\\"offset_y\\":2}</pet_action>"},"finish_reason":"stop"}]}\n',
        'data: [DONE]\n',
    ]
    uploaded = []

    async def stream(request):
        uploaded.append(json.loads(request.content))
        return httpx.Response(200, content="".join(chunks).encode())

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(stream), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "pet-action.db"
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings["api_key"] = "mock"
            client.put("/api/settings", json=settings)
            character = client.post("/api/characters", json={"name": "动作角色"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            response = client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "安慰我", "pet_motion_enabled": True})
            events = [json.loads(line) for line in response.text.splitlines()]
            visible = "".join(event.get("token", "") for event in events)
            assert visible.rstrip() == "别担心，我在这里。"
            assert "pet_action" not in visible
            assert events[-1]["pet_motion"] == {
                "expression": "shy", "action": "lean-left", "gazeMode": "none",
                "lookX": 0.0, "lookY": 0.0, "intensity": 1.0, "duration": 3500,
                "offsetX": -6.0, "offsetY": 2.0, "movement": "stay", "moveDistance": 0.0,
            }
            assert any("桌宠动作导演工具" in message["content"] for message in uploaded[0]["messages"] if message["role"] == "system")
            messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
            assert messages[-1]["content"] == "别担心，我在这里。"


def test_chat_environment_context_respects_time_and_location_switches(monkeypatch):
    uploaded_requests = []

    async def stream(request):
        uploaded_requests.append(json.loads(request.content))
        content = (
            'data: {"choices":[{"delta":{"content":"收到"},"finish_reason":"stop"}]}\n'
            'data: [DONE]\n'
        )
        return httpx.Response(200, content=content.encode())

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(stream), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "environment-context.db"
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings.update(api_key="mock", include_local_time=False, include_location_context=False, location_context="中国上海市")
            assert client.put("/api/settings", json=settings).status_code == 200
            character = client.post("/api/characters", json={"name": "环境测试角色"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            assert client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "第一次"}).status_code == 200
            first_system_text = "\n".join(message["content"] for message in uploaded_requests[-1]["messages"] if message["role"] == "system")
            assert "当前设备本地时间" not in first_system_text
            assert "中国上海市" not in first_system_text

            settings = client.get("/api/settings").json()
            settings.update(include_local_time=True, include_location_context=True, location_context="中国上海市")
            assert client.put("/api/settings", json=settings).status_code == 200
            saved = client.get("/api/settings").json()
            assert saved["include_local_time"] is True
            assert saved["include_location_context"] is True
            assert saved["location_context"] == "中国上海市"
            assert client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "第二次"}).status_code == 200
            second_system_text = "\n".join(message["content"] for message in uploaded_requests[-1]["messages"] if message["role"] == "system")
            assert "当前本机时间" in second_system_text
            assert "中国上海市" in second_system_text
            assert "不要声称无法获取当前时间" in second_system_text


def test_local_time_context_is_explicit_and_machine_independent():
    fixed = datetime(2026, 9, 22, 14, 5, 6, tzinfo=timezone(timedelta(hours=8), name="CST"))
    context = build_local_time_context(fixed)
    assert "2026年09月22日" in context
    assert "星期二" in context
    assert "14:05:06（下午）" in context
    assert "CST（UTC+08:00）" in context
    assert "2026-09-22T14:05:06+08:00" in context
    assert "相对时间" in context


def test_custom_pet_motion_is_safely_normalized():
    motion = normalize_pet_action({
        "expression": "surprised",
        "emotion_label": "先退缩再鼓起勇气",
        "action": "custom",
        "eyes": "wide",
        "mouth": "o",
        "blush": 1.8,
        "gaze": "cursor",
        "movement": "stay",
        "duration_ms": 1700,
        "easing": "spring",
        "repeat": 9,
        "effect": "sparkle",
        "body_keyframes": [
            {"at": 0, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
            {"at": .45, "x": -99, "y": -99, "rotate": 900, "scale_x": .2, "scale_y": 2},
            {"at": 1, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
        ],
        "face_keyframes": [
            {"at": 0, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
            {"at": 1, "x": 99, "y": -99, "rotate": 80, "scale_x": .1, "scale_y": 4},
        ],
        "crest_keyframes": [
            {"at": 0, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
            {"at": 1, "x": -99, "y": 99, "rotate": -90, "scale_x": .1, "scale_y": 4},
        ],
    })
    assert motion is not None
    assert motion["action"] == "custom"
    assert motion["emotionLabel"] == "先退缩再鼓起勇气"
    assert motion["eyes"] == "wide"
    assert motion["mouth"] == "o"
    assert motion["blush"] == 1.0
    assert motion["repeat"] == 3
    assert motion["easing"] == "spring"
    assert motion["effect"] == "sparkle"
    assert motion["keyframes"][1] == {"at": .45, "x": -18.0, "y": -40.0, "rotate": 540.0, "scaleX": .72, "scaleY": 1.3}
    assert motion["faceKeyframes"][1] == {"at": 1.0, "x": 10, "y": -10, "rotate": 20, "scaleX": .75, "scaleY": 1.25}
    assert motion["crestKeyframes"][1] == {"at": 1.0, "x": -5, "y": 7, "rotate": -45, "scaleX": .7, "scaleY": 1.35}


def test_custom_pet_motion_rejects_invalid_or_unsafe_shapes():
    assert normalize_pet_action({"expression": "happy", "action": "custom", "gaze": "cursor", "movement": "stay", "body_keyframes": [{"at": 0}]}) is None
    assert normalize_pet_action({"expression": "happy", "action": "custom", "gaze": "cursor", "movement": "stay", "body_keyframes": [{"at": 0}, {"at": .5}, {"at": .5}, {"at": 1}]}) is None
    assert normalize_pet_action({"expression": "happy", "action": "custom", "gaze": "cursor", "movement": "stay", "eyes": "url(javascript:bad)", "body_keyframes": [{"at": 0}, {"at": 1}]}) is None
    assert normalize_pet_action({"expression": "happy", "action": "custom", "gaze": "cursor", "movement": "stay", "effect": "<script>", "body_keyframes": [{"at": 0}, {"at": 1}]}) is None
    assert normalize_pet_action({"expression": "happy", "action": "custom", "gaze": "cursor", "movement": "stay", "body_keyframes": [{"at": 0}, {"at": 1}], "face_keyframes": [{"at": 0}]}) is None


def test_custom_pet_motion_completes_missing_layers():
    motion = normalize_pet_action({
        "expression": "happy", "action": "custom", "gaze": "cursor", "movement": "stay",
        "body_keyframes": [
            {"at": 0, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
            {"at": .5, "x": 8, "y": -12, "rotate": 20, "scale_x": .9, "scale_y": 1.1},
            {"at": 1, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
        ],
    })
    assert motion is not None
    assert motion["generatedLayers"] == ["face", "crest"]
    assert len(motion["faceKeyframes"]) == 3
    assert len(motion["crestKeyframes"]) == 3
    assert 0 < motion["motionQuality"] < 100


def test_chat_accepts_streamed_pet_action_tool_call(monkeypatch):
    arguments = json.dumps({
        "expression": "happy", "emotion_label": "侧跳后回头", "action": "custom", "gaze": "cursor",
        "movement": "stay", "intensity": .8, "duration_ms": 1600, "effect": "star",
        "body_keyframes": [
            {"at": 0, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
            {"at": 1, "x": 0, "y": 0, "rotate": 0, "scale_x": 1, "scale_y": 1},
        ],
    }, ensure_ascii=False)
    chunks = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "好呀！", "tool_calls": [{"index": 0, "function": {"name": "perform_pet_action", "arguments": arguments[:80]}}]}, "finish_reason": None}]}, ensure_ascii=False) + "\n",
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": arguments[80:]}}]}, "finish_reason": "tool_calls"}]}, ensure_ascii=False) + "\n",
        "data: [DONE]\n",
    ]
    uploaded = []

    async def stream(request):
        uploaded.append(json.loads(request.content))
        return httpx.Response(200, content="".join(chunks).encode())

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(stream), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "pet-tool-call.db"
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings.update(api_key="mock", base_url="https://api.deepseek.com/v1")
            client.put("/api/settings", json=settings)
            character = client.post("/api/characters", json={"name": "工具角色"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            response = client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "演一个动作", "pet_motion_enabled": True})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert "tools" in uploaded[0]
            assert "tool_choice" in uploaded[0]
            assert "".join(event.get("token", "") for event in events) == "好呀！"
            assert events[-1]["pet_motion"]["emotionLabel"] == "侧跳后回头"
            assert events[-1]["pet_motion"]["effect"] == "star"
            assert events[-1]["pet_motion"]["generatedLayers"] == ["face", "crest"]


def test_chat_auto_continues_when_model_hits_length_limit(monkeypatch):
    calls = []

    async def stream(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            content = (
                'data: {"choices":[{"delta":{"content":"第一段"},"finish_reason":null}]}\n'
                'data: {"choices":[{"delta":{"content":""},"finish_reason":"length"}]}\n'
                'data: [DONE]\n'
            )
        else:
            content = (
                'data: {"choices":[{"delta":{"content":"接下去"},"finish_reason":null}]}\n'
                'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}\n'
                'data: [DONE]\n'
            )
        return httpx.Response(200, content=content.encode())

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(stream), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "chat-continuation.db"
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings["api_key"] = "mock"
            client.put("/api/settings", json=settings)
            character = client.post("/api/characters", json={"name": "续写角色"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            response = client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "长回答"})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert [event.get("token") for event in events if event.get("token")] == ["第一段", "接下去"]
            assert any(event.get("continuing") for event in events)
            assert events[-1] == {"done": True}
            assert calls[1]["messages"][-2] == {"role": "assistant", "content": "第一段"}
            messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
            assert messages[-1]["content"] == "第一段接下去"


def test_backup_restore_round_trip_preserves_snapshot_and_creates_safety_copy():
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "backup-restore.db"
        document_dir = database.DATA_DIR / "documents"
        document_dir.mkdir(parents=True)
        (document_dir / "sample.txt").write_text("原始附件", encoding="utf-8")
        with TestClient(app) as client:
            original = client.post("/api/characters", json={"name": "备份中的角色"}).json()
            backup_response = client.post("/api/system/backups", json={"preferences": {"yus-ai-theme": "paper", "untrusted": "ignored"}})
            assert backup_response.status_code == 200
            backup = backup_response.json()
            assert database.Path(backup["path"]).is_file()
            assert backup["counts"]["characters"] == 1
            with zipfile.ZipFile(backup["path"]) as archive:
                assert {"manifest.json", "preferences.json", "database/yus_ai.db", "documents/sample.txt"}.issubset(archive.namelist())
                assert json.loads(archive.read("preferences.json")) == {"yus-ai-theme": "paper"}
            client.delete(f"/api/characters/{original['id']}")
            client.post("/api/characters", json={"name": "恢复前的新角色"})
            (document_dir / "sample.txt").write_text("已经改变", encoding="utf-8")
            restore_response = client.post("/api/system/backups/restore", json={"path": backup["path"]})
            assert restore_response.status_code == 200
            restored = restore_response.json()
            assert restored["requires_restart"] is True
            assert restored["preferences"] == {"yus-ai-theme": "paper"}
            assert database.Path(restored["safety_backup"]).is_file()
            assert [item["name"] for item in client.get("/api/characters").json()] == ["备份中的角色"]
            assert (document_dir / "sample.txt").read_text(encoding="utf-8") == "原始附件"


def test_update_check_and_verified_download(monkeypatch):
    installer = b"verified installer payload"
    digest = hashlib.sha256(installer).hexdigest()

    async def latest_release():
        return {
            "version": "9.9.9", "name": "测试更新", "notes": "更新说明", "published_at": "2026-09-22T00:00:00Z",
            "release_url": "https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/tag/v9.9.9",
            "asset": {
                "name": "Yus-AI-9.9.9-x64-setup.exe", "size": len(installer),
                "url": "https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/download/v9.9.9/Yus-AI-9.9.9-x64-setup.exe",
                "digest": f"sha256:{digest}",
            },
        }

    async def download(_request):
        return httpx.Response(200, content=installer)

    original_client = httpx.AsyncClient
    updater_client_options = []

    def updater_client(**kwargs):
        updater_client_options.append(kwargs.copy())
        return original_client(transport=httpx.MockTransport(download), **kwargs)

    monkeypatch.setattr("app.main._latest_release", latest_release)
    monkeypatch.setattr("app.main.httpx.AsyncClient", updater_client)
    with tempfile.TemporaryDirectory() as directory:
        database.DATA_DIR = database.Path(directory)
        database.DB_PATH = database.DATA_DIR / "update.db"
        with TestClient(app) as client:
            check = client.get("/api/system/update").json()
            assert check["available"] is True
            assert check["current_version"] == "0.15.3"
            response = client.post("/api/system/update/download")
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1]["stage"] == "complete"
            assert events[-1]["sha256"] == digest
            assert database.Path(events[-1]["path"]).read_bytes() == installer
            assert updater_client_options[-1]["trust_env"] is False


def test_update_check_falls_back_to_official_release_page(monkeypatch):
    digest = "a" * 64

    async def github(request):
        if request.url.host == "api.github.com":
            return httpx.Response(403, text="rate limit exceeded")
        if "expanded_assets" in request.url.path:
            return httpx.Response(200, text=(
                '<li class="Box-row"><a href="/qtys/Yu-s-Ai-plugin-platform/releases/download/'
                'v9.8.7/Yus-AI-9.8.7-x64-setup.exe">setup</a>'
                f'<span>sha256:{digest}</span><span>150 MB</span>'
                '<relative-time datetime="2026-09-22T00:00:00Z"></relative-time></li>'
            ))
        return httpx.Response(200, text=(
            '<html><head><title>Release Yus AI v9.8.7 · GitHub</title></head>'
            '<body><a href="/qtys/Yu-s-Ai-plugin-platform/releases/tag/v9.8.7">release</a></body></html>'
        ))

    original_client = httpx.AsyncClient
    observed_options = []

    def github_client(**kwargs):
        observed_options.append(kwargs.copy())
        return original_client(transport=httpx.MockTransport(github), **kwargs)

    monkeypatch.setattr("app.main.httpx.AsyncClient", github_client)
    release = asyncio.run(_latest_release())
    assert release["version"] == "9.8.7"
    assert release["asset"]["size"] == 150 * 1024 * 1024
    assert release["asset"]["digest"] == f"sha256:{digest}"
    assert observed_options[0]["trust_env"] is False
