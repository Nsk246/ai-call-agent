"""FastAPI app: calls, history, hangup, polish, voice preview, Twilio callbacks."""
from __future__ import annotations

import asyncio
import io
import wave

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db
from .ai_helpers import polish_task
from .config import get_settings
from .gemini_live import GeminiBridge
from .session import manager
from .twilio_client import hangup_call, place_call

settings = get_settings()
app = FastAPI(title="The Operator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"], allow_headers=["*"],
)

VOICES = ["Aoede", "Kore", "Leda", "Zephyr", "Puck", "Charon", "Fenrir", "Orus"]


class StartCallRequest(BaseModel):
    to_number: str = Field(..., examples=["+919876543210"])
    task: str
    language: str = Field("en", pattern="^(en|ml)$")
    caller_name: str = "the caller"
    voice: str = "Aoede"


class PolishRequest(BaseModel):
    task: str
    language: str = "en"


@app.post("/api/calls")
async def start_call(req: StartCallRequest):
    voice = req.voice if req.voice in VOICES else "Aoede"
    session = await manager.create(
        task=req.task, language=req.language, to_number=req.to_number,
        caller_name=req.caller_name, voice=voice,
    )
    try:
        call_sid = await asyncio.to_thread(
            place_call, to_number=req.to_number, session_id=session.session_id)
    except Exception as exc:  # noqa: BLE001
        session.status = "failed"
        raise HTTPException(status_code=502, detail=f"Twilio call failed: {exc}") from exc
    session.call_sid = call_sid
    session.status = "ringing"
    try:
        db.insert_call(session)
    except Exception as exc:  # noqa: BLE001
        print(f"[db] insert failed: {exc}")
    return {"session_id": session.session_id, "status": session.status}


@app.post("/api/calls/{session_id}/hangup")
async def hangup(session_id: str):
    session = manager.get(session_id)
    if not session or not session.call_sid:
        raise HTTPException(status_code=404, detail="Unknown session")
    try:
        await asyncio.to_thread(hangup_call, session.call_sid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Hangup failed: {exc}") from exc
    return {"ok": True}


@app.get("/api/calls/{session_id}")
async def get_call(session_id: str):
    session = manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")
    return {"status": session.status, "transcript": session.transcript,
            "summary": session.summary}


@app.get("/api/history")
async def history():
    try:
        return {"calls": db.list_calls()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/history/{call_id}")
async def history_detail(call_id: str):
    rec = db.get_call(call_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    return rec


@app.post("/api/polish")
async def polish(req: PolishRequest):
    try:
        return {"task": await polish_task(req.task, req.language)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/voices")
async def voices():
    return {"voices": VOICES}


_preview_cache: dict[str, bytes] = {}


@app.get("/api/voice-preview")
async def voice_preview(voice: str = "Aoede"):
    """Best-effort voice sample via Cloud TTS Chirp3-HD (same personas).
    Returns 503 if Google credentials aren't configured - frontend degrades."""
    if voice not in VOICES:
        raise HTTPException(status_code=400, detail="Unknown voice")
    if voice in _preview_cache:
        return Response(content=_preview_cache[voice], media_type="audio/wav")
    try:
        def _synth() -> bytes:
            from google.cloud import texttospeech
            client = texttospeech.TextToSpeechClient()
            resp = client.synthesize_speech(
                input=texttospeech.SynthesisInput(
                    text="Hi! This is how I sound on a call."),
                voice=texttospeech.VoiceSelectionParams(
                    language_code="en-IN", name=f"en-IN-Chirp3-HD-{voice}"),
                audio_config=texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.LINEAR16),
            )
            raw = resp.audio_content
            if raw[:4] == b"RIFF":
                return raw
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
                w.writeframes(raw)
            return buf.getvalue()
        data = await asyncio.to_thread(_synth)
        _preview_cache[voice] = data
        return Response(content=data, media_type="audio/wav")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Preview unavailable: {exc}") from exc


@app.post("/api/twilio/status")
async def twilio_status(request: Request):
    """Twilio status callbacks: surfaces busy/no-answer/failed to the UI."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")
    print(f"[twilio-status] sid={call_sid[-8:]} status={status} "
          f"answeredBy={form.get('AnsweredBy','')} sipCode={form.get('SipResponseCode','')}")
    session = manager.get_by_call_sid(call_sid)
    if session:
        mapped = {"busy": "busy", "no-answer": "no_answer", "failed": "failed",
                  "canceled": "failed"}.get(status)
        if mapped and session.status in ("created", "ringing"):
            session.status = mapped
            await session.emit({"type": "status", "status": mapped})
            try:
                db.update_call(session.session_id, status=mapped)
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True}


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
    if session.summary:
        await ws.send_json({"type": "summary", "summary": session.summary})
    session.monitors.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        session.monitors.discard(ws)


@app.websocket("/ws/twilio/{session_id}")
async def twilio_ws(ws: WebSocket, session_id: str):
    await ws.accept()
    session = manager.get(session_id)
    if not session:
        await ws.close()
        return
    bridge = GeminiBridge(ws, session)
    try:
        await bridge.run()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"[twilio_ws] error: {exc}")
    finally:
        await bridge.cleanup()
