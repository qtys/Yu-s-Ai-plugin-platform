import base64
import copy
import json
import struct
import zlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.roleplay import PNG_SIGNATURE, BUILTIN

BASE = "/api/plugins/roleplay"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "roleplay.db")
    with TestClient(app) as result:
        yield result


def card(name="测试角色"):
    return {"spec": "chara_card_v2", "spec_version": "2.0", "unknown_root": ["preserve"], "data": {
        "name": name, "description": "巡林员", "personality": "耐心", "scenario": "山间小屋",
        "first_mes": "{{user}}，我是{{char}}。", "mes_example": "<START>\n{{user}}: 森林在哪？\n{{char}}: 跟我来。",
        "creator": "公开测试作者", "extensions": {"unknown": {"script": "never execute"}},
        "system_prompt": "{{original}}\n角色规则：{{char}}只知道小屋。",
        "post_history_instructions": "{{original}}\n后置约定：称呼{{user}}。",
        "alternate_greetings": ["另一个开场：{{char}}向{{user}}挥手。"],
        "character_book": {"scan_depth": 3, "token_budget": 20, "extensions": {"vendor": True}, "entries": [
            {"keys": ["水杯"], "content": "水杯是蓝色的。", "enabled": True, "insertion_order": 1, "extensions": {"retained": 1}},
            {"keys": ["宝藏"], "content": "秘密只在宝藏触发。", "enabled": True, "insertion_order": 2},
        ]}}}


def imported(client, value=None):
    payload = {"filename": "test.json", "content": json.dumps(value or card(), ensure_ascii=False)}
    response = client.post(BASE + "/cards/import", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["character"]


def preview(client, role, text="水杯", conv=None):
    response = client.post(f"{BASE}/characters/{role['id']}/preview", json={"content": text, "conversation_id": conv})
    assert response.status_code == 200, response.text
    return response.json()


def test_card_roundtrip_and_native_export_preserve_fields(client):
    value = card()
    role = imported(client, value)
    assert role["background"] == "巡林员"
    source = client.get(f"{BASE}/characters/{role['id']}/export").json()
    assert source["unknown_root"] == value["unknown_root"]
    assert source["data"]["extensions"] == value["data"]["extensions"]
    assert source["data"]["character_book"] == value["data"]["character_book"]
    native = client.get(f"{BASE}/characters/{role['id']}/export?format=native").json()
    again = imported(client, native)
    assert client.get(f"{BASE}/characters/{again['id']}/export").json() == source
    edited = {**role, "personality": "活泼", "greeting": "新开场"}
    assert client.put(f"/api/characters/{role['id']}", json=edited).status_code == 200
    result = client.get(f"{BASE}/characters/{role['id']}/export").json()
    assert result["data"]["personality"] == "活泼" and result["data"]["first_mes"] == "新开场"
    assert result["data"]["extensions"] == source["data"]["extensions"]


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def png(value):
    return PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"tEXt", b"chara\0" + base64.b64encode(json.dumps(value).encode())) + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0")) + chunk(b"IEND", b"")


def test_png_import_metadata_removed_from_avatar(client):
    raw = png(card())
    payload = {"filename": "test.png", "png": True, "content": base64.b64encode(raw).decode()}
    inspect = client.post(BASE + "/cards/inspect", json=payload)
    assert inspect.status_code == 200
    assert inspect.json()["tavern"]
    result = client.post(BASE + "/cards/import", json=payload).json()["character"]
    image = base64.b64decode(result["avatar_data"].split(",")[1])
    assert b"chara\0" not in image and b"IDAT" in image
    broken = bytearray(raw); broken[30] ^= 1
    payload["content"] = base64.b64encode(broken).decode()
    assert client.post(BASE + "/cards/import", json=payload).status_code == 422
    payload["content"] = base64.b64encode(PNG_SIGNATURE + chunk(b"IEND", b"")).decode()
    assert client.post(BASE + "/cards/inspect", json=payload).status_code == 422


@pytest.mark.parametrize("value", [[], {"spec": "bad", "data": {}}, {"name": "x", "scenario": 4}, {"spec": "chara_card_v2", "data": {"name": "x", "alternate_greetings": 3}}, {"format": "yus-ai-character", "character": {"name": "x"}, "tavern_card": {"data": []}}])
def test_bad_import_does_not_create_character(client, value):
    response = client.post(BASE + "/cards/import", json={"content": json.dumps(value)})
    assert response.status_code == 422
    assert client.get("/api/characters").json() == []


