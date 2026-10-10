import { useCallback, useEffect, useRef, useState } from "react";
import { filterMarkdown } from "./MessageContent";
import { splitSpeechText } from "./speech-utils";
import "./Speech.css";
import { WorkspaceIcon } from "./WorkspaceNavigation";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
type SpeechSettings = {
  proxy_mode: "auto" | "direct";
  stt_base_url: string; stt_api_key: string; stt_model: string;
  tts_base_url: string; tts_api_key: string; tts_model: string; tts_voice: string; tts_speed: number;
};
async function checkResponse(response: Response) {
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(typeof result.detail === "string" ? result.detail : `语音请求失败 (${response.status})`);
  }
  return response;
}

export function useSpeech(enabled: boolean, scope: string | number | null, onTranscript: (text: string) => void) {
  const [phase, setPhase] = useState<"idle" | "preparing" | "recording" | "transcribing">("idle");
  const [reading, setReading] = useState<string | null>(null);
  const [error, setError] = useState("");
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const callback = useRef(onTranscript);
  callback.current = onTranscript;
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef(0);
  const recordingSequence = useRef(0);
  const playbackSequence = useRef(0);
  const recordingRequest = useRef<AbortController | null>(null);
  const playbackRequest = useRef<AbortController | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef("");
  const endPlayback = useRef<(() => void) | null>(null);

  const stopReading = useCallback(() => {
    playbackSequence.current++;
    playbackRequest.current?.abort();
    endPlayback.current?.();
    endPlayback.current = null;
    audio.current?.pause();
    audio.current = null;
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audioUrl.current = "";
    setReading(null);
  }, []);

  const cancelRecording = useCallback(() => {
    recordingSequence.current++;
    recordingRequest.current?.abort();
    window.clearTimeout(timer.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    recorder.current = null;
    phaseRef.current = "idle";
    setPhase("idle");
  }, []);

  useEffect(() => {
    cancelRecording();
    stopReading();
    setError("");
    return () => { cancelRecording(); stopReading(); };
  }, [enabled, scope, cancelRecording, stopReading]);

  async function toggleRecording() {
    if (!enabled) return;
    if (phaseRef.current === "recording") { recorder.current?.stop(); return; }
    if (phaseRef.current !== "idle") return;
    stopReading();
    setError("");
    const sequence = ++recordingSequence.current;
    phaseRef.current = "preparing";
    setPhase("preparing");
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        throw new Error("当前环境无法录音，请使用桌面程序或 localhost/HTTPS 预览并允许麦克风权限");
      }
      // Validate configuration before requesting permission or uploading audio.
      const config: SpeechSettings = await (await checkResponse(await fetch(`${API}/speech/settings`))).json();
      if (!config.stt_base_url || !config.stt_model) throw new Error("请先在模型配置的语音模型中配置识别 API 地址和模型");
      if (sequence !== recordingSequence.current) return;
      const captured = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (sequence !== recordingSequence.current) { captured.getTracks().forEach((track) => track.stop()); return; }
      stream.current = captured;
      const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((value) => MediaRecorder.isTypeSupported(value));
      const instance = new MediaRecorder(captured, mime ? { mimeType: mime } : undefined);
      recorder.current = instance;
      const chunks: Blob[] = [];
      let size = 0;
      instance.ondataavailable = (event) => {
        size += event.data.size;
        if (size > 10 * 1024 * 1024) { cancelRecording(); setError("录音超过 10 MB，请分段录制"); return; }
        if (event.data.size) chunks.push(event.data);
      };
      instance.onerror = () => { cancelRecording(); setError("录音失败，请检查麦克风设备和权限"); };
      instance.onstop = () => {
        window.clearTimeout(timer.current);
        captured.getTracks().forEach((track) => track.stop());
        if (sequence !== recordingSequence.current) return;
        recorder.current = null;
        stream.current = null;
        const blob = new Blob(chunks, { type: instance.mimeType || mime || "audio/webm" });
        if (!blob.size) { phaseRef.current = "idle"; setPhase("idle"); setError("没有录到音频，请检查麦克风"); return; }
        phaseRef.current = "transcribing";
        setPhase("transcribing");
        void (async () => {
          try {
            const dataUrl = await new Promise<string>((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result));
              reader.onerror = () => reject(new Error("无法读取录音"));
              reader.readAsDataURL(blob);
            });
            if (sequence !== recordingSequence.current) return;
            const controller = new AbortController();
            recordingRequest.current = controller;
            const response = await checkResponse(await fetch(`${API}/speech/transcribe`, {
              method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
              body: JSON.stringify({ audio_base64: dataUrl.split(",")[1], mime_type: blob.type }),
            }));
            const result: { text: string } = await response.json();
            if (sequence === recordingSequence.current) {
              if (result.text) callback.current(result.text);
              else setError("未识别到文字，请再试一次");
            }
          } catch (problem) {
            if (sequence === recordingSequence.current) setError((problem as Error).message);
          } finally {
            if (sequence === recordingSequence.current) { phaseRef.current = "idle"; setPhase("idle"); }
          }
        })();
      };
      instance.start(250);
      phaseRef.current = "recording";
      setPhase("recording");
      timer.current = window.setTimeout(() => { if (instance.state === "recording") instance.stop(); }, 60_000);
    } catch (problem) {
      if (sequence !== recordingSequence.current) return;
      cancelRecording();
      setError((problem as Error).name === "NotAllowedError" ? "麦克风权限被拒绝，请在系统或浏览器设置中允许录音" : (problem as Error).message);
    }
  }

  async function read(text: string, key: string) {
    if (!enabled) return;
    if (reading === key) { stopReading(); return; }
    cancelRecording();
    stopReading();
    setError("");
    const plain = filterMarkdown(text);
    if (!plain) return;
    if (plain.length > 30_000) { setError("本条回复较长，请编辑或分段后再朗读（上限 3 万字符）"); return; }
    const sequence = playbackSequence.current;
    setReading(key);
    try {
      for (const chunk of splitSpeechText(plain)) {
        if (sequence !== playbackSequence.current) break;
        const controller = new AbortController();
        playbackRequest.current = controller;
        const response = await checkResponse(await fetch(`${API}/speech/synthesize`, {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
          body: JSON.stringify({ text: chunk }),
        }));
        const blob = await response.blob();
        if (sequence !== playbackSequence.current) break;
        const url = URL.createObjectURL(blob);
        audioUrl.current = url;
        const player = new Audio(url);
        audio.current = player;
        await new Promise<void>((resolve, reject) => {
          endPlayback.current = resolve;
          player.onended = () => resolve();
          player.onerror = () => reject(new Error("音频播放失败，请确认服务返回有效 MP3"));
          void player.play().catch(() => reject(new Error("无法播放音频，请允许音频播放后再点击朗读")));
        });
        if (sequence === playbackSequence.current) {
          URL.revokeObjectURL(url);
          audioUrl.current = "";
          audio.current = null;
          endPlayback.current = null;
        }
      }
    } catch (problem) {
      if (sequence === playbackSequence.current) setError((problem as Error).message);
    } finally {
      if (sequence === playbackSequence.current) stopReading();
    }
  }

  return { phase, reading, error, read, stopReading, toggleRecording, cancelRecording };
}

