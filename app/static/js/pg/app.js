import { get, post, del, openStream } from "./api.js";
import * as voice from "./voice.js";
import { el, renderDesks, renderBoard, renderReport, renderEvidence, logLine, fmtTime } from "./render.js";

const $ = (id) => document.getElementById(id);
const STATE_LABEL = { idle: "Idle", briefed: "Briefed", researching: "Researching", reporting: "Reporting", done: "Done", blocked: "Blocked", error: "Error", skipped: "Skipped" };
const STEPS = ["understand", "brief", "research", "meet", "verify", "report"];
const SIDE = ["results", "evidence", "log"];
const STOP_WORDS = /^(stop|cancel|interrupt|never ?mind|wait|ruko|रुको|रुकिए|बंद करो)[.!\s]*$/i;

function renderSearchUsage(usage) {
  if (!usage || !Number.isInteger(usage.used) || !Number.isInteger(usage.limit)
      || !Number.isInteger(usage.remaining)) return;
  const note = $("search-limit-note");
  if (note) note.textContent =
    `Research searches this month: ${usage.used}/${usage.limit} used · ${usage.remaining} remaining`;
}

async function refreshSearchUsage() {
  try {
    const { search_usage: usage } = await get("/api/usage");
    renderSearchUsage(usage);
  } catch (e) {
    console.error("Could not refresh monthly search usage:", e);
    showError("Could not refresh monthly search usage: " + e.message);
  }
}

const S = { runId: null, status: "idle", lastSeq: 0, stream: null, cfg: null, agents: { map: {}, supStep: "" }, tasks: {},
  report: null, lang: localStorage.getItem("pg_lang") || "en-IN",
  notify: localStorage.getItem("pg_notify") === "1" && "Notification" in window
    && Notification.permission === "granted", notificationRegistration: null, listening: false,
  stats: { search: 0, pages: 0, start: null }, timer: null,
  sendState: "idle", sendTimer: null,
  opt: { autosend: localStorage.getItem("pg_autosend") !== "0", handsfree: localStorage.getItem("pg_handsfree") === "1" } };

/* ------------------------------------------------------------------ helpers */
function say(text, who = "sup") { const b = el("div", { class: `bubble ${who}` }, text); $("transcript").append(b); b.scrollIntoView({ block: "end" }); }
function setPhase(t) { $("phase").textContent = t; }
function banner(msg) { $("banner").hidden = !msg; $("banner").textContent = msg || ""; }
function showError(message) {
  if (!message) return;
  $("error-message").textContent = message;
  const dialog = $("error-dialog");
  if (!dialog.open) dialog.showModal();
}
function closeError() { if ($("error-dialog").open) $("error-dialog").close(); }
$("btn-error-close").addEventListener("click", closeError);
$("error-dialog").addEventListener("click", (event) => {
  if (event.target === $("error-dialog")) closeError();
});
function active() { return S.status === "running" || S.status === "queued"; }
function redraw() { renderDesks($("desks"), S.agents); renderBoard($("board"), S.tasks); renderStats(); }
function langShort() { return S.lang.split("-")[0]; }
function setDetectedLanguage(language) {
  if (!language) return;
  const base = language.split("-")[0].toLowerCase();
  const options = [...$("lang").options];
  const option = options.find((item) => item.value.toLowerCase() === language.toLowerCase()) ||
    options.find((item) => item.value.split("-")[0].toLowerCase() === base);
  S.lang = option ? option.value : language;
  if (option) $("lang").value = option.value;
  localStorage.setItem("pg_lang", S.lang);
}

function showSendState(state) {
  S.sendState = state;
  if (S.sendTimer) { clearTimeout(S.sendTimer); S.sendTimer = null; }
  $("btn-send").textContent = state === "sending" ? "Sending..." : state === "sent" ? "Sent" : "Send";
  if (state === "sent") {
    S.sendTimer = window.setTimeout(() => {
      S.sendState = "idle"; S.sendTimer = null; $("btn-send").textContent = "Send";
    }, 1200);
  }
}