def test_default_macros_examples_lore_and_scoped_isolation(client):
    role = imported(client)
    result = preview(client, role)
    assert result["enabled"] is False
    assert result["lore"] == [{"index": 1, "cost": 7}]
    assert "秘密只在宝藏" not in str(result["messages"])
    assert {"role": "user", "content": "森林在哪？"} in result["before"]
    assert {"role": "assistant", "content": "跟我来。"} in result["before"]
    assert "后置约定：称呼用户" in result["after"][-1]["content"]
    assert "{{original}}" not in str(result["messages"])
    assert client.patch(f"{BASE}/characters/{role['id']}", json={"config": {"user_name": "小明", "greeting_index": 0}}).status_code == 200
    conv = client.post("/api/conversations", json={"character_id": role["id"]}).json()
    assert client.get(f"/api/conversations/{conv['id']}/messages").json()[0]["content"] == "另一个开场：测试角色向小明挥手。"
    other = client.post("/api/characters", json={"name": "另一位", "personality": "独立"}).json()
    assert "小明" not in str(preview(client, other)["messages"])
    assert client.post(f"{BASE}/characters/{other['id']}/preview", json={"conversation_id": conv["id"]}).status_code == 409


def test_lore_budget_selective_disabled_and_depth(client):
    role = imported(client)
    entries = [
        {"keys": ["APPLE"], "content": "高优先级", "priority": 100, "insertion_order": 2},
        {"keys": ["apple"], "content": "低优先级", "priority": 1, "insertion_order": 1},
        {"keys": ["apple"], "content": "关闭条目", "enabled": False},
        {"keys": ["apple"], "secondary_keys": ["pear"], "content": "组合关键词", "selective": True},
        {"keys": ["apple"], "content": "正则跳过", "use_regex": True},
        {"keys": [], "content": "常驻", "constant": True, "position": "after_char"},
    ]
    route = f"{BASE}/characters/{role['id']}"
    assert client.patch(route, json={"config": {"book": {"entries": entries}, "lore_budget": 6}}).status_code == 200
    result = preview(client, role, "apple")
    assert [item["index"] for item in result["lore"]] == [1, 6]
    assert 2 in result["excluded_lore"]
    assert any("正则" in s for s in result["warnings"])
    assert client.patch(route, json={"config": {"lore_budget": 100}}).status_code == 200
    assert 4 not in [item["index"] for item in preview(client, role, "apple")["lore"]]
    assert 4 in [item["index"] for item in preview(client, role, "apple pear")["lore"]]
    client.patch(route, json={"config": {"lore_enabled": False}})
    assert preview(client, role, "apple pear")["lore"] == []


def test_worldinfo_json_adapter_and_unknown_fields(client):
    source = {"vendor": {"kept": 1}, "entries": {"3": {"uid": 3, "key": ["海"], "keysecondary": [], "content": "海是蓝色", "disable": False, "order": 4, "position": 1, "unknown": "preserve"}}}
    result = client.post(BASE + "/worldbooks/inspect", json={"content": json.dumps(source)}).json()
    assert result["entries"][0]["keys"] == ["海"]
    assert result["entries"][0]["unknown"] == "preserve" and result["vendor"] == source["vendor"]
    role = imported(client)
    assert client.patch(f"{BASE}/characters/{role['id']}", json={"config": {"book": source}}).status_code == 200
    assert len(preview(client, role, "海")["lore"]) == 1


def test_preset_crud_order_disabled_wrap_unknown(client):
    role = imported(client)
    data = copy.deepcopy(BUILTIN["data"])
    data["unknown"] = {"keep": True}
    data["prompts"] += [{"identifier": "extra", "role": "system", "content": "专属 {{char}}"}, {"identifier": "skipped", "content": "不能发送"}]
    order = data["prompt_order"][0]["order"]
    order.insert(0, {"identifier": "extra", "enabled": True})
    order.append({"identifier": "skipped", "enabled": False})
    data["personality_format"] = "性格模板：{{personality}}"
    data["wi_format"] = "世界：{0}"
    item = client.post(BASE + "/presets", json={"name": "测试预设", "data": data}).json()
    route = f"{BASE}/characters/{role['id']}"
    assert client.patch(route, json={"config": {"preset_id": item["id"]}}).status_code == 200
    result = preview(client, role)
    assert result["before"][0]["content"] == "专属 测试角色"
    assert "不能发送" not in str(result["messages"])
    assert "性格模板：耐心" in str(result["messages"])
    assert "世界：水杯是蓝色" in str(result["messages"])
    assert next(p for p in client.get(BASE + "/presets").json() if p["id"] == item["id"])["data"]["unknown"] == data["unknown"]
    assert client.put(BASE + "/presets/builtin:roleplay", json={"name": "x", "data": data}).status_code == 409
    assert client.delete(BASE + "/presets/builtin:roleplay").status_code == 409
    assert client.delete(BASE + "/presets/" + item["id"]).json()["reset_bindings"] == 1
    assert preview(client, role)["preset_id"] == BUILTIN["id"]
    data["prompt_order"][0]["order"] = [{"identifier": "main"}]
    assert client.post(BASE + "/presets", json={"name": "bad", "data": data}).status_code == 422


