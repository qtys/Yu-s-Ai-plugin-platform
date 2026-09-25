import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import MessageContent, { type MessageDisplayMode } from "../src/MessageContent";
import BottomSheet from "./BottomSheet";
import { mobileDb, type MobileCharacter, type MobileConversation, type MobileInstruction, type MobileMessage, type MobileModelProfile, type MobilePromptTemplate, type MobileSettings } from "./localDb";
import { streamModelReply } from "./modelClient";
import { buildMessages, templateFields } from "./promptBuilder";

type Tab = "chat" | "recent" | "roles" | "plugins" | "settings";
type Drawer = "models" | "display" | "instructions" | null;
type UiMessage = MobileMessage & { pending?: boolean };
const PLUGINS = [
  { id: "message_display", name: "消息显示", description: "渲染或过滤 Markdown", supported: true, defaultEnabled: true },
  { id: "conversation_environment", name: "对话环境信息", description: "按需向模型提供当前时间", supported: true, defaultEnabled: true },
  { id: "novel_reply", name: "小说式回复", description: "按角色卡以第三人称小说风格回复", supported: true, defaultEnabled: false },
  { id: "instruction_review", name: "二次审核", description: "需要独立的移动端审核流程", supported: false, defaultEnabled: false },
  { id: "translation", name: "离线翻译", description: "Argos 语言包尚未移植到 Android", supported: false, defaultEnabled: false },
  { id: "proactive", name: "角色主动互动", description: "需要 Android 后台任务和通知适配", supported: false, defaultEnabled: false },
] as const;

const DEFAULT_SETTINGS: MobileSettings = { id: "model", baseUrl: "", model: "", temperature: 0.8, maxTokens: 2048 };

function initialPluginState(): Record<string, boolean> {
  return Object.fromEntries(PLUGINS.map((plugin) => [plugin.id, plugin.supported && (localStorage.getItem(`mobile_plugin_${plugin.id}`) === null ? plugin.defaultEnabled : localStorage.getItem(`mobile_plugin_${plugin.id}`) === "true")]));
}