export function VoiceControls({ voice, disabled = false }: { voice: ReturnType<typeof useSpeech>; disabled?: boolean }) {
  return <div className="voice-controls">
    <button type="button" disabled={disabled || voice.phase === "preparing" || voice.phase === "transcribing"} onClick={() => void voice.toggleRecording()} aria-label={voice.phase === "recording" ? "结束录音并识别" : "开始语音输入"} className={voice.phase === "recording" ? "voice-recording" : ""}>
      {voice.phase === "recording" ? "■ 结束录音" : voice.phase === "preparing" ? "准备录音…" : voice.phase === "transcribing" ? "识别中…" : "🎙 语音输入"}
    </button>
    {voice.phase !== "idle" && <button type="button" onClick={voice.cancelRecording}>取消</button>}
    {voice.reading && <button type="button" onClick={voice.stopReading}>■ 停止朗读</button>}
    <small role="status">{voice.error || (voice.phase === "recording" ? "最长 60 秒；录音将提交给配置的语音服务" : voice.phase === "transcribing" ? "转写后填入输入框，不会自动发送" : voice.reading ? "正在生成或播放 AI 语音" : "点击录音 · 转写后确认发送 · AI 合成语音")}</small>
  </div>;
}

/** Compact desktop controls; the pet keeps its existing VoiceControls layout. */
export function SpeechToolbar({ voice, disabled = false }: { voice: ReturnType<typeof useSpeech>; disabled?: boolean }) {
  const label = voice.phase === "recording" ? "结束录音并识别" : voice.phase === "preparing" ? "正在准备录音" : voice.phase === "transcribing" ? "正在识别语音" : "开始语音输入";
  return <div className="composer-voice-actions">
    <button type="button" className={`toolbar-button ${voice.phase === "recording" ? "voice-recording" : ""}`} title={label} aria-label={label} aria-describedby="desktop-speech-status" aria-pressed={voice.phase === "recording"}
      disabled={disabled || voice.phase === "preparing" || voice.phase === "transcribing"} onClick={() => void voice.toggleRecording()}>
      <WorkspaceIcon name={voice.phase === "recording" ? "stop" : "mic"} />
    </button>
    {voice.phase !== "idle" && <button className="toolbar-button voice-cancel" type="button" onClick={voice.cancelRecording} aria-label="取消录音或识别" title="取消录音或识别">×</button>}
    {voice.reading && <button className="toolbar-button" type="button" onClick={voice.stopReading} aria-label="停止朗读" title="停止朗读"><WorkspaceIcon name="stop" /></button>}
  </div>;
}

