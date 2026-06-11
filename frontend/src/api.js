const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function j(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export const startCall = (payload) => j("POST", "/api/calls", payload);
export const hangupCall = (id) => j("POST", `/api/calls/${id}/hangup`);
export const polishTask = (task, language) => j("POST", "/api/polish", { task, language });
export const getHistory = () => j("GET", "/api/history");
export const getHistoryDetail = (id) => j("GET", `/api/history/${id}`);
export const previewUrl = (voice) => `${API_BASE}/api/voice-preview?voice=${voice}`;

export function openMonitor(sessionId, handlers) {
  const wsBase = API_BASE.replace(/^http/, "ws");
  let ws;
  let attempts = 0;
  let closedByApp = false;
  const connect = () => {
    ws = new WebSocket(`${wsBase}/ws/monitor/${sessionId}`);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "transcript") handlers.onTranscript(m);
      else if (m.type === "status") handlers.onStatus(m.status);
      else if (m.type === "latency") handlers.onLatency?.(m.seconds);
      else if (m.type === "summary") handlers.onSummary?.(m.summary);
    };
    ws.onclose = () => {
      if (!closedByApp && attempts < 3) {
        attempts += 1;
        setTimeout(connect, 1000 * attempts);
      }
    };
  };
  connect();
  return { close: () => { closedByApp = true; ws?.close(); } };
}
