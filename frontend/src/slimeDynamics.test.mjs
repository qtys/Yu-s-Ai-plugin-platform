import { test } from 'node:test';
import assert from 'node:assert/strict';
import { advanceSpring, dentMap } from './slimeDynamics.ts';

function simulate(fps) {
  let state = { value: 0, velocity: 0 };
  for (let i = 0; i < fps * 2; i++) state = advanceSpring(state, 1, 1 / fps);
  return state;
}
test('springs converge consistently at 30, 60 and 144 Hz', () => {
  for (const fps of [30, 60, 144]) {
    const state = simulate(fps);
    assert.ok(Math.abs(state.value - 1) < .001);
    assert.ok(Math.abs(state.velocity) < .01);
  }
});
test('suspended frames stay bounded and release returns to rest', () => {
  let state = advanceSpring({ value: 0, velocity: 120 }, 12, 10, 125, 12);
  assert.ok(Number.isFinite(state.value) && Math.abs(state.value) < 20);
  for (let i = 0; i < 240; i++) state = advanceSpring(state, 0, 1 / 60, 125, 12);
  assert.ok(Math.abs(state.value) < .001);
});
test('touch maps keep neutral surroundings and clamp contact coordinates', () => {
  const decoded = decodeURIComponent(dentMap(-999, 999));
  assert.ok(decoded.includes('cx="15" cy="170"'));
  assert.ok(decoded.includes('offset="1" stop-color="rgb(128,128,128)"'));
  assert.notEqual(dentMap(25, 80), dentMap(130, 80));
});
