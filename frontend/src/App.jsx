import { useEffect, useRef, useState } from "react";
import {
  startCall, hangupCall, polishTask, getHistory, getHistoryDetail,
  previewUrl, openMonitor,
} from "./api.js";

const VOICES = ["Aoede", "Kore", "Leda", "Zephyr", "Puck", "Charon", "Fenrir", "Orus"];
const TEMPLATES = [
  ["Book a table", "Call the restaurant and book a table for two tonight at 8 PM under the name {name}. Confirm the time and ask about parking before ending."],
  ["Confirm appt", "Call to confirm {name}'s appointment. Verify the date and time, and ask if anything needs to be brought along."],
  ["Store hours", "Call the store and ask for today's opening hours and whether the item {name} asked about is in stock."],
  ["Reschedule", "Call to reschedule {name}'s appointment to later this week. Find an available slot and repeat the final date and time back."],
];
const ST = {
  idle: ["STANDING BY", "st-idle"], ringing: ["DIALING", "st-ringing"],
  live: ["LINE OPEN", "st-live"], ended: ["TRANSMISSION ENDED", "st-idle"],
  dropped: ["LINE DROPPED", "st-bad"], no_answer: ["NO ANSWER", "st-bad"],
  busy: ["LINE BUSY", "st-bad"], failed: ["CALL FAILED", "st-bad"],
};
const isMlm = (t) => /[\u0D00-\u0D7F]/.test(t || "");
const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
const loadContacts = () => {
  try { return JSON.parse(localStorage.getItem("operator_contacts") || "[]"); }
  catch { return []; }
};

function PrintLine({ role, text, animate, onTick }) {
  const [n, setN] = useState(animate ? 0 : text.length);
  useEffect(() => {
    if (!animate) return;
    let i = 0;
    const step = Math.max(1, Math.round(text.length / 90));
    const t = setInterval(() => {
      i = Math.min(text.length, i + step);
      setN(i); onTick?.();
      if (i >= text.length) clearInterval(t);
    }, 16);
    return () => clearInterval(t);
  }, [text, animate]);
  const printing = n < text.length;
  return (
    <p className={`line ${role}`}>
      <span className="spk">{role === "agent" ? "AGENT\u00A0\u00A0>>" : "CALLEE >>"}</span>{" "}
      <span className={isMlm(text) ? "mlm" : ""}>{text.slice(0, n)}</span>
      {printing && <span className="caret" />}
    </p>
  );
}