function setStatus(s) {
  S.status = s; $("btn-stop").hidden = !active();
  if (active()) { if (!S.timer) { S.stats.start = S.stats.start || Date.now(); S.timer = setInterval(renderStats, 1000); } }
  else if (S.timer) { clearInterval(S.timer); S.timer = null; renderStats(); }
}
function renderStats() {
  const list = Object.values(S.agents.map).filter(a => a.selected);
  const finished = list.filter(a => ["done", "blocked", "error"].includes(a.state)).length;
  $("st-agents").textContent = `${finished}/${list.length}`;
  $("st-search").textContent = S.stats.search; $("st-pages").textContent = S.stats.pages;
  $("st-found").textContent = list.reduce((n, a) => n + (a.found || 0), 0);
  const sec = S.stats.start ? Math.floor((Date.now() - S.stats.start) / 1000) : 0;
  $("st-time").textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
function setStep(name, allDone = false) {
  const idx = STEPS.indexOf(name);
  document.querySelectorAll("#stepper li").forEach((li, i) => { li.className = allDone || i < idx ? "done" : i === idx ? "current" : ""; li.removeAttribute("aria-current"); if (i === idx && !allDone) li.setAttribute("aria-current", "step"); });
}

/* Mobile navigation scrolls through the stacked sections; the side tabs switch report panels. */
function go(view) {
  document.body.dataset.view = view;
  if (SIDE.includes(view)) document.body.dataset.side = view;
  document.querySelectorAll(".tabbar button").forEach(b => b.setAttribute("aria-current", String(b.dataset.go === view)));
  document.querySelectorAll("#side-tabs button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.side === document.body.dataset.side)));
  if (isMobile()) {
    const target = document.getElementById({ talk: "panel-talk", office: "panel-office", results: "panel-results", evidence: "panel-evidence", log: "panel-log" }[view]);
    target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }
}
document.querySelectorAll(".tabbar button").forEach(b => b.addEventListener("click", () => go(b.dataset.go)));
document.querySelectorAll("#side-tabs button").forEach(b => b.addEventListener("click", () => go(b.dataset.side)));
const isMobile = () => matchMedia("(max-width:760px)").matches;
function layout() { document.body.dataset.layout = isMobile() ? "mobile" : "desktop"; }
matchMedia("(max-width:760px)").addEventListener("change", layout); layout(); document.body.dataset.side = "results";

function sayAloud(text, lang, then) {
  if (!$("chk-speak").checked) { then && then(); return; }
  const r = voice.speak(text, lang || S.lang, then);
  if (r.ok && r.voiceFound === false) { $("voice-note").hidden = false; $("voice-note").textContent = `No installed voice for ${lang || S.lang}; your device will use a default voice. More voices can be added in system settings.`; }
}
function afterSpeech() { if (S.opt.handsfree && !S.listening && !active() && voice.sttSupported) startListening(); }

/* ------------------------------------------------------------------ events */
function ensureAgent(a, extra = {}) {
  const cur = S.agents.map[a.key] || { key: a.key, label: a.label, role: a.role || "", avatar: a.avatar, selected: false, state: "idle", stateLabel: "Idle" };
  return (S.agents.map[a.key] = Object.assign(cur, extra));
}
function setAgentState(key, state, step) { const a = S.agents.map[key]; if (!a) return; a.state = state; a.stateLabel = STATE_LABEL[state] || state; if (step) a.step = step; }

function handle(ev) {
  S.lastSeq = Math.max(S.lastSeq, ev.seq);
  const d = ev.data || {};
  logLine($("activity"), ev);
  switch (ev.type) {
    case "run_started": setStatus("running"); setPhase("NEXUS is reading your request…"); setStep("understand");
      if (d.provider_configured === false) banner("No search provider key is configured on the server, so the agents cannot research products."); break;
    case "understanding": setDetectedLanguage(d.brief.language); $("mission-title").textContent = d.brief.product; S.agents.supStep = `Understood: “${d.brief.product}”`;
      setPhase(`Request understood (${d.brief.understood_by === "llm" ? "AI model" : "basic rules"}).`);
      for (const a of Object.values(S.agents.map)) Object.assign(a, { selected: false, state: "idle", stateLabel: "Idle", step: "", found: null, progress: null });
      S.tasks = {}; break;
    case "notice":
      say(ev.message, "sys");
      if (d.persistence_failed) showError(ev.message);
      break;
    case "location_suggested": $("loc-prompt").hidden = false; $("loc-prompt-text").textContent = (ev.message || "Local availability may matter.") + " Sharing is optional; I'll carry on with general research either way."; break;
    case "location_used": say(ev.message, "sys"); break;
    case "team_selected": setStep("brief");
      for (const a of d.selected) ensureAgent(a, { selected: true, state: "briefed", stateLabel: "Briefed", reason: a.reason, role: a.role });
      for (const a of d.skipped) ensureAgent(a, { selected: false, reason: a.reason });
      setPhase(`${d.selected.length} agents activated, ${d.skipped.length} stood down.`); break;
    case "meeting_started": $("meeting").hidden = false; $("meeting-name").textContent = d.meeting === "kickoff" ? "Kick-off" : "Findings";
      $("meeting-log").replaceChildren(); setStep(d.meeting === "kickoff" ? "brief" : "meet");
      setPhase(d.meeting === "kickoff" ? "Kick-off meeting in progress…" : "Findings meeting in progress…"); break;
    case "meeting_message": $("meeting-log").append(el("li", {}, el("b", {}, (S.agents.map[ev.agent]?.label || (ev.agent === "supervisor" ? "NEXUS" : ev.agent)) + ": "), ev.message)); break;
    case "task_assigned": setStep("research"); S.tasks[ev.agent] = { label: S.agents.map[ev.agent]?.label || ev.agent, col: "assigned", note: d.task.title }; setPhase("Tasks assigned. Agents are researching in parallel."); break;
    case "agent_status": setAgentState(ev.agent, d.state, ev.message);
      if (S.tasks[ev.agent]) S.tasks[ev.agent].col = { researching: "researching", done: "done", blocked: "blocked", error: "blocked" }[d.state] || S.tasks[ev.agent].col;
      if (d.found != null && S.agents.map[ev.agent]) S.agents.map[ev.agent].found = d.found; break;
    case "agent_step": if (S.agents.map[ev.agent]) { S.agents.map[ev.agent].step = ev.message; S.agents.map[ev.agent].progress = d.progress; }
      if (d.step === "shopping_search" || d.step === "web_search") S.stats.search++;
      if (d.step === "open_page") S.stats.pages++; break;
    case "agent_report": if (S.tasks[ev.agent]) { S.tasks[ev.agent].note = ev.message.slice(0, 80); S.tasks[ev.agent].col = (d.status === "found" || d.status === "none_found") ? "done" : "blocked"; }
      if (S.agents.map[ev.agent]) { S.agents.map[ev.agent].found = d.offers; if (S.agents.map[ev.agent].state === "researching") setAgentState(ev.agent, (d.status === "found" || d.status === "none_found") ? "done" : "blocked"); } break;
    case "verification_started": setStep("verify"); S.agents.supStep = "Cross-checking prices, links, images and requirements…"; setPhase("NEXUS is verifying everything the team found…"); break;
    case "verification_step": S.agents.supStep = ev.message; break;
    case "followup_started": setStatus("running"); setPhase(ev.message); if (d.selected) for (const a of d.selected) ensureAgent(a, { selected: true, state: "briefed", stateLabel: "Briefed", reason: a.reason }); break;
    case "report_ready": S.report = d.report; setStep("report", true);
      renderReport($("report"), d.report, S.runId, { share: shareReport }); renderEvidence($("evidence"), d.report);
      S.agents.supStep = "Report ready."; if (isMobile()) go("results"); break;
    case "supervisor_say": say(ev.message);
      if (d.speak) sayAloud(ev.message, d.language ? (d.language === langShort() ? S.lang : d.language) : S.lang, afterSpeech); break;
    case "run_done": setStatus("done"); setPhase("Finished. Ask a follow-up, refine the budget, or request more platforms."); finished("Research finished"); loadHistory(); break;
    case "run_cancelled": setStatus("cancelled"); setPhase("Interrupted. Tell me what to change."); loadHistory(); break;
    case "run_error": setStatus("error"); setPhase(ev.message); showError(ev.message); break;
  }
  redraw();
}
async function finished(title) {
  if (!S.notify || (document.visibilityState === "visible" && document.hasFocus())
      || !("Notification" in window) || Notification.permission !== "granted") return;
  try {
    const registration = S.notificationRegistration || await navigator.serviceWorker.ready;
    await registration.showNotification(title, {
      body: S.report ? S.report.summary.text.slice(0, 140) : "Your report is ready.",
      icon: "/static/pwa/icon-192.png", badge: "/static/pwa/icon-192.png",
      tag: `research-${S.runId}`, data: { url: "/playground" },
    });
  } catch (e) {
    console.error("Could not display research completion notification:", e);
    showError("Research finished, but the browser could not display its notification. Check your browser and operating-system notification settings.");
  }
}
function attach(runId, after = 0) { S.stream && S.stream.close(); S.runId = runId; S.lastSeq = after; S.stream = openStream(runId, after, handle); }

/* ------------------------------------------------------------------ sending */
async function cancelRun() {
  if (!S.runId) return;
  try { await post(`/api/runs/${S.runId}/cancel`); } catch (e) { showError(e.message); }
}
async function sendText(text) {
  text = text.trim(); if (!text) return;
  voice.hush();
  if (STOP_WORDS.test(text)) {                      // "stop" is a command, never a search
    say(text, "user"); $("input").value = "";
    if (active()) await cancelRun(); else say("Nothing is running right now. Tell me what to research.");
    return;
  }
  if (S.sendState === "sending") return;
  showSendState("sending");
  say(text, "user"); $("input").value = "";
  try {
    if (S.runId && active()) { await cancelRun(); for (let i = 0; i < 40 && active(); i++) await new Promise(r => setTimeout(r, 250)); }
    if (S.runId) {
      const r = await post(`/api/runs/${S.runId}/followup`, { text, language: S.lang });
      setDetectedLanguage(r.language);
      if (r.action === "question" || r.action === "busy") { showSendState("sent"); say(r.answer); sayAloud(r.answer, S.lang, afterSpeech); return; }
      if (r.action === "new_search") { showSendState("sent"); refreshSearchUsage(); resetOffice(); attach(r.run_id, 0); setStatus("running"); return; }
      showSendState("sent"); say(r.answer); attach(S.runId, S.lastSeq); setStatus("running"); return;
    }
    resetOffice();
    const r = await post("/api/runs", { text, language: S.lang });
    showSendState("sent");
    refreshSearchUsage();
    attach(r.run_id, 0); setStatus("running"); if (isMobile()) go("office");
  } catch (e) { showSendState("idle"); showError(e.message); }
}
function resetOffice() {
  S.tasks = {}; S.report = null; S.stats = { search: 0, pages: 0, start: Date.now() }; $("activity").replaceChildren(); $("meeting").hidden = true;
  $("report").replaceChildren(el("p", { class: "empty" }, "The team is working…"));
  $("evidence").replaceChildren(el("p", { class: "empty" }, "Evidence appears after NEXUS finishes verifying."));
  for (const a of Object.values(S.agents.map)) Object.assign(a, { selected: false, state: "idle", stateLabel: "Idle", step: "", found: null, progress: null });
  setStep("none"); $("loc-prompt").hidden = true; redraw();
}
$("composer").addEventListener("submit", (e) => { e.preventDefault(); sendText($("input").value); });
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendText($("input").value); } });
window.addEventListener("focus", refreshSearchUsage);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshSearchUsage(); });
$("btn-stop").addEventListener("click", cancelRun);
$("btn-hush").addEventListener("click", () => { voice.hush(); });
$("btn-new").addEventListener("click", () => { voice.hush(); voice.abortListening(); S.stream && S.stream.close(); S.runId = null; setStatus("idle"); resetOffice();
  $("mission-title").textContent = "No mission yet"; $("report").replaceChildren(el("p", { class: "empty" }, "Results appear here once the team has reported and NEXUS has verified them."));
  setPhase("Waiting for a request."); say("New topic. What should I research?"); });

