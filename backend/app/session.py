"""Per-call session state and a process-wide registry."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket


@dataclass
class CallSession:
    task: str
    language: str
    to_number: str
    caller_name: str
    voice: str = "Aoede"
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    call_sid: str | None = None
    status: str = "created"  # created|ringing|live|ended|dropped|no_answer|busy|failed
    started_at: float | None = None
    transcript: list[dict] = field(default_factory=list)
    summary: dict | None = None
    monitors: set[WebSocket] = field(default_factory=set)

    @property
    def duration_sec(self) -> int:
        return int(time.time() - self.started_at) if self.started_at else 0

    async def emit(self, event: dict) -> None:
        if event.get("type") == "transcript":
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

    def get_by_call_sid(self, call_sid: str) -> CallSession | None:
        for s in self._sessions.values():
            if s.call_sid == call_sid:
                return s
        return None

    async def end(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session and session.status not in (
            "ended", "dropped", "no_answer", "busy", "failed"
        ):
            session.status = "ended"


manager = SessionManager()
