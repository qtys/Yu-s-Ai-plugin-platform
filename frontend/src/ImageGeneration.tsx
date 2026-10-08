import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
type Config = { model_profile_id: number | null; base_url: string; api_key: string; model: string; size: string; quality: string; response_format: string; proxy_mode: string };
type Profile = { id: number; name: string; model: string };
type GeneratedImage = { id: string; prompt: string; model: string; image_url: string; created_at: string; conversation_id?: number | null; message_id?: number | null };
export type ImageSource = { id: number; content: string; excerpt?: string };
const imageUrl = (image: GeneratedImage) => `${API.replace(/\/api\/?$/, "")}${image.image_url}`;
async function saveImage(imageId: string): Promise<string> {
  if ("__TAURI_INTERNALS__" in window) {
    const destination = await save({ defaultPath: `yus-ai-${imageId}.png`, filters: [{ name: "PNG 图片", extensions: ["png"] }] });
    if (!destination) return "";
    await invoke("export_generated_image", { imageId, destination });
    return "图片已保存到选择的位置";
  }
  const response = await fetch(`${API}/images/${imageId}/file`);
  if (!response.ok) throw new Error("图片文件读取失败");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a"); link.href = url; link.download = `yus-ai-${imageId}.png`; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return "已开始下载图片";
}