/* -------------------------------------------------------------------- voice */
function micUi(on, text) {
  S.listening = on;
  $("btn-mic").setAttribute("aria-pressed", String(on));
  $("btn-mic").setAttribute("aria-label", on ? "Stop microphone" : "Start microphone");
  $("btn-mic").lastElementChild.textContent = on ? "STOP" : "MIC";
  $("mic-live").hidden = !on; if (text) $("mic-live-text").textContent = text;
}
function voiceNote(msg) {
  $("voice-note").hidden = true;
  $("voice-note").textContent = "";
  if (msg) showError(msg);
  else closeError();
}
let recognitionRetryTimer = null;
let recognitionAbortRetries = 0;
const MAX_RECOGNITION_ABORT_RETRIES = 2;
function listenAgain() {
  voice.listen({ lang: S.lang,
    onStart: () => { voiceNote(""); micUi(true, `Listening in ${S.lang}… speak now`); },
    onInterim: (t) => {
      recognitionAbortRetries = 0;
      $("input").value = t; $("mic-live-text").textContent = `Heard: “${t}”`;
    },
    onFinal: (t) => {
      if (!t) return;
      recognitionAbortRetries = 0;
      $("input").value = t;
      if (S.opt.autosend || STOP_WORDS.test(t)) {
        micUi(false);
        if (recognitionRetryTimer) { clearTimeout(recognitionRetryTimer); recognitionRetryTimer = null; }
        voice.stopListening();
        sendText(t);
      } else {
        $("input").focus();
      }
    },
    onError: (m, code) => {
      if (code === "aborted") return;
      voiceNote(code === "no-speech"
        ? "I didn't hear anything yet. I'm keeping the microphone on; speak when you're ready."
        : m);
    },
    onEnd: ({ failed, error } = {}) => {
      if (!S.listening) { micUi(false); return; }
      if (!failed || error === "no-speech" ||
          (error === "aborted" && recognitionAbortRetries < MAX_RECOGNITION_ABORT_RETRIES)) {
        if (error === "aborted") recognitionAbortRetries++;
        micUi(true, "Keeping the microphone on…");
        recognitionRetryTimer = window.setTimeout(() => {
          recognitionRetryTimer = null;
          if (S.listening && !voice.isListening()) listenAgain();
        }, 750);
        return;
      }
      micUi(false);
      if (error === "aborted" || error === "unsupported") {
        voiceNote(error === "unsupported"
          ? "Speech recognition isn't supported in this browser. Please type your request."
          : "Speech recognition kept getting interrupted by the browser. Check its speech-recognition permission and internet connection, then tap MIC to try again.");
      }
    } });
}
function startListening() {
  if (S.listening || voice.isListening()) return;
  voice.hush();                                  // never listen to our own voice
  voiceNote("");
  recognitionAbortRetries = 0;
  if (recognitionRetryTimer) { clearTimeout(recognitionRetryTimer); recognitionRetryTimer = null; }
  micUi(true, `Starting microphone in ${S.lang}…`);
  listenAgain();
}
$("btn-mic").addEventListener("click", () => {
  if (S.listening) {
    micUi(false);
    if (recognitionRetryTimer) { clearTimeout(recognitionRetryTimer); recognitionRetryTimer = null; }
    voice.stopListening();
    return;
  }
  startListening();
});
const support = voice.supportSummary();
if (!voice.sttSupported || !support.canListen) { voiceNote(support.text); $("btn-mic").disabled = true; }
if (!voice.ttsSupported) { $("chk-speak").checked = false; $("chk-speak").disabled = true; }
$("lang").value = S.lang; $("lang").addEventListener("change", (e) => { S.lang = e.target.value; localStorage.setItem("pg_lang", S.lang); });

