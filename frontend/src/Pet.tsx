import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
const WAITING_MESSAGE = "本地服务还没准备好，请稍后再点我。";
const READY_MESSAGE = "点点我，我们来聊天吧。";
type Character = { id: number; name: string };
type Conversation = { id: number; character_id: number; title: string };
type PetState = { position_x: number | null; position_y: number | null };
type TranslationPackage = { from_code: "zh" | "en"; to_code: "zh" | "en"; name: string; size_mb: number; installed: boolean };

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail ?? `请求失败 (${response.status})`);
  }
  return response.json();
}

export default function Pet() {
  const desktop = "__TAURI_INTERNALS__" in window;
  const [open, setOpen] = useState(false);
  const [translationOpen, setTranslationOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [placement, setPlacement] = useState("above-right");
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [reply, setReply] = useState(READY_MESSAGE);
  const [translationPackages, setTranslationPackages] = useState<TranslationPackage[]>([]);
  const [translationSource, setTranslationSource] = useState<"zh" | "en">("zh");
  const [translationInput, setTranslationInput] = useState("");
  const [translationOutput, setTranslationOutput] = useState("");
  const [translationBusy, setTranslationBusy] = useState(false);
  const [character, setCharacter] = useState<Character | null>(null);
  const [showControls, setShowControls] = useState(false);
  const [petSize, setPetSize] = useState(
    () => Number(localStorage.getItem("yus-ai-pet-size")) || 100,
  );
  const [petOpacity, setPetOpacity] = useState(
    () => Number(localStorage.getItem("yus-ai-pet-opacity")) || 100,
  );
  const [dialogFontSize, setDialogFontSize] = useState(
    () => Number(localStorage.getItem("yus-ai-dialog-font-size")) || 100,
  );
  const conversationRef = useRef<number | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const draggingRef = useRef(false);
  const didDragRef = useRef(false);
  const positionRestoredRef = useRef(false);
  const expanded = open || translationOpen;
  const openRef = useRef(expanded);
  const placementRef = useRef(placement);
  const petSizeRef = useRef(petSize);

  useEffect(() => { openRef.current = expanded; }, [expanded]);
  useEffect(() => { placementRef.current = placement; }, [placement]);
  useEffect(() => { petSizeRef.current = petSize; }, [petSize]);

  useEffect(() => {
    let disposed = false;
    let retryTimer: number | undefined;
    async function loadContext() {
      try {
        const [characters, petState] = await Promise.all([
          request<Character[]>("/characters"),
          request<PetState>("/pet/state"),
        ]);
        if (desktop && !positionRestoredRef.current && petState.position_x !== null && petState.position_y !== null) {
          const restoredPlacement = await invoke<string>("set_pet_position", {
            x: petState.position_x,
            y: petState.position_y,
            scale: petSizeRef.current / 100,
          });
          setPlacement(restoredPlacement);
          positionRestoredRef.current = true;
        }
        setReply((value) => value === WAITING_MESSAGE ? READY_MESSAGE : value);
        const preferred = Number(localStorage.getItem("yus-ai-character"));
        const selected = characters.find((item) => item.id === preferred) ?? characters[0] ?? null;
        setCharacter(selected);
        if (!selected) return;
        const conversations = await request<Conversation[]>(`/conversations?character_id=${selected.id}`);
        const preferredConversation = Number(localStorage.getItem("yus-ai-conversation"));
        conversationRef.current = conversations.find((item) => item.id === preferredConversation)?.id ?? conversations[0]?.id ?? null;
      } catch {
        setReply(WAITING_MESSAGE);
        if (!disposed) retryTimer = window.setTimeout(loadContext, 1000);
      }
    }
    void loadContext();
    window.addEventListener("focus", loadContext);
    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      window.removeEventListener("focus", loadContext);
    };
  }, []);

  useEffect(() => {
    if (!desktop) return;
    let saveTimer: number | undefined;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void getCurrentWindow().onMoved(() => {
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(async () => {
        try {
          const position = await invoke<{ x: number; y: number }>("get_pet_position", {
            expanded: openRef.current,
            scale: petSizeRef.current / 100,
            placement: placementRef.current,
          });
          await request("/pet/state", {
            method: "PUT",
            body: JSON.stringify({ position_x: position.x, position_y: position.y }),
          });
        } catch { /* 后端启动或退出期间不阻塞窗口操作 */ }
      }, 300);
    }).then((stop) => { if (disposed) stop(); else unlisten = stop; });
    return () => {
      disposed = true;
      window.clearTimeout(saveTimer);
      unlisten?.();
    };
  }, [desktop]);

  useEffect(() => {
    localStorage.setItem("yus-ai-pet-size", String(petSize));
  }, [petSize]);
  useEffect(() => {
    localStorage.setItem("yus-ai-pet-opacity", String(petOpacity));
  }, [petOpacity]);
  useEffect(() => {
    localStorage.setItem("yus-ai-dialog-font-size", String(dialogFontSize));
  }, [dialogFontSize]);

  useEffect(() => {
    if (!desktop) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void listen<string>("pet-control", (event) => {
      if (event.payload === "size-up" || event.payload === "size-down") {
        setPetSize((current) => {
          const next = Math.max(70, Math.min(125, current + (event.payload === "size-up" ? 5 : -5)));
          void invoke<string>("set_pet_layout", {
            expanded: openRef.current,
            scale: next / 100,
            currentExpanded: openRef.current,
            currentPlacement: placementRef.current,
          }).then(setPlacement);
          return next;
        });
      }
      if (event.payload === "opacity-up") setPetOpacity((current) => Math.min(100, current + 10));
      if (event.payload === "opacity-down") setPetOpacity((current) => Math.max(30, current - 10));
    }).then((stop) => { if (disposed) stop(); else unlisten = stop; });
    return () => { disposed = true; unlisten?.(); };
  }, [desktop]);

  async function toggleBubble() {
    const expanded = !open;
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded,
        scale: petSize / 100,
        currentExpanded: open,
        currentPlacement: placement,
      });
      setPlacement(nextPlacement);
    }
    setOpen(expanded);
    setTranslationOpen(false);
    setMenuOpen(false);
    if (!expanded) setShowControls(false);
  }

  async function toggleTranslation() {
    const nextOpen = !translationOpen;
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded: nextOpen,
        scale: petSize / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
      });
      setPlacement(nextPlacement);
    }
    setTranslationOpen(nextOpen);
    setOpen(false);
    setMenuOpen(false);
    if (nextOpen) {
      try { setTranslationPackages(await request<TranslationPackage[]>("/translation/packages")); }
      catch (error) { setTranslationOutput((error as Error).message); }
    }
  }

  function beginDrag(event: React.PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    dragStartRef.current = { x: event.screenX, y: event.screenY };
    draggingRef.current = false;
    didDragRef.current = false;
  }

  async function continueDrag(event: React.PointerEvent<HTMLButtonElement>) {
    const start = dragStartRef.current;
    if (!desktop || !start || draggingRef.current) return;
    if (Math.hypot(event.screenX - start.x, event.screenY - start.y) < 6) return;
    draggingRef.current = true;
    didDragRef.current = true;
    await invoke("start_pet_drag");
    dragStartRef.current = null;
    window.setTimeout(() => { draggingRef.current = false; }, 0);
  }

  function finishDrag() {
    dragStartRef.current = null;
  }

  function toggleMenu() {
    if (didDragRef.current) {
      didDragRef.current = false;
      return;
    }
    if (!expanded) setMenuOpen((value) => !value);
  }

  function updatePetSize(value: number) {
    setPetSize(value);
    if (desktop)
      void invoke<string>("set_pet_layout", {
        expanded,
        scale: value / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
      }).then(setPlacement);
  }

  async function returnToMain() {
    if (desktop)
      await invoke("set_pet_layout", { expanded: false, scale: petSize / 100, currentExpanded: expanded, currentPlacement: placement });
    setOpen(false);
    setTranslationOpen(false);
    setShowControls(false);
    await invoke("show_main_window");
  }

  async function hidePet() {
    setOpen(false);
    setTranslationOpen(false);
    setMenuOpen(false);
    setShowControls(false);
    if (desktop) await invoke("hide_pet_window");
  }

  async function downloadTranslationPackage() {
    const target = translationSource === "zh" ? "en" : "zh";
    setTranslationBusy(true);
    setTranslationOutput(`正在下载 ${translationSource === "zh" ? "中英" : "英中"}离线语言包……`);
    try {
      await request(`/translation/packages/${translationSource}/${target}`, { method: "POST" });
      setTranslationPackages(await request<TranslationPackage[]>("/translation/packages"));
      setTranslationOutput("语言包安装完成，现在可以离线翻译了。");
    } catch (error) {
      setTranslationOutput((error as Error).message);
    } finally { setTranslationBusy(false); }
  }

  async function runTranslation(event: FormEvent) {
    event.preventDefault();
    const text = translationInput.trim();
    if (!text || translationBusy) return;
    const target = translationSource === "zh" ? "en" : "zh";
    setTranslationBusy(true);
    setTranslationOutput("正在翻译……");
    try {
      const result = await request<{ translation: string }>("/translation", {
        method: "POST",
        body: JSON.stringify({ text, source: translationSource, target }),
      });
      setTranslationOutput(result.translation);
    } catch (error) { setTranslationOutput((error as Error).message); }
    finally { setTranslationBusy(false); }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const content = input.trim();
    if (!content || busy) return;
    if (!character) {
      setReply("请先在主界面创建一个角色。");
      return;
    }
    setInput("");
    setBusy(true);
    setReply("正在想……");
    try {
      let id = conversationRef.current;
      if (!id) {
        const conversation = await request<Conversation>("/conversations", {
          method: "POST",
          body: JSON.stringify({ character_id: character.id }),
        });
        id = conversation.id;
        conversationRef.current = id;
        localStorage.setItem("yus-ai-conversation", String(id));
      }
      const response = await fetch(`${API}/conversations/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail ?? "发送失败");
      }
      if (!response.body) throw new Error("无法读取模型回复");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let complete = "";
      setReply("");
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line) continue;
          const data = JSON.parse(line);
          if (data.error) throw new Error(data.error);
          if (data.token) {
            complete += data.token;
            setReply(complete);
          }
        }
      }
    } catch (error) {
      setReply((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={`pet-stage ${expanded ? "open" : ""} ${placement}`}>
      <div
        className={`pet-canvas ${expanded ? "open" : ""}`}
        style={{ transform: `scale(${petSize / 100})`, "--dialog-font-scale": dialogFontSize / 100 } as React.CSSProperties}
      >
      {open && (
        <section className="speech-bubble">
          <div className="speech-head">
            <strong>{character?.name ?? "蓝雨"}</strong>
            <div className="speech-actions">
              <button onClick={() => setShowControls((value) => !value)}>调整</button>
              <button onClick={returnToMain}>展开</button>
              <button className="close-bubble" onClick={() => void toggleBubble()} aria-label="关闭对话框">×</button>
            </div>
          </div>
          {showControls && (
            <div className="pet-controls">
              <label>大小 <input type="range" min="70" max="125" value={petSize} onChange={(event) => updatePetSize(Number(event.target.value))} /><span>{petSize}%</span></label>
              <label>透明度 <input type="range" min="30" max="100" value={petOpacity} onChange={(event) => setPetOpacity(Number(event.target.value))} /><span>{petOpacity}%</span></label>
              <label>文字 <input type="range" min="80" max="160" step="5" value={dialogFontSize} onChange={(event) => setDialogFontSize(Number(event.target.value))} /><span>{dialogFontSize}%</span></label>
            </div>
          )}
          <div className={`pet-reply ${busy ? "thinking" : ""}`}>{reply}</div>
          <form onSubmit={send}>
            <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="和我说点什么……" autoFocus />
            <button disabled={busy || !input.trim()} aria-label="发送">↑</button>
          </form>
        </section>
      )}
      {translationOpen && (
        <section className="speech-bubble translation-panel">
          <div className="speech-head">
            <strong>离线翻译</strong>
            <div className="speech-actions">
              <button onClick={() => setTranslationSource((value) => value === "zh" ? "en" : "zh")}>⇄ {translationSource === "zh" ? "中 → 英" : "英 → 中"}</button>
              <button className="close-bubble" onClick={() => void toggleTranslation()} aria-label="关闭翻译">×</button>
            </div>
          </div>
          {(() => {
            const target = translationSource === "zh" ? "en" : "zh";
            const model = translationPackages.find((item) => item.from_code === translationSource && item.to_code === target);
            return model && !model.installed ? (
              <button className="download-model" disabled={translationBusy} onClick={() => void downloadTranslationPackage()}>
                下载 {model.name}（约 {model.size_mb} MB）
              </button>
            ) : null;
          })()}
          <form className="translation-form" onSubmit={runTranslation}>
            <textarea value={translationInput} onChange={(event) => setTranslationInput(event.target.value)} placeholder="输入要翻译的内容……" autoFocus />
            <button disabled={translationBusy || !translationInput.trim()}>翻译</button>
          </form>
          <div className={`translation-result ${translationBusy ? "thinking" : ""}`}>{translationOutput || "译文会显示在这里。"}</div>
        </section>
      )}
      <button
        className={`pet-character ${busy ? "thinking" : ""}`}
        style={{ opacity: petOpacity / 100 }}
        onPointerDown={beginDrag}
        onPointerMove={continueDrag}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
        onClick={toggleMenu}
        onDoubleClick={() => { if (open) void toggleBubble(); else if (translationOpen) void toggleTranslation(); }}
        aria-label="蓝色雨滴史莱姆，拖动移动，点击打开功能菜单"
      >
        <img src="/assets/blue-slime-pet.png" alt="蓝色雨滴史莱姆" draggable={false} />
      </button>
      {menuOpen && !expanded && (
        <nav className="pet-plugin-menu" aria-label="桌宠功能">
          <button className="plugin-orb chat-orb" onClick={() => void toggleBubble()}><span>💬</span>对话</button>
          <button className="plugin-orb translate-orb" onClick={() => void toggleTranslation()}><span>译</span>翻译</button>
          <button className="plugin-orb add-orb" disabled title="等待插件接入"><span>＋</span>插件</button>
          <button className="plugin-orb close-orb" onClick={() => void hidePet()}><span>×</span>关闭</button>
        </nav>
      )}
      </div>
    </main>
  );
}
