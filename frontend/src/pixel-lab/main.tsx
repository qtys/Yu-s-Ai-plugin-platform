import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { DEMOS } from './demos';
import { drawScene, validateScene } from './scene';
import type { Scene } from './scene';
import './style.css';

const API = import.meta.env.VITE_PIXEL_API_URL ?? 'http://127.0.0.1:8001/api/experiments/pixel-motion';
type Config = { model: string; configured: boolean; characters: { id: number; name: string }[] };

function PixelLab() {
  const [scene, setScene] = useState<Scene>(() => validateScene(DEMOS[0].scene));
  const [prompt, setPrompt] = useState(DEMOS[0].prompt);
  const [source, setSource] = useState('手绘测试 · 非模型生成');
  const [config, setConfig] = useState<Config | null>(null);
  const [character, setCharacter] = useState('');
  const [status, setStatus] = useState('正在连接实验服务…');
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(true);
  const [loop, setLoop] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [zoom, setZoom] = useState(4);
  const [timeline, setTimeline] = useState(0);
  const [editor, setEditor] = useState(JSON.stringify(scene, null, 2));
  const canvas = useRef<HTMLCanvasElement>(null);
  const time = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const requestSequence = useRef(0);

  useEffect(() => {
    const abort = new AbortController();
    fetch(`${API}/config`, { signal: abort.signal }).then(async r => {
      if (!r.ok) throw new Error();
      const c = await r.json() as Config;
      setConfig(c); setStatus(c.configured ? '模型已连接，可生成新表演' : '请先在主界面配置模型连接');
    }).catch(() => { if (!abort.signal.aborted) setStatus('实验服务未连接。手绘场景仍可预览；启动 8001 端口的实验后端后刷新。'); });
    return () => { abort.abort(); controller.current?.abort(); };
  }, []);

  useEffect(() => {
    let raf = 0, previous = performance.now(), lastUpdate = 0;
    const tick = (now: number) => {
      const elapsed = Math.min(100, now - previous); previous = now;
      if (playing) {
        time.current += elapsed * speed / scene.duration_ms;
        if (time.current >= 1) { if (loop) time.current %= 1; else { time.current = 1; setPlaying(false); } }
      }
      const ctx = canvas.current?.getContext('2d');
      if (ctx) drawScene(ctx, scene, time.current);
      if (now - lastUpdate > 100) { setTimeline(time.current); lastUpdate = now; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick); return () => cancelAnimationFrame(raf);
  }, [scene, playing, loop, speed]);

  function load(next: Scene, from: string) {
    setScene(validateScene(next)); time.current = 0; setTimeline(0); setSource(from); setPlaying(true); setEditor(JSON.stringify(next, null, 2));
  }
  function demo(index: number) {
    const d = DEMOS[index]; setPrompt(d.prompt); load(d.scene, '手绘测试 · 非模型生成');
  }
  async function generate() {
    if (!prompt.trim() || busy) return;
    const seq = ++requestSequence.current;
    const abort = new AbortController(); controller.current = abort;
    const timeout = window.setTimeout(() => abort.abort(), 90_000);
    setBusy(true); setStatus('模型正在创作扇叶伸展、弯曲与表情节奏，基础身体保持完整…');
    try {
      const r = await fetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: prompt.trim(), character_id: character ? Number(character) : null, mode: 'soft' }), signal: abort.signal });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `生成失败（${r.status}）`);
      if (seq !== requestSequence.current) return;
      load(data.scene, `模型生成 · ${data.model}`);
      setStatus(`已生成新的表演数据${data.tokens ? ` · ${data.tokens} tokens` : ''}。请检查是否真正回应了这句话。`);
    } catch (error) {
      if (seq === requestSequence.current) setStatus(error instanceof Error && error.name !== 'AbortError' ? error.message : '已取消或等待超时；当前预览保留。');
    } finally { window.clearTimeout(timeout); if (seq === requestSequence.current) setBusy(false); }
  }
  function exportScene() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(scene, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = 'yus-ai-pixel-scene.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <main className="lab">
    <header><div className="brand">雨<span>Yu's AI / LAB</span></div><span className="experiment">实验功能 · 独立预览</span></header>
    <section className="intro"><p className="eyebrow">SOFT PIXEL PERFORMANCE / 02</p><h1>先保持可爱，再长出动作。</h1><p>圆润身体与五官保持完整；模型创作蓝色扇叶的伸展、弯曲与情绪节奏。</p></section>
    <div className="workspace">
      <section className="preview card"><div className="section-top"><h2>表演舞台</h2><span className="badge">{source}</span></div>
        <div className="stage"><div className="stage-grid"/><canvas ref={canvas} width={96} height={96} style={{ width: zoom * 96, height: zoom * 96 }} aria-label="生成式像素史莱姆动画"/><span className="stage-label">96 × 96 · 透明像素画布</span></div>
        <div className="transport"><button onClick={() => setPlaying(v => !v)}>{playing ? '暂停' : '播放'}</button><button onClick={() => { time.current = 0; setPlaying(true); }}>重播</button><label><input type="checkbox" checked={loop} onChange={e => setLoop(e.target.checked)}/>循环</label><select aria-label="播放速度" value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value={.5}>0.5× 观察细节</option><option value={1}>1×</option><option value={1.5}>1.5×</option></select><select aria-label="像素放大倍数" value={zoom} onChange={e => setZoom(Number(e.target.value))}><option value={3}>3×</option><option value={4}>4×</option><option value={5}>5×</option></select></div>
        <input className="timeline" aria-label="动画时间轴" type="range" min={0} max={1} step={.001} value={timeline} onChange={e => { setPlaying(false); time.current = Number(e.target.value); setTimeline(time.current); }}/>
        <div className="scene-caption"><h3>{scene.title}</h3><p>{scene.intent}</p><div className="metrics">{scene.style === 'soft' ? <><span>完整基础造型</span><span>{scene.fan!.length} 个扇叶关键姿态</span><span>柔软连接 · 收回融合</span></> : <><span>{scene.body.frames[0].points.length} 个轮廓顶点</span><span>{scene.body.frames.length} 个身体关键姿态</span><span>{scene.props.length} 个自由绘制图层</span></>}<span>{(scene.duration_ms / 1000).toFixed(1)} 秒</span></div></div>
      </section>
      <section className="director card"><div className="section-top"><h2>对话 → 表演</h2><span className="badge muted">{config?.model ?? '本地预览'}</span></div>
        <label className="field">角色人设<select value={character} onChange={e => setCharacter(e.target.value)} disabled={busy}><option value="">仅蓝雨史莱姆外观，不指定人设</option>{config?.characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label className="field">你对它说<textarea value={prompt} maxLength={2000} disabled={busy} onChange={e => setPrompt(e.target.value)} placeholder="例如：我终于解决了那个困扰我很久的问题。"/></label>
        <button className="primary" disabled={busy || !config?.configured || !prompt.trim()} onClick={generate}>{busy ? '正在创作表演…' : '让模型创作新表演'}</button>
        {busy && <button className="cancel" onClick={() => controller.current?.abort()}>取消等待</button>}
        <p className={`status ${busy ? 'working' : ''}`} role="status">{status}</p><p className="note">生成会使用当前模型的 token；只发送这句话和所选角色设定，不写入聊天记录。</p>
        <div className="examples"><h3>造型校准与旧版对照</h3><p>01 是新版美术校准；其余保留作旧版对照。均为手绘测试数据。</p>{DEMOS.map((d, i) => <button key={d.prompt} disabled={busy} onClick={() => demo(i)}><span>0{i + 1}</span>{d.prompt}<b>↗</b></button>)}</div>
      </section>
    </div>
    <details className="data card"><summary>查看 / 修改动画数据 <span>JSON · 不执行模型代码</span></summary><textarea aria-label="动画 JSON" spellCheck={false} value={editor} onChange={e => setEditor(e.target.value)}/><div><button disabled={busy} onClick={() => { try { load(validateScene(JSON.parse(editor)), '手动数据 · 已验证'); setStatus('数据已载入。'); } catch (e) { setStatus(e instanceof Error ? e.message : '数据格式无效'); } }}>验证并播放</button><button onClick={exportScene}>导出当前动画</button></div><p className="note">椭圆、多边形和线条是绘图原语；形状、顶点、轨迹和道具关系由数据决定。当前版本尚未自动判断动作是否符合语义、轮廓是否穿插。</p></details>
    <footer>蓝雨像素实验 · 当前桌宠保持原有模式 · 实验成功后再整合</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<PixelLab/>);
