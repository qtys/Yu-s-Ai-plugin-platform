import type { FaceFrame, Point, Scene, Shape, ShapeFrame } from './scene.ts';
import { SOFT_DEMO } from './soft-demo.ts';

const points = (pairs: number[][]): Point[] => pairs.map(([x, y]) => ({ x, y }));
const outline = points([[3,-36],[10,-22],[22,-16],[27,-4],[29,12],[23,22],[10,25],[-8,25],[-23,22],[-29,12],[-26,-3],[-17,-17]]);
const frame = (at: number, p: Point[], x = 48, y = 58, opacity = 1): ShapeFrame => ({ at, x, y, opacity, angle: 0, points: p });
const face = (at: number, patch: Partial<FaceFrame> = {}): FaceFrame => ({ at, x: 0, y: 0, eye_open_left: 1, eye_open_right: 1, gaze_x: 0, gaze_y: 0, mouth_width: 6, mouth_open: 0, smile: 1, blush: .45, ...patch });
const body = (frames: ShapeFrame[]): Shape => ({ id: 'slime', label: '蓝雨身体', kind: 'polygon', anchor: 'world', layer: 'front', color: 2, stroke: 0, width: 1, frames });
const prop = (id: string, label: string, kind: Shape['kind'], color: number, frames: ShapeFrame[], layer: Shape['layer'] = 'front', anchor: Shape['anchor'] = 'world'): Shape => ({ id, label, kind, color, frames, layer, anchor, stroke: 0, width: 1 });
const flat = points([[9,-14],[21,-10],[31,-6],[36,0],[35,9],[26,12],[11,13],[-10,13],[-30,10],[-35,6],[-30,-3],[-17,-10]]);
const reach = outline.map((p, i) => i === 3 || i === 4 ? { x: p.x + 14, y: p.y - 4 } : p);
const lamp = points([[-6,-7],[6,7]]);
const lit = outline.map((p, i) => i === 0 ? { x: 16, y: -30 } : i === 1 ? { x: 15, y: -23 } : p);
const tangled = outline.map((p, i) => ({ x: p.x + (i % 3 === 0 ? 5 : -2), y: p.y + (i % 2 === 0 ? -3 : 3) }));

// Authored fixtures demonstrate the renderer; they are never presented as model generation.
export const DEMOS: { prompt: string; scene: Scene }[] = [
  SOFT_DEMO,
  { prompt: '今天实在太累了，想躺一会儿。', scene: {
    version: 1, title: '把小枕头推过来，慢慢摊平', intent: '局部身体伸出推枕头，接触后身体摊平，眼睑和嘴形逐渐放松。此为手绘引擎测试场景。', duration_ms: 6500,
    body: body([frame(0, outline), frame(.22, reach), frame(.42, outline), frame(.68, flat, 46, 68), frame(.86, flat, 46, 68), frame(1, outline)]),
    face: [face(0), face(.24, { gaze_x: 1 }), face(.65, { y: 1, eye_open_left: .18, eye_open_right: .3, mouth_width: 3, smile: -.4 }), face(.84, { y: 1, eye_open_left: 0, eye_open_right: .08, mouth_width: 3 }), face(1)],
    props: [prop('pillow', '被推到身下的小枕头', 'polygon', 7, [frame(0, points([[-15,-4],[12,-5],[16,0],[13,5],[-14,4],[-17,0]]), 80, 80, 0), frame(.18, points([[-15,-4],[12,-5],[16,0],[13,5],[-14,4],[-17,0]]), 80, 80), frame(.4, points([[-15,-4],[12,-5],[16,0],[13,5],[-14,4],[-17,0]]), 59, 80), frame(.85, points([[-18,-2],[15,-2],[18,0],[15,4],[-17,4],[-20,0]]), 54, 80), frame(1, points([[-15,-4],[12,-5],[16,0],[13,5],[-14,4],[-17,0]]), 54, 80, 0)], 'behind')],
  } },
  { prompt: '电路终于调通了，灯亮起来了！', scene: {
    version: 1, title: '用水滴尖端点亮小灯泡', intent: '身体轮廓的水滴尖端伸向灯泡，接触时发光，随后惊喜鼓起再恢复。此为手绘引擎测试场景。', duration_ms: 5000,
    body: body([frame(0, outline), frame(.25, lit), frame(.4, lit), frame(.64, outline.map((p, i) => ({ x: p.x * (i > 1 ? 1.1 : 1), y: p.y - (i < 5 ? 4 : 0) }))), frame(1, outline)]),
    face: [face(0), face(.25, { gaze_x: 1, gaze_y: -1, mouth_width: 4, mouth_open: 3 }), face(.52, { mouth_width: 9, mouth_open: 5 }), face(.8, { eye_open_left: .1, eye_open_right: .1, mouth_width: 9 }), face(1)],
    props: [prop('bulb', '可独立移动的灯泡', 'ellipse', 6, [frame(0, lamp, 65, 26, 0), frame(.15, lamp, 65, 26), frame(.76, lamp, 65, 26), frame(1, lamp, 65, 26, 0)]), ...[-1,0,1].map((n, i) => prop(`ray_${i}`, '接触后才出现的光线', 'line', 6, [frame(0, points([[0,0],[n*7,-6]]), 65+n*7, 16, 0), frame(.28, points([[0,0],[n*7,-6]]), 65+n*7, 16, 0), frame(.44, points([[0,0],[n*7,-9]]), 65+n*7, 16), frame(.72, points([[0,0],[n*7,-6]]), 65+n*7, 16), frame(1, points([[0,0],[n*7,-6]]), 65+n*7, 16, 0)]))],
  } },
  { prompt: '这个问题越想越乱，思路打结了。', scene: {
    version: 1, title: '把思路线绕出来，自己也打了结', intent: '轮廓侧面伸出，线条逐点展开并环绕身体，惊讶后解开；不是整只旋转。此为手绘引擎测试场景。', duration_ms: 6200,
    body: body([frame(0, outline), frame(.26, reach), frame(.55, tangled), frame(.78, outline), frame(1, outline)]),
    face: [face(0), face(.2, { gaze_x: 1 }), face(.55, { gaze_x: -1, mouth_width: 4, mouth_open: 4, blush: .75 }), face(.78, { eye_open_left: .15, gaze_x: 1, mouth_width: 7 }), face(1)],
    props: [prop('thought_thread', '重新绘制的思路线', 'line', 7, [frame(0, points(Array.from({ length: 12 }, () => [22,6])), 0, 0, 0), frame(.16, points(Array.from({ length: 12 }, () => [22,6])), 0, 0, 1), frame(.5, points([[22,6],[35,-4],[25,-18],[-20,-19],[-32,-5],[-15,16],[25,18],[35,2],[-8,-5],[-32,12],[-8,23],[25,10]]), 0, 0), frame(.8, points([[22,6],[32,4],[34,2],[37,1],[39,0],[41,-1],[42,-2],[43,-3],[44,-4],[44,-5],[45,-6],[45,-7]]), 0, 0), frame(1, points(Array.from({ length: 12 }, () => [22,6])), 0, 0, 0)], 'front', 'body')],
  } },
];
