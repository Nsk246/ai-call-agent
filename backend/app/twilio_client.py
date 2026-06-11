"""Twilio outbound call placement."""
from __future__ import annotations

from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from .config import get_settings


def _build_twiml(session_id: str) -> str:
    settings = get_settings()
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=f"{settings.ws_base_url}/ws/twilio/{session_id}")
    # Custom parameter is echoed back in Twilio's "start" event.
    stream.parameter(name="session_id", value=session_id)
    response.append(connect)
    return str(response)


def place_call(*, to_number: str, session_id: str) -> str:
    """Dial `to_number`; returns the Twilio call SID. Raises on failure."""
    settings = get_settings()
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    call = client.calls.create(
        to=to_number,
        from_=settings.twilio_from_number,
        twiml=_build_twiml(session_id),
    )
    return call.sid
