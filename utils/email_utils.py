import os
import smtplib
from email.message import EmailMessage
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))  # Zoho SSL
SMTP_USER = os.getenv("SMTP_USER", "info@srtech.co.in")
SMTP_PASS = os.getenv("SMTP_PASS", "")  # 12-char app password
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
def send_email(to_email: str, subject: str, body_text: str) -> None:
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASS and EMAIL_FROM):
        raise RuntimeError("SMTP env vars not configured.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email
    msg.set_content(body_text)
    # :white_check_mark: SSL instead of STARTTLS
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    print(f"[INFO] Sent mail to {to_email}")
def send_email_otp(to_email: str, otp: str, minutes_valid: int = 5) -> None:
    subject = "Your One-Time Password (OTP)"
    body = (
        f"Hello,\n\n"
        f"Your login OTP is: {otp}\n\n"
        f"This code is valid for {minutes_valid} minute(s).\n"
        f"Do not share it with anyone.\n\n"
        f"Thanks,\nSRTech"
    )
    send_email(to_email, subject, body)


