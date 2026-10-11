import { useEffect, useState } from "react";
import "./WorkspaceAtmosphere.css";
import "./WorkspaceTide.css";

export type AtmosphereMotion = "off" | "light" | "full";

function usePageVisible() {
  const [visible, setVisible] = useState(() => !document.hidden);
  useEffect(() => {
    const update = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

export function WorkspaceBackdrop({ motion }: { motion: AtmosphereMotion }) {
  const visible = usePageVisible();
  return <div className="workspace-night-sky" data-motion={motion} data-visible={visible} aria-hidden="true">
    <div className="night-sky-wash" />
    <svg className="night-sky-crescent" viewBox="0 0 160 160"><path className="sky-moon-body" d="M104 18a58 58 0 1 0 37 87A57 57 0 0 1 104 18Z" /><path className="sky-moon-outline" d="M92 27a49 49 0 0 0 39 86" /><path className="sky-orbit-star" d="m133 32 4 11 11 4-11 4-4 11-4-11-11-4 11-4Z" /><circle cx="36" cy="125" r="2" /><circle cx="142" cy="91" r="2.5" /></svg>
    <svg className="night-sky-orbit" viewBox="0 0 440 440"><circle cx="220" cy="220" r="190" /><ellipse cx="220" cy="220" rx="110" ry="205" transform="rotate(-35 220 220)" /><circle className="sky-orbit-dot" cx="353" cy="85" r="6" /><path className="sky-orbit-star" d="m79 261 4 10 10 4-10 4-4 10-4-10-10-4 10-4Z" /></svg>
    <svg className="night-sky-trail" viewBox="0 0 230 340"><path d="m40 34 69 57-31 95 69 68-30 51" /><g><circle cx="40" cy="34" r="3" /><circle cx="109" cy="91" r="4" /><circle cx="78" cy="186" r="3" /><circle cx="147" cy="254" r="4" /><circle cx="117" cy="305" r="2" /></g><path className="sky-orbit-star" d="m109 77 4 10 10 4-10 4-4 10-4-10-10-4 10-4Z" /></svg>
    <div className="night-sky-lights"><i /><i /><i /><i /><i /><i /></div>
    <div className="night-sky-meteor"><i /><i /></div>
    <svg className="night-sky-landscape" viewBox="0 0 1400 280" preserveAspectRatio="none"><path className="sky-hill-far" d="M0 80C180 30 240 165 430 145S660 205 800 154 1170 25 1400 110V280H0Z" /><path className="sky-hill-middle" d="M0 154C200 115 250 220 530 215S850 190 1000 162 1240 105 1400 156V280H0Z" /><path className="sky-hill-near" d="M0 224C160 177 380 272 650 255S980 200 1150 222 1320 200 1400 218V280H0Z" /><path className="sky-hill-thread" d="M0 160C200 121 250 226 530 221S850 196 1000 168 1240 111 1400 162" /></svg>
  </div>;
}

export function WorkspaceTideBackdrop({ motion, chatting = false }: { motion: AtmosphereMotion; chatting?: boolean }) {
  const visible = usePageVisible();
  return <div className="workspace-tide-scene" data-motion={motion} data-visible={visible} data-chatting={chatting} aria-hidden="true">
    <div className="violet-horizon" />
    <div className="violet-aurora"><i /><i /></div>
    <div className="violet-stars" />
    <div className="violet-water" />
    <div className="violet-sculpture">
      <div className="violet-orbit violet-orbit-back" />
      <svg viewBox="0 0 240 280"><defs>
        <linearGradient id="violet-drop-metal" x1="0" y1="0" x2="1" y2=".8"><stop stopColor="#fff5ff" /><stop offset=".22" stopColor="#d9bff0" /><stop offset=".49" stopColor="#a98bcc" /><stop offset=".68" stopColor="#e4d9ff" /><stop offset=".83" stopColor="#b5c8ec" /><stop offset="1" stopColor="#e1caf1" /></linearGradient>
        <radialGradient id="violet-drop-light"><stop stopColor="#e9d5ff" stopOpacity=".8" /><stop offset="1" stopColor="#be7bff" stopOpacity="0" /></radialGradient>
      </defs><path className="violet-drop" d="M120 15C97 65 30 96 30 170a90 90 0 0 0 180 0C210 96 143 65 120 15Z" fill="url(#violet-drop-metal)" />
      <path d="M112 48C84 99 52 120 50 165" fill="none" stroke="#ebcaff" strokeWidth="3" strokeLinecap="round" opacity=".7" />
      <ellipse cx="155" cy="170" rx="48" ry="66" fill="url(#violet-drop-light)" />
      <path className="violet-eye" d="M77 163q9-12 18 0M145 163q9-12 18 0" /><path className="violet-smile" d="M108 194q12 12 24 0" />
      </svg>
      <div className="violet-orbit violet-orbit-front" />
      <div className="violet-reflection" />
    </div>
  </div>;
}

/** Decorative only: no focus targets, input interception, polling or model calls. */
export default function WorkspaceAtmosphere({ busy, motion, scene = "grape" }: { busy: boolean; motion: AtmosphereMotion; scene?: "grape" | "tide" }) {
  const visible = usePageVisible();
  if (scene === "tide") return <div className="workspace-atmosphere" data-scene={scene} data-motion={motion} data-busy={busy} data-visible={visible} aria-hidden="true">
    <div className="tide-composer-sheen" />
    <div className="atmosphere-border" />
    {busy && <div className="atmosphere-tide-send"><span /></div>}
  </div>;
  return <div className="workspace-atmosphere" data-scene={scene} data-motion={motion} data-busy={busy} data-visible={visible} aria-hidden="true">
    <svg className="atmosphere-moon" viewBox="0 0 64 64"><path d="M36 9a22 22 0 1 0 19 32A21 21 0 0 1 36 9Z" /><path className="atmosphere-star" d="m53 7 2 5 5 2-5 2-2 5-2-5-5-2 5-2Z" /></svg>
    <svg className="atmosphere-planet" viewBox="0 0 100 80"><g className="planet-float"><circle cx="50" cy="40" r="21" /><ellipse cx="50" cy="40" rx="39" ry="10" transform="rotate(-25 50 40)" /><path className="planet-meridian" d="M45 20q20 20 10 40" /></g><path className="atmosphere-star" d="m87 8 2 5 5 2-5 2-2 5-2-5-5-2 5-2Z" /></svg>
    <svg className="atmosphere-constellation" viewBox="0 0 150 65"><path className="constellation-line" d="m12 40 36-19 37 23 48-30" /><g className="constellation-stars"><circle cx="12" cy="40" r="2" /><path d="m48 14 2 5 5 2-5 2-2 5-2-5-5-2 5-2Z" /><circle cx="85" cy="44" r="2.5" /><path d="m133 8 2 4 4 2-4 2-2 4-2-4-4-2 4-2Z" /></g></svg>
    <div className="atmosphere-peeker"><svg viewBox="0 0 80 65"><g className="peeker-breathe"><path className="peeker-body" d={"M5 63C5 17 23 7 40 7s35 10 35 56Z"} /><path className="peeker-highlight" d={"M16 24q5-9 13-10"} /><g className="peeker-eyes"><ellipse cx="27" cy="32" rx="5" ry="7" /><ellipse cx="53" cy="32" rx="5" ry="7" /><g className="peeker-gaze"><circle cx="29" cy="30" r="2" /><circle cx="55" cy="30" r="2" /></g></g><path className="peeker-mouth" d="M35 43q5 5 10 0" /></g></svg></div>
    <div className="atmosphere-whispers"><i /><i /><i /></div>
    <div className="atmosphere-edge-stars"><i>✧</i><i>·</i><i>✦</i></div>
    <svg className="atmosphere-charm" viewBox="0 0 30 68"><path d="M15 1v37" /><path className="atmosphere-star" d="m15 37 4 9 10 2-8 7 1 10-7-5-8 5 2-10-8-7 10-2Z" /></svg>
    <div className="atmosphere-border" />
    {busy && <><div className="atmosphere-send-ripple" /><div className="atmosphere-send-sparkles"><i>✦</i><i>✧</i><i>✦</i></div></>}
  </div>;
}
