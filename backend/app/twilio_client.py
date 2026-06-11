"""Twilio outbound call placement + hangup."""
from __future__ import annotations

from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from .config import get_settings


def _client() -> Client:
    s = get_settings()
    return Client(s.twilio_account_sid, s.twilio_auth_token)


def _build_twiml(session_id: str) -> str:
    settings = get_settings()
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=f"{settings.ws_base_url}/ws/twilio/{session_id}")
    stream.parameter(name="session_id", value=session_id)
    response.append(connect)
    return str(response)


def place_call(*, to_number: str, session_id: str) -> str:
    settings = get_settings()
    call = _client().calls.create(
        to=to_number,
        from_=settings.twilio_from_number,
        twiml=_build_twiml(session_id),
        status_callback=f"{settings.public_base_url}/api/twilio/status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST",
    )
    return call.sid


def hangup_call(call_sid: str) -> None:
    _client().calls(call_sid).update(status="completed")
