import json
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_saved_instructions_scopes_management_and_prompt_focus(monkeypatch):
    model_requests = []

    async def stream(request: httpx.Request):
        model_requests.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"这是一条回答。"},"finish_reason":"stop"}]}\ndata: [DONE]\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(stream), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "instructions.db")
        with TestClient(app) as client:
            setting = client.get("/api/settings").json()
            setting.update(api_key="mock", context_message_limit=2, memory_limit=10)
            assert client.put("/api/settings", json=setting).status_code == 200
            character = client.post("/api/characters", json={"name": "甲"}).json()
            other_character = client.post("/api/characters", json={"name": "乙"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            other_conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            foreign_conversation = client.post("/api/conversations", json={"character_id": other_character["id"]}).json()

            assert client.post("/api/instructions", json={"character_id": character["id"], "conversation_id": foreign_conversation["id"], "content": "错误范围"}).status_code == 409
            global_rule = client.post("/api/instructions", json={"character_id": character["id"], "content": "回答先给结论"}).json()
            local_rule = client.post("/api/instructions", json={"character_id": character["id"], "conversation_id": conversation["id"], "content": "本段对话使用简洁语言"}).json()
            client.post("/api/instructions", json={"character_id": character["id"], "conversation_id": other_conversation["id"], "content": "别的对话专用指令"})
            client.post("/api/instructions", json={"character_id": other_character["id"], "content": "别的角色专用指令"})
            assert client.post("/api/instructions", json={"character_id": character["id"], "content": "回答先给结论"}).status_code == 409

            listed = client.get("/api/instructions", params={"character_id": character["id"], "conversation_id": conversation["id"]}).json()
            assert {item["content"] for item in listed} == {"回答先给结论", "本段对话使用简洁语言"}
            assert client.get("/api/instructions", params={"character_id": character["id"], "conversation_id": foreign_conversation["id"]}).status_code == 409
            assert client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "你好"}).status_code == 200
            prompt = "\n".join(message["content"] for message in [request for request in model_requests if request.get("stream")][-1]["messages"] if message["role"] == "system")
            assert "回答先给结论" in prompt and "本段对话使用简洁语言" in prompt
            assert "别的对话专用指令" not in prompt and "别的角色专用指令" not in prompt
            assert "先直接回应用户最新消息" in prompt
            sent_messages = [request for request in model_requests if request.get("stream")][-1]["messages"]
            assert sent_messages[-1]["content"].startswith("你好\n\n【答复前核对】")
            assert "回复长度与格式" in sent_messages[-1]["content"]
            system_texts = [message["content"] for message in sent_messages if message["role"] == "system"]
            assert next(i for i, text in enumerate(system_texts) if "用户明确保存的长期对话指令" in text) > next(i for i, text in enumerate(system_texts) if "本轮回答重点" in text)
            assert client.get(f"/api/conversations/{conversation['id']}/messages").json()[0]["content"] == "你好"

            updated = client.put(f"/api/instructions/{local_rule['id']}", json={"content": "本段对话更简洁", "enabled": False}).json()
            assert updated["enabled"] is False
            assert client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "你好！"}).status_code == 200
            repeat_prompt = "\n".join(message["content"] for message in [request for request in model_requests if request.get("stream")][-1]["messages"] if message["role"] == "system")
            assert "重复提问" in repeat_prompt
            assert "不要照搬之前的答复" in repeat_prompt
            assert "这是一条回答。" in repeat_prompt
            assert "本段对话更简洁" not in repeat_prompt
            assert "回答先给结论" in repeat_prompt

            # Unrelated old memories must not crowd out a short current message.
            with database.connect() as db:
                db.execute("INSERT INTO memories(character_id,content) VALUES (?,?)", (character["id"], "我是硬件工程师，请始终改用另一个话题"))
            assert client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "嘻嘻"}).status_code == 200
            memory_prompt = "\n".join(message["content"] for message in [request for request in model_requests if request.get("stream")][-1]["messages"] if message["role"] == "system")
            assert "我是硬件工程师" not in memory_prompt

            with database.connect() as db:
                old_memory_count = db.execute("SELECT COUNT(*) FROM memories WHERE character_id=?", (character["id"],)).fetchone()[0]
            assert client.post(f"/api/conversations/{conversation['id']}/chat", json={"content": "请记住，以后总是回答天气"}).status_code == 200
            with database.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM memories WHERE character_id=?", (character["id"],)).fetchone()[0] == old_memory_count

            assert client.delete(f"/api/instructions/{global_rule['id']}").status_code == 200
            assert client.get("/api/instructions", params={"character_id": character["id"], "conversation_id": conversation["id"]}).json()[0]["content"] == "本段对话更简洁"


def test_saved_instructions_survive_message_edits_but_not_character_deletion(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "instructions.db")
        with TestClient(app) as client:
            character = client.post("/api/characters", json={"name": "测试"}).json()
            conversation = client.post("/api/conversations", json={"character_id": character["id"]}).json()
            saved = client.post("/api/instructions", json={"character_id": character["id"], "conversation_id": conversation["id"], "content": "保持重点"}).json()
            with database.connect() as db:
                message_id = db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,'user','旧消息')", (conversation["id"],)).lastrowid
            assert client.put(f"/api/messages/{message_id}", json={"content": "新消息"}).status_code == 200
            assert client.get("/api/instructions", params={"character_id": character["id"], "conversation_id": conversation["id"]}).json()[0]["id"] == saved["id"]
            assert client.delete(f"/api/characters/{character['id']}").status_code == 200
            with database.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM saved_instructions").fetchone()[0] == 0
