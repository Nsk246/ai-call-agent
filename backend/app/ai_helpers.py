"""Gemini text helpers: post-call summary + task polishing."""
from __future__ import annotations

import json
import traceback

from google import genai
from google.genai import types

from .config import get_settings

_FALLBACK_SUMMARY = {"outcome": "unknown", "headline": "Summary unavailable",
                     "details": [], "sentiment": "neutral"}

_client_instance: genai.Client | None = None


def _client() -> genai.Client:
    global _client_instance
    if _client_instance is None:
        _client_instance = genai.Client(api_key=get_settings().gemini_api_key)
    return _client_instance


async def _generate(prompt, **cfg) -> str:
    resp = await _client().aio.models.generate_content(
        model=get_settings().transcript_model,
        contents=prompt,
        config=types.GenerateContentConfig(**cfg),
    )
    return resp.text or ""


async def summarize_call(task: str, transcript: list[dict], status: str) -> dict:
    lines = "\n".join(
        f"{'AGENT' if t.get('role') == 'agent' else 'CALLEE'}: {t.get('text', '')}"
        for t in transcript if t.get("text"))
    if not lines.strip():
        return {"outcome": "failed", "headline": "No conversation took place.",
                "details": [], "sentiment": "neutral"}
    prompt = (
        "You are summarizing a phone call an AI agent made on the user's behalf.\n"
        f"The agent's task was: {task}\nCall end status: {status}\n"
        f"Transcript:\n{lines}\n\n"
        "Return STRICT JSON only, with keys: outcome (one of: accomplished, partial, "
        "failed, unknown), headline (<=2 sentences, in English, stating concretely what "
        "was learned/agreed), details (array of {label, value} for key facts like time, "
        "place, preference - max 5), sentiment (positive|neutral|negative).")
    try:
        data = json.loads(await _generate(
            prompt, temperature=0.1, response_mime_type="application/json") or "{}")
        return {
            "outcome": data.get("outcome", "unknown"),
            "headline": str(data.get("headline", ""))[:500] or "Summary unavailable",
            "details": [d for d in data.get("details", [])
                        if isinstance(d, dict) and d.get("label")][:5],
            "sentiment": data.get("sentiment", "neutral"),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[summary] failed: {exc!r}")
        traceback.print_exc()
        return dict(_FALLBACK_SUMMARY)


async def polish_task(task: str, language: str) -> str:
    """Raises RuntimeError with a readable message on failure (endpoint surfaces it)."""
    if not task.strip():
        return task
    lang = "Malayalam" if language == "ml" else "English"
    prompt = (
        "Rewrite this into a clear, complete brief for an AI phone-call agent. Keep it "
        "to 1-3 sentences of plain English (the brief itself stays in English even if "
        f"the call will be in {lang}). Include the concrete goal, any specifics implied, "
        "and what to confirm before ending. Output ONLY the rewritten brief.\n\n"
        f"User's draft: {task}")
    try:
        out = (await _generate(prompt, temperature=0.3)).strip()
        if not out or len(out) > 600:
            raise RuntimeError("Model returned an unusable rewrite")
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[polish] failed: {exc!r}")
        traceback.print_exc()
        raise RuntimeError(f"Polish failed: {type(exc).__name__}: {exc}") from exc
