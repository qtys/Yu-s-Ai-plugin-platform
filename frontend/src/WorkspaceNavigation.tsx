import { useEffect, useRef, useState } from "react";

export function WorkspaceIcon({ name }: { name: "chat" | "role" | "recent" | "plugin" | "settings" | "model" | "attach" | "pin" | "drop" | "mic" | "command" | "send" | "stop" | "image" }) {
  const paths = {
    chat: "M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-9.5A8.5 8.5 0 0 1 10.5 4h2A8.5 8.5 0 0 1 21 11.5Z",
    role: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2H4Z",
    recent: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 7v5l3 2",
    plugin: "M9 3H4v6H3a3 3 0 0 0 0 6h1v6h6v-1a3 3 0 0 1 6 0v1h5v-6h-1a3 3 0 0 1 0-6h1V3h-6V2a3 3 0 0 0-6 0v1Z",
    settings: "m9 3 1-1h4l1 1 1 2 2 1 2 1 1 3v4l-1 1-2 1-1 2-1 2-3 1h-4l-1-1-1-2-2-1-2-1-1-3v-4l1-1 2-1 1-2 1-2ZM16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
    model: "M7 7h10v10H7V7ZM10 10h4v4h-4v-4M9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4",
    attach: "m21 11-9 9a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5",
    pin: "m8 3 10 5-3 2-2 5-3-1-5 7 3-8-2-2 4-4-2-4Z",
    drop: "M12 2.5C9.4 6.2 5.5 10.4 5.5 14.2a6.5 6.5 0 0 0 13 0C18.5 10.4 14.6 6.2 12 2.5ZM8.5 14.3a3.5 3.5 0 0 0 3.5 3.4",
    mic: "M15 5a3 3 0 0 0-6 0v7a3 3 0 0 0 6 0V5ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8",
    command: "M9 9V5a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v13a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V9Z",
    send: "M12 20V4M5 11l7-7 7 7",
    stop: "M6 6h12v12H6Z",
    image: "M4 3h16v18H4V3ZM4 16l5-5 4 4 3-3 4 4M16 7h.01",
  };
  return <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

type RecentProps = {
  conversations: { id: number; title: string }[];
  activeConversation: number | null;
  onOpen: (id: number) => void;
  onRename: (id: number) => void;
  onDelete: (id: number) => void;
  onNew: () => void;
  canCreate: boolean;
};

type NavigationProps = {
  panel: "chat" | "characters" | "plugins" | "settings" | "models";
  onPanel: (panel: NavigationProps["panel"]) => void;
};

export default function WorkspaceNavigation({ panel, onPanel }: NavigationProps) {
  return <div className="workspace-topbar">
    <button className="workspace-brand" onClick={() => onPanel("chat")} aria-label="Yu's AI，返回对话"><span className="workspace-logo"><WorkspaceIcon name="drop" /></span><strong>Yu’s AI</strong></button>
    <nav className="workspace-navigation" aria-label="主导航">
      <button className={panel === "chat" ? "active" : ""} aria-current={panel === "chat" ? "page" : undefined} onClick={() => onPanel("chat")}><WorkspaceIcon name="chat" />对话</button>
      <button className={panel === "models" ? "active" : ""} aria-current={panel === "models" ? "page" : undefined} onClick={() => onPanel("models")}><WorkspaceIcon name="model" />模型配置</button>
      <button className={panel === "plugins" || panel === "characters" ? "active" : ""} aria-current={panel === "plugins" || panel === "characters" ? "page" : undefined} onClick={() => onPanel("plugins")}><WorkspaceIcon name="plugin" />插件</button>
      <button className={panel === "settings" ? "active workspace-settings" : "workspace-settings"} aria-current={panel === "settings" ? "page" : undefined} onClick={() => onPanel("settings")} aria-label="设置" title="设置"><WorkspaceIcon name="settings" />设置</button>
    </nav>
  </div>;
}

export function WorkspaceRecent(props: RecentProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const recentRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    function outside(event: PointerEvent) {
      if (!recentRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); }
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  const matching = props.conversations.filter((item) => item.title.toLowerCase().includes(query.toLowerCase()));
  return <div className="workspace-recent" ref={recentRef}>
        <button ref={triggerRef} className={open ? "active" : ""} aria-expanded={open} aria-controls="workspace-history" onClick={() => setOpen((value) => !value)}><WorkspaceIcon name="recent" />最近</button>
        {open && <section className="workspace-history" id="workspace-history" aria-label="最近对话">
          <div className="history-heading"><strong>最近对话</strong><button disabled={!props.canCreate} onClick={() => { setOpen(false); props.onNew(); }} aria-label="新建对话">＋</button></div>
          <input ref={searchRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索对话…" aria-label="搜索最近对话" />
          <div className="history-items">
            {matching.map((item) => <div className={item.id === props.activeConversation ? "history-row active" : "history-row"} key={item.id}>
              <button className="history-open" title={item.title} onClick={() => { setOpen(false); props.onOpen(item.id); }}>{item.title}</button>
              <button className="history-action" aria-label={`重命名：${item.title}`} title="重命名" onClick={() => props.onRename(item.id)}>✎</button>
              <button className="history-action" aria-label={`删除：${item.title}`} title="删除" onClick={() => props.onDelete(item.id)}>×</button>
            </div>)}
            {!matching.length && <p className="history-empty">{query ? "没有找到匹配的对话" : "还没有对话，从新对话开始吧"}</p>}
          </div>
        </section>}
      </div>;
}
