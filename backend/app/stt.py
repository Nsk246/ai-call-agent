"""Streaming speech-to-text via Google Cloud Speech (v1).

The synchronous Google client runs in a dedicated worker thread (Google's own
battle-tested mic-streaming pattern); results are bridged to the asyncio loop
with run_coroutine_threadsafe. The worker transparently reconnects around
Google's ~5-minute streaming limit so long calls don't drop.

The SpeechClient is created lazily on first use (NOT at import time), so the
.env file is loaded and GOOGLE_APPLICATION_CREDENTIALS is exported first.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Awaitable, Callable

from google.cloud import speech

from .config import get_settings

_shared_client: speech.SpeechClient | None = None
_client_lock = threading.Lock()


def _client() -> speech.SpeechClient:
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                get_settings()  # loads .env, exports GOOGLE_APPLICATION_CREDENTIALS
                _shared_client = speech.SpeechClient()
    return _shared_client


class StreamingSTT:
    def __init__(
        self,
        *,
        language_code: str,
        alternative_language_codes: list[str] | None,
        loop: asyncio.AbstractEventLoop,
        on_interim: Callable[[str], Awaitable[None]],
        on_final: Callable[[str], Awaitable[None]],
        model: str = "telephony",
        use_enhanced: bool = True,
    ) -> None:
        self._client = _client()
        self._language_code = language_code
        self._alt_codes = alternative_language_codes or []
        self._model = model
        self._use_enhanced = use_enhanced
        self._loop = loop
        self._on_interim = on_interim
        self._on_final = on_final

        self._audio_q: "queue.Queue[bytes | None]" = queue.Queue()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- public API (called from the asyncio side) ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def feed(self, mulaw_chunk: bytes) -> None:
        if not self._closed.is_set():
            self._audio_q.put(mulaw_chunk)

    def stop(self) -> None:
        self._closed.set()
        self._audio_q.put(None)

    # ---- worker thread ----
    def _config(self) -> speech.StreamingRecognitionConfig:
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
            sample_rate_hertz=8000,
            language_code=self._language_code,
            alternative_language_codes=self._alt_codes,
            enable_automatic_punctuation=True,
            model=self._model,
            use_enhanced=self._use_enhanced,
        )
        return speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=True,
        )

    def _requests(self):
        while not self._closed.is_set():
            chunk = self._audio_q.get()
            if chunk is None:
                return
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                responses = self._client.streaming_recognize(
                    config=self._config(),
                    requests=self._requests(),
                )
                for response in responses:
                    for result in response.results:
                        if not result.alternatives:
                            continue
                        text = result.alternatives[0].transcript.strip()
                        if not text:
                            continue
                        cb = self._on_final if result.is_final else self._on_interim
                        asyncio.run_coroutine_threadsafe(cb(text), self._loop)
            except Exception as exc:  # noqa: BLE001 - reconnect on timeout/error
                if self._closed.is_set():
                    break
                print(f"[stt] stream ended ({exc}); reconnecting")
                time.sleep(0.2)
