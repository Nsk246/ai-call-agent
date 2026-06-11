"""Configuration loaded from environment variables (.env)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Gemini Live (speech-to-speech call brain) ---
    gemini_api_key: str
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    gemini_voice: str = "Aoede"

    # --- Callee transcripts (display only) ---
    # Each caller utterance is transcribed by a regular Gemini model for the
    # frontend transcript. Never touches the live call path.
    callee_transcripts_enabled: bool = True
    transcript_model: str = "gemini-2.5-flash"

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
    return Settings()
