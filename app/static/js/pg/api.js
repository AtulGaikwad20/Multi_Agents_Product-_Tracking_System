const csrf = () => document.querySelector('meta[name="csrf-token"]').content;

export async function call(method, url, body) {
  const res = await fetch(url, {
    method, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* non-JSON error */ }
  if (res.status === 401) { location.href = "/login"; throw new Error("signed out"); }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
export const get = (u) => call("GET", u);
export const post = (u, b) => call("POST", u, b || {});
export const del = (u) => call("DELETE", u);

/** Open the run's event stream; resumes after `after`. Returns {close}. */
export function openStream(runId, after, onEvent, onEnd) {
  const es = new EventSource(`/api/runs/${runId}/events?after=${after}`);
  const types = ["run_started", "understanding", "notice", "location_suggested", "location_used", "team_selected",
    "meeting_started", "meeting_message", "meeting_ended", "task_assigned", "agent_status", "agent_step",
    "agent_report", "agent_error", "verification_started", "verification_step", "followup_started",
    "report_ready", "supervisor_say", "run_done", "run_cancelled", "run_error"];
  for (const t of types) es.addEventListener(t, (m) => onEvent(JSON.parse(m.data)));
  es.addEventListener("end", () => { es.close(); onEnd && onEnd(); });
  es.onerror = () => { /* EventSource retries automatically with Last-Event-ID */ };
  return { close: () => es.close() };
}
