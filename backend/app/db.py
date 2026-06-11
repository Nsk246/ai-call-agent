"""SQLite persistence for call history. Every write is best-effort: a storage
failure must never affect a live call, so callers wrap these in try/except."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

_DB_PATH = "calls.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _c() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY,
                created_at REAL,
                to_number TEXT, caller_name TEXT, language TEXT, voice TEXT,
                task TEXT, status TEXT, duration_sec INTEGER DEFAULT 0,
                transcript TEXT DEFAULT '[]', summary TEXT DEFAULT 'null'
            )"""
        )
        _conn.commit()
    return _conn


def insert_call(session) -> None:
    with _lock:
        _c().execute(
            "INSERT OR REPLACE INTO calls (id, created_at, to_number, caller_name,"
            " language, voice, task, status) VALUES (?,?,?,?,?,?,?,?)",
            (session.session_id, time.time(), session.to_number, session.caller_name,
             session.language, session.voice, session.task, session.status),
        )
        _c().commit()


def update_call(session_id: str, *, status: str | None = None,
                duration_sec: int | None = None,
                transcript: list | None = None, summary: dict | None = None) -> None:
    sets, vals = [], []
    if status is not None:
        sets.append("status=?"); vals.append(status)
    if duration_sec is not None:
        sets.append("duration_sec=?"); vals.append(duration_sec)
    if transcript is not None:
        sets.append("transcript=?"); vals.append(json.dumps(transcript, ensure_ascii=False))
    if summary is not None:
        sets.append("summary=?"); vals.append(json.dumps(summary, ensure_ascii=False))
    if not sets:
        return
    vals.append(session_id)
    with _lock:
        _c().execute(f"UPDATE calls SET {', '.join(sets)} WHERE id=?", vals)
        _c().commit()


def list_calls(limit: int = 50) -> list[dict]:
    with _lock:
        rows = _c().execute(
            "SELECT id, created_at, to_number, caller_name, language, voice, task,"
            " status, duration_sec, summary FROM calls ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    keys = ["id", "created_at", "to_number", "caller_name", "language", "voice",
            "task", "status", "duration_sec", "summary"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["summary"] = json.loads(d["summary"] or "null")
        out.append(d)
    return out


def get_call(call_id: str) -> dict | None:
    with _lock:
        r = _c().execute(
            "SELECT id, created_at, to_number, caller_name, language, voice, task,"
            " status, duration_sec, transcript, summary FROM calls WHERE id=?",
            (call_id,),
        ).fetchone()
    if not r:
        return None
    keys = ["id", "created_at", "to_number", "caller_name", "language", "voice",
            "task", "status", "duration_sec", "transcript", "summary"]
    d = dict(zip(keys, r))
    d["transcript"] = json.loads(d["transcript"] or "[]")
    d["summary"] = json.loads(d["summary"] or "null")
    return d
