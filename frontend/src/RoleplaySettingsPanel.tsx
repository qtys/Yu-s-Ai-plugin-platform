import { useEffect, useId, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./RoleplaySettings.css";

const API = (import.meta.env.VITE_API_URL ?? "http://localhost:8000/api") + "/plugins/roleplay";
type Character = { id: number; name: string };
type Prompt = { identifier: string; name?: string; content?: string; role?: string; injection_position?: number; enabled?: boolean };
const blockLabels: Record<string, string> = { main: "主提示词", worldInfoBefore: "世界书 · 设定前", charDescription: "角色描述", charPersonality: "角色性格", scenario: "情境 / 场景", personaDescription: "用户人设", worldInfoAfter: "世界书 · 设定后", dialogueExamples: "示例对白", chatHistory: "真实聊天历史", jailbreak: "历史后置指令", nsfw: "辅助提示词", enhanceDefinitions: "设定补充" };
const terms = {
  preset: { name: "预设", explanation: "一套发给模型的回复规则，决定如何组织提示词和聊天历史。角色卡管‘是谁’，预设管‘怎么回应’。新手先用内置预设即可。" },
  persona: { name: "用户人设", explanation: "告诉角色你是谁、你们是什么关系。例如：‘我是来森林小屋做客的旅人’。留空也能聊天。" },
  scan: { name: "扫描最近消息数", explanation: "世界书在最近多少条消息里寻找关键词，包含这次发送的消息。例如设为 8，只检查最近 8 条；这不是模型的聊天上下文长度。" },
  scene: { name: "情境 / 场景", explanation: "对话发生的地点、状态和背景。例如：‘傍晚，两人在小屋里喝茶’。不是实时屏幕信息，也不会自动推进时间。" },
  post: { name: "历史之后的指令", explanation: "放在聊天记录之后、每轮持续发送的补充规则。例如：‘不要替我决定动作’。与只发送一次的首轮初始化不同。" },
  lore: { name: "世界书", explanation: "按需要提供给模型的设定资料库。例如，聊到‘水杯’时才加入杯子的颜色和来历，避免每次都发送全部设定。" },
  budget: { name: "Token / 预算", explanation: "Token 是模型处理文字的计量单位，不等于字数。这里的预算只限制本轮世界书内容，超出时优先保留高优先级条目；是粗略估算，不是回复字数上限。" },
  constant: { name: "常驻", explanation: "不用匹配关键词，每轮都尝试加入这个条目；仍须启用，且受世界书预算限制。适合重要的背景设定。" },
  keywords: { name: "关键词", explanation: "出现任意一个关键词就可以触发条目。例如：‘水杯，杯子’。按文字包含匹配，不是模型自动理解同义词。" },
  secondary: { name: "辅助关键词", explanation: "开启后，主关键词和辅助关键词都要各匹配至少一个。例如主关键词‘水杯’，辅助关键词‘蓝色’，两组都命中才触发（常驻条目除外）。" },
  priority: { name: "预算优先级", explanation: "预算装不下所有条目时，数值越大的越优先保留。它不代表指令权限，也不决定排列位置。" },
  order: { name: "排列 / 注入位置", explanation: "决定设定内容放在发给模型的哪个位置。条目顺序越小越靠前；‘设定之前/之后’指相对角色设定的位置，不是界面上的位置。酒馆的绝对深度插入暂不支持。" },
  macro: { name: "宏 / 变量", explanation: "宏是会被替换的模板，例如 {{char}} 变成角色名，{{user}} 变成你的称呼。setvar/getvar 用来暂存/读取文本，只在本次请求有效。含不支持宏的模块会跳过。" },
  messageRole: { name: "消息角色", explanation: "System 是给模型的规则，User 是用户发言，Assistant 是模型发言/示例。一般规则选 System；这里只改变请求格式，不会新建角色卡。" },
  diagnostic: { name: "兼容性诊断", explanation: "检查导入预设有哪些模块可以处理、被禁用或不受支持。‘可处理’不保证模型一定遵守；实际发送了什么，要看请求检查。" },
  request: { name: "请求检查", explanation: "查看准备发给模型的内容。预览不调用模型；最近实际主请求显示上一次真实发送的内容，可能包含私人聊天。" },
  json: { name: "JSON / Chat Completion", explanation: "JSON 是保存配置的文本文件格式；Chat Completion 是按 System/User/Assistant 消息组织对话的预设格式。直接选择支持的酒馆预设文件，不需要手写 JSON。" },
} satisfies Record<string, { name: string; explanation: string }>;
type TermKey = keyof typeof terms;

function Term({ term, text }: { term: TermKey; text?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const help = terms[term];
  return <span className="roleplay-term"><span>{text ?? help.name}</span><button type="button" className="roleplay-term-help" aria-label={`解释${help.name}`} aria-expanded={open} aria-controls={id} onClick={(event) => { event.preventDefault(); setOpen(!open); }} onBlur={() => setOpen(false)}>?</button>{open && <span id={id} className="roleplay-term-explanation" role="note">{help.explanation}</span>}</span>;
}
type OrderItem = { identifier: string; enabled?: boolean };
type PresetData = { prompts: Prompt[]; prompt_order?: { character_id: number; order: OrderItem[] }[]; [key: string]: unknown };
type Preset = { id: string; name: string; builtin: boolean; data: PresetData; warnings?: string[] };
type Diagnostic = { counts: Record<string, number>; estimated_tokens: number; warnings: string[]; note: string; blocks: { identifier: string; name: string; status: string; unsupported_macros: string[] }[] };
type Entry = { keys?: string[]; secondary_keys?: string[]; content: string; enabled?: boolean; constant?: boolean; selective?: boolean; position?: string | number; insertion_order?: number; [key: string]: unknown };
type Book = { entries: Entry[]; [key: string]: unknown };
type Config = { preset_id: string; user_name: string; user_persona: string; lore_enabled: boolean; scan_depth: number; lore_budget: number; book: Book | null; greeting_index: number };
type Settings = { config: Config & { greeting_index: number }; book: Book; scenario: string; post_history_instructions: string; alternate_greetings: string[]; source: { creator: string; notes: string; tags: string[] } };
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API + path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "字段格式无效，请检查输入");
  return data as T;
}
async function download(name: string, value: unknown, kind = "preset") {
  try {
    const content = JSON.stringify(value, null, 2);
    if ("__TAURI_INTERNALS__" in window) {
      const path = await invoke<string>("export_roleplay_json", { name: name.replace(/\.json$/i, ""), content, kind });
      window.alert(`已导出到：\n${path}`);
    } else {
      const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url);
    }
  } catch (e) { window.alert(`导出失败：${String(e)}`); }
}

