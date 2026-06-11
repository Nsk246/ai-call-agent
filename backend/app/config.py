"""Configuration loaded from environment variables (.env)."""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Gemini Live (speech-to-speech call brain) ---
    gemini_api_key: str
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    gemini_voice: str = "Aoede"

    # --- Google Cloud STT (side-channel: accurate callee transcripts only) ---
    google_application_credentials: str = ""
    transcript_stt_enabled: bool = True
    english_stt_code: str = "en-IN"
    malayalam_stt_code: str = "ml-IN"

    # --- Twilio ---
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str

    # --- Networking ---
    public_base_url: str
    cors_origins: str = "*"

    @property
    def ws_base_url(self) -> str:
        return self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")


@lru_cache
def get_settings() -> "Settings":
    s = Settings()
    if s.google_application_credentials:
        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS", s.google_application_credentials
        )
    return s
