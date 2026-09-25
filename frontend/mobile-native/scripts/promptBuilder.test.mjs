import test from "node:test";
import assert from "node:assert/strict";
import { buildMessages, templateFields } from "../promptBuilder.ts";

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
