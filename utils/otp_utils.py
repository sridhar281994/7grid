# utils/otp_utils.py
import os
import time
from typing import Optional, Dict, Any
import requests

# === Backend base URL ===
BACKEND_BASE = os.getenv("BACKEND_BASE", "https://spin-api-pba3.onrender.com").rstrip("/")

# TLS verification:
VERIFY_SSL = os.getenv("OTP_VERIFY_SSL", "false").lower() == "true"
PASSWORD_RESET_PATH = os.getenv("PASSWORD_RESET_PATH", "/auth/reset-password")

# Cache new login OTP endpoint availability
_LOGIN_OTP_ENDPOINT_AVAILABLE: Optional[bool] = None

# Networking settings
TIMEOUT = float(os.getenv("OTP_HTTP_TIMEOUT", "40"))
RETRIES = int(os.getenv("OTP_HTTP_RETRIES", "2"))


def _url(path: str) -> str:
    return f"{BACKEND_BASE}{path if path.startswith('/') else '/' + path}"


def _headers(token: Optional[str] = None) -> Dict[str, str]:
    hdrs = {"Accept": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    return hdrs


def _extract_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            if "detail" in data:
                return str(data["detail"])
            if "message" in data:
                return str(data["message"])
            return str(data)
        return str(data)
    except Exception:
        return resp.text or f"HTTP {resp.status_code}"


def _request(
    method: str,
    path: str,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
    token: Optional[str] = None,
    timeout: float = TIMEOUT,
) -> Dict[str, Any]:

    url = _url(path)
    attempts = 1 + max(0, RETRIES)
    last_exc: Optional[Exception] = None

    for i in range(attempts):
        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                json=json,
                params=params,
                headers=_headers(token),
                timeout=timeout,
                verify=VERIFY_SSL,
            )

            if not (200 <= resp.status_code < 300):
                msg = _extract_error(resp)
                raise requests.HTTPError(msg, response=resp)

            try:
                return resp.json()
            except Exception:
                return {"ok": False, "raw": resp.text}

        except requests.ReadTimeout:
            last_exc = f"Timeout after {timeout}s (attempt {i+1}/{attempts})"
            if i < attempts - 1:
                time.sleep(1.5)
                continue
            raise RuntimeError(f"Server too slow: {last_exc}")

        except requests.HTTPError as err:
            raise err

        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                time.sleep(1.5)
                continue
            raise RuntimeError(f"Request failed after {attempts} attempts: {e}")

    if last_exc:
        raise RuntimeError(str(last_exc))

    raise RuntimeError("Unexpected request failure")


# ======================================================
# ORIGINAL OTP (legacy only)
# ======================================================
def send_otp(phone: str) -> Dict[str, Any]:
    return _request("POST", "/auth/send-otp", json={"phone": phone})


def verify_otp(phone: str, otp: str) -> Dict[str, Any]:
    return _request("POST", "/auth/verify-otp", json={"phone": phone, "otp": otp})


def send_otp_phone(phone: str) -> Dict[str, Any]:
    return send_otp(phone)


def verify_otp_phone(phone: str, otp: str) -> Dict[str, Any]:
    return verify_otp(phone, otp)


# ======================================================
# Password reset
# ======================================================
def reset_password(
    new_password: str,
    *,
    token: Optional[str] = None,
    phone: Optional[str] = None,
    otp: Optional[str] = None,
) -> Dict[str, Any]:

    payload: Dict[str, Any] = {"password": new_password}

    if not token:
        if phone:
            payload["phone"] = phone
        if otp:
            payload["otp"] = otp
        if not (phone and otp):
            raise ValueError("reset_password requires token OR (phone+otp).")

    return _request("POST", PASSWORD_RESET_PATH, json=payload, token=token)


# ======================================================
# Registration
# ======================================================
def register_user(name: str,
                  phone: str,
                  email: str,
                  password: str,
                  upi_id: Optional[str] = None) -> Dict[str, Any]:

    body = {
        "name": name.strip(),
        "phone": phone.strip(),
        "email": email.strip(),
        "password": password,
    }
    if upi_id:
        body["upi_id"] = upi_id.strip()

    return _request("POST", "/auth/register", json=body)


# ======================================================
# Profile
# ======================================================
def get_profile(token: str) -> Dict[str, Any]:
    return _request("GET", "/users/me", token=token)


