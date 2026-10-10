import test from 'node:test';
import assert from 'node:assert/strict';
import { placeSatelliteBubbles } from './petSatelliteLayout.ts';

const space = { width: 720, height: 700, petX: 360, petY: 150, inputX: 260, inputY: 306 };
test('keep fitting newest bubbles above; only older overflow goes left', () => {
  const positions = placeSatelliteBubbles([{width:250,height:100},{width:200,height:50},{width:180,height:50}],space);
  assert.equal(positions[2].top,88);
  assert.equal(positions[1].top,31);
  assert.equal(positions[0].left+Math.min(250,positions[0].maxWidth),space.inputX-12);
  assert.equal(positions[0].top,space.petY+4);
  assert.ok(positions.every(p=>p.visible));
});
test('near the top the side bubble hugs the pet instead of centering in spare room', () => {
  const p = placeSatelliteBubbles([{width:110,height:60}],{...space,petY:50})[0];
  assert.equal(p.left+110,space.inputX-12);
  assert.equal(p.top,12);
});
test('left screen corner falls back to close right-hand placement', () => {
  const s={...space,petX:90,petY:50,inputX:12};
  const p=placeSatelliteBubbles([{width:200,height:70}],s)[0];
  assert.equal(p.left,s.inputX+200+12);
  assert.ok(p.left>=12&&p.left+200<=s.width-12);
});
test('a short older sentence above never overlaps a long newest side sentence', () => {
  const p=placeSatelliteBubbles([{width:200,height:40},{width:300,height:400}],space);
  assert.ok(p[0].top+40<=space.petY-12);
  assert.equal(p[1].top,space.petY+4);
});
test('overflow wraps within side space and retires bubbles that cannot fit', () => {
  const p=placeSatelliteBubbles([{width:300,height:500,sideHeight:600},{width:300,height:50}],space);
  assert.equal(p[0].maxWidth,236);
  assert.equal(p[0].visible,false);
});
