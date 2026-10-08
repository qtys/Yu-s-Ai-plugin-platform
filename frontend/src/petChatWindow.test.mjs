import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';

const config = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'));
test('companion shares WebView2 environment options with the existing windows', () => {
  const pet = config.app.windows.find(window => window.label === 'pet');
  const chat = config.app.windows.find(window => window.label === 'pet-chat');
  assert.equal(chat.additionalBrowserArgs, pet.additionalBrowserArgs);
  assert.equal(chat.additionalBrowserArgs, config.app.windows[0].additionalBrowserArgs);
});
test('companion starts hidden and passive without replacing the pet document', () => {
  const chat = config.app.windows.find(window => window.label === 'pet-chat');
  assert.equal(chat.visible, false);
  assert.equal(chat.focus, false);
  assert.equal(chat.transparent, true);
  assert.equal(chat.url, 'pet-chat.html');
  assert.notEqual(chat.url, config.app.windows.find(window => window.label === 'pet').url);
});