// Serialized, debounced writes; closing the modal flushes the captured role/preset,
// not whichever role is selected afterwards. Failed writes remain retryable.
function useAutoSave(save: (value: unknown) => Promise<unknown>) {
  const [status, setStatus] = useState("修改后自动保存");
  const pending = useRef<unknown>(undefined), timer = useRef(0), live = useRef(true), revision = useRef(0);
  const queue = useRef(Promise.resolve());
  const saveRef = useRef(save); saveRef.current = save;
  function flush() {
    const value = pending.current; pending.current = undefined;
    const version = revision.current;
    if (value === undefined) return;
    const saveValue = saveRef.current;
    queue.current = queue.current.then(async () => {
      try { await saveValue(value); if (live.current) setStatus(pending.current === undefined ? "已自动保存" : "等待自动保存…"); }
      catch (e) { if (pending.current === undefined && version === revision.current) pending.current = value; if (live.current && version === revision.current) setStatus(`保存失败：${(e as Error).message}`); }
    });
  }
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; window.clearTimeout(timer.current); flush(); };
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- flush the captured target on close
  }, []);
  function schedule(value: unknown) {
    revision.current += 1; pending.current = value; setStatus("等待自动保存…"); window.clearTimeout(timer.current); timer.current = window.setTimeout(flush, 700);
  }
  return { schedule, status, flush };
}

