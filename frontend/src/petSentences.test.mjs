import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitPetSentences } from './petSentences.ts';
test('streams whole sentences and holds the unfinished tail', () => {
  assert.deepEqual(splitPetSentences('辛苦啦。先喝口水！我陪'), ['辛苦啦。', '先喝口水！']);
  assert.deepEqual(splitPetSentences('辛苦啦。先喝口水！我陪你', true), ['辛苦啦。', '先喝口水！', '我陪你']);
});
test('closing quotes and repeated punctuation remain with the sentence', () => {
  assert.deepEqual(splitPetSentences('“你好！”然后呢？', true), ['“你好！”', '然后呢？']);
  assert.deepEqual(splitPetSentences('真的吗？！好呀。', true), ['真的吗？！', '好呀。']);
});
test('decimal values are not split and English sentences work', () => {
  assert.deepEqual(splitPetSentences('温度 23.5 度。', true), ['温度 23.5 度。']);
  assert.deepEqual(splitPetSentences('Hello. Nice to meet you!', true), ['Hello.', 'Nice to meet you!']);
  assert.deepEqual(splitPetSentences('It costs 5. Okay.', true), ['It costs 5.', 'Okay.']);
});
test('empty output and whitespace do not create bubbles', () => {
  assert.deepEqual(splitPetSentences(' \n ', true), []);
});
