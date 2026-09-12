import os
import tempfile

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
