import base64
import asyncio
import json
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app, model_summary


IMAGE = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xfftest\xff\xd9").decode()


def test_screen_description_requires_opt_in_and_vision_model(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "screen.db")
        with TestClient(app) as client:
            request = {"image_data_url": IMAGE}
            assert client.post("/api/screen/describe", json=request).status_code == 403
            setting = client.get("/api/settings").json()
            assert setting["screen_access_enabled"] is False
            setting.update(screen_access_enabled=True, api_key="test-key")
            assert client.put("/api/settings", json=setting).status_code == 200
            setting["vision_model"] = "vision-test"
            assert client.put("/api/settings", json=setting).status_code == 200
            assert client.post("/api/screen/describe", json={"image_data_url": "data:image/jpeg;base64,AA=="}).status_code == 422

            def handler(http_request: httpx.Request):
                body = json.loads(http_request.content)
                assert body["model"] == "vision-test"
                assert body["messages"][1]["content"][1]["image_url"]["url"] == IMAGE
                return httpx.Response(200, json={"choices": [{"message": {"content": "屏幕上正在编辑代码。"}}]})

            original_client = httpx.AsyncClient
            monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
            assert client.post("/api/screen/describe", json=request).json() == {"description": "屏幕上正在编辑代码。"}


def test_saved_vision_profile_uses_its_own_endpoint_and_key(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)["model"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": "看到了图片。"}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "vision-profile.db")
        with TestClient(app) as client:
            profile = client.post("/api/model-profiles", json={
                "name": "图片专用", "base_url": "https://vision.example/v1", "api_key": "vision-secret", "model": "vision-model"
            }).json()
            setting = client.get("/api/settings").json()
            setting.update(api_key="chat-secret", screen_access_enabled=True, vision_model_profile_id=profile["id"])
            assert client.put("/api/settings", json=setting).status_code == 200
            assert client.post("/api/screen/describe", json={"image_data_url": IMAGE}).json() == {"description": "看到了图片。"}
            with database.connect() as db:
                stored = dict(db.execute("SELECT * FROM settings WHERE id=1").fetchone())
            assert asyncio.run(model_summary(stored, "diagram.pdf", "", "看图", [{"name": "page", "page_number": 1, "data_url": IMAGE}])) == "看到了图片。"
            assert seen == [("https://vision.example/v1/chat/completions", "Bearer vision-secret", "vision-model")] * 2
            assert client.delete(f"/api/model-profiles/{profile['id']}").status_code == 200
            assert client.get("/api/settings").json()["vision_model_profile_id"] is None


def test_proactive_screen_context_is_opt_in_and_ephemeral(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "screen.db")
        seen = []

        async def fake_describe(setting, data_url, *, proactive):
            assert proactive and data_url == IMAGE
            return "用户正在编辑代码"

        async def fake_generate(setting, config, prompt, history, now):
            seen.append(config.get("_screen_context"))
            return "今天这段代码写得顺利吗？", "daily", [], 10, "", None

        monkeypatch.setattr("app.main.describe_screen", fake_describe)
        monkeypatch.setattr("app.main.generate_proactive", fake_generate)
        with TestClient(app) as client:
            character = client.post("/api/characters", json={"name": "测试"}).json()
            setting = client.get("/api/settings").json()
            setting.update(screen_access_enabled=True, api_key="test-key", vision_model="vision-test")
            assert client.put("/api/settings", json=setting).status_code == 200
            config = client.get("/api/plugins/proactive").json()
            config.update(enabled=True, screen_context_enabled=True)
            assert client.put("/api/plugins/proactive", json=config).status_code == 200
            with database.connect() as db:
                db.execute("UPDATE proactive_plugin SET next_due=0 WHERE id=1")
            response = client.post("/api/plugins/proactive/generate", json={"character_id": character["id"], "screen_image": IMAGE})
            assert response.status_code == 200
            assert seen == ["用户正在编辑代码"]
            with database.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM messages WHERE content LIKE '%用户正在编辑代码%'").fetchone()[0] == 0

            async def failed_describe(setting, data_url, *, proactive):
                raise ValueError("vision unavailable")

            monkeypatch.setattr("app.main.describe_screen", failed_describe)
            with database.connect() as db:
                db.execute("UPDATE proactive_plugin SET next_due=0 WHERE id=1")
            response = client.post("/api/plugins/proactive/generate", json={"character_id": character["id"], "screen_image": IMAGE})
            assert response.status_code == 200
            assert seen[-1] is None