export default function RoleplaySettingsPanel({ characters, activeCharacter, activeConversation }: { characters: Character[]; activeCharacter: number | null; activeConversation: number | null }) {
  const [role, setRole] = useState<number | null>(activeCharacter ?? characters[0]?.id ?? null);
  const [presets, setPresets] = useState<Preset[]>([]), [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [report, setReport] = useState<Diagnostic | null>(null);
  const [pendingImport, setPendingImport] = useState<{ name: string; data: PresetData } | null>(null);
  async function reload() { setPresets(await call<Preset[]>("/presets")); }
  useEffect(() => { let live = true; void call<Preset[]>("/presets").then((value) => { if (live) setPresets(value); }).catch((e: Error) => { if (live) setError(e.message); }); return () => { live = false; }; }, []);
  async function importPreset(file?: File) {
    if (!file) return;
    try {
      if (file.size > 1_500_000) throw new Error("预设文件最大 1.5 MB");
      const data = JSON.parse(await file.text()) as PresetData;
      const payload = { name: file.name.replace(/\.json$/i, "").slice(0, 80), data };
      setReport(await call<Diagnostic>("/presets/inspect", { method: "POST", body: JSON.stringify(payload) }));
      setPendingImport(payload); setError("");
    } catch (e) { setError((e as Error).message); }
  }
  async function confirmImport() {
    if (!pendingImport) return;
    const payload = pendingImport; setPendingImport(null);
    try { const value = await call<Preset>("/presets", { method: "POST", body: JSON.stringify(payload) }); await reload(); setEditing(value.id); }
    catch (e) { setError((e as Error).message); setPendingImport(payload); }
  }
  async function diagnose(preset: Preset) {
    try { setPendingImport(null); setReport(await call<Diagnostic>("/presets/inspect", { method: "POST", body: JSON.stringify({ name: preset.name, data: preset.data }) })); }
    catch (e) { setError((e as Error).message); }
  }
  async function copy(preset: Preset) {
    try { const value = await call<Preset>("/presets", { method: "POST", body: JSON.stringify({ name: `${preset.name.slice(0, 72)} · 副本`, data: preset.data }) }); await reload(); setEditing(value.id); }
    catch (e) { setError((e as Error).message); }
  }
  async function remove(preset: Preset) {
    if (!window.confirm(`删除“${preset.name}”？绑定它的角色将改用内置预设。`)) return;
    try { await call(`/presets/${preset.id}`, { method: "DELETE" }); await reload(); setEditing(null); setRole(null); }
    catch (e) { setError((e as Error).message); }
  }
  const preset = presets.find((p) => p.id === editing);
  return <div className="roleplay-settings">
    <p>选择角色与预设，按需补充世界书。术语旁的「?」可查看解释。</p>
    <details className="roleplay-glossary"><summary>术语说明 · 看不懂时再展开</summary><dl>{Object.entries(terms).map(([key, value]) => <div key={key}><dt>{value.name}</dt><dd>{value.explanation}</dd></div>)}</dl></details>
    <label>配置角色<select value={role ?? ""} onChange={(e) => setRole(Number(e.target.value) || null)}><option value="">选择角色</option>{characters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    {role && presets.length > 0 && <RoleSettings key={role} id={role} presets={presets} conversationId={role === activeCharacter ? activeConversation : null} />}
    <details><summary>预设库 · {presets.length} 个</summary>
      <Term term="preset" text="什么是预设？" />
      <Term term="diagnostic" />
      <Term term="json" />
      <label className="roleplay-upload">导入酒馆 Chat Completion JSON<input type="file" accept=".json,application/json" onChange={(e) => { void importPreset(e.target.files?.[0]); e.target.value = ""; }} /></label>
      {report && <fieldset><legend>兼容性诊断{pendingImport ? ` · ${pendingImport.name}（尚未导入）` : ""}</legend><p>可处理 {report.counts.ready + report.counts.dynamic} · 禁用 {report.counts.disabled} · 不兼容 {report.counts.unsupported} · 空白 {report.counts.empty} · 原文约 {report.estimated_tokens} token</p><small>{report.note}</small>{report.warnings.map((w) => <p key={w}>{w}</p>)}<details><summary>逐模块检查</summary>{report.blocks.map((b) => <p key={b.identifier}>{b.name}：{({ ready: "可处理", dynamic: "角色 / 历史动态内容", disabled: "禁用 / 未排序", unsupported: "不支持，跳过", empty: "空白" } as Record<string, string>)[b.status]}{b.unsupported_macros.length > 0 && `（${b.unsupported_macros.join("、")}）`}</p>)}</details>{pendingImport && <><button onClick={() => void confirmImport()}>确认导入（保留原始字段）</button><button onClick={() => { setPendingImport(null); setReport(null); }}>取消</button></>}</fieldset>}
      {presets.map((p) => <div className="roleplay-preset-row" key={p.id}><strong>{p.name}</strong><small>{p.builtin ? "内置" : "自定义"}</small><button onClick={() => setEditing(editing === p.id ? null : p.id)}>查看 / 编辑</button><button onClick={() => void diagnose(p)}>兼容诊断</button><button onClick={() => void copy(p)}>复制</button><button onClick={() => download(`${p.name}.json`, p.data)}>导出</button>{!p.builtin && <button onClick={() => void remove(p)}>删除</button>}</div>)}
      {preset && <PresetEditor key={preset.id} preset={preset} onSaved={() => void reload()} />}
    </details>
    {error && <p role="alert">{error}</p>}
    <small>兼容范围：V2 PNG / JSON 角色卡、Chat Completion 提示词顺序、关键词 / 常驻世界书。未知字段保留，脚本、正则、深度注入与递归扫描不执行。角色卡从本页「我的角色」中的导入入口添加。</small>
  </div>;
}

function RoleSettings({ id, presets, conversationId }: { id: number; presets: Preset[]; conversationId: number | null }) {
  const [settings, setSettings] = useState<Settings | null>(null), [error, setError] = useState("");
  const [preview, setPreview] = useState<unknown>(null), [testText, setTestText] = useState("你好");
  const { schedule, status, flush } = useAutoSave(async (v) => { await call(`/characters/${id}`, { method: "PATCH", body: JSON.stringify(v) }); });
  useEffect(() => { let current = true; void call<Settings>(`/characters/${id}`).then((v) => { if (current) setSettings(v); }).catch((e: Error) => { if (current) setError(e.message); }); return () => { current = false; }; }, [id]);
  function change(patch: Partial<Settings>, config: Partial<Config> = {}) {
    if (!settings) return;
    const next = { ...settings, ...patch, config: { ...settings.config, ...config } }; setSettings(next);
    schedule({ config: next.config });
  }
  function bookChange(entries: Entry[]) {
    if (!settings) return;
    const book = { ...settings.book, entries }; change({ book }, { book });
  }
  async function inspect(actual: boolean) {
    try {
      setError("");
      setPreview(actual ? await call(`/requests/${conversationId}`) : await call(`/characters/${id}/preview`, { method: "POST", body: JSON.stringify({ content: testText, conversation_id: conversationId }) }));
    } catch (e) { setError((e as Error).message); }
  }
  if (!settings) return <small>{error || "正在读取角色配置…"}</small>;
  const config = settings.config;
  return <>
    <label><Term term="preset" text="绑定预设" /><select value={config.preset_id} onChange={(e) => change({}, { preset_id: e.target.value })}>{presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <div className="roleplay-two"><label>用户称呼<input maxLength={80} value={config.user_name} onChange={(e) => change({}, { user_name: e.target.value })} /></label><label><Term term="scan" /><input type="number" min={1} max={100} value={config.scan_depth} onChange={(e) => change({}, { scan_depth: Number(e.target.value) })} /></label></div>
    <label><Term term="persona" /><textarea rows={2} value={config.user_persona} onChange={(e) => change({}, { user_persona: e.target.value })} /></label>
    {settings.alternate_greetings.length > 0 && <label>新会话开场白<select value={config.greeting_index} onChange={(e) => change({}, { greeting_index: Number(e.target.value) })}><option value={-1}>默认开场白</option>{settings.alternate_greetings.map((text, index) => <option key={index} value={index}>{index + 1}. {text.slice(0, 45)}</option>)}</select></label>}
    <small>角色的场景与后置规则请在角色卡中编辑，这里不重复填写。</small>
    <details><summary>世界书 · {settings.book.entries.length} 条</summary>
      <Term term="lore" text="世界书是什么？" />
      <details><summary>世界书设置说明</summary><Term term="constant" /><Term term="keywords" /><Term term="secondary" /><Term term="priority" /><Term term="order" /></details>
      <label>关键词世界书<input type="checkbox" checked={config.lore_enabled} onChange={(e) => change({}, { lore_enabled: e.target.checked })} /></label>
      <label><Term term="budget" text="预算（估算 token）" /><input type="number" min={0} max={16000} value={config.lore_budget} onChange={(e) => change({}, { lore_budget: Number(e.target.value) })} /></label>
      <small>预算是本地字符估算，不是服务商精确 tokenizer。只扫描当前角色的当前会话。条目顺序决定排列，导入的 priority 决定预算优先级。</small>
      {settings.book.entries.map((entry, index) => <fieldset key={index}><legend>条目 {index + 1}</legend>
        <div className="roleplay-two"><label>启用<input type="checkbox" checked={entry.enabled !== false} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, enabled: e.target.checked } : x))} /></label><label>常驻<input type="checkbox" checked={!!entry.constant} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, constant: e.target.checked } : x))} /></label></div>
        <label>关键词（逗号分隔）<input value={(entry.keys ?? []).join(", ")} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, keys: e.target.value.split(/[,，]/).map((s) => s.trim()) } : x))} /></label>
        <div className="roleplay-two"><label>同时匹配辅助关键词<input type="checkbox" checked={!!entry.selective} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, selective: e.target.checked } : x))} /></label><label>区分大小写<input type="checkbox" checked={!!entry.case_sensitive} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, case_sensitive: e.target.checked } : x))} /></label></div>
        {entry.selective && <label>辅助关键词（逗号分隔）<input value={(entry.secondary_keys ?? []).join(", ")} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, secondary_keys: e.target.value.split(/[,，]/).map((s) => s.trim()) } : x))} /></label>}
        <div className="roleplay-two"><label>预算优先级（越大越优先）<input type="number" value={Number(entry.priority ?? 0)} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, priority: Number(e.target.value) } : x))} /></label><label>排列顺序（越小越靠前）<input type="number" value={entry.insertion_order ?? index} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, insertion_order: Number(e.target.value) } : x))} /></label></div>
        <label>内容<textarea rows={3} value={entry.content} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, content: e.target.value } : x))} /></label>
        <label>位置<select value={String(entry.position ?? "before_char")} onChange={(e) => bookChange(settings.book.entries.map((x, i) => i === index ? { ...x, position: e.target.value } : x))}><option value="before_char">角色设定之前</option><option value="after_char">角色设定之后</option><option value="0">导入：之前</option><option value="1">导入：之后</option></select></label>
        <button onClick={() => bookChange(settings.book.entries.filter((_, i) => i !== index))}>删除条目</button>
      </fieldset>)}
      <button onClick={() => bookChange([...settings.book.entries, { keys: [], content: "", enabled: true, insertion_order: settings.book.entries.length, position: "before_char" }])}>添加条目</button>
      <button onClick={() => void download("worldbook.json", settings.book, "worldbook")}>导出世界书</button>
      <label className="roleplay-upload">导入世界书 JSON<input type="file" accept=".json" onChange={(e) => {
        const file = e.target.files?.[0]; if (file && file.size > 1_500_000) { setError("世界书文件最大 1.5 MB"); e.target.value = ""; return; } if (file) void file.text().then(async (text) => {
          const book = await call<Book>("/worldbooks/inspect", { method: "POST", body: JSON.stringify({ filename: file.name, content: text }) }); change({ book }, { book });
        }).catch((ex: Error) => setError(ex.message)); e.target.value = "";
      }} /></label>
    </details>
    <div className="roleplay-actions"><button onClick={() => void call(`/characters/${id}/export`).then((value) => download("character.chara.json", value, "character")).catch((e: Error) => setError(e.message))}>导出酒馆角色卡 JSON</button></div>
    <small role="status">{status}</small>{status.startsWith("保存失败") && <button onClick={flush}>重试保存</button>}
    <details><summary>请求检查（仅本机可见）</summary><label>测试消息<input value={testText} onChange={(e) => setTestText(e.target.value)} /></label>
      <Term term="request" text="这里能查看什么？" />
      <div className="roleplay-actions"><button onClick={() => void inspect(false)}>预览角色扮演层</button><button disabled={!conversationId} onClick={() => void inspect(true)}>最近实际主请求</button></div>
      <small>先等待「已自动保存」再预览。实际快照可能包含私人聊天，导出角色卡不包含快照，请勿公开。</small>
      {preview !== null && <><RequestDiagnostic value={preview} /><details><summary>完整请求 JSON</summary><pre className="roleplay-preview">{JSON.stringify(preview, null, 2)}</pre></details></>}
    </details>
    {error && <p role="alert">{error}</p>}
    {settings.source.creator && <small>作者：{settings.source.creator}</small>}
  </>;
}

