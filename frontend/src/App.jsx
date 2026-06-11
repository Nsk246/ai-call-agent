import { useEffect, useRef, useState } from "react";
import {
  startCall, hangupCall, polishTask, getHistory, getHistoryDetail,
  previewUrl, openMonitor,
} from "./api.js";

const VOICES = ["Aoede", "Kore", "Leda", "Zephyr", "Puck", "Charon", "Fenrir", "Orus"];
const TEMPLATES = [
  ["Book a table", "Call the restaurant and book a table for two tonight at 8 PM under the name {name}. Confirm the booking time and ask about parking before ending."],
  ["Confirm appt", "Call to confirm {name}'s appointment. Verify the date and time, and ask if anything needs to be brought along."],
  ["Store hours", "Call the store and ask for today's opening hours and whether the item {name} asked about is in stock."],
  ["Reschedule", "Call to reschedule {name}'s appointment to later this week. Find an available slot, confirm it clearly, and repeat the final date and time back."],
];
const STATUS_TEXT = {
  idle: ["STANDBY", "var(--mut2)"], ringing: ["DIALING", "var(--amber)"],
  live: ["LINE OPEN", "var(--green)"], ended: ["CALL ENDED", "var(--mut)"],
  dropped: ["CONNECTION DROPPED", "var(--red)"], no_answer: ["NO ANSWER", "var(--red)"],
  busy: ["LINE BUSY", "var(--red)"], failed: ["CALL FAILED", "var(--red)"],
};
const isMlm = (t) => /[\u0D00-\u0D7F]/.test(t || "");

function loadContacts() {
  try { return JSON.parse(localStorage.getItem("operator_contacts") || "[]"); }
  catch { return []; }
}

