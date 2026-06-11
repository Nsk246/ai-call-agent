"""Twilio <-> Gemini Live audio bridge.

Call path (latency-critical, untouched by anything else):
  Twilio mu-law 8k -> PCM16 16k -> Gemini Live; Gemini PCM16 24k -> mu-law 8k -> Twilio.

Callee transcripts (display only): caller audio is buffered per utterance;
when the model starts its reply (= caller finished talking) the snippet is
sent to a regular Gemini model for exact transcription in the original
language/script. Accurate Malayalam, ~1s behind speech, zero impact on the
live call.

SDK NOTE: session.receive() ends at every turn boundary - it must be wrapped
in an outer loop or the bridge goes deaf after the greeting.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import io
import json
import wave

from fastapi import WebSocket
from google import genai
from google.genai import types

from .config import get_settings
from .session import CallSession, manager

_LANG_NAME = {"en": "English", "ml": "Malayalam"}

# Don't bother transcribing buffers shorter than this (16kHz * 2B * seconds).
_MIN_UTT_BYTES = int(16000 * 2 * 0.4)
# Cap utterance buffer at 60s so a long silence can't grow it unbounded.
_MAX_UTT_BYTES = 16000 * 2 * 60

_TRANSCRIBE_PROMPT = (
    "Transcribe EXACTLY what is spoken in this phone-call audio. Use the "
    "original language and script (Malayalam in Malayalam script, English in "
    "Latin script; keep code-switching as spoken). Output ONLY the "
    "transcription text. If there is no clear speech, output nothing."
)


def _system_prompt(task: str, language: str, caller_name: str) -> str:
    lang = _LANG_NAME.get(language, "English")
    lang_rule = (
        "Speak natural, everyday spoken Malayalam - the way people talk on the phone in "
        "Kerala - mixing in common English words where a real speaker would. If the other "
        "person switches to English, follow them."
        if language == "ml"
        else "Speak natural, relaxed conversational English."
    )
    return (
        f"You are a warm, friendly assistant on a LIVE PHONE CALL, calling on behalf of "
        f"{caller_name}. Your single objective: {task}\n\n"
        f"Primary language for this call: {lang}. {lang_rule}\n"
        "Rules:\n"
        "- You placed this call, so when it connects, greet them and briefly say who "
        "you're calling for and why.\n"
        "- Short turns: one or two sentences, one question at a time. Listen more than "
        "you talk.\n"
        "- Be polite and human-sounding, but never claim to be human. If asked, say "
        f"you're an AI assistant calling for {caller_name}.\n"
        "- When the objective is achieved, impossible, or they want to hang up: say one "
        "short polite closing line, and AFTER finishing speaking it, call the end_call "
        "function."
    )


_END_CALL_TOOL = {
    "function_declarations": [
        {
            "name": "end_call",
            "description": (
                "Hang up the phone call. Call this only AFTER you have spoken your "
                "closing line, when the task is complete or the conversation is over."
            ),
        }
    ]
}


def _pcm16k_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


class GeminiBridge:
    def __init__(self, ws: WebSocket, session: CallSession) -> None:
        self.ws = ws
        self.session = session
        self.settings = get_settings()
        self.client = genai.Client(api_key=self.settings.gemini_api_key)
        self.stream_sid: str | None = None
        self.ending = False
        # stateful resamplers (must persist across chunks)
        self._up_state = None    # 8k -> 16k (caller -> Gemini)
        self._down_state = None  # 24k -> 8k (Gemini -> caller)
        # agent transcript accumulation (from Gemini output transcription)
        self._out_buf = ""
        # caller-utterance audio buffer for display transcription
        self._utt_buf = bytearray()
        self._model_turn_open = False
        self._transcribe_tasks: set[asyncio.Task] = set()

    # ---- audio conversion ----
    def _twilio_to_gemini(self, mulaw: bytes) -> bytes:
        pcm8 = audioop.ulaw2lin(mulaw, 2)
        pcm16, self._up_state = audioop.ratecv(pcm8, 2, 1, 8000, 16000, self._up_state)
        return pcm16

    def _gemini_to_twilio(self, pcm24: bytes) -> bytes:
        pcm8, self._down_state = audioop.ratecv(pcm24, 2, 1, 24000, 8000, self._down_state)
        return audioop.lin2ulaw(pcm8, 2)

    # ---- Twilio senders ----
    async def _send_audio(self, mulaw: bytes) -> None:
        await self.ws.send_json(
            {"event": "media", "streamSid": self.stream_sid,
             "media": {"payload": base64.b64encode(mulaw).decode("ascii")}}
        )

    async def _clear_twilio(self) -> None:
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
        """Caller finished talking (model is replying): transcribe the snippet."""
        if not self.settings.callee_transcripts_enabled:
            self._utt_buf.clear()
            return
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
                contents=[
                    types.Part.from_bytes(data=_pcm16k_to_wav(pcm), mime_type="audio/wav"),
                    _TRANSCRIBE_PROMPT,
                ],
                config=types.GenerateContentConfig(temperature=0.0),
            )
            text = (resp.text or "").strip()
            if text:
                await self.session.emit(
                    {"type": "transcript", "role": "callee", "text": text}
                )
        except Exception as exc:  # noqa: BLE001 - transcripts are best-effort
            print(f"[callee-transcript] failed (non-fatal): {exc}")

    # ---- main ----
    async def run(self) -> None:
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": _system_prompt(
                self.session.task, self.session.language, self.session.caller_name
            ),
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": self.settings.gemini_voice}
                }
            },
            "output_audio_transcription": {},
            "tools": [_END_CALL_TOOL],
        }
        async with self.client.aio.live.connect(
            model=self.settings.gemini_live_model, config=config
        ) as gemini:
            pump = asyncio.create_task(self._gemini_to_phone(gemini))
            try:
                await self._phone_to_gemini(gemini)
            finally:
                pump.cancel()
                try:
                    await pump
                except asyncio.CancelledError:
                    pass

    async def _phone_to_gemini(self, gemini) -> None:
        """Twilio WS receive loop: caller audio -> Gemini (+ utterance buffer)."""
        async for message in self.ws.iter_text():
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                self.stream_sid = data["start"]["streamSid"]
                self.session.status = "live"
                await self.session.emit({"type": "status", "status": "live"})
                await gemini.send_realtime_input(
                    text="(The call just connected and someone answered. Greet them now.)"
                )

            elif event == "media":
                pcm16 = self._twilio_to_gemini(base64.b64decode(data["media"]["payload"]))
                if len(self._utt_buf) < _MAX_UTT_BYTES:
                    self._utt_buf.extend(pcm16)
                await gemini.send_realtime_input(
                    audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000")
                )

            elif event == "stop":
                break

    async def _gemini_to_phone(self, gemini) -> None:
        """Gemini receive loop, restarted at every turn boundary (SDK NOTE above)."""
        while not self.ending:
            turn_seen = False
            async for response in gemini.receive():
                turn_seen = True
                sc = response.server_content
                has_output = bool(response.data) or bool(
                    sc and sc.output_transcription and sc.output_transcription.text
                )

                # Model started a new reply => the caller's utterance just ended.
                if has_output and not self._model_turn_open:
                    self._model_turn_open = True
                    self._snapshot_utterance()

                if sc is not None:
                    if sc.interrupted:                   # caller barged in
                        await self._clear_twilio()
                        self._down_state = None
                        await self._flush_out()
                        self._model_turn_open = False
                    if sc.output_transcription and sc.output_transcription.text:
                        self._out_buf += sc.output_transcription.text
                    if sc.turn_complete:
                        await self._flush_out()
                        self._model_turn_open = False

                if response.data:                        # model audio (24k PCM)
                    await self._send_audio(self._gemini_to_twilio(response.data))

                if response.tool_call:
                    for fc in response.tool_call.function_calls:
                        if fc.name == "end_call":
                            await gemini.send_tool_response(
                                function_responses=[types.FunctionResponse(
                                    id=fc.id, name=fc.name, response={"status": "ok"}
                                )]
                            )
                            await asyncio.sleep(2.5)     # let the goodbye play out
                            await self.hang_up()
                            return

            if not turn_seen:
                await asyncio.sleep(0.05)                # session gone; no hot loop

    async def hang_up(self) -> None:
        if self.ending:
            return
        self.ending = True
        self._snapshot_utterance()                       # last words, if any
        await self._flush_out()
        if self._transcribe_tasks:                       # let pending transcripts land
            await asyncio.gather(*self._transcribe_tasks, return_exceptions=True)
        await self.session.emit({"type": "status", "status": "ended"})
        await manager.end(self.session.session_id)
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    async def cleanup(self) -> None:
        await self.hang_up()
