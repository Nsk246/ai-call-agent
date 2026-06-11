const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export async function startCall(payload) {
  const res = await fetch(`${API_BASE}/api/calls`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export function openMonitor(sessionId, handlers) {
  const wsBase = API_BASE.replace(/^http/, "ws");
  const ws = new WebSocket(`${wsBase}/ws/monitor/${sessionId}`);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "transcript") handlers.onTranscript(msg);
    else if (msg.type === "status") handlers.onStatus(msg.status);
    else if (msg.type === "error") handlers.onError?.(msg.message);
  };
  ws.onclose = () => handlers.onClose?.();
  return ws;
}
