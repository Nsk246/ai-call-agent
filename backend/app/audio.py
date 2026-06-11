"""Audio + text-segmentation helpers for the telephony pipeline.

Twilio Media Streams speak G.711 mu-law @ 8 kHz, mono, base64, headerless.
"""
from __future__ import annotations

import re


def chunk_bytes(data: bytes, size: int = 1600):
    """Yield `size`-byte slices. 1600 bytes of mu-law @ 8 kHz ~= 200 ms."""
    for i in range(0, len(data), size):
        yield data[i:i + size]


# ---------------------------------------------------------------------------
# Streaming sentence segmentation.
#
# Rules that matter for speech (not prose):
#   * Split at . ? ! and the Indic danda, but ONLY when followed by whitespace,
#     so decimals ("8.30"), times, and version numbers never get cut.
#   * Don't split after common abbreviations ("Dr.", "Mr.", "St.", "e.g.").
#   * A newline is always a boundary.
#   * Flush long run-ons early at a word boundary so audio starts sooner.
# ---------------------------------------------------------------------------
_ENDERS = {".", "?", "!", "\u0964"}  # \u0964 = danda
_ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "st", "vs", "no", "jr", "sr",
    "e.g", "i.e", "etc", "approx", "dept", "inc", "ltd", "pvt",
}
_MAX_SEGMENT = 180  # flush long run-ons so audio starts sooner


def _ends_with_abbreviation(text: str) -> bool:
    """True if `text` (which ends just before a '.') ends in a known abbrev."""
    m = re.search(r"([A-Za-z][A-Za-z.]*)$", text)
    if not m:
        return False
    word = m.group(1).lower().rstrip(".")
    return word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Split a growing text buffer into complete spoken segments.

    Returns (segments_ready_to_speak, remaining_buffer). Conservative on
    purpose: when in doubt it waits for more tokens; the turn pipeline flushes
    whatever remains at end-of-stream anyway.
    """
    segments: list[str] = []
    start = 0
    n = len(buffer)
    for i in range(n):
        ch = buffer[i]
        if ch == "\n":
            seg = buffer[start:i].strip()
            if seg:
                segments.append(seg)
            start = i + 1
            continue
        if ch in _ENDERS:
            if i + 1 >= n:
                break  # boundary not confirmed yet; wait for the next token
            if not buffer[i + 1].isspace():
                continue  # "8.30", "v2.1", "?!" runs, etc.
            if ch == "." and _ends_with_abbreviation(buffer[start:i]):
                continue  # "Dr. Smith", "e.g. this"
            seg = buffer[start:i + 1].strip()
            if seg:
                segments.append(seg)
            start = i + 1

    remainder = buffer[start:]
    if len(remainder) > _MAX_SEGMENT:
        cut = remainder.rfind(" ", 0, _MAX_SEGMENT)
        if cut > 40:
            seg = remainder[:cut].strip()
            if seg:
                segments.append(seg)
            remainder = remainder[cut + 1:]
    return segments, remainder


_MD_CHARS = re.compile(r"[*_`#~\[\]<>|]")
_WS = re.compile(r"\s+")


def sanitize_for_tts(text: str) -> str:
    """Strip anything that would be read aloud as garbage (markdown, etc.)."""
    text = _MD_CHARS.sub("", text)
    return _WS.sub(" ", text).strip()
