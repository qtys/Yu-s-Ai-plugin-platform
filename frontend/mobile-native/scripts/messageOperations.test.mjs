import test from "node:test";
import assert from "node:assert/strict";
import { IDBFactory } from "fake-indexeddb";
import { mobileDb } from "../localDb.ts";
import { editedMessage, withdrawnMessageIds } from "../messageOperations.ts";
import { buildMessages } from "../promptBuilder.ts";

const message = (id, role, createdAt, conversationId = "c") => ({ id, role, createdAt, conversationId, content: id });
const history = [message("u1", "user", 1), message("a1", "assistant", 2), message("a2", "assistant", 3), message("u2", "user", 4), message("a3", "assistant", 5), message("other", "assistant", 2, "other")];

test("withdraw user removes its replies only; assistant and unanswered user are independent", () => {
  assert.deepEqual(withdrawnMessageIds(history, "u1"), ["u1", "a1", "a2"]);
  assert.deepEqual(withdrawnMessageIds(history, "a1"), ["a1"]);
  assert.deepEqual(withdrawnMessageIds([message("failed-user", "user", 1)], "failed-user"), ["failed-user"]);
  assert.throws(() => withdrawnMessageIds(history, "missing"), /不存在/);
});

test("editing trims and validates without changing identity or ordering", () => {
  const updated = editedMessage(history[0], " 修改内容 ", 10);
  assert.equal(updated.content, "修改内容");
  assert.equal(updated.id, "u1");
  assert.equal(updated.createdAt, 1);
  assert.equal(updated.editedAt, 10);
  assert.throws(() => editedMessage(history[0], "  "), /不能为空/);
  assert.throws(() => editedMessage(history[0], "文".repeat(34000)), /超过/);
});

test("persistent edit and withdrawal alter next request and keep other chats, instructions and first-turn state", async () => {
  globalThis.indexedDB = new IDBFactory();
  const character = { id: "r", name: "测试", prompt: "角色", createdAt: 1 };
  await mobileDb.putConversation({ id: "c", characterId: "r", title: "测试", updatedAt: 1, initialPromptApplied: true });
  await mobileDb.putConversation({ id: "other", characterId: "r", title: "另一个", updatedAt: 1 });
  for (const item of history) await mobileDb.putMessage(item);
  await mobileDb.putInstruction({ id: "i", characterId: "r", conversationId: "c", content: "保留指令", enabled: true, createdAt: 1 });
  await mobileDb.changeMessage("c", "a1", "新的回答");
  let saved = await mobileDb.messages("c");
  assert.equal(saved.find((item) => item.id === "a1").content, "新的回答");
  let request = buildMessages(character, saved, "继续", {}, [], "");
  assert.equal(request.find((item) => item.role === "assistant").content, "新的回答");
  await assert.rejects(mobileDb.changeMessage("c", "a1", " "), /不能为空/);
  assert.equal((await mobileDb.messages("c")).find((item) => item.id === "a1").content, "新的回答");
  await assert.rejects(mobileDb.changeMessage("other", "a1", null), /不存在/);
  await mobileDb.changeMessage("c", "u1", null);
  saved = await mobileDb.messages("c");
  assert.deepEqual(saved.map((item) => item.id), ["u2", "a3"]);
  request = buildMessages(character, saved, "继续", {}, [], "");
  assert.ok(!request.some((item) => ["u1", "新的回答", "a2"].includes(item.content)));
  assert.equal((await mobileDb.messages("other")).length, 1);
  assert.equal((await mobileDb.instructions("r", "c")).length, 1);
  assert.equal((await mobileDb.conversations("r")).find((item) => item.id === "c").initialPromptApplied, true);
});

test("transaction rolls back message changes when its conversation is missing", async () => {
  globalThis.indexedDB = new IDBFactory();
  await mobileDb.putMessage(message("orphan", "user", 1));
  await assert.rejects(mobileDb.changeMessage("c", "orphan", "改写"), /对话不存在/);
  assert.equal((await mobileDb.messages("c"))[0].content, "orphan");
});
