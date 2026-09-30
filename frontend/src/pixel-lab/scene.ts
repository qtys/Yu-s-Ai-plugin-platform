export type Point = { x: number; y: number };
export type ShapeFrame = { at: number; x: number; y: number; angle: number; opacity: number; points: Point[] };
export type Shape = { id: string; label: string; kind: 'polygon' | 'ellipse' | 'line'; anchor: 'body' | 'world'; layer: 'behind' | 'front'; color: number; stroke: number; width: number; frames: ShapeFrame[] };
export type FaceFrame = { at: number; x: number; y: number; eye_open_left: number; eye_open_right: number; gaze_x: number; gaze_y: number; mouth_width: number; mouth_open: number; smile: number; blush: number };
export type FanFrame = { at: number; extension: number; spread: number; bend: number; sway: number };
export type Scene = { version: 1; title: string; intent: string; duration_ms: number; body: Shape; face: FaceFrame[]; props: Shape[]; style?: 'soft'; fan?: FanFrame[] };
export const PALETTE = ['#102d50', '#2379b7', '#53bbf2', '#a9e9ff', '#f6fcff', '#f49db3', '#f8da73', '#bba7ef', '#80dcb3', '#ed9964', '#844968', '#20364b'];

// JSON import and model results share the same bounded validation boundary.
export function validateScene(input: unknown): Scene {
  const s = input as Scene;
  const fail = () => { throw new Error('动画格式无效：请检查轨迹时间、轮廓顶点和数值范围'); };
  const number = (n: unknown, min: number, max: number) => typeof n === 'number' && Number.isFinite(n) && n >= min && n <= max;
  const track = (f: { at: number }[] | undefined, max: number) => {
    if (!Array.isArray(f) || f.length < 2 || f.length > max || f[0]?.at !== 0 || f.at(-1)?.at !== 1 || f.some((v, i) => !v || !number(v.at, 0, 1) || (i > 0 && v.at <= f[i - 1].at))) fail();
  };
  const shape = (p: Shape) => {
    if (!p || typeof p.id !== 'string' || !/^[a-zA-Z0-9_-]{1,32}$/.test(p.id) || typeof p.label !== 'string' || p.label.length > 50 || !['polygon', 'ellipse', 'line'].includes(p.kind) || !['body', 'world'].includes(p.anchor) || !['behind', 'front'].includes(p.layer) || !Number.isInteger(p.color) || !number(p.color, 0, 11) || !Number.isInteger(p.stroke) || !number(p.stroke, 0, 11) || !number(p.width, 1, 4)) fail();
    track(p.frames, 10);
    const count = p.frames[0].points?.length;
    if (!number(count, p.kind === 'polygon' ? 3 : 2, 32) || (p.kind === 'ellipse' && count !== 2)) fail();
    for (const f of p.frames) {
      if (!number(f.x, -96, 192) || !number(f.y, -96, 192) || !number(f.angle, -720, 720) || !number(f.opacity, 0, 1) || !Array.isArray(f.points) || f.points.length !== count || f.points.some(v => !v || !number(v.x, -96, 96) || !number(v.y, -96, 96))) fail();
    }
  };
  if (!s || s.version !== 1 || typeof s.title !== 'string' || !s.title.length || s.title.length > 60 || typeof s.intent !== 'string' || !s.intent.length || s.intent.length > 300 || !Number.isInteger(s.duration_ms) || !number(s.duration_ms, 1500, 12000) || !Array.isArray(s.props) || s.props.length > 12) fail();
  shape(s.body);
  if (s.body.kind !== 'polygon' || s.body.anchor !== 'world' || s.body.frames[0].points.length < 8) fail();
  s.props.forEach(shape);
  const ids = [s.body.id, ...s.props.map(p => p.id)];
  if (new Set(ids).size !== ids.length) fail();
  track(s.face, 12);
  if (s.style !== undefined && s.style !== 'soft') fail();
  if (s.style === 'soft') {
    track(s.fan, 10);
    for (const f of s.fan!) if (!number(f.extension, 0, 1) || !number(f.spread, .3, 1.2) || !number(f.bend, -12, 12) || !number(f.sway, -24, 24)) fail();
    if (s.fan![0].extension !== 0 || s.fan!.at(-1)!.extension !== 0) fail();
  }
  for (const f of s.face) {
    if (!number(f.x, -18, 18) || !number(f.y, -18, 18) || !number(f.eye_open_left, 0, 1) || !number(f.eye_open_right, 0, 1) || !number(f.gaze_x, -1, 1) || !number(f.gaze_y, -1, 1) || !number(f.mouth_width, 1, 12) || !number(f.mouth_open, 0, 8) || !number(f.smile, -1, 1) || !number(f.blush, 0, 1)) fail();
  }
  return s;
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
function segment<T extends { at: number }>(frames: T[], t: number): [T, T, number] {
  const index = frames.findIndex(f => f.at >= t);
  const i = index < 1 ? (index < 0 ? frames.length - 1 : 1) : index;
  const a = frames[i - 1], b = frames[i];
  const u = Math.max(0, Math.min(1, (t - a.at) / (b.at - a.at)));
  return [a, b, u * u * (3 - 2 * u)];
}
export function sampleShape(shape: Shape, t: number): ShapeFrame {
  const [a, b, u] = segment(shape.frames, t);
  return { at: t, x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), angle: lerp(a.angle, b.angle, u), opacity: lerp(a.opacity, b.opacity, u), points: a.points.map((p, i) => ({ x: lerp(p.x, b.points[i].x, u), y: lerp(p.y, b.points[i].y, u) })) };
}
function sampleFace(frames: FaceFrame[], t: number): FaceFrame {
  const [a, b, u] = segment(frames, t);
  return Object.fromEntries(Object.keys(a).map(k => [k, lerp(a[k as keyof FaceFrame], b[k as keyof FaceFrame], u)])) as FaceFrame;
}
function path(ctx: CanvasRenderingContext2D, points: Point[], smooth: boolean) {
  ctx.beginPath();
  if (smooth) {
    const last = points[points.length - 1];
    ctx.moveTo((last.x + points[0].x) / 2, (last.y + points[0].y) / 2);
    points.forEach((p, i) => { const next = points[(i + 1) % points.length]; ctx.quadraticCurveTo(p.x, p.y, (p.x + next.x) / 2, (p.y + next.y) / 2); });
    ctx.closePath();
  } else {
    ctx.moveTo(points[0].x, points[0].y);
    points.slice(1).forEach(p => ctx.lineTo(p.x, p.y));
  }
}
function transform(ctx: CanvasRenderingContext2D, f: ShapeFrame) {
  ctx.translate(f.x, f.y); ctx.rotate(f.angle * Math.PI / 180);
}
function drawShape(ctx: CanvasRenderingContext2D, shape: Shape, f: ShapeFrame, body: ShapeFrame) {
  ctx.save();
  if (shape.anchor === 'body') transform(ctx, body);
  transform(ctx, f); ctx.globalAlpha = f.opacity;
  ctx.fillStyle = PALETTE[shape.color]; ctx.strokeStyle = PALETTE[shape.kind === 'line' ? shape.color : shape.stroke]; ctx.lineWidth = shape.width;
  if (shape.kind === 'ellipse') {
    const [a, b] = f.points;
    ctx.beginPath(); ctx.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.max(.01, Math.abs(b.x - a.x) / 2), Math.max(.01, Math.abs(b.y - a.y) / 2), 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  } else {
    path(ctx, f.points, false);
    if (shape.kind === 'polygon') { ctx.closePath(); ctx.fill(); }
    ctx.stroke();
  }
  ctx.restore();
}
function eye(ctx: CanvasRenderingContext2D, x: number, openness: number, f: FaceFrame) {
  ctx.strokeStyle = PALETTE[0]; ctx.lineWidth = 1.5;
  if (openness < .07) { ctx.beginPath(); ctx.moveTo(x - 4, -3); ctx.quadraticCurveTo(x, -1, x + 4, -3); ctx.stroke(); return; }
  ctx.save(); ctx.beginPath(); ctx.ellipse(x, -3, 5, 7 * openness, 0, 0, Math.PI * 2); ctx.fillStyle = PALETTE[0]; ctx.fill(); ctx.clip();
  ctx.fillStyle = PALETTE[4]; ctx.fillRect(x - 4, -9, 8, 13);
  ctx.fillStyle = PALETTE[1]; ctx.beginPath(); ctx.ellipse(x + f.gaze_x * 2, -2 + f.gaze_y * 2, 3.5, 5.5, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = PALETTE[0]; ctx.fillRect(x - 1 + f.gaze_x * 2, -5 + f.gaze_y * 2, 3, 5);
  ctx.fillStyle = PALETTE[3]; ctx.fillRect(x - 2 + f.gaze_x * 2, 1 + f.gaze_y * 2, 4, 2);
  ctx.fillStyle = PALETTE[4]; ctx.fillRect(x - 2 + f.gaze_x * 2, -6 + f.gaze_y * 2, 2, 2); ctx.restore();
}
export function drawScene(ctx: CanvasRenderingContext2D, scene: Scene, t: number) {
  if (scene.style === 'soft') { drawSoftScene(ctx, scene, t); return; }
  ctx.clearRect(0, 0, 96, 96);
  const body = sampleShape(scene.body, t);
  ctx.save(); ctx.fillStyle = '#12243930'; ctx.beginPath(); ctx.ellipse(body.x, 87, 25, 3, 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  for (const p of scene.props.filter(p => p.layer === 'behind')) drawShape(ctx, p, sampleShape(p, t), body);
  ctx.save(); transform(ctx, body); ctx.globalAlpha = body.opacity;
  path(ctx, body.points, true); ctx.fillStyle = PALETTE[scene.body.color]; ctx.fill();
  ctx.save(); ctx.clip(); ctx.fillStyle = PALETTE[1]; ctx.beginPath(); ctx.ellipse(12, 23, 40, 10, -.12, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = PALETTE[3]; ctx.beginPath(); ctx.ellipse(-12, -18, 13, 4, -.5, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = PALETTE[4]; ctx.fillRect(-19, -19, 4, 2); ctx.restore();
  path(ctx, body.points, true); ctx.strokeStyle = PALETTE[scene.body.stroke]; ctx.lineWidth = 1; ctx.stroke();
  const face = sampleFace(scene.face, t);
  ctx.save(); ctx.translate(face.x, face.y); eye(ctx, -9, face.eye_open_left, face); eye(ctx, 9, face.eye_open_right, face);
  ctx.save(); ctx.globalAlpha *= face.blush; ctx.fillStyle = PALETTE[5]; ctx.fillRect(-18, 5, 8, 3); ctx.fillRect(10, 5, 8, 3); ctx.restore();
  ctx.strokeStyle = PALETTE[10]; ctx.fillStyle = PALETTE[10]; ctx.lineWidth = 1;
  if (face.mouth_open > .4) {
    ctx.beginPath(); ctx.ellipse(0, 8, face.mouth_width / 2, face.mouth_open, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = PALETTE[5]; ctx.beginPath(); ctx.ellipse(0, 8 + face.mouth_open * .55, face.mouth_width * .3, face.mouth_open * .3, 0, 0, Math.PI * 2); ctx.fill();
  } else { ctx.beginPath(); ctx.moveTo(-face.mouth_width / 2, 8); ctx.quadraticCurveTo(0, 8 + face.smile * 4, face.mouth_width / 2, 8); ctx.stroke(); }
  ctx.restore(); ctx.restore();
  for (const p of scene.props.filter(p => p.layer === 'front')) drawShape(ctx, p, sampleShape(p, t), body);
}

// Protected silhouette: generated motion can grow an appendage, never cut the face/body.
function drawSoftScene(ctx: CanvasRenderingContext2D, scene: Scene, t: number) {
  ctx.clearRect(0, 0, 96, 96);
  const [a, b, u] = segment(scene.fan!, t);
  const f = { extension: lerp(a.extension, b.extension, u), spread: lerp(a.spread, b.spread, u), bend: lerp(a.bend, b.bend, u), sway: lerp(a.sway, b.sway, u) };
  const face = sampleFace(scene.face, t);
  ctx.fillStyle = '#112a4430'; ctx.beginPath(); ctx.ellipse(44, 84, 23, 3, 0, 0, Math.PI * 2); ctx.fill();
  ctx.save(); ctx.translate(44, 57);
  if (f.extension > .005) {
    ctx.save(); ctx.translate(20, 2); ctx.rotate(f.sway * Math.PI / 180);
    ctx.scale(f.extension, Math.sqrt(f.extension));
    ctx.beginPath(); ctx.moveTo(-6, 5);
    ctx.bezierCurveTo(4, 7, 8, 3 + f.bend * .35, 12, -3 + f.bend * .4);
    ctx.bezierCurveTo(16, -17 * f.spread + f.bend, 27, -19 * f.spread + f.bend, 30, -9 + f.bend);
    ctx.bezierCurveTo(33, 3 + f.bend, 23, 13 * f.spread + f.bend, 13, 5 + f.bend * .4);
    ctx.bezierCurveTo(6, 9, 1, 13, -6, 11); ctx.closePath();
    const leaf = ctx.createLinearGradient(5, -15, 25, 12); leaf.addColorStop(0, '#b9f4fa'); leaf.addColorStop(.45, '#67d4ee'); leaf.addColorStop(1, '#38a9d3');
    ctx.fillStyle = leaf; ctx.fill(); ctx.strokeStyle = '#318dbb'; ctx.lineWidth = .8; ctx.stroke();
    ctx.beginPath(); ctx.moveTo(4, 8); ctx.quadraticCurveTo(15, 0 + f.bend * .5, 26, -8 + f.bend); ctx.strokeStyle = '#cff9fa'; ctx.lineWidth = .7; ctx.stroke(); ctx.restore();
  }
  // Rounded droplet, with no generated vertices or movable holes.
  ctx.beginPath(); ctx.moveTo(-1, -31);
  ctx.bezierCurveTo(3, -33, 8, -24, 15, -16);
  ctx.bezierCurveTo(28, -3, 29, 18, 16, 24);
  ctx.bezierCurveTo(8, 28, -13, 28, -21, 20);
  ctx.bezierCurveTo(-31, 10, -23, -10, -14, -19);
  ctx.bezierCurveTo(-7, -26, -5, -30, -1, -31); ctx.closePath();
  const skin = ctx.createLinearGradient(-10, -28, 12, 26); skin.addColorStop(0, '#a6ecfa'); skin.addColorStop(.48, '#64cde9'); skin.addColorStop(1, '#389bc9'); ctx.fillStyle = skin; ctx.fill();
  ctx.strokeStyle = '#348fb7'; ctx.lineWidth = .8; ctx.stroke();
  ctx.save(); ctx.globalAlpha = .75; ctx.fillStyle = '#e5fcff'; ctx.beginPath(); ctx.ellipse(-11, -16, 7, 2.4, -.8, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  ctx.translate(Math.max(-2, Math.min(2, face.x)), Math.max(-2, Math.min(2, face.y)) + 3);
  for (const [x, openness] of [[-8, face.eye_open_left], [8, face.eye_open_right]]) {
    ctx.strokeStyle = '#235776'; ctx.lineWidth = 1.1;
    if (openness < .15) { ctx.beginPath(); ctx.moveTo(x - 3.5, -1); ctx.quadraticCurveTo(x, -3, x + 3.5, -1); ctx.stroke(); continue; }
    ctx.save(); ctx.beginPath(); ctx.ellipse(x, -2, 3.8, 5.3 * openness, 0, 0, Math.PI * 2); ctx.clip();
    ctx.fillStyle = '#214f70'; ctx.fillRect(x - 4, -8, 8, 13);
    ctx.fillStyle = '#3f99c3'; ctx.beginPath(); ctx.ellipse(x + face.gaze_x, .6 + face.gaze_y, 2.8, 2.6, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#defaff'; ctx.beginPath(); ctx.ellipse(x - 1.2 + face.gaze_x, -4 + face.gaze_y, 1.25, 1.5, 0, 0, Math.PI * 2); ctx.fill(); ctx.fillRect(x + 1, 0, 1, 1); ctx.restore();
  }
  ctx.save(); ctx.globalAlpha = Math.min(.55, face.blush); ctx.fillStyle = '#ffa6b8';
  for (const x of [-14, 14]) { ctx.beginPath(); ctx.ellipse(x, 5, 4.5, 2.2, 0, 0, Math.PI * 2); ctx.fill(); } ctx.restore();
  ctx.strokeStyle = '#456079'; ctx.lineWidth = 1;
  if (face.mouth_open > 1) { ctx.fillStyle = '#6b5870'; ctx.beginPath(); ctx.ellipse(0, 6.5, Math.min(2.7, face.mouth_width / 2), Math.min(2.8, face.mouth_open), 0, 0, Math.PI * 2); ctx.fill(); }
  else { ctx.beginPath(); ctx.moveTo(-2.5, 6); ctx.quadraticCurveTo(0, 6 + Math.max(-.5, face.smile) * 2.8, 2.5, 6); ctx.stroke(); }
  ctx.restore();
}
