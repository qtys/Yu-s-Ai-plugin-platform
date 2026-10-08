import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resumePetBubbles } from './petBubblePlayback.ts';

test('reopening skips received sentences rather than replaying from the first', () => {
  const received = ['第一句。', '第二句。', '第三句。', '第四句。', '第五句。'];
  const resumed = resumePetBubbles(received);
  assert.deepEqual(resumed.visible, received.slice(-4));
  assert.equal(resumed.cursor, 5);
  received.push('新到的第六句。');
  assert.equal(received[resumed.cursor], '新到的第六句。');
});

test('finished output remains available on reopen, empty output starts at zero', () => {
  assert.deepEqual(resumePetBubbles(['已完成。']), { cursor: 1, visible: ['已完成。'] });
  assert.deepEqual(resumePetBubbles([]), { cursor: 0, visible: [] });
});
