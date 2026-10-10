import { useEffect, useRef, useState } from "react";
import { splitPetSentences } from "./petSentences";
import { isFreshPetReply, resumePetBubbles } from "./petBubblePlayback";

export function usePetBubbles(text: string, generating: boolean, enabled: boolean) {
  const [bubbles, setBubbles] = useState<{ id: number; text: string }[]>([]);
  const sequence = useRef(0);
  const [hovered, setHovered] = useState(false);
  const [fading, setFading] = useState(false);
  const [completedText, setCompletedText] = useState<string | null>(null);
  const previous = useRef("");
  const shown = useRef(0);
  const lastShown = useRef(0);
  const wasEnabled = useRef(enabled);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const fresh = isFreshPetReply(text, previous.current);
    if (fresh) {
      shown.current = 0;
      lastShown.current = 0;
      setBubbles([]);
      setFading(false);
      setCompletedText(null);
      setHovered(false);
    }
    previous.current = text;
    const reopening = enabled && !wasEnabled.current;
    wasEnabled.current = enabled;
    if (!enabled || !text) return;
    const sentences = splitPetSentences(text, !generating);
    if (reopening && !fresh) {
      // Hidden output is already received: show its latest snapshot, never replay it.
      const resumed = resumePetBubbles(sentences);
      shown.current = resumed.cursor;
      lastShown.current = Date.now();
      setBubbles(resumed.visible.map(sentence => ({ id: ++sequence.current, text: sentence })));
      setFading(false);
      setCompletedText(null);
      setHovered(false);
      setTick(current => current + 1);
      return;
    }
    if (shown.current < sentences.length) {
      const delay = Math.max(0, lastShown.current + 1400 - Date.now());
      const timer = window.setTimeout(() => {
        const sentence = sentences[shown.current++];
        lastShown.current = Date.now();
        setFading(false);
        setBubbles(current => [...current, { id: ++sequence.current, text: sentence }].slice(-4));
        setTick(current => current + 1);
      }, delay);
      return () => window.clearTimeout(timer);
    }
    if (hovered) setFading(false);
    if (generating || hovered || !shown.current) return;
    // Start the reading clock only after the entire response has been displayed.
    const readingMs = Math.min(30000, Math.max(8000, sentences.slice(-4).join("").length * 180));
    const fadeTimer = window.setTimeout(() => setFading(true), readingMs);
    const clearTimer = window.setTimeout(() => { setBubbles([]); setCompletedText(text); }, readingMs + 350);
    return () => { window.clearTimeout(fadeTimer); window.clearTimeout(clearTimer); };
  }, [text, generating, enabled, hovered, tick]);
  return { bubbles, fading, completed: !!text && completedText === text, setHovered };
}
