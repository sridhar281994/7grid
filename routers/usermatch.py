from kivy.uix.screenmanager import Screen
from kivy.clock import Clock
from kivy.uix.label import Label
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
import threading
import time
import requests

try:
    from utils import storage
except Exception:
    storage = None


class UserMatchScreen(Screen):
    selected_amount = 0
    _poll_event = None
    _stop_flag = False

    def on_enter(self, *_):
        """Start matchmaking when screen entered."""
        if self.selected_amount <= 0:
            return
        me = self._current_player_name()
        self.start_matchmaking(local_player_name=me, amount=self.selected_amount)

    def on_leave(self, *_):
        """Stop polling if user leaves screen."""
        self._stop_polling()

    def _current_player_name(self) -> str:
        if storage:
            user = storage.get_user() or {}
            if user.get("name"):
                return user["name"].strip()
            if user.get("email"):
                return user["email"].split("@", 1)[0]
        return "You"

    def start_matchmaking(self, local_player_name: str, amount: int):
        """Send request to backend to create/join match."""
        if not storage:
            self._show_popup("Error", "Storage not available")
            return

        token = storage.get_token()
        if not token:
            self._show_popup("Error", "You are not logged in")
            return

        backend = storage.get_backend_url()
        if not backend:
            self._show_popup("Error", "Backend URL missing")
            return

        url = f"{backend}/matches/create"
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"stake_amount": amount},
                timeout=10,
            )
            data = resp.json()
        except Exception as e:
            self._show_popup("Error", f"Failed to connect: {e}")
            return

        if not data.get("ok"):
            self._show_popup("Error", f"Match create failed: {data}")
            return

        match_id = data["match_id"]
        p1_name = data.get("p1")
        p2_name = data.get("p2")

        # Save match info + names
        storage.set_current_match(match_id, amount, p1_name, p2_name)

        # Start polling
        self._stop_flag = False
        self._poll_event = Clock.schedule_interval(lambda dt: self._poll_match(match_id), 2)

        self._show_popup("Searching", f"Waiting for opponent...\nMatch {match_id}")

    def _poll_match(self, match_id: int):
        """Poll backend until opponent joins."""
        if self._stop_flag:
            return False

        token = storage.get_token()
        backend = storage.get_backend_url()
        if not (token and backend):
            return False

        try:
            resp = requests.get(
                f"{backend}/matches/check",
                headers={"Authorization": f"Bearer {token}"},
                params={"match_id": match_id},
                timeout=10,
            )
            data = resp.json()
        except Exception as e:
            print(f"[WARN] Match poll failed: {e}")
            return True

        if data.get("ready"):
            # Update player names from backend
            p1_name = data.get("p1")
            p2_name = data.get("p2")
            storage.set_player_names(p1_name, p2_name)

            # Stop polling and move to game
            self._stop_polling()
            game_screen = self.manager.get_screen("dicegame")
            if hasattr(game_screen, "set_stage_and_players"):
                game_screen.set_stage_and_players(
                    amount=data.get("stake", 0),
                    player1=p1_name,
                    player2=p2_name,
                )
            self.manager.current = "dicegame"
            return False

        return True # keep polling

    def _stop_polling(self):
        self._stop_flag = True
        if self._poll_event:
            try:
                self._poll_event.cancel()
            except Exception:
                pass
        self._poll_event = None

    def _show_popup(self, title: str, message: str):
        layout = BoxLayout(orientation="vertical", spacing=10, padding=10)
        lbl = Label(text=message, halign="center", valign="middle")
        lbl.bind(size=lambda *_: setattr(lbl, "text_size", lbl.size))
        layout.add_widget(lbl)
        popup = Popup(title=title, content=layout, size_hint=(None, None), size=(300, 200))
        popup.open()
        Clock.schedule_once(lambda *_: popup.dismiss(), 2)

