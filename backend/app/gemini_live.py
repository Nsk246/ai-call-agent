"""Twilio <-> Gemini Live audio bridge with failure handling.

Resilience: silence watchdog (2 nudges then hang up), max-duration cap,
Gemini session drop -> 'dropped' status with partial transcript preserved,
all persistence/summary work isolated from the live audio path.
SDK NOTE: session.receive() ends at every turn boundary - outer loop required.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import io
import json
import time
import wave

from fastapi import WebSocket
from google import genai
from google.genai import types

from . import db
from .ai_helpers import summarize_call
from .config import get_settings
from .session import CallSession, manager

_LANG_NAME = {"en": "English", "ml": "Malayalam"}
_MIN_UTT_BYTES = int(16000 * 2 * 0.4)
_MAX_UTT_BYTES = 16000 * 2 * 60
_SPEECH_RMS = 300          # inbound energy above this counts as caller speech
_SILENCE_NUDGE_SEC = 18    # nudge the model if line is silent this long
_MAX_NUDGES = 2
_MAX_CALL_SEC = 600        # hard cap

_TRANSCRIBE_PROMPT = (
    "Transcribe EXACTLY what is spoken in this phone-call audio. Use the original "
    "language and script (Malayalam in Malayalam script, English in Latin script; keep "
    "code-switching as spoken). Output ONLY the transcription text. If there is no "
    "clear speech, output nothing."
)


def _system_prompt(task: str, language: str, caller_name: str) -> str:
    lang = _LANG_NAME.get(language, "English")
    lang_rule = (
        "Speak natural, everyday spoken Malayalam - the way people talk on the phone in "
        "Kerala - mixing in common English words where a real speaker would. If the "
        "other person switches to English, follow them."
        if language == "ml"
        else "Speak natural, relaxed conversational English."
    )
    return (
        f"You are a warm, friendly assistant on a LIVE PHONE CALL, calling on behalf of "
        f"{caller_name}. Your single objective: {task}\n\n"
        f"Primary language: {lang}. {lang_rule}\n"
        "Conversation rules:\n"
        "- You placed this call: greet them and briefly say who you're calling for and why.\n"
        "- Short turns: one or two sentences, one question at a time.\n"
        "- If they interrupt you, STOP and respond to what they said - never restart "
        "your sentence. If the thing you didn't finish saying is still important, bring "
        "it up naturally at the next opportunity; if it's no longer relevant, drop it.\n"
        "- If you couldn't hear or understand them, politely ask them to repeat - once. "
        "Never guess critical details. Always confirm names, times, dates and numbers "
        "by repeating them back once.\n"
        "- If the wrong person answers, politely ask for the right person, or briefly "
        "explain the purpose and ask if they can help.\n"
        "- If you reach VOICEMAIL (a recorded greeting followed by a beep), leave one "
        "concise message: who you're calling for, the purpose, and that they can expect "
        "another call. Then call end_call.\n"
        "- If the line stays silent after you check in twice, say a polite goodbye and "
        "call end_call.\n"
        "- Never claim to be human. If asked, say you're an AI assistant calling for "
        f"{caller_name}.\n"
        "- When the objective is achieved, impossible, or they want to hang up: one "
        "short polite closing line, and AFTER speaking it, call end_call."
    )


_END_CALL_TOOL = {"function_declarations": [{
    "name": "end_call",
    "description": ("Hang up the phone call. Call this only AFTER you have spoken "
                    "your closing line, when the task is complete or the conversation "
                    "is over."),
}]}


def _pcm16k_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
    return buf.getvalue()


class GeminiBridge:
    def __init__(self, ws: WebSocket, session: CallSession) -> None:
        self.ws = ws
        self.session = session
        self.settings = get_settings()
        self.client = genai.Client(api_key=self.settings.gemini_api_key)
        self.stream_sid: str | None = None
        self.ending = False
        self.stream_active = False
        self._up_state = None
        self._down_state = None
        self._out_buf = ""
        self._utt_buf = bytearray()
        self._model_turn_open = False
        self._transcribe_tasks: set[asyncio.Task] = set()
        # watchdog + latency state
        self._last_activity = time.monotonic()
        self._last_speech_ts: float | None = None
        self._nudges = 0
        self._gemini = None

    # ---- audio conversion ----
    def _twilio_to_gemini(self, mulaw: bytes) -> bytes:
        pcm8 = audioop.ulaw2lin(mulaw, 2)
        pcm16, self._up_state = audioop.ratecv(pcm8, 2, 1, 8000, 16000, self._up_state)
        return pcm16

    def _gemini_to_twilio(self, pcm24: bytes) -> bytes:
        pcm8, self._down_state = audioop.ratecv(pcm24, 2, 1, 24000, 8000, self._down_state)
        return audioop.lin2ulaw(pcm8, 2)

    async def _send_audio(self, mulaw: bytes) -> None:
        if not self.stream_active:
            return
        await self.ws.send_json(
            {"event": "media", "streamSid": self.stream_sid,
             "media": {"payload": base64.b64encode(mulaw).decode("ascii")}})

    async def _clear_twilio(self) -> None:
        if not self.stream_active:
            return
        try:
            await self.ws.send_json({"event": "clear", "streamSid": self.stream_sid})
        except Exception:  # noqa: BLE001
            pass

    # ---- transcripts ----
    async def _flush_out(self) -> None:
        text = self._out_buf.strip()
        self._out_buf = ""
        if text:
            await self.session.emit({"type": "transcript", "role": "agent", "text": text})

    def _snapshot_utterance(self) -> None:
        pcm = bytes(self._utt_buf)
        self._utt_buf.clear()
        if len(pcm) < _MIN_UTT_BYTES:
            return
        task = asyncio.create_task(self._transcribe_and_emit(pcm))
        self._transcribe_tasks.add(task)
        task.add_done_callback(self._transcribe_tasks.discard)

    async def _transcribe_and_emit(self, pcm: bytes) -> None:
        try:
            resp = await self.client.aio.models.generate_content(
                model=self.settings.transcript_model,
                contents=[types.Part.from_bytes(data=_pcm16k_to_wav(pcm),
                                                mime_type="audio/wav"),
                          _TRANSCRIBE_PROMPT],
                config=types.GenerateContentConfig(temperature=0.0),
            )
            text = (resp.text or "").strip()
            if text:
                await self.session.emit(
                    {"type": "transcript", "role": "callee", "text": text})
        except Exception as exc:  # noqa: BLE001
            print(f"[callee-transcript] failed (non-fatal): {exc}")

    # ---- watchdog ----
    async def _watchdog(self, gemini) -> None:
        while not self.ending:
            await asyncio.sleep(2)
            now = time.monotonic()
            if self.session.started_at and self.session.duration_sec > _MAX_CALL_SEC:
                print("[watchdog] max duration reached")
                await self.hang_up("ended")
                return
            if now - self._last_activity > _SILENCE_NUDGE_SEC:
                self._last_activity = now
                if self._nudges < _MAX_NUDGES:
                    self._nudges += 1
                    try:
                        await gemini.send_realtime_input(text=(
                            "(The line has been silent for a while. Politely check if "
                            "they are still there.)"))
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    print("[watchdog] silent after nudges; ending")
                    await self.hang_up("ended")
                    return

    # ---- main ----
    async def run(self) -> None:
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": _system_prompt(
                self.session.task, self.session.language, self.session.caller_name),
            "speech_config": {"voice_config": {"prebuilt_voice_config": {
                "voice_name": self.session.voice or self.settings.gemini_voice}}},
            "output_audio_transcription": {},
            "tools": [_END_CALL_TOOL],
        }
        try:
            async with self.client.aio.live.connect(
                model=self.settings.gemini_live_model, config=config
            ) as gemini:
                self._gemini = gemini
                pump = asyncio.create_task(self._gemini_to_phone(gemini))
                dog = asyncio.create_task(self._watchdog(gemini))
                try:
                    await self._phone_to_gemini(gemini)
                finally:
                    for t in (pump, dog):
                        t.cancel()
                    await asyncio.gather(pump, dog, return_exceptions=True)
        except Exception as exc:  # noqa: BLE001 - session died mid-call
            print(f"[bridge] gemini session error: {exc}")
            await self.hang_up("dropped")

    async def _phone_to_gemini(self, gemini) -> None:
        async for message in self.ws.iter_text():
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                self.stream_sid = data["start"]["streamSid"]
                self.stream_active = True
                self.session.status = "live"
                self.session.started_at = time.time()
                self._last_activity = time.monotonic()
                await self.session.emit({"type": "status", "status": "live"})
                try:
                    db.update_call(self.session.session_id, status="live")
                except Exception:  # noqa: BLE001
                    pass
                await gemini.send_realtime_input(text=(
                    "(The call just connected and someone answered. Greet them now.)"))

            elif event == "media":
                mulaw = base64.b64decode(data["media"]["payload"])
                pcm16 = self._twilio_to_gemini(mulaw)
                if audioop.rms(pcm16, 2) > _SPEECH_RMS:
                    self._last_activity = time.monotonic()
                    self._last_speech_ts = time.monotonic()
                    self._nudges = 0
                if len(self._utt_buf) < _MAX_UTT_BYTES:
                    self._utt_buf.extend(pcm16)
                try:
                    await gemini.send_realtime_input(
                        audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))
                except Exception as exc:  # noqa: BLE001
                    print(f"[bridge] send failed: {exc}")
                    await self.hang_up("dropped")
                    return

            elif event == "stop":
                self.stream_active = False
                break
        # Twilio side ended (callee hung up, or our hangup endpoint)
        await self.hang_up("ended")

    async def _gemini_to_phone(self, gemini) -> None:
        while not self.ending:
            turn_seen = False
            async for response in gemini.receive():
                turn_seen = True
                sc = response.server_content
                has_output = bool(response.data) or bool(
                    sc and sc.output_transcription and sc.output_transcription.text)

                if has_output:
                    self._last_activity = time.monotonic()
                    if not self._model_turn_open:
                        self._model_turn_open = True
                        self._snapshot_utterance()
                        if self._last_speech_ts is not None:
                            lat = time.monotonic() - self._last_speech_ts
                            self._last_speech_ts = None
                            if 0 < lat < 10:
                                await self.session.emit(
                                    {"type": "latency", "seconds": round(lat, 2)})

                if sc is not None:
                    if sc.interrupted:
                        await self._clear_twilio()
                        self._down_state = None
                        await self._flush_out()
                        self._model_turn_open = False
                    if sc.output_transcription and sc.output_transcription.text:
                        self._out_buf += sc.output_transcription.text
                    if sc.turn_complete:
                        await self._flush_out()
                        self._model_turn_open = False

                if response.data:
                    await self._send_audio(self._gemini_to_twilio(response.data))

                if response.tool_call:
                    for fc in response.tool_call.function_calls:
                        if fc.name == "end_call":
                            try:
                                await gemini.send_tool_response(
                                    function_responses=[types.FunctionResponse(
                                        id=fc.id, name=fc.name,
                                        response={"status": "ok"})])
                            except Exception:  # noqa: BLE001
                                pass
                            await asyncio.sleep(2.5)
                            await self.hang_up("ended")
                            return
            if not turn_seen:
                await asyncio.sleep(0.05)

    # ---- teardown ----
    async def hang_up(self, final_status: str = "ended") -> None:
        if self.ending:
            return
        self.ending = True
        self.stream_active = False
        self._snapshot_utterance()
        await self._flush_out()
        if self._transcribe_tasks:
            await asyncio.gather(*self._transcribe_tasks, return_exceptions=True)
        self.session.status = final_status
        await self.session.emit({"type": "status", "status": final_status})
        await manager.end(self.session.session_id)
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        # persistence + summary: isolated, best-effort, off the call path
        asyncio.create_task(self._finalize(final_status))

    async def _finalize(self, final_status: str) -> None:
        try:
            db.update_call(self.session.session_id, status=final_status,
                           duration_sec=self.session.duration_sec,
                           transcript=self.session.transcript)
        except Exception as exc:  # noqa: BLE001
            print(f"[db] persist failed: {exc}")
        try:
            summary = await summarize_call(
                self.session.task, self.session.transcript, final_status)
            self.session.summary = summary
            await self.session.emit({"type": "summary", "summary": summary})
            db.update_call(self.session.session_id, summary=summary)
        except Exception as exc:  # noqa: BLE001
            print(f"[summary] finalize failed: {exc}")

    async def cleanup(self) -> None:
        await self.hang_up("ended")
