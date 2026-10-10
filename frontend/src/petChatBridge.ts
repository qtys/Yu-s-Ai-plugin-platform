import type { MessageDisplayMode } from "./MessageContent";
export type PetChatLayout = { width: number; height: number; petX: number; petY: number; inputX: number; inputY: number; available: number; bubbleX?: number; bubbleTop?: number; bubbleWidthLimit?: number };
export type PetChatSnapshot = {
  open: boolean; ready: boolean; bubbles: { id: number; text: string }[]; fading: boolean;
  showInput: boolean; proactive: boolean;
  busy: boolean; status: string; enabled: boolean; model: string; scale: number; fontScale: number;
  mode: MessageDisplayMode; layout: PetChatLayout; bubbleWidth: number;
};
export type PetChatEvent = { type: "ready" } | { type: "send"; content: string } | { type: "hover"; hovered: boolean } | { type: "reply-proactive" };
