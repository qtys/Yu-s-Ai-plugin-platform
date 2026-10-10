import test from "node:test";
import assert from "node:assert/strict";
import { buildMessages, templateFields } from "../promptBuilder.ts";
import { BUILTIN_INITIAL_TEMPLATES, initialPromptFor, renderInitialPrompt } from "../roleInitialization.ts";
import { streamModelReply } from "../modelClient.ts";

test("enabled saved instructions and one-time template reach the next request", () => {
  const character = { id: "c", name: "蓝雨", prompt: "保持角色设定", createdAt: 1 };
  const active = { id: "a", characterId: "c", conversationId: null, content: "先给结论", enabled: true, createdAt: 1 };
  const disabled = { ...active, id: "b", content: "这条不该提交", enabled: false };
  const messages = buildMessages(character, [], "现在回答", {}, [active, disabled], "限制在三段内");
  assert.match(messages[0].content, /先给结论/);
  assert.match(messages[0].content, /限制在三段内/);
  assert.doesNotMatch(messages[0].content, /这条不该提交/);
  assert.deepEqual(messages.at(-1), { role: "user", content: "现在回答" });
});

test("template variables are found once each", () => {
  assert.deepEqual(templateFields("写 {{主题}}，地点为 {{地点}}；仍是 {{主题}}"), ["主题", "地点"]);
});

test("bundled first-turn templates work without any local template database", () => {
  assert.equal(BUILTIN_INITIAL_TEMPLATES.length, 4);
  assert.equal(BUILTIN_INITIAL_TEMPLATES[0].name, "沉浸式角色开篇");
  assert.match(renderInitialPrompt(BUILTIN_INITIAL_TEMPLATES[0].content, "蓝雨"), /蓝雨/);
  assert.throws(() => renderInitialPrompt("{{未填写}}", "蓝雨"), /未填写变量/);
});

test("new chats initialize independently, legacy and completed chats never reinitialize", () => {
  const character = { id: "c", name: "蓝雨", prompt: "设定", createdAt: 1, initialPromptEnabled: true, initialPrompt: "开场：{{角色名称}}" };
  const conversation = { id: "a", characterId: "c", title: "新对话", updatedAt: 1, initialPromptApplied: false };
  assert.equal(initialPromptFor(character, conversation), "开场：蓝雨");
  assert.equal(initialPromptFor(character, { ...conversation, id: "b" }), "开场：蓝雨");
  assert.equal(initialPromptFor(character, { ...conversation, initialPromptApplied: true }), "");
  assert.equal(initialPromptFor(character, { ...conversation, initialPromptApplied: undefined }), "");
  assert.equal(initialPromptFor({ ...character, initialPromptEnabled: false }, conversation), "");
  const first = buildMessages(character, [], "你好", {}, [], "", initialPromptFor(character, conversation));
  assert.match(first[1].content, /首轮角色初始化/);
  const next = buildMessages(character, [], "再聊", {}, [], "", initialPromptFor(character, { ...conversation, initialPromptApplied: true }));
  assert.equal(next.filter((item) => item.role === "system").length, 1);
});

test("stream completion distinguishes stop, truncation, empty and interrupted replies", async () => {
  const originalFetch = globalThis.fetch;
  const config = { baseUrl: "https://example.test/v1", model: "mock", apiKey: "mock", temperature: 1, maxTokens: 128 };
  try {
    for (const [finish, done, complete] of [["stop", true, true], ["length", true, false], [null, false, false]]) {
      globalThis.fetch = async () => new Response(`data: ${JSON.stringify({ choices: [{ delta: { content: "回复" }, finish_reason: finish }] })}\n${done ? "data: [DONE]\n" : ""}`);
      const tokens = [];
      const result = await streamModelReply(config, [{ role: "user", content: "你好" }], (token) => tokens.push(token));
      assert.equal(result.complete, complete);
      assert.equal(result.content, "回复");
      assert.deepEqual(tokens, ["回复"]);
    }
    globalThis.fetch = async () => new Response("data: [DONE]\n");
    assert.equal((await streamModelReply(config, [{ role: "user", content: "你好" }], () => {})).content, "");
    globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) { controller.error(new Error("中断")); } }));
    await assert.rejects(streamModelReply(config, [{ role: "user", content: "你好" }], () => {}), /中断/);
  } finally { globalThis.fetch = originalFetch; }
});
