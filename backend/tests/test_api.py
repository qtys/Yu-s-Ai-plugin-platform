import json
import os
import tempfile
import asyncio

import httpx

from fastapi.testclient import TestClient

from app import database
from app.main import app


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
