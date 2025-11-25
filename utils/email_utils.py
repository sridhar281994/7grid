import os
import requests
from dotenv import load_dotenv

# Load env vars
load_dotenv()

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_FROM = os.getenv("BREVO_FROM")  # info@srtech.co.in
BREVO_SENDER_NAME = "SRTech"


def send_email(to_email: str, subject: str, body_text: str) -> None:
    """
    Send plain text email using Brevo REST API
    """

    if not BREVO_API_KEY or not BREVO_FROM:
        raise RuntimeError("BREVO_API_KEY or BREVO_FROM not configured")

    url = "https://api.brevo.com/v3/smtp/email"

    payload = {
        "sender": {
            "name": BREVO_SENDER_NAME,
            "email": BREVO_FROM
        },
        "to": [
            {"email": to_email}
        ],
        "subject": subject,
        "textContent": body_text
    }

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers, timeout=10)

    if response.status_code not in (200, 201, 202):
        print(f"[ERROR] Brevo email failed: {response.status_code} {response.text}")
        raise RuntimeError("Brevo email send failed")

    print(f"[INFO] OTP email sent to {to_email} via Brevo")


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


def mask_email(e: str) -> str:
    try:
        local, domain = e.split("@", 1)
        if len(local) <= 2:
            masked = local[0] + "*"
        else:
            masked = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked}@{domain}"
    except Exception:
        return "***"
