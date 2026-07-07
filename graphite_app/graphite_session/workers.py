import threading
import re
from datetime import datetime

from PySide6.QtCore import QThread, Signal


class SaveWorkerThread(QThread):
    finished = Signal(int)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, db, title_generator, chat_data, current_chat_id, first_message):
        super().__init__()
        self.db = db
        self.title_generator = title_generator
        self.chat_data = chat_data
        self.current_chat_id = current_chat_id
        self.first_message = first_message
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _is_cancelled(self):
        return self._stop_event.is_set()

    def _fallback_title(self):
        words = re.findall(r"[A-Za-z0-9']+", str(self.first_message or ""))
        if words:
            title = " ".join(words[:5]).strip()
            if title:
                return title[:80]
        return f"Chat {datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def run(self):
        try:
            if self._is_cancelled():
                self.cancelled.emit()
                return

            new_chat_id = self.current_chat_id
            if not self.current_chat_id:
                title = self._fallback_title()
                new_chat_id = self.db.save_chat(title, self.chat_data)
            else:
                chat = self.db.load_chat(self.current_chat_id)
                if self._is_cancelled():
                    self.cancelled.emit()
                    return
                if chat:
                    title = chat["title"]
                    self.db.update_chat(self.current_chat_id, title, self.chat_data)
                else:
                    title = self._fallback_title()
                    new_chat_id = self.db.save_chat(title, self.chat_data)

            if self._is_cancelled():
                self.cancelled.emit()
                return

            if new_chat_id:
                self.db.save_notes(new_chat_id, self.chat_data.get("notes_data", []))
                self.db.save_pins(new_chat_id, self.chat_data.get("pins_data", []))

            self.finished.emit(new_chat_id)
        except Exception as exc:
            if self._is_cancelled():
                self.cancelled.emit()
                return
            self.error.emit(f"Background save failed: {str(exc)}")
