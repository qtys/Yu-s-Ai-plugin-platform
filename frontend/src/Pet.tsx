import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import MessageContent from "./MessageContent";
import type { MessageDisplayMode } from "./MessageContent";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
const WAITING_MESSAGE = "本地服务还没准备好，请稍后再点我。";
const READY_MESSAGE = "点点我，我们来聊天吧。";
type Character = {
  id: number; name: string; greeting?: string; personality?: string;
  speaking_style?: string; relationship?: string;
};
type Conversation = { id: number; character_id: number; title: string };
type PetState = { position_x: number | null; position_y: number | null };
type DisplaySettings = { message_display_mode: MessageDisplayMode };
type TranslationPackage = { from_code: "zh" | "en"; to_code: "zh" | "en"; name: string; size_mb: number; installed: boolean };
type DownloadProgress = { stage: "testing" | "retrying" | "downloading" | "installing" | "complete" | "error"; percent: number; downloaded?: number; total?: number; error?: string; source?: string; attempt?: number; max_attempts?: number; resumed?: boolean };
type PetFeature = "chat" | "translation" | "settings";
type PetMood = "idle" | "tap" | "happy" | "confused";
type IdleAction = "none" | "squish" | "wiggle" | "sleepy" | "frontflip" | "backflip";
type PetFrame = "normal" | "blink" | "wink" | "surprised";
type DayPeriod = "morning" | "daytime" | "evening" | "night";
const PERIOD_LABELS: Record<DayPeriod, string> = { morning: "早晨", daytime: "白天", evening: "傍晚", night: "深夜" };

function getDayPeriod(date = new Date()): DayPeriod {
  const hour = date.getHours();
  if (hour >= 5 && hour < 10) return "morning";
  if (hour >= 10 && hour < 17) return "daytime";
  if (hour >= 17 && hour < 22) return "evening";
  return "night";
}

