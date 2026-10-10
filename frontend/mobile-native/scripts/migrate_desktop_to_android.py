"""One-time, idempotent USB migration into the debuggable Android app.

Requires adb, a connected authorized device, and websocket-client. The app must
be running. No character text, chat content, or model secret is printed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket


PACKAGE = "com.qtys.yusai.mobile"
STORES = ("characters", "conversations", "messages", "settings", "modelProfiles", "instructions", "promptTemplates", "initialTemplates")


def timestamp(value: str | None, fallback: int = 0) -> int:
    if not value:
        return fallback
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


def desktop_bundle(path: Path) -> tuple[dict, dict[str, str]]:
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
    finally:
        source.close()
    snapshot.row_factory = sqlite3.Row
    fields = (
        ("角色名称", "name"), ("角色简介", "description"), ("身份背景", "background"),
        ("性格", "personality"), ("说话方式", "speaking_style"),
        ("与用户的关系", "relationship"), ("行为边界", "boundaries"),
        ("示例对话", "example_dialogue"), ("补充指令", "system_prompt"),
    )
    characters = []
    for row in snapshot.execute("SELECT * FROM characters ORDER BY id"):
        prompt = "\n\n".join(f"【{label}】\n{row[field].strip()}" for label, field in fields if row[field] and row[field].strip())
        characters.append({
            "id": f"desktop-character-{row['id']}", "name": row["name"], "prompt": prompt,
            "createdAt": timestamp(row["created_at"], row["id"]),
            "initialPromptEnabled": bool(row["initial_prompt_enabled"]) if "initial_prompt_enabled" in row.keys() else False,
            "initialPrompt": row["initial_prompt"] if "initial_prompt" in row.keys() else "",
            "desktopCard": dict(row),
        })
    conversations = [
        {"id": f"desktop-conversation-{row['id']}", "characterId": f"desktop-character-{row['character_id']}",
         "title": row["title"], "updatedAt": timestamp(row["updated_at"], row["id"]),
         "initialPromptApplied": bool(row["initial_prompt_applied"]) if "initial_prompt_applied" in row.keys() else True}
        for row in snapshot.execute("SELECT * FROM conversations ORDER BY id")
    ]
    messages = [
        {"id": f"desktop-message-{row['id']}", "conversationId": f"desktop-conversation-{row['conversation_id']}",
         "role": row["role"], "content": row["content"],
         "createdAt": timestamp(row["created_at"], row["id"] * 1000) + row["id"] % 1000}
        for row in snapshot.execute("SELECT * FROM messages WHERE origin!='proactive' ORDER BY id")
    ]
    profiles = []
    keys = {}
    for row in snapshot.execute("SELECT * FROM model_profiles ORDER BY id"):
        profile_id = f"desktop-model-{row['id']}"
        profiles.append({
            "id": profile_id, "name": row["name"], "baseUrl": row["base_url"],
            "model": row["model"], "visionModel": row["vision_model"],
            "createdAt": timestamp(row["created_at"], row["id"]),
        })
        if row["api_key"]:
            keys[profile_id] = row["api_key"]
    settings = snapshot.execute("SELECT * FROM settings WHERE id=1").fetchone()
    active_id = f"desktop-model-{settings['active_model_profile_id']}"
    active = next((profile for profile in profiles if profile["id"] == active_id), profiles[0] if profiles else None)
    mobile_settings = {
        "id": "model", "activeProfileId": active["id"] if active else "",
        "baseUrl": active["baseUrl"] if active else settings["base_url"],
        "model": active["model"] if active else settings["model"],
        "temperature": settings["temperature"], "maxTokens": settings["max_tokens"],
    }
    instructions = [
        {"id": f"desktop-instruction-{row['id']}", "characterId": f"desktop-character-{row['character_id']}",
         "conversationId": f"desktop-conversation-{row['conversation_id']}" if row["conversation_id"] else None,
         "content": row["content"], "enabled": bool(row["enabled"]),
         "createdAt": timestamp(row["created_at"], row["id"]), "sourceTemplateName": row["source_template_name"]}
        for row in snapshot.execute("SELECT * FROM saved_instructions ORDER BY id")
    ]
    prompt_templates = [
        {"id": f"desktop-template-{row['id']}", "name": row["name"], "category": row["category"],
         "content": row["content"], "createdAt": timestamp(row["created_at"], row["id"])}
        for row in snapshot.execute("SELECT * FROM prompt_templates ORDER BY id")
    ]
    initial_templates = []
    if snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='role_initialization_templates'").fetchone():
        initial_templates = [
            {"id": f"custom-desktop-{row['id']}", "name": row["name"], "content": row["content"], "source": "custom"}
            for row in snapshot.execute("SELECT * FROM role_initialization_templates ORDER BY id")
        ]
    snapshot.close()
    return {"characters": characters, "conversations": conversations, "messages": messages,
            "modelProfiles": profiles, "settings": mobile_settings,
            "instructions": instructions, "promptTemplates": prompt_templates, "initialTemplates": initial_templates}, keys


class Devtools:
    def __init__(self, adb: Path, serial: str):
        self.adb = str(adb)
        self.serial = serial
        self.port: int | None = None
        self.socket = None
        self.next_id = 0

    def command(self, *args: str) -> str:
        return subprocess.check_output([self.adb, "-s", self.serial, *args], text=True, stderr=subprocess.DEVNULL).strip()

    def __enter__(self):
        if self.command("get-state") != "device":
            raise RuntimeError("ADB 设备未授权或未连接")
        pid = self.command("shell", "pidof", PACKAGE)
        if not pid.isdigit():
            raise RuntimeError("请先在手机上打开 Yu's AI Mobile")
        self.port = int(self.command("forward", "tcp:0", f"localabstract:webview_devtools_remote_{pid}"))
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=5) as response:
            pages = json.load(response)
        page = next((item for item in pages if item.get("url") in ("http://tauri.localhost/", "https://tauri.localhost/")), None)
        if not page:
            raise RuntimeError("未找到 Yu's AI Mobile 调试页面")
        self.socket = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
        return self

    def __exit__(self, *_):
        if self.socket:
            self.socket.close()
        if self.port is not None:
            self.command("forward", "--remove", f"tcp:{self.port}")

    def evaluate(self, expression: str):
        self.next_id += 1
        self.socket.send(json.dumps({"id": self.next_id, "method": "Runtime.evaluate",
                                     "params": {"expression": expression, "returnByValue": True, "awaitPromise": True}}))
        while True:
            result = json.loads(self.socket.recv())
            if result.get("id") != self.next_id:
                continue
            if result.get("error") or result.get("result", {}).get("exceptionDetails"):
                raise RuntimeError("手机端执行导入失败；已停止，未输出敏感数据")
            return result["result"]["result"].get("value")


def backup_mobile(devtools: Devtools, target: Path) -> dict:
    expression = """(async()=>{
      const request=indexedDB.open('yus-ai-mobile');
      const db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)});
      const result={};
      for(const name of ['characters','conversations','messages','settings','modelProfiles','instructions','promptTemplates','initialTemplates']){
        if(!db.objectStoreNames.contains(name)){result[name]=[];continue;}
        const tx=db.transaction(name,'readonly');const item=tx.objectStore(name).getAll();
        result[name]=await new Promise((resolve,reject)=>{item.onsuccess=()=>resolve(item.result);item.onerror=()=>reject(item.error)});
      }
      db.close();
      result._localStorage=Object.fromEntries(Object.entries(localStorage));
      result._modelKeys={};
      const ids=new Set(result.modelProfiles.map(item=>item.id));
      for(const item of result.settings){if(item.activeProfileId)ids.add(item.activeProfileId)}
      for(const id of ids){result._modelKeys[id]=await window.__TAURI_INTERNALS__.invoke('load_model_key',{profileId:id})}
      return result;
    })()"""
    data = devtools.evaluate(expression)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    return data


def import_mobile(devtools: Devtools, bundle: dict, keys: dict[str, str], instructions_only: bool):
    # The desktop payload stays inside this process and the loopback DevTools session.
    payload = {name: bundle[name] for name in ("instructions", "promptTemplates")} if instructions_only else bundle
    literal = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    names = ["instructions", "promptTemplates"] if instructions_only else ["characters", "conversations", "messages", "modelProfiles", "instructions", "promptTemplates", "initialTemplates"]
    names_literal = json.dumps(names)
    expression = f"""(async()=>{{
      const bundle={literal};const names={names_literal};const request=indexedDB.open('yus-ai-mobile');
      const db=await new Promise((resolve,reject)=>{{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)}});
      const existing={{}};
      for(const name of names){{const tx=db.transaction(name,'readonly');const request=tx.objectStore(name).getAllKeys();
        existing[name]=new Set(await new Promise((resolve,reject)=>{{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)}}));}}
      const tx=db.transaction({"['instructions','promptTemplates']" if instructions_only else "['characters','conversations','messages','settings','modelProfiles','instructions','promptTemplates','initialTemplates']"},'readwrite');
      for(const name of names){{for(const item of bundle[name]){{if(!existing[name].has(item.id))tx.objectStore(name).add(item)}}}}
      if(bundle.settings)tx.objectStore('settings').put(bundle.settings);
      await new Promise((resolve,reject)=>{{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error)}});
      db.close();return Object.fromEntries(names.map(name=>[name,bundle[name].length]));
    }})()"""
    counts = devtools.evaluate(expression)
    for profile_id, key in ([] if instructions_only else keys.items()):
        args = json.dumps({"profileId": profile_id, "apiKey": key}, ensure_ascii=True)
        expression = f"window.__TAURI_INTERNALS__.invoke('save_model_key',{args})"
        devtools.evaluate(expression)
    devtools.evaluate("location.reload()")
    return counts


def inspect_mobile(devtools: Devtools) -> dict:
    expression = """(async()=>{
      const request=indexedDB.open('yus-ai-mobile');
      const db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)});
      const result={};
      for(const name of ['characters','conversations','messages','modelProfiles','instructions','promptTemplates','initialTemplates']){
        const tx=db.transaction(name,'readonly');const item=tx.objectStore(name).count();
        result[name]=await new Promise((resolve,reject)=>{item.onsuccess=()=>resolve(item.result);item.onerror=()=>reject(item.error)});
      }
      const tx=db.transaction('settings','readonly');const item=tx.objectStore('settings').get('model');
      const settings=await new Promise((resolve,reject)=>{item.onsuccess=()=>resolve(item.result);item.onerror=()=>reject(item.error)});
      db.close();result.activeProfileId=settings?.activeProfileId??'';
      const key=await window.__TAURI_INTERNALS__.invoke('load_model_key',{profileId:result.activeProfileId});
      result.activeKeyConfigured=Boolean(key);return result;
    })()"""
    return devtools.evaluate(expression)


def verify_bundle(devtools: Devtools, bundle: dict, keys: dict[str, str]) -> dict:
    """Compare imported values without returning any private content to logs."""
    payload = {name: items for name, items in bundle.items() if isinstance(items, list)}
    literal = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    result = devtools.evaluate(f"""(async()=>{{
      const bundle={literal};const request=indexedDB.open('yus-ai-mobile');
      const db=await new Promise((resolve,reject)=>{{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)}});
      const result={{}};let localRoleOverrides=0;
      for(const [name,items] of Object.entries(bundle)){{
        const tx=db.transaction(name,'readonly');
        const checks=items.map(item=>new Promise((resolve,reject)=>{{
          const request=tx.objectStore(name).get(item.id);
          request.onsuccess=()=>{{
            const actual=request.result;
            if(!actual){{resolve(false);return;}}
            const differing=Object.keys(item).filter(key=>JSON.stringify(actual[key])!==JSON.stringify(item[key]));
            const localRoleEdit=name==='characters'&&differing.length>0&&differing.every(key=>['initialPromptEnabled','initialPrompt'].includes(key));
            if(localRoleEdit)localRoleOverrides++;
            resolve(differing.length===0||localRoleEdit);
          }};
          request.onerror=()=>reject(request.error);
        }}));
        result[name]=(await Promise.all(checks)).filter(Boolean).length;
      }}
      db.close();return {{...result,localRoleOverrides}};
    }})()""")
    if any(result.get(name) != len(items) for name, items in payload.items()):
        raise RuntimeError(f"导入内容校验不一致：匹配数={result}；期望数={dict((name, len(items)) for name, items in payload.items())}；备份保留，未输出私人内容")
    verified_keys = 0
    for profile_id, key in keys.items():
        args = json.dumps({"profileId": profile_id}, ensure_ascii=True)
        expected = json.dumps(key, ensure_ascii=True)
        matched = devtools.evaluate(f"(async()=>await window.__TAURI_INTERNALS__.invoke('load_model_key',{args})==={expected})()")
        if not matched:
            raise RuntimeError("模型密钥校验失败；未输出密钥")
        verified_keys += 1
    return {**result, "verifiedModelKeys": verified_keys}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="电脑端安装目录里的 yus_ai.db")
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--instructions-only", action="store_true", help="只合并导入指令和模板，保留手机聊天与模型配置")
    args = parser.parse_args()
    if not args.adb.is_file() or (not args.verify_only and not args.db.is_file()):
        parser.error("数据库或 adb 路径不存在")
    if args.verify_only:
        with Devtools(args.adb, args.serial) as devtools:
            print("手机端迁移校验：", inspect_mobile(devtools))
            bundle, keys = desktop_bundle(args.db)
            print("逐条内容与密钥校验：", verify_bundle(devtools, bundle, keys))
        return
    bundle, keys = desktop_bundle(args.db)
    backup_path = args.backup_dir / f"mobile-before-import-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    with Devtools(args.adb, args.serial) as devtools:
        existing = backup_mobile(devtools, backup_path)
        counts = import_mobile(devtools, bundle, keys, args.instructions_only)
    print("手机端迁移前备份：", backup_path)
    print("手机端原有记录数：", {name: len(existing[name]) for name in STORES})
    print("本次合并导入：", counts, "本次处理的模型密钥数：", 0 if args.instructions_only else len(keys))


if __name__ == "__main__":
    main()
