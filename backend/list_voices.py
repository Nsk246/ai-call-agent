"""Print available voices for a language, to verify/choose a TTS voice name.

Usage:
    python list_voices.py ml-IN
    python list_voices.py en-IN
Requires GOOGLE_APPLICATION_CREDENTIALS to be set (or in .env).
"""
import sys

from dotenv import load_dotenv
from google.cloud import texttospeech

load_dotenv()


def main(language: str) -> None:
    client = texttospeech.TextToSpeechClient()
    voices = client.list_voices(language_code=language).voices
    rows = sorted((v.name, v.ssml_gender.name) for v in voices)
    print(f"{len(rows)} voices for {language}:\n")
    for name, gender in rows:
        tag = "  <-- Chirp 3 HD" if "Chirp3-HD" in name else ""
        print(f"  {name:35} {gender}{tag}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "en-IN")
