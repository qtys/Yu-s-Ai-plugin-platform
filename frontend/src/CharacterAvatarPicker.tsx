import { useRef, useState } from "react";
import { WorkspaceIcon } from "./WorkspaceNavigation";

export default function CharacterAvatarPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function load(file?: File) {
    if (!file) return;
    setError("");
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 10 * 1024 * 1024) {
      setError("请选择 10 MB 以内的 PNG、JPEG 或 WebP 图片"); return;
    }
    setLoading(true);
    try {
      const bitmap = await createImageBitmap(file);
      try {
        const canvas = document.createElement("canvas");
        const scale = Math.min(1, 512 / Math.max(bitmap.width, bitmap.height));
        canvas.width = Math.max(1, Math.round(bitmap.width * scale)); canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        const context = canvas.getContext("2d");
        if (!context) throw new Error("无法处理头像图片");
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        onChange(canvas.toDataURL("image/webp", 0.9));
      } finally { bitmap.close(); }
    } catch { setError("无法读取图片，请更换有效的图片文件"); }
    finally { setLoading(false); }
  }
  return <div className="character-avatar-field">
    <span>角色头像</span>
    <input ref={input} type="file" hidden accept="image/png,image/jpeg,image/webp" aria-label="上传角色头像" onChange={(event) => { void load(event.target.files?.[0]); event.target.value = ""; }} />
    <div className="character-avatar-picker">
      <button type="button" className="avatar-preview" onClick={() => input.current?.click()} disabled={loading} aria-label="选择角色头像">{value ? <img src={value} alt="角色头像预览" /> : <WorkspaceIcon name="role" />}</button>
      <div><strong>{loading ? "正在处理头像…" : value ? "头像已选择" : "给角色一个独特的面孔"}</strong><small>PNG / JPEG / WebP · 仅保存在本地</small><div className="avatar-picker-actions"><button type="button" onClick={() => input.current?.click()} disabled={loading}>{value ? "更换图片" : "选择图片"}</button>{value && <button type="button" onClick={() => onChange("")} disabled={loading}>移除</button>}</div></div>
    </div>
    {error && <small role="alert">{error}</small>}
  </div>;
}
