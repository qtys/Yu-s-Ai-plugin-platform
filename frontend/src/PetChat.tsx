import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { emitTo, listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import MessageContent from "./MessageContent";
import type { PetChatSnapshot } from "./petChatBridge";
import { placeSatelliteBubbles } from "./petSatelliteLayout";
import type { BubbleBounds } from "./petSatelliteLayout";

export default function PetChat() {
  const [snapshot, setSnapshot] = useState<PetChatSnapshot | null>(null);
  const [input, setInput] = useState("");
  const pending = useRef(false);
  const stack = useRef<HTMLDivElement>(null);
  const measurer = useRef<HTMLDivElement>(null);
  const [positions, setPositions] = useState<BubbleBounds[]>([]);
  useLayoutEffect(() => {
    if (!snapshot?.open || !snapshot.ready || !stack.current || !measurer.current) return;
    const measure = () => {
      const displayed = Array.from(stack.current?.children ?? []) as HTMLElement[];
      const sizes = Array.from(measurer.current?.children ?? []).map((element, index) => ({
        width: (element as HTMLElement).offsetWidth, height: (element as HTMLElement).offsetHeight,
        sideHeight: displayed[index]?.offsetHeight,
      }));
      const next = placeSatelliteBubbles(sizes, snapshot.layout);
      setPositions(current => JSON.stringify(current) === JSON.stringify(next) ? current : next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    [...stack.current.children, ...measurer.current.children].forEach(element => observer.observe(element));
    return () => observer.disconnect();
  }, [snapshot]);
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
      const regions = Array.from(document.querySelectorAll(".pet-chat-input, .pet-sentence-stack .pet-sentence")).filter(element => getComputedStyle(element).visibility !== "hidden").map(element => {
        const rect = element.getBoundingClientRect();
        return [rect.left / scale, rect.top / scale, rect.width / scale, rect.height / scale];
      });
      await invoke("set_pet_chat_regions", { regions, scale });
      await invoke("show_pet_chat_window");
    };
    let frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => void sync().catch(console.error)); });
    const timer = setTimeout(() => void sync().catch(console.error), 300);
    const observer = new ResizeObserver(() => void sync().catch(console.error));
    document.querySelectorAll(".pet-chat-input, .pet-sentence-stack .pet-sentence").forEach(element => observer.observe(element));
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); observer.disconnect(); };
  }, [snapshot, positions]);
  if (!snapshot?.open || !snapshot.ready) return null;
  const { layout } = snapshot;
  return <main className={`pet-chat-satellite model-${snapshot.model}`} style={{
    "--pet-scale": snapshot.scale, "--dialog-font-scale": snapshot.fontScale,
    "--chat-bubble-width": `${Math.min(snapshot.bubbleWidth, layout.bubbleWidthLimit ?? snapshot.bubbleWidth)}px`,
    width: layout.width, height: layout.height, transform: `scale(${snapshot.scale})`,
  } as CSSProperties}>
    <div ref={measurer} className="satellite-measurer" aria-hidden="true">
      {snapshot.bubbles.map((bubble,index) => <div className="pet-sentence" key={bubble.id}><MessageContent content={bubble.text} mode={snapshot.mode} />{snapshot.proactive && index === snapshot.bubbles.length-1 && <button type="button" className="pet-proactive-reply">聊聊这个话题</button>}</div>)}
    </div>
    <div ref={stack} className={`pet-sentence-stack ${snapshot.fading ? "fading" : ""}`}
      aria-live="polite" onPointerEnter={() => void emitTo("pet", "pet-chat-event", { type: "hover", hovered: true })}
      onPointerLeave={() => void emitTo("pet", "pet-chat-event", { type: "hover", hovered: false })}>
      {snapshot.bubbles.map((bubble, index) => <div className="pet-sentence" key={bubble.id} style={{
        left: positions[index]?.left, top: positions[index]?.top,
        maxWidth: Math.min(snapshot.bubbleWidth, positions[index]?.maxWidth ?? snapshot.bubbleWidth),
        visibility: positions[index]?.visible ? "visible" : "hidden",
      }}><MessageContent content={bubble.text} mode={snapshot.mode} />{snapshot.proactive && index === snapshot.bubbles.length - 1 && <button type="button" className="pet-proactive-reply" onClick={() => void emitTo("pet", "pet-chat-event", { type: "reply-proactive" })}>聊聊这个话题</button>}</div>)}
    </div>
    {snapshot.showInput && <section className="pet-chat-input" style={{ left: layout.inputX, top: layout.inputY }}>
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
    </section>}
  </main>;
}
