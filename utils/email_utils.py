import os
import requests
from dotenv import load_dotenv

load_dotenv()

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_FROM = os.getenv("SMTP_FROM")


def _get_access_token():
    url = "https://accounts.zoho.com/oauth/v2/token"
    payload = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }
    r = requests.post(url, data=payload, timeout=15)
    return r.json().get("access_token")


def send_email(to_email: str, subject: str, body_text: str) -> None:
    access_token = _get_access_token()

    url = "https://mail.zoho.com/api/accounts/me/messages"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    data = {
        "fromAddress": ZOHO_FROM,
        "toAddress": to_email,
        "subject": subject,
        "content": body_text,
        "mailFormat": "plaintext"
    }

    r = requests.post(url, json=data, headers=headers, timeout=15)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Zoho API mail failed: {r.text}")


def send_email_otp(to_email: str, otp: str, minutes_valid: int = 5) -> None:
    subject = "Your One-Time Password (OTP)"
    body = f"""
Hello,

Your login OTP is: {otp}

This code is valid for {minutes_valid} minute(s).
Do not share it with anyone.

Thanks,
SRTech
"""
    send_email(to_email, subject, body)
