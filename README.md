# The Operator — AI Call Agent (English + Malayalam)

An AI agent that places real outbound phone calls and holds a natural,
low-latency voice conversation in English or Malayalam to accomplish a task
you give it: check on family, book a table, confirm an appointment, take a
message back for you. Built around Gemini Live speech-to-speech, Twilio
telephony, and a FastAPI + React app styled as a telegram-era calling bureau.

## Architecture

    React "Wire" console --POST /api/calls--> FastAPI --Twilio REST--> outbound call
                                                 |
                          Twilio <Connect><Stream> (mu-law 8 kHz, bidirectional)
                                                 |
              +---------- WS /ws/twilio/{session} -------------+
              |  caller audio  mu-law 8k -> PCM16 16k -> Gemini |
              |  Gemini Live (native speech-to-speech)          |
              |  model audio   PCM16 24k -> mu-law 8k -> caller |
              +-------------------------------------------------+
                                                 |
               live transcript + status -> WS /ws/monitor/{session} -> console

There is no STT to LLM to TTS cascade on the call path. Gemini Live hears the
caller directly and speaks directly, which is what makes the latency low and
the Malayalam natural. Native VAD handles interruptions; barge-in flushes
Twilio's buffer instantly.

Around the live call:

- Callee transcripts for the UI come from a per-utterance Gemini Flash
  transcription of the buffered caller audio, in the original script,
  including Malayalam-English code-switching. Display only, never on the
  call path.
- After each call, Gemini Flash produces a structured outcome summary
  (accomplished / partial / failed, headline, key facts) shown as a stamped
  receipt and stored with the call.
- Calls persist to SQLite (backend/calls.db) with transcript, duration,
  status, and summary.

## Conversation behaviour

The agent follows a conversation contract, not just a task string:

- Matches tone to context: casual with friends and family, professional with
  businesses, and mirrors the other person's energy.
- Works the objective piece by piece, asks natural follow-up questions, pins
  down vague answers, and confirms names, times, and numbers.
- Closing protocol: recap what was agreed, ask if there is anything else or
  any message to relay back to the user, then say goodbye and hang up via an
  end_call function with strict preconditions.
- Handles interruptions without restarting sentences, asks to repeat when
  audio is unclear, deals with wrong-person answers, leaves a single concise
  voicemail when it hits one, and ends politely after repeated silence.

Safety nets: silence watchdog (two check-ins, then a graceful hangup), a hard
call duration cap, dropped-session handling with the partial transcript
preserved and summarized, and Twilio status callbacks so busy, no-answer, and
failed calls surface in the UI instead of hanging on "dialing".

## Frontend ("Wire")

A telegram-styled console: paper sheet, typewriter type, Malayalam set in
Noto Sans Malayalam. Agent lines print character by character with a blinking
caret while the line is open. The outcome arrives as a stamped receipt.
Includes call history ("Filed transmissions") with export and call-again,
a contacts directory, task templates, a one-click task polisher, per-call
voice selection with audition previews, a live response-latency readout, an
in-call hang-up control, and fluid sizing from phone width to ultrawide.

## Requirements

1. Gemini API key (aistudio.google.com). The Live native-audio models need a
   billed project.
2. Twilio account with a voice-capable number. Trial accounts can only call
   verified numbers; calls to India need geo permissions enabled.
3. Google Cloud service account with Text-to-Speech enabled (only for the
   voice audition previews; everything else runs on the Gemini key).
4. Python 3.11+ and Node 18+.
5. A public HTTPS URL for the backend. GitHub Codespaces port forwarding
   works; ngrok works locally.

## Setup

Backend:

    cd backend
    python -m venv .venv && source .venv/bin/activate   # optional in Codespaces
    pip install -r requirements.txt
    cp .env.example .env    # fill in keys, see below
    uvicorn app.main:app --host 0.0.0.0 --port 8000

Environment keys in backend/.env:

    GEMINI_API_KEY=...
    GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
    TWILIO_ACCOUNT_SID=AC...
    TWILIO_AUTH_TOKEN=...
    TWILIO_FROM_NUMBER=+1...
    GOOGLE_APPLICATION_CREDENTIALS=/abs/path/service-account.json
    PUBLIC_BASE_URL=https://your-public-url    # no trailing slash

Frontend:

    cd frontend
    npm install
    echo "VITE_API_BASE=https://your-public-url" > .env
    npm run dev

### Codespaces notes

Port 8000 must be set to Public in the Ports tab or Twilio cannot reach the
media stream and status callbacks. The forwarded URL follows the pattern
https://CODESPACE_NAME-8000.app.github.dev and changes if you create a new
Codespace, so re-check PUBLIC_BASE_URL and the frontend .env after a rebuild.
backend/diag.py asks Twilio what happened to recent calls when debugging.

## API

- POST /api/calls — place a call: to_number, task, language (en|ml),
  caller_name, voice
- POST /api/calls/{id}/hangup — end a live call
- GET /api/calls/{id} — live status, transcript, summary
- GET /api/history and GET /api/history/{id} — stored calls
- POST /api/polish — rewrite a task draft into a proper agent brief
- GET /api/voices and GET /api/voice-preview?voice=Name
- POST /api/twilio/status — Twilio status callback receiver
- WS /ws/twilio/{id} — Twilio media stream bridge
- WS /ws/monitor/{id} — live transcript, status, latency, summary events

## Responsible use

This dials real people. You are responsible for complying with calling and
recording-consent laws in your and the callee's jurisdiction (for example
TCPA in the US and TRAI/DND rules in India). The agent always discloses it is
an AI when asked and never claims to be human; keep it that way. Do not use
it for spam, deception, or impersonation.
