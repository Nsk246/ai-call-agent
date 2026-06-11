"""Configuration loaded from environment variables (.env)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Anthropic ---
    anthropic_api_key: str
    # Haiku 4.5 = lowest latency for voice. Switch to "claude-sonnet-4-6" for
    # stronger reasoning / richer Malayalam at a small latency cost.
    agent_model: str = "claude-haiku-4-5-20251001"

    # --- Twilio ---
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str  # e.g. +14155551234 (a Twilio voice number you own)

    # --- Google Cloud ---
    # Path to a service-account JSON with Speech + Text-to-Speech enabled.
    # Set GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/key.json in .env
    google_application_credentials: str = ""

    # --- Networking ---
    # Public, HTTPS-reachable base URL of THIS backend (no trailing slash).
    # Locally use an ngrok https URL, e.g. https://ab12.ngrok-free.app
    public_base_url: str

    # --- Behaviour ---
    # Indian English ("en-IN") usually transcribes accented English better than en-US.
    english_stt_code: str = "en-IN"
    # Chirp 3: HD = most natural tier (human disfluencies, emotion, low-latency).
    english_tts_voice: str = "en-IN-Chirp3-HD-Aoede"
    malayalam_stt_code: str = "ml-IN"
    malayalam_tts_voice: str = "ml-IN-Chirp3-HD-Aoede"
    # Allow English<->Malayalam code-switching ("Manglish") on the Malayalam leg.
    enable_code_switching: bool = True

    cors_origins: str = "*"

    @property
    def ws_base_url(self) -> str:
        return self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")


@lru_cache
def get_settings() -> "Settings":
    s = Settings()
    if s.google_application_credentials:
        import os
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", s.google_application_credentials)
    return s
