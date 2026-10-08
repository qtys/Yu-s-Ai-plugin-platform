import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fitPetBubbleCount } from './petBubbleLayout.ts';
test('four bubbles fit without shrinking', () => {
  assert.equal(fitPetBubbleCount([40, 40, 40, 40], 190), 4);
});
test('remove oldest instead of adding a scrollbar or shrinking the newest', () => {
  assert.equal(fitPetBubbleCount([40, 100, 240], 350), 2);
  assert.equal(fitPetBubbleCount([40, 100, 240], 250), 1);
});
test('keep newest and never exceed four', () => {
  assert.equal(fitPetBubbleCount([20, 20, 20, 20, 20], 300), 4);
  assert.equal(fitPetBubbleCount([], 300), 0);
  assert.equal(fitPetBubbleCount([500], 300), 1);
});
