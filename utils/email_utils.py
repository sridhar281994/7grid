import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Ensure .env is loaded FIRST
load_dotenv()

SMTP_HOST = "smtp.zoho.in"
SMTP_PORT = 465  # SSL

EMAIL_FROM = os.getenv("EMAIL_FROM")   # your Zoho email (eg: no-reply@srtech.co.in)
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")  # Zoho App Password


def send_email(to_email: str, subject: str, body_text: str) -> None:
    """
    Send plain text email using Zoho SMTP.
    """

    if not EMAIL_FROM or not EMAIL_PASSWORD:
        raise RuntimeError(
            "Zoho SMTP env vars not configured (EMAIL_FROM / EMAIL_PASSWORD)."
        )

    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
            print(f"[INFO] Email sent to {to_email}")
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")
        raise


def send_email_otp(to_email: str, otp: str, minutes_valid: int = 5) -> None:
    """Send OTP via email."""
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
    """Mask email for safe logs (e.g., j****e@gmail.com)."""
    try:
        local, domain = e.split("@", 1)
        if len(local) <= 2:
            masked_local = local[0] + "*"
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked_local}@{domain}"
    except Exception:
        return "***"
