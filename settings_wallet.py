import requests
import threading
import webbrowser

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

try:
    from utils import storage
except Exception:
    storage = None


class WalletActionsMixin:
    """Reusable wallet-related actions to keep the settings screen lean."""

    @staticmethod
    def _auth_pair():
        token = storage.get_token() if storage else None
        backend = storage.get_backend_url() if storage else None
        return token, backend

    def _require_auth(self):
        token, backend = self._auth_pair()
        if not (token and backend):
            self.show_popup("Error", "Login need")
            return None, None
        return token, backend

    @staticmethod
    def _run_async(worker):
        threading.Thread(target=worker, daemon=True).start()

    # ------------------ Recharge ------------------
    def recharge(self):
        box = BoxLayout(orientation="vertical", spacing=5, padding=5)
        amount_input = TextInput(hint_text="Enter recharge amount", multiline=False, input_filter="int")
        box.add_widget(amount_input)
        submit_btn = Button(text="Submit", size_hint_y=None, height=40)
        box.add_widget(submit_btn)
        popup = Popup(title="Recharge", content=box, size_hint=(0.8, 0.4))

        def send_request(amount: int):
            token, backend = self._require_auth()
            if not (token and backend):
                return

            def worker():
                try:
                    resp = requests.post(
                        f"{backend}/wallet/recharge/create-link",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"amount": amount},
                        timeout=15,
                        verify=False,
                    )
                    data = resp.json()
                    url = data.get("short_url")
                    if not url:
                        raise RuntimeError("No link")

                    Clock.schedule_once(lambda dt: webbrowser.open(url), 0)
                    Clock.schedule_once(
                        lambda dt: self.show_popup("Info", "Pay app", "Finish payment then refresh"),
                        0,
                    )
                except Exception as err:
                    Clock.schedule_once(lambda dt, msg=str(err): self.show_popup("Error", "Recharge fail", msg), 0)

            self._run_async(worker)

        def submit(_):
            try:
                amount = int(amount_input.text.strip())
                if amount <= 0:
                    raise ValueError
            except Exception:
                self.show_popup("Error", "Amount bad")
                return
            popup.dismiss()
            send_request(amount)

        submit_btn.bind(on_release=submit)
        popup.open()

    # ------------------ Withdraw ------------------
    def withdraw(self):
        token, backend = self._require_auth()
        if not (token and backend):
            return
        user = storage.get_user() or {}
        balance = user.get("wallet_balance") or 0

        box = BoxLayout(orientation="vertical", spacing=5, padding=5)
        amount_input = TextInput(hint_text="Enter withdraw amount", multiline=False, input_filter="int")
        upi_input = TextInput(hint_text="Enter your UPI ID", multiline=False)
        box.add_widget(amount_input)
        box.add_widget(upi_input)
        submit_btn = Button(text="Withdraw", size_hint_y=None, height=40)
        box.add_widget(submit_btn)
        popup = Popup(title="Withdraw", content=box, size_hint=(0.8, 0.5))

        def submit(_):
            try:
                amount = int(amount_input.text.strip())
                upi_id = upi_input.text.strip()
                if amount <= 0 or not upi_id:
                    raise ValueError
                if amount > balance:
                    self.show_popup("Error", "Low wallet")
                    return
            except Exception as exc:
                self.show_popup("Error", "Input bad", str(exc))
                return

            def worker():
                try:
                    resp = requests.post(
                        f"{backend}/wallet/withdraw/request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"amount": amount, "upi_id": upi_id},
                        timeout=10,
                        verify=False,
                    )
                    if resp.status_code == 200:
                        Clock.schedule_once(lambda dt: self.show_popup("Success", "Withdraw sent", f"₹{amount} pending"), 0)
                        Clock.schedule_once(lambda dt: self.refresh_wallet_balance(), 0)
                    else:
                        raise RuntimeError(resp.text or "No response")
                except Exception as err:
                    Clock.schedule_once(lambda dt, msg=str(err): self.show_popup("Error", "Withdraw fail", msg), 0)

            self._run_async(worker)
            popup.dismiss()

        submit_btn.bind(on_release=submit)
        popup.open()

    # ------------------ Wallet History ------------------
    def show_wallet_history(self):
        token, backend = self._require_auth()
        if not (token and backend):
            return

        def worker():
            try:
                resp = requests.get(
                    f"{backend}/wallet/history",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"limit": 20},
                    timeout=10,
                    verify=False,
                )
                if resp.status_code != 200:
                    raise RuntimeError(resp.text)
                data = resp.json()
                txs = data if isinstance(data, list) else data.get("transactions", [])
                if not txs:
                    Clock.schedule_once(lambda dt: self.show_popup("History", "No data"), 0)
                    return

                layout = BoxLayout(orientation="vertical", spacing=5, padding=10, size_hint_y=None)
                layout.bind(minimum_height=layout.setter("height"))

                for tx in txs:
                    amount_val = tx.get("amount", 0)
                    if storage:
                        amount_val = storage.normalize_wallet_amount(amount_val)
                    else:
                        try:
                            amount_val = int(round(float(amount_val)))
                        except (TypeError, ValueError):
                            amount_val = 0
                    text = f"[{tx['timestamp'][:16]}] {tx['type']} ₹{amount_val} ({tx['status']})"
                    lbl = Label(
                        text=text,
                        halign="left",
                        valign="middle",
                        size_hint_y=None,
                        height=30,
                    )
                    lbl.bind(size=lambda inst, _: setattr(inst, "text_size", inst.size))
                    layout.add_widget(lbl)

                scroll = ScrollView(size_hint=(1, 1))
                scroll.add_widget(layout)

                popup = Popup(
                    title="Wallet History (Last 20)",
                    content=scroll,
                    size_hint=(0.9, 0.7),
                )
                Clock.schedule_once(lambda dt: popup.open(), 0)

            except Exception as err:
                Clock.schedule_once(lambda dt, msg=str(err): self.show_popup("Error", "History fail", msg), 0)

        self._run_async(worker)

    # ------------------ Wallet Portal ------------------
    def open_wallet_portal(self):
        token, backend = self._require_auth()
        if not (token and backend):
            return

        def worker():
            try:
                session_url = self._fetch_wallet_portal_link(token, backend)
                if not session_url:
                    raise RuntimeError("Wallet link missing")
            except Exception as err:
                Clock.schedule_once(
                    lambda dt, msg=str(err): self.show_popup("Error", "Wallet open fail", msg),
                    0,
                )
                return

            def launch(_dt):
                try:
                    opened = webbrowser.open(session_url, new=2, autoraise=True)
                    if not opened:
                        raise RuntimeError("Browser refused link")
                    self.show_popup("Info", "Opening wallet site", session_url)
                except Exception as err:
                    self.show_popup("Error", "Wallet open fail", str(err))

            Clock.schedule_once(launch, 0)

        self._run_async(worker)

    def _fetch_wallet_portal_link(self, token: str, backend: str) -> str:
        """Create a wallet portal link using the /wallet-link endpoint to get a bridge token."""

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        attempts: list[str] = []

        # Primary method: Use /wallet-link endpoint to get a proper wallet bridge token
        try:
            resp = requests.post(
                f"{backend}/wallet-link",
                headers=headers,
                timeout=10,
                verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                bridge_token = data.get("token")
                if bridge_token:
                    # Get base wallet URL
                    base_url = None
                    if storage and hasattr(storage, "get_wallet_url"):
                        try:
                            base_url = storage.get_wallet_url()
                        except Exception:
                            base_url = None
                    if not base_url:
                        base_url = "https://wallet.srtech.co.in"

                    # Construct URL with token parameter (not session_token)
                    parsed = urlparse(base_url)
                    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
                    query_items["token"] = bridge_token
                    query_items.setdefault("source", "app")
                    rebuilt = parsed._replace(query=urlencode(query_items))
                    wallet_url = urlunparse(rebuilt)
                    return wallet_url
                else:
                    attempts.append("POST /wallet-link → No token in response")
            else:
                attempts.append(f"POST /wallet-link → {resp.status_code}")
        except Exception as exc:
            attempts.append(f"POST /wallet-link: {exc}")

        # Fallback: Try other endpoints that might return a URL directly
        candidates = [
            ("POST", f"{backend}/wallet/portal/create-link", {"source": "app"}),
            ("POST", f"{backend}/wallet/portal/link", {"source": "app"}),
            ("GET", f"{backend}/wallet/portal/link", None),
            ("POST", f"{backend}/wallet/portal", {"source": "app"}),
            ("GET", f"{backend}/wallet/portal", None),
        ]

        def _parse_url(resp):
            try:
                data = resp.json()
            except Exception:
                data = resp.text or ""
            return self._search_wallet_url(data)

        for method, url, body in candidates:
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=body if method != "GET" else None,
                    params=body if method == "GET" else None,
                    allow_redirects=False,
                    timeout=10,
                    verify=False,
                )
                if resp.status_code >= 400:
                    attempts.append(f"{method} {url} → {resp.status_code}")
                    continue
                session_url = _parse_url(resp)
                if not session_url:
                    location = resp.headers.get("Location")
                    if location and location.startswith("http"):
                        session_url = location
                if session_url:
                    return session_url
            except Exception as exc:
                attempts.append(f"{method} {url}: {exc}")
                continue

        # Last resort: Try to get wallet URL from profile endpoints
        profile_paths = [
            f"{backend}/wallet/portal-url",
            f"{backend}/users/me/wallet-link",
            f"{backend}/users/me/wallet",
            f"{backend}/users/me/profile",
            f"{backend}/users/me",
        ]
        for path in profile_paths:
            try:
                resp = requests.get(path, headers=headers, timeout=10, verify=False)
                if resp.status_code != 200:
                    attempts.append(f"GET {path} → {resp.status_code}")
                    continue
                data = resp.json()
                session_url = self._search_wallet_url(data)
                if session_url:
                    return session_url
            except Exception as exc:
                attempts.append(f"GET {path}: {exc}")

        # Final fallback: Construct URL manually (should not be needed if /wallet-link works)
        base_url = None
        if storage and hasattr(storage, "get_wallet_url"):
            try:
                base_url = storage.get_wallet_url()
            except Exception:
                base_url = None
        if not base_url:
            base_url = "https://wallet.srtech.co.in"

        try:
            parsed = urlparse(base_url)
            query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
            # Note: This fallback won't work properly without a bridge token
            # But we include it for backward compatibility
            if token:
                # Try to use token as bridge token (may not work)
                query_items["token"] = token
            query_items.setdefault("source", "app")
            query_items.setdefault("channel", "app")
            rebuilt = parsed._replace(query=urlencode(query_items))
            fallback_url = urlunparse(rebuilt)
            attempts.append(f"Fallback link={fallback_url}")
            return fallback_url
        except Exception as exc:
            attempts.append(f"Fallback build fail: {exc}")
            raise RuntimeError("; ".join(attempts))

    @staticmethod
    def _search_wallet_url(payload) -> str:
        def scan(node, require_wallet: bool) -> str:
            if isinstance(node, str):
                val = node.strip()
                if val.startswith("http"):
                    if not require_wallet or "wallet" in val.lower():
                        return val
                return ""
            if isinstance(node, dict):
                for value in node.values():
                    result = scan(value, require_wallet)
                    if result:
                        return result
                return ""
            if isinstance(node, (list, tuple, set)):
                for value in node:
                    result = scan(value, require_wallet)
                    if result:
                        return result
                return ""
            return ""

        return scan(payload, True) or scan(payload, False) or ""

    # ------------------ Wallet Refresh ------------------
    def refresh_wallet_balance(self):
        token, backend = self._auth_pair()

        def worker():
            balance_text = "Wallet: ₹0"
            if token and backend:
                try:
                    resp = requests.get(
                        f"{backend}/users/me",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10,
                        verify=False,
                    )
                    if resp.status_code == 200:
                        user = resp.json()
                        if storage:
                            storage.set_user(user)
                        balance = user.get("wallet_balance") or 0
                        if storage:
                            balance_text = storage.wallet_label_text(balance)
                        else:
                            try:
                                balance_text = f"Wallet: ₹{int(round(float(balance)))}"
                            except (TypeError, ValueError):
                                balance_text = "Wallet: ₹0"
                except Exception as err:
                    print(f"[WARN] Wallet refresh failed: {err}")

            def update_label(_dt):
                wallet_lbl = self.ids.get("wallet_label")
                if wallet_lbl:
                    if getattr(wallet_lbl, "markup", False):
                        wallet_lbl.text = f"[b]{balance_text}[/b]"
                    else:
                        wallet_lbl.text = balance_text

            Clock.schedule_once(update_label, 0)

        self._run_async(worker)
