import type { Scene, FaceFrame } from './scene.ts';

const points = [[0,-31],[14,-20],[25,-4],[24,14],[15,24],[0,27],[-16,24],[-25,12],[-24,-5],[-13,-20]].map(([x,y]) => ({x,y}));
const face = (at: number, patch: Partial<FaceFrame> = {}): FaceFrame => ({ at, x: 0, y: 0, eye_open_left: 1, eye_open_right: 1, gaze_x: 0, gaze_y: 0, mouth_width: 5, mouth_open: 0, smile: .8, blush: .4, ...patch });
export const SOFT_DEMO: { prompt: string; scene: Scene } = {
  prompt: '好热呀，从身体侧面长出一片柔软的蓝色扇叶，给自己扇扇风吧。',
  scene: {
    version: 1, style: 'soft', title: '一片凉风，慢慢融回身体',
    intent: '固定圆润身体与五官，侧面蓝色扇叶逐渐长出、柔软摆动，再缩回身体。这是美术与连接效果的手绘校准样例，不是模型生成。',
    duration_ms: 7200,
    body: { id: 'slime', label: '受保护的基础造型', kind: 'polygon', anchor: 'world', layer: 'front', color: 2, stroke: 0, width: 1, frames: [0,1].map(at => ({ at, x: 44, y: 57, angle: 0, opacity: 1, points })) },
    face: [face(0), face(.16, { mouth_open: 1.8, gaze_x: .5 }), face(.35, { gaze_x: .8 }), face(.48, { eye_open_left: .1, eye_open_right: .1 }), face(.64, { eye_open_left: .45, eye_open_right: .45 }), face(.85), face(1)],
    fan: [
      { at: 0, extension: 0, spread: .65, bend: 0, sway: 0 },
      { at: .2, extension: .85, spread: .85, bend: -3, sway: -8 },
      { at: .32, extension: 1, spread: .95, bend: 4, sway: 12 },
      { at: .44, extension: 1, spread: .8, bend: -6, sway: -15 },
      { at: .56, extension: 1, spread: .95, bend: 5, sway: 12 },
      { at: .68, extension: .95, spread: .8, bend: -5, sway: -12 },
      { at: .8, extension: .85, spread: .85, bend: 1, sway: 3 },
      { at: 1, extension: 0, spread: .65, bend: 0, sway: 0 },
    ], props: [],
  },
};
