import { useEffect, useRef, useState } from "react";
import { startCall, openMonitor } from "./api.js";

const STATUS_LABEL = {
  idle: "Idle",
  ringing: "Ringing…",
  live: "Live",
  ended: "Ended",
  error: "Error",
};

export default function App() {
  const [form, setForm] = useState({
    caller_name: "",
    to_number: "",
    language: "en",
    task: "",
  });
  const [status, setStatus] = useState("idle");
  const [transcript, setTranscript] = useState([]);
  const [error, setError] = useState("");
  const wsRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [transcript]);

  useEffect(() => () => wsRef.current?.close(), []);

  const update = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const canStart =
    form.to_number.trim() && form.task.trim() && status !== "ringing" && status !== "live";

  async function handleStart() {
    setError("");
    setTranscript([]);
    setStatus("ringing");
    try {
      const { session_id } = await startCall(form);
      wsRef.current?.close();
      wsRef.current = openMonitor(session_id, {
        onTranscript: (m) =>
          setTranscript((t) => {
            // Agent replies stream in as sentence segments; keep them in one bubble.
            if (m.cont && t.length && t[t.length - 1].role === "agent") {
              const copy = t.slice();
              copy[copy.length - 1] = {
                ...copy[copy.length - 1],
                text: `${copy[copy.length - 1].text} ${m.text}`,
              };
              return copy;
            }
            return [...t, m];
          }),
        onStatus: (s) => setStatus(s),
        onError: (m) => setError(m),
      });
    } catch (e) {
      setStatus("error");
      setError(e.message);
    }
  }

  return (
    <div className="wrap">
      <header>
        <h1>AI Call Agent</h1>
        <span className={`pill pill-${status}`}>{STATUS_LABEL[status] || status}</span>
      </header>

      <section className="panel">
        <label>
          Calling on behalf of
          <input
            value={form.caller_name}
            onChange={update("caller_name")}
            placeholder="Your name"
          />
        </label>

        <label>
          Phone number to call
          <input
            value={form.to_number}
            onChange={update("to_number")}
            placeholder="+91 98765 43210"
          />
        </label>

        <label>
          Language
          <select value={form.language} onChange={update("language")}>
            <option value="en">English</option>
            <option value="ml">Malayalam (മലയാളം)</option>
          </select>
        </label>

        <label>
          What should the agent accomplish?
          <textarea
            rows={3}
            value={form.task}
            onChange={update("task")}
            placeholder="e.g. Book a table for two at 8pm on Friday and confirm parking."
          />
        </label>

        <button onClick={handleStart} disabled={!canStart}>
          {status === "ringing" || status === "live" ? "Call in progress…" : "Place call"}
        </button>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="transcript" ref={logRef}>
        {transcript.length === 0 && <p className="muted">Live transcript will appear here.</p>}
        {transcript.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            <span className="who">{m.role === "agent" ? "Agent" : "Callee"}</span>
            <p>{m.text}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
