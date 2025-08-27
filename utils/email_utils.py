import os
import smtplib
import ssl
from email.message import EmailMessage

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@example.com")
OTP_SUBJECT = os.getenv("OTP_SUBJECT", "Your SRTech OTP")

def send_email_otp(to_email: str, code: str) -> None:
    """Send a plain OTP mail via Gmail SMTP (App Password required)."""
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASS):
        raise RuntimeError("SMTP not configured (SMTP_HOST/PORT/USER/PASS).")

    body = f"""Hi,

Your one-time password (OTP) is: {code}

This OTP will expire in a few minutes. If you did not request this, ignore this email.

— SRTech
"""
    msg = EmailMessage()
    msg["Subject"] = OTP_SUBJECT
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