const mic = $("mic-dialog");
$("btn-micset").addEventListener("click", async () => {
  $("opt-autosend").checked = S.opt.autosend; $("opt-handsfree").checked = S.opt.handsfree;
  $("mic-support").textContent = support.text; $("mic-test-result").textContent = "Press the button and speak for a few seconds. The bar should move.";
  const p = await voice.micPermission();
  $("mic-perm").textContent = "Permission: " + ({ granted: "allowed", denied: "blocked (allow it via the lock icon in the address bar)", prompt: "not asked yet (you'll be asked when you test or speak)" }[p] || "unknown (the browser doesn't say)");
  mic.showModal();
});
$("opt-autosend").addEventListener("change", (e) => { S.opt.autosend = e.target.checked; localStorage.setItem("pg_autosend", e.target.checked ? "1" : "0"); });
$("opt-handsfree").addEventListener("change", (e) => { S.opt.handsfree = e.target.checked; localStorage.setItem("pg_handsfree", e.target.checked ? "1" : "0"); });
$("btn-mictest").addEventListener("click", async () => {
  voice.abortListening(); $("btn-mictest").disabled = true; $("mic-test-result").textContent = "Listening for 4 seconds… say something.";
  const r = await voice.testMic((lvl) => { $("meter-bar").style.width = Math.round(lvl * 100) + "%"; });
  $("btn-mictest").disabled = false;
  $("mic-test-result").textContent = !r.ok ? r.error : r.heard ? `Your microphone works (${r.devices} input device${r.devices === 1 ? "" : "s"} found).`
    : `The microphone opened but I heard almost nothing (${r.devices} device(s)). Speak closer, check the input volume, or pick another microphone in system sound settings.`;
  const p = await voice.micPermission(); $("mic-perm").textContent = "Permission: " + (p === "granted" ? "allowed" : p);
});

