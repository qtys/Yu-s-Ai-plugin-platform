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
            character = client.post("/api/characters", json={"name": "测试角色"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            assert client.get(f"/api/conversations/{conversation['id']}/messages").json() == []
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
