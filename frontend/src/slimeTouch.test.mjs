import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rememberTouch, touchVisible } from './slimeTouch.ts';
const empty = () => ({ recent: [], lastProtest: -Infinity });

test('head, cheeks and centre have distinct touch responses', () => {
  assert.equal(rememberTouch(empty(), 70, 35, 1000).memory.kind, 'head');
  assert.equal(rememberTouch(empty(), 30, 100, 1000).memory.kind, 'cheek');
  const right = rememberTouch(empty(), 110, 100, 1000).memory;
  assert.equal(right.kind, 'cheek'); assert.equal(right.side, 'right');
  assert.equal(rememberTouch(empty(), 70, 100, 1000).memory.kind, 'boop');
});
test('three rapid taps produce a restrained protest with a cooldown', () => {
  let state = empty();
  for (const now of [1000, 1400]) state = rememberTouch(state, 30, 100, now).history;
  const third = rememberTouch(state, 30, 100, 1700);
  assert.equal(third.memory.kind, 'protest');
  state = third.history;
  for (const now of [1800, 1900, 2000]) {
    const next = rememberTouch(state, 30, 100, now);
    assert.notEqual(next.memory.kind, 'protest'); state = next.history;
  }
});
test('separated taps do not accumulate indefinitely', () => {
  let state = empty();
  for (const now of [1000, 4000, 7000]) {
    const next = rememberTouch(state, 70, 35, now);
    assert.equal(next.memory.kind, 'head'); state = next.history;
  }
});

test('another poke cannot instantly erase or extend an active protest', () => {
  const previous = { kind: 'protest', side: 'left', until: 8000 };
  const next = rememberTouch({ recent: [], lastProtest: 1700 }, 70, 35, 2000, previous);
  assert.equal(next.memory, previous);
  assert.equal(next.memory.until, 8000);
  assert.equal(rememberTouch(next.history, 70, 35, 9000, previous).memory.kind, 'head');
});
test('memory expires and never overrides protected model expression or disabled motion', () => {
  const memory = rememberTouch(empty(), 30, 100, 1000).memory;
  assert.equal(touchVisible(memory, 2000, false, true), true);
  assert.equal(touchVisible(memory, 2000, true, true), false);
  assert.equal(touchVisible(memory, 2000, false, false), false);
  assert.equal(touchVisible(memory, memory.until, false, true), false);
  assert.equal(touchVisible(null, 2000, false, true), false);
});