/* ----------------------------------------------------------------- location */
const dlg = $("loc-dialog");
function showLoc(loc) { $("btn-location").textContent = loc ? `Location: ${loc.label.split(",")[0]}` : "Location: off"; $("btn-location").classList.toggle("ok", !!loc); }
function locError(m) {
  $("loc-error").hidden = true;
  $("loc-error").textContent = "";
  if (m) showError(m);
}
$("btn-location").addEventListener("click", () => { locError(""); dlg.showModal(); });
$("loc-prompt-share").addEventListener("click", () => dlg.showModal());
$("loc-prompt-skip").addEventListener("click", () => { $("loc-prompt").hidden = true; });
$("loc-manual-save").addEventListener("click", async () => { try { const r = await post("/api/location", { mode: "manual", text: $("loc-manual").value }); showLoc(r.location); dlg.close(); locSaved(r.location); } catch (e) { locError(e.message); } });
$("loc-device").addEventListener("click", () => {
  locError("");
  if (!$("loc-geo-consent").checked) return locError("Please tick the box to allow sending your position once to the place-name service, or type a place instead.");
  if (!navigator.geolocation) return locError("This device or browser can't provide location. Please type a place instead.");
  const precision = document.querySelector('input[name="prec"]:checked').value;
  navigator.geolocation.getCurrentPosition(async (pos) => {   // one-shot; never watchPosition
    try { const r = await post("/api/location", { mode: "device", precision, lat: pos.coords.latitude, lng: pos.coords.longitude, share_with_geocoder: true }); showLoc(r.location); dlg.close(); locSaved(r.location); } catch (e) { locError(e.message); }
  }, (err) => locError(err.code === 1 ? "Location permission was denied. That's fine: type a city or postal code instead, or change the permission in your browser or device settings." : "Your position couldn't be determined. Please type a place instead."),
  { enableHighAccuracy: precision === "precise", timeout: 12000, maximumAge: 60000 });
});
$("loc-forget").addEventListener("click", async () => {
  try {
    await del("/api/location");
    showLoc(null); locError(""); dlg.close(); say("Your location has been forgotten.", "sys");
  } catch (e) { locError(e.message); }
});
function locSaved(loc) { $("loc-prompt").hidden = true; say(`Location set to ${loc.label} (${loc.precision}). It is used only for requests that need it, and results based on it are estimates.`, "sys");
  if (S.runId && S.status === "done") say("Want me to redo this search with your location? Just say “check again near me”.", "sys"); }

