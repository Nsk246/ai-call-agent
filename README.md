# AI Call Agent (English + Malayalam)

An AI voice agent that places **real outbound phone calls** and holds a live,
bidirectional conversation in **English** or **Malayalam** to accomplish a task
you specify (book a table, confirm an appointment, ask a question, etc.).

```
React panel ──POST /api/calls──▶ FastAPI ──Twilio REST──▶ outbound call
                                    │
                  Twilio <Connect><Stream> (mu-law 8 kHz, bidirectional)
                                    │
        ┌──────────── WS /ws/twilio/{session} ───────────────┐
        │ callee audio ─▶ Google STT (en-IN / ml-IN) ─▶ text  │
        │ text ─▶ Claude (your task = goal) ─▶ short reply     │
        │ reply ─▶ Google TTS (en/ml, mu-law) ─▶ callee audio  │
        │ barge-in via Twilio `clear`; [END_CALL] hangs up     │
        └──────────────────────────────────────────────────────┘
                                    │
            live transcript ─▶ WS /ws/monitor/{session} ─▶ React panel
```

Why this is a server and not a browser artifact: placing real PSTN calls needs
a telephony provider (Twilio), secret credentials, and a webhook/WebSocket
endpoint Twilio can reach. None of that can live in a sandboxed browser app.

## Prerequisites

1. **Twilio** account + a voice-capable phone number (Account SID, Auth Token, From number).
2. **Google Cloud** project with *Cloud Speech-to-Text* and *Cloud Text-to-Speech*
   APIs enabled, and a service-account JSON key.
3. **Anthropic** API key.
4. **ngrok** (or any public HTTPS tunnel) for local development.
5. Python 3.11 and Node 18+.

> Malayalam note: Google STT transcribes `ml-IN`; TTS speaks it with voices like
> `ml-IN-Wavenet-A`. Code-switching ("Manglish") is on by default for the
> Malayalam leg via `alternative_language_codes`.

## Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your keys
```

In a second terminal, expose the backend so Twilio can reach it:

```bash
ngrok http 8000
# copy the https URL into PUBLIC_BASE_URL in backend/.env
```

Run it:

```bash
uvicorn app.main:app --reload --port 8000
```

## Frontend

```bash
cd frontend
npm install
cp .env.example .env        # set VITE_API_BASE to your backend (http://localhost:8000)
npm run dev                 # http://localhost:5173
```

## Use it

1. Open the panel, enter your name, the number to call, pick the language, and
   describe the task.
2. Click **Place call**. The number rings; the agent speaks first, then the live
   transcript streams into the panel.
3. The agent hangs up automatically when the task is done (it emits an internal
   `[END_CALL]` token that never gets spoken).

## Latency & naturalness

The pipeline is built so the caller hears the agent fast and the voice sounds human:

- **Streaming, pipelined turns.** Claude is streamed token-by-token; text is cut
  into sentences on the fly and each sentence is synthesized and sent the moment
  it's ready. The caller hears sentence one while sentence two is still being
  generated.
- **Speech-aware segmentation.** The splitter never cuts decimals ("8.30"),
  times, or abbreviations ("Dr.", "e.g."), so TTS never reads a half-number, and
  text is sanitized of markdown before synthesis.
- **TTS prewarm.** The Text-to-Speech channel is warmed while the phone is
  still ringing, so the opening line skips the first-RPC handshake. The STT
  client is shared across calls for the same reason.
- **Chirp 3: HD voices** (Google's most natural tier - human disfluencies,
  emotional range, built for real-time agents) for both English and Malayalam.
  Audio is requested as native LINEAR16 and resampled locally to 8 kHz mu-law.
- **Speech-first prompting.** The agent speaks numbers, times, and prices as
  words ("eight thirty in the evening"), keeps turns to a sentence or two,
  varies its openers, and on the Malayalam leg speaks everyday phone Malayalam
  with natural English code-switching. If interrupted, its memory records
  exactly where it was cut off so it doesn't restart the thought.
- **Haiku 4.5 by default** for the lowest model latency (set `AGENT_MODEL` to
  `claude-sonnet-4-6` for richer replies).
- **Real barge-in, race-free.** A turn is one cancellable task; interrupting
  stops the LLM, pending TTS, and playback together and flushes Twilio's buffer
  with `clear`. This also fires when a short utterance reaches a *final*
  transcript while the agent is mid-sentence. A grace window plus a minimum
  interim length keep echo and line noise from clipping the agent.

Pick or verify a voice name:

```bash
cd backend
python list_voices.py ml-IN     # or en-IN
```

Further latency wins if you need them: Google's `streaming_synthesize`
(bidirectional TTS) shaves more off long sentences, and Deepgram gives tighter
endpointing for English (Google remains the better choice for Malayalam).

## Tuning & scaling

- Voices: set `ENGLISH_TTS_VOICE` / `MALAYALAM_TTS_VOICE` to any name from
  `list_voices.py`.
- `ENABLE_CODE_SWITCHING=true` lets the Malayalam leg also recognise English
  ("Manglish"), which is how people actually talk.
- Session state is in-memory and single-process. For multiple Uvicorn workers,
  back `SessionManager` with Redis.

## Responsible use

This dials real people. You are responsible for complying with calling/robocall
and recording-consent laws in your and the callee's jurisdiction (e.g. TCPA in
the US, and India's TRAI/DND rules). Always have the agent disclose it's an AI
(the system prompt does this), get consent before recording, honor do-not-call
requests, and don't use it for spam, deception, or impersonation.
