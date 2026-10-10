import type { MobileMessage } from "./localDb";

export function editedMessage(message: MobileMessage, content: string, now = Date.now()): MobileMessage {
  const text = content.trim();
  if (!text) throw new Error("消息内容不能为空");
  if (new TextEncoder().encode(text).length > 100000) throw new Error("消息超过模型支持的单条长度，请缩短后保存");
  return { ...message, content: text, editedAt: now };
}

export function withdrawnMessageIds(history: MobileMessage[], messageId: string): string[] {
  const target = history.find((item) => item.id === messageId);
  if (!target) throw new Error("消息不存在或已撤回");
  const sameConversation = history.filter((item) => item.conversationId === target.conversationId).sort((a, b) => a.createdAt - b.createdAt);
  const index = sameConversation.findIndex((item) => item.id === messageId);
  const ids = [messageId];
  if (target.role === "user") {
    for (const item of sameConversation.slice(index + 1)) {
      if (item.role === "user") break;
      ids.push(item.id);
    }
  }
  return ids;
}
