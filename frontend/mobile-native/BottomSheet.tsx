import { useEffect, useRef, type ReactNode } from "react";

export default function BottomSheet({ title, subtitle, onClose, children }: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => { if (event.key === "Escape") onCloseRef.current(); };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);
  return <div className="mobile-sheet-backdrop" onClick={onClose}>
    <section className="mobile-sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
      <div className="mobile-sheet-handle" />
      <header className="mobile-sheet-head"><div><strong>{title}</strong>{subtitle && <small>{subtitle}</small>}</div><button ref={closeRef} type="button" aria-label="关闭抽屉" onClick={onClose}>×</button></header>
      <div className="mobile-sheet-content">{children}</div>
    </section>
  </div>;
}
