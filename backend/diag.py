"""Ask Twilio what happened to recent calls."""
from dotenv import load_dotenv
load_dotenv()
from app.config import get_settings
from twilio.rest import Client

s = get_settings()
c = Client(s.twilio_account_sid, s.twilio_auth_token)

print("=== Recent calls (Twilio's view) ===")
for call in c.calls.list(limit=4):
    print(f"to={call.to}  status={call.status}  duration={call.duration}s  "
          f"start={call.start_time}  answered_by={getattr(call, 'answered_by', None)}")

print("\n=== Recent Twilio alerts/errors ===")
try:
    for a in c.monitor.alerts.list(limit=6):
        print(f"[{a.date_created}] code={a.error_code}  {str(a.alert_text)[:160]}")
except Exception as exc:
    print(f"(alerts unavailable: {exc})")
