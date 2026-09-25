import type { MobileCharacter, MobileInstruction, MobileMessage } from "./localDb";
import type { ModelMessage } from "./modelClient";

export function buildMessages(character: MobileCharacter, history: MobileMessage[], content: string, enabled: Record<string, boolean>, saved: MobileInstruction[], oneTime: string): ModelMessage[] {
  const instructions = [character.prompt.trim() || `你正在扮演“${character.name}”，保持角色一致，直接回应用户。`];
  if (enabled.conversation_environment) instructions.push(`当前时间：${new Date().toLocaleString("zh-CN", { hour12: false })}。只在与本轮问题有关时考虑时间，不必主动提及。`);
  if (enabled.novel_reply) instructions.push("以第三人称网络小说笔法回复，适度描写人物动作、声音、神态与氛围；先准确回答问题，保持角色卡设定，不替用户编造经历或台词。用户明确要求的格式优先。");
  const active = saved.filter((item) => item.enabled);
  if (active.length) instructions.push("【用户保存的长期对话指令】以下指令在本角色或当前会话内持续有效；本轮用户明确修正旧偏好时，以本轮要求为准。\n" + active.map((item, index) => `${index + 1}. ${item.content}`).join("\n"));
  if (oneTime) instructions.push("【仅本轮使用的模板指令】\n" + oneTime);
  if (active.length || oneTime) instructions.push("【答复前核对】请落实上方指令的内容、格式和长度要求，并保持角色设定；不要擅自忽略。");
  return [{ role: "system", content: instructions.join("\n\n") }, ...history.slice(-24).map((item) => ({ role: item.role, content: item.content })), { role: "user", content }];
}

export function templateFields(content: string): string[] {
  return [...new Set([...content.matchAll(/\{\{([^{}]+)\}\}/g)].map((match) => match[1].trim()).filter(Boolean))];
}