export function GeneratedImageMessage({ imageId }: { imageId: string }) {
  const [status, setStatus] = useState("");
  const [failed, setFailed] = useState(false);
  return <figure className="message-generated-image">
    {failed ? <p>图片文件暂时无法读取，请检查本地服务或数据文件。</p> : <a href={`${API}/images/${imageId}/file`} target="_blank" rel="noreferrer" title="查看原图"><img src={`${API}/images/${imageId}/file`} alt="生成的图片" loading="lazy" onError={() => setFailed(true)} /></a>}
    <figcaption><span>AI 生成图片</span><button type="button" onClick={() => { void saveImage(imageId).then(setStatus).catch((error: Error) => setStatus(error.message)); }}>保存图片</button></figcaption>
    {status && <small role="status">{status}</small>}
  </figure>;
}
async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, { method, headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（HTTP ${response.status}）`);
  return data as T;
}

export function ImageSettingsPanel() {
  const [config, setConfig] = useState<Config | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [status, setStatus] = useState("正在读取绘图设置…");
  const pending = useRef<Partial<Config>>({});
  const queue = useRef(Promise.resolve());
  const timer = useRef(0);
  const live = useRef(true);
  function flush() {
    const patch = pending.current;
    pending.current = {};
    if (!Object.keys(patch).length) return;
    queue.current = queue.current.then(async () => {
      try {
        await request<Config>("/images/settings", "PATCH", patch);
        if (live.current) setStatus(Object.keys(pending.current).length ? "等待自动保存…" : "已自动保存");
      } catch (problem) {
        pending.current = { ...patch, ...pending.current };
        if (live.current) setStatus(`保存失败：${(problem as Error).message}；修改字段可重试`);
      }
    });
  }
  useEffect(() => {
    live.current = true;
    void Promise.all([request<Config>("/images/settings"), request<Profile[]>("/model-profiles")]).then(([value, items]) => {
      if (live.current) { setConfig(value); setProfiles(items); setStatus("修改后自动保存"); }
    }).catch((problem: Error) => { if (live.current) setStatus(problem.message); });
    return () => { live.current = false; window.clearTimeout(timer.current); flush(); };
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- flush reads only stable refs and flushes unsaved changes on close
  }, []);
  function change(patch: Partial<Config>) {
    setConfig((value) => value ? { ...value, ...patch } : value);
    pending.current = { ...pending.current, ...patch };
    setStatus("等待自动保存…");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, 650);
  }
  return <div className="image-settings">
    <p>支持 OpenAI 兼容的 images/generations 接口。不自动读取聊天或角色卡；每次手动生成一张，可能产生费用。</p>
    {config && <>
      <label>模型连接<select value={config.model_profile_id ?? ""} onChange={(event) => change({ model_profile_id: event.target.value ? Number(event.target.value) : null })}><option value="">独立绘图连接</option>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · 复用地址与密钥</option>)}</select></label>
      {!config.model_profile_id && <>
        <label>API 基础地址<input type="url" placeholder="https://服务地址/v1" value={config.base_url} onChange={(event) => change({ base_url: event.target.value })} /></label>
        <label>API 密钥<input type="password" autoComplete="off" value={config.api_key} onFocus={(event) => { if (config.api_key === "••••••••") event.currentTarget.select(); }} onChange={(event) => change({ api_key: event.target.value })} /></label>
      </>}
      <label>绘图模型名称<input placeholder="填写服务商的绘图模型 ID，而非聊天模型" value={config.model} onChange={(event) => change({ model: event.target.value })} /></label>
      <small>复用连接不代表聊天模型能画图，仍需填入该服务支持的绘图模型名称。完整 images/generations 地址也可粘贴。</small>
      <div className="image-options">
        <label>默认尺寸<select value={config.size} onChange={(event) => change({ size: event.target.value })}>{["auto", "1024x1024", "1536x1024", "1024x1536", "1792x1024", "1024x1792", "512x512"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>质量<select value={config.quality} onChange={(event) => change({ quality: event.target.value })}>{["default", "auto", "low", "medium", "high", "standard", "hd"].map((value) => <option key={value} value={value}>{value === "default" ? "服务默认（不传参数）" : value}</option>)}</select></label>
        <label>返回格式<select value={config.response_format} onChange={(event) => change({ response_format: event.target.value })}><option value="auto">服务默认（推荐）</option><option value="b64_json">Base64 图片</option><option value="url">图片链接</option></select></label>
        <label>网络连接<select value={config.proxy_mode} onChange={(event) => change({ proxy_mode: event.target.value })}><option value="auto">自动使用系统代理</option><option value="direct">直连</option></select></label>
      </div>
      <small>不同模型支持的尺寸、质量不同；GPT Image 通常使用默认返回格式，DALL·E 可选 Base64。失败不会自动重试，避免重复扣费。</small>
    </>}
    <small role="status">{status}</small>
  </div>;
}

export function ImageGenerationDialog({ open, onClose, enabled, onSettings, source, initialPrompt, conversationId, ensureConversation, onGenerated }: { open: boolean; onClose: () => void; enabled: boolean; onSettings: () => void; source: ImageSource | null; initialPrompt: string; conversationId: number | null; ensureConversation: () => Promise<number | null | undefined>; onGenerated: (conversationId: number) => void }) {
  const [prompt, setPrompt] = useState("");
  const [extraInstructions, setExtraInstructions] = useState("");
  const [images, setImages] = useState<GeneratedImage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saveStatus, setSaveStatus] = useState("");
  const running = useRef(false);
  const mounted = useRef(true);
  const focusRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (open && !running.current) { setPrompt(initialPrompt); setExtraInstructions(""); setError(""); }
  }, [open, initialPrompt, source]);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void request<GeneratedImage[]>("/images").then((items) => { if (!cancelled && !running.current) setImages(items); }).catch((problem: Error) => { if (!cancelled) setError(problem.message); });
    focusRef.current?.focus();
    function escape(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    document.addEventListener("keydown", escape);
    return () => { cancelled = true; document.removeEventListener("keydown", escape); };
  }, [open, onClose]);
  async function generate() {
    if (running.current || (!source && !prompt.trim()) || !enabled) return;
    running.current = true; setBusy(true); setError(""); setSaveStatus("");
    try {
      const targetConversation = conversationId ?? await ensureConversation();
      if (!targetConversation) throw new Error("请先选择角色并创建对话");
      const result = await request<GeneratedImage>("/images/generate", "POST", { prompt: prompt.trim(), conversation_id: targetConversation, source_message_id: source?.id, source_excerpt: source?.excerpt, extra_instructions: extraInstructions.trim() });
      if (mounted.current) setImages((items) => [result, ...items].slice(0, 24));
      if (result.conversation_id) { onGenerated(result.conversation_id); if (mounted.current) onClose(); }
      else if (mounted.current) setSaveStatus("原对话已删除，图片仍保留在作品库中，未添加到其他对话。");
    } catch (problem) { if (mounted.current) setError((problem as Error).message); }
    finally { running.current = false; if (mounted.current) setBusy(false); }
  }
  async function download(image: GeneratedImage) {
    try {
      setSaveStatus(await saveImage(image.id));
    } catch (problem) { setError(String(problem)); }
  }
  if (!open) return null;
  return <div className="plugin-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="plugin-modal image-studio" role="dialog" aria-modal="true" aria-label="图片生成">
      <header className="plugin-modal-header"><div><small>IMAGE STUDIO</small><h2>{source ? "根据这段内容绘图" : "把想象画出来"}</h2><p>仅提交指定内容与额外指令，生成图片会保存到当前对话。</p></div><button type="button" className="plugin-modal-close" aria-label="关闭图片生成" onClick={onClose}>×</button></header>
      <div className="plugin-modal-content">
        <label className="image-prompt">{source ? "指定的对话内容" : "图片描述"}<textarea ref={focusRef} rows={4} maxLength={12000} value={source ? source.excerpt ?? source.content : prompt} readOnly={Boolean(source)} onChange={(event) => setPrompt(event.target.value)} placeholder="例如：一只蓝色雨滴精灵坐在窗边，柔和晨光，水彩插画…" disabled={busy} /></label>
        <label className="image-prompt">额外绘图指令（可选）<textarea rows={2} maxLength={4000} value={extraInstructions} onChange={(event) => setExtraInstructions(event.target.value)} placeholder="例如：日系插画、横向构图、突出人物神态，不要文字…" disabled={busy} /></label>
        <div className="image-studio-actions"><button type="button" onClick={onSettings}>绘图设置</button><button type="button" className="primary" disabled={!enabled || busy || (!source && !prompt.trim())} onClick={() => void generate()}>{busy ? "正在生成…" : "生成并加入对话"}</button></div>
        {!enabled && <small>请先在插件页面启用“图片生成”。</small>}
        {busy && <small role="status">正在等待绘图服务，可能需要几分钟。关闭窗口不会取消请求；请勿重复生成，以免重复收费。</small>}
        {error && <p className="image-error" role="alert">{error}</p>}
        {saveStatus && <small role="status">{saveStatus}</small>}
        {images.length ? <div className="image-gallery">{images.map((image) => <article key={image.id}><img src={imageUrl(image)} alt={image.prompt} loading="lazy" /><div><p>{image.prompt}</p><small>{image.model} · {image.created_at}</small><button type="button" onClick={() => void download(image)}>保存图片</button></div></article>)}</div> : <div className="image-empty">你的第一幅作品，从一句描述开始。</div>}
      </div>
      <footer className="plugin-modal-footer"><small>每次生成可能收费 · 不自动重试</small><button type="button" onClick={onClose}>完成</button></footer>
    </section>
  </div>;
}
