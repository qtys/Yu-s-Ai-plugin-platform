import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseStoryOptions } from './storyOptions.ts';

test('ordinary marked options are extracted without changing source data', () => {
  const text = '正文\n『剧情发展』\n1.[正常]打开电视。\n2.[其他]出去散步。\n</Status_block>';
  const result = parseStoryOptions(text);
  assert.deepEqual(result.options, [{number:'1',label:'正常',text:'打开电视。'}, {number:'2',label:'其他',text:'出去散步。'}]);
  assert.ok(result.body.includes('正文'));
  assert.ok(!result.body.includes('打开电视。'));
  assert.ok(text.includes('打开电视。'));
});
test('ordinary numbered prose and fenced examples are not controls', () => {
  for (const text of ['1.[正常]打开电视。', '```xml\n『剧情发展』\n1.[正常]打开电视。\n```']) {
    assert.deepEqual(parseStoryOptions(text), {body:text,options:[]});
  }
});
test('unsupported branches remain text and markup is parsed only as literal text', () => {
  const text = '『剧情发展』\n1.[未知]不解析\n2.[正常]<script>alert(1)</script>\n3.[其他]散步';
  const result = parseStoryOptions(text);
  assert.deepEqual(result.options, [{number:'1',label:'未知',text:'不解析'}, {number:'2',label:'正常',text:'<script>alert(1)</script>'}, {number:'3',label:'其他',text:'散步'}]);
});
test('bounds, duplicates and subsequent prose are preserved', () => {
  const result = parseStoryOptions('『剧情发展』\n1.[正常]散步\n1.[其他]读书\n正文结束\n2.[正常]不解析');
  assert.equal(result.options.length,1);
  assert.ok(result.body.includes('读书'));
  assert.ok(result.body.includes('2.[正常]不解析'));
});
test('completed character-state code blocks do not hide ordinary options', () => {
  const text = '<details><summary>角色状态</summary>\n```\n位置：书房\n```\n</details>\n『剧情发展』\n1.[正常]去花园散步。\n2.[其他]整理书架。\n</Status_block>';
  assert.equal(parseStoryOptions(text).options.length, 2);
});
test('tilde and longer fences protect examples but allow options after closing', () => {
  const example = '~~~~\n『剧情发展』\n1.[正常]示例\n~~~\n';
  assert.equal(parseStoryOptions(example).options.length, 0);
  assert.deepEqual(parseStoryOptions(example + '~~~~\n『剧情发展』\n2.[其他]整理书架').options, [{number:'2',label:'其他',text:'整理书架'}]);
});
test('a validated supplement at the end takes precedence over an earlier incomplete block', () => {
  const result = parseStoryOptions('『剧情发展』\n1.[正常]旧选项\n\n『剧情发展』\n1.[正常]散步\n2.[正常]整理书架');
  assert.deepEqual(result.options, [{number:'1',label:'正常',text:'散步'}, {number:'2',label:'正常',text:'整理书架'}]);
});
test('original ordinary branches retain nonconsecutive numbers and both supported labels', () => {
  const result = parseStoryOptions('正文\n『剧情发展』\n1.[正常]散步\n2.[未知]原文\n4.[其他]整理书架\n</Status_block>');
  assert.deepEqual(result.options, [{number:'1',label:'正常',text:'散步'}, {number:'2',label:'未知',text:'原文'}, {number:'4',label:'其他',text:'整理书架'}]);
});
test('only backend-approved choices become controls, with original tags preserved', () => {
  const text = '『剧情发展』\n1.[普通]散步\n2.[探索]查看地图\n3.[自定义]保留原文';
  const allowed = new Set(['1\u0000普通\u0000散步', '2\u0000探索\u0000查看地图']);
  const result = parseStoryOptions(text, allowed);
  assert.deepEqual(result.options.map(option => option.label), ['普通', '探索']);
  assert.ok(result.body.includes('[自定义]保留原文'));
  assert.equal(parseStoryOptions(text, new Set()).options.length, 0);
});