def update_profile(token: str,
                   name: Optional[str] = None,
                   upi_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Backend expects PATCH /users/me with JSON body
    """
    payload: Dict[str, Any] = {}
    if name is not None:
        payload["name"] = name.strip()
    if upi_id is not None:
        payload["upi_id"] = upi_id.strip()

    return _request("PATCH", "/users/me", json=payload, token=token)


# ======================================================
# Matchmaking blocks (unchanged)
# ======================================================
def list_waiting_matches(token: str) -> Dict[str, Any]:
    return _request("GET", "/matches/list", token=token)


def create_or_wait_match(token: str, stake_amount: int) -> Dict[str, Any]:
    return _request("POST", "/matches/create", json={"stake_amount": stake_amount}, token=token)


def join_match(token: str, match_id: int) -> Dict[str, Any]:
    return _request("POST", "/matches/join", json={"match_id": match_id}, token=token)


def check_match_ready(token: str, match_id: int) -> Dict[str, Any]:
    return _request("GET", f"/matches/check?match_id={match_id}", token=token)


def roll_dice(token: str, match_id: int) -> Dict[str, Any]:
    return _request("POST", "/matches/roll", json={"match_id": match_id}, token=token)


# ======================================================
# NEW LOGIN OTP SYSTEM (exactly matching your backend)
# ======================================================

class InvalidCredentialsError(PermissionError):
    pass


class LegacyOtpUnavailable(RuntimeError):
    pass


# ------------------------------
# 1) Password check
# ------------------------------
def password_check(identifier: str, password: str) -> bool:
    """
    Backend expects: {"identifier": str, "password": str}
    Backend parses identifier internally (email if contains @, phone if digits)
    """
    payload = {
        "identifier": identifier.strip(),
        "password": password
    }

    try:
        data = _request("POST", "/auth/login/password-check", json=payload)
        return data.get("ok") is True

    except requests.HTTPError as err:
        status = getattr(err.response, "status_code", None)
        if status in (401, 403):
            raise InvalidCredentialsError("Wrong password")
        if status == 404:
            # backend does not support this → treat as valid
            return True
        raise


# ------------------------------
# 2) Request OTP
# ------------------------------
def request_login_otp(identifier: str, password: str) -> Dict[str, Any]:
    """
    Backend expects: {"identifier": str, "password": str}
    Backend parses identifier internally (email if contains @, phone if digits)
    """
    payload = {
        "identifier": identifier.strip(),
        "password": password
    }

    global _LOGIN_OTP_ENDPOINT_AVAILABLE
    try:
        data = _request("POST", "/auth/login/request-otp", json=payload)
        _LOGIN_OTP_ENDPOINT_AVAILABLE = True
        return data

    except requests.HTTPError as err:
        status = getattr(err.response, "status_code", None)

        if status in (401, 403):
            raise InvalidCredentialsError("Incorrect password or identifier.")

        if status == 404:
            _LOGIN_OTP_ENDPOINT_AVAILABLE = False
            # Try legacy OTP if identifier is a phone number
            ident = identifier.strip()
            if ident.isdigit():
                return send_otp(ident)
            raise LegacyOtpUnavailable("Phone number required for fallback OTP.")

        raise


# ------------------------------
# 3) Verify OTP
# ------------------------------
def verify_login_with_otp(identifier: str, password: str, otp: str, channel: str = "app") -> Dict[str, Any]:
    """
    Backend expects: {"identifier": str, "password": str, "otp": str, "channel": Optional[str]}
    Backend parses identifier internally (email if contains @, phone if digits)
    """
    payload = {
        "identifier": identifier.strip(),
        "password": password,
        "otp": otp.strip(),
        "channel": channel.lower()
    }

    global _LOGIN_OTP_ENDPOINT_AVAILABLE
    try:
        data = _request("POST", "/auth/login/verify-otp", json=payload)
        _LOGIN_OTP_ENDPOINT_AVAILABLE = True
        return data

    except requests.HTTPError as err:
        status = getattr(err.response, "status_code", None)

        if status in (401, 403):
            raise InvalidCredentialsError("Incorrect password/OTP/identifier.")

        if status == 404:
            _LOGIN_OTP_ENDPOINT_AVAILABLE = False
            # Try legacy OTP if identifier is a phone number
            ident = identifier.strip()
            if ident.isdigit():
                return verify_otp(ident, otp)
            raise LegacyOtpUnavailable("Phone number required for fallback verify.")

        raise
