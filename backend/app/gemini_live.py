"""Twilio <-> Gemini Live audio bridge.

Call path (latency-critical): Twilio mu-law 8k -> PCM16 16k -> Gemini;
Gemini PCM16 24k -> mu-law 8k -> Twilio. Pure speech-to-speech.

Transcripts for the frontend:
  - Agent side: Gemini's output_audio_transcription (follows the model's own
    speech, accurate in any language).
  - Callee side: a PARALLEL Google Cloud STT stream (ml-IN / en-IN). Gemini's
    input transcription has no language setting and mis-detects Malayalam as
    English, so we don't display it. The STT side-channel never touches the
    conversation path, so it adds zero latency to the call.

SDK NOTE: session.receive() ends at every turn boundary - it must be wrapped
in an outer loop or the bridge goes deaf after the greeting.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import json

from fastapi import WebSocket
from google import genai
from google.genai import types

from .config import get_settings
from .session import CallSession, manager
from .stt import StreamingSTT

_LANG_NAME = {"en": "English", "ml": "Malayalam"}


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
        # side-channel STT for accurate callee transcripts
        self.stt: StreamingSTT | None = None

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

    # ---- transcript helpers ----
    async def _on_callee_final(self, text: str) -> None:
        await self.session.emit({"type": "transcript", "role": "callee", "text": text})

    async def _noop(self, text: str) -> None:
        return

    async def _flush_out(self) -> None:
        text = self._out_buf.strip()
        self._out_buf = ""
        if text:
            await self.session.emit({"type": "transcript", "role": "agent", "text": text})

    def _start_transcript_stt(self) -> None:
        if not self.settings.transcript_stt_enabled:
            return
        try:
            if self.session.language == "ml":
                code, alt, model, enhanced = (
                    self.settings.malayalam_stt_code,
                    [self.settings.english_stt_code], "default", False,
                )
            else:
                code, alt, model, enhanced = (
                    self.settings.english_stt_code, [], "telephony", True,
                )
            self.stt = StreamingSTT(
                language_code=code,
                alternative_language_codes=alt,
                loop=asyncio.get_running_loop(),
                on_interim=self._noop,
                on_final=self._on_callee_final,
                model=model,
                use_enhanced=enhanced,
            )
            self.stt.start()
        except Exception as exc:  # noqa: BLE001 - transcripts are optional
            print(f"[transcript-stt] disabled ({exc})")
            self.stt = None

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
        """Twilio WS receive loop: caller audio -> Gemini (+ STT side-channel)."""
        async for message in self.ws.iter_text():
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                self.stream_sid = data["start"]["streamSid"]
                self.session.status = "live"
                await self.session.emit({"type": "status", "status": "live"})
                self._start_transcript_stt()
                # We placed the call -> nudge the model to greet first.
                await gemini.send_realtime_input(
                    text="(The call just connected and someone answered. Greet them now.)"
                )

            elif event == "media":
                mulaw = base64.b64decode(data["media"]["payload"])
                if self.stt:
                    self.stt.feed(mulaw)          # parallel, display-only
                await gemini.send_realtime_input(
                    audio=types.Blob(
                        data=self._twilio_to_gemini(mulaw),
                        mime_type="audio/pcm;rate=16000",
                    )
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

                if sc is not None:
                    if sc.interrupted:                   # caller barged in
                        await self._clear_twilio()
                        self._down_state = None
                        await self._flush_out()
                    if sc.output_transcription and sc.output_transcription.text:
                        self._out_buf += sc.output_transcription.text
                    if sc.turn_complete:
                        await self._flush_out()

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
        if self.stt:
            self.stt.stop()
        await self._flush_out()
        await self.session.emit({"type": "status", "status": "ended"})
        await manager.end(self.session.session_id)
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    async def cleanup(self) -> None:
        await self.hang_up()
