"""FastAPI app: REST to start calls, Twilio media-stream bridge, monitor WS.

Latency design
--------------
Each turn streams Claude tokens, splits them into sentences on the fly, and
synthesizes + sends each sentence the moment it's ready - the caller hears
sentence one while sentence two is still being generated. A turn is a single
cancellable task, so a barge-in stops the LLM, pending TTS, and playback
together and flushes Twilio's buffer. The TTS channel is prewarmed while the
phone is still ringing.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import END_TOKEN, CallAgent
from .audio import chunk_bytes, sanitize_for_tts, split_sentences
from .config import get_settings
from .session import CallSession, manager
from .stt import StreamingSTT
from .tts import prewarm, synthesize
from .twilio_client import place_call

settings = get_settings()
app = FastAPI(title="AI Call Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Per-language STT/TTS configuration.
# ---------------------------------------------------------------------------
def _lang_config(language: str) -> dict:
    if language == "ml":
        alt = [settings.english_stt_code] if settings.enable_code_switching else []
        return dict(
            stt_code=settings.malayalam_stt_code,
            stt_alt=alt,
            stt_model="default",   # telephony model isn't offered for ml-IN
            stt_enhanced=False,
            tts_code=settings.malayalam_stt_code,
            tts_voice=settings.malayalam_tts_voice,
        )
    return dict(
        stt_code=settings.english_stt_code,
        stt_alt=[],
        stt_model="telephony",
        stt_enhanced=True,
        tts_code=settings.english_stt_code,
        tts_voice=settings.english_tts_voice,
    )


class StartCallRequest(BaseModel):
    to_number: str = Field(..., examples=["+919876543210"])
    task: str = Field(..., description="What the agent should accomplish on the call")
    language: str = Field("en", pattern="^(en|ml)$")
    caller_name: str = Field("the caller")


@app.post("/api/calls")
async def start_call(req: StartCallRequest):
    session = await manager.create(
        task=req.task,
        language=req.language,
        to_number=req.to_number,
        caller_name=req.caller_name,
    )
    try:
        # Twilio's SDK is blocking; keep it off the event loop.
        call_sid = await asyncio.to_thread(
            place_call, to_number=req.to_number, session_id=session.session_id
        )
    except Exception as exc:  # noqa: BLE001
        await manager.end(session.session_id)
        raise HTTPException(status_code=502, detail=f"Twilio call failed: {exc}") from exc

    # Warm the TTS channel while the phone is still ringing, so the opening
    # line doesn't pay the first-RPC handshake.
    cfg = _lang_config(req.language)
    asyncio.get_running_loop().create_task(prewarm(cfg["tts_code"], cfg["tts_voice"]))

    session.status = "ringing"
    return {"session_id": session.session_id, "call_sid": call_sid, "status": session.status}


@app.get("/api/calls/{session_id}")
async def get_call(session_id: str):
    session = manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")
    return {"status": session.status, "transcript": session.transcript}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.websocket("/ws/monitor/{session_id}")
async def monitor_ws(ws: WebSocket, session_id: str):
    await ws.accept()
    session = manager.get(session_id)
    if not session:
        await ws.send_json({"type": "error", "message": "unknown session"})
        await ws.close()
        return
    await ws.send_json({"type": "status", "status": session.status})
    for event in session.transcript:
        await ws.send_json(event)
    session.monitors.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        session.monitors.discard(ws)


# Ignore barge-in for this long after the agent's audio starts playing, so
# line noise / acoustic echo of its own first syllable can't clip it.
_BARGE_IN_GRACE = 0.5
# Interim text must be at least this long to count as a real interruption
# (echo and breath noise often produce 1-3 char interims).
_BARGE_IN_MIN_CHARS = 4


class TwilioBridge:
    def __init__(self, ws: WebSocket, session: CallSession) -> None:
        self.ws = ws
        self.session = session
        self.loop = asyncio.get_running_loop()
        self.stream_sid: str | None = None
        self.cfg = _lang_config(session.language)
        self.agent = CallAgent(
            task=session.task, language=session.language, caller_name=session.caller_name
        )
        self.stt: StreamingSTT | None = None

        self.agent_speaking = False
        self.speak_started: float | None = None
        self.turn_task: asyncio.Task | None = None
        self.turn_lock = asyncio.Lock()
        self.marks: dict[str, asyncio.Event] = {}
        self.ending = False

    # ---- transcript callbacks (scheduled onto the loop from the STT thread) ----
    async def on_interim(self, text: str) -> None:
        if (
            self.agent_speaking
            and len(text) >= _BARGE_IN_MIN_CHARS
            and self.speak_started is not None
            and (time.monotonic() - self.speak_started) > _BARGE_IN_GRACE
        ):
            await self._stop_speaking()

    async def on_final(self, text: str) -> None:
        if self.ending:
            return
        await self.session.emit({"type": "transcript", "role": "callee", "text": text})
        async with self.turn_lock:
            if self.ending:
                return
            # If the agent is still talking (short utterances can reach a final
            # result without an interim ever firing), silence it FULLY - cancel
            # the turn AND flush Twilio's audio buffer - before answering.
            await self._stop_speaking()
            self.turn_task = asyncio.create_task(self._run_turn(user_text=text))

    # ---- the cancellable turn pipeline ----
    async def _run_turn(self, *, user_text: str | None = None, opening: bool = False) -> None:
        self.agent_speaking = True
        self.speak_started = None
        seg_q: asyncio.Queue = asyncio.Queue()
        synth_tasks: list[asyncio.Task] = []
        should_end = False

        async def produce() -> None:
            nonlocal should_end
            buffer = ""
            gen = self.agent.stream_opening() if opening else self.agent.stream_reply(user_text)
            async for delta in gen:
                buffer += delta
                segments, buffer = split_sentences(buffer)
                for seg in segments:
                    should_end = should_end or END_TOKEN in seg
                    await self._enqueue_segment(seg, seg_q, synth_tasks)
            should_end = should_end or END_TOKEN in buffer
            await self._enqueue_segment(buffer, seg_q, synth_tasks)
            await seg_q.put(None)

        async def consume() -> None:
            first = True
            spoke = False
            while True:
                item = await seg_q.get()
                if item is None:
                    break
                synth_task, text = item
                audio = await synth_task
                event = {"type": "transcript", "role": "agent", "text": text}
                if not first:
                    event["cont"] = True
                await self.session.emit(event)
                first = False
                await self._send_audio(audio)
                spoke = True
            if spoke:
                await self._await_playback(timeout=30)

        try:
            await asyncio.gather(produce(), consume())
        except asyncio.CancelledError:
            for t in synth_tasks:
                if not t.done():
                    t.cancel()
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[turn] error: {exc}")
            for t in synth_tasks:
                if not t.done():
                    t.cancel()
        finally:
            self.agent_speaking = False

        if should_end and not self.ending:
            await self._hang_up()

    async def _enqueue_segment(self, raw: str, seg_q: asyncio.Queue, tasks: list) -> None:
        text = sanitize_for_tts(raw.replace(END_TOKEN, ""))
        if not text:
            return
        task = asyncio.create_task(
            synthesize(text, self.cfg["tts_code"], self.cfg["tts_voice"])
        )
        tasks.append(task)
        await seg_q.put((task, text))

    # ---- Twilio audio I/O ----
    async def _send_audio(self, mulaw: bytes) -> None:
        if self.speak_started is None:
            self.speak_started = time.monotonic()
        for chunk in chunk_bytes(mulaw):
            payload = base64.b64encode(chunk).decode("ascii")
            await self.ws.send_json(
                {"event": "media", "streamSid": self.stream_sid, "media": {"payload": payload}}
            )
            await asyncio.sleep(0)  # yield so a barge-in can cancel promptly

    async def _await_playback(self, *, timeout: float) -> None:
        name = uuid.uuid4().hex
        evt = asyncio.Event()
        self.marks[name] = evt
        try:
            await self.ws.send_json(
                {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}}
            )
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self.marks.pop(name, None)

    async def _stop_speaking(self) -> None:
        """Silence the agent NOW: cancel its turn and flush Twilio's buffer."""
        task = self.turn_task  # capture: on_final may start a newer turn after us
        self.agent_speaking = False
        try:
            await self.ws.send_json({"event": "clear", "streamSid": self.stream_sid})
        except Exception:  # noqa: BLE001
            pass
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.turn_task is task:
            self.turn_task = None

    async def _hang_up(self) -> None:
        if self.ending:
            return
        self.ending = True
        await self.session.emit({"type": "status", "status": "ended"})
        await manager.end(self.session.session_id)
        if self.stt:
            self.stt.stop()
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- Twilio receive loop ----
    async def run(self) -> None:
        async for message in self.ws.iter_text():
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                self.stream_sid = data["start"]["streamSid"]
                self.session.status = "live"
                await self.session.emit({"type": "status", "status": "live"})
                self.stt = StreamingSTT(
                    language_code=self.cfg["stt_code"],
                    alternative_language_codes=self.cfg["stt_alt"],
                    loop=self.loop,
                    on_interim=self.on_interim,
                    on_final=self.on_final,
                    model=self.cfg["stt_model"],
                    use_enhanced=self.cfg["stt_enhanced"],
                )
                self.stt.start()
                async with self.turn_lock:
                    self.turn_task = asyncio.create_task(self._run_turn(opening=True))

            elif event == "media":
                if self.stt:
                    self.stt.feed(base64.b64decode(data["media"]["payload"]))

            elif event == "mark":
                evt = self.marks.get(data.get("mark", {}).get("name"))
                if evt:
                    evt.set()

            elif event == "stop":
                break

    async def cleanup(self) -> None:
        t = self.turn_task
        self.turn_task = None
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        if self.stt:
            self.stt.stop()
        if self.session.status != "ended":
            self.session.status = "ended"
            await self.session.emit({"type": "status", "status": "ended"})


@app.websocket("/ws/twilio/{session_id}")
async def twilio_ws(ws: WebSocket, session_id: str):
    await ws.accept()
    session = manager.get(session_id)
    if not session:
        await ws.close()
        return
    bridge = TwilioBridge(ws, session)
    try:
        await bridge.run()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"[twilio_ws] error: {exc}")
    finally:
        await bridge.cleanup()
