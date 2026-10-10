import { useEffect, useState } from "react";
import { mobileDb, type MobileCharacter } from "./localDb";
import { BUILTIN_INITIAL_TEMPLATES, renderInitialPrompt, type InitialTemplate } from "./roleInitialization";
import { templateFields } from "./promptBuilder";

export default function InitialPromptPanel({ character, onSave }: { character: MobileCharacter; onSave: (character: MobileCharacter) => Promise<void> }) {
  const [enabled, setEnabled] = useState(Boolean(character.initialPromptEnabled));
  const [content, setContent] = useState(character.initialPrompt ?? "");
  const [templates, setTemplates] = useState<InitialTemplate[]>(BUILTIN_INITIAL_TEMPLATES);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const chosen = templates.find((item) => item.id === selected);
  const fields = templateFields(content).filter((field) => field !== "角色名称");
  useEffect(() => {
    let live = true;
    mobileDb.initialTemplates().then((items) => { if (live) setTemplates([...BUILTIN_INITIAL_TEMPLATES, ...items]); }).catch((error: Error) => { if (live) setStatus(error.message); });
    return () => { live = false; };
  }, []);
  function select(id: string) {
    const item = templates.find((template) => template.id === id);
    setSelected(id); setVariables({}); setConfirmDelete(false);
    if (item) { setContent(item.content); setName(item.name); setStatus("已填入模板，保存角色后生效"); }
  }
  async function run(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); } catch (error) { setStatus((error as Error).message); }
    finally { setBusy(false); }
  }
  async function saveTemplate(update: boolean) {
    if (!name.trim() || !content.trim()) throw new Error("请填写名称与内容");
    const item: InitialTemplate = { id: update ? selected : `custom-${crypto.randomUUID()}`, name: name.trim(), content: content.trim(), source: "custom" };
    await mobileDb.putInitialTemplate(item);
    setTemplates((items) => update ? items.map((old) => old.id === item.id ? item : old) : [...items, item]);
    setSelected(item.id); setStatus("本机模板已保存；角色配置仍需单独保存");
  }
  return <div className="standalone-card mobile-initial-panel">
    <label className="mobile-initial-toggle"><input type="checkbox" checked={enabled} disabled={busy} onChange={(event) => setEnabled(event.target.checked)} />启用首轮指令</label>
    <small>每个新对话仅首轮发送；失败或未完整输出可重试。旧对话不重新初始化。</small>
    <label>选择模板<select value={selected} disabled={busy} onChange={(event) => select(event.target.value)}><option value="">手动填写或选择模板</option><optgroup label="软件内置">{templates.filter((item) => item.source === "builtin").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</optgroup><optgroup label="本机自定义">{templates.filter((item) => item.source === "custom").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</optgroup></select></label>
    <label>首轮指令<textarea rows={7} maxLength={12000} value={content} disabled={busy} onChange={(event) => setContent(event.target.value)} /></label>
    {fields.map((field) => <label key={field}>{field}<input value={variables[field] ?? ""} onChange={(event) => setVariables((items) => ({ ...items, [field]: event.target.value }))} /></label>)}
    {fields.length > 0 && <button type="button" disabled={busy} onClick={() => {
      if (fields.some((field) => !variables[field]?.trim())) { setStatus("请填完变量"); return; }
      setContent(content.replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (match, field: string) => field === "角色名称" ? match : variables[field].trim()));
      setVariables({});
    }}>填入变量</button>}
    <small>{'{{角色名称}}'} 会自动填入。首轮回复仍会留在聊天历史中。</small>
    <label>模板名称<input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} /></label>
    <div className="mobile-editor-actions"><button type="button" disabled={busy} onClick={() => void run(() => saveTemplate(false))}>另存模板</button>{chosen?.source === "builtin" && <button type="button" disabled={busy} onClick={() => select(selected)}>恢复原文</button>}{chosen?.source === "custom" && <><button type="button" disabled={busy} onClick={() => void run(() => saveTemplate(true))}>更新模板</button><button type="button" disabled={busy} onClick={() => {
      if (!confirmDelete) { setConfirmDelete(true); return; }
      void run(async () => { await mobileDb.deleteInitialTemplate(selected); setTemplates((items) => items.filter((item) => item.id !== selected)); setSelected(""); setConfirmDelete(false); setStatus("模板已删除，角色副本保留"); });
    }}>{confirmDelete ? "确认删除" : "删除模板"}</button></>}</div>
    <button type="button" className="standalone-primary" disabled={busy} onClick={() => void run(async () => {
      if (enabled && content.trim()) renderInitialPrompt(content, character.name);
      await onSave({ ...character, initialPromptEnabled: enabled, initialPrompt: content.trim() });
      setStatus("角色首轮配置已保存；新建对话测试");
    })}>保存角色配置</button>
    <small role="status">{status || "内置模板人人可用；自定义模板仅在本机保存"}</small>
  </div>;
}
