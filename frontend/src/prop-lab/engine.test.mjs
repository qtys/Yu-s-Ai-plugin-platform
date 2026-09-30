import test from 'node:test';
import assert from 'node:assert/strict';
import { validatePerformance, pathData, sample, MOUTH, resolveAnchor } from './engine.ts';
import { DEMO } from './demo.ts';
test('contact stays at mouth while rotating and releasing is continuous',()=>{
  validatePerformance(DEMO);
  const before=structuredClone(DEMO);
  for(const t of [.45,.55,.6,.7]){const p=sample(DEMO,t);assert.equal(p.x,MOUTH.x);assert.equal(p.y,MOUTH.y);assert.ok(p.attached);}
  const a=sample(DEMO,.76),b=sample(DEMO,.760001);
  assert.ok(Math.hypot(a.x-b.x,a.y-b.y)<.001);
  assert.equal(sample(DEMO,0).opacity,0);assert.equal(sample(DEMO,1).opacity,0);
  assert.deepEqual(DEMO,before);
  assert.match(pathData(DEMO.paths[0]),/^M/);
});
test('eye attachment follows the rotating head exactly without requiring drinking',()=>{
  const p=structuredClone(DEMO);
  for(const f of p.frames)if(f.anchor==='mouth'){f.anchor='left_eye';f.angle=0;}
  validatePerformance(p);
  for(const t of [.45,.55,.6,.7]){
    const pose=sample(p,t),eye=resolveAnchor('left_eye',pose.head);
    assert.ok(Math.hypot(pose.x-eye.x,pose.y-eye.y)<1e-9);
    assert.equal(pose.angle,pose.head);
  }
  const f=sample(p,.76),next=sample(p,.760001);
  assert.ok(Math.hypot(f.x-next.x,f.y-next.y)<.001);
  for(const frame of p.frames)frame.anchor='stage';
  validatePerformance(p); // Free-floating prop does not need contact.
});
test('rejects script-like paths, extreme geometry and broken attachment',()=>{
  for(const change of [p=>{p.paths[0].fill='url(https://evil.test)';},p=>{p.paths[0].segments[0][0]=Infinity;},p=>{p.frames[3].x=20;},p=>{p.frames[4].at=0;},p=>{p.frames.at(-1).opacity=1;}]){
    const p=structuredClone(DEMO);change(p);assert.throws(()=>validatePerformance(p));
  }
});
