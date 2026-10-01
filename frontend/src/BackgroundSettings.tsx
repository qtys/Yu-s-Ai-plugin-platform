import { useRef, useState } from "react";

export type WorkspaceBackground = { image: string; name: string; strength: number };
const storageKey = "yus-ai-workspace-background";
export function readWorkspaceBackground(): WorkspaceBackground | null {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) ?? "null");
    if (!value || !/^data:image\/(jpeg|png|webp);base64,/.test(value.image) || typeof value.name !== "string") return null;
    return { image: value.image, name: value.name, strength: Math.min(0.7, Math.max(0.1, Number(value.strength) || 0.2)) };
  } catch { return null; }
}

async function prepareImage(file: File): Promise<string> {
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("请选择 PNG、JPG 或 WebP 图片。");
  if (file.size > 15 * 1024 * 1024) throw new Error("图片不能超过 15 MB。");
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const scale = Math.min(1, 1600 / Math.max(image.width, image.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法处理图片，请重试。");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.82);
  } finally { URL.revokeObjectURL(url); }
}

export default function BackgroundSettings({ value, onChange }: { value: WorkspaceBackground | null; onChange: (value: WorkspaceBackground | null) => void }) {
  const picker = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function persist(next: WorkspaceBackground | null) {
    try {
      if (next) localStorage.setItem(storageKey, JSON.stringify(next));
      else localStorage.removeItem(storageKey);
      onChange(next);
      setError("");
    } catch { setError("背景保存失败，本地存储空间可能不足。请使用更小的图片。"); }
  }
  async function select(file: File) {
    setBusy(true); setError("");
    try { persist({ image: await prepareImage(file), name: file.name, strength: value?.strength ?? 0.2 }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "图片读取失败。"); }
    finally { setBusy(false); }
  }
  return <div className="background-settings">
    <div className="form-section-title"><strong>自定义背景</strong><small>铺满整个内容窗口，随窗口尺寸缩放，保持比例并居中裁切。仅保存在本机。</small></div>
    <input ref={picker} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void select(file); }} />
    <div className="background-actions">
      {value && <img className="background-thumbnail" src={value.image} alt="当前背景预览" />}
      <div><strong>{value?.name ?? "未设置背景图片"}</strong><small>PNG / JPG / WebP，最大 15 MB</small></div>
      <button type="button" disabled={busy} onClick={() => picker.current?.click()}>{busy ? "处理图片中…" : value ? "更换图片" : "选择图片"}</button>
      {value && <button type="button" disabled={busy} onClick={() => persist(null)}>清除背景</button>}
    </div>
    {value && <label className="background-strength">背景可见度 <span>{Math.round(value.strength * 100)}%</span><input type="range" min="10" max="70" value={Math.round(value.strength * 100)} onChange={(event) => persist({ ...value, strength: Number(event.target.value) / 100 })} /><small>提高可见度让图片更突出；对话内容仍保留阅读底色。</small></label>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
