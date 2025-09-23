import os
import smtplib
import ssl
from email.message import EmailMessage
# -------------------------
# SMTP Config from Environment
# -------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")   # smtp.zoho.in for Zoho
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))         # 587 (TLS) or 465 (SSL)
SMTP_USER = os.getenv("SMTP_USER", "")                 # your email address
SMTP_PASS = os.getenv("SMTP_PASS", "")                 # app password
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)        # "SRTech <you@domain.com>"
# -------------------------
# Core Email Sender
# -------------------------
def send_email(to_email: str, subject: str, body_text: str) -> None:
    """Send a plain-text email via SMTP (handles TLS on 587 or SSL on 465)."""
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASS and EMAIL_FROM):
        raise RuntimeError("SMTP env vars not configured (SMTP_HOST/PORT/USER/PASS/EMAIL_FROM).")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email
    msg.set_content(body_text)
    try:
        if SMTP_PORT == 465:
            # SSL mode (Zoho, some providers)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        else:
            # TLS mode (Gmail, Zoho TLS on 587)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
    except Exception as e:
        raise RuntimeError(f"Failed to send email: {e}")
# -------------------------
# OTP Helper
# -------------------------
def send_email_otp(to_email: str, otp: str, minutes_valid: int = 5) -> None:
    subject = "Your One-Time Password (OTP)"
    body = (
        f"Hello,\n\n"
        f"Your login OTP is: {otp}\n\n"
        f"This code is valid for {minutes_valid} minute(s). "
        f"Do not share it with anyone.\n\n"
        f"Thanks,\nSRTech"
    )
    send_email(to_email, subject, body)
# -------------------------
# Mask Email for Logs
# -------------------------
def mask_email(e: str) -> str:
    """Mask an email for safe UI logs (e.g., j****e@domain.com)."""
    try:
        local, domain = e.split("@", 1)
        if len(local) <= 2:
            masked_local = local[0] + "*"
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked_local}@{domain}"
    except Exception:
        return "***"





