import { forwardRef, useEffect, useId, useImperativeHandle, useRef, useState } from "react";
import type { RefObject } from "react";
import { advanceSpring, dentMap } from "./slimeDynamics";
import type { Spring } from "./slimeDynamics";
import { rememberTouch, touchVisible } from "./slimeTouch";
import type { TouchHistory, TouchMemory } from "./slimeTouch";

export type SlimeTouch = { press: (x: number, y: number) => void; release: (cancel?: boolean) => void; tap: () => void };
type Props = {
  gaze: { x: number; y: number }; enabled: boolean; dragging: boolean; expression: string; protectedExpression: boolean;
  faceRef: RefObject<HTMLSpanElement | null>; crestRef: RefObject<HTMLSpanElement | null>;
};

const still = (): Spring => ({ value: 0, velocity: 0 });

export default forwardRef<SlimeTouch, Props>(function SlimeDetails(props, ref) {
  const id = `slime-soft-${useId().replace(/:/g, "")}`;
  const root = useRef<HTMLSpanElement>(null);
  const displacement = useRef<SVGFEDisplacementMapElement>(null);
  const map = useRef<SVGFEImageElement>(null);
  const bodyImage = useRef<SVGImageElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const wake = useRef<() => void>(() => {});
  const held = useRef(false);
  const reduced = useRef(false);
  const contact = useRef<{ x: number; y: number } | null>(null);
  const history = useRef<TouchHistory>({ recent: [], lastProtest: -Infinity });
  const [memory, setMemory] = useState<TouchMemory | null>(null);
  const memoryRef = useRef(memory);
  memoryRef.current = memory;
  const [reacting, setReacting] = useState(false);
  const physics = useRef({ eyeX: still(), eyeY: still(), faceX: still(), faceY: still(), crest: still(), dent: still() });

  useImperativeHandle(ref, () => ({
    press(x, y) {
      contact.current = { x, y };
      if (!latest.current.enabled || reduced.current) return;
      map.current?.setAttribute("href", dentMap(x + 15, y + 20));
      held.current = true;
      physics.current.dent.velocity = 120;
      wake.current();
    },
    release(cancel) { held.current = false; if (cancel) contact.current = null; wake.current(); },
    tap() {
      const point = contact.current;
      contact.current = null;
      if (!point || !latest.current.enabled || latest.current.dragging) return;
      const next = rememberTouch(history.current, point.x, point.y, Date.now(), memoryRef.current);
      history.current = next.history;
      if (next.memory === memoryRef.current) return;
      memoryRef.current = next.memory;
      setMemory(next.memory);
      setReacting(true);
    },
  }), []);

  useEffect(() => {
    if (!memory) return;
    const reactionTimer = window.setTimeout(() => setReacting(false), 750);
    const expiryTimer = window.setTimeout(() => { setMemory(null); setReacting(false); }, Math.max(0, memory.until - Date.now()));
    return () => { window.clearTimeout(reactionTimer); window.clearTimeout(expiryTimer); };
  }, [memory]);
  useEffect(() => {
    if (!props.enabled) { setMemory(null); setReacting(false); history.current = { recent: [], lastProtest: -Infinity }; }
    if (props.dragging) contact.current = null;
  }, [props.enabled, props.dragging]);

  useEffect(() => {
    let frame = 0;
    let previous = 0;
    let disposed = false;
    const preference = matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => { reduced.current = preference.matches; wake.current(); };
    reduced.current = preference.matches;
    const tick = (time: number) => {
      frame = 0;
      const dt = previous ? (time - previous) / 1000 : 1 / 60;
      previous = time;
      const p = latest.current;
      const animated = p.enabled && !reduced.current;
      const targetX = Math.max(-1, Math.min(1, p.gaze.x));
      const targetY = Math.max(-1, Math.min(1, p.gaze.y));
      const s = physics.current;
      const targets = {
        eyeX: targetX, eyeY: targetY, faceX: targetX, faceY: targetY,
        crest: animated ? targetX * 5 + s.faceX.velocity * -1.8 + (p.dragging ? targetX * -5 : 0) : 0,
        dent: animated && held.current && !p.dragging ? 12 : 0,
      };
      for (const key of Object.keys(targets) as (keyof typeof targets)[]) {
        if (!animated) s[key] = { value: targets[key], velocity: 0 };
        else s[key] = advanceSpring(s[key], targets[key], dt,
          key.startsWith("eye") ? 250 : key === "crest" ? 85 : 125,
          key === "crest" || key === "dent" ? 12 : 24);
      }
      const style = root.current?.style;
      style?.setProperty("--detail-eye-x", String(s.eyeX.value));
      style?.setProperty("--detail-eye-y", String(s.eyeY.value));
      style?.setProperty("--detail-face-x", `${s.faceX.value * 1.5}px`);
      style?.setProperty("--detail-face-y", `${s.faceY.value * 1.1}px`);
      style?.setProperty("--detail-crest", `${Math.max(-12, Math.min(12, s.crest.value))}deg`);
      style?.setProperty("--detail-body-x", String(1 + s.dent.value * .0008));
      style?.setProperty("--detail-body-y", String(1 - s.dent.value * .0008));
      displacement.current?.setAttribute("scale", String(s.dent.value));
      if (Math.abs(s.dent.value) > .02) bodyImage.current?.setAttribute("filter", `url(#${id})`);
      else bodyImage.current?.removeAttribute("filter");
      const unsettled = (Object.keys(targets) as (keyof typeof targets)[]).some(key =>
        Math.abs(s[key].value - targets[key]) > .001 || Math.abs(s[key].velocity) > .01);
      if (animated && unsettled && !disposed) frame = requestAnimationFrame(tick);
      else previous = 0;
    };
    wake.current = () => { if (!disposed && !frame) frame = requestAnimationFrame(tick); };
    const release = () => { held.current = false; wake.current(); };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", release);
    preference.addEventListener("change", updatePreference);
    wake.current();
    return () => {
      disposed = true; cancelAnimationFrame(frame); wake.current = () => {};
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      window.removeEventListener("blur", release);
      preference.removeEventListener("change", updatePreference);
    };
  }, [id]);

  useEffect(() => { if (props.dragging) held.current = false; wake.current(); },
    [props.gaze.x, props.gaze.y, props.dragging, props.enabled]);

  const visible = touchVisible(memory, Date.now(), props.protectedExpression || props.dragging, props.enabled);
  return <span ref={root} className="slime-detail-root" data-touch={visible ? memory?.kind : undefined}
    data-touch-side={visible ? memory?.side : undefined} data-touch-phase={visible ? reacting ? "reaction" : "memory" : undefined}>
    <svg className="slime-body-layer" viewBox="0 0 170 190" aria-hidden="true">
      <defs><filter id={id} x="-15%" y="-15%" width="130%" height="130%" colorInterpolationFilters="sRGB">
        <feImage ref={map} href={dentMap(85, 95)} x="0" y="0" width="170" height="190" result="touch" />
        <feDisplacementMap ref={displacement} in="SourceGraphic" in2="touch" scale="0" xChannelSelector="R" yChannelSelector="G" />
      </filter></defs>
      <image ref={bodyImage} href="/assets/blue-slime-pet-body-v2.png" width="170" height="190" preserveAspectRatio="xMidYMid meet" />
    </svg>
    <span className="slime-detail-crest"><span ref={props.crestRef} className="slime-crest-layer" /></span>
    <span className="slime-highlight-layer"><i /><b /></span>
    <span className="slime-detail-face"><span ref={props.faceRef} className={`slime-face-layer detail-expression-${props.expression}`}>
      <span className="slime-eye slime-eye-left"><span className="slime-pupil"><i /></span><b /></span>
      <span className="slime-eye slime-eye-right"><span className="slime-pupil"><i /></span><b /></span>
      <span className="slime-blush slime-blush-left" /><span className="slime-blush slime-blush-right" />
      <span className="slime-mouth"><i /></span>
    </span></span>
  </span>;
});
