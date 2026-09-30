import test from 'node:test';
import assert from 'node:assert/strict';
import { splitSpeechText } from './speech-utils.ts';

test('speech chunks preserve all text including emoji', () => {
  const text = '这是较长的回复。😀'.repeat(1000);
  const chunks = splitSpeechText(text);
  assert.equal(chunks.join(''), text);
  assert.ok(chunks.every(chunk => Array.from(chunk).length <= 3000));
  assert.ok(chunks.every(chunk => !/^[\uDC00-\uDFFF]|[\uD800-\uDBFF]$/.test(chunk)));
});
test('split at sentence boundaries when possible', () => {
  assert.deepEqual(splitSpeechText('123456。abcdefgh', 10), ['123456。', 'abcdefgh']);
  assert.deepEqual(splitSpeechText('  '), []);
  assert.throws(() => splitSpeechText('x', 0));
});
