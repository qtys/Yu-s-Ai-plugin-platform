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
from app.roleplay import PNG_SIGNATURE, BUILTIN, mark_lore_examples

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
    assert [{"index": e["index"], "cost": e["cost"]} for e in result["lore"]] == [{"index": 1, "cost": 7}]
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
    assert 5 in result["excluded_lore"]  # Plain regex-flagged keys now match safely.
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


def test_constant_regex_flag_and_depth_positions(client):
    role = imported(client)
    book = {"entries": [
        {"keys": [], "content": "人物甲设定 {{<user>}}", "constant": True, "use_regex": True, "extensions": {"position": 0}},
        {"keys": [], "content": "深度零资料", "constant": True, "use_regex": True, "extensions": {"position": 4, "depth": 0, "role": 0}},
        {"keys": [], "content": "深度一资料", "constant": True, "extensions": {"position": 4, "depth": 1, "role": 1}},
        {"keys": [], "content": "深度超长资料", "constant": True, "extensions": {"position": 4, "depth": 100, "role": 2}},
        {"keys": [".*"], "content": "不执行正则", "use_regex": True},
    ]}
    client.patch(f"{BASE}/characters/{role['id']}", json={"config": {"book": book, "lore_budget": 1000}})
    result = preview(client, role, "本轮真实输入")
    assert len(result["lore"]) == 4
    msgs = result["messages"]
    latest = next(i for i, m in enumerate(msgs) if m["content"] == "本轮真实输入")
    assert msgs[latest - 1] == {"role": "user", "content": "深度一资料"}
    assert msgs[latest + 1] == {"role": "system", "content": "深度零资料"}
    assert {"role": "assistant", "content": "深度超长资料"} in msgs
    assert "人物甲设定" in str(msgs) and "不执行正则" not in str(msgs)
    client.patch(f"{BASE}/characters/{role['id']}", json={"config": {"lore_budget": 1}})
    assert any("预算不足" in w for w in preview(client, role)["warnings"])


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
    client.patch(f"{BASE}/characters/{role['id']}", json={"config": {"book": {"entries": [{"constant": True, "use_regex": True, "content": "实际请求深度资料", "extensions": {"position": 4, "depth": 0, "role": 0}}]}}})
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
    assert request["messages"][-2] == {"role": "system", "content": "实际请求深度资料"}
    assert any(m["role"] == "system" and "角色交互的事实边界" in m["content"] for m in request["messages"])
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
    assert "角色交互的事实边界" not in str(uploaded[-1]["messages"])
    assert client.get(BASE + "/characters/" + str(role["id"])).json()["scenario"] == "山间小屋"


def test_builtin_rules_do_not_turn_short_user_input_into_system_topic():
    main = next(p["content"] for p in BUILTIN["data"]["prompts"] if p["identifier"] == "main")
    tail = next(p["content"] for p in BUILTIN["data"]["prompts"] if p["identifier"] == "jailbreak")
    assert "不强加小说旁白" in main
    assert "历史中重复或错误的回答不是必须延续的设定" in main
    assert "非用户发言" in tail
    assert "此前用户正在讨论的事项" in tail
    assert "不得引用为" in tail
    assert "保持人物动机与场景连贯" not in tail


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


def test_core_lore_wins_over_format_sample_and_warns_on_budget(client):
    role = imported(client)
    book = {'entries': [
        {'constant': True, 'comment': '状态栏格式模板', 'content': '样例' * 80, 'insertion_order': 0},
        {'constant': True, 'comment': '世界观', 'content': '真实世界是山间森林。', 'insertion_order': 1},
        {'constant': True, 'comment': '主要剧情', 'content': '目标是找到迷路旅人。', 'insertion_order': 2},
        {'constant': True, 'comment': '人物', 'content': '姓名：林青，巡林员。', 'insertion_order': 3},
    ]}
    client.patch(f'{BASE}/characters/{role["id"]}', json={'config': {'book': book, 'lore_budget': 50}})
    plan = preview(client, role)
    assert {item['index'] for item in plan['lore']} == {2, 3, 4}
    assert plan['excluded_lore'] == [1]
    assert any('状态栏格式模板' in warning and '本轮未发送' in warning for warning in plan['warnings'])
    assert any('全部已触发设定约需' in warning for warning in plan['warnings'])
    # Explicit priorities remain an author/user override, not silently rewritten.
    book['entries'][0]['priority'] = 100
    book['entries'][0]['content'] = '紧急格式'
    client.patch(f'{BASE}/characters/{role["id"]}', json={'config': {'book': book, 'lore_budget': 4}})
    assert [item['index'] for item in preview(client, role)['lore']] == [1]


def test_literal_regex_flag_and_alternatives_are_supported_without_regex_engine(client):
    role = imported(client)
    book = {'entries': [
        {'keys': ['林青'], 'use_regex': True, 'content': '巡林员资料'},
        {'keys': ['森林|山间'], 'use_regex': True, 'content': '山间路径'},
        {'keys': ['(a+)+$'], 'use_regex': True, 'content': '不执行复杂正则'},
    ]}
    client.patch(f'{BASE}/characters/{role["id"]}', json={'config': {'book': book, 'lore_budget': 100}})
    plan = preview(client, role, '林青走进森林')
    assert {item['index'] for item in plan['lore']} == {1, 2}
    assert any('复杂关键词正则' in warning for warning in plan['warnings'])
    assert preview(client, role, '无关消息')['lore'] == []


def test_format_examples_are_marked_not_current_scene():
    text = '**[状态栏]模版开始 **\n日期：示例日期；地点：示例小屋\n**[状态栏]模版结束 **'
    marked = mark_lore_examples(text)
    assert '不是真实剧情或历史' in marked and '样例中的人物' in marked
    assert text in marked  # Preserve author's original example.
    assert mark_lore_examples('真实设定：山间森林') == '真实设定：山间森林'


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
