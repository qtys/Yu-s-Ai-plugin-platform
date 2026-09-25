import json
import sqlite3
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_existing_instruction_database_migrates(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "old.db")
        with sqlite3.connect(database.DB_PATH) as db:
            db.execute("CREATE TABLE saved_instructions(id INTEGER PRIMARY KEY,character_id INTEGER,conversation_id INTEGER,content TEXT,enabled INTEGER,created_at TEXT)")
            db.execute("INSERT INTO saved_instructions VALUES (1,1,NULL,'原有指令',1,'2026-01-01')")
        database.init_db()
        with database.connect() as db:
            item = db.execute("SELECT content,source_template_name FROM saved_instructions WHERE id=1").fetchone()
            assert item["content"] == "原有指令" and item["source_template_name"] == ""
            assert db.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0] == 0


def test_template_import_render_scope_and_one_time_isolation(monkeypatch):
    sent = []

    async def respond(request: httpx.Request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"收到"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "templates.db")
        with TestClient(app) as client:
            setting = client.get("/api/settings").json()
            setting["api_key"] = "test-key"
            assert client.put("/api/settings", json=setting).status_code == 200
            first = client.post("/api/characters", json={"name": "甲"}).json()["id"]
            second = client.post("/api/characters", json={"name": "乙"}).json()["id"]
            first_chat = client.post("/api/conversations", json={"character_id": first}).json()["id"]
            other_chat = client.post("/api/conversations", json={"character_id": first}).json()["id"]
            second_chat = client.post("/api/conversations", json={"character_id": second}).json()["id"]
            template = client.post("/api/prompt-templates", json={"name": "测试模板", "category": "写作", "content": "请用{{风格}}回答"}).json()
            assert template["id"] > 0
            assert len(client.get("/api/prompt-templates").json()) == 1
            assert client.post(f"/api/prompt-templates/{template['id']}/apply", json={"character_id": first, "variables": {}}).status_code == 422
            invalid_chat = client.post(f"/api/conversations/{first_chat}/chat", json={"content": "无效", "character_id": first,
                "one_time_template_id": template["id"], "one_time_template_variables": {}})
            assert invalid_chat.status_code == 422
            assert client.get(f"/api/conversations/{first_chat}/messages").json() == []
            assert client.post(f"/api/prompt-templates/{template['id']}/apply", json={"character_id": second, "conversation_id": first_chat, "variables": {"风格": "简短"}}).status_code == 409
            applied = client.post(f"/api/prompt-templates/{template['id']}/apply", json={"character_id": first, "conversation_id": first_chat, "variables": {"风格": "简短"}}).json()
            assert applied["source_template_name"] == "测试模板"
            assert applied["content"] == "请用简短回答"
            assert len(client.get(f"/api/instructions?character_id={first}&conversation_id={first_chat}").json()) == 1
            assert not client.get(f"/api/instructions?character_id={first}&conversation_id={other_chat}").json()
            assert client.post(f"/api/conversations/{first_chat}/chat", json={"content": "一", "character_id": first}).status_code == 200
            assert "请用简短回答" in str(sent[-1]["messages"])
            assert client.post(f"/api/conversations/{other_chat}/chat", json={"content": "二", "character_id": first}).status_code == 200
            assert "请用简短回答" not in str(sent[-1]["messages"])
            assert client.post(f"/api/conversations/{second_chat}/chat", json={"content": "三", "character_id": second,
                "one_time_template_id": template["id"], "one_time_template_variables": {"风格": "热情"}}).status_code == 200
            assert "请用热情回答" in str(sent[-1]["messages"])
            assert client.post(f"/api/conversations/{second_chat}/chat", json={"content": "四", "character_id": second}).status_code == 200
            assert "请用热情回答" not in str(sent[-1]["messages"])
            assert not client.get(f"/api/instructions?character_id={second}&conversation_id={second_chat}").json()
            assert client.put(f"/api/instructions/{applied['id']}", json={"content": applied["content"], "enabled": False}).status_code == 200
            assert client.post(f"/api/conversations/{first_chat}/chat", json={"content": "五", "character_id": first}).status_code == 200
            assert "请用简短回答" not in str(sent[-1]["messages"])
            character_rule = client.post(f"/api/prompt-templates/{template['id']}/apply", json={"character_id": first,
                "variables": {"风格": "严谨"}}).json()
            assert character_rule["conversation_id"] is None
            assert client.post(f"/api/conversations/{other_chat}/chat", json={"content": "六", "character_id": first}).status_code == 200
            assert "请用严谨回答" in str(sent[-1]["messages"])
            assert client.post(f"/api/conversations/{second_chat}/chat", json={"content": "七", "character_id": second}).status_code == 200
            assert "请用严谨回答" not in str(sent[-1]["messages"])
            assert client.delete(f"/api/prompt-templates/{template['id']}").status_code == 200
            assert len(client.get(f"/api/instructions?character_id={first}&conversation_id={first_chat}").json()) == 2


def test_template_validation_and_update(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "templates.db")
        with TestClient(app) as client:
            assert client.post("/api/prompt-templates", json={"name": "空", "content": " "}).status_code == 422
            template = client.post("/api/prompt-templates", json={"name": "初版", "content": "{{主题}}"}).json()
            updated = client.put(f"/api/prompt-templates/{template['id']}", json={"name": "新版", "category": "研究", "content": "分析{{主题}}"}).json()
            assert updated["name"] == "新版" and updated["category"] == "研究"
            assert client.delete(f"/api/prompt-templates/{template['id']}").status_code == 200
            assert client.delete(f"/api/prompt-templates/{template['id']}").status_code == 404
            too_many = " ".join(f"{{{{变量{i}}}}}" for i in range(13))
            assert client.post("/api/prompt-templates", json={"name": "过多变量", "content": too_many}).status_code == 422
