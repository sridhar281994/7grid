from threading import Thread
from typing import Optional, Dict, Any

import requests
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.properties import NumericProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from utils import storage
from utils.otp_utils import (
    InvalidCredentialsError,
    LegacyOtpUnavailable,
    get_profile,
    request_login_otp,
    verify_login_with_otp,
)


def _safe_text(screen: Screen, wid: str, default: str = "") -> str:
    w = getattr(screen, "ids", {}).get(wid)
    return (w.text or "").strip() if w else default


def _popup(title: str, msg: str) -> None:
    def _open(*_):
        popup = Popup(
            title=title,
            content=Label(text=str(msg)),
            size_hint=(0.7, 0.3),
            auto_dismiss=True,
        )
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss(), 2)

    Clock.schedule_once(_open, 0)


class LoginScreen(Screen):
    font_scale = NumericProperty(1.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Clock.schedule_once(self._update_font_scale, 0)

    def on_size(self, *args):
        self._update_font_scale()

    def _update_font_scale(self, *_):
        width = self.width or Window.width or 1
        height = self.height or Window.height or 1
        width_ratio = width / 520.0
        height_ratio = height / 720.0
        self.font_scale = max(0.75, min(1.25, min(width_ratio, height_ratio)))

    # -----------------------
    # Navigation
    # -----------------------
    def go_back(self):
        if self.manager:
            self.manager.current = "welcome"

    def open_forgot_password(self):
        if not self.manager:
            return

        phone = _safe_text(self, "phone_input")
        try:
            forgot = self.manager.get_screen("forgot_password")
        except Exception:
            forgot = None

        if forgot and hasattr(forgot, "prefill_phone"):
            forgot.prefill_phone(phone)

        self.manager.current = "forgot_password"

    # -----------------------
    # Helpers
    # -----------------------
    def _read_identifier(self) -> str:
        return _safe_text(self, "phone_input")

    def _read_password(self) -> str:
        return _safe_text(self, "password_input")

    def _validate_identifier(self, identifier: str) -> bool:
        if not identifier:
            return False
        identifier = identifier.strip()
        if "@" in identifier and "." in identifier.split("@")[-1]:
            return True
        if identifier.isdigit() and 6 <= len(identifier) <= 15:
            return True
        return len(identifier) >= 3

    # -----------------------
    # Send OTP
    # -----------------------
    def send_otp_to_user(self):
        identifier = self._read_identifier()
        password = self._read_password()

        if not self._validate_identifier(identifier):
            _popup("Error", "Enter a valid registered email or number.")
            return
        if len(password) < 4:
            _popup("Error", "Enter your password.")
            return

        def work():
            # Request OTP - this will validate the password automatically
            try:
                data = request_login_otp(identifier, password)
                ok = bool(data.get("ok", True))
                msg = data.get("message") or ("OTP sent" if ok else "Failed to send OTP")
                _popup("Success" if ok else "Error", msg)
                return
            except InvalidCredentialsError:
                _popup("Error", "Wrong password.")
                return
            except LegacyOtpUnavailable:
                _popup("Info", "OTP works only for registered phone number.")
                return
            except Exception as exc:
                _popup("Error", f"Send OTP error:\n{exc}")
                return

        Thread(target=work, daemon=True).start()

    # -----------------------
    # Verify OTP + Login
    # -----------------------
    def verify_and_login(self):
        identifier = self._read_identifier()
        password = self._read_password()
        otp = _safe_text(self, "otp_input")

        if not self._validate_identifier(identifier):
            _popup("Error", "Enter a valid registered email or number.")
            return
        if len(password) < 4:
            _popup("Error", "Enter your password.")
            return
        if not otp:
            _popup("Error", "Enter the OTP.")
            return

        def work():
            try:
                data = verify_login_with_otp(identifier, password, otp)
                token = data.get("access_token") or data.get("token")
                user = data.get("user")

                if token and not user:
                    try:
                        prof = get_profile(token)
                        if isinstance(prof, dict):
                            user = prof.get("user") or prof
                    except Exception:
                        pass

                if not token:
                    _popup("Error", "Invalid OTP.")
                    return

                storage.set_token(token)
                if isinstance(user, dict):
                    storage.set_user(user)

                def after(*_):
                    if self.manager:
                        self.manager.current = "stage"
                    _popup("Success", "Login successful.")

                Clock.schedule_once(after, 0)

            except InvalidCredentialsError:
                _popup("Error", "Wrong password.")
                return
            except Exception as exc:
                msg = str(exc).lower()
                if "password" in msg and ("wrong" in msg or "invalid" in msg):
                    _popup("Error", "Wrong password.")
                    return
                _popup("Error", f"Verify OTP error:\n{exc}")

        Thread(target=work, daemon=True).start()
