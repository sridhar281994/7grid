import os
import smtplib
from email.message import EmailMessage
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")          # your Gmail address
SMTP_PASS = os.getenv("SMTP_PASS", "")          # Gmail App Password (not your login)
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER) # e.g., "SRTech <you@gmail.com>"
def send_email(to_email: str, subject: str, body_text: str) -> None:
    """Send a plain-text email via Gmail SMTP (TLS)."""
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASS and EMAIL_FROM):
        raise RuntimeError("SMTP env vars not configured (SMTP_HOST/PORT/USER/PASS/EMAIL_FROM).")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email
    msg.set_content(body_text)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
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
def mask_email(e: str) -> str:
    """Optional helper: mask user email for safe UI logs (e.g., j****e@gmail.com)."""
    try:
        local, domain = e.split("@", 1)
        if len(local) <= 2:
            masked_local = local[0] + "*"
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked_local}@{domain}"
    except Exception:
        return "***"
