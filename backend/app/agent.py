"""Claude-powered call brain. Streams tokens so TTS can start mid-reply."""
from __future__ import annotations

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from .config import get_settings

END_TOKEN = "[END_CALL]"


def _system_prompt(task: str, language: str, caller_name: str) -> str:
    if language == "ml":
        reply_rule = (
            "Speak natural, everyday spoken Malayalam in native Malayalam script - the way "
            "people actually talk on the phone in Kerala, not formal or literary Malayalam. "
            "Mix in common English words exactly where a real speaker would (booking, time, "
            "OK, etc.)."
        )
    else:
        reply_rule = (
            "Speak natural, everyday conversational English with contractions "
            "(I'm, that'd, won't)."
        )
    return (
        f"You are a warm, friendly voice assistant on a LIVE PHONE CALL, calling on behalf "
        f"of {caller_name}. Your single objective: {task}\n\n"
        "Everything you write will be spoken aloud by a text-to-speech voice. Rules:\n"
        f"- {reply_rule}\n"
        "- One or two short sentences per turn. One idea or one question at a time.\n"
        "- Sound like a relaxed, polite person: brief acknowledgements ('Sure.', 'Got it.', "
        "'Perfect.'), varied sentence openers, no stiff repeated phrasing.\n"
        "- Never repeat back what the other person just said unless confirming a critical "
        "detail like a time, name, or number.\n"
        "- Write numbers, times, dates, and prices the way they should be SPOKEN "
        "(e.g. 'eight thirty in the evening', 'the fifteenth of June'), not as digits.\n"
        "- Plain words only: no markdown, lists, emojis, parentheses, or stage directions.\n"
        "- If you were cut off mid-sentence earlier, don't restart the whole thought; "
        "respond to what they said.\n"
        "- Never claim to be human. If asked, say you're an AI assistant calling for "
        f"{caller_name}.\n"
        f"- When the objective is achieved, impossible, or they want to hang up: one short "
        f"polite closing line, then append the exact token {END_TOKEN} at the very end. "
        "Never speak or mention the token."
    )


class CallAgent:
    def __init__(self, *, task: str, language: str, caller_name: str) -> None:
        self._settings = get_settings()
        self._client = AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        self._system = _system_prompt(task, language, caller_name)
        self._history: list[dict] = []

    def stream_opening(self) -> AsyncIterator[str]:
        return self._stream(
            "(The call just connected and someone answered. Greet them and state your "
            "purpose briefly.)"
        )

    def stream_reply(self, user_text: str) -> AsyncIterator[str]:
        return self._stream(user_text)

    async def _stream(self, user_text: str) -> AsyncIterator[str]:
        self._history.append({"role": "user", "content": user_text})
        full = ""
        completed = False
        try:
            async with self._client.messages.stream(
                model=self._settings.agent_model,
                max_tokens=150,
                system=self._system,
                messages=self._history,
            ) as stream:
                async for delta in stream.text_stream:
                    full += delta
                    yield delta
            completed = True
        finally:
            # Always record a reply so user/assistant roles keep alternating.
            # If we were cancelled (barge-in), tell the model the truth about
            # what actually reached the caller's ear.
            content = full.strip() or "..."
            if not completed:
                content += " (you were interrupted here; the rest was never spoken)"
            self._history.append({"role": "assistant", "content": content})
