import os
import tempfile
import base64
import asyncio
import json
import io
import zipfile

import httpx

from fastapi.testclient import TestClient

from app import database
from app.main import app
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
            assert "当前设备本地时间" in second_system_text
            assert "中国上海市" in second_system_text
            assert "不必特意提及时间" in second_system_text


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
