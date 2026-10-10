import { useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
type Config = {
  natural_style: boolean; character_motivation: boolean; continuity: boolean;
  detail_level: "light" | "balanced" | "rich"; hard_bans_enabled: boolean;
  banned_terms: string; ban_contrast_template: boolean;
};

export default function NovelSettingsPanel() {
  const [config, setConfig] = useState<Config | null>(null);
  const [status, setStatus] = useState("正在读取…");
  const pending = useRef<Partial<Config>>({});
  const timer = useRef(0);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const live = useRef(true);
  async function checked(response: Response) {
    if (!response.ok) throw new Error("请求失败，请检查本地服务或禁词长度");
    return response.json();
  }
  function flush() {
    const patch = pending.current;
    pending.current = {};
    if (!Object.keys(patch).length) return;
    queue.current = queue.current.then(async () => {
      try {
        await checked(await fetch(`${API}/plugins/novel_reply/settings`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
        }));
        if (live.current) setStatus(Object.keys(pending.current).length ? "等待自动保存…" : "已自动保存");
      } catch (error) {
        pending.current = { ...patch, ...pending.current };
        if (live.current) setStatus(`保存失败：${(error as Error).message}`);
      }
    });
  }
  useEffect(() => {
    live.current = true;
    void fetch(`${API}/plugins/novel_reply/settings`).then(checked).then((value: Config) => {
      if (live.current) { setConfig(value); setStatus("修改后自动保存"); }
    }).catch(() => { if (live.current) setStatus("读取失败，请重新打开设置"); });
    return () => { live.current = false; window.clearTimeout(timer.current); flush(); };
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- pending changes are flushed on close
  }, []);
  function change(patch: Partial<Config>) {
    setConfig((value) => value ? { ...value, ...patch } : value);
    pending.current = { ...pending.current, ...patch };
    setStatus("等待自动保存…");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, 650);
  }
  return <div className="speech-settings novel-settings">
    <small>第三人称小说笔法。仅发送启用的精简规则；当前消息与有效对话指令优先，桌宠仍保持简短气泡。</small>
    {config && <>
      <label>自然文风<input type="checkbox" checked={config.natural_style} onChange={(e) => change({ natural_style: e.target.checked })} /></label>
      <label>依照人物动机行动<input type="checkbox" checked={config.character_motivation} onChange={(e) => change({ character_motivation: e.target.checked })} /></label>
      <label>保持情节与情绪连贯<input type="checkbox" checked={config.continuity} onChange={(e) => change({ continuity: e.target.checked })} /></label>
      <label>描写强度<select value={config.detail_level} onChange={(e) => change({ detail_level: e.target.value as Config["detail_level"] })}>
        <option value="light">轻量 · 对白与关键动作</option><option value="balanced">适中 · 神态与氛围</option><option value="rich">丰富 · 关键场景细写</option>
      </select></label>
      <details className="plugin-options"><summary>硬性禁词与句式<span aria-hidden="true">⌄</span></summary><div className="plugin-options-body">
        <label>启用禁词检查<input type="checkbox" checked={config.hard_bans_enabled} onChange={(e) => change({ hard_bans_enabled: e.target.checked })} /></label>
        <label>禁用「不是…而是… / 并非…而是…」句式<input type="checkbox" disabled={!config.hard_bans_enabled} checked={config.ban_contrast_template} onChange={(e) => change({ ban_contrast_template: e.target.checked })} /></label>
        <label>禁词 / 短语（每行一条）<textarea rows={8} maxLength={4000} value={config.banned_terms} onChange={(e) => change({ banned_terms: e.target.value })} placeholder="一行一个词或短语；清空表示不限制字面词汇" /></label>
        <small>最多 100 条，每条 80 字。原创对白也检查；用户原文引用、代码与网址保留。禁词按字面匹配，不支持正则。</small>
        <small>开启后按完整句子显示。命中才额外调用一次改写（可能计费，最多等待 30 秒）；仍未通过则不展示、不保存违规回复，不会生硬删词。关闭恢复实时流式输出。</small>
      </div></details>
      <small>连贯性仅依据已上传上下文，不会额外建立永久记忆。未引入原文的长篇默认字数、删除英文或解除服务商限制的声明。</small>
    </>}
    <small role="status">{status}</small>
    {status.startsWith("保存失败") && <button type="button" onClick={flush}>重试保存</button>}
  </div>;
}
