import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { emitTo, listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import MessageContent from "./MessageContent";
import type { PetChatSnapshot } from "./petChatBridge";

export default function PetChat() {
  const [snapshot, setSnapshot] = useState<PetChatSnapshot | null>(null);
  const [input, setInput] = useState("");
  const pending = useRef(false);
  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    void listen<PetChatSnapshot>("pet-chat-state", event => {
      if (!disposed) {
        setSnapshot(event.payload);
        if (event.payload.busy) { pending.current = false; setInput(""); }
        else pending.current = false;
      }
    }).then(unlisten => {
      if (disposed) unlisten();
      else { stop = unlisten; void emitTo("pet", "pet-chat-event", { type: "ready" }); }
    });
    return () => { disposed = true; stop?.(); };
  }, []);
  useEffect(() => {
    if (!snapshot?.open || !snapshot.ready) return;
    const sync = async () => {
      const scale = snapshot.scale;
      const regions = Array.from(document.querySelectorAll(".pet-chat-input, .pet-sentence")).map(element => {
        const rect = element.getBoundingClientRect();
        return [rect.left / scale, rect.top / scale, rect.width / scale, rect.height / scale];
      });
      await invoke("set_pet_chat_regions", { regions, scale });
      await invoke("show_pet_chat_window");
    };
    let frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => void sync().catch(console.error)); });
    const timer = setTimeout(() => void sync().catch(console.error), 300);
    const observer = new ResizeObserver(() => void sync().catch(console.error));
    document.querySelectorAll(".pet-chat-input, .pet-sentence").forEach(element => observer.observe(element));
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); observer.disconnect(); };
  }, [snapshot]);
  if (!snapshot?.open || !snapshot.ready) return null;
  const { layout } = snapshot;
  return <main className={`pet-chat-satellite model-${snapshot.model}`} style={{
    "--pet-scale": snapshot.scale, "--dialog-font-scale": snapshot.fontScale,
    "--chat-bubble-width": `${Math.min(snapshot.bubbleWidth, layout.bubbleWidthLimit ?? snapshot.bubbleWidth)}px`,
    width: layout.width, height: layout.height, transform: `scale(${snapshot.scale})`,
  } as CSSProperties}>
    <div className={`pet-sentence-stack ${snapshot.fading ? "fading" : ""}`} style={{ left: layout.bubbleX ?? layout.petX, top: layout.bubbleTop, bottom: layout.bubbleTop === undefined ? layout.height - layout.petY + 12 : "auto" }}
      aria-live="polite" onPointerEnter={() => void emitTo("pet", "pet-chat-event", { type: "hover", hovered: true })}
      onPointerLeave={() => void emitTo("pet", "pet-chat-event", { type: "hover", hovered: false })}>
      {snapshot.bubbles.map(bubble => <div className="pet-sentence" key={bubble.id}><MessageContent content={bubble.text} mode={snapshot.mode} /></div>)}
    </div>
    <section className="pet-chat-input" style={{ left: layout.inputX, top: layout.inputY }}>
      <form onSubmit={event => {
        event.preventDefault();
        if (!input.trim() || snapshot.busy || pending.current) return;
        pending.current = true;
        void emitTo("pet", "pet-chat-event", { type: "send", content: input.trim() }).catch(() => { pending.current = false; });
      }}>
        <input value={input} onChange={event => setInput(event.target.value)} disabled={!snapshot.enabled}
          placeholder={snapshot.enabled ? "和我说点什么……" : "请先在展开界面创建角色"}
          onPointerDown={event => {
            event.preventDefault();
            const target = event.currentTarget;
            void invoke("set_pet_keyboard_focus", { enabled: true }).then(() => {
              requestAnimationFrame(() => { if (target.isConnected) target.focus({ preventScroll: true }); });
            }).catch(console.error);
          }} />
        <button disabled={snapshot.busy || !snapshot.enabled || !input.trim()} aria-label="发送">{snapshot.busy ? "· ·" : "↑"}</button>
      </form>
      {snapshot.status && <small className="pet-input-status" role="status">{snapshot.status}</small>}
    </section>
  </main>;
}
