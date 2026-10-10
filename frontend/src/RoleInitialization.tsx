import { useEffect, useState } from "react";
import "./RoleInitialization.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
type Template = { id: string; name: string; content: string; source: "builtin" | "custom" };
type Props = { enabled: boolean; content: string; onChange: (patch: { initial_prompt_enabled?: boolean; initial_prompt?: string }) => void };

export default function RoleInitialization({ enabled, content, onChange }: Props) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [variables, setVariables] = useState<Record<string, string>>({});
  const slots = [...new Set(Array.from(content.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g), (item) => item[1]))].filter((slot) => slot !== "角色名称");
  const chosen = templates.find((item) => item.id === selected);
  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}/role-initialization/templates${path}`, options);
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "操作失败，请检查输入或本地服务");
    return value;
  }
  useEffect(() => {
    let live = true;
    void request("").then((value: Template[]) => { if (live) setTemplates(value); }).catch((error: Error) => { if (live) setStatus(error.message); });
    return () => { live = false; };
  }, []);
  function select(id: string) {
    setSelected(id);
    setVariables({});
    const template = templates.find((item) => item.id === id);
    if (template) { setName(template.name); onChange({ initial_prompt: template.content }); setStatus("已填入模板；保存角色后生效"); }
  }
  async function saveTemplate(update: boolean) {
    if (!name.trim() || !content.trim()) { setStatus("请填写模板名称和内容"); return; }
    setBusy(true);
    try {
      const saved: Template = await request(update ? `/${selected}` : "", {
        method: update ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim(), content }),
      });
      setTemplates((items) => update ? items.map((item) => item.id === saved.id ? saved : item) : [...items, saved]);
      setSelected(saved.id);
      setStatus("已保存本机模板；角色指令仍需保存角色，不会改变其他角色");
    } catch (error) { setStatus((error as Error).message); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!chosen || chosen.source !== "custom" || !window.confirm(`删除本机模板“${chosen.name}”？已保存的角色副本不受影响。`)) return;
    setBusy(true);
    try {
      await request(`/${selected}`, { method: "DELETE" });
      setTemplates((items) => items.filter((item) => item.id !== selected));
      setSelected(""); setStatus("模板已删除，输入框和角色副本未清除");
    } catch (error) { setStatus((error as Error).message); }
    finally { setBusy(false); }
  }
  function fillVariables() {
    if (slots.some((slot) => !variables[slot]?.trim())) { setStatus("请填完变量；角色名称会在发送时自动填入"); return; }
    onChange({ initial_prompt: content.replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (match, slot: string) => slot === "角色名称" ? match : variables[slot] ?? match) });
    setVariables({}); setStatus("变量已填入，保存角色后生效");
  }
  return <fieldset className="role-initialization">
    <legend>首轮角色初始化</legend>
    <label className="role-initialization-toggle"><input type="checkbox" checked={enabled} onChange={(event) => onChange({ initial_prompt_enabled: event.target.checked })} />启用首轮指令</label>
    <small>每个新对话仅在首轮成功完成时执行；失败或中断可重试。旧对话不会重新初始化，不会并入长期角色提示词。</small>
    <label>软件自带 / 本机自定义模板<select value={selected} disabled={busy} onChange={(event) => select(event.target.value)}>
      <option value="">手动填写或选择模板</option>
      <optgroup label="软件内置 · 每个人安装后都可用">{templates.filter((item) => item.source === "builtin").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</optgroup>
      <optgroup label="本机自定义">{templates.filter((item) => item.source === "custom").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</optgroup>
    </select></label>
    <label>首轮指令内容<textarea rows={6} maxLength={12000} value={content} onChange={(event) => onChange({ initial_prompt: event.target.value })} placeholder="可以直接编辑；模板修改不会自动覆盖其他角色" /></label>
    {slots.length > 0 && <div className="role-initialization-variables">
      {slots.map((slot) => <label key={slot}>{slot}<input value={variables[slot] ?? ""} onChange={(event) => setVariables((values) => ({ ...values, [slot]: event.target.value }))} /></label>)}
      <button type="button" onClick={fillVariables}>填入变量</button>
    </div>}
    <small>可用 {'{{角色名称}}'} 自动填入当前角色名；其他 {'{{变量}}'} 请先填写。首轮回复仍会留在聊天历史中，后续可能受其影响。</small>
    <label>模板名称<input maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：我的小说开篇" /></label>
    <div className="role-initialization-actions">
      <button type="button" disabled={busy} onClick={() => void saveTemplate(false)}>另存为本机模板</button>
      {chosen?.source === "custom" && <><button type="button" disabled={busy} onClick={() => void saveTemplate(true)}>更新此模板</button><button type="button" className="danger" disabled={busy} onClick={() => void remove()}>删除模板</button></>}
      {chosen?.source === "builtin" && <button type="button" disabled={busy} onClick={() => select(selected)}>恢复内置原文</button>}
    </div>
    <small role="status">{status || "内置模板随软件打包；自定义模板仅保存在本机，不会发布到其他人的安装包"}</small>
  </fieldset>;
}
