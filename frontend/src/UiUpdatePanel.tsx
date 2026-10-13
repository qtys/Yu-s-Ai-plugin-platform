import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { confirm } from "@tauri-apps/plugin-dialog";

type Info = { revision: number; host_version: string; available: boolean; bytes: number };
type Local = { pending: boolean; staged_revision: number | null; loaded_revision: number | null; host_version: string; development: boolean };
export default function UiUpdatePanel({ disabled }: { disabled: boolean }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [working, setWorking] = useState(false);
  const [status, setStatus] = useState("");
  const [local, setLocal] = useState<Local | null>(null);
  const live = useRef(true);
  const disabledRef = useRef(disabled);
  useEffect(() => { disabledRef.current = disabled; }, [disabled]);
  useEffect(() => {
    live.current = true;
    void invoke<Local>("ui_update_status").then(value => { if (live.current) setLocal(value); }).catch(() => undefined);
    return () => { live.current = false; };
  }, []);
  async function check() {
    setWorking(true);
    try { const value = await invoke<Info>("check_ui_update"); setInfo(value); setStatus(value.available ? `界面更新 #${value.revision} · ${(value.bytes / 1024 / 1024).toFixed(1)} MB` : "当前没有新版界面"); }
    catch (error) { setInfo(null); setStatus(String(error)); }
    finally { setWorking(false); }
  }
  async function apply() {
    if (!await confirm("下载签名界面包，校验成功后重启应用。请先保存未发送的输入；已保存的聊天数据不会被覆盖。是否继续？", { title: "界面更新", kind: "info" })) return;
    setWorking(true); setStatus("下载并验证更新包…");
    try {
      await invoke("stage_ui_update");
      if (!live.current) return; // Leaving settings must not restart a newly started chat.
      setLocal(await invoke<Local>("ui_update_status"));
      setStatus("更新已验证，等待重启");
      if (!disabledRef.current) await restart();
    }
    catch (error) { setStatus(`${String(error)}；旧界面保持可用，可稍后重启或回退`); }
    finally { setWorking(false); }
  }
  async function restart() {
    if (!live.current || disabledRef.current) return;
    if (!await confirm("界面更新已就绪。请先保存未发送内容，是否现在重启？", { title: "应用界面更新", kind: "info" })) return;
    if (!live.current || disabledRef.current) return;
    await invoke("restart_application");
  }
  async function rollback() {
    if (!await confirm("回退到上一版界面并重启；没有上一版时使用安装包内置界面。请先保存未发送的输入。是否继续？", { title: "回退界面", kind: "warning" })) return;
    if (!live.current || disabledRef.current) return;
    setWorking(true);
    try { await invoke("rollback_ui_update"); await invoke("restart_application"); }
    catch (error) { setStatus(String(error)); }
    finally { setWorking(false); }
  }
  return <section className="maintenance-card ui-update-card"><div className="maintenance-icon">◇</div><div><strong>界面热更新</strong><small>GitHub 签名小包 · 重启生效 · 不更新后端</small>{local && <small>宿主 {local.host_version} · {local.loaded_revision ? `当前界面 #${local.loaded_revision}` : "安装包内置界面"}{local.pending ? ` · #${local.staged_revision} 待重启` : ""}</small>}</div>
    {local?.development && <small>开发版直接自动刷新，热更新仅供正式安装版使用。</small>}
    <button type="button" disabled={disabled || working || local?.development} onClick={() => void check()}>{working ? "处理中…" : "检查界面更新"}</button>
    {info?.available && !local?.pending && <button type="button" className="primary" disabled={disabled || working} onClick={() => void apply()}>下载并验证</button>}
    {local?.pending && <button type="button" className="primary" disabled={disabled || working} onClick={() => void restart().catch(error => setStatus(String(error)))}>重启应用更新</button>}
    <button type="button" disabled={disabled || working || local?.development} onClick={() => void rollback()}>回退界面</button>
    {status && <small role="status">{status}</small>}
  </section>;
}