export function SpeechStatus({ voice }: { voice: ReturnType<typeof useSpeech> }) {
  const text = voice.error || (voice.phase === "recording" ? "正在录音 · 最长 60 秒，点击停止后提交给语音服务" : voice.phase === "preparing" ? "正在准备麦克风…" : voice.phase === "transcribing" ? "正在识别 · 文字将填入输入框，不会自动发送" : voice.reading ? "正在生成或播放 AI 语音" : "");
  return <small id="desktop-speech-status" className="composer-voice-status" role={voice.error ? "alert" : "status"}>{text}</small>;
}

export function SpeechSettingsPanel() {
  const [config, setConfig] = useState<SpeechSettings | null>(null);
  const [status, setStatus] = useState("正在读取语音配置…");
  const [testing, setTesting] = useState<"stt" | "tts" | null>(null);
  const [networkStatus, setNetworkStatus] = useState("");
  const pending = useRef<Partial<SpeechSettings>>({});
  const timer = useRef(0);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const live = useRef(true);
  const preview = useSpeech(true, "preview", () => {});

  function flush() {
    const patch = pending.current;
    pending.current = {};
    if (!Object.keys(patch).length) return;
    queue.current = queue.current.then(async () => {
      try {
        await checkResponse(await fetch(`${API}/speech/settings`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
        }));
        if (live.current) setStatus(Object.keys(pending.current).length ? "等待自动保存…" : "已自动保存");
      } catch (problem) {
        if (live.current) setStatus(`保存失败：${(problem as Error).message}；修改字段可重试`);
        // Keep failed changes for a subsequent edit without overwriting newer values.
        pending.current = { ...patch, ...pending.current };
      }
    });
  }

  useEffect(() => {
    live.current = true;
    void fetch(`${API}/speech/settings`).then(checkResponse).then((response) => response.json()).then((value: SpeechSettings) => {
      if (live.current) { setConfig(value); setStatus("修改后自动保存"); }
    }).catch((problem: Error) => { if (live.current) setStatus(problem.message); });
    return () => { live.current = false; window.clearTimeout(timer.current); flush(); };
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- flush uses refs and saves pending edits on close
  }, []);

  function change(patch: Partial<SpeechSettings>) {
    setConfig((value) => value ? { ...value, ...patch } : value);
    pending.current = { ...pending.current, ...patch };
    setStatus("等待自动保存…");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, 650);
  }

  async function testConnection(kind: "stt" | "tts") {
    setTesting(kind);
    setNetworkStatus("正在检查网络连接…");
    try {
      window.clearTimeout(timer.current);
      flush();
      await queue.current;
      if (Object.keys(pending.current).length) throw new Error("配置尚未保存成功，请先检查地址并重试");
      const response = await checkResponse(await fetch(`${API}/speech/connection-test`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind }),
      }));
      const result = await response.json();
      if (live.current) setNetworkStatus(`服务器可达（HTTP ${result.http_status}，${result.proxy_used ? "使用代理" : "直连"}）。仅验证网络，不验证密钥、模型或识别效果。`);
    } catch (problem) {
      if (live.current) setNetworkStatus((problem as Error).message);
    } finally {
      if (live.current) setTesting(null);
    }
  }

  return <div className="speech-settings">
    <small>仅支持 OpenAI 格式兼容的语音接口。识别和合成可使用不同服务；聊天模型地址不会自动作为语音地址。录音上传给识别服务，朗读文字提交给合成服务，可能产生费用。</small>
    {config && <>
      <label>语音网络连接<select value={config.proxy_mode} onChange={(event) => change({ proxy_mode: event.target.value as SpeechSettings["proxy_mode"] })}>
        <option value="auto">自动使用系统代理</option><option value="direct">直连（不使用代理）</option>
      </select></label>
      <small>自动读取环境变量或 Windows 系统 HTTP/HTTPS 代理；不支持 PAC 脚本和 SOCKS 代理。</small>
      {(["stt", "tts"] as const).map((kind) => <fieldset key={kind}>
        <legend>{kind === "stt" ? "语音输入 · 识别" : "语音输出 · AI 朗读"}</legend>
        <label>API 基础地址<input type="url" placeholder="https://服务地址/v1" value={config[`${kind}_base_url`]} onChange={(event) => change({ [`${kind}_base_url`]: event.target.value })} /></label>
        <small>也可粘贴完整 audio/transcriptions 或 audio/speech 地址，保存时自动纠正。</small>
        <button type="button" disabled={testing !== null} onClick={() => void testConnection(kind)}>{testing === kind ? "检查中…" : "检查网络连接"}</button>
        <label>API 密钥<input type="password" autoComplete="off" value={config[`${kind}_api_key`]} onChange={(event) => change({ [`${kind}_api_key`]: event.target.value })} /></label>
        <label>模型名称<input placeholder={kind === "stt" ? "填写服务商的识别模型 ID" : "填写服务商的合成模型 ID"} value={config[`${kind}_model`]} onChange={(event) => change({ [`${kind}_model`]: event.target.value })} /></label>
        {kind === "tts" && <>
          <label>音色 ID<input value={config.tts_voice} onChange={(event) => change({ tts_voice: event.target.value })} /></label>
          <label>语速（{config.tts_speed}×）<input type="range" min="0.25" max="4" step="0.05" value={config.tts_speed} onChange={(event) => change({ tts_speed: Number(event.target.value) })} /></label>
          <button type="button" onClick={async () => { flush(); await queue.current; await preview.read("你好，我是你的 AI 伙伴，很高兴听到你的声音。", "preview"); }}>{preview.reading ? "停止试听" : "试听 AI 音色"}</button>
          {preview.error && <small role="alert">{preview.error}</small>}
        </>}
      </fieldset>)}
      <small>仅点击录音、手动朗读，不持续监听、不自动发送、不自动朗读主动发言。请先启用语音模型再测试。</small>
      {networkStatus && <small role="status">{networkStatus}</small>}
    </>}
    <small role="status">{status}</small>
  </div>;
}
