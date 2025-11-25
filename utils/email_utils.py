import os
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# Ensure .env is loaded FIRST
load_dotenv()


def send_email(to_email: str, subject: str, body_text: str) -> None:
    """
    Send plain text email using SendGrid API.
    Env vars are read at runtime to avoid stale values.
    """

    sendgrid_api_key = os.getenv("SENDGRID_API_KEY")
    email_from = os.getenv("EMAIL_FROM", "no-reply@srtech.co.in")

    if not sendgrid_api_key or not email_from:
        raise RuntimeError(
            "SendGrid env vars not configured (SENDGRID_API_KEY / EMAIL_FROM)."
        )

    message = Mail(
        from_email=email_from,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body_text
    )

    try:
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(message)
        print(f"[INFO] Email sent to {to_email} | Status: {response.status_code}")
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
