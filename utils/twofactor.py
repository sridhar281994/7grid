import os
import requests

API_KEY = os.getenv("TWOFACTOR_API_KEY", "")
TEMPLATE = os.getenv("TWOFACTOR_TEMPLATE", "").strip()  # optional
BASE = "https://2factor.in/API/V1"

def send_otp_via_2factor(phone: str, timeout: int = 10):
    """
    Returns (ok: bool, session_id: str|None, err: str|None)
    """
    if not API_KEY:
        return False, None, "Missing TWOFACTOR_API_KEY"
    try:
        if TEMPLATE:
            # Use your template (AUTOGEN2)
            url = f"{BASE}/{API_KEY}/SMS/{phone}/AUTOGEN2/{TEMPLATE}"
        else:
            # Provider-generated OTP (AUTOGEN)
            url = f"{BASE}/{API_KEY}/SMS/{phone}/AUTOGEN"
        r = requests.get(url, timeout=timeout)
        data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
        status = (data or {}).get("Status") or (data or {}).get("status")
        details = (data or {}).get("Details") or (data or {}).get("details")
        if r.ok and str(status).lower() == "success" and details:
            # 'Details' is the SessionId
            return True, str(details), None
        return False, None, f"2Factor error: {data or r.text}"
    except Exception as e:
        return False, None, str(e)

def verify_otp_via_2factor(session_id: str, otp: str, timeout: int = 10):
    """
    Returns (ok: bool, err: str|None)
    """
    if not API_KEY:
        return False, "Missing TWOFACTOR_API_KEY"
    try:
        url = f"{BASE}/{API_KEY}/SMS/VERIFY/{session_id}/{otp}"
        r = requests.get(url, timeout=timeout)
        data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
        status = (data or {}).get("Status") or (data or {}).get("status")
        if r.ok and str(status).lower() == "success":
            return True, None
        return False, f"2Factor verify error: {data or r.text}"
    except Exception as e:
        return False, str(e)
