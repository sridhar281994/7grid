import smtplib
import ssl
import random
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

# ── Gmail SMTP config (set these in Render → Environment) ─────────
EMAIL_ADDRESS = os.getenv("OTP_EMAIL_ADDRESS", "")        # your Gmail (sender)
EMAIL_PASSWORD = os.getenv("OTP_EMAIL_PASSWORD", "")      # Gmail App Password
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465  # SSL
OTP_TTL_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))

# Simple in-memory store (OK for now; swap to DB later if needed)
_OTP_STORE = {}  # { email: { "otp": "123456", "exp": datetime } }

def _now():
    return datetime.now(timezone.utc)

def _gen_otp(length=6) -> str:
    return str(random.randint(10**(length-1), 10**length - 1))

def send_otp_via_email(to_email: str) -> bool:
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("[Email OTP] Missing EMAIL env vars.")
        return False

    otp = _gen_otp()
    _OTP_STORE[to_email] = {"otp": otp, "exp": _now() + timedelta(minutes=OTP_TTL_MIN)}

    subject = "Your One-Time Password (OTP)"
    body_txt = f"Your OTP is {otp}. It expires in {OTP_TTL_MIN} minutes."
    body_html = f"""
    <html><body style="font-family:Arial,Helvetica,sans-serif;">
      <h2>SRTech Verification Code</h2>
      <p>Your OTP is <strong style="font-size:18px;">{otp}</strong>.</p>
      <p>This code will expire in {OTP_TTL_MIN} minutes.</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_txt, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ctx) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[Email OTP Error] {e}")
        return False

def verify_email_otp(to_email: str, otp_in: str) -> bool:
    item = _OTP_STORE.get(to_email)
    if not item:
        return False
    if _now() > item["exp"]:
        return False
    return item["otp"] == otp_in