function roleAwareGreeting(character: Character | null, period: DayPeriod) {
  const timeLines: Record<DayPeriod, string[]> = {
    morning: ["早上好，新的一天慢慢开始吧。", "醒来了吗？先喝一点水吧。"],
    daytime: ["忙碌的时候，也别忘了让眼睛休息一下。", "坐久了吗？起来活动一下吧。"],
    evening: ["辛苦一天了，记得给自己留一点休息时间。", "晚上好，今天过得怎么样？"],
    night: ["已经很晚了，剩下的事情明天再做也可以。", "夜深了，别让自己太累。"],
  };
  const profile = `${character?.personality ?? ""} ${character?.speaking_style ?? ""}`;
  let ending = "我会待在这里，需要时就叫我。";
  if (/温柔|体贴|治愈|耐心/.test(profile)) ending = "慢慢来，我会陪着你的。";
  else if (/活泼|开朗|元气|可爱/.test(profile)) ending = "打起精神，我们一起加油！";
  else if (/冷静|理性|沉稳|严谨/.test(profile)) ending = "按自己的节奏处理就好。";
  else if (/傲娇/.test(profile)) ending = "我只是顺便提醒你，可别想多了。";
  const relation = character?.relationship ?? "";
  const address = ["主人", "朋友", "搭档", "老师", "同学", "前辈"].find((item) => relation.includes(item));
  const lead = address ? `${address}，` : "";
  const characterGreeting = character?.greeting?.trim().split(/\r?\n/)[0]?.slice(0, 72);
  if (characterGreeting && Math.random() < 0.25) return `${lead}${characterGreeting}`;
  const options = timeLines[period];
  return `${lead}${options[Math.floor(Math.random() * options.length)]}${ending}`;
}

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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [layoutChanging, setLayoutChanging] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [mood, setMood] = useState<PetMood>("idle");
  const [idleAction, setIdleAction] = useState<IdleAction>("none");
  const [petFrame, setPetFrame] = useState<PetFrame>("normal");
  const [gaze, setGaze] = useState({ x: 0, y: 0 });
  const [proactiveMessage, setProactiveMessage] = useState("");
  const [aiProactiveEnabled, setAiProactiveEnabled] = useState<boolean | null>(null);
  const [proactiveSources, setProactiveSources] = useState<{title: string; url: string}[]>([]);
  const proactiveInFlightRef = useRef(false);
  const [pendingProactive, setPendingProactive] = useState<{characterId: number; content: string; sources: {title: string; url: string}[]} | null>(null);
  const pendingProactiveRef = useRef(pendingProactive);
  pendingProactiveRef.current = pendingProactive;
  const proactiveBubbleRef = useRef<HTMLElement | null>(null);
  const [proactiveEnabled, setProactiveEnabled] = useState(
    () => localStorage.getItem("yus-ai-proactive-enabled") !== "false",
  );
  const [timeAwareEnabled, setTimeAwareEnabled] = useState(
    () => localStorage.getItem("yus-ai-time-aware-enabled") !== "false",
  );
  const [roleAwareEnabled, setRoleAwareEnabled] = useState(
    () => localStorage.getItem("yus-ai-role-aware-enabled") !== "false",
  );
  const [dayPeriod, setDayPeriod] = useState<DayPeriod>(() => getDayPeriod());
  const [placement, setPlacement] = useState("above-right");
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [reply, setReply] = useState(READY_MESSAGE);
  const [translationPackages, setTranslationPackages] = useState<TranslationPackage[]>([]);
  const [translationSource, setTranslationSource] = useState<"zh" | "en">("zh");
  const [translationInput, setTranslationInput] = useState("");
  const [translationOutput, setTranslationOutput] = useState("");
  const [translationBusy, setTranslationBusy] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null);
  const [continuousTranslation, setContinuousTranslation] = useState(false);
  const [character, setCharacter] = useState<Character | null>(null);
  const proactiveContextRef = useRef({ character, expanded: false, menuOpen: false, busy: false, dragging: false });
  proactiveContextRef.current = { character, expanded: open || translationOpen || settingsOpen, menuOpen, busy, dragging };
  const [messageDisplayMode, setMessageDisplayMode] = useState<MessageDisplayMode>("markdown");
  const [petSize, setPetSize] = useState(
    () => Number(localStorage.getItem("yus-ai-pet-size")) || 100,
  );
  const [petOpacity, setPetOpacity] = useState(
    () => Number(localStorage.getItem("yus-ai-pet-opacity")) || 100,
  );
  const [dialogFontSize, setDialogFontSize] = useState(
    () => Number(localStorage.getItem("yus-ai-dialog-font-size")) || 100,
  );
  const [dialogWidth, setDialogWidth] = useState(
    () => Number(localStorage.getItem("yus-ai-dialog-width")) || 430,
  );
  const [dialogHeight, setDialogHeight] = useState(
    () => Number(localStorage.getItem("yus-ai-dialog-height")) || 520,
  );
  const conversationRef = useRef<number | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const draggingRef = useRef(false);
  const didDragRef = useRef(false);
  const positionRestoredRef = useRef(false);
  const startupShownRef = useRef(false);
  const continuousTranslationRef = useRef(false);
  const translationSequenceRef = useRef(0);
  const menuClickTimerRef = useRef<number | undefined>(undefined);
  const lastFeatureRef = useRef<PetFeature>(
    (localStorage.getItem("yus-ai-last-pet-feature") as PetFeature | null) ?? "chat",
  );
  const expanded = open || translationOpen || settingsOpen;
  const openRef = useRef(expanded);
  const placementRef = useRef(placement);
  const petSizeRef = useRef(petSize);
  const dialogWidthRef = useRef(dialogWidth);
  const dialogHeightRef = useRef(dialogHeight);
  const moodTimerRef = useRef<number | undefined>(undefined);
  const frameTimerRef = useRef<number | undefined>(undefined);
  const interactionCountRef = useRef(0);
  const dialogResizeTimerRef = useRef<number | undefined>(undefined);
  const dialogResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const dialogHeightResizeRef = useRef<{ startY: number; startHeight: number } | null>(null);

  useEffect(() => { openRef.current = expanded; }, [expanded]);
  useEffect(() => { placementRef.current = placement; }, [placement]);
  useEffect(() => { petSizeRef.current = petSize; }, [petSize]);
  useEffect(() => { dialogWidthRef.current = dialogWidth; }, [dialogWidth]);
  useEffect(() => { dialogHeightRef.current = dialogHeight; }, [dialogHeight]);
  useEffect(() => { continuousTranslationRef.current = continuousTranslation; }, [continuousTranslation]);
  useEffect(() => {
    if (desktop) void invoke("set_pet_interaction_mode", {
      mode: expanded ? 2 : menuOpen ? 1 : proactiveMessage ? 3 : 0,
      alignLeft: placement.endsWith("left"),
      proactiveHeight: Math.ceil((proactiveBubbleRef.current?.getBoundingClientRect().height ?? 0) / (petSize / 100)),
    });
  }, [desktop, expanded, menuOpen, placement, proactiveMessage, petSize, proactiveSources]);
  useEffect(() => () => {
    window.clearTimeout(menuClickTimerRef.current);
    window.clearTimeout(dialogResizeTimerRef.current);
    window.clearTimeout(frameTimerRef.current);
  }, []);
  useEffect(() => { localStorage.setItem("yus-ai-proactive-enabled", String(proactiveEnabled)); }, [proactiveEnabled]);
  useEffect(() => { localStorage.setItem("yus-ai-time-aware-enabled", String(timeAwareEnabled)); }, [timeAwareEnabled]);
  useEffect(() => { localStorage.setItem("yus-ai-role-aware-enabled", String(roleAwareEnabled)); }, [roleAwareEnabled]);
  useEffect(() => {
    if (!proactiveMessage) return;
    const timer = window.setTimeout(() => setProactiveMessage(""), 30000);
    return () => window.clearTimeout(timer);
  }, [proactiveMessage]);
  useEffect(() => {
    const timer = window.setInterval(() => setDayPeriod(getDayPeriod()), 60000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (expanded || dragging || busy) return;
    const actions: IdleAction[] = dayPeriod === "night"
      ? ["sleepy", "sleepy", "squish"]
      : ["squish", "wiggle", "sleepy", "frontflip", "backflip"];
    const timer = window.setInterval(() => {
      const action = actions[Math.floor(Math.random() * actions.length)];
      setIdleAction(action);
      window.setTimeout(() => setIdleAction("none"), action === "sleepy" ? 2400 : action.endsWith("flip") ? 950 : 1100);
    }, 11000);
    return () => window.clearInterval(timer);
  }, [expanded, dragging, busy, dayPeriod]);

  useEffect(() => {
    ["blink", "wink", "surprised", "frontflip", "backflip"].forEach((frame) => {
      const image = new Image();
      image.src = `/assets/blue-slime-pet-${frame}.png`;
    });
  }, []);

  useEffect(() => {
    if (expanded || dragging || busy) return;
    let timer: number;
    const scheduleBlink = () => {
      timer = window.setTimeout(() => {
        setPetFrame("blink");
        window.clearTimeout(frameTimerRef.current);
        frameTimerRef.current = window.setTimeout(() => {
          setPetFrame("normal");
          scheduleBlink();
        }, 145);
      }, 3200 + Math.random() * 4200);
    };
    scheduleBlink();
    return () => window.clearTimeout(timer);
  }, [expanded, dragging, busy]);

  useEffect(() => {
    if (!proactiveEnabled || aiProactiveEnabled !== false || expanded || (timeAwareEnabled && dayPeriod === "night")) return;
    const showGreeting = () => {
      setProactiveSources([]);
      setProactiveMessage(roleAwareGreeting(roleAwareEnabled ? character : null, timeAwareEnabled ? dayPeriod : "daytime"));
      window.setTimeout(() => setProactiveMessage(""), 9000);
    };
    const today = new Date().toISOString().slice(0, 10);
    const greetingKey = `${today}-${dayPeriod}-${character?.id ?? 0}`;
    const greeted = localStorage.getItem("yus-ai-last-time-greeting") === greetingKey;
    const first = greeted ? undefined : window.setTimeout(() => {
      localStorage.setItem("yus-ai-last-time-greeting", greetingKey);
      showGreeting();
    }, 15000);
    const recurring = window.setInterval(showGreeting, 30 * 60 * 1000);
    return () => { window.clearTimeout(first); window.clearInterval(recurring); };
  }, [proactiveEnabled, aiProactiveEnabled, timeAwareEnabled, roleAwareEnabled, dayPeriod, character, expanded]);

  useEffect(() => {
    let disposed = false;
    const check = async () => {
      if (proactiveInFlightRef.current || pendingProactiveRef.current) return false;
      proactiveInFlightRef.current = true;
      try {
        const config = await request<{enabled: boolean}>("/plugins/proactive");
        if (disposed) return false;
        setAiProactiveEnabled(config.enabled);
        const { character, expanded, menuOpen, busy, dragging } = proactiveContextRef.current;
        if (!config.enabled || !character || expanded || menuOpen || busy || dragging || (!desktop && document.hidden) || (desktop && !(await getCurrentWindow().isVisible()))) return false;
        const result = await request<{skipped?: boolean; content: string; conversation_id: number; sources: {title: string; url: string}[]}>("/plugins/proactive/generate", { method: "POST", body: JSON.stringify({ character_id: character.id, conversation_id: conversationRef.current }) });
        if (result.skipped) return true;
        if (!disposed && proactiveContextRef.current.character?.id === character.id) {
          conversationRef.current = result.conversation_id;
          localStorage.setItem("yus-ai-conversation", String(result.conversation_id));
          setPendingProactive({ characterId: character.id, content: result.content, sources: result.sources });
        }
        return true;
      } catch (error) {
        console.warn("主动发言暂时失败", error);
        return true;
      }
      finally { proactiveInFlightRef.current = false; }
    };
    let timer: number;
    const schedule = async () => {
      const checked = await check();
      if (disposed) return;
      try {
        const config = await request<{enabled: boolean; next_due: number}>("/plugins/proactive");
        const untilDue = config.next_due > 0 ? config.next_due * 1000 - Date.now() : 30000;
        const delay = checked ? Math.max(1000, Math.min(60000, untilDue + 150)) : 15000;
        timer = window.setTimeout(() => void schedule(), delay);
      } catch {
        timer = window.setTimeout(() => void schedule(), 30000);
      }
    };
    timer = window.setTimeout(() => void schedule(), 1000);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [desktop]);

  useEffect(() => {
    if (!pendingProactive || expanded || menuOpen || busy || dragging) return;
    if (pendingProactive.characterId === character?.id) {
      setProactiveSources(pendingProactive.sources);
      setProactiveMessage(pendingProactive.content);
    }
    setPendingProactive(null);
  }, [pendingProactive, character?.id, expanded, menuOpen, busy, dragging]);

  useEffect(() => {
    let disposed = false;
    let retryTimer: number | undefined;
    async function loadContext() {
      try {
        const [characters, petState, displaySettings, proactiveConfig] = await Promise.all([
          request<Character[]>("/characters"),
          request<PetState>("/pet/state"),
          request<DisplaySettings>("/settings"),
          request<{enabled: boolean}>("/plugins/proactive"),
        ]);
        setAiProactiveEnabled(proactiveConfig.enabled);
        setMessageDisplayMode(displaySettings.message_display_mode);
        if (desktop && !positionRestoredRef.current) {
          if (petState.position_x !== null && petState.position_y !== null) {
            const restoredPlacement = await invoke<string>("set_pet_position", {
              x: petState.position_x,
              y: petState.position_y,
              scale: petSizeRef.current / 100,
            });
            setPlacement(restoredPlacement);
          }
          positionRestoredRef.current = true;
          if (!startupShownRef.current) {
            startupShownRef.current = true;
            await invoke("show_pet_window");
          }
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
      if (!positionRestoredRef.current) return;
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
    localStorage.setItem("yus-ai-dialog-width", String(dialogWidth));
  }, [dialogWidth]);
  useEffect(() => {
    localStorage.setItem("yus-ai-dialog-height", String(dialogHeight));
  }, [dialogHeight]);

  useEffect(() => {
    if (!desktop) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    let unlistenReset: (() => void) | undefined;
    let unlistenSelection: (() => void) | undefined;
    void listen<string>("pet-control", (event) => {
      if (event.payload === "size-up" || event.payload === "size-down") {
        setPetSize((current) => {
          const next = Math.max(70, Math.min(125, current + (event.payload === "size-up" ? 5 : -5)));
          void invoke<string>("set_pet_layout", {
            expanded: openRef.current,
            scale: next / 100,
            currentExpanded: openRef.current,
            currentPlacement: placementRef.current,
            dialogWidth: dialogWidthRef.current,
            dialogHeight: dialogHeightRef.current,
          }).then(setPlacement);
          return next;
        });
      }
      if (event.payload === "opacity-up") setPetOpacity((current) => Math.min(100, current + 10));
      if (event.payload === "opacity-down") setPetOpacity((current) => Math.max(30, current - 10));
    }).then((stop) => { if (disposed) stop(); else unlisten = stop; });
    void listen("pet-reset", () => {
      continuousTranslationRef.current = false;
      setContinuousTranslation(false);
      void invoke("set_continuous_translation", { enabled: false });
      if (openRef.current) {
        void invoke<string>("set_pet_layout", {
          expanded: false,
          scale: petSizeRef.current / 100,
          currentExpanded: true,
          currentPlacement: placementRef.current,
          dialogWidth: dialogWidthRef.current,
          dialogHeight: dialogHeightRef.current,
        }).then(setPlacement);
      }
      setOpen(false);
      setTranslationOpen(false);
      setSettingsOpen(false);
      setMenuOpen(false);
    }).then((stop) => { if (disposed) stop(); else unlistenReset = stop; });
    void listen<string>("screen-text-selected", (event) => {
      if (!continuousTranslationRef.current || document.hasFocus()) return;
      const text = event.payload.trim();
      if (!text) return;
      const source: "zh" | "en" = /[\u3400-\u9fff]/.test(text) ? "zh" : "en";
      const target = source === "zh" ? "en" : "zh";
      const sequence = ++translationSequenceRef.current;
      setTranslationSource(source);
      setTranslationInput(text);
      setTranslationBusy(true);
      setTranslationOutput("已捕获选中文本，正在翻译……");
      void request<{ translation: string }>("/translation", {
        method: "POST",
        body: JSON.stringify({ text, source, target }),
      }).then((result) => {
        if (sequence === translationSequenceRef.current) setTranslationOutput(result.translation);
      }).catch((error) => {
        if (sequence === translationSequenceRef.current) setTranslationOutput((error as Error).message);
      }).finally(() => {
        if (sequence === translationSequenceRef.current) setTranslationBusy(false);
      });
    }).then((stop) => { if (disposed) stop(); else unlistenSelection = stop; });
    return () => {
      disposed = true;
      void invoke("set_continuous_translation", { enabled: false });
      unlisten?.(); unlistenReset?.(); unlistenSelection?.();
    };
  }, [desktop]);

  async function beginLayoutChange() {
    if (!desktop) return;
    setLayoutChanging(true);
    await new Promise((resolve) => window.setTimeout(resolve, 85));
  }

  function finishLayoutChange() {
    if (!desktop) return;
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => setLayoutChanging(false)));
  }

  function rememberFeature(feature: PetFeature) {
    lastFeatureRef.current = feature;
    localStorage.setItem("yus-ai-last-pet-feature", feature);
  }

  function showMood(nextMood: PetMood, duration = 900) {
    window.clearTimeout(moodTimerRef.current);
    setMood(nextMood);
    moodTimerRef.current = window.setTimeout(() => setMood("idle"), duration);
  }

  function showPetFrame(frame: PetFrame, duration = 650) {
    window.clearTimeout(frameTimerRef.current);
    setPetFrame(frame);
    frameTimerRef.current = window.setTimeout(() => setPetFrame("normal"), duration);
  }

  async function toggleBubble() {
    const nextOpen = !open;
    if (nextOpen) rememberFeature("chat");
    await beginLayoutChange();
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded: nextOpen,
        scale: petSize / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
        dialogWidth,
        dialogHeight,
      });
      setPlacement(nextPlacement);
    }
    setOpen(nextOpen);
    setTranslationOpen(false);
    setSettingsOpen(false);
    setMenuOpen(false);
    finishLayoutChange();
  }

  async function toggleTranslation() {
    const nextOpen = !translationOpen;
    if (nextOpen) rememberFeature("translation");
    if (!nextOpen && continuousTranslationRef.current) {
      continuousTranslationRef.current = false;
      setContinuousTranslation(false);
      if (desktop) await invoke("set_continuous_translation", { enabled: false });
    }
    await beginLayoutChange();
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded: nextOpen,
        scale: petSize / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
        dialogWidth,
        dialogHeight,
      });
      setPlacement(nextPlacement);
    }
    setTranslationOpen(nextOpen);
    setOpen(false);
    setSettingsOpen(false);
    setMenuOpen(false);
    finishLayoutChange();
    if (nextOpen) {
      try { setTranslationPackages(await request<TranslationPackage[]>("/translation/packages")); }
      catch (error) { setTranslationOutput((error as Error).message); }
    }
  }

  async function toggleSettings() {
    const nextOpen = !settingsOpen;
    if (nextOpen) rememberFeature("settings");
    await beginLayoutChange();
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded: nextOpen,
        scale: petSize / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
        dialogWidth,
        dialogHeight,
      });
      setPlacement(nextPlacement);
    }
    setSettingsOpen(nextOpen);
    setOpen(false);
    setTranslationOpen(false);
    setMenuOpen(false);
    finishLayoutChange();
  }

  function beginDrag(event: React.PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    dragStartRef.current = { x: event.screenX, y: event.screenY };
    draggingRef.current = false;
    didDragRef.current = false;
    showMood("tap", 500);
    interactionCountRef.current += 1;
    showPetFrame(interactionCountRef.current % 4 === 0 ? "surprised" : "wink", 620);
  }

  async function continueDrag(event: React.PointerEvent<HTMLButtonElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    setGaze({
      x: Math.max(-1, Math.min(1, (event.clientX - bounds.left) / bounds.width * 2 - 1)),
      y: Math.max(-1, Math.min(1, (event.clientY - bounds.top) / bounds.height * 2 - 1)),
    });
    const start = dragStartRef.current;
    if (!desktop || !start || draggingRef.current) return;
    if (Math.hypot(event.screenX - start.x, event.screenY - start.y) < 6) return;
    draggingRef.current = true;
    didDragRef.current = true;
    setDragging(true);
    try {
      await invoke("start_pet_drag");
      const snappedPlacement = await invoke<string>("snap_pet_to_edge", {
        threshold: 42,
        expanded,
        currentPlacement: placement,
      });
      setPlacement(snappedPlacement);
      showMood("happy", 900);
    } finally {
      dragStartRef.current = null;
      setDragging(false);
      window.setTimeout(() => { draggingRef.current = false; }, 0);
    }
  }

  function finishDrag() {
    dragStartRef.current = null;
  }

  function stopLooking() {
    setGaze({ x: 0, y: 0 });
  }

  function toggleMenu() {
    if (didDragRef.current) {
      didDragRef.current = false;
      return;
    }
    if (expanded) {
      window.clearTimeout(menuClickTimerRef.current);
      if (open) void toggleBubble();
      else if (translationOpen) void toggleTranslation();
      else if (settingsOpen) void toggleSettings();
    } else {
      window.clearTimeout(menuClickTimerRef.current);
      menuClickTimerRef.current = window.setTimeout(() => setMenuOpen((value) => !value), 220);
    }
  }

  function openLastFeature() {
    if (expanded || didDragRef.current) return;
    window.clearTimeout(menuClickTimerRef.current);
    setMenuOpen(false);
    if (lastFeatureRef.current === "translation") void toggleTranslation();
    else if (lastFeatureRef.current === "settings") void toggleSettings();
    else void toggleBubble();
  }

  function updatePetSize(value: number) {
    setPetSize(value);
    if (desktop)
      void invoke<string>("set_pet_layout", {
        expanded,
        scale: value / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
        dialogWidth,
        dialogHeight,
      }).then(setPlacement);
  }

  async function returnToMain() {
    continuousTranslationRef.current = false;
    setContinuousTranslation(false);
    if (desktop) await invoke("set_continuous_translation", { enabled: false });
    if (desktop)
      await invoke("set_pet_layout", { expanded: false, scale: petSize / 100, currentExpanded: expanded, currentPlacement: placement, dialogWidth, dialogHeight });
    setOpen(false);
    setTranslationOpen(false);
    setSettingsOpen(false);
    await invoke("show_main_window");
  }

  async function hidePet() {
    continuousTranslationRef.current = false;
    setContinuousTranslation(false);
    if (desktop) await invoke("set_continuous_translation", { enabled: false });
    setOpen(false);
    setTranslationOpen(false);
    setSettingsOpen(false);
    setMenuOpen(false);
    if (desktop) await invoke("hide_pet_window");
  }

  async function downloadTranslationPackage() {
    const pairs = [
      { source: "zh", target: "en", label: "中译英" },
      { source: "en", target: "zh", label: "英译中" },
    ];
    setTranslationBusy(true);
    setDownloadProgress({ stage: "downloading", percent: 0 });
    setTranslationOutput("正在测速并下载中英双向离线语言包……");
    try {
      for (let pairIndex = 0; pairIndex < pairs.length; pairIndex += 1) {
        const pair = pairs[pairIndex];
        setTranslationOutput(`正在处理 ${pair.label}语言包（${pairIndex + 1}/2）……`);
        const response = await fetch(`${API}/translation/packages/${pair.source}/${pair.target}/stream`, { method: "POST" });
        if (!response.ok || !response.body) throw new Error(`${pair.label}语言包下载失败 (${response.status})`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (!line.trim()) continue;
            const progress = JSON.parse(line) as DownloadProgress;
            const overall = Math.round((pairIndex * 100 + progress.percent) / pairs.length);
            setDownloadProgress({ ...progress, percent: overall });
            if (progress.stage === "error") throw new Error(`${pair.label}：${progress.error ?? "语言包安装失败"}`);
            if (progress.stage === "installing") setTranslationOutput(`${pair.label}下载完成，正在安装……`);
          }
        }
      }
      const refreshed = await request<TranslationPackage[]>(`/translation/packages?refresh=${Date.now()}`);
      setTranslationPackages(refreshed);
      setDownloadProgress({ stage: "complete", percent: 100 });
      setTranslationOutput("中译英和英译中语言包均已安装，现在可以双向离线翻译了。");
    } catch (error) {
      setDownloadProgress((value) => ({ stage: "error", percent: value?.percent ?? 0, error: (error as Error).message }));
      setTranslationOutput((error as Error).message);
    } finally { setTranslationBusy(false); }
  }

  function updateDialogWidth(value: number) {
    setDialogWidth(value);
    dialogWidthRef.current = value;
    if (!desktop || !expanded) return;
    if (dialogResizeTimerRef.current !== undefined) return;
    dialogResizeTimerRef.current = window.setTimeout(() => {
      dialogResizeTimerRef.current = undefined;
      void invoke<string>("resize_pet_dialog", {
        width: dialogWidthRef.current,
        height: dialogHeightRef.current,
        scale: petSizeRef.current / 100,
        placement: placementRef.current,
      }).then(setPlacement);
    }, 32);
  }

  function beginDialogResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dialogResizeRef.current = { startX: event.screenX, startWidth: dialogWidth };
  }

  function continueDialogResize(event: React.PointerEvent<HTMLDivElement>) {
    const resize = dialogResizeRef.current;
    if (!resize) return;
    const direction = placement.endsWith("left") ? 1 : -1;
    const nextWidth = Math.round(Math.max(430, Math.min(720, resize.startWidth + (event.screenX - resize.startX) * direction)) / 10) * 10;
    if (nextWidth !== dialogWidthRef.current) updateDialogWidth(nextWidth);
  }

  function finishDialogResize() {
    dialogResizeRef.current = null;
  }

  function updateDialogHeight(value: number) {
    setDialogHeight(value);
    dialogHeightRef.current = value;
    if (!desktop || !expanded) return;
    if (dialogResizeTimerRef.current !== undefined) return;
    dialogResizeTimerRef.current = window.setTimeout(() => {
      dialogResizeTimerRef.current = undefined;
      void invoke<string>("resize_pet_dialog", {
        width: dialogWidthRef.current,
        height: dialogHeightRef.current,
        scale: petSizeRef.current / 100,
        placement: placementRef.current,
      }).then(setPlacement);
    }, 32);
  }

  function beginDialogHeightResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dialogHeightResizeRef.current = { startY: event.screenY, startHeight: dialogHeight };
  }

  function continueDialogHeightResize(event: React.PointerEvent<HTMLDivElement>) {
    const resize = dialogHeightResizeRef.current;
    if (!resize) return;
    const direction = placement.startsWith("below") ? 1 : -1;
    const nextHeight = Math.round(Math.max(520, Math.min(760, resize.startHeight + (event.screenY - resize.startY) * direction)) / 10) * 10;
    if (nextHeight !== dialogHeightRef.current) updateDialogHeight(nextHeight);
  }

  function finishDialogHeightResize() {
    dialogHeightResizeRef.current = null;
  }

  async function toggleContinuousTranslation() {
    if (!desktop) {
      setTranslationOutput("连续翻译仅在 Windows 桌面版中可用。");
      return;
    }
    const enabled = !continuousTranslationRef.current;
    try {
      await invoke("set_continuous_translation", { enabled });
      continuousTranslationRef.current = enabled;
      setContinuousTranslation(enabled);
      setTranslationOutput(enabled
        ? "连续翻译已开启：在其他窗口中用鼠标选中文本，译文会自动显示在这里。"
        : "连续翻译已关闭。");
    } catch (error) { setTranslationOutput(`无法切换连续翻译：${String(error)}`); }
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
      showMood("happy", 1200);
    } catch (error) { setTranslationOutput((error as Error).message); showMood("confused", 1400); }
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
      buffer += decoder.decode();
      if (buffer.trim()) {
        const data = JSON.parse(buffer);
        if (data.error) throw new Error(data.error);
        if (data.token) { complete += data.token; setReply(complete); }
      }
      showMood("happy", 1800);
    } catch (error) {
      setReply((error as Error).message);
      showMood("confused", 1800);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={`pet-stage ${expanded ? "open" : ""} ${placement} period-${dayPeriod}`}>
      <div
        className={`pet-canvas ${expanded ? "open" : ""} ${layoutChanging ? "layout-changing" : ""}`}
        style={{ transform: `scale(${petSize / 100})`, "--dialog-font-scale": dialogFontSize / 100, "--dialog-width": `${dialogWidth}px`, "--dialog-height": `${dialogHeight}px` } as React.CSSProperties}
      >
      {open && (
        <section className="speech-bubble">
          <div className="dialog-resize-handle" role="separator" aria-label="拖动调整对话框宽度" onPointerDown={beginDialogResize} onPointerMove={continueDialogResize} onPointerUp={finishDialogResize} onPointerCancel={finishDialogResize} />
          <div className="dialog-height-handle" role="separator" aria-label="拖动调整对话框高度" onPointerDown={beginDialogHeightResize} onPointerMove={continueDialogHeightResize} onPointerUp={finishDialogHeightResize} onPointerCancel={finishDialogHeightResize} />
          <div className="speech-head">
            <strong>{character?.name ?? "蓝雨"}</strong>
            <div className="speech-actions">
              <button onClick={returnToMain}>展开</button>
              <button className="close-bubble" onClick={() => void toggleBubble()} aria-label="关闭对话框">×</button>
            </div>
          </div>
          <div className={`pet-reply ${busy ? "thinking" : ""}`}>
            <MessageContent content={reply} mode={messageDisplayMode} />
          </div>
          <form onSubmit={send}>
            <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="和我说点什么……" autoFocus />
            <button disabled={busy || !input.trim()} aria-label="发送">↑</button>
          </form>
        </section>
      )}
      {translationOpen && (
        <section className="speech-bubble translation-panel">
          <div className="dialog-resize-handle" role="separator" aria-label="拖动调整翻译框宽度" onPointerDown={beginDialogResize} onPointerMove={continueDialogResize} onPointerUp={finishDialogResize} onPointerCancel={finishDialogResize} />
          <div className="dialog-height-handle" role="separator" aria-label="拖动调整翻译框高度" onPointerDown={beginDialogHeightResize} onPointerMove={continueDialogHeightResize} onPointerUp={finishDialogHeightResize} onPointerCancel={finishDialogHeightResize} />
          <div className="speech-head">
            <strong>离线翻译</strong>
            <div className="speech-actions">
              <button className={continuousTranslation ? "continuous active" : "continuous"} onClick={() => void toggleContinuousTranslation()}>
                {continuousTranslation ? "● 连续" : "○ 连续"}
              </button>
              <button onClick={() => setTranslationSource((value) => value === "zh" ? "en" : "zh")}>⇄ {translationSource === "zh" ? "中 → 英" : "英 → 中"}</button>
              <button className="close-bubble" onClick={() => void toggleTranslation()} aria-label="关闭翻译">×</button>
            </div>
          </div>
          {(() => {
            const missing = translationPackages.filter((item) => !item.installed);
            const totalSize = missing.reduce((total, item) => total + item.size_mb, 0);
            return missing.length ? (
              <div className="download-area">
                <button className="download-model" disabled={translationBusy} onClick={() => void downloadTranslationPackage()}>
                  {translationBusy ? "正在下载双向语言包…" : `一键下载中英双向语言包（约 ${totalSize} MB）`}
                </button>
                {downloadProgress && (
                  <div className={`download-progress ${downloadProgress.stage}`}>
                    <div className="download-progress-track"><span style={{ width: `${downloadProgress.percent}%` }} /></div>
                    <small>{downloadProgress.stage === "testing" ? `正在测速${downloadProgress.source ? ` · ${downloadProgress.source}` : ""}` : downloadProgress.stage === "retrying" ? `正在重试 ${downloadProgress.attempt ?? ""}/${downloadProgress.max_attempts ?? 3}${downloadProgress.source ? ` · ${downloadProgress.source}` : ""}` : downloadProgress.stage === "installing" ? "正在安装" : downloadProgress.stage === "error" ? "下载失败" : downloadProgress.stage === "complete" ? "安装完成" : `${downloadProgress.resumed ? "续传" : "下载"} ${downloadProgress.percent}%${downloadProgress.source ? ` · ${downloadProgress.source}` : ""}`}</small>
                  </div>
                )}
              </div>
            ) : null;
          })()}
          <form className="translation-form" onSubmit={runTranslation}>
            <textarea value={translationInput} onChange={(event) => setTranslationInput(event.target.value)} placeholder="输入要翻译的内容……" autoFocus />
            <button disabled={translationBusy || !translationInput.trim()}>翻译</button>
          </form>
          <div className={`translation-result ${translationBusy ? "thinking" : ""}`}>{translationOutput || "译文会显示在这里。"}</div>
        </section>
      )}
      {settingsOpen && (
        <section className="speech-bubble settings-panel">
          <div className="dialog-resize-handle" role="separator" aria-label="拖动调整设置框宽度" onPointerDown={beginDialogResize} onPointerMove={continueDialogResize} onPointerUp={finishDialogResize} onPointerCancel={finishDialogResize} />
          <div className="dialog-height-handle" role="separator" aria-label="拖动调整设置框高度" onPointerDown={beginDialogHeightResize} onPointerMove={continueDialogHeightResize} onPointerUp={finishDialogHeightResize} onPointerCancel={finishDialogHeightResize} />
          <div className="speech-head">
            <strong>桌宠设置</strong>
            <button className="close-bubble" onClick={() => void toggleSettings()} aria-label="关闭设置">×</button>
          </div>
          <div className="pet-controls">
            <div className="pet-action-controls">
              <button type="button" disabled={idleAction.endsWith("flip")} onClick={() => { setIdleAction("frontflip"); window.setTimeout(() => setIdleAction("none"), 950); }}>前空翻</button>
              <button type="button" disabled={idleAction.endsWith("flip")} onClick={() => { setIdleAction("backflip"); window.setTimeout(() => setIdleAction("none"), 950); }}>后空翻</button>
            </div>
            <label>桌宠大小 <input type="range" min="70" max="125" value={petSize} onChange={(event) => updatePetSize(Number(event.target.value))} /><span>{petSize}%</span></label>
            <label>透明度 <input type="range" min="30" max="100" value={petOpacity} onChange={(event) => setPetOpacity(Number(event.target.value))} /><span>{petOpacity}%</span></label>
            <label>对话文字 <input type="range" min="80" max="160" step="5" value={dialogFontSize} onChange={(event) => setDialogFontSize(Number(event.target.value))} /><span>{dialogFontSize}%</span></label>
            <label>对话框宽度 <input type="range" min="430" max="720" step="10" value={dialogWidth} onChange={(event) => updateDialogWidth(Number(event.target.value))} /><span>{dialogWidth}px</span></label>
            <label>对话框高度 <input type="range" min="520" max="760" step="10" value={dialogHeight} onChange={(event) => updateDialogHeight(Number(event.target.value))} /><span>{dialogHeight}px</span></label>
            <label className="proactive-toggle">本地问候 <input type="checkbox" checked={proactiveEnabled} onChange={(event) => setProactiveEnabled(event.target.checked)} /><span>{proactiveEnabled ? "开启" : "关闭"}</span></label>
            <label className="proactive-toggle">时间感知 <input type="checkbox" checked={timeAwareEnabled} onChange={(event) => setTimeAwareEnabled(event.target.checked)} /><span>{timeAwareEnabled ? "开启" : "关闭"}</span></label>
            <label className="proactive-toggle">角色台词 <input type="checkbox" checked={roleAwareEnabled} onChange={(event) => setRoleAwareEnabled(event.target.checked)} /><span>{roleAwareEnabled ? "开启" : "关闭"}</span></label>
          </div>
        </section>
      )}
      {proactiveMessage && !expanded && !menuOpen && (
        <aside ref={proactiveBubbleRef} className="proactive-bubble" aria-live="polite">
          <button onClick={() => setProactiveMessage("")} aria-label="关闭主动提醒">×</button>
          <small>{PERIOD_LABELS[dayPeriod]} · {character?.name ?? "蓝雨"}</small>
          {proactiveMessage}
          {proactiveSources.length > 0 && <small>新闻参考来源已附在对话记录中</small>}
          <a href="#" onClick={(event) => { event.preventDefault(); setReply(proactiveMessage); setProactiveMessage(""); void toggleBubble(); }}>聊聊这个话题</a>
        </aside>
      )}
      <button
        className={`pet-character ${busy ? "thinking" : ""} mood-${mood} idle-${idleAction} ${dragging ? "dragging" : ""}`}
        style={{ opacity: petOpacity / 100, "--gaze-x": gaze.x, "--gaze-y": gaze.y } as React.CSSProperties}
        onPointerDown={beginDrag}
        onPointerMove={continueDrag}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
        onPointerLeave={stopLooking}
        onClick={toggleMenu}
        onDoubleClick={() => { if (!expanded) openLastFeature(); }}
        aria-label="蓝色雨滴史莱姆，拖动移动，点击打开功能菜单"
      >
        <img src={`/assets/blue-slime-pet${idleAction.endsWith("flip") ? `-${idleAction}` : petFrame === "normal" ? "" : `-${petFrame}`}.png`} alt="蓝色雨滴史莱姆" draggable={false} />
        <span className="pet-ripple" />
        <span className="pet-emote" aria-hidden="true">{busy ? "…" : mood === "happy" ? "♥" : mood === "confused" ? "?" : idleAction === "sleepy" ? "Zzz" : dayPeriod === "night" ? "☾" : ""}</span>
      </button>
      {menuOpen && !expanded && (
        <nav className="pet-plugin-menu" aria-label="桌宠功能">
          <button className="plugin-orb chat-orb" onClick={() => void toggleBubble()}><span>💬</span>对话</button>
          <button className="plugin-orb translate-orb" onClick={() => void toggleTranslation()}><span>译</span>翻译</button>
          <button className="plugin-orb settings-orb" onClick={() => void toggleSettings()}><span>⚙</span>设置</button>
          <button className="plugin-orb add-orb" disabled title="等待插件接入"><span>＋</span>插件</button>
          <button className="plugin-orb close-orb" onClick={() => void hidePet()}><span>×</span>关闭</button>
        </nav>
      )}
      </div>
    </main>
  );
}
