export type VectorPath = { segments: number[][]; closed: boolean; fill: string; stroke: string; width: number; opacity: number };
export type Anchor = 'stage' | 'mouth' | 'left_eye' | 'right_eye' | 'forehead';
export type Frame = { at: number; anchor: Anchor; x: number; y: number; angle: number; head_angle: number; opacity: number; label: string };
export type Performance = { title: string; intent: string; prop_name: string; duration_ms: number; paths: VectorPath[]; frames: Frame[] };
export const MOUTH = { x: 50 + 655 / 1355 * 320, y: 20 + 918 / 1161 * (320 * 1161 / 1355) };
const point = (x:number,y:number) => ({x:50+x/1355*320,y:20+y/1355*320});
export const ANCHORS: Record<Anchor,{x:number;y:number}> = { stage:MOUTH, mouth:MOUTH, left_eye:point(468,742), right_eye:point(884,780), forehead:point(650,450) };
export function resolveAnchor(anchor:Anchor,head:number,x=0,y=0){
  const p=ANCHORS[anchor];
  if(anchor==='stage')return {x:p.x+x,y:p.y+y};
  const a=head*Math.PI/180,dx=p.x+x-MOUTH.x,dy=p.y+y-MOUTH.y;
  return {x:MOUTH.x+dx*Math.cos(a)-dy*Math.sin(a),y:MOUTH.y+dx*Math.sin(a)+dy*Math.cos(a)};
}
const bounded = (v: unknown, min: number, max: number) => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max;
export function validatePerformance(value: unknown): Performance {
  const p = value as Performance;
  const fail = () => { throw new Error('道具数据不合格：检查路径、嘴部连接、首尾收回和参数范围'); };
  if (!p || typeof p.title !== 'string' || !p.title.length || p.title.length > 60 || typeof p.intent !== 'string' || !p.intent.length || p.intent.length > 300 || typeof p.prop_name !== 'string' || !p.prop_name.length || p.prop_name.length > 50 || !Number.isInteger(p.duration_ms) || !bounded(p.duration_ms, 3000, 12000) || !Array.isArray(p.paths) || p.paths.length < 2 || p.paths.length > 14 || !Array.isArray(p.frames) || p.frames.length < 5 || p.frames.length > 14) fail();
  for (const path of p.paths) {
    if (!path || typeof path.closed !== 'boolean' || !/^#[0-9a-f]{6}$/i.test(path.fill) || !/^#[0-9a-f]{6}$/i.test(path.stroke) || !bounded(path.width,0,3) || !bounded(path.opacity,0,1) || !Array.isArray(path.segments) || path.segments.length < 2 || path.segments.length > 24 || path.segments[0]?.length !== 2) fail();
    for (const s of path.segments) if (!Array.isArray(s) || ![2,4,6].includes(s.length) || s.some(v => !bounded(v,-80,80))) fail();
  }
  for (const [i,f] of p.frames.entries()) {
    if (!f || !bounded(f.at,0,1) || (i>0 && f.at <= p.frames[i-1].at) || !Object.hasOwn(ANCHORS,f.anchor) || !bounded(f.x,-110,130) || !bounded(f.y,-45,105) || !bounded(f.angle,-55,55) || !bounded(f.head_angle,-10,10) || !bounded(f.opacity,0,1) || typeof f.label !== 'string' || !f.label.length || f.label.length > 40 || (f.anchor === 'mouth' && (f.x !== 0 || f.y !== 0))) fail();
  }
  const first=p.frames[0], last=p.frames.at(-1)!;
  if (first.at!==0 || last.at!==1 || first.anchor!=='stage' || last.anchor!=='stage' || first.opacity!==0 || last.opacity!==0 || first.head_angle!==0 || last.head_angle!==0) fail();
  return p;
}
export function pathData(p: VectorPath) {
  return p.segments.map((s,i) => `${i===0?'M':s.length===2?'L':s.length===4?'Q':'C'}${s.join(' ')}`).join(' ') + (p.closed?' Z':'');
}
export function sample(p: Performance, t: number) {
  const index = p.frames.findIndex(f=>f.at>=t);
  const i = index < 1 ? (index < 0 ? p.frames.length-1 : 1) : index;
  const a=p.frames[i-1], b=p.frames[i];
  const u=Math.max(0,Math.min(1,(t-a.at)/(b.at-a.at))), ease=u*u*(3-2*u);
  const mix=(x:number,y:number)=>x+(y-x)*ease;
  // Resolve anchors before interpolation: attachment changes cannot teleport the prop.
  const head=mix(a.head_angle,b.head_angle);
  const pa=resolveAnchor(a.anchor,a.head_angle,a.x,a.y),pb=resolveAnchor(b.anchor,b.head_angle,b.x,b.y);
  const same=a.anchor===b.anchor;
  const position=same?resolveAnchor(a.anchor,head,mix(a.x,b.x),mix(a.y,b.y)):{x:mix(pa.x,pb.x),y:mix(pa.y,pb.y)};
  const target=resolveAnchor(same?a.anchor:b.anchor,head);
  return { ...position, target, anchor:same?a.anchor:b.anchor, angle:same?mix(a.angle,b.angle)+(a.anchor==='stage'?0:head):mix(a.angle+(a.anchor==='stage'?0:a.head_angle),b.angle+(b.anchor==='stage'?0:b.head_angle)), head, opacity:mix(a.opacity,b.opacity), label:u>=1?b.label:a.label, attached:same && a.anchor!=='stage' };
}