/* ------------------------------------------------------------------ extras */
$("btn-notify").addEventListener("click", async () => {
  if (!("Notification" in window) || !("serviceWorker" in navigator)) {
    return showError("This browser does not support background notifications. Try an up-to-date browser on HTTPS or localhost.");
  }
  try {
    if (S.notify) {
      S.notify = false;
      localStorage.removeItem("pg_notify");
    }
    else {
      const p = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
      if (p !== "granted") {
        S.notify = false;
        localStorage.removeItem("pg_notify");
        showError("Notification permission wasn't granted. Allow notifications for this site in your browser settings, then try again.");
      } else {
        S.notificationRegistration = await navigator.serviceWorker.ready;
        if (!S.notificationRegistration.showNotification) {
          throw new Error("Background notifications are unavailable in this browser.");
        }
        S.notify = true;
        localStorage.setItem("pg_notify", "1");
      }
    }
  } catch (e) {
    S.notify = false;
    localStorage.removeItem("pg_notify");
    showError("Could not update notification settings: " + e.message);
  }
  updateNotificationButton();
});
function updateNotificationButton() {
  const enabled = S.notify && "Notification" in window && Notification.permission === "granted"
    && "serviceWorker" in navigator && !!S.notificationRegistration?.showNotification;
  S.notify = enabled;
  $("notify-label").textContent = `Notifications: ${enabled ? "Enabled" : "Disabled"}`;
  $("btn-notify").setAttribute("aria-pressed", String(enabled));
  $("btn-notify").classList.toggle("ok", enabled);
  if (!enabled) localStorage.removeItem("pg_notify");
}
async function shareReport() {
  try {
    const res = await fetch(`/api/runs/${S.runId}/export?format=pdf`, { credentials: "same-origin" });
    if (!res.ok) {
      showError("Could not prepare the PDF for sharing. Please try downloading it instead.");
      return;
    }
    const file = new File([await res.blob()], "product-research.pdf", { type: "application/pdf" });
    const url = location.origin + `/api/runs/${S.runId}/export?format=pdf`;
    if (navigator.share) {
      if (navigator.canShare?.({ files: [file] })) await navigator.share({ files: [file], title: "Product research report" });
      else await navigator.share({ title: "Product research report", url });
    } else if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
      banner("PDF link copied. The recipient must sign in to download it.");
    } else {
      showError("This browser cannot share or copy the PDF link. Please download the PDF instead.");
    }
  } catch (e) { if (e.name !== "AbortError") showError("Could not share the PDF: " + e.message); }
}
async function loadHistory() {
  try {
    const { runs } = await get("/api/runs");
    $("history").replaceChildren(...runs.map(r => el("li", {}, el("button", { type: "button", onclick: () => openRun(r.id) }, r.text.slice(0, 60)), el("div", { class: "desk-role" }, `${r.status} · ${r.offers} listings · ${fmtTime(r.updated_at)}`))));
    if (!runs.length) $("history").replaceChildren(el("li", { class: "desk-role" }, "Nothing yet."));
  } catch (e) { showError("Could not load research history: " + e.message); }
}
async function openRun(id) {
  try {
    resetOffice(); const run = await get(`/api/runs/${id}`); setStatus(run.status === "running" ? "running" : run.status);
    attach(id, 0); say(`Opened earlier research: “${run.text}”.`, "sys"); if (isMobile()) go("results");
  } catch (e) { showError("Could not open this research history: " + e.message); }
}