function Receipt({ s }) {
  const cls = s.outcome === "accomplished" ? ["ok", "ACCOMPLISHED"]
    : s.outcome === "partial" ? ["mid", "PARTIAL"]
    : s.outcome === "failed" ? ["bad", "NOT DONE"] : ["mid", "UNCLEAR"];
  return (
    <div className="receipt">
      <span className={`stamp ${cls[0]}`}>{cls[1]}</span>
      <p className="rl">OUTCOME RECEIPT</p>
      <p className="rb">{s.headline}</p>
      {s.details?.length > 0 && (
        <p className="rf">
          {s.details.map((d, i) => (
            <span key={i}>{String(d.label).toUpperCase()}: <b>{String(d.value)}</b>
              {i < s.details.length - 1 ? "\u00A0\u00A0·\u00A0\u00A0" : ""}</span>
          ))}
        </p>
      )}
    </div>
  );
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
  const [playing, setPlaying] = useState("");
  const [contacts, setContacts] = useState(loadContacts);
  const [history, setHistory] = useState([]);
  const [hSel, setHSel] = useState(null);
  const monRef = useRef(null);
  const sidRef = useRef(null);
  const feedRef = useRef(null);
  const audioRef = useRef(null);
  const liveStart = useRef(null);
  const animFrom = useRef(0);

  const scrollFeed = () => feedRef.current?.scrollTo(0, feedRef.current.scrollHeight);
  useEffect(scrollFeed, [transcript]);
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

  async function refreshHistory() {
    try { setHistory((await getHistory()).calls); } catch (e) { setError(e.message); }
  }

  async function place() {
    setError(""); setTranscript([]); setSummary(null); setLatency(null);
    setElapsed(0); liveStart.current = null; animFrom.current = 0;
    setBusy(true); setStatus("ringing");
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
    try { await hangupCall(sidRef.current); } catch (e) { setError(e.message); }
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

  function preview(v, e) {
    e.stopPropagation();
    audioRef.current?.pause();
    setPlaying(v);
    audioRef.current = new Audio(previewUrl(v));
    audioRef.current.onended = () => setPlaying("");
    audioRef.current.play().catch(() => {
      setPlaying(""); setError("Voice preview unavailable");
    });
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
    try { setHSel(await getHistoryDetail(id)); } catch (e) { setError(e.message); }
  }
  function exportTranscript(rec) {
    const txt = (rec.transcript || []).map((t) => `${t.role === "agent" ? "AGENT " : "CALLEE"} >> ${t.text}`).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
    a.download = `transmission-${rec.id.slice(0, 8)}.txt`;
    a.click();
  }
  function callAgain(rec) {
    setForm({ caller_name: rec.caller_name, to_number: rec.to_number, language: rec.language, task: rec.task, voice: rec.voice || "Aoede" });
    setTab("console"); setStatus("idle"); setTranscript([]); setSummary(null);
  }

  const [stWord, stCls] = ST[status] || ST.idle;
  const today = new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }).toUpperCase();
  const txNo = String((history.length || 0) + 1).padStart(4, "0");
  const slipTag = (h) => {
    if (["failed", "no_answer", "busy", "dropped"].includes(h.status)) return ["bad", h.status.replace("_", " ").toUpperCase()];
    const o = h.summary?.outcome;
    if (o === "accomplished") return ["ok", "ACCOMPLISHED"];
    if (o === "failed") return ["bad", "NOT DONE"];
    return ["mid", (o || "FILED").toUpperCase()];
  };

  return (
    <div className="sheet">
      <div className="masthead">
        <h1>THE OPERATOR</h1>
        <div className="tabs">
          {[["console", "CONSOLE"], ["history", "TRANSMISSIONS"], ["contacts", "DIRECTORY"]].map(([k, l]) => (
            <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
      </div>
      <div className="subline">
        <span>AI CALLING BUREAU · EST. 2026</span>
        <span>{today}</span>
      </div>

      {tab === "console" && (
        <div className="layout">
          <div className="panel">
            <p className="formhead">COMPOSE TRANSMISSION</p>
            <div className="field">
              <label>ON BEHALF OF</label>
              <input value={form.caller_name} onChange={up("caller_name")} placeholder="Your name" />
            </div>
            <div className="field">
              <label>TO (PHONE)</label>
              <div className="numrow">
                <input value={form.to_number} onChange={up("to_number")} placeholder="+1 312 555 0141" />
                <button className="tool" title="File in directory" onClick={saveContact}>FILE</button>
              </div>
            </div>
            {contacts.length > 0 && (
              <div className="field tagrow">
                {contacts.slice(0, 6).map((c) => (
                  <button key={c.number} className="tag" onClick={() => setForm((f) => ({ ...f, to_number: c.number }))}>{c.name}</button>
                ))}
              </div>
            )}
            <div className="field tagrow">
              <button className={`tag ${form.language === "en" ? "on" : ""}`} onClick={() => setForm((f) => ({ ...f, language: "en" }))}>ENGLISH</button>
              <button className={`tag mlm ${form.language === "ml" ? "on" : ""}`} onClick={() => setForm((f) => ({ ...f, language: "ml" }))}>മലയാളം</button>
            </div>
            <div className="field">
              <label>INSTRUCTIONS TO AGENT</label>
              <textarea rows={5} value={form.task} onChange={up("task")} placeholder="What should the agent accomplish on this call?" />
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, alignItems: "center" }}>
                <div className="tagrow">
                  {TEMPLATES.map(([l, t]) => (
                    <button key={l} className="tag" style={{ padding: "5px 10px", fontSize: 12 }}
                      onClick={() => setForm((f) => ({ ...f, task: t.replaceAll("{name}", f.caller_name || "the caller") }))}>{l}</button>
                  ))}
                </div>
                <button className="linky" onClick={polish} disabled={polishing || !form.task.trim()}>
                  {polishing ? "polishing…" : "polish ✦"}
                </button>
              </div>
            </div>
            <div className="field">
              <label>AGENT VOICE — ▸ TO AUDITION</label>
              <div className="tagrow">
                {VOICES.map((v) => (
                  <button key={v} className={`tag ${form.voice === v ? "on" : ""} ${playing === v ? "playing" : ""}`}
                    onClick={() => setForm((f) => ({ ...f, voice: v }))}>
                    <span onClick={(e) => preview(v, e)}>▸</span> {v}
                  </button>
                ))}
              </div>
            </div>
            <button className="send" onClick={place} disabled={busy || inCall || !form.to_number.trim() || !form.task.trim()}>
              {inCall ? "TRANSMITTING…" : "PLACE CALL"}
            </button>
            {error && <p className="errline">!! {error}</p>}
          </div>

          <div className="txpane panel raised">
            <div className="txhead">
              <div>
                <p className={`statusword ${stCls}`} style={{ margin: "0 0 4px" }}>
                  <span className="pip" />{stWord}{status === "live" && form.caller_name ? "" : ""}
                </p>
                <span className="bigtime">{fmt(elapsed)}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
                {latency != null && status === "live" && (
                  <span className="hud">RESPONSE <b>{latency.toFixed(2)}s</b></span>
                )}
                <span className="hud">TX No. {txNo}</span>
                {inCall && <button className="endbtn" onClick={hangup}>✕ END CALL</button>}
              </div>
            </div>
            <hr className="perf" />
            <div className="feed" ref={feedRef}>
              {transcript.length === 0 && (
                <p className="feedempty">
                  {status === "ringing" ? "— CONNECTING TRANSMISSION —" : "— AWAITING TRANSMISSION —"}
                </p>
              )}
              {transcript.map((m, i) => (
                <PrintLine key={i} role={m.role} text={m.text}
                  animate={m.role === "agent" && status === "live"} onTick={scrollFeed} />
              ))}
              {status === "live" && <p className="line"><span className="caret" /></p>}
              {status === "ended" && !summary && transcript.length > 0 && (
                <p className="developing">— DEVELOPING RECEIPT —</p>
              )}
            </div>
            {!inCall && transcript.length > 0 && (
              <div className="feedtools">
                <button className="tool quiet" onClick={() => {
                  navigator.clipboard?.writeText(transcript.map((t) =>
                    `${t.role === "agent" ? "AGENT " : "CALLEE"} >> ${t.text}`).join("\n"));
                }}>COPY</button>
              </div>
            )}
            {summary && <><hr className="perf" /><Receipt s={summary} /></>}
          </div>
        </div>
      )}

      {tab === "history" && (
        <div className="hgrid">
          <div className="panel">
            <p className="formhead">FILED TRANSMISSIONS</p>
            {history.length === 0 && <p className="feedempty">— NONE FILED —</p>}
            {history.map((h, i) => {
              const [tc, tl] = slipTag(h);
              return (
                <button key={h.id} className={`slip ${hSel?.id === h.id ? "sel" : ""}`} onClick={() => openHistory(h.id)}>
                  <span className="t"><span>{h.caller_name || h.to_number}</span>
                    <span className={`sliptag ${tc}`}>{tl}</span></span>
                  <span className="s" style={{ display: "block" }}>{(h.task || "").slice(0, 60)}{h.task?.length > 60 ? "…" : ""}</span>
                  <span className="m" style={{ display: "block" }}>
                    No. {String(history.length - i).padStart(4, "0")} · {new Date(h.created_at * 1000).toLocaleString()} · {fmt(h.duration_sec || 0)}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="panel raised">
            {!hSel && <p className="feedempty">— SELECT A TRANSMISSION TO REVIEW —</p>}
            {hSel && (
              <>
                <div className="dethead">
                  <span className="nm">{hSel.caller_name || hSel.to_number}</span>
                  <span className="mt">{hSel.to_number} · {fmt(hSel.duration_sec || 0)}</span>
                  <div className="toolrow" style={{ marginLeft: "auto" }}>
                    <button className="tool quiet" onClick={() => exportTranscript(hSel)}>EXPORT</button>
                    <button className="tool" onClick={() => callAgain(hSel)}>CALL AGAIN</button>
                  </div>
                </div>
                {hSel.summary && <Receipt s={hSel.summary} />}
                <hr className="perf" style={{ margin: "18px 0 0" }} />
                <div className="feed">
                  {(hSel.transcript || []).map((m, i) => (
                    <PrintLine key={i} role={m.role} text={m.text} animate={false} />
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {tab === "contacts" && (
        <div style={{ marginTop: 28 }}>
          <p className="formhead">DIRECTORY</p>
          {contacts.length === 0 && <p className="feedempty">— EMPTY — FILE NUMBERS FROM THE CONSOLE —</p>}
          {contacts.map((c) => (
            <div key={c.number} className="dirrow">
              <input value={c.name} readOnly />
              <input value={c.number} readOnly />
              <button className="tool" onClick={() => { setForm((f) => ({ ...f, to_number: c.number })); setTab("console"); }}>USE</button>
              <button className="tool" onClick={() => delContact(c.number)}>REMOVE</button>
            </div>
          ))}
        </div>
      )}

      <div className="footer">
        <span>THE OPERATOR — AI CALL AGENT</span>
        <span>EN · ML BILINGUAL SERVICE</span>
      </div>
    </div>
  );
}
