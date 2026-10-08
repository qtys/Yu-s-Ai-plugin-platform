import json
import tempfile

import httpx
from fastapi.testclient import TestClient
from app import database
from app.main import app


def test_pet_style_is_request_scoped_and_history_keeps_full_reply(monkeypatch):
    requests = []
    reply = "辛苦啦。先喝口水。我陪你。"

    async def respond(request):
        requests.append(json.loads(request.content))
        event = {"choices": [{"delta": {"content": reply}, "finish_reason": "stop"}]}
        return httpx.Response(200, content="data: " + json.dumps(event) + "\ndata: [DONE]\n")

    original = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.Path(directory) / "pet.db")
        with TestClient(app) as client:
            settings = client.get("/api/settings").json()
            settings["api_key"] = "test-only"
            client.put("/api/settings", json=settings)
            character = client.post("/api/characters", json={"name": "雨"}).json()["id"]
            conversation = client.post("/api/conversations", json={"character_id": character}).json()["id"]
            url = f"/api/conversations/{conversation}/chat"
            response = client.post(url, json={"content": "我加班了", "pet_chat_mode": True})
            assert response.status_code == 200
            assert len(requests) == 1
            assert any("【桌宠轻对话模式】" in m["content"] for m in requests[-1]["messages"])
            messages = client.get(f"/api/conversations/{conversation}/messages").json()
            assert messages[-1]["content"] == reply
            assert messages[-2]["content"] == "我加班了"
            client.post(url, json={"content": "详细说明"})
            assert len(requests) == 2
            assert not any("【桌宠轻对话模式】" in m["content"] for m in requests[-1]["messages"])