export default function StandaloneMobileApp() {
  const [tab, setTab] = useState<Tab>("chat");
  const [characters, setCharacters] = useState<MobileCharacter[]>([]);
  const [characterId, setCharacterId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<MobileConversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [settings, setSettings] = useState<MobileSettings>(DEFAULT_SETTINGS);
  const [modelProfiles, setModelProfiles] = useState<MobileModelProfile[]>([]);
  const [instructions, setInstructions] = useState<MobileInstruction[]>([]);
  const [templates, setTemplates] = useState<MobilePromptTemplate[]>([]);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [instructionTab, setInstructionTab] = useState<"saved" | "templates">("saved");
  const [instructionDraft, setInstructionDraft] = useState("");
  const [instructionScope, setInstructionScope] = useState<"character" | "conversation">("character");
  const [editingInstructionId, setEditingInstructionId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [templateVariables, setTemplateVariables] = useState<Record<string, string>>({});
  const [templateScope, setTemplateScope] = useState<"once" | "conversation" | "character">("once");
  const [queuedTemplate, setQueuedTemplate] = useState<{ name: string; content: string } | null>(null);
  const [newProfileName, setNewProfileName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [plugins, setPlugins] = useState(initialPluginState);
  const [displayMode, setDisplayMode] = useState<MessageDisplayMode>(() => {
    const saved = localStorage.getItem("mobile_display_mode");
    return saved === "plain" || saved === "raw" ? saved : "markdown";
  });
  const [newCharacterName, setNewCharacterName] = useState("");
  const [newCharacterPrompt, setNewCharacterPrompt] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const messageScrollRef = useRef<HTMLDivElement>(null);
  const keySaveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const selectedCharacter = characters.find((item) => item.id === characterId);
  const selectedConversation = conversations.find((item) => item.id === conversationId);

  useEffect(() => {
    let live = true;
    Promise.all([mobileDb.characters(), mobileDb.settings(), mobileDb.modelProfiles(), mobileDb.promptTemplates()]).then(([list, savedSettings, profiles, savedTemplates]) => {
      if (!live) return;
      setCharacters(list);
      setModelProfiles(profiles);
      setTemplates(savedTemplates);
      const active = profiles.find((item) => item.id === savedSettings.activeProfileId) ?? profiles[0];
      setSettings(active ? { ...savedSettings, activeProfileId: active.id, baseUrl: active.baseUrl, model: active.model } : savedSettings);
      const preferred = localStorage.getItem("mobile_character_id");
      setCharacterId(list.find((item) => item.id === preferred)?.id ?? list[0]?.id ?? null);
      setReady(true);
    }).catch((cause: Error) => setError(`本地数据打开失败：${cause.message}`));
    return () => { live = false; };
  }, []);

  useEffect(() => {
    let baselineWidth = window.innerWidth;
    let baselineHeight = Math.min(window.innerHeight, window.visualViewport?.height ?? window.innerHeight);
    const syncViewport = () => {
      const visibleHeight = Math.max(1, Math.floor(Math.min(window.innerHeight, window.visualViewport?.height ?? window.innerHeight)));
      if (Math.abs(window.innerWidth - baselineWidth) > 60) {
        baselineWidth = window.innerWidth;
        baselineHeight = visibleHeight;
      } else if (visibleHeight > baselineHeight) {
        baselineHeight = visibleHeight;
      }
      document.documentElement.style.setProperty("--mobile-viewport-height", `${visibleHeight}px`);
      const focused = document.activeElement;
      const editing = focused instanceof HTMLElement && focused.matches("input, textarea, [contenteditable='true']");
      setKeyboardOpen(editing && baselineHeight - visibleHeight > 120);
    };
    syncViewport();
    window.addEventListener("resize", syncViewport);
    window.visualViewport?.addEventListener("resize", syncViewport);
    document.addEventListener("focusin", syncViewport);
    document.addEventListener("focusout", syncViewport);
    return () => {
      window.removeEventListener("resize", syncViewport);
      window.visualViewport?.removeEventListener("resize", syncViewport);
      document.removeEventListener("focusin", syncViewport);
      document.removeEventListener("focusout", syncViewport);
      document.documentElement.style.removeProperty("--mobile-viewport-height");
    };
  }, []);

  useEffect(() => {
    if (!characterId) { setConversations([]); setConversationId(null); return; }
    setQueuedTemplate(null);
    setEditingInstructionId(null);
    setInstructionDraft("");
    setConfirmDeleteId(null);
    setSelectedTemplateId(null);
    setConversations([]);
    setConversationId(null);
    setMessages([]);
    localStorage.setItem("mobile_character_id", characterId);
    let live = true;
    mobileDb.conversations(characterId).then((list) => {
      if (!live) return;
      setConversations(list);
      setConversationId((current) => list.some((item) => item.id === current) ? current : list[0]?.id ?? null);
    }).catch((cause: Error) => setError(cause.message));
    return () => { live = false; };
  }, [characterId]);

  useEffect(() => {
    if (!characterId) { setInstructions([]); return; }
    setEditingInstructionId(null);
    setInstructionDraft("");
    setConfirmDeleteId(null);
    let live = true;
    mobileDb.instructions(characterId, conversationId).then((list) => { if (live) setInstructions(list); }).catch((cause: Error) => setError(cause.message));
    return () => { live = false; };
  }, [characterId, conversationId]);

  useEffect(() => {
    if (!conversationId) { setMessages([]); return; }
    if (busy) return;
    let live = true;
    mobileDb.messages(conversationId).then((list) => { if (live) setMessages(list); }).catch((cause: Error) => setError(cause.message));
    return () => { live = false; };
  }, [conversationId, busy]);

  useEffect(() => {
    if (!ready) return;
    const timer = window.setTimeout(() => {
      const active = modelProfiles.find((item) => item.id === settings.activeProfileId);
      const updates: Promise<unknown>[] = [mobileDb.putSettings(settings)];
      if (active && (active.baseUrl !== settings.baseUrl || active.model !== settings.model)) {
        const changed = { ...active, baseUrl: settings.baseUrl, model: settings.model };
        updates.push(mobileDb.putModelProfile(changed));
        setModelProfiles((list) => list.map((item) => item.id === changed.id ? changed : item));
      }
      Promise.all(updates).catch((cause: Error) => setError(cause.message));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [ready, settings, modelProfiles]);

  useEffect(() => {
    if (!ready || !settings.activeProfileId || !isTauri()) return;
    let live = true;
    setApiKey("");
    invoke<string>("load_model_key", { profileId: settings.activeProfileId })
      .then((key) => { if (live) setApiKey(key); })
      .catch((cause: Error) => { if (live) setError(`读取模型密钥失败：${cause.message}`); });
    return () => { live = false; };
  }, [ready, settings.activeProfileId]);

  useLayoutEffect(() => {
    if (tab !== "chat") return;
    const scrollToLatest = () => {
      const container = messageScrollRef.current;
      if (container) container.scrollTop = container.scrollHeight;
    };
    scrollToLatest();
    const frame = window.requestAnimationFrame(scrollToLatest);
    const timer = window.setTimeout(scrollToLatest, 120);
    return () => { window.cancelAnimationFrame(frame); window.clearTimeout(timer); };
  }, [tab, conversationId, messages, keyboardOpen]);

  async function createCharacter() {
    const name = newCharacterName.trim();
    if (!name) { setError("请填写角色名称"); return; }
    const character: MobileCharacter = { id: crypto.randomUUID(), name, prompt: newCharacterPrompt.trim(), createdAt: Date.now() };
    try {
      await mobileDb.putCharacter(character);
      setCharacters((list) => [...list, character]);
      setCharacterId(character.id);
      setNewCharacterName(""); setNewCharacterPrompt(""); setTab("chat"); setError("");
    } catch (cause) { setError((cause as Error).message); }
  }

  async function createConversation(): Promise<MobileConversation> {
    if (!characterId) throw new Error("请先创建角色");
    const conversation: MobileConversation = { id: crypto.randomUUID(), characterId, title: "新对话", updatedAt: Date.now() };
    await mobileDb.putConversation(conversation);
    setConversations((list) => [conversation, ...list]);
    setConversationId(conversation.id);
    setMessages([]);
    setTab("chat");
    return conversation;
  }

  async function send() {
    const content = draft.trim();
    if (!content || !selectedCharacter || busy) return;
    if (!settings.baseUrl || !settings.model || !apiKey.trim()) {
      setTab("settings"); setError("请先填写 HTTPS 模型地址、模型名称和 API Key"); return;
    }
    setBusy(true); setError("");
    try {
      const conversation = selectedConversation ?? await createConversation();
      const history = await mobileDb.messages(conversation.id);
      const currentInstructions = await mobileDb.instructions(selectedCharacter.id, conversation.id);
      const now = Date.now();
      const userMessage: MobileMessage = { id: crypto.randomUUID(), conversationId: conversation.id, role: "user", content, createdAt: now };
      await mobileDb.putMessage(userMessage);
      setDraft("");
      const pendingId = crypto.randomUUID();
      setMessages([...history, userMessage, { id: pendingId, conversationId: conversation.id, role: "assistant", content: "", createdAt: now + 1, pending: true }]);
      const reply = await streamModelReply({ ...settings, apiKey }, buildMessages(selectedCharacter, history, content, plugins, currentInstructions, queuedTemplate?.content ?? ""), (token) => {
        setMessages((list) => list.map((item) => item.id === pendingId && item.pending ? { ...item, content: item.content + token } : item));
      });
      const assistant: MobileMessage = { id: pendingId, conversationId: conversation.id, role: "assistant", content: reply, createdAt: now + 1 };
      await mobileDb.putMessage(assistant);
      const updated = { ...conversation, title: conversation.title === "新对话" ? content.slice(0, 28) : conversation.title, updatedAt: Date.now() };
      await mobileDb.putConversation(updated);
      setConversations(await mobileDb.conversations(selectedCharacter.id));
      setMessages((list) => list.map((item) => item.id === pendingId ? assistant : item));
      setQueuedTemplate(null);
    } catch (cause) {
      setError((cause as Error).message);
      setMessages((list) => list.filter((item) => !item.pending));
    } finally { setBusy(false); }
  }

  function updateSettings(next: MobileSettings) {
    setSettings(next);
  }

  function selectModelProfile(id: string) {
    const profile = modelProfiles.find((item) => item.id === id);
    if (!profile) return;
    setApiKey("");
    setSettings((current) => ({ ...current, activeProfileId: id, baseUrl: profile.baseUrl, model: profile.model }));
  }

  async function createModelProfile() {
    const name = newProfileName.trim();
    if (!name) { setError("请填写新模型配置名称"); return; }
    const profile: MobileModelProfile = { id: crypto.randomUUID(), name: name.trim(), baseUrl: "", model: "", visionModel: "", createdAt: Date.now() };
    try {
      await mobileDb.putModelProfile(profile);
      setModelProfiles((list) => [...list, profile]);
      setSettings((current) => ({ ...current, activeProfileId: profile.id, baseUrl: "", model: "" }));
      setApiKey("");
      setNewProfileName("");
      setDrawer(null);
    } catch (cause) { setError((cause as Error).message); }
  }

  function updateApiKey(key: string) {
    setApiKey(key);
    if (settings.activeProfileId && isTauri()) {
      const profileId = settings.activeProfileId;
      keySaveQueue.current = keySaveQueue.current
        .catch(() => undefined)
        .then(() => invoke("save_model_key", { profileId, apiKey: key }))
        .catch((cause: Error) => setError(`保存模型密钥失败：${cause.message}`));
    }
  }

  function togglePlugin(id: string, value: boolean) {
    localStorage.setItem(`mobile_plugin_${id}`, String(value));
    setPlugins((current) => ({ ...current, [id]: value }));
  }

  async function refreshInstructions() {
    if (characterId) setInstructions(await mobileDb.instructions(characterId, conversationId));
  }

  async function saveInstruction() {
    const content = instructionDraft.trim();
    if (!characterId || !content) { setError("请先选择角色并填写指令"); return; }
    if (content.length > (editingInstructionId ? 16000 : 800)) { setError("手写指令最多 800 字"); return; }
    const targetConversation = instructionScope === "conversation" ? conversationId : null;
    if (instructionScope === "conversation" && !targetConversation) { setError("请先打开一段对话"); return; }
    try {
      if (editingInstructionId) {
        const old = instructions.find((item) => item.id === editingInstructionId);
        if (!old) throw new Error("要编辑的指令已不存在");
        await mobileDb.putInstruction({ ...old, content });
      } else {
        if (await mobileDb.instructionCount(characterId) >= 30) throw new Error("每个角色最多保存 30 条指令");
        const existing = await mobileDb.instructions(characterId, targetConversation);
        if (existing.some((item) => item.conversationId === targetConversation && item.content === content)) throw new Error("此范围已有相同指令");
        await mobileDb.putInstruction({ id: crypto.randomUUID(), characterId, conversationId: targetConversation, content, enabled: true, createdAt: Date.now() });
      }
      setInstructionDraft(""); setEditingInstructionId(null); setError("");
      await refreshInstructions();
    } catch (cause) { setError((cause as Error).message); }
  }

  async function toggleInstruction(item: MobileInstruction) {
    try { await mobileDb.putInstruction({ ...item, enabled: !item.enabled }); await refreshInstructions(); }
    catch (cause) { setError((cause as Error).message); }
  }

  async function deleteInstruction(id: string) {
    try {
      await mobileDb.deleteInstruction(id);
      if (editingInstructionId === id) { setEditingInstructionId(null); setInstructionDraft(""); }
      setConfirmDeleteId(null);
      await refreshInstructions();
    } catch (cause) { setError((cause as Error).message); }
  }

  async function applyTemplate() {
    const template = templates.find((item) => item.id === selectedTemplateId);
    if (!template || !characterId) return;
    const fields = templateFields(template.content);
    if (fields.some((field) => !templateVariables[field]?.trim())) { setError("请填写模板中的所有变量"); return; }
    const content = template.content.replace(/\{\{([^{}]+)\}\}/g, (_, field: string) => templateVariables[field.trim()]?.trim() ?? "").trim();
    if (!content || content.length > 16000) { setError("模板展开后须为 1–16000 字"); return; }
    if (templateScope === "once") { setQueuedTemplate({ name: template.name, content }); setDrawer(null); setError(""); return; }
    const targetConversation = templateScope === "conversation" ? conversationId : null;
    if (templateScope === "conversation" && !targetConversation) { setError("请先打开一段对话"); return; }
    try {
      if (await mobileDb.instructionCount(characterId) >= 30) throw new Error("每个角色最多保存 30 条指令");
      const existing = await mobileDb.instructions(characterId, targetConversation);
      if (existing.some((item) => item.conversationId === targetConversation && item.content === content)) throw new Error("此范围已有相同指令");
      await mobileDb.putInstruction({ id: crypto.randomUUID(), characterId, conversationId: targetConversation, content, enabled: true, createdAt: Date.now(), sourceTemplateName: template.name });
      setInstructionTab("saved"); setSelectedTemplateId(null); setError("");
      await refreshInstructions();
    } catch (cause) { setError((cause as Error).message); }
  }

  return <div className={`mobile-app standalone-app ${keyboardOpen ? "keyboard-open" : ""}`}>
    <header className="mobile-header"><div><span className="mobile-brand">Yu's AI</span><small>独立手机端 · 数据保存在本机</small></div><div className="mobile-header-right"><span className="mobile-current-role" title={selectedCharacter?.name ?? "未选择角色"}>{selectedCharacter?.name ?? "未选角色"}</span><span className={`mobile-status ${ready ? "online" : ""}`}>{ready ? "本地可用" : "初始化中"}</span></div></header>
    <main className="mobile-main">
      {tab === "chat" && <section className="mobile-chat">
        <div className="mobile-messages" ref={messageScrollRef} aria-live="polite">{!messages.length && <div className="mobile-empty"><span>✦</span><h2>开始一段对话</h2><p>角色与聊天记录只保存在这台设备。首次使用请先创建角色并配置模型。</p></div>}{messages.map((item) => <article key={item.id} className={`mobile-message ${item.role}`}><span className="mobile-speaker">{item.role === "user" ? "你" : selectedCharacter?.name ?? "AI"}</span><div className="mobile-bubble">{item.content ? <MessageContent content={item.content} mode={item.role === "assistant" && plugins.message_display ? displayMode : "raw"} /> : "正在回复…"}</div></article>)}</div>
        {queuedTemplate && <div className="mobile-queued-template"><span>下一条使用模板：{queuedTemplate.name}</span><button type="button" onClick={() => setQueuedTemplate(null)} aria-label="取消本轮模板">×</button></div>}
        <div className="mobile-compose"><textarea aria-label="输入消息" placeholder="给角色发消息…" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={!characterId || busy} rows={2} /><div className="mobile-compose-actions"><button type="button" className="mobile-instruction-trigger" disabled={!selectedCharacter} onClick={() => { setInstructionTab("saved"); setDrawer("instructions"); }}>指令{instructions.filter((item) => item.enabled).length > 0 && <span>{instructions.filter((item) => item.enabled).length}</span>}</button><button type="button" disabled={!draft.trim() || !characterId || busy} onClick={send}>{busy ? "回复中" : "发送"}</button></div></div>
      </section>}
      {tab === "recent" && <section className="mobile-list-panel"><div className="mobile-list-heading"><h1>最近对话</h1><button type="button" className="mobile-new" disabled={!characterId || busy} onClick={() => createConversation().catch((cause: Error) => setError(cause.message))}>＋ 新对话</button></div><p>属于当前角色：{selectedCharacter?.name ?? "未选择"}</p>{conversations.length ? conversations.map((item) => <button key={item.id} disabled={busy} className={`mobile-conversation ${item.id === conversationId ? "selected" : ""}`} onClick={() => { if (item.id !== conversationId) setMessages([]); setConversationId(item.id); setTab("chat"); }}>{item.title}<span>›</span></button>) : <div className="mobile-placeholder">还没有对话，点击上方「新对话」开始。</div>}</section>}
      {tab === "roles" && <section className="mobile-list-panel"><h1>角色</h1><p>点击下方角色切换对话，或创建新角色。</p><div className="standalone-card"><label>角色名称<input value={newCharacterName} onChange={(event) => setNewCharacterName(event.target.value)} maxLength={80} placeholder="例如：蓝雨" /></label><label>角色设定<textarea value={newCharacterPrompt} onChange={(event) => setNewCharacterPrompt(event.target.value)} rows={5} placeholder="性格、说话方式、身份与行为边界…" /></label><button className="standalone-primary" disabled={busy} onClick={createCharacter}>创建角色</button></div>{characters.map((item) => <button key={item.id} disabled={busy} className={`mobile-conversation ${item.id === characterId ? "selected" : ""}`} onClick={() => { setCharacterId(item.id); setTab("chat"); }}>{item.name}<span>{item.id === characterId ? "当前" : "选择"}</span></button>)}</section>}
      {tab === "plugins" && <section className="mobile-list-panel"><h1>手机端插件</h1><p>这里只列出明确适配状态；桌面端专有能力不会假装可用。</p>{PLUGINS.map((plugin) => <div key={plugin.id} className={`mobile-plugin ${plugin.supported ? "" : "unsupported"}`}><div><strong>{plugin.name}</strong><p>{plugin.description}</p><small>{plugin.supported ? "本机独立运行" : "待 Android 适配"}</small></div><button role="switch" aria-label={`${plugin.name}开关`} aria-checked={plugin.supported && plugins[plugin.id]} className={plugins[plugin.id] && plugin.supported ? "active" : ""} disabled={!plugin.supported} onClick={() => togglePlugin(plugin.id, !plugins[plugin.id])}>{!plugin.supported ? "待适配" : plugins[plugin.id] ? "已开启" : "已关闭"}</button>{plugin.id === "message_display" && plugins.message_display && <button type="button" className="mobile-display-mode" onClick={() => setDrawer("display")}>显示方式 <strong>{displayMode === "markdown" ? "渲染 Markdown" : displayMode === "plain" ? "过滤 Markdown" : "原始文本"}</strong><span>›</span></button>}</div>)}</section>}
      {tab === "settings" && <section className="mobile-list-panel"><h1>模型设置</h1><p>手机直接请求你选择的模型服务，不依赖电脑。模型配置保存在手机本机，密钥单独存入应用私有目录。</p><div className="standalone-card"><button type="button" className="standalone-settings-picker" onClick={() => setDrawer("models")}><span><small>当前模型配置</small><strong>{modelProfiles.find((item) => item.id === settings.activeProfileId)?.name ?? "选择模型"}</strong><small>{settings.model || "尚未设置模型 ID"}</small></span><b>⌄</b></button><label>API 基础地址<input type="url" value={settings.baseUrl} placeholder="https://…/v1" onChange={(event) => updateSettings({ ...settings, baseUrl: event.target.value })} /></label><label>模型名称<input value={settings.model} placeholder="填写服务商提供的模型 ID" onChange={(event) => updateSettings({ ...settings, model: event.target.value })} /></label><label>API Key<input type="password" autoComplete="off" value={apiKey} placeholder="保存在应用私有目录" onChange={(event) => updateApiKey(event.target.value)} /></label><div className="standalone-settings-row"><label>温度<input type="number" min="0" max="2" step="0.1" value={settings.temperature} onChange={(event) => updateSettings({ ...settings, temperature: Number(event.target.value) || 0 })} /></label><label>最大输出 Token<input type="number" min="128" max="16384" step="128" value={settings.maxTokens} onChange={(event) => updateSettings({ ...settings, maxTokens: Number(event.target.value) || 128 })} /></label></div></div><div className="standalone-note">目前仅接受 HTTPS 的 OpenAI 兼容接口。对话和角色保存在手机本机；卸载应用可能清除这些数据，备份与桌面同步尚未实现。</div></section>}
    </main>
    {error && <div className="mobile-error" role="alert">{error}<button aria-label="关闭错误提示" onClick={() => setError("")}>×</button></div>}
    {!keyboardOpen && <nav className="mobile-nav standalone-nav" aria-label="主导航"><button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>◉<span>对话</span></button><button className={tab === "recent" ? "active" : ""} onClick={() => setTab("recent")}>☷<span>最近</span></button><button className={tab === "roles" ? "active" : ""} onClick={() => setTab("roles")}>✦<span>角色</span></button><button className={tab === "plugins" ? "active" : ""} onClick={() => setTab("plugins")}>◇<span>插件</span></button><button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>⚙<span>模型</span></button></nav>}
    {drawer === "models" && <BottomSheet title="选择模型" subtitle="仅切换配置，不会改变已有对话记录" onClose={() => setDrawer(null)}>
      {modelProfiles.map((item) => <button type="button" key={item.id} className={`mobile-sheet-option ${item.id === settings.activeProfileId ? "selected" : ""}`} onClick={() => { selectModelProfile(item.id); setDrawer(null); }}><span className="mobile-sheet-avatar model">◇</span><span className="mobile-sheet-option-text"><strong>{item.name}</strong><small>{item.model || "尚未设置模型 ID"}</small></span><span className="mobile-sheet-check">{item.id === settings.activeProfileId ? "✓" : "›"}</span></button>)}
      <div className="mobile-sheet-create"><input aria-label="新模型配置名称" placeholder="新配置名称" value={newProfileName} onChange={(event) => setNewProfileName(event.target.value)} maxLength={80} /><button type="button" disabled={!newProfileName.trim()} onClick={() => void createModelProfile()}>新建</button></div>
    </BottomSheet>}
    {drawer === "display" && <BottomSheet title="消息显示方式" subtitle="只影响显示，不修改聊天原文" onClose={() => setDrawer(null)}>
      {([['markdown', '渲染 Markdown', '保留标题、列表和代码排版'], ['plain', '过滤 Markdown', '阅读时隐藏常见标记'], ['raw', '原始文本', '按模型原文显示']] as const).map(([mode, name, description]) => <button type="button" key={mode} className={`mobile-sheet-option ${displayMode === mode ? "selected" : ""}`} onClick={() => { localStorage.setItem("mobile_display_mode", mode); setDisplayMode(mode); setDrawer(null); }}><span className="mobile-sheet-option-text"><strong>{name}</strong><small>{description}</small></span><span className="mobile-sheet-check">{displayMode === mode ? "✓" : "›"}</span></button>)}
    </BottomSheet>}
    {drawer === "instructions" && <BottomSheet title="指令与模板" subtitle="已保存的指令会参与下一次模型请求" onClose={() => setDrawer(null)}>
      <div className="mobile-sheet-tabs"><button type="button" className={instructionTab === "saved" ? "active" : ""} onClick={() => setInstructionTab("saved")}>生效指令 <span>{instructions.filter((item) => item.enabled).length}</span></button><button type="button" className={instructionTab === "templates" ? "active" : ""} onClick={() => setInstructionTab("templates")}>提示词模板 <span>{templates.length}</span></button></div>
      {instructionTab === "saved" ? <>
        <div className="mobile-instruction-list">{instructions.length ? instructions.map((item) => <article key={item.id} className={`mobile-instruction-card ${item.enabled ? "" : "disabled"}`}><div className="mobile-instruction-meta"><span>{item.conversationId ? "当前对话" : "该角色通用"}{item.sourceTemplateName ? ` · ${item.sourceTemplateName}` : ""}</span><button type="button" role="switch" aria-label={`启用指令：${item.content.slice(0, 16)}`} aria-checked={item.enabled} disabled={busy} className={`mobile-mini-switch ${item.enabled ? "active" : ""}`} onClick={() => void toggleInstruction(item)}>{item.enabled ? "开启" : "关闭"}</button></div><p>{item.content}</p><div className="mobile-instruction-actions"><button type="button" disabled={busy} onClick={() => { setEditingInstructionId(item.id); setInstructionDraft(item.content); setInstructionScope(item.conversationId ? "conversation" : "character"); }}>编辑</button>{confirmDeleteId === item.id ? <><button type="button" disabled={busy} className="danger" onClick={() => void deleteInstruction(item.id)}>确认删除</button><button type="button" onClick={() => setConfirmDeleteId(null)}>取消</button></> : <button type="button" disabled={busy} onClick={() => setConfirmDeleteId(item.id)}>删除</button>}</div></article>) : <p className="mobile-sheet-empty">还没有保存指令。可添加角色通用要求，或针对当前对话单独设置。</p>}</div>
        <div className="mobile-instruction-editor"><strong>{editingInstructionId ? "编辑指令" : "添加指令"}</strong><textarea aria-label="指令内容" value={instructionDraft} maxLength={editingInstructionId ? 16000 : 800} onChange={(event) => setInstructionDraft(event.target.value)} rows={4} placeholder="例如：先给结论，再解释原因；严格遵守我指定的格式。" /><div className="mobile-scope-pills"><button type="button" className={instructionScope === "character" ? "active" : ""} disabled={Boolean(editingInstructionId)} onClick={() => setInstructionScope("character")}>角色通用</button><button type="button" className={instructionScope === "conversation" ? "active" : ""} disabled={Boolean(editingInstructionId) || !conversationId} onClick={() => setInstructionScope("conversation")}>当前对话</button></div><div className="mobile-editor-actions">{editingInstructionId && <button type="button" onClick={() => { setEditingInstructionId(null); setInstructionDraft(""); }}>取消编辑</button>}<button type="button" className="primary" disabled={!instructionDraft.trim() || busy} onClick={() => void saveInstruction()}>{editingInstructionId ? "保存修改" : "保存指令"}</button></div><small>每个角色最多 30 条。手写指令最多 800 字；本轮明确的新要求优先于旧偏好。</small></div>
      </> : <div className="mobile-template-list">{templates.length ? templates.map((item) => <button type="button" key={item.id} className={`mobile-sheet-option ${selectedTemplateId === item.id ? "selected" : ""}`} onClick={() => { setSelectedTemplateId(item.id); setTemplateVariables({}); }}><span className="mobile-sheet-option-text"><strong>{item.name}</strong><small>{item.category || "未分类"} · {templateFields(item.content).length ? `${templateFields(item.content).length} 个变量` : "无需填写变量"}</small></span><span className="mobile-sheet-check">{selectedTemplateId === item.id ? "✓" : "›"}</span></button>) : <p className="mobile-sheet-empty">手机里还没有模板。</p>}
        {templates.find((item) => item.id === selectedTemplateId) && <div className="mobile-template-editor"><strong>应用模板</strong><p>{templates.find((item) => item.id === selectedTemplateId)?.content}</p>{templateFields(templates.find((item) => item.id === selectedTemplateId)!.content).map((field) => <label key={field}>{field}<input value={templateVariables[field] ?? ""} onChange={(event) => setTemplateVariables((current) => ({ ...current, [field]: event.target.value }))} placeholder={`填写${field}`} /></label>)}<div className="mobile-scope-pills"><button type="button" className={templateScope === "once" ? "active" : ""} onClick={() => setTemplateScope("once")}>仅下一条</button><button type="button" className={templateScope === "conversation" ? "active" : ""} disabled={!conversationId} onClick={() => setTemplateScope("conversation")}>当前对话</button><button type="button" className={templateScope === "character" ? "active" : ""} onClick={() => setTemplateScope("character")}>角色通用</button></div><button type="button" className="mobile-apply-template" disabled={busy} onClick={() => void applyTemplate()}>应用模板</button></div>}
      </div>}
    </BottomSheet>}
  </div>;
}