export function RoleCardContext({ id }: { id: number }) {
  const [fields, setFields] = useState<{ scenario: string; post_history_instructions: string } | null>(null);
  const [error, setError] = useState("");
  const { schedule, status, flush } = useAutoSave(async (value) => call(`/characters/${id}`, { method: "PATCH", body: JSON.stringify(value) }));
  useEffect(() => { let live = true; void call<Settings>(`/characters/${id}`).then((value) => { if (live) setFields({ scenario: value.scenario, post_history_instructions: value.post_history_instructions }); }).catch((e: Error) => { if (live) setError(e.message); }); return () => { live = false; }; }, [id]);
  if (!fields) return <small>{error || "正在读取角色场景…"}</small>;
  function change(key: "scenario" | "post_history_instructions", value: string) {
    const next = { ...fields!, [key]: value }; setFields(next); schedule(next);
  }
  return <details className="roleplay-settings"><summary>角色场景与补充规则（可选）</summary><label><Term term="scene" /><textarea rows={3} value={fields.scenario} onChange={(event) => change("scenario", event.target.value)} /></label><label><Term term="post" /><textarea rows={3} value={fields.post_history_instructions} onChange={(event) => change("post_history_instructions", event.target.value)} /></label><small role="status">此处修改独立自动保存 · {status}</small>{status.startsWith("保存失败") && <button type="button" onClick={flush}>重试保存</button>}</details>;
}

