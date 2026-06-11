"""FastAPI app: REST to start calls, Twilio <-> Gemini Live bridge, monitor WS."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import get_settings
from .gemini_live import GeminiBridge
from .session import manager
from .twilio_client import place_call

settings = get_settings()
app = FastAPI(title="AI Call Agent (Gemini Live)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartCallRequest(BaseModel):
    to_number: str = Field(..., examples=["+919876543210"])
    task: str = Field(..., description="What the agent should accomplish on the call")
    language: str = Field("en", pattern="^(en|ml)$")
    caller_name: str = Field("the caller")


@app.post("/api/calls")
async def start_call(req: StartCallRequest):
    session = await manager.create(
        task=req.task, language=req.language,
        to_number=req.to_number, caller_name=req.caller_name,
    )
    try:
        call_sid = await asyncio.to_thread(
            place_call, to_number=req.to_number, session_id=session.session_id
        )
    except Exception as exc:  # noqa: BLE001
        await manager.end(session.session_id)
        raise HTTPException(status_code=502, detail=f"Twilio call failed: {exc}") from exc
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
