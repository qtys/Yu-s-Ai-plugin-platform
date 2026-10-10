import { useEffect, useRef, type ReactNode } from "react";

export default function MessageBubble({ children, disabled, onActions }: { children: ReactNode; disabled: boolean; onActions: () => void }) {
  const hold = useRef<{ timer: number; x: number; y: number } | null>(null);
  function cancel() {
    if (hold.current) window.clearTimeout(hold.current.timer);
    hold.current = null;
  }
  useEffect(() => { if (disabled) cancel(); return cancel; }, [disabled]);
  return <div className="mobile-bubble" tabIndex={disabled ? undefined : 0} aria-label={disabled ? undefined : "消息，长按或按 Enter 打开编辑与撤回"}
    onPointerDown={(event) => {
      cancel();
      if (disabled || event.button !== 0 || (event.target as HTMLElement).closest("a,button,input,textarea")) return;
      hold.current = { x: event.clientX, y: event.clientY, timer: window.setTimeout(() => { hold.current = null; onActions(); }, 500) };
    }}
    onPointerMove={(event) => { if (hold.current && Math.hypot(event.clientX - hold.current.x, event.clientY - hold.current.y) > 10) cancel(); }}
    onPointerUp={cancel} onPointerCancel={cancel} onPointerLeave={cancel}
    onContextMenu={(event) => { if (!disabled) { event.preventDefault(); cancel(); onActions(); } }}
    onKeyDown={(event) => { if (!disabled && event.target === event.currentTarget && (event.key === "Enter" || event.key === "ContextMenu")) { event.preventDefault(); onActions(); } }}>
    {children}
  </div>;
}
