import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setPetLayoutAnchor, clearPetLayoutAnchor } from './petLayoutLock.ts';

test('layout override is never active without both coordinates', () => {
  const properties = new Map();
  let locked = false;
  const check = () => {
    if (locked) assert.ok(properties.has('--layout-pet-x') && properties.has('--layout-pet-y'));
  };
  const canvas = {
    style: {
      setProperty(key, value) { properties.set(key, value); check(); },
      removeProperty(key) { properties.delete(key); check(); },
    },
    setAttribute(key, value) { assert.equal(key, 'data-layout-anchor'); locked = value === 'locked'; check(); },
    removeAttribute(key) { assert.equal(key, 'data-layout-anchor'); locked = false; check(); },
  };
  setPetLayoutAnchor(canvas, 74, 100);
  setPetLayoutAnchor(canvas, 145, 306);
  assert.equal(properties.get('--layout-pet-x'), '145px');
  clearPetLayoutAnchor(canvas);
  assert.equal(locked, false);
  assert.equal(properties.size, 0);
});
