// Voice I/O. Speech recognition uses the browser's Web Speech API (Chrome, Edge, Safari). Availability varies,
// so everything here detects support and explains failures in plain words instead of failing silently.
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const sttSupported = !!SR;
export const ttsSupported = "speechSynthesis" in window;
export const micApiSupported = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

export function supportSummary() {
  const ua = navigator.userAgent;
  const brave = !!(navigator.brave);
  const secure = window.isSecureContext;
  const parts = [];
  if (!secure) parts.push("This page isn't a secure context (use https:// or http://localhost), so browsers block the microphone.");
  if (!SR) parts.push(brave ? "Brave disables speech recognition by default; use Chrome or Edge, or type." :
    /Firefox/.test(ua) ? "Firefox doesn't support speech recognition; use Chrome, Edge or Safari, or type." :
    "This browser doesn't support speech recognition; use Chrome, Edge or Safari, or type.");
  else parts.push("Speech recognition: available.");
  parts.push(ttsSupported ? "Spoken replies: available." : "Spoken replies: not available in this browser.");
  return { text: parts.join(" "), canListen: !!SR && secure };
}

export async function micPermission() {
  try {
    if (!navigator.permissions) return "unknown";
    return (await navigator.permissions.query({ name: "microphone" })).state;   // granted | denied | prompt
  } catch { return "unknown"; }
}

/** Open the mic for a few seconds and report the loudest level seen. Releases the mic afterwards. */
export async function testMic(onLevel, ms = 4000) {
  if (!micApiSupported) return { ok: false, error: "This browser can't access the microphone." };
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) {
    const why = { NotAllowedError: "Permission was denied. Click the lock icon in the address bar and allow the microphone, or change it in system settings.",
      NotFoundError: "No microphone was found. Plug one in or check Windows sound settings.",
      NotReadableError: "The microphone is busy or blocked by another app. Close other apps that use it.",
      SecurityError: "The browser blocked microphone access on this page." }[e.name] || `Could not open the microphone (${e.name}).`;
    return { ok: false, error: why };
  }
  const devices = (await navigator.mediaDevices.enumerateDevices().catch(() => [])).filter(d => d.kind === "audioinput").length;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx(), src = ctx.createMediaStreamSource(stream), an = ctx.createAnalyser();
  an.fftSize = 512; src.connect(an);
  const buf = new Uint8Array(an.fftSize); let peak = 0;
  await new Promise((resolve) => {
    const end = performance.now() + ms;
    (function tick() {
      an.getByteTimeDomainData(buf);
      let m = 0; for (const v of buf) m = Math.max(m, Math.abs(v - 128));
      const level = Math.min(1, m / 64); peak = Math.max(peak, level); onLevel && onLevel(level);
      performance.now() < end ? requestAnimationFrame(tick) : resolve();
    })();
  });
  stream.getTracks().forEach(t => t.stop()); ctx.close();
  onLevel && onLevel(0);
  return { ok: true, devices, peak, heard: peak > 0.08 };
}

let rec = null;
export const isListening = () => !!rec;
export function listen({ lang, onStart, onInterim, onFinal, onError, onEnd }) {
  if (!SR) {
    onError && onError("Speech recognition isn't supported in this browser. Please type instead.", "unsupported");
    onEnd && onEnd({ gotText: false, failed: true, error: "unsupported" });
    return;
  }
  stopListening();
  const r = new SR();
  let gotText = false, failed = false, error = null;
  r.lang = lang; r.interimResults = true; r.continuous = true; r.maxAlternatives = 1;
  r.onstart = () => { if (rec === r && onStart) onStart(); };
  r.onresult = (e) => {
    if (rec !== r) return;
    let interim = "", final = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i];
      (res.isFinal ? (final += res[0].transcript) : (interim += res[0].transcript));
    }
    if (interim && !final) onInterim && onInterim(interim.trim());
    if (final) { gotText = true; onFinal(final.trim()); }
  };
  r.onerror = (e) => {
    if (rec !== r) return;
    failed = true; error = e.error;
    const why = {
      "not-allowed": "Microphone permission was denied. Click the lock icon in the address bar to allow it, or change it in your browser or system settings. You can also type.",
      "service-not-allowed": "Speech recognition is blocked here (browser or system policy). Please type instead.",
      "no-speech": "I didn't hear anything. Check the microphone with the ⚙ test, then tap the mic and speak.",
      "audio-capture": "No working microphone was found. Check that it's plugged in and selected in system sound settings.",
      "network": "Speech recognition needs an internet connection (the browser sends audio to its speech service). Check your connection and try again.",
      "language-not-supported": "This language isn't supported for voice input in this browser. Pick another language or type.",
      "aborted": "Speech recognition was interrupted. Check browser speech-recognition permissions and tap MIC to try again." }[e.error];
    if (why !== null) onError && onError(why || `Voice input stopped (${e.error}).`, e.error);
  };
  r.onend = () => {
    if (rec !== r) return;
    rec = null;
    onEnd && onEnd({ gotText, failed, error });
  };
  rec = r;
  try { r.start(); } catch (e) {
    if (rec === r) rec = null;
    failed = true;
    onError && onError(e.name === "NotAllowedError"
      ? "Microphone permission was denied. Allow microphone access in your browser settings, then try again."
      : "Could not start the microphone. Check that it is available and try again.");
    onEnd && onEnd({ gotText: false, failed, error: e.name });
  }
}
export function stopListening() { if (rec) { try { rec.stop(); } catch {} } }
export function abortListening() { if (rec) { try { rec.abort(); } catch {} rec = null; } }

// ---- speech output: chunked by sentence (Chrome silently stops long utterances), with voice selection
let voices = [];
if (ttsSupported) { const load = () => { voices = speechSynthesis.getVoices(); }; load(); speechSynthesis.addEventListener("voiceschanged", load); }
let speaking = false, token = 0;
export const isSpeaking = () => speaking;
export function speak(text, lang, onEnd) {
  if (!ttsSupported || !text) { onEnd && onEnd(); return { ok: false }; }
  speechSynthesis.cancel(); const my = ++token; speaking = true;
  const chunks = (text.match(/[^.!?।。]+[.!?।。]?\s*/g) || [text]).map(c => c.trim()).filter(Boolean);
  const base = lang.split("-")[0].toLowerCase();
  const voice = voices.find(v => v.lang.toLowerCase() === lang.toLowerCase()) || voices.find(v => v.lang.toLowerCase().startsWith(base));
  let i = 0;
  const next = () => {
    if (my !== token) return;
    if (i >= chunks.length) { speaking = false; onEnd && onEnd(); return; }
    const u = new SpeechSynthesisUtterance(chunks[i++]); u.lang = lang; if (voice) u.voice = voice;
    u.onend = next; u.onerror = () => { if (my === token) { speaking = false; onEnd && onEnd(); } };
    speechSynthesis.speak(u);
  };
  next();
  return { ok: true, voiceFound: !!voice };
}
export function hush() { token++; speaking = false; if (ttsSupported) speechSynthesis.cancel(); }
