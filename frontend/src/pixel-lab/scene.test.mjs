import test from 'node:test';
import assert from 'node:assert/strict';
import { validateScene, sampleShape } from './scene.ts';
import { DEMOS } from './demos.ts';

test('all authored fixtures have valid stable topology and morph individual contour points', () => {
  for (const { scene } of DEMOS) {
    validateScene(scene);
    const before = structuredClone(scene);
    const first = sampleShape(scene.body, 0);
    assert.deepEqual(first.points, scene.body.frames[0].points);
    assert.deepEqual(sampleShape(scene.body, 1).points, scene.body.frames.at(-1).points);
    if (scene.style === 'soft') {
      assert.ok(scene.fan.some(f => f.extension > .5));
      assert.ok(scene.body.frames.every(f => JSON.stringify(f.points) === JSON.stringify(first.points)));
    } else assert.ok(scene.body.frames.some(f => JSON.stringify(f.points) !== JSON.stringify(first.points)));
    sampleShape(scene.body, .37);
    assert.deepEqual(scene, before);
  }
});

test('rejects changing vertex counts, duplicate layers, unbounded and nonfinite geometry', () => {
  for (const change of [
    s => s.body.frames[1].points.pop(),
    s => { s.body.frames[1].at = 0; },
    s => { s.body.frames[1].x = Infinity; },
    s => { s.body.frames[1].points[0].x = 100000; },
    s => s.props.push(structuredClone(s.body)),
    s => { s.face[1].mouth_open = 200; },
  ]) {
    const s = structuredClone(DEMOS[1].scene);
    change(s);
    assert.throws(() => validateScene(s));
  }
});

test('protected appendage must retract and respect bending limits', () => {
  for (const change of [s => { s.fan[1].sway = 90; }, s => { s.fan.at(-1).extension = 1; }, s => { s.fan[2].extension = -1; }]) {
    const s = structuredClone(DEMOS[0].scene); change(s); assert.throws(() => validateScene(s));
  }
});