def test_real_request_matches_snapshot_and_plugins_coexist(client, monkeypatch):
    uploaded = []
    async def respond(request):
        uploaded.append(json.loads(request.content))
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"自然回复。"},"finish_reason":"stop"}]}\ndata: [DONE]\n')
    original = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    settings = client.get("/api/settings").json(); settings["api_key"] = "private-mock-key"
    client.put("/api/settings", json=settings)
    role = imported(client)
    client.put(f"/api/characters/{role['id']}", json={**role, "initial_prompt_enabled": True, "initial_prompt": "首轮唯一内容"})
    conv = client.post("/api/conversations", json={"character_id": role["id"]}).json()
    route = f"/api/conversations/{conv['id']}/chat"
    client.put("/api/plugins/roleplay/state", json={"enabled": True})
    client.put("/api/plugins/novel_reply/state", json={"enabled": True})
    assert client.post(route, json={"content": "水杯"}).status_code == 200
    request = uploaded[-1]
    snapshot = client.get(f"{BASE}/requests/{conv['id']}").json()
    assert snapshot["messages"] == request["messages"]
    assert "private-mock-key" not in str(snapshot)
    assert "首轮唯一内容" in str(request["messages"])
    assert "小说式回复插件" in str(request["messages"])
    assert request["messages"][-1]["role"] == "system"
    assert "后置约定" in request["messages"][-1]["content"]
    assert client.post(route, json={"content": "下一句"}).status_code == 200
    assert "首轮唯一内容" not in str(uploaded[-1]["messages"])
    user = next(m for m in client.get(f"/api/conversations/{conv['id']}/messages").json() if m["role"] == "user")
    client.put(f"/api/messages/{user['id']}", json={"content": "改后的历史"})
    assert client.get(f"{BASE}/requests/{conv['id']}").status_code == 404
    client.put("/api/plugins/roleplay/state", json={"enabled": False})
    client.post(route, json={"content": "恢复"})
    assert uploaded[-1]["messages"][-1] == {"role": "user", "content": "恢复"}
    assert "后置约定" not in str(uploaded[-1]["messages"])
    assert client.get(BASE + "/characters/" + str(role["id"])).json()["scenario"] == "山间小屋"


def test_migration_and_cascading_deletion(client):
    role = imported(client)
    database.init_db()
    assert preview(client, role)["preset_id"] == BUILTIN["id"]
    client.delete(f"/api/characters/{role['id']}")
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM roleplay_characters").fetchone()[0] == 0


def test_android_cannot_enable_desktop_plugin(client):
    assert client.put("/api/plugins/roleplay/state", json={"enabled": True, "platform": "android", "device_id": "android_device_01"}).status_code == 409


def test_incomplete_optional_metadata_cannot_break_settings(client):
    source = card()
    source["data"]["character_book"] = {}
    role = imported(client, source)
    assert client.get(f"{BASE}/characters/{role['id']}").json()["book"]["entries"] == []
    source["data"]["creator"] = {"bad": True}
    assert client.post(BASE + "/cards/inspect", json={"content": json.dumps(source)}).status_code == 422


def test_lore_scan_uses_current_conversation_and_depth(client):
    role = imported(client)
    conv = client.post("/api/conversations", json={"character_id": role["id"]}).json()
    other = client.post("/api/conversations", json={"character_id": role["id"]}).json()
    with database.connect() as db:
        db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,'user','宝藏')", (other["id"],))
        db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,'user','水杯')", (conv["id"],))
    client.patch(f"{BASE}/characters/{role['id']}", json={"config": {"scan_depth": 2, "lore_budget": 100}})
    assert [x["index"] for x in preview(client, role, "你好", conv["id"])["lore"]] == [1]
    client.patch(f"{BASE}/characters/{role['id']}", json={"config": {"scan_depth": 1}})
    assert preview(client, role, "你好", conv["id"])["lore"] == []
