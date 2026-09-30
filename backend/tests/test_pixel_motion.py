import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import database, pixel_motion


def scene_data():
    points = [{"x": x, "y": y} for x, y in [(0,-24),(12,-18),(24,0),(18,18),(0,22),(-18,18),(-24,0),(-12,-18)]]
    return {
        "version": 1, "title": "测试形变", "intent": "接触后身体局部变化", "duration_ms": 2000,
        "body": {"id": "slime", "label": "身体", "kind": "polygon", "color": 2,
                 "frames": [{"at": 0, "x": 48, "y": 60, "points": points}, {"at": 1, "x": 48, "y": 60, "points": points}]},
        "face": [{"at": 0}, {"at": 1}], "props": [],
    }


def soft_data():
    return {"title": "柔软扇叶", "intent": "从侧面长出扇叶再缩回", "duration_ms": 6000,
            "face": [{"at": 0}, {"at": 1}],
            "fan": [{"at": at, "extension": extension, "spread": .8, "bend": 2, "sway": -8}
                    for at, extension in [(0,0), (.4,1), (1,0)]]}


def test_soft_mode_protects_body_and_retracts_appendage():
    scene = pixel_motion.decode_soft(json.dumps(soft_data()))
    assert scene.style == "soft"
    assert scene.props == []
    assert scene.body.frames[0].points == scene.body.frames[1].points
    for change in (
        lambda s: s.update(body=scene_data()["body"]),
        lambda s: s["fan"][-1].update(extension=1),
        lambda s: s["fan"][1].update(sway=90),
    ):
        data = soft_data(); change(data)
        with pytest.raises(ValidationError):
            pixel_motion.decode_soft(json.dumps(data))


def test_rejects_broken_topology_nonfinite_and_executable_fields():
    for mutate in (
        lambda s: s["body"]["frames"][1]["points"].pop(),
        lambda s: s["body"]["frames"][1].update(at=0),
        lambda s: s["body"]["frames"][0].update(x=float("inf")),
        lambda s: s.update(script="fetch('https://example.com')"),
        lambda s: s["props"].append(s["body"].copy()),
    ):
        data = scene_data()
        mutate(data)
        with pytest.raises(ValidationError):
            pixel_motion.PixelScene.model_validate(data)


@pytest.mark.parametrize("mode", ["free", "soft"])
def test_generation_uses_selected_role_and_never_writes_chat_or_returns_keys(tmp_path, monkeypatch, mode):
    monkeypatch.delenv("YUS_AI_PIXEL_CONFIG_DB", raising=False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    with database.connect() as db:
        db.execute("UPDATE settings SET api_key='private-test-key',base_url='https://model.test/v1',model='test-model'")
        cid = db.execute("INSERT INTO characters(name,personality) VALUES ('实验角色','温柔好奇')").lastrowid
    captured = {}

    def respond(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(soft_data() if mode == "soft" else scene_data())}, "finish_reason": "stop"}], "usage": {"total_tokens": 12}})

    original = httpx.AsyncClient
    monkeypatch.setattr(pixel_motion.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    app = FastAPI()
    app.include_router(pixel_motion.router)
    with TestClient(app) as client:
        config = client.get("/api/experiments/pixel-motion/config").json()
        assert "private-test-key" not in json.dumps(config)
        r = client.post("/api/experiments/pixel-motion", json={"content": "灯亮了", "character_id": cid, "mode": mode})
        assert r.status_code == 200
        assert r.json()["source"] == "model"
        if mode == "soft":
            assert r.json()["scene"]["style"] == "soft"
            assert r.json()["scene"]["fan"][-1]["extension"] == 0
        assert "private-test-key" not in r.text
        assert json.loads(captured["messages"][1]["content"])["character"]["personality"] == "温柔好奇"
        assert client.post("/api/experiments/pixel-motion", json={"content": "test", "character_id": cid + 999}).status_code == 404
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


@pytest.mark.parametrize("finish_reason,repair_ok", [("stop", False), ("length", False), ("stop", True)])
def test_rejects_or_repairs_invalid_provider_data(tmp_path, monkeypatch, finish_reason, repair_ok):
    monkeypatch.delenv("YUS_AI_PIXEL_CONFIG_DB", raising=False)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    with database.connect() as db:
        db.execute("UPDATE settings SET api_key='test',base_url='https://api.deepseek.com/v1'")
    original = httpx.AsyncClient
    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        content = json.dumps(scene_data()) if repair_ok and len(calls) == 2 else "not JSON"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}], "usage": {"total_tokens": 10}})
    monkeypatch.setattr(pixel_motion.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    app = FastAPI()
    app.include_router(pixel_motion.router)
    with TestClient(app) as client:
        response = client.post("/api/experiments/pixel-motion", json={"content": "test"})
        assert response.status_code == (200 if repair_ok else 502)
        if repair_ok:
            assert response.json()["tokens"] == 20
        assert len(calls) == (1 if finish_reason == "length" else 2)