function RequestDiagnostic({ value }: { value: unknown }) {
  if (!value || typeof value !== "object") return null;
  const object = value as Record<string, unknown>;
  const plan = (object.plan && typeof object.plan === "object" ? object.plan : object) as Record<string, unknown>;
  const warnings = Array.isArray(plan.warnings) ? plan.warnings.filter((w): w is string => typeof w === "string") : [];
  const statuses: Record<string, string> = { sent: "已发送", history: "聊天历史位置", disabled: "禁用", unsupported: "不兼容，跳过", empty_or_variable: "空白 / 仅变量初始化", empty_or_unsupported: "空白 / 不兼容" };
  return <fieldset><legend>本次组装结果</legend>{typeof plan.macro_scope === "string" && <small>{plan.macro_scope}</small>}{warnings.map((w, index) => <p key={index}>{w}</p>)}{Array.isArray(plan.diagnostics) && <details><summary>实际模块状态</summary>{plan.diagnostics.map((item: unknown, index: number) => {
    if (!item || typeof item !== "object") return null;
    const block = item as Record<string, unknown>;
    return <p key={index}>{typeof block.identifier === "string" ? blockLabels[block.identifier] ?? block.identifier : "模块"}：{typeof block.status === "string" ? statuses[block.status] ?? block.status : "未知"}</p>;
  })}</details>}</fieldset>;
}

