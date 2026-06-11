"""Text-to-speech via Google Cloud Chirp 3: HD voices.

We request native LINEAR16 (a WAV the stdlib `wave` module reads), then
resample to 8 kHz mu-law locally - exactly what Twilio needs, on the most
natural voice tier, with no dependence on server-side resampling quirks.

The gRPC client and channel are shared module-wide; `prewarm()` performs a
throwaway synthesis while the phone is still ringing so the opening line
doesn't pay the TLS/channel handshake.
"""
from __future__ import annotations

import asyncio
import io
import wave

# stdlib on <=3.12; the `audioop-lts` package provides the same module
# name on Python 3.13+ (pinned in requirements.txt).
import audioop

from google.cloud import texttospeech

_client = texttospeech.TextToSpeechClient()


def _to_mulaw_8k(wav_bytes: bytes) -> bytes:
    """Convert a LINEAR16 WAV (any rate) to headerless 8 kHz mono mu-law."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
    except wave.Error:
        # Fallback: headerless 24 kHz 16-bit mono (Chirp 3 HD native).
        channels, width, rate, pcm = 1, 2, 24000, wav_bytes

    if channels == 2:
        pcm = audioop.tomono(pcm, width, 0.5, 0.5)
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if rate != 8000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 8000, None)
    return audioop.lin2ulaw(pcm, 2)


def _synthesize_blocking(text: str, language_code: str, voice_name: str) -> bytes:
    resp = _client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code=language_code, name=voice_name
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16
        ),
    )
    return _to_mulaw_8k(resp.audio_content)


async def synthesize(text: str, language_code: str, voice_name: str) -> bytes:
    """Return headerless mu-law 8 kHz bytes for the given text."""
    return await asyncio.to_thread(_synthesize_blocking, text, language_code, voice_name)


async def prewarm(language_code: str, voice_name: str) -> None:
    """Fire-and-forget channel warmup; failures are logged, never raised."""
    try:
        await synthesize("ok", language_code, voice_name)
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] prewarm failed (non-fatal): {exc}")
