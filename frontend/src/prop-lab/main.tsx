import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MOUTH, pathData, sample, validatePerformance } from './engine';
import type { Performance } from './engine';
import { DEMO } from './demo';
import '../pixel-lab/style.css';
import './style.css';
const API=import.meta.env.VITE_PIXEL_API_URL ?? 'http://127.0.0.1:8001/api/experiments/pixel-motion';
function Lab(){
  const [scene,setScene]=useState<Performance>(DEMO), [content,setContent]=useState('生成一只精致的金色放大镜，漂浮到爱丽丝左眼前，让她歪头仔细观察，然后放大镜慢慢离开。镜片透明，不要挡住眼睛。');
  const [history,setHistory]=useState<{scene:Performance;source:string}[]>([]);
  const [source,setSource]=useState('手绘校准 · 非模型生成'),[status,setStatus]=useState('正在读取模型连接…'),[model,setModel]=useState(''),[configured,setConfigured]=useState(false);
  const [t,setT]=useState(0),[playing,setPlaying]=useState(true),[busy,setBusy]=useState(false),[debug,setDebug]=useState(false),[speed,setSpeed]=useState(1),[error,setError]=useState(false);
  const controller=useRef<AbortController|null>(null),time=useRef(0);
  useEffect(()=>{const c=new AbortController(); fetch(`${API}/config`,{signal:c.signal}).then(async r=>{if(!r.ok)throw new Error();const d=await r.json();setModel(d.model);setConfigured(d.configured);setStatus(d.configured?'模型已配置，可生成新道具和轨迹':'请先配置模型');}).catch(()=>{if(!c.signal.aborted)setStatus('实验服务未连接，校准动画仍可播放');});return()=>{c.abort();controller.current?.abort();};},[]);
  useEffect(()=>{let raf=0,last=performance.now();const tick=(now:number)=>{if(playing)time.current=(time.current+Math.min(80,now-last)*speed/scene.duration_ms)%1;last=now;setT(time.current);raf=requestAnimationFrame(tick);};raf=requestAnimationFrame(tick);return()=>cancelAnimationFrame(raf);},[playing,speed,scene]);
  async function generate(){
    if(busy)return;const c=new AbortController();controller.current=c;const timeout=setTimeout(()=>c.abort(),90000);setBusy(true);setError(false);setStatus('正在创作新道具、选择连接节点与动作轨迹，不会修改角色原画…');
    try{const r=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,mode:'prop'}),signal:c.signal});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'生成失败');const next=validatePerformance(d.scene),from=`模型生成 · ${d.model}`;setScene(next);setSource(from);setHistory(h=>[{scene:next,source:from},...h].slice(0,4));time.current=0;setPlaying(true);setStatus(`生成成功 · ${d.tokens??'?'} tokens · 请检查节点连接和画风。`);}catch(e){setError(true);setStatus(c.signal.aborted?'已取消或超时，保留旧预览':e instanceof Error?e.message:'生成失败');}finally{clearTimeout(timeout);setBusy(false);}
  }
  const pose=sample(scene,t);
  return <main className="lab prop-lab"><header><div className="brand">雨<span>Yu's AI / LAB</span></div><span className="experiment">道具与动作工具 · 独立实验</span></header>
    <section className="intro"><p className="eyebrow">GENERATED PROP / ANCHOR-AWARE MOTION</p><h1>保留原画，让新道具参与表演。</h1><p>模型决定道具与动作，可连接嘴部、眼睛、额头，也可以自由漂浮。不再限定喝水。</p></section>
    <div className="workspace"><section className="preview card"><div className="section-top"><h2>爱丽丝 · 自由道具实验</h2><span className="badge">{source}</span></div>
      <div className="prop-stage"><svg viewBox="0 0 480 400" role="img" aria-label="原画爱丽丝与生成道具动画">
        <g transform={`rotate(${pose.head} ${MOUTH.x} ${MOUTH.y})`}><image href="/assets/alice-v3-head-original.png" x="50" y="20" width="320" height={320*1161/1355}/></g>
        <g transform={`translate(${pose.x} ${pose.y}) rotate(${pose.angle})`} opacity={pose.opacity}>
          {scene.paths.map((p,i)=><path key={i} d={pathData(p)} fill={p.closed?p.fill:'none'} stroke={p.stroke} strokeWidth={p.width} opacity={p.opacity} strokeLinecap="round" strokeLinejoin="round"/>)}
        </g>
        {debug&&<g><circle cx={pose.target.x} cy={pose.target.y} r="3" fill="#ec7996"/><circle cx={pose.x} cy={pose.y} r="3" fill="#75d8e0"/><line x1={pose.target.x} y1={pose.target.y} x2={pose.x} y2={pose.y} stroke="#75d8e0" strokeDasharray="3 4"/><text x="20" y="325" fill="#dceaf4" fontSize="12">{pose.anchor} · 节点距离：{Math.hypot(pose.x-pose.target.x,pose.y-pose.target.y).toFixed(1)} · {pose.attached?'跟随头部':'舞台移动'}</text></g>}
      </svg><span className="phase">{pose.label}</span></div>
      <div className="transport"><button onClick={()=>setPlaying(!playing)}>{playing?'暂停':'播放'}</button><button onClick={()=>{time.current=0;setPlaying(true);}}>重播</button><select aria-label="播放速度" value={speed} onChange={e=>setSpeed(Number(e.target.value))}><option value={.5}>0.5×</option><option value={1}>1×</option></select><label><input type="checkbox" checked={debug} onChange={e=>setDebug(e.target.checked)}/>显示连接点</label></div>
      <input className="timeline" type="range" aria-label="动画时间轴" min="0" max="1" step=".001" value={t} onChange={e=>{setPlaying(false);time.current=Number(e.target.value);setT(time.current);}}/>
      <div className="scene-caption"><h3>{scene.title}</h3><p>{scene.intent}</p><div className="metrics"><span>{scene.paths.length} 个新道具图层</span><span>{scene.frames.length} 个轨迹节点</span><span>{scene.duration_ms/1000} 秒</span></div></div></section>
      <section className="director card"><div className="section-top"><h2>对话 → 新道具与轨迹</h2><span className="badge muted">{model||'未连接'}</span></div><label className="field">你希望它做什么<textarea disabled={busy} maxLength={2000} value={content} onChange={e=>setContent(e.target.value)}/></label><button className="primary" disabled={busy||!configured||!content.trim()} onClick={generate}>{busy?'正在创作…':'生成道具与动作'}</button>{busy&&<button className="cancel" onClick={()=>controller.current?.abort()}>取消等待</button>}<p role="status" className={error?'status error':'status'}>{status}</p>
      <div className="examples"><h3>试试其他内容</h3><p>这些只是输入建议，没有对应的预置道具或动作。</p>{[
        ['放大镜观察','生成一只精致的金色放大镜，漂浮到爱丽丝左眼前，让她歪头仔细观察，然后放大镜慢慢离开。镜片透明，不要挡住眼睛。'],
        ['饼干品尝','生成一块可爱的巧克力豆饼干，漂浮到爱丽丝嘴边停留，让她歪头品尝一下，然后饼干移开。不要声称饼干被咬掉，因为当前不能改变道具形状。'],
        ['额头小王冠','为爱丽丝生成一个小巧的蓝金王冠，缓缓落到额头上，跟着她歪头展示，再飘走。'],
      ].map(([label,prompt])=><button key={label} disabled={busy} onClick={()=>setContent(prompt)}>{label} · 填入提示词 ↗</button>)}
      <p>角色只有头部，不模拟手持。当前支持一个新道具的移动、旋转、透明度及节点跟随；暂不支持吃掉缺口或真实光学放大。</p>
      {history.length>0&&<><h3>本次已生成 · 可直接重播</h3>{history.map((h,i)=><button key={i} disabled={busy} onClick={()=>{setScene(h.scene);setSource(h.source);time.current=0;setPlaying(true);setError(false);setStatus('重播已生成结果，不消耗token');}}>{h.scene.title} ↗</button>)}</>}
      <button disabled={busy} onClick={()=>{setScene(DEMO);setSource('手绘校准 · 非模型生成');time.current=0;setPlaying(true);setStatus('已切换手绘校准');}}>查看手绘喝水校准 ↗</button></div><p className="note">当前不会接入正式聊天、主动发言或覆盖原桌宠；生成使用你的模型 token，仅发送输入文本。重播列表仅在当前页面内存中保存，刷新会清空。</p>
      </section></div><details className="data card"><summary>查看模型道具与轨迹 JSON</summary><pre>{JSON.stringify(scene,null,2)}</pre></details></main>;
}
createRoot(document.getElementById('root')!).render(<Lab/>);