export default function App() {
  const [tab, setTab] = useState("console");
  const [form, setForm] = useState({ caller_name: "", to_number: "", language: "en", task: "", voice: "Aoede" });
  const [status, setStatus] = useState("idle");
  const [transcript, setTranscript] = useState([]);
  const [latency, setLatency] = useState(null);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  const [polishing, setPolishing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [contacts, setContacts] = useState(loadContacts);
  const [history, setHistory] = useState([]);
  const [hSel, setHSel] = useState(null);
  const monRef = useRef(null);
  const sidRef = useRef(null);
  const feedRef = useRef(null);
  const audioRef = useRef(null);
  const liveStart = useRef(null);

  useEffect(() => { feedRef.current?.scrollTo(0, feedRef.current.scrollHeight); }, [transcript]);
  useEffect(() => () => monRef.current?.close(), []);
  useEffect(() => {
    if (status !== "live") return;
    if (!liveStart.current) liveStart.current = Date.now();
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - liveStart.current) / 1000)), 500);
    return () => clearInterval(t);
  }, [status]);
  useEffect(() => { if (tab === "history") refreshHistory(); }, [tab]);

  const inCall = status === "ringing" || status === "live";
  const up = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  async function refreshHistory() {
    try { setHistory((await getHistory()).calls); }
    catch (e) { setError(e.message); }
  }

  async function place() {
    setError(""); setTranscript([]); setSummary(null); setLatency(null);
    setElapsed(0); liveStart.current = null; setBusy(true); setStatus("ringing");
    try {
      const { session_id } = await startCall(form);
      sidRef.current = session_id;
      monRef.current?.close();
      monRef.current = openMonitor(session_id, {
        onTranscript: (m) => setTranscript((t) => [...t, m]),
        onStatus: (s) => setStatus(s),
        onLatency: (s) => setLatency(s),
        onSummary: (s) => setSummary(s),
      });
    } catch (e) { setStatus("failed"); setError(e.message); }
    finally { setBusy(false); }
  }

  async function hangup() {
    try { await hangupCall(sidRef.current); }
    catch (e) { setError(e.message); }
  }

  async function polish() {
    if (!form.task.trim()) return;
    setPolishing(true);
    try {
      const { task } = await polishTask(form.task, form.language);
      setForm((f) => ({ ...f, task }));
    } catch (e) { setError(e.message); }
    finally { setPolishing(false); }
  }

  function preview(v) {
    audioRef.current?.pause();
    audioRef.current = new Audio(previewUrl(v));
    audioRef.current.play().catch(() => setError("Voice preview unavailable (Google TTS credentials needed)"));
  }

  function applyTemplate(text) {
    setForm((f) => ({ ...f, task: text.replaceAll("{name}", f.caller_name || "the caller") }));
  }

  function saveContact() {
    if (!form.to_number.trim()) return;
    const next = [...contacts.filter((c) => c.number !== form.to_number),
      { name: form.caller_name || form.to_number, number: form.to_number }];
    setContacts(next);
    localStorage.setItem("operator_contacts", JSON.stringify(next));
  }

  function delContact(num) {
    const next = contacts.filter((c) => c.number !== num);
    setContacts(next);
    localStorage.setItem("operator_contacts", JSON.stringify(next));
  }

  async function openHistory(id) {
    try { setHSel(await getHistoryDetail(id)); }
    catch (e) { setError(e.message); }
  }

  function exportTranscript(rec) {
    const txt = (rec.transcript || []).map((t) => `${t.role === "agent" ? "AGENT" : "CALLEE"}: ${t.text}`).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
    a.download = `call-${rec.id.slice(0, 8)}.txt`;
    a.click();
  }

  function callAgain(rec) {
    setForm({ caller_name: rec.caller_name, to_number: rec.to_number, language: rec.language, task: rec.task, voice: rec.voice || "Aoede" });
    setTab("console"); setStatus("idle"); setTranscript([]); setSummary(null);
  }

  const [stTxt, stColor] = STATUS_TEXT[status] || STATUS_TEXT.idle;
  const dialCls = status === "live" ? "live" : status === "ringing" ? "ringing"
    : ["dropped", "no_answer", "busy", "failed"].includes(status) ? "bad" : "";

  const Summary = ({ s }) => {
    const cls = s.outcome === "accomplished" ? ["", "ok", "ti-circle-check", "TASK ACCOMPLISHED"]
      : s.outcome === "partial" ? ["partial", "warn", "ti-progress", "PARTIALLY DONE"]
      : s.outcome === "failed" ? ["failed", "bad", "ti-circle-x", "NOT ACCOMPLISHED"]
      : ["partial", "warn", "ti-help-circle", "OUTCOME UNCLEAR"];
    return (
      <div className={`sumcard ${cls[0]}`}>
        <div className={`sumhead ${cls[1]}`}>{cls[3]}</div>
        <p className="body">{s.headline}</p>
        {s.details?.length > 0 && (
          <div className="facts">
            {s.details.map((d, i) => <span key={i}>{d.label} <b>{String(d.value)}</b></span>)}
          </div>
        )}
      </div>
    );
  };

  const Feed = ({ items }) => (
    <div className="feed" ref={feedRef}>
      {items.length === 0 && <p className="empty">Transcript appears here once the line opens.</p>}
      {items.map((m, i) => (
        <div key={i} className={`bub ${m.role === "callee" ? "callee" : ""}`}>
          <p className="who">{m.role === "agent" ? "AGENT" : "CALLEE"}</p>
          <p className={`tx ${isMlm(m.text) ? "mlm" : ""}`}>{m.text}</p>
        </div>
      ))}
    </div>
  );

  return (
    <div className="shell">
      <div className="topbar">
        <div className="brand"><span className="brand-dot">☏</span>THE OPERATOR</div>
        <div className="tabs">
          {["console", "history", "contacts"].map((t) => (
            <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {tab === "console" && (
        <div className="grid">
          <div className="left">
            <p className="lbl">New call</p>
            <div className="row"><input placeholder="Calling on behalf of (your name)" value={form.caller_name} onChange={up("caller_name")} /></div>
            <div className="row" style={{ display: "flex", gap: 6 }}>
              <input placeholder="+1 312 555 0141" value={form.to_number} onChange={up("to_number")} className="mono" style={{ fontSize: 12.5 }} />
              <button className="toolbtn" title="Save contact" onClick={saveContact}>＋</button>
            </div>
            {contacts.length > 0 && (
              <div className="row chips">
                {contacts.slice(0, 6).map((c) => (
                  <button key={c.number} className="chip" onClick={() => setForm((f) => ({ ...f, to_number: c.number, caller_name: f.caller_name }))}>{c.name}</button>
                ))}
              </div>
            )}
            <div className="row chips">
              <button className={`chip pill ${form.language === "en" ? "on" : ""}`} onClick={() => setForm((f) => ({ ...f, language: "en" }))}>English</button>
              <button className={`chip pill mlm ${form.language === "ml" ? "on" : ""}`} onClick={() => setForm((f) => ({ ...f, language: "ml" }))}>മലയാളം</button>
            </div>
            <p className="lbl">Task</p>
            <div className="row">
              <textarea rows={4} placeholder="What should the agent accomplish?" value={form.task} onChange={up("task")} />
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 5 }}>
                <button className="linkbtn" onClick={polish} disabled={polishing || !form.task.trim()}>
                  {polishing ? "Polishing…" : "✦ Polish task"}
                </button>
              </div>
            </div>
            <div className="row chips">
              {TEMPLATES.map(([label, text]) => (
                <button key={label} className="chip" onClick={() => applyTemplate(text)}>{label}</button>
              ))}
            </div>
            <p className="lbl">Voice</p>
            <div className="row chips">
              {VOICES.map((v) => (
                <button key={v} className={`chip ${form.voice === v ? "on" : ""}`}
                  onClick={() => setForm((f) => ({ ...f, voice: v }))}
                  onDoubleClick={() => preview(v)} title="Double-click to preview">
                  ▸ {v}
                </button>
              ))}
            </div>
            <button className="cta" onClick={place} disabled={busy || inCall || !form.to_number.trim() || !form.task.trim()}>
              {inCall ? "Call in progress…" : "Place call"}
            </button>
            {error && <p className="err">{error}</p>}
          </div>

          <div className="right">
            <div className="callhead">
              <div className={`dial ${dialCls}`}>
                <div className="ring" /><div className="ring2" />
                <div className="ic">{status === "live" ? "☎" : "✆"}</div>
              </div>
              <div>
                <div className="statusline" style={{ color: stColor }}>{stTxt}</div>
                <div className="timer mono">{fmt(elapsed)}</div>
              </div>
              <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
                {latency != null && status === "live" && (
                  <div className="hud">
                    <p className="lbl" style={{ margin: 0 }}>Response</p>
                    <p className="mono" style={{ color: "var(--amber)", fontSize: 14 }}>{latency.toFixed(2)}s</p>
                  </div>
                )}
                {inCall && <button className="hangup" onClick={hangup}>✕ Hang up</button>}
              </div>
            </div>
            {summary && <Summary s={summary} />}
            <Feed items={transcript} />
          </div>
        </div>
      )}

      {tab === "history" && (
        <div className="grid" style={{ gridTemplateColumns: "280px minmax(0,1fr)" }}>
          <div className="hlist">
            {history.length === 0 && <p className="empty">No calls yet.</p>}
            {history.map((h) => (
              <button key={h.id} className={`hcard ${hSel?.id === h.id ? "sel" : ""}`} onClick={() => openHistory(h.id)}>
                <div className="t">
                  <span>{h.caller_name || h.to_number}</span>
                  <span className={`tag ${h.summary?.outcome === "accomplished" ? "ok" : ["failed", "no_answer", "busy", "dropped"].includes(h.status) || h.summary?.outcome === "failed" ? "bad" : "mid"}`}>
                    {h.status === "ended" ? (h.summary?.outcome || "done").toUpperCase() : h.status.replace("_", " ").toUpperCase()}
                  </span>
                </div>
                <div className="s">{(h.task || "").slice(0, 46)}{h.task?.length > 46 ? "…" : ""}</div>
                <div className="m mono">{new Date(h.created_at * 1000).toLocaleString()} · {fmt(h.duration_sec || 0)}</div>
              </button>
            ))}
          </div>
          <div className="right">
            {!hSel && <p className="empty">Select a call to view its outcome and transcript.</p>}
            {hSel && (
              <>
                <div className="callhead" style={{ alignItems: "baseline" }}>
                  <span style={{ fontSize: 17 }}>{hSel.caller_name || hSel.to_number}</span>
                  <span className="mono" style={{ fontSize: 12, color: "var(--mut2)" }}>{hSel.to_number} · {fmt(hSel.duration_sec || 0)}</span>
                  <div className="toolrow" style={{ marginLeft: "auto" }}>
                    <button className="toolbtn" onClick={() => exportTranscript(hSel)}>↓ Export</button>
                    <button className="toolbtn" onClick={() => callAgain(hSel)}>☏ Call again</button>
                  </div>
                </div>
                {hSel.summary && <Summary s={hSel.summary} />}
                <Feed items={hSel.transcript || []} />
              </>
            )}
          </div>
        </div>
      )}

      {tab === "contacts" && (
        <div className="contacts">
          <p className="lbl">Saved contacts</p>
          {contacts.length === 0 && <p className="empty">No contacts saved. Use ＋ next to the number field on the console.</p>}
          {contacts.map((c) => (
            <div key={c.number} className="crow">
              <input value={c.name} readOnly style={{ flex: 1 }} />
              <input value={c.number} readOnly className="mono" style={{ flex: 1, fontSize: 12.5 }} />
              <button className="toolbtn" onClick={() => { setForm((f) => ({ ...f, to_number: c.number })); setTab("console"); }}>Use</button>
              <button className="toolbtn" onClick={() => delContact(c.number)}>✕</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