function PresetEditor({ preset, onSaved }: { preset: Preset; onSaved: () => void }) {
  const [value, setValue] = useState(preset);
  const { schedule, status, flush } = useAutoSave(async (v) => { await call(`/presets/${preset.id}`, { method: "PUT", body: JSON.stringify(v) }); onSaved(); });
  const defaults = ["main", "worldInfoBefore", "charDescription", "charPersonality", "scenario", "personaDescription", "worldInfoAfter", "dialogueExamples", "chatHistory", "jailbreak"];
  const groups = value.data.prompt_order?.length ? value.data.prompt_order : [{ character_id: 100001, order: [...defaults, ...value.data.prompts.map((p) => p.identifier).filter((id) => !defaults.includes(id))].map((identifier) => ({ identifier, enabled: true })) }];
  const group = groups.find((g) => g.character_id === 100001) ?? groups[0];
  function update(next: Preset) { setValue(next); schedule({ name: next.name, data: next.data }); }
  function orderChange(order: OrderItem[]) { update({ ...value, data: { ...value.data, prompt_order: groups.map((g) => g === group ? { ...g, order } : g) } }); }
  return <><details><summary>提示词编辑说明</summary><Term term="macro" /><Term term="messageRole" /><Term term="order" /></details><fieldset disabled={preset.builtin}><legend>{preset.builtin ? "内置预设 · 复制后编辑" : "自定义预设 · 修改会影响所有绑定角色"}</legend>
    <small>提示词段就是发给模型的一段规则；上移 / 下移调整发送顺序。常用模块已显示中文名。</small>
    <label>名称<input value={value.name} maxLength={80} onChange={(e) => update({ ...value, name: e.target.value })} /></label>
    {group.order.map((item, index) => { const prompt = value.data.prompts.find((p) => p.identifier === item.identifier); return <div className="roleplay-block" key={item.identifier}>
      <div className="roleplay-preset-row"><label><input type="checkbox" disabled={item.identifier === "chatHistory"} checked={item.enabled !== false} onChange={(e) => orderChange(group.order.map((x, i) => i === index ? { ...x, enabled: e.target.checked } : x))} />{blockLabels[item.identifier] ?? prompt?.name ?? "自定义提示词"}</label><button aria-label={`向上移动 ${blockLabels[item.identifier] ?? item.identifier}`} disabled={index === 0} onClick={() => { const order = [...group.order]; [order[index - 1], order[index]] = [order[index], order[index - 1]]; orderChange(order); }}>↑</button><button aria-label={`向下移动 ${blockLabels[item.identifier] ?? item.identifier}`} disabled={index === group.order.length - 1} onClick={() => { const order = [...group.order]; [order[index + 1], order[index]] = [order[index], order[index + 1]]; orderChange(order); }}>↓</button></div>
      {!blockLabels[item.identifier] && prompt && <label>段落名称<input value={prompt.name ?? "自定义提示词"} onChange={(e) => update({ ...value, data: { ...value.data, prompts: value.data.prompts.map((p) => p.identifier === item.identifier ? { ...p, name: e.target.value } : p) } })} /></label>}
      {prompt && !["charDescription", "charPersonality", "scenario", "dialogueExamples", "chatHistory", "worldInfoBefore", "worldInfoAfter", "personaDescription"].includes(item.identifier) && <><label>消息角色<select value={prompt.role ?? "system"} onChange={(e) => update({ ...value, data: { ...value.data, prompts: value.data.prompts.map((p) => p.identifier === item.identifier ? { ...p, role: e.target.value } : p) } })}><option value="system">System</option><option value="user">User</option><option value="assistant">Assistant</option></select></label><textarea rows={3} value={prompt.content ?? ""} onChange={(e) => update({ ...value, data: { ...value.data, prompts: value.data.prompts.map((p) => p.identifier === item.identifier ? { ...p, content: e.target.value } : p) } })} /></>}
      {prompt?.injection_position !== undefined && prompt.injection_position !== 0 && <small>此段绝对深度注入暂不发送</small>}
    </div>; })}
    <button onClick={() => {
      const identifier = `custom-${crypto.randomUUID()}`;
      update({ ...value, data: { ...value.data, prompts: [...value.data.prompts, { identifier, role: "system", content: "" }], prompt_order: groups.map((g) => g === group ? { ...g, order: [...g.order, { identifier, enabled: true }] } : g) } });
    }}>添加提示词段</button>
    <small role="status">{status}</small>{status.startsWith("保存失败") && <button onClick={flush}>重试保存</button>}
  </fieldset></>;
}
