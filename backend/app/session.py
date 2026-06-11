"""Per-call session state and a process-wide registry.

A CallSession ties together the call's task/language config, the live
transcript, and any frontend "monitor" websockets watching it. State is
in-memory (single-process). For multi-worker deployments back this with Redis.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket


@dataclass
class CallSession:
    task: str
    language: str  # "en" | "ml"
    to_number: str
    caller_name: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "created"  # created | ringing | live | ended
    transcript: list[dict] = field(default_factory=list)
    monitors: set[WebSocket] = field(default_factory=set)

    async def emit(self, event: dict) -> None:
        """Record an event and fan it out to all monitor websockets."""
        if event.get("type") in {"transcript", "status"}:
            self.transcript.append(event)
        dead = []
        for ws in self.monitors:
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.monitors.discard(ws)


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, **kwargs) -> CallSession:
        session = CallSession(**kwargs)
        async with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> CallSession | None:
        return self._sessions.get(session_id)

    async def end(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session:
            session.status = "ended"


manager = SessionManager()