/* --------------------------------------------------------------------- boot */
(async function boot() {
  try {
    S.cfg = await get("/api/config");
    document.querySelector('meta[name="csrf-token"]').content = S.cfg.csrf;
    for (const a of S.cfg.agents) ensureAgent(a, { selected: false, reason: "Waiting for a request.", role: a.role });
    $("chip-search").textContent = S.cfg.status.search_configured ? "Search: live" : "Search: not configured";
    $("chip-search").classList.add(S.cfg.status.search_configured ? "ok" : "bad");
    $("chip-llm").textContent = S.cfg.status.llm_available ? "AI model: on" : "AI model: off";
    $("chip-llm").classList.add(S.cfg.status.llm_available ? "ok" : "bad");
    const warn = [];
    if (!S.cfg.status.search_configured) warn.push("No search provider key is configured on the server, so the agents cannot research products yet.");
    if (!S.cfg.status.llm_available) warn.push("No AI model is configured: non-English requests use basic parsing and replies may remain in English.");
    if (!S.cfg.status.store_persistent) warn.push("History is stored in memory only and will not sync across devices.");
    banner(warn.join(" "));
    showLoc(S.cfg.location);
  } catch (e) { showError("Could not load configuration: " + e.message); }
  refreshSearchUsage();
  setStep("none"); redraw(); loadHistory();
  if ("serviceWorker" in navigator) {
    try {
      await navigator.serviceWorker.register("/sw.js");
      S.notificationRegistration = await navigator.serviceWorker.ready;
    } catch (e) {
      console.error("Could not register the service worker:", e);
    }
  }
  updateNotificationButton();
})();
